"""SQLAlchemy entities.

Two rules shape this schema:

* **Observed and inferred data never share a table.** Everything here except
  ``AIAnalysis`` is collected from a source. ``AIAnalysis`` is the only table
  holding LLM output, and it carries its own confidence and model columns.
* **Provenance is queryable.** ``SessionSourceFile`` records the transcript path
  and its SHA-256, so the chain ``metric → session → event → raw transcript``
  can be walked in SQL.

Types stay portable (``JSON``, not ``JSONB``/``ARRAY``) so the suite can run on
SQLite while production runs on PostgreSQL; the JSON variant below upgrades to
JSONB on PostgreSQL automatically.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

#: JSONB on PostgreSQL, plain JSON everywhere else.
JSONType = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Resolved project root - the git root when there is one, otherwise the
    #: deepest directory containing every recorded cwd. Never the lossy
    #: directory name under ~/.claude/projects.
    path: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    repository_url: Mapped[str | None] = mapped_column(String(1024))
    #: The opaque ~/.claude/projects directory name. Kept for traceability only.
    claude_project_key: Mapped[str | None] = mapped_column(String(512))
    is_git_repository: Mapped[bool | None] = mapped_column(Boolean)

    sessions: Mapped[list[Session]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    commits: Mapped[list[GitCommit]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    baseline: Mapped[ProjectBaseline | None] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False,
        passive_deletes=True,
    )


class Session(TimestampMixin, Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    claude_session_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )

    title: Mapped[str | None] = mapped_column(Text)
    slug: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Wall-clock span. Sessions routinely straddle days, so this is NOT a
    #: measure of working time - use ``active_turn_seconds`` for that.
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    active_turn_seconds: Mapped[float | None] = mapped_column(Float)
    api_duration_seconds: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    first_prompt: Mapped[str | None] = mapped_column(Text)
    last_prompt: Mapped[str | None] = mapped_column(Text)

    primary_cwd: Mapped[str | None] = mapped_column(String(1024))
    project_path_source: Mapped[str | None] = mapped_column(String(32))
    models_used: Mapped[list[str]] = mapped_column(JSONType, default=list)
    claude_code_versions: Mapped[list[str]] = mapped_column(JSONType, default=list)
    git_branches: Mapped[list[str]] = mapped_column(JSONType, default=list)
    cwds: Mapped[list[str]] = mapped_column(JSONType, default=list)

    # --- counters derived arithmetically from observed events ---
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    human_prompt_count: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_human_prompt_count: Mapped[int] = mapped_column(Integer, default=0)
    injected_user_message_count: Mapped[int] = mapped_column(Integer, default=0)
    assistant_message_count: Mapped[int] = mapped_column(Integer, default=0)
    assistant_record_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_error_count: Mapped[int] = mapped_column(Integer, default=0)
    thinking_block_count: Mapped[int] = mapped_column(Integer, default=0)
    files_touched: Mapped[int] = mapped_column(Integer, default=0)
    files_modified: Mapped[int] = mapped_column(Integer, default=0)
    files_read: Mapped[int] = mapped_column(Integer, default=0)
    files_outside_project: Mapped[int] = mapped_column(Integer, default=0)

    collection_warnings: Mapped[list[str]] = mapped_column(JSONType, default=list)

    project: Mapped[Project | None] = relationship(back_populates="sessions")
    messages: Mapped[list[Message]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )
    tool_calls: Mapped[list[ToolCall]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )
    usages: Mapped[list[Usage]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )
    changed_files: Mapped[list[ChangedFile]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )
    commit_links: Mapped[list[SessionCommitLink]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )
    source_file: Mapped[SessionSourceFile | None] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False,
        passive_deletes=True,
    )
    analyses: Mapped[list[AIAnalysis]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )
    survey: Mapped[SessionSurvey | None] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False,
        passive_deletes=True,
    )


class SessionSourceFile(Base):
    """Provenance: which bytes produced this session's rows."""

    __tablename__ = "session_source_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    transcript_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    transcript_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    transcript_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    transcript_mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_lines: Mapped[int] = mapped_column(Integer, default=0)
    parsed_lines: Mapped[int] = mapped_column(Integer, default=0)
    unparseable_lines: Mapped[int] = mapped_column(Integer, default=0)
    unknown_event_types: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    collector_version: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Where the raw transcript was copied to. Claude Code removes its own
    #: transcripts over time - 8 of 53 had already vanished when this was added -
    #: and without a copy the chain metric -> session -> event -> raw bytes
    #: breaks for those sessions, which the brief requires keeping intact.
    archive_path: Mapped[str | None] = mapped_column(String(1024))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[Session] = relationship(back_populates="source_file")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_session_sequence", "session_id", "sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Distinguishes a typed prompt from a tool result, a compact summary or an
    #: injected note - all of which Claude Code writes with role "user".
    message_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    content: Mapped[str | None] = mapped_column(Text)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    uuid: Mapped[str | None] = mapped_column(String(64))
    parent_uuid: Mapped[str | None] = mapped_column(String(64))
    origin_kind: Mapped[str | None] = mapped_column(String(32))
    prompt_source: Mapped[str | None] = mapped_column(String(32))
    prompt_id: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(64))
    is_sidechain: Mapped[bool] = mapped_column(Boolean, default=False)
    is_meta: Mapped[bool] = mapped_column(Boolean, default=False)

    session: Mapped[Session] = relationship(back_populates="messages")


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("ix_tool_calls_session_tool", "session_id", "tool_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    tool_use_id: Mapped[str | None] = mapped_column(String(64))
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    input: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    structured_result: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    output_preview: Mapped[str | None] = mapped_column(Text)
    output_chars: Mapped[int | None] = mapped_column(Integer)
    #: NULL means the call never received a result (interrupted session), which
    #: is different from False.
    is_error: Mapped[bool | None] = mapped_column(Boolean)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)

    session: Mapped[Session] = relationship(back_populates="tool_calls")


