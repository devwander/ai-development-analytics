"""Schemas for OBSERVED data.

Everything in this module is collected directly from a source (Claude Code
transcript, ccusage, git). Nothing here is inferred by an LLM. Inferred data
lives in ``app.schemas.inferred`` and is always kept separate so that a metric
can later be traced back to the raw event that produced it.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

DataSource = Literal["claude_transcript", "ccusage", "git", "filesystem"]


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageType(str, Enum):
    """Why a message exists, which is not the same as who "said" it.

    Claude Code writes tool results as ``type: "user"`` records. Counting those
    as human prompts would inflate every interaction metric, so the distinction
    is preserved explicitly.
    """

    HUMAN_PROMPT = "human_prompt"
    # A user-role record Claude Code injected itself: the summary written
    # when a conversation is compacted, or a meta/system-injected note.
    COMPACT_SUMMARY = "compact_summary"
    INJECTED_USER = "injected_user"
    TOOL_RESULT = "tool_result"
    ASSISTANT_TEXT = "assistant_text"
    ASSISTANT_THINKING = "assistant_thinking"
    ASSISTANT_TOOL_USE = "assistant_tool_use"
    SYSTEM = "system"
    ATTACHMENT = "attachment"
    OTHER = "other"


class Message(BaseModel):
    uuid: str | None = None
    parent_uuid: str | None = None
    role: MessageRole
    message_type: MessageType
    sequence: int
    timestamp: datetime | None = None
    content: str | None = None
    # Only set for genuine human input; ``None`` when Claude Code did not record
    # an origin (which is the case for tool-result "user" records).
    origin_kind: str | None = None
    prompt_source: str | None = None
    prompt_id: str | None = None
    model: str | None = None
    is_sidechain: bool = False
    is_meta: bool = False


class ToolCall(BaseModel):
    tool_use_id: str | None = None
    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)
    # Claude Code's own structured record of what the tool did (patches,
    # file paths, exit state). Kept verbatim - it is observed data.
    structured_result: dict[str, Any] | None = None
    output_preview: str | None = None
    output_chars: int | None = None
    is_error: bool | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    assistant_uuid: str | None = None
    result_uuid: str | None = None
    sequence: int = 0


class ChangeType(str, Enum):
    CREATED = "created"
    MODIFIED = "modified"
    READ = "read"
    DELETED = "deleted"
    RENAMED = "renamed"
    UNKNOWN = "unknown"


class FileTouch(BaseModel):
    """A file the session interacted with, as observed in the transcript.

    This records that *Claude touched a file*. It says nothing about whether the
    change was committed - that correlation is computed separately.
    """

    file_path: str
    change_type: ChangeType
    #: ``False`` for scratch/temp files outside the project tree, which are
    #: working artefacts rather than changes to the codebase. ``None`` when
    #: the project path could not be determined.
    is_within_project: bool | None = None
    tool_name: str | None = None
    occurrences: int = 1
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    insertions: int | None = None
    deletions: int | None = None


class ModelUsage(BaseModel):
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None


class Usage(BaseModel):
    source: DataSource
    models: list[ModelUsage] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    first_activity: datetime | None = None
    last_activity: datetime | None = None
    collected_at: datetime | None = None
    # Set when the source could not attribute usage to this session.
    notes: list[str] = Field(default_factory=list)


class GitCommit(BaseModel):
    commit_hash: str
    author_name: str | None = None
    author_email: str | None = None
    message: str
    authored_at: datetime | None = None
    committed_at: datetime | None = None
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    changed_files: list[str] = Field(default_factory=list)


class GitCorrelation(BaseModel):
    """Temporal/­file association between a session and a commit.

    Deliberately named "likely_related": the collector has no way to prove that
    the AI authored a commit, only that the two overlap.
    """

    commit_hash: str
    likely_related: bool
    overlapping_files: list[str] = Field(default_factory=list)
    overlap_count: int = 0
    minutes_after_session_end: float | None = None
    within_session_window: bool = False
    reasons: list[str] = Field(default_factory=list)


class GitInfo(BaseModel):
    is_repository: bool
    repository_root: str | None = None
    repository_url: str | None = None
    current_branch: str | None = None
    session_branches: list[str] = Field(default_factory=list)
    working_tree_dirty: bool | None = None
    working_tree_changes: int | None = None
    commits_in_window: list[GitCommit] = Field(default_factory=list)
    correlations: list[GitCorrelation] = Field(default_factory=list)
    error: str | None = None


class ToolStat(BaseModel):
    tool_name: str
    calls: int
    errors: int = 0
    total_duration_seconds: float | None = None


class SessionMetrics(BaseModel):
    """Counters derived arithmetically from observed events - never inferred."""

    message_count: int = 0
    #: User-role records that are not tool results and not Claude-injected.
    human_prompt_count: int = 0
    #: Subset of the above that Claude Code explicitly tagged
    #: ``origin.kind == "human"``. Older Claude Code versions did not write
    #: that field, so this can legitimately be lower than the count above.
    confirmed_human_prompt_count: int = 0
    injected_user_message_count: int = 0
    #: Distinct assistant API messages, de-duplicated by (message id,
    #: request id). Claude Code writes one record per content block, so the
    #: raw record count is always higher.
    assistant_message_count: int = 0
    assistant_record_count: int = 0
    tool_call_count: int = 0
    tool_error_count: int = 0
    thinking_block_count: int = 0
    files_touched: int = 0
    files_modified: int = 0
    files_read: int = 0
    #: Subset of ``files_touched`` living outside the project tree.
    files_outside_project: int = 0
    api_duration_seconds: float | None = None
    wall_clock_seconds: float | None = None
    active_turn_seconds: float | None = None


class SessionSource(BaseModel):
    """Provenance so that a metric can be traced back to its raw bytes."""

    transcript_path: str
    transcript_bytes: int
    transcript_mtime: datetime | None = None
    transcript_sha256: str
    total_lines: int
    parsed_lines: int
    unparseable_lines: int
    unknown_event_types: dict[str, int] = Field(default_factory=dict)
    collector_version: str


class Session(BaseModel):
    claude_session_id: str
    #: Project root. Derived from the deepest directory containing every
    #: recorded ``cwd``, then upgraded to the git repository root when the
    #: git collector finds one. See ``project_path_source``.
    project_path: str | None = None
    project_name: str | None = None
    project_path_source: Literal["cwd_common_ancestor", "primary_cwd", "git_root"] | None = None
    #: The single most frequent ``cwd``, kept verbatim as observed.
    primary_cwd: str | None = None
    claude_project_key: str | None = None
    title: str | None = None
    slug: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    status: SessionStatus = SessionStatus.UNKNOWN
    first_prompt: str | None = None
    last_prompt: str | None = None
    models_used: list[str] = Field(default_factory=list)
    claude_code_versions: list[str] = Field(default_factory=list)
    cwds: list[str] = Field(default_factory=list)
    git_branches: list[str] = Field(default_factory=list)
    is_sidechain: bool = False


class SessionReport(BaseModel):
    """The unit the aggregator produces: one session, all sources joined."""

    schema_version: str = "1.0"
    generated_at: datetime
    session: Session
    metrics: SessionMetrics
    messages: list[Message] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_stats: list[ToolStat] = Field(default_factory=list)
    files: list[FileTouch] = Field(default_factory=list)
    usage: list[Usage] = Field(default_factory=list)
    git: GitInfo | None = None
    source: SessionSource | None = None
    warnings: list[str] = Field(default_factory=list)
