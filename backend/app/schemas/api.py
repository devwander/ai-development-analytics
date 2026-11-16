"""Response models for the REST API.

Observed and inferred data stay visibly separate: everything here is observed
except ``AnalysisOut``, which carries ``inferred: true`` and a confidence.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class ProjectOut(ORMModel):
    id: int
    name: str
    path: str
    repository_url: str | None = None
    claude_project_key: str | None = None
    is_git_repository: bool | None = None


class ProjectMetrics(BaseModel):
    id: int
    name: str
    path: str
    repository_url: str | None = None
    is_git_repository: bool | None = None
    sessions: int
    active_turn_seconds: float
    total_tokens: int
    #: None when no source could determine a cost - never a placeholder zero.
    cost_usd: float | None = None
    first_session_at: datetime | None = None
    last_session_at: datetime | None = None


class SessionSummary(ORMModel):
    id: int
    claude_session_id: str
    project_id: int | None = None
    project_name: str | None = None
    title: str | None = None
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    active_turn_seconds: float | None = None
    models_used: list[str] = Field(default_factory=list)
    human_prompt_count: int = 0
    tool_call_count: int = 0
    total_tokens: int | None = None
    cost_usd: float | None = None
    usage_source: str | None = None
    has_analysis: bool = False


class SourceOut(ORMModel):
    transcript_path: str
    transcript_sha256: str
    transcript_bytes: int
    transcript_mtime: datetime | None = None
    total_lines: int
    parsed_lines: int
    unparseable_lines: int
    unknown_event_types: dict[str, int] = Field(default_factory=dict)
    collector_version: str
    archive_path: str | None = None
    archived_at: datetime | None = None
    #: Whether the raw bytes are still readable - original or archived copy.
    auditable: bool = True


class ChangedFileOut(ORMModel):
    file_path: str
    change_type: str
    tool_name: str | None = None
    occurrences: int
    insertions: int | None = None
    deletions: int | None = None
    is_within_project: bool | None = None


class SessionDetail(SessionSummary):
    first_prompt: str | None = None
    last_prompt: str | None = None
    slug: str | None = None
    primary_cwd: str | None = None
    project_path: str | None = None
    project_path_source: str | None = None
    claude_code_versions: list[str] = Field(default_factory=list)
    git_branches: list[str] = Field(default_factory=list)
    confirmed_human_prompt_count: int = 0
    injected_user_message_count: int = 0
    assistant_message_count: int = 0
    assistant_record_count: int = 0
    message_count: int = 0
    tool_error_count: int = 0
    thinking_block_count: int = 0
    files_touched: int = 0
    files_modified: int = 0
    files_read: int = 0
    files_outside_project: int = 0
    api_duration_seconds: float | None = None
    collection_warnings: list[str] = Field(default_factory=list)
    files: list[ChangedFileOut] = Field(default_factory=list)
    source: SourceOut | None = None
    has_survey: bool = False


class MessageOut(ORMModel):
    sequence: int
    role: str
    message_type: str
    content: str | None = None
    timestamp: datetime | None = None
    uuid: str | None = None
    parent_uuid: str | None = None
    origin_kind: str | None = None
    prompt_source: str | None = None
    model: str | None = None
    is_sidechain: bool = False
    is_meta: bool = False


class ToolCallOut(ORMModel):
    sequence: int
    tool_use_id: str | None = None
    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)
    output_preview: str | None = None
    output_chars: int | None = None
    #: None means the call never received a result (interrupted), not "no error".
    is_error: bool | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None


class ToolStatOut(BaseModel):
    tool_name: str
    calls: int
    errors: int


class ToolsResponse(BaseModel):
    stats: list[ToolStatOut]
    calls: Page[ToolCallOut]


class UsageOut(ORMModel):
    source: str
    model: str | None = None
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    total_tokens: int
    cost_usd: float | None = None
    first_activity: datetime | None = None
    last_activity: datetime | None = None
    collected_at: datetime | None = None
    notes: list[str] = Field(default_factory=list)


class UsageResponse(BaseModel):
    """Totals per source plus the per-model breakdown.

    ``preferred`` is the single row a caller should use when it needs one
    number; summing ``totals`` across sources would double-count.
    """

    preferred: UsageOut | None = None
    totals: list[UsageOut]
    by_model: list[UsageOut]


class CommitOut(ORMModel):
    commit_hash: str
    author_name: str | None = None
    author_email: str | None = None
    message: str
    authored_at: datetime | None = None
    committed_at: datetime | None = None
    files_changed: int
    insertions: int
    deletions: int
    changed_files: list[str] = Field(default_factory=list)


class CommitAssociation(BaseModel):
    """An association backed by evidence - never a claim of authorship."""

    commit: CommitOut
    likely_related: bool
    overlap_count: int
    overlapping_files: list[str] = Field(default_factory=list)
    minutes_after_session_end: float | None = None
    within_session_window: bool
    reasons: list[str] = Field(default_factory=list)


class GitResponse(BaseModel):
    repository_url: str | None = None
    is_git_repository: bool | None = None
    branches_during_session: list[str] = Field(default_factory=list)
    associations: list[CommitAssociation]
    likely_related_count: int


class AnalysisOut(ORMModel):
    """Inferred data. Every field is an LLM opinion, not an observation."""

    inferred: Literal[True] = True
    id: int
    objective: str | None = None
    summary: str | None = None
    result: str | None = None
    task_type: str | None = None
    complexity: str | None = None
    success: bool | None = None
    requires_rework: bool | None = None
    rework_evidence: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    problems_encountered: list[str] = Field(default_factory=list)
    confidence: float | None = None
    model: str | None = None
    provider: str | None = None
    created_at: datetime


class DayBucket(BaseModel):
    day: date
    sessions: int
    total_tokens: int
    cost_usd: float | None = None
    active_turn_seconds: float


class OverviewOut(BaseModel):
    sessions: int
    projects: int
    active_turn_seconds: float
    active_turn_hours: float
    wall_clock_seconds: float
    total_tokens: int
    cost_usd: float | None = None
    human_prompts: int
    tool_calls: int
    tool_errors: int
    #: How many sessions each source supplied a total for. Sources are never
    #: summed together; see app/services/analytics.py.
    usage_sources: dict[str, int]
    sessions_without_usage: int
    sessions_by_status: dict[str, int]
    per_day: list[DayBucket]
    #: Inferred. Empty until the LLM analyzer runs.
    task_types: dict[str, int]
    attribution: AttributionOut | None = None
    coverage: CoverageOut | None = None
    validity: ValidityThreatsOut | None = None
    effort_shape: EffortShapeOut | None = None
    survey: SurveyAggregate | None = None


class AttributionOut(BaseModel):
    """Upper bound on how much committed code passed through a tracked AI edit.

    Both halves are loose: the numerator misses shell-written files, the
    denominator counts whole commits. Read it as a ceiling, never as authorship.
    """

    ai_insertions: int
    ai_deletions: int
    ai_files: int
    linked_commits: int
    commit_insertions: int
    commit_deletions: int
    #: None when no linked commit added a line - a ratio would divide by zero.
    attribution_ratio: float | None = None


class CoverageOut(BaseModel):
    """How much of the sample supports each kind of claim."""

    sessions: int
    with_prompts: int
    in_git_project: int
    with_linked_commit: int
    with_analysis: int
    with_survey: int
    #: Sessions belonging to no project - invisible in every project view, which
    #: is why the global total can exceed the sum of the project rows.
    without_project: int = 0


class ValidityThreatsOut(BaseModel):
    """Properties of the dataset that qualify every other number."""

    #: Transcripts where the model was never called - they carry no cost
    #: because there was no request, which is why some days show sessions and
    #: no spend.
    sessions_without_model_response: int = 0
    #: A session is only auditable while its raw bytes still exist somewhere.
    transcripts_archived: int = 0
    transcripts_original_missing: int = 0
    transcripts_unauditable: int = 0
    claude_code_versions: list[str]
    models_used: list[str]
    sessions_editing_via_shell: int
    sessions_likely_resumed: int
    transcripts_with_corrupted_lines: int
    corrupted_lines: int
    interrupted_sessions: int


class EffortShapeOut(BaseModel):
    sessions_measured: int
    median_session_seconds: float | None = None
    longest_session_seconds: float | None = None
    top_quintile_share: float | None = None
    median_prompts_per_session: float | None = None
    max_prompts_in_a_session: int | None = None


class BaselineOut(ORMModel):
    """Git activity before and after AI adoption. Descriptive, not causal."""

    adoption_at: datetime
    window_days: int
    before_commits: int
    before_insertions: int
    before_deletions: int
    before_active_days: int
    before_authors: list[str]
    after_commits: int
    after_insertions: int
    after_deletions: int
    after_active_days: int
    after_authors: list[str]
    notes: list[str]
    computed_at: datetime
    rates: dict[str, float | None] = Field(default_factory=dict)


class SurveyOut(ORMModel):
    """Self-declared perception. Neither observed nor inferred."""

    declared: Literal[True] = True
    respondent: str | None = None
    answered_at: datetime
    perceived_quality: int | None = None
    perceived_productivity: int | None = None
    perceived_effort: int | None = None
    trust_in_output: int | None = None
    rework_reported: bool | None = None
    comment: str | None = None


class SurveyIn(BaseModel):
    respondent: str | None = None
    perceived_quality: int | None = Field(default=None, ge=1, le=5)
    perceived_productivity: int | None = Field(default=None, ge=1, le=5)
    perceived_effort: int | None = Field(default=None, ge=1, le=5)
    trust_in_output: int | None = Field(default=None, ge=1, le=5)
    rework_reported: bool | None = None
    comment: str | None = None


class SurveyAggregate(BaseModel):
    """Means over declared answers. Reported with n, because n is usually small."""

    responses: int
    mean_perceived_quality: float | None = None
    mean_perceived_productivity: float | None = None
    mean_perceived_effort: float | None = None
    mean_trust_in_output: float | None = None
    rework_reported_count: int = 0


class SessionExportOut(BaseModel):
    """Every collected field for one session, assembled into one document.

    The download counterpart to the individual endpoints above - nothing here
    is paginated, so a session with 3,000 tool calls returns all 3,000.
    """

    generated_at: datetime
    session: SessionDetail
    messages: list[MessageOut]
    tool_calls: list[ToolCallOut]
    tool_stats: list[ToolStatOut]
    usage: UsageResponse
    git: GitResponse
    analysis: AnalysisOut | None = None
    survey: SurveyOut | None = None


class ProjectComparisonRow(BaseModel):
    """One project, normalised so projects of different sizes can be compared."""

    id: int
    name: str
    sessions: int
    active_turn_hours: float
    total_tokens: int
    cost_usd: float | None = None
    human_prompts: int
    tool_calls: int
    tool_error_rate: float | None = None
    prompts_per_hour: float | None = None
    cost_per_hour: float | None = None
    linked_commits: int
    attribution_ratio: float | None = None
    commit_coverage: float | None = None
    survey_responses: int
    mean_perceived_quality: float | None = None


class ProjectAnalyticsOut(OverviewOut):
    project: ProjectOut
    tools: list[ToolStatOut]
    attribution: AttributionOut
    coverage: CoverageOut
    validity: ValidityThreatsOut
    effort_shape: EffortShapeOut
    survey: SurveyAggregate
    baseline: BaselineOut | None = None