class Usage(Base):
    """Token/cost figures.

    One row per ``(session, source, model)``, plus one row per
    ``(session, source)`` with ``model IS NULL`` holding that source's total.
    ``source`` is mandatory because the sources legitimately disagree: ccusage
    de-duplicates resumed sessions, so its per-session totals differ from the
    transcript's. Aggregations must always filter on it.
    """

    __tablename__ = "usages"
    __table_args__ = (
        UniqueConstraint("session_id", "source", "model", name="uq_usage_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    #: NULL = the total across every model for this source.
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    #: NULL when the source could not determine a cost. Never a placeholder 0.
    cost_usd: Mapped[float | None] = mapped_column(Float)
    first_activity: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[list[str]] = mapped_column(JSONType, default=list)

    session: Mapped[Session] = relationship(back_populates="usages")


class GitCommit(Base):
    __tablename__ = "git_commits"
    __table_args__ = (
        UniqueConstraint("project_id", "commit_hash", name="uq_commit_per_project"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    commit_hash: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    author_name: Mapped[str | None] = mapped_column(String(255))
    author_email: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    authored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    files_changed: Mapped[int] = mapped_column(Integer, default=0)
    insertions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    changed_files: Mapped[list[str]] = mapped_column(JSONType, default=list)

    project: Mapped[Project] = relationship(back_populates="commits")
    session_links: Mapped[list[SessionCommitLink]] = relationship(
        back_populates="commit", cascade="all, delete-orphan", passive_deletes=True
    )


class SessionCommitLink(Base):
    """Evidence-bearing association between a session and a commit.

    Deliberately a table of its own rather than a nullable FK on ``git_commits``
    (which is what the original data model sketched): the same commit can relate
    to several sessions, and the association has to carry *why* it was made.
    ``likely_related`` is an association, never a claim of authorship - there is
    no ``caused_by_ai`` column and there must not be one.
    """

    __tablename__ = "session_commit_links"
    __table_args__ = (
        UniqueConstraint("session_id", "git_commit_id", name="uq_session_commit"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    git_commit_id: Mapped[int] = mapped_column(
        ForeignKey("git_commits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    likely_related: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    overlap_count: Mapped[int] = mapped_column(Integer, default=0)
    overlapping_files: Mapped[list[str]] = mapped_column(JSONType, default=list)
    minutes_after_session_end: Mapped[float | None] = mapped_column(Float)
    within_session_window: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list[str]] = mapped_column(JSONType, default=list)

    session: Mapped[Session] = relationship(back_populates="commit_links")
    commit: Mapped[GitCommit] = relationship(back_populates="session_links")


class ChangedFile(Base):
    """A file the session touched, as observed in the transcript."""

    __tablename__ = "changed_files"
    __table_args__ = (
        UniqueConstraint("session_id", "file_path", name="uq_session_file"),
        Index("ix_changed_files_path", "file_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    git_commit_id: Mapped[int | None] = mapped_column(
        ForeignKey("git_commits.id", ondelete="SET NULL")
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    change_type: Mapped[str] = mapped_column(String(16), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(128))
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    insertions: Mapped[int | None] = mapped_column(Integer)
    deletions: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: False for scratch/temp files outside the project tree.
    is_within_project: Mapped[bool | None] = mapped_column(Boolean, index=True)

    session: Mapped[Session] = relationship(back_populates="changed_files")


class AIAnalysis(TimestampMixin, Base):
    """The ONLY table holding inferred data. Populated in Etapa 9.

    Every column here is an LLM opinion, not an observation. ``confidence`` and
    ``model`` are mandatory context for reading any of it.
    """

    __tablename__ = "ai_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    objective: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    task_type: Mapped[str | None] = mapped_column(String(32), index=True)
    complexity: Mapped[str | None] = mapped_column(String(16))
    success: Mapped[bool | None] = mapped_column(Boolean)
    requires_rework: Mapped[bool | None] = mapped_column(Boolean, index=True)
    rework_evidence: Mapped[list[str]] = mapped_column(JSONType, default=list)
    technologies: Mapped[list[str]] = mapped_column(JSONType, default=list)
    problems_encountered: Mapped[list[str]] = mapped_column(JSONType, default=list)
    confidence: Mapped[float | None] = mapped_column(Float)
    model: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(32))
    prompt_version: Mapped[str | None] = mapped_column(String(32))

    session: Mapped[Session] = relationship(back_populates="analyses")


class SyncState(Base):
    """Incremental-sync bookkeeping, one row per transcript file.

    A transcript is re-processed only when its fingerprint changed or when the
    session may still be running. Sessions are re-read whole rather than from a
    byte offset: Claude Code rewrites trailing records (titles, cost-state), so
    resuming mid-file would miss updates.
    """

    __tablename__ = "sync_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    transcript_path: Mapped[str] = mapped_column(
        String(1024), unique=True, nullable=False, index=True
    )
    claude_session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parsed_lines: Mapped[int] = mapped_column(Integer, default=0)
    session_status: Mapped[str | None] = mapped_column(String(32))
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    sync_count: Mapped[int] = mapped_column(Integer, default=1)


class SessionSurvey(TimestampMixin, Base):
    """Self-reported perception, collected from the developer.

    This is the only table holding data that is neither observed from a tool nor
    inferred by a model: it is **declared** by a person. Perceived quality cannot
    be read out of a transcript, so it needs an instrument, and keeping it in its
    own table means an analysis can always say which of the three kinds of data a
    number came from.

    Likert answers are 1-5. ``None`` means the question was skipped, which is
    different from a neutral 3.
    """

    __tablename__ = "session_surveys"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    #: Who answered. Free text so a study with several participants still works.
    respondent: Mapped[str | None] = mapped_column(String(128))
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    #: 1 = muito ruim … 5 = muito boa
    perceived_quality: Mapped[int | None] = mapped_column(Integer)
    #: 1 = muito improdutiva … 5 = muito produtiva
    perceived_productivity: Mapped[int | None] = mapped_column(Integer)
    #: 1 = muito leve … 5 = muito pesado (mental effort - higher is worse)
    perceived_effort: Mapped[int | None] = mapped_column(Integer)
    #: 1 = nenhuma confiança … 5 = confiança total
    trust_in_output: Mapped[int | None] = mapped_column(Integer)
    #: What the developer says, independently of what the analyzer infers.
    rework_reported: Mapped[bool | None] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(Text)

    session: Mapped[Session] = relationship(back_populates="survey")


class ProjectBaseline(TimestampMixin, Base):
    """Git activity before AI adoption, for the same project.

    Without a comparison window, "productivity" has no denominator. The baseline
    is computed deliberately by a CLI command and stored with its parameters, so
    a reader can see exactly which window produced it - it is never recomputed
    silently behind a chart.
    """

    __tablename__ = "project_baselines"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    #: Boundary between "before AI" and "with AI". Defaults to the first session
    #: recorded for the project, which is when Claude Code actually arrived.
    adoption_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Both windows have this length, so the two halves are comparable.
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)

    before_commits: Mapped[int] = mapped_column(Integer, default=0)
    before_insertions: Mapped[int] = mapped_column(Integer, default=0)
    before_deletions: Mapped[int] = mapped_column(Integer, default=0)
    before_files_changed: Mapped[int] = mapped_column(Integer, default=0)
    before_active_days: Mapped[int] = mapped_column(Integer, default=0)
    before_authors: Mapped[list[str]] = mapped_column(JSONType, default=list)

    after_commits: Mapped[int] = mapped_column(Integer, default=0)
    after_insertions: Mapped[int] = mapped_column(Integer, default=0)
    after_deletions: Mapped[int] = mapped_column(Integer, default=0)
    after_files_changed: Mapped[int] = mapped_column(Integer, default=0)
    after_active_days: Mapped[int] = mapped_column(Integer, default=0)
    after_authors: Mapped[list[str]] = mapped_column(JSONType, default=list)

    #: Caveats that apply to this particular comparison.
    notes: Mapped[list[str]] = mapped_column(JSONType, default=list)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="baseline")
