"""Incremental synchronisation.

The rule from the brief: 100 sessions on the first run, then only the new ones.
A session still in progress must be re-read so it can be completed later.

Deciding whether to re-read a transcript is deliberately cheap: size and mtime
are compared first, and the SHA-256 is only computed when those differ. On this
machine that turns a full re-hash of ~150MB into a stat() per file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from sqlalchemy.orm import Session as DbSession

from app.collectors.claude.discovery import (
    SessionFile,
    discover_projects,
    discover_sessions,
    resolve_claude_data_path,
)
from app.collectors.claude.parser import file_sha256
from app.config import Settings, get_settings
from app.repositories.session_repository import SessionRepository
from app.services.aggregator import SessionAggregator
from app.services.archive import TranscriptArchive, utcnow


class SyncDecision(str, Enum):
    NEW = "new"
    CHANGED = "changed"
    FORCED = "forced"
    STILL_ACTIVE = "still_active"
    SKIPPED_UNCHANGED = "skipped_unchanged"
    EMPTY = "empty"
    FAILED = "failed"


@dataclass
class SyncOutcome:
    session_id: str
    transcript_path: str
    decision: SyncDecision
    error: str | None = None


@dataclass
class SyncSummary:
    started_at: datetime
    finished_at: datetime | None = None
    outcomes: list[SyncOutcome] = field(default_factory=list)
    pruned_projects: int = 0
    archived_files: int = 0
    archived_bytes: int = 0
    archive_errors: list[str] = field(default_factory=list)

    def count(self, decision: SyncDecision) -> int:
        return sum(1 for o in self.outcomes if o.decision is decision)

    @property
    def processed(self) -> int:
        return sum(
            self.count(d)
            for d in (
                SyncDecision.NEW,
                SyncDecision.CHANGED,
                SyncDecision.STILL_ACTIVE,
                SyncDecision.FORCED,
            )
        )

    @property
    def failures(self) -> list[SyncOutcome]:
        return [o for o in self.outcomes if o.decision is SyncDecision.FAILED]


class SyncService:
    def __init__(
        self,
        db: DbSession,
        aggregator: SessionAggregator | None = None,
        *,
        claude_data_path: Path | None = None,
        force: bool = False,
        settings: Settings | None = None,
        archive: TranscriptArchive | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.archive = archive or TranscriptArchive(
            self.settings.transcript_archive_path,
            enabled=self.settings.archive_transcripts,
        )
        self._archived: list[int] = []
        self._archive_errors: list[str] = []
        #: Reprocess every transcript regardless of its fingerprint. Needed
        #: after a collector change alters how existing data is interpreted.
        self.force = force
        self.repository = SessionRepository(db)
        self.aggregator = aggregator or SessionAggregator()
        self.claude_data_path = claude_data_path

    def _data_path(self) -> Path:
        if self.claude_data_path is not None:
            return self.claude_data_path
        return resolve_claude_data_path(None)

    def decide(self, session_file: SessionFile) -> tuple[SyncDecision, str | None]:
        """Whether this transcript needs processing, and its current digest.

        The digest is returned so the caller does not hash the file twice.
        """
        if session_file.size_bytes == 0:
            return SyncDecision.EMPTY, None
        if self.force:
            return SyncDecision.FORCED, None

        state = self.repository.get_sync_state(str(session_file.path))
        if state is None:
            return SyncDecision.NEW, None

        # A session that was still running last time may have grown, and Claude
        # Code also rewrites trailing records (title, cost-state) in place.
        if state.session_status == "active":
            return SyncDecision.STILL_ACTIVE, None

        if state.size_bytes != session_file.size_bytes:
            return SyncDecision.CHANGED, None
        if state.mtime is not None and _to_utc(state.mtime) != session_file.mtime:
            # mtime moved but the size did not: hash to find out if it matters.
            digest = file_sha256(session_file.path)
            if digest != state.sha256:
                return SyncDecision.CHANGED, digest
            return SyncDecision.SKIPPED_UNCHANGED, digest
        return SyncDecision.SKIPPED_UNCHANGED, None

    def sync_session(self, session_file: SessionFile) -> SyncOutcome:
        decision, _ = self.decide(session_file)
        if decision in (SyncDecision.SKIPPED_UNCHANGED, SyncDecision.EMPTY):
            return SyncOutcome(session_file.session_id, str(session_file.path), decision)
        try:
            report = self.aggregator.build(session_file)
            row = self.repository.save(report)
            self.repository.record_sync(report)
            self._archive_transcript(session_file, report, row.id)
        except Exception as exc:  # one bad transcript must not stop the run
            self.db.rollback()
            return SyncOutcome(
                session_file.session_id,
                str(session_file.path),
                SyncDecision.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
        self.db.commit()
        return SyncOutcome(session_file.session_id, str(session_file.path), decision)

    def sync_all(
        self, *, project_key: str | None = None, limit: int | None = None
    ) -> SyncSummary:
        summary = SyncSummary(started_at=datetime.now(timezone.utc))
        data_path = self._data_path()
        session_files = discover_sessions(data_path)
        if project_key:
            session_files = [s for s in session_files if s.project_key == project_key]
        if limit is not None:
            session_files = session_files[:limit]
        for session_file in session_files:
            summary.outcomes.append(self.sync_session(session_file))
        summary.archived_files = len(self._archived)
        summary.archived_bytes = sum(self._archived)
        summary.archive_errors = list(self._archive_errors)
        summary.pruned_projects = self.prune_orphan_projects()
        self.db.commit()
        summary.finished_at = datetime.now(timezone.utc)
        return summary

    def _archive_transcript(self, session_file, report, session_id: int) -> None:
        """Keep a verified copy of the bytes this session was derived from.

        Failures are recorded, never raised: the collected data is still valid
        without an archive - it just cannot be re-audited from source later.
        """
        source = report.source
        if source is None:
            return
        result = self.archive.store(
            session_file.path,
            project_key=session_file.project_key,
            session_id=report.session.claude_session_id,
            sha256=source.transcript_sha256,
        )
        if result.error:
            self._archive_errors.append(
                f"{report.session.claude_session_id}: {result.error}"
            )
            return
        if result.path is not None:
            self.repository.record_archive(session_id, str(result.path), utcnow())
            if result.copied:
                self._archived.append(result.bytes_written)

    def prune_orphan_projects(self) -> int:
        """Drop projects left with no sessions.

        Re-resolving project boundaries can move every session off a project
        row (an empty git repo that used to swallow two clients, say). The empty
        row would otherwise linger and be counted by the analytics queries.
        """
        from sqlalchemy import delete, func, select

        from app.models.entities import Project, Session as SessionRow

        orphans = self.db.scalars(
            select(Project.id)
            .outerjoin(SessionRow, SessionRow.project_id == Project.id)
            .group_by(Project.id)
            .having(func.count(SessionRow.id) == 0)
        ).all()
        if not orphans:
            return 0
        self.db.execute(delete(Project).where(Project.id.in_(orphans)))
        self.db.flush()
        return len(orphans)

    def discover_project_keys(self) -> list[str]:
        return [p.project_key for p in discover_projects(self._data_path())]


def _to_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; PostgreSQL hands back aware ones."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
