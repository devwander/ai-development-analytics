"""Compare git activity before and after AI adoption, in the same repository.

Without a comparison window "productivity" has no denominator: 28 hours and
86,000 lines mean nothing until you know what the same team did in the same
repository before Claude Code arrived.

Two deliberate design choices:

* **The adoption date comes from the data.** It defaults to the first recorded
  session for the project - that is literally when the tool started being used
  there. It can be overridden when the project adopted AI before this dataset
  begins.
* **The result is stored, not recomputed.** A baseline is a dated claim with
  parameters attached; a chart that silently recalculated it would be impossible
  to audit later.

The comparison is *descriptive*, never causal. Team size, project phase and
scope all change over a quarter, and none of that is controlled here - which is
why every baseline carries its caveats in ``notes``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.collectors.git.collector import GitCollector, GitError, find_repository_root
from app.models.entities import Project, ProjectBaseline
from app.models.entities import Session as SessionRow

DEFAULT_WINDOW_DAYS = 90


@dataclass
class WindowStats:
    commits: int = 0
    insertions: int = 0
    deletions: int = 0
    files_changed: int = 0
    active_days: int = 0
    authors: list[str] | None = None


class BaselineError(RuntimeError):
    pass


def adoption_date(db: DbSession, project: Project) -> datetime | None:
    """When Claude Code first appears in this project's record."""
    return db.scalar(
        select(func.min(SessionRow.started_at)).where(
            SessionRow.project_id == project.id
        )
    )


def _summarise(collector: GitCollector, since: datetime, until: datetime) -> WindowStats:
    commits = collector.commits_between(since, until, max_commits=5000)
    if not commits:
        return WindowStats(authors=[])
    days = {
        (c.committed_at or c.authored_at).date()
        for c in commits
        if (c.committed_at or c.authored_at) is not None
    }
    return WindowStats(
        commits=len(commits),
        insertions=sum(c.insertions for c in commits),
        deletions=sum(c.deletions for c in commits),
        files_changed=sum(c.files_changed for c in commits),
        active_days=len(days),
        authors=sorted({c.author_name for c in commits if c.author_name}),
    )


def compute_baseline(
    db: DbSession,
    project: Project,
    *,
    adoption_at: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: datetime | None = None,
) -> ProjectBaseline:
    now = now or datetime.now(timezone.utc)
    boundary = adoption_at or adoption_date(db, project)
    if boundary is None:
        raise BaselineError(
            f"project {project.name!r} has no session with a start time; "
            "pass an explicit adoption date"
        )
    if boundary.tzinfo is None:
        boundary = boundary.replace(tzinfo=timezone.utc)

    root = find_repository_root(project.path) if project.path else None
    if root is None:
        raise BaselineError(f"project {project.name!r} is not a git repository")

    collector = GitCollector(root)
    window = timedelta(days=window_days)
    try:
        before = _summarise(collector, boundary - window, boundary)
        after = _summarise(collector, boundary, min(boundary + window, now))
    except GitError as exc:
        raise BaselineError(str(exc)) from exc

    notes: list[str] = [
        "Comparação descritiva entre duas janelas do mesmo repositório. "
        "Não isola a IA: tamanho da equipe, fase e escopo do projeto também "
        "mudam entre as janelas.",
    ]
    if adoption_at is None:
        notes.append(
            "Data de adoção inferida da primeira sessão registrada. Se o projeto "
            "já usava IA antes deste conjunto de dados, informe a data real."
        )
    elapsed_after = (min(boundary + window, now) - boundary).days
    if elapsed_after < window_days:
        notes.append(
            f"A janela pós-adoção tem apenas {elapsed_after} dos {window_days} dias "
            "pedidos: ainda não decorreu tempo suficiente. Compare taxas, não totais."
        )
    if before.commits == 0:
        notes.append(
            "Nenhum commit na janela anterior — o repositório pode ter nascido "
            "junto com o uso de IA, e nesse caso não há linha de base."
        )

    row = db.scalar(
        select(ProjectBaseline).where(ProjectBaseline.project_id == project.id)
    )
    if row is None:
        row = ProjectBaseline(project_id=project.id, adoption_at=boundary)
        db.add(row)

    row.adoption_at = boundary
    row.window_days = window_days
    row.before_commits = before.commits
    row.before_insertions = before.insertions
    row.before_deletions = before.deletions
    row.before_files_changed = before.files_changed
    row.before_active_days = before.active_days
    row.before_authors = before.authors or []
    row.after_commits = after.commits
    row.after_insertions = after.insertions
    row.after_deletions = after.deletions
    row.after_files_changed = after.files_changed
    row.after_active_days = after.active_days
    row.after_authors = after.authors or []
    row.notes = notes
    row.computed_at = now
    db.flush()
    return row


def baseline_rates(row: ProjectBaseline) -> dict[str, float | None]:
    """Per-active-day rates, which survive windows of unequal length.

    Returns ``None`` rather than 0.0 whenever the rate is undefined - no active
    days, or a counter that was never populated. Zero would read as "they
    shipped nothing", which is a different claim from "there is no baseline".
    """

    def rate(value: int | None, days: int | None) -> float | None:
        if value is None or not days:
            return None
        return value / days

    return {
        "before_commits_per_active_day": rate(row.before_commits, row.before_active_days),
        "after_commits_per_active_day": rate(row.after_commits, row.after_active_days),
        "before_insertions_per_active_day": rate(
            row.before_insertions, row.before_active_days
        ),
        "after_insertions_per_active_day": rate(
            row.after_insertions, row.after_active_days
        ),
    }
