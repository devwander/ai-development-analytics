"""Join the three collectors into a single SessionReport.

The aggregator never invents a value. When a source cannot answer, the field
stays ``None`` and the reason is appended to ``warnings``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from app.collectors.ccusage.collector import CcusageCollector, CcusageError
from app.collectors.claude.discovery import SessionFile
from app.collectors.claude.parser import parse_transcript
from app.collectors.git.collector import GitCollector, find_repository_root
from app.schemas.observed import GitInfo, SessionReport


class SessionAggregator:
    def __init__(
        self,
        ccusage: CcusageCollector | None = None,
        *,
        enable_ccusage: bool = True,
        enable_git: bool = True,
    ) -> None:
        self.ccusage = ccusage or CcusageCollector()
        self.enable_ccusage = enable_ccusage
        self.enable_git = enable_git

    def build(
        self,
        transcript_path: Path | SessionFile,
        *,
        now: datetime | None = None,
    ) -> SessionReport:
        if isinstance(transcript_path, SessionFile):
            project_key = transcript_path.project_key
            path = transcript_path.path
        else:
            path = Path(transcript_path)
            project_key = path.parent.name

        now = now or datetime.now(timezone.utc)
        parsed = parse_transcript(path, project_key=project_key, now=now)
        warnings = list(parsed.warnings)
        usages = [parsed.usage]

        if self.enable_ccusage:
            try:
                ccusage_usage = self.ccusage.collect(parsed.session.claude_session_id)
            except CcusageError as exc:
                ccusage_usage = None
                warnings.append(f"ccusage unavailable: {exc}")
            else:
                if ccusage_usage is None:
                    warnings.append(
                        "ccusage reports no usage for this session id. It de-duplicates "
                        "by message id across the dataset, so a resumed or forked "
                        "session is attributed to its parent. Transcript-derived usage "
                        "is used instead."
                    )
                else:
                    usages.append(ccusage_usage)

        git_info: GitInfo | None = None
        if self.enable_git:
            git_info = self._collect_git(parsed, warnings)
            # The git root is a stronger answer for the project boundary than
            # anything the transcript alone can offer.
            if git_info and git_info.repository_root:
                parsed.session.project_path = git_info.repository_root
                parsed.session.project_name = Path(git_info.repository_root).name
                parsed.session.project_path_source = "git_root"

        _scope_files_to_project(parsed, warnings)

        return SessionReport(
            generated_at=now,
            session=parsed.session,
            metrics=parsed.metrics,
            messages=parsed.messages,
            tool_calls=parsed.tool_calls,
            tool_stats=parsed.tool_stats,
            files=parsed.files,
            usage=usages,
            git=git_info,
            source=parsed.source,
            warnings=warnings,
        )

    def _collect_git(self, parsed, warnings: list[str]) -> GitInfo | None:
        project_path = parsed.session.project_path
        if not project_path:
            warnings.append("no cwd recorded in the transcript; git collection skipped")
            return GitInfo(is_repository=False, error="project path unknown")
        if not Path(project_path).is_dir():
            warnings.append(f"project path no longer exists: {project_path}")
            return GitInfo(is_repository=False, error="project path no longer exists")

        # Resolve from the busiest working directory, not from the cwd common
        # ancestor: starting at the ancestor walks *up* past a repository nested
        # inside it, attributing the session to an outer repo that merely
        # contains it.
        candidates = [parsed.session.primary_cwd, project_path]
        root = None
        for candidate in candidates:
            if candidate and Path(candidate).is_dir():
                root = find_repository_root(candidate)
                if root is not None:
                    break
        if root is None:
            return GitInfo(is_repository=False, repository_root=None)

        collector = GitCollector(root)
        return collector.collect(
            session_start=parsed.session.started_at,
            session_end=parsed.session.ended_at,
            session_files=parsed.files,
            session_branches=parsed.session.git_branches,
            session_id=parsed.session.claude_session_id,
        )


def _scope_files_to_project(parsed, warnings: list[str]) -> None:
    """Mark files that live outside the project tree.

    Sessions write scratch files - screenshots, temporary HTML, downloaded
    assets - under /tmp. Those are working artefacts, not changes to the
    codebase, and counting them as project files distorts every file metric.
    They are flagged rather than dropped: they are still observed data.
    """
    project_path = parsed.session.project_path
    if not project_path:
        return
    root = os.path.normpath(project_path)
    outside = 0
    for touch in parsed.files:
        normalised = os.path.normpath(touch.file_path)
        within = normalised == root or normalised.startswith(root + os.sep)
        touch.is_within_project = within
        if not within:
            outside += 1
    parsed.metrics.files_outside_project = outside
    if outside:
        warnings.append(
            f"{outside} of {len(parsed.files)} file(s) touched lie outside the "
            "project tree (scratch/temporary files) and are flagged as such"
        )
