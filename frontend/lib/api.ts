/**
 * Typed client for the analytics API.
 *
 * The types mirror the FastAPI response models. Two conventions from the
 * backend carry through and must not be smoothed over in the UI:
 *
 *  - `cost_usd` is `number | null`. Null means no source could determine a
 *    cost - it is never rendered as $0.00.
 *  - Every token/cost figure carries the source that produced it
 *    (`usage_source`), because ccusage and the transcript disagree by
 *    construction and are never summed.
 */

const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly path: string,
  ) {
    super(message);
  }
}

/**
 * Always hits the API. The dashboard reads a local database that the developer
 * re-syncs by hand, so a cached response would quietly show pre-sync numbers -
 * and stale analytics are worse than one extra query against localhost.
 */
async function get<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  } catch (cause) {
    throw new ApiError(
      `Could not reach the API at ${API_URL}. Is uvicorn running?`,
      0,
      path,
    );
  }
  if (!response.ok) {
    throw new ApiError(
      `${response.status} ${response.statusText}`,
      response.status,
      path,
    );
  }
  return (await response.json()) as T;
}

/** Returns null on 404 instead of throwing - used where absence is expected. */
async function getOptional<T>(path: string): Promise<T | null> {
  try {
    return await get<T>(path);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

// -- types ----------------------------------------------------------------

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface DayBucket {
  day: string;
  sessions: number;
  total_tokens: number;
  cost_usd: number | null;
  active_turn_seconds: number;
}

export interface Attribution {
  ai_insertions: number;
  ai_deletions: number;
  ai_files: number;
  linked_commits: number;
  commit_insertions: number;
  commit_deletions: number;
  /** Null when no linked commit added a line. Never rendered as 0%. */
  attribution_ratio: number | null;
}

export interface Coverage {
  sessions: number;
  with_prompts: number;
  in_git_project: number;
  with_linked_commit: number;
  with_analysis: number;
  with_survey: number;
  without_project: number;
}

export interface ValidityThreats {
  sessions_without_model_response: number;
  transcripts_archived: number;
  transcripts_original_missing: number;
  transcripts_unauditable: number;
  claude_code_versions: string[];
  models_used: string[];
  sessions_editing_via_shell: number;
  sessions_likely_resumed: number;
  transcripts_with_corrupted_lines: number;
  corrupted_lines: number;
  interrupted_sessions: number;
}

export interface EffortShape {
  sessions_measured: number;
  median_session_seconds: number | null;
  longest_session_seconds: number | null;
  top_quintile_share: number | null;
  median_prompts_per_session: number | null;
  max_prompts_in_a_session: number | null;
}

export interface SurveyAggregate {
  responses: number;
  mean_perceived_quality: number | null;
  mean_perceived_productivity: number | null;
  mean_perceived_effort: number | null;
  mean_trust_in_output: number | null;
  rework_reported_count: number;
}

export interface Baseline {
  adoption_at: string;
  window_days: number;
  before_commits: number;
  before_insertions: number;
  before_deletions: number;
  before_active_days: number;
  before_authors: string[];
  after_commits: number;
  after_insertions: number;
  after_deletions: number;
  after_active_days: number;
  after_authors: string[];
  notes: string[];
  computed_at: string;
  rates: Record<string, number | null>;
}

export interface Survey {
  declared: true;
  respondent: string | null;
  answered_at: string;
  perceived_quality: number | null;
  perceived_productivity: number | null;
  perceived_effort: number | null;
  trust_in_output: number | null;
  rework_reported: boolean | null;
  comment: string | null;
}

export interface ProjectComparisonRow {
  id: number;
  name: string;
  sessions: number;
  active_turn_hours: number;
  total_tokens: number;
  cost_usd: number | null;
  human_prompts: number;
  tool_calls: number;
  tool_error_rate: number | null;
  prompts_per_hour: number | null;
  cost_per_hour: number | null;
  linked_commits: number;
  attribution_ratio: number | null;
  commit_coverage: number | null;
  survey_responses: number;
  mean_perceived_quality: number | null;
}

export interface Overview {
  sessions: number;
  projects: number;
  active_turn_seconds: number;
  active_turn_hours: number;
  wall_clock_seconds: number;
  total_tokens: number;
  cost_usd: number | null;
  human_prompts: number;
  tool_calls: number;
  tool_errors: number;
  usage_sources: Record<string, number>;
  sessions_without_usage: number;
  sessions_by_status: Record<string, number>;
  per_day: DayBucket[];
  task_types: Record<string, number>;
  attribution: Attribution | null;
  coverage: Coverage | null;
  validity: ValidityThreats | null;
  effort_shape: EffortShape | null;
  survey: SurveyAggregate | null;
}

export interface ProjectMetrics {
  id: number;
  name: string;
  path: string;
  repository_url: string | null;
  is_git_repository: boolean | null;
  sessions: number;
  active_turn_seconds: number;
  total_tokens: number;
  cost_usd: number | null;
  first_session_at: string | null;
  last_session_at: string | null;
}

export interface ToolStat {
  tool_name: string;
  calls: number;
  errors: number;
}

export interface ProjectAnalytics extends Overview {
  project: { id: number; name: string; path: string; repository_url: string | null };
  tools: ToolStat[];
  attribution: Attribution;
  coverage: Coverage;
  validity: ValidityThreats;
  effort_shape: EffortShape;
  survey: SurveyAggregate;
  baseline: Baseline | null;
}

export interface SessionSummary {
  id: number;
  claude_session_id: string;
  project_id: number | null;
  project_name: string | null;
  title: string | null;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  active_turn_seconds: number | null;
  models_used: string[];
  human_prompt_count: number;
  tool_call_count: number;
  total_tokens: number | null;
  cost_usd: number | null;
  usage_source: string | null;
  has_analysis: boolean;
}

export interface ChangedFile {
  file_path: string;
  change_type: string;
  tool_name: string | null;
  occurrences: number;
  insertions: number | null;
  deletions: number | null;
  is_within_project: boolean | null;
}

export interface SourceInfo {
  transcript_path: string;
  transcript_sha256: string;
  transcript_bytes: number;
  total_lines: number;
  parsed_lines: number;
  unparseable_lines: number;
  unknown_event_types: Record<string, number>;
  collector_version: string;
  archive_path: string | null;
  archived_at: string | null;
  /** Whether the raw bytes are still readable - original or archived copy. */
  auditable: boolean;
}

export interface SessionDetail extends SessionSummary {
  first_prompt: string | null;
  last_prompt: string | null;
  slug: string | null;
  primary_cwd: string | null;
  project_path: string | null;
  project_path_source: string | null;
  claude_code_versions: string[];
  git_branches: string[];
  confirmed_human_prompt_count: number;
  injected_user_message_count: number;
  assistant_message_count: number;
  assistant_record_count: number;
  message_count: number;
  tool_error_count: number;
  thinking_block_count: number;
  files_touched: number;
  files_modified: number;
  files_read: number;
  files_outside_project: number;
  api_duration_seconds: number | null;
  collection_warnings: string[];
  files: ChangedFile[];
  source: SourceInfo | null;
  has_survey: boolean;
}

export interface Message {
  sequence: number;
  role: string;
  message_type: string;
  content: string | null;
  timestamp: string | null;
  origin_kind: string | null;
  prompt_source: string | null;
  model: string | null;
}

export interface ToolCall {
  sequence: number;
  tool_name: string;
  input: Record<string, unknown>;
  output_preview: string | null;
  is_error: boolean | null;
  duration_seconds: number | null;
  started_at: string | null;
}

export interface ToolsResponse {
  stats: ToolStat[];
  calls: Page<ToolCall>;
}

export interface Usage {
  source: string;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  total_tokens: number;
  cost_usd: number | null;
  collected_at: string | null;
  notes: string[];
}

export interface UsageResponse {
  preferred: Usage | null;
  totals: Usage[];
  by_model: Usage[];
}

export interface CommitAssociation {
  commit: {
    commit_hash: string;
    author_name: string | null;
    message: string;
    committed_at: string | null;
    files_changed: number;
    insertions: number;
    deletions: number;
  };
  likely_related: boolean;
  overlap_count: number;
  overlapping_files: string[];
  minutes_after_session_end: number | null;
  within_session_window: boolean;
  reasons: string[];
}

export interface GitResponse {
  repository_url: string | null;
  is_git_repository: boolean | null;
  branches_during_session: string[];
  associations: CommitAssociation[];
  likely_related_count: number;
}

export interface Analysis {
  inferred: true;
  objective: string | null;
  summary: string | null;
  result: string | null;
  task_type: string | null;
  complexity: string | null;
  success: boolean | null;
  requires_rework: boolean | null;
  rework_evidence: string[];
  technologies: string[];
  problems_encountered: string[];
  confidence: number | null;
  model: string | null;
  provider: string | null;
  created_at: string;
}

/** Everything the API knows about one session, unpaginated - the download payload. */
export interface SessionExport {
  generated_at: string;
  session: SessionDetail;
  messages: Message[];
  tool_calls: ToolCall[];
  tool_stats: ToolStat[];
  usage: UsageResponse;
  git: GitResponse;
  analysis: Analysis | null;
  survey: Survey | null;
}

/** The date range every scoped endpoint accepts. */
export interface PeriodQuery {
  since?: string;
  until?: string;
}

function periodQuery(period: PeriodQuery): string {
  const query = new URLSearchParams();
  if (period.since) query.set("since", period.since);
  if (period.until) query.set("until", period.until);
  const suffix = query.toString();
  return suffix ? `?${suffix}` : "";
}

// -- endpoints ------------------------------------------------------------

export const api = {
  overview: (period: PeriodQuery = {}) =>
    get<Overview>(`/analytics/overview${periodQuery(period)}`),
  projects: (period: PeriodQuery = {}) =>
    get<ProjectMetrics[]>(`/projects${periodQuery(period)}`),
  projectAnalytics: (id: number, period: PeriodQuery = {}) =>
    get<ProjectAnalytics>(`/analytics/projects/${id}${periodQuery(period)}`),
  projectComparison: (period: PeriodQuery = {}) =>
    get<ProjectComparisonRow[]>(`/analytics/projects${periodQuery(period)}`),
  survey: (id: string) => getOptional<Survey>(`/sessions/${id}/survey`),
  sessions: (
    params: Record<string, string | number | undefined> & PeriodQuery = {},
  ) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") query.set(key, String(value));
    }
    const suffix = query.toString() ? `?${query}` : "";
    return get<Page<SessionSummary>>(`/sessions${suffix}`);
  },
  session: (id: string) => get<SessionDetail>(`/sessions/${id}`),
  messages: (
    id: string,
    params: Record<string, string | number | string[]> = {},
  ) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      // message_type repeats: ?message_type=a&message_type=b
      if (Array.isArray(value)) {
        for (const item of value) query.append(key, item);
      } else {
        query.set(key, String(value));
      }
    }
    return get<Page<Message>>(`/sessions/${id}/messages?${query}`);
  },
  tools: (id: string) => get<ToolsResponse>(`/sessions/${id}/tools?limit=1`),
  usage: (id: string) => get<UsageResponse>(`/sessions/${id}/usage`),
  git: (id: string) => get<GitResponse>(`/sessions/${id}/git`),
  analysis: (id: string) => getOptional<Analysis>(`/sessions/${id}/analysis`),
  sessionExport: (id: string) => get<SessionExport>(`/sessions/${id}/export`),
};
