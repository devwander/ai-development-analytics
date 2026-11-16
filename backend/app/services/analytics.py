"""Aggregation queries.

One rule governs every query here: **never sum across usage sources.**
``usages`` holds one total row per (session, source), and the sources disagree
by construction - ccusage de-duplicates resumed sessions, so its per-session
totals differ from the transcript's (ADR 0002). Adding them double-counts.

Filtering on a single source is not the answer either: on this machine 15 of 53
sessions have no ccusage row at all, so ``WHERE source = 'ccusage'`` silently
drops 28% of the data. Instead each session contributes exactly one row, from
its preferred source: ccusage when present, the transcript otherwise. The chosen
source travels with the row so a caller can always see where a number came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import Select, and_, case, func, select
from sqlalchemy.orm import Session as DbSession

from app.models.entities import AIAnalysis, Project, Usage
from app.models.entities import Session as SessionRow

#: Order in which sources are trusted for a session total.
SOURCE_PRIORITY = ("ccusage", "claude_transcript")


def preferred_usage() -> Any:
    """Subquery yielding exactly one usage total per session.

    Uses a window function rather than PostgreSQL's ``DISTINCT ON`` so the same
    query runs on SQLite in the test suite.
    """
    priority = case(
        {source: index for index, source in enumerate(SOURCE_PRIORITY)},
        value=Usage.source,
        else_=len(SOURCE_PRIORITY),
    )
    ranked = (
        select(
            Usage.session_id.label("session_id"),
            Usage.source.label("source"),
            Usage.input_tokens.label("input_tokens"),
            Usage.output_tokens.label("output_tokens"),
            Usage.cache_creation_tokens.label("cache_creation_tokens"),
            Usage.cache_read_tokens.label("cache_read_tokens"),
            Usage.total_tokens.label("total_tokens"),
            Usage.cost_usd.label("cost_usd"),
            func.row_number()
            .over(partition_by=Usage.session_id, order_by=priority)
            .label("rank"),
        )
        .where(Usage.model.is_(None))
        .subquery()
    )
    return (
        select(
            ranked.c.session_id,
            ranked.c.source,
            ranked.c.input_tokens,
            ranked.c.output_tokens,
            ranked.c.cache_creation_tokens,
            ranked.c.cache_read_tokens,
            ranked.c.total_tokens,
            ranked.c.cost_usd,
        )
        .where(ranked.c.rank == 1)
        .subquery()
    )


@dataclass(frozen=True)
class Period:
    """An inclusive date range applied to a session's start time.

    Both ends are optional, so "everything since X" and "everything up to Y" are
    expressible without sentinel dates. ``until`` is inclusive: a reader asking
    for 28/08 means the whole of that day, not midnight.
    """

    since: date | None = None
    until: date | None = None

    @property
    def is_open(self) -> bool:
        return self.since is None and self.until is None

    def start_at(self) -> datetime | None:
        if self.since is None:
            return None
        return datetime.combine(self.since, time.min, tzinfo=timezone.utc)

    def end_at(self) -> datetime | None:
        if self.until is None:
            return None
        # Exclusive upper bound one day on, so the last day is fully included.
        return datetime.combine(self.until + timedelta(days=1), time.min, tzinfo=timezone.utc)


#: Convenience for callers that do not filter.
ALL_TIME = Period()


def _session_scope(
    query: Select, project_id: int | None, period: Period | None = None
) -> Select:
    """Scope a query to a project and/or a date range.

    Every metric goes through here, which is what makes the numbers on a filtered
    page agree with each other: there is one definition of "in scope" and each
    function inherits it rather than re-implementing the predicate.

    A session with no recorded start time falls outside any date range - it cannot
    be placed in time, and silently keeping it would make the filtered totals
    disagree with the filtered session list.
    """
    if project_id is not None:
        query = query.where(SessionRow.project_id == project_id)
    if period is not None:
        start = period.start_at()
        end = period.end_at()
        if start is not None:
            query = query.where(SessionRow.started_at >= start)
        if end is not None:
            query = query.where(SessionRow.started_at < end)
    return query


def _day(column) -> Any:
    """Truncate a timestamp to a date, portably."""
    return func.date(column)


def overview(
    db: DbSession, *, project_id: int | None = None, period: Period | None = None
) -> dict[str, Any]:
    usage = preferred_usage()

    totals = db.execute(
        _session_scope(
            select(
                func.count(func.distinct(SessionRow.id)).label("sessions"),
                func.count(func.distinct(SessionRow.project_id)).label("projects"),
                func.coalesce(func.sum(SessionRow.active_turn_seconds), 0).label(
                    "active_turn_seconds"
                ),
                func.coalesce(func.sum(SessionRow.duration_seconds), 0).label(
                    "wall_clock_seconds"
                ),
                func.coalesce(func.sum(SessionRow.human_prompt_count), 0).label("prompts"),
                func.coalesce(func.sum(SessionRow.tool_call_count), 0).label("tool_calls"),
                func.coalesce(func.sum(SessionRow.tool_error_count), 0).label("tool_errors"),
                func.coalesce(func.sum(usage.c.total_tokens), 0).label("total_tokens"),
                func.sum(usage.c.cost_usd).label("cost_usd"),
            ).select_from(SessionRow).outerjoin(usage, usage.c.session_id == SessionRow.id),
            project_id,
            period,
        )
    ).one()

    by_source = dict(
        db.execute(
            _session_scope(
                select(usage.c.source, func.count())
                .select_from(SessionRow)
                .join(usage, usage.c.session_id == SessionRow.id)
                .group_by(usage.c.source),
                project_id,
                period,
            )
        ).all()
    )
    sessions_without_usage = db.scalar(
        _session_scope(
            select(func.count())
            .select_from(SessionRow)
            .outerjoin(usage, usage.c.session_id == SessionRow.id)
            .where(usage.c.session_id.is_(None)),
            project_id,
            period,
        )
    )

    per_day = [
        {
            "day": _as_date(row.day),
            "sessions": row.sessions,
            "total_tokens": int(row.total_tokens or 0),
            "cost_usd": float(row.cost_usd) if row.cost_usd is not None else None,
            "active_turn_seconds": float(row.active_turn_seconds or 0),
        }
        for row in db.execute(
            _session_scope(
                select(
                    _day(SessionRow.started_at).label("day"),
                    func.count(func.distinct(SessionRow.id)).label("sessions"),
                    func.coalesce(func.sum(usage.c.total_tokens), 0).label("total_tokens"),
                    func.sum(usage.c.cost_usd).label("cost_usd"),
                    func.coalesce(func.sum(SessionRow.active_turn_seconds), 0).label(
                        "active_turn_seconds"
                    ),
                )
                .select_from(SessionRow)
                .outerjoin(usage, usage.c.session_id == SessionRow.id)
                .where(SessionRow.started_at.is_not(None))
                .group_by(_day(SessionRow.started_at))
                .order_by(_day(SessionRow.started_at)),
                project_id,
                period,
            )
        ).all()
    ]

    by_status = dict(
        db.execute(
            _session_scope(
                select(SessionRow.status, func.count()).group_by(SessionRow.status),
                project_id,
                period,
            )
        ).all()
    )

    return {
        "sessions": totals.sessions,
        "projects": totals.projects,
        "active_turn_seconds": float(totals.active_turn_seconds or 0),
        "active_turn_hours": round(float(totals.active_turn_seconds or 0) / 3600, 2),
        "wall_clock_seconds": float(totals.wall_clock_seconds or 0),
        "total_tokens": int(totals.total_tokens or 0),
        "cost_usd": float(totals.cost_usd) if totals.cost_usd is not None else None,
        "human_prompts": int(totals.prompts or 0),
        "tool_calls": int(totals.tool_calls or 0),
        "tool_errors": int(totals.tool_errors or 0),
        "usage_sources": by_source,
        "sessions_without_usage": int(sessions_without_usage or 0),
        "sessions_by_status": by_status,
        "per_day": per_day,
        "task_types": task_type_breakdown(db, project_id=project_id, period=period),
    }


def task_type_breakdown(
    db: DbSession, *, project_id: int | None = None, period: Period | None = None
) -> dict[str, int]:
    """Inferred data. Empty until the LLM analyzer runs (Etapa 9)."""
    rows = db.execute(
        _session_scope(
            select(AIAnalysis.task_type, func.count())
            .select_from(AIAnalysis)
            .join(SessionRow, SessionRow.id == AIAnalysis.session_id)
            .where(AIAnalysis.task_type.is_not(None))
            .group_by(AIAnalysis.task_type),
            project_id,
            period,
        )
    ).all()
    return {task_type: count for task_type, count in rows}


def project_metrics(
    db: DbSession, *, period: Period | None = None
) -> list[dict[str, Any]]:
    """Projects with their aggregate metrics, optionally scoped to a period.

    The date predicate goes in the JOIN condition, not in WHERE: with an outer
    join a WHERE clause on the joined table silently turns it into an inner
    join, and projects with no session in the range would vanish from the
    directory instead of showing zeros.
    """
    usage = preferred_usage()
    session_join = [SessionRow.project_id == Project.id]
    if period is not None:
        start, end = period.start_at(), period.end_at()
        if start is not None:
            session_join.append(SessionRow.started_at >= start)
        if end is not None:
            session_join.append(SessionRow.started_at < end)

    rows = db.execute(
        select(
            Project.id,
            Project.name,
            Project.path,
            Project.repository_url,
            Project.is_git_repository,
            func.count(func.distinct(SessionRow.id)).label("sessions"),
            func.coalesce(func.sum(SessionRow.active_turn_seconds), 0).label(
                "active_turn_seconds"
            ),
            func.coalesce(func.sum(usage.c.total_tokens), 0).label("total_tokens"),
            func.sum(usage.c.cost_usd).label("cost_usd"),
            func.min(SessionRow.started_at).label("first_session_at"),
            func.max(SessionRow.started_at).label("last_session_at"),
        )
        .select_from(Project)
        .outerjoin(SessionRow, and_(*session_join))
        .outerjoin(usage, usage.c.session_id == SessionRow.id)
        .group_by(Project.id)
        .order_by(func.sum(usage.c.cost_usd).desc().nulls_last())
    ).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "path": row.path,
            "repository_url": row.repository_url,
            "is_git_repository": row.is_git_repository,
            "sessions": row.sessions,
            "active_turn_seconds": float(row.active_turn_seconds or 0),
            "total_tokens": int(row.total_tokens or 0),
            "cost_usd": float(row.cost_usd) if row.cost_usd is not None else None,
            "first_session_at": row.first_session_at,
            "last_session_at": row.last_session_at,
        }
        for row in rows
    ]


def tool_breakdown(
    db: DbSession, *, project_id: int | None = None, period: Period | None = None
) -> list[dict[str, Any]]:
    from app.models.entities import ToolCall

    rows = db.execute(
        _session_scope(
            select(
                ToolCall.tool_name,
                func.count().label("calls"),
                func.sum(case((ToolCall.is_error.is_(True), 1), else_=0)).label("errors"),
            )
            .select_from(ToolCall)
            .join(SessionRow, SessionRow.id == ToolCall.session_id)
            .group_by(ToolCall.tool_name)
            .order_by(func.count().desc()),
            project_id,
            period,
        )
    ).all()
    return [
        {"tool_name": row.tool_name, "calls": row.calls, "errors": int(row.errors or 0)}
        for row in rows
    ]


def _readable(path: str | None) -> bool:
    from pathlib import Path as _Path

    return bool(path) and _Path(path).exists()


def _as_date(value: Any) -> date | None:
    if value is None or isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Attribution, coverage and validity - the metrics a study needs before it can
# say anything about productivity.
# ---------------------------------------------------------------------------

#: A session with many tool calls but no tracked file edit probably wrote files
#: through the shell, which the transcript does not record (ADR 0001).
_SHELL_EDIT_TOOL_THRESHOLD = 20


def attribution(
    db: DbSession, *, project_id: int | None = None, period: Period | None = None
) -> dict[str, Any]:
    """How much of the committed code can actually be traced to the AI.

    The numerator is what Claude verifiably wrote: the +/- lines Claude Code
    records in ``structuredPatch`` for each Edit/Write. The denominator is the
    insertions of the commits associated with those sessions.

    The ratio is a **lower bound on a ceiling**, and both halves are loose:

    * the numerator misses everything written through the shell, because the
      transcript does not record it;
    * the denominator counts a whole commit, which contains every staged change
      - hand-typed lines included - not only what the session produced.

    So the number answers "at most this share of the committed lines passed
    through a tracked AI edit", never "the AI wrote this share". Reporting
    lines-per-hour without it invites large over-attribution: measured here,
    5.6% on one project and 42.8% on another.
    """
    from app.models.entities import ChangedFile, GitCommit, SessionCommitLink

    ai_query = (
        select(
            func.coalesce(func.sum(ChangedFile.insertions), 0).label("insertions"),
            func.coalesce(func.sum(ChangedFile.deletions), 0).label("deletions"),
            func.count(func.distinct(ChangedFile.file_path)).label("files"),
        )
        .select_from(ChangedFile)
        .join(SessionRow, SessionRow.id == ChangedFile.session_id)
        .where(
            ChangedFile.is_within_project.is_not(False),
            ChangedFile.insertions.is_not(None),
        )
    )
    ai = db.execute(_session_scope(ai_query, project_id, period)).one()

    # Distinct commits: a commit linked to two sessions must be counted once.
    linked_commits = (
        _session_scope(
            select(func.distinct(GitCommit.id).label("commit_id"))
            .select_from(GitCommit)
            .join(SessionCommitLink, SessionCommitLink.git_commit_id == GitCommit.id)
            .join(SessionRow, SessionRow.id == SessionCommitLink.session_id)
            .where(SessionCommitLink.likely_related.is_(True)),
            project_id,
            period,
        )
    ).subquery()

    git = db.execute(
        select(
            func.count().label("commits"),
            func.coalesce(func.sum(GitCommit.insertions), 0).label("insertions"),
            func.coalesce(func.sum(GitCommit.deletions), 0).label("deletions"),
        )
        .select_from(linked_commits)
        .join(GitCommit, GitCommit.id == linked_commits.c.commit_id)
    ).one()

    ratio = (
        (ai.insertions / git.insertions) if git.insertions else None
    )
    return {
        "ai_insertions": int(ai.insertions),
        "ai_deletions": int(ai.deletions),
        "ai_files": int(ai.files),
        "linked_commits": int(git.commits),
        "commit_insertions": int(git.insertions),
        "commit_deletions": int(git.deletions),
        "attribution_ratio": ratio,
    }


def coverage(
    db: DbSession, *, project_id: int | None = None, period: Period | None = None
) -> dict[str, Any]:
    """How much of the dataset can support each kind of claim.

    Effort claims can use every session; delivery claims can only use the ones
    with an associated commit. Publishing the second number beside the first
    stops a reader assuming the whole sample backs every chart.
    """
    from app.models.entities import AIAnalysis, SessionCommitLink, SessionSurvey

    total = db.scalar(
        _session_scope(select(func.count()).select_from(SessionRow), project_id, period)
    )
    with_prompts = db.scalar(
        _session_scope(
            select(func.count())
            .select_from(SessionRow)
            .where(SessionRow.human_prompt_count > 0),
            project_id,
            period,
        )
    )
    in_git = db.scalar(
        _session_scope(
            select(func.count())
            .select_from(SessionRow)
            .join(Project, Project.id == SessionRow.project_id)
            .where(Project.is_git_repository.is_(True)),
            project_id,
            period,
        )
    )
    with_commit = db.scalar(
        _session_scope(
            select(func.count(func.distinct(SessionRow.id)))
            .select_from(SessionRow)
            .join(SessionCommitLink, SessionCommitLink.session_id == SessionRow.id)
            .where(SessionCommitLink.likely_related.is_(True)),
            project_id,
            period,
        )
    )
    with_analysis = db.scalar(
        _session_scope(
            select(func.count(func.distinct(SessionRow.id)))
            .select_from(SessionRow)
            .join(AIAnalysis, AIAnalysis.session_id == SessionRow.id),
            project_id,
            period,
        )
    )
    with_survey = db.scalar(
        _session_scope(
            select(func.count())
            .select_from(SessionRow)
            .join(SessionSurvey, SessionSurvey.session_id == SessionRow.id),
            project_id,
            period,
        )
    )
    # A session with no project is invisible in every project view, so the
    # overview total and the sum of the project rows would disagree by exactly
    # this number if it were not reported.
    without_project = db.scalar(
        _session_scope(
            select(func.count())
            .select_from(SessionRow)
            .where(SessionRow.project_id.is_(None)),
            project_id,
            period,
        )
    )
    return {
        "sessions": int(total or 0),
        "with_prompts": int(with_prompts or 0),
        "in_git_project": int(in_git or 0),
        "with_linked_commit": int(with_commit or 0),
        "with_analysis": int(with_analysis or 0),
        "with_survey": int(with_survey or 0),
        "without_project": int(without_project or 0),
    }


def validity_threats(
    db: DbSession, *, project_id: int | None = None, period: Period | None = None
) -> dict[str, Any]:
    """Facts that qualify every other number on the page.

    None of these are failures. They are properties of the dataset that a study
    has to declare, and burying them would make the rest of the dashboard read
    as more certain than it is.
    """
    from app.models.entities import SessionSourceFile

    versions: set[str] = set()
    models: set[str] = set()
    for row in db.scalars(
        _session_scope(select(SessionRow), project_id, period)
    ).all():
        versions.update(row.claude_code_versions or [])
        models.update(row.models_used or [])

    shell_editing = db.scalar(
        _session_scope(
            select(func.count())
            .select_from(SessionRow)
            .where(
                SessionRow.files_modified == 0,
                SessionRow.tool_call_count > _SHELL_EDIT_TOOL_THRESHOLD,
            ),
            project_id,
            period,
        )
    )
    # ccusage de-duplicates a resumed session onto its parent (ADR 0002), so a
    # session with real assistant traffic and no ccusage row is very likely one.
    resumed = db.scalar(
        _session_scope(
            select(func.count())
            .select_from(SessionRow)
            .where(
                SessionRow.assistant_message_count > 0,
                ~SessionRow.id.in_(
                    select(Usage.session_id).where(Usage.source == "ccusage")
                ),
            ),
            project_id,
            period,
        )
    )
    corrupted = db.execute(
        _session_scope(
            select(
                func.count().label("files"),
                func.coalesce(func.sum(SessionSourceFile.unparseable_lines), 0).label("lines"),
            )
            .select_from(SessionSourceFile)
            .join(SessionRow, SessionRow.id == SessionSourceFile.session_id)
            .where(SessionSourceFile.unparseable_lines > 0),
            project_id,
            period,
        )
    ).one()
    interrupted = db.scalar(
        _session_scope(
            select(func.count())
            .select_from(SessionRow)
            .where(SessionRow.status == "interrupted"),
            project_id,
            period,
        )
    )

    # Provenance is only auditable while the bytes still exist somewhere.
    sources = db.scalars(
        _session_scope(
            select(SessionSourceFile)
            .join(SessionRow, SessionRow.id == SessionSourceFile.session_id),
            project_id,
            period,
        )
    ).all()
    unauditable = sum(
        1
        for source in sources
        if not _readable(source.transcript_path) and not _readable(source.archive_path)
    )
    archived = sum(1 for source in sources if _readable(source.archive_path))
    original_gone = sum(
        1 for source in sources if not _readable(source.transcript_path)
    )

    # A transcript can exist without the model ever having been called: the
    # developer opened Claude Code, typed, and left. Those sessions carry no
    # cost because there was no request - which is why some days show sessions
    # and no spend.
    never_answered = db.scalar(
        _session_scope(
            select(func.count())
            .select_from(SessionRow)
            .where(SessionRow.assistant_message_count == 0),
            project_id,
            period,
        )
    )

    return {
        "sessions_without_model_response": int(never_answered or 0),
        "transcripts_archived": archived,
        "transcripts_original_missing": original_gone,
        "transcripts_unauditable": unauditable,
        "claude_code_versions": sorted(versions),
        "models_used": sorted(models),
        "sessions_editing_via_shell": int(shell_editing or 0),
        "sessions_likely_resumed": int(resumed or 0),
        "transcripts_with_corrupted_lines": int(corrupted.files or 0),
        "corrupted_lines": int(corrupted.lines or 0),
        "interrupted_sessions": int(interrupted or 0),
    }


def effort_shape(
    db: DbSession, *, project_id: int | None = None, period: Period | None = None
) -> dict[str, Any]:
    """The distribution of effort, not just its total.

    A sum hides whether 30 hours were one marathon or forty short sessions, and
    those are different phenomena. Concentration answers that in one number.
    """
    rows = db.scalars(
        _session_scope(
            select(SessionRow)
            .where(SessionRow.active_turn_seconds.is_not(None))
            .order_by(SessionRow.active_turn_seconds.desc()),
            project_id,
            period,
        )
    ).all()
    durations = [row.active_turn_seconds or 0.0 for row in rows]
    total = sum(durations)
    prompts = [row.human_prompt_count for row in rows]

    def _median(values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return float(ordered[middle])
        return (ordered[middle - 1] + ordered[middle]) / 2

    top = durations[: max(1, len(durations) // 5)] if durations else []
    return {
        "sessions_measured": len(durations),
        "median_session_seconds": _median(durations),
        "longest_session_seconds": max(durations) if durations else None,
        #: Share of all working time in the busiest fifth of sessions.
        "top_quintile_share": (sum(top) / total) if total else None,
        "median_prompts_per_session": _median([float(p) for p in prompts]),
        "max_prompts_in_a_session": max(prompts) if prompts else None,
    }


def survey_aggregate(
    db: DbSession, *, project_id: int | None = None, period: Period | None = None
) -> dict[str, Any]:
    """Means over declared answers, always reported with the response count.

    A mean of two Likert answers is not a finding, so ``responses`` travels with
    every number rather than being left for the reader to look up.
    """
    from app.models.entities import SessionSurvey

    row = db.execute(
        _session_scope(
            select(
                func.count().label("responses"),
                func.avg(SessionSurvey.perceived_quality).label("quality"),
                func.avg(SessionSurvey.perceived_productivity).label("productivity"),
                func.avg(SessionSurvey.perceived_effort).label("effort"),
                func.avg(SessionSurvey.trust_in_output).label("trust"),
                func.count().filter(SessionSurvey.rework_reported.is_(True)).label("rework"),
            )
            .select_from(SessionSurvey)
            .join(SessionRow, SessionRow.id == SessionSurvey.session_id),
            project_id,
            period,
        )
    ).one()

    def _mean(value: Any) -> float | None:
        return round(float(value), 2) if value is not None else None

    return {
        "responses": int(row.responses or 0),
        "mean_perceived_quality": _mean(row.quality),
        "mean_perceived_productivity": _mean(row.productivity),
        "mean_perceived_effort": _mean(row.effort),
        "mean_trust_in_output": _mean(row.trust),
        "rework_reported_count": int(row.rework or 0),
    }


def project_comparison(
    db: DbSession, *, period: Period | None = None
) -> list[dict[str, Any]]:
    """Every project side by side, normalised by effort.

    Totals cannot be compared across projects of different sizes; rates can.
    Each project is measured with its own scoped queries rather than one big
    join - joining sessions to commits fans the session rows out and silently
    multiplies the effort totals.
    """
    rows: list[dict[str, Any]] = []
    for project in db.scalars(select(Project).order_by(Project.name)).all():
        overview_data = overview(db, project_id=project.id, period=period)
        attribution_data = attribution(db, project_id=project.id, period=period)
        coverage_data = coverage(db, project_id=project.id, period=period)
        survey_data = survey_aggregate(db, project_id=project.id, period=period)
        hours = overview_data["active_turn_hours"]
        sessions = overview_data["sessions"]

        rows.append(
            {
                "id": project.id,
                "name": project.name,
                "sessions": sessions,
                "active_turn_hours": hours,
                "total_tokens": overview_data["total_tokens"],
                "cost_usd": overview_data["cost_usd"],
                "human_prompts": overview_data["human_prompts"],
                "tool_calls": overview_data["tool_calls"],
                "tool_error_rate": (
                    overview_data["tool_errors"] / overview_data["tool_calls"]
                    if overview_data["tool_calls"]
                    else None
                ),
                "prompts_per_hour": (
                    overview_data["human_prompts"] / hours if hours else None
                ),
                "cost_per_hour": (
                    overview_data["cost_usd"] / hours
                    if hours and overview_data["cost_usd"] is not None
                    else None
                ),
                "linked_commits": attribution_data["linked_commits"],
                "attribution_ratio": attribution_data["attribution_ratio"],
                "commit_coverage": (
                    coverage_data["with_linked_commit"] / sessions if sessions else None
                ),
                "survey_responses": survey_data["responses"],
                "mean_perceived_quality": survey_data["mean_perceived_quality"],
            }
        )
    return sorted(rows, key=lambda r: r["active_turn_hours"], reverse=True)
