"""Persist a SessionReport, idempotently.

Re-syncing a session must converge on the same rows rather than accumulate
duplicates: a session that was ACTIVE at the last sync will be re-read after it
grows. Child rows are therefore replaced wholesale inside one transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from app.models.entities import (
    ChangedFile,
    GitCommit,
    Message,
    Project,
    Session,
    SessionCommitLink,
    SessionSourceFile,
    SyncState,
    ToolCall,
    Usage,
)
from app.repositories.sanitize import NullByteScrubber
from app.schemas.observed import SessionReport


class SessionRepository:
    def __init__(self, db: DbSession) -> None:
        self.db = db
        #: Replaced per save(); see app.repositories.sanitize.
        self.clean = NullByteScrubber()
        #: Archive location carried across a re-save, which replaces the
        #: provenance row wholesale.
        self._previous_archive: dict[int, tuple[str | None, object]] = {}

    # -- projects ---------------------------------------------------------
    def upsert_project(self, report: SessionReport) -> Project | None:
        path = report.session.project_path
        if not path:
            return None
        project = self.db.scalar(select(Project).where(Project.path == path))
        if project is None:
            project = Project(path=path, name=Path(path).name)
            self.db.add(project)
        project.name = Path(path).name
        project.claude_project_key = (
            report.session.claude_project_key or project.claude_project_key
        )
        if report.git is not None:
            project.is_git_repository = report.git.is_repository
            if report.git.repository_url:
                project.repository_url = report.git.repository_url
        self.db.flush()
        return project

    # -- sessions ---------------------------------------------------------
    def get_session(self, claude_session_id: str) -> Session | None:
        return self.db.scalar(
            select(Session).where(Session.claude_session_id == claude_session_id)
        )

    def save(self, report: SessionReport) -> Session:
        self.clean = NullByteScrubber()
        project = self.upsert_project(report)
        row = self.get_session(report.session.claude_session_id)
        if row is None:
            row = Session(claude_session_id=report.session.claude_session_id)
            self.db.add(row)
        else:
            if row.source_file is not None:
                self._previous_archive[row.id] = (
                    row.source_file.archive_path,
                    row.source_file.archived_at,
                )
            self._clear_children(row)

        self._apply_session_fields(row, report, project)
        self.db.flush()

        self._save_source(row, report)
        self._save_messages(row, report)
        self._save_tool_calls(row, report)
        self._save_usages(row, report)
        self._save_files(row, report)
        if project is not None:
            self._save_git(row, project, report)
        if self.clean.dirty:
            row.collection_warnings = [*row.collection_warnings, self.clean.warning()]
        self.db.flush()
        return row

    def _clear_children(self, row: Session) -> None:
        """Replace, never append: a re-read transcript is the full truth."""
        for model in (Message, ToolCall, Usage, ChangedFile, SessionCommitLink):
            self.db.execute(delete(model).where(model.session_id == row.id))
        self.db.execute(
            delete(SessionSourceFile).where(SessionSourceFile.session_id == row.id)
        )
        self.db.flush()

    def _apply_session_fields(
        self, row: Session, report: SessionReport, project: Project | None
    ) -> None:
        session = report.session
        metrics = report.metrics
        row.project_id = project.id if project else None
        row.title = self.clean(session.title)
        row.slug = session.slug
        row.started_at = session.started_at
        row.ended_at = session.ended_at
        row.duration_seconds = session.duration_seconds
        row.active_turn_seconds = metrics.active_turn_seconds
        row.api_duration_seconds = metrics.api_duration_seconds
        row.status = session.status.value
        row.first_prompt = self.clean(session.first_prompt)
        row.last_prompt = self.clean(session.last_prompt)
        row.primary_cwd = session.primary_cwd
        row.project_path_source = session.project_path_source
        row.models_used = list(session.models_used)
        row.claude_code_versions = list(session.claude_code_versions)
        row.git_branches = list(session.git_branches)
        row.cwds = list(session.cwds)
        row.collection_warnings = self.clean(list(report.warnings))
        for field in (
            "message_count", "human_prompt_count", "confirmed_human_prompt_count",
            "injected_user_message_count", "assistant_message_count",
            "assistant_record_count", "tool_call_count", "tool_error_count",
            "thinking_block_count", "files_touched", "files_modified",
            "files_read", "files_outside_project",
        ):
            setattr(row, field, getattr(metrics, field))

    def _save_source(self, row: Session, report: SessionReport) -> None:
        if report.source is None:
            return
        source = report.source
        previous = self._previous_archive.get(row.id)
        self.db.add(
            SessionSourceFile(
                session_id=row.id,
                transcript_path=source.transcript_path,
                transcript_sha256=source.transcript_sha256,
                transcript_bytes=source.transcript_bytes,
                transcript_mtime=source.transcript_mtime,
                total_lines=source.total_lines,
                parsed_lines=source.parsed_lines,
                unparseable_lines=source.unparseable_lines,
                unknown_event_types=dict(source.unknown_event_types),
                collector_version=source.collector_version,
                archive_path=previous[0] if previous else None,
                archived_at=previous[1] if previous else None,
            )
        )

    def _save_messages(self, row: Session, report: SessionReport) -> None:
        self.db.add_all(
            Message(
                session_id=row.id,
                sequence=message.sequence,
                role=message.role.value,
                message_type=message.message_type.value,
                content=self.clean(message.content),
                timestamp=message.timestamp,
                uuid=message.uuid,
                parent_uuid=message.parent_uuid,
                origin_kind=message.origin_kind,
                prompt_source=message.prompt_source,
                prompt_id=message.prompt_id,
                model=message.model,
                is_sidechain=message.is_sidechain,
                is_meta=message.is_meta,
            )
            for message in report.messages
        )

    def _save_tool_calls(self, row: Session, report: SessionReport) -> None:
        self.db.add_all(
            ToolCall(
                session_id=row.id,
                sequence=call.sequence,
                tool_use_id=call.tool_use_id,
                tool_name=call.tool_name,
                input=self.clean(call.input),
                structured_result=self.clean(call.structured_result),
                output_preview=self.clean(call.output_preview),
                output_chars=call.output_chars,
                is_error=call.is_error,
                started_at=call.started_at,
                finished_at=call.finished_at,
                duration_seconds=call.duration_seconds,
            )
            for call in report.tool_calls
        )

    def _save_usages(self, row: Session, report: SessionReport) -> None:
        for usage in report.usage:
            # model IS NULL marks the source's total, so a query can pick either
            # granularity without summing rows that overlap.
            self.db.add(
                Usage(
                    session_id=row.id,
                    source=usage.source,
                    model=None,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_creation_tokens=usage.cache_creation_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    total_tokens=usage.total_tokens,
                    cost_usd=usage.cost_usd,
                    first_activity=usage.first_activity,
                    last_activity=usage.last_activity,
                    collected_at=usage.collected_at,
                    notes=self.clean(list(usage.notes)),
                )
            )
            for model in usage.models:
                self.db.add(
                    Usage(
                        session_id=row.id,
                        source=usage.source,
                        model=model.model,
                        input_tokens=model.input_tokens,
                        output_tokens=model.output_tokens,
                        cache_creation_tokens=model.cache_creation_tokens,
                        cache_read_tokens=model.cache_read_tokens,
                        total_tokens=model.total_tokens,
                        cost_usd=model.cost_usd,
                        collected_at=usage.collected_at,
                        notes=[],
                    )
                )

    def _save_files(self, row: Session, report: SessionReport) -> None:
        self.db.add_all(
            ChangedFile(
                session_id=row.id,
                file_path=self.clean(touch.file_path),
                change_type=touch.change_type.value,
                tool_name=touch.tool_name,
                occurrences=touch.occurrences,
                insertions=touch.insertions,
                deletions=touch.deletions,
                first_seen_at=touch.first_seen_at,
                last_seen_at=touch.last_seen_at,
                is_within_project=touch.is_within_project,
            )
            for touch in report.files
        )

    def _save_git(self, row: Session, project: Project, report: SessionReport) -> None:
        git = report.git
        if git is None or not git.is_repository:
            return
        commits_by_hash: dict[str, GitCommit] = {}
        for commit in git.commits_in_window:
            existing = self.db.scalar(
                select(GitCommit).where(
                    GitCommit.project_id == project.id,
                    GitCommit.commit_hash == commit.commit_hash,
                )
            )
            if existing is None:
                existing = GitCommit(
                    project_id=project.id,
                    commit_hash=commit.commit_hash,
                    message=self.clean(commit.message),
                )
                self.db.add(existing)
            existing.author_name = commit.author_name
            existing.author_email = commit.author_email
            existing.message = self.clean(commit.message)
            existing.authored_at = commit.authored_at
            existing.committed_at = commit.committed_at
            existing.files_changed = commit.files_changed
            existing.insertions = commit.insertions
            existing.deletions = commit.deletions
            existing.changed_files = self.clean(list(commit.changed_files))
            commits_by_hash[commit.commit_hash] = existing
        self.db.flush()

        for correlation in git.correlations:
            commit = commits_by_hash.get(correlation.commit_hash)
            if commit is None:
                continue
            self.db.add(
                SessionCommitLink(
                    session_id=row.id,
                    git_commit_id=commit.id,
                    likely_related=correlation.likely_related,
                    overlap_count=correlation.overlap_count,
                    overlapping_files=self.clean(list(correlation.overlapping_files)),
                    minutes_after_session_end=correlation.minutes_after_session_end,
                    within_session_window=correlation.within_session_window,
                    reasons=self.clean(list(correlation.reasons)),
                )
            )

    # -- sync state -------------------------------------------------------
    def get_sync_state(self, transcript_path: str) -> SyncState | None:
        return self.db.scalar(
            select(SyncState).where(SyncState.transcript_path == transcript_path)
        )

    def record_sync(self, report: SessionReport) -> SyncState:
        source = report.source
        assert source is not None, "a report without provenance cannot be synced"
        state = self.get_sync_state(source.transcript_path)
        if state is None:
            state = SyncState(
                transcript_path=source.transcript_path,
                claude_session_id=report.session.claude_session_id,
                sha256=source.transcript_sha256,
                size_bytes=source.transcript_bytes,
                sync_count=0,
            )
            self.db.add(state)
        state.claude_session_id = report.session.claude_session_id
        state.sha256 = source.transcript_sha256
        state.size_bytes = source.transcript_bytes
        state.mtime = source.transcript_mtime
        state.parsed_lines = source.parsed_lines
        state.session_status = report.session.status.value
        state.last_synced_at = datetime.now(timezone.utc)
        state.sync_count = (state.sync_count or 0) + 1
        self.db.flush()
        return state


    def record_archive(self, session_id: int, path: str, archived_at) -> None:
        """Attach an archive location to a session's provenance row."""
        source = self.db.scalar(
            select(SessionSourceFile).where(SessionSourceFile.session_id == session_id)
        )
        if source is not None:
            source.archive_path = path
            source.archived_at = archived_at
            self.db.flush()
