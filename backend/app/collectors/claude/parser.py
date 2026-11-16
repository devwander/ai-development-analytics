"""Parse a Claude Code JSONL transcript into structured, observed data.

Design rules (from the project brief):

* Never guess a format. Every event type handled here was observed in a real
  transcript on this machine; see ``docs/adr/0001-transcript-format.md``.
* Unknown event types must not break the collection - they are counted and
  reported so the schema drift is visible instead of silent.
* Corrupted lines must not break the collection either. Real transcripts on
  this machine contain a NUL-byte-padded line, so this is not hypothetical.
* Raw data is never replaced. The parser records the transcript path and its
  SHA-256 so any derived metric can be traced back to the original bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from app.schemas.observed import (
    ChangeType,
    FileTouch,
    Message,
    MessageRole,
    MessageType,
    ModelUsage,
    Session,
    SessionMetrics,
    SessionSource,
    SessionStatus,
    ToolCall,
    ToolStat,
    Usage,
)

COLLECTOR_VERSION = "claude-transcript/0.1.0"

#: Event types observed in real transcripts. Anything outside this set is
#: reported as an unknown event rather than silently dropped.
KNOWN_EVENT_TYPES = frozenset(
    {
        "user",
        "assistant",
        "system",
        "attachment",
        "summary",
        "mode",
        "permission-mode",
        "ai-title",
        "custom-title",
        "last-prompt",
        "file-history-delta",
        "file-history-snapshot",
        "queue-operation",
        "cost-state",
        "bridge-session",
        "atis-latch",
        "agent-name",
        "frame-link",
        "artifact-autoreact-ledger",
        "artifact-comment-monitor",
    }
)

#: tool name -> input key holding the file path, and what the tool does to it.
FILE_TOOLS: dict[str, tuple[str, ChangeType]] = {
    "Read": ("file_path", ChangeType.READ),
    "Edit": ("file_path", ChangeType.MODIFIED),
    "Write": ("file_path", ChangeType.CREATED),
    "NotebookEdit": ("notebook_path", ChangeType.MODIFIED),
}

_INTERRUPT_MARKERS = (
    "[Request interrupted by user",
    "[Request interrupted by the user",
)


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _patch_line_counts(structured_patch: Any) -> tuple[int, int]:
    """Count +/- lines in Claude Code's ``structuredPatch`` hunks."""
    insertions = deletions = 0
    if not isinstance(structured_patch, list):
        return 0, 0
    for hunk in structured_patch:
        if not isinstance(hunk, dict):
            continue
        for line in hunk.get("lines") or []:
            if not isinstance(line, str) or not line:
                continue
            if line[0] == "+":
                insertions += 1
            elif line[0] == "-":
                deletions += 1
    return insertions, deletions


def iter_records(path: Path) -> Iterator[tuple[int, dict[str, Any] | None]]:
    """Yield ``(line_number, record_or_None)``. ``None`` means unparseable."""
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip().strip("\x00")
            if not line:
                continue
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                yield line_number, None
                continue
            if not isinstance(record, dict):
                yield line_number, None
                continue
            yield line_number, record


def file_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class ParsedTranscript:
    session: Session
    metrics: SessionMetrics
    messages: list[Message]
    tool_calls: list[ToolCall]
    tool_stats: list[ToolStat]
    files: list[FileTouch]
    usage: Usage
    source: SessionSource
    warnings: list[str] = field(default_factory=list)
    #: Cost/usage snapshot Claude Code itself writes into the transcript.
    cost_state: dict[str, Any] | None = None
    #: ``(message id, request id)`` of every API message billed to this
    #: session. Two transcripts sharing keys means one was resumed or
    #: forked from the other, and ccusage will attribute the overlap to
    #: only one of them. See ADR 0002.
    usage_message_keys: set[tuple[str | None, str | None]] = field(
        default_factory=set
    )


class _FileAccumulator:
    """Merge repeated touches of the same path into one FileTouch."""

    _PRECEDENCE = {
        ChangeType.READ: 0,
        ChangeType.UNKNOWN: 1,
        ChangeType.RENAMED: 2,
        ChangeType.MODIFIED: 3,
        ChangeType.CREATED: 4,
        ChangeType.DELETED: 5,
    }

    def __init__(self) -> None:
        self._items: dict[str, FileTouch] = {}

    def add(
        self,
        file_path: str,
        change_type: ChangeType,
        tool_name: str | None,
        timestamp: datetime | None,
        insertions: int | None = None,
        deletions: int | None = None,
    ) -> None:
        existing = self._items.get(file_path)
        if existing is None:
            self._items[file_path] = FileTouch(
                file_path=file_path,
                change_type=change_type,
                tool_name=tool_name,
                first_seen_at=timestamp,
                last_seen_at=timestamp,
                insertions=insertions,
                deletions=deletions,
            )
            return

        existing.occurrences += 1
        if self._PRECEDENCE[change_type] > self._PRECEDENCE[existing.change_type]:
            existing.change_type = change_type
            existing.tool_name = tool_name
        if timestamp:
            if existing.first_seen_at is None or timestamp < existing.first_seen_at:
                existing.first_seen_at = timestamp
            if existing.last_seen_at is None or timestamp > existing.last_seen_at:
                existing.last_seen_at = timestamp
        if insertions is not None:
            existing.insertions = (existing.insertions or 0) + insertions
        if deletions is not None:
            existing.deletions = (existing.deletions or 0) + deletions

    def result(self) -> list[FileTouch]:
        return sorted(self._items.values(), key=lambda f: f.file_path)


def parse_transcript(
    path: Path,
    *,
    project_key: str | None = None,
    active_threshold_minutes: int = 30,
    now: datetime | None = None,
) -> ParsedTranscript:
    path = Path(path)
    now = now or datetime.now(timezone.utc)

    session_ids: set[str] = set()
    cwds: dict[str, int] = {}
    branches: dict[str, int] = {}
    versions: set[str] = set()
    models: dict[str, int] = {}

    messages: list[Message] = []
    files = _FileAccumulator()
    tool_calls: list[ToolCall] = []
    pending_tools: dict[str, ToolCall] = {}
    unknown_events: dict[str, int] = {}
    warnings: list[str] = []

    title: str | None = None
    slug: str | None = None
    first_prompt: str | None = None
    last_prompt: str | None = None
    cost_state: dict[str, Any] | None = None

    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
    }
    per_model: dict[str, dict[str, int]] = {}

    first_ts: datetime | None = None
    last_ts: datetime | None = None
    turn_duration_ms = 0
    thinking_blocks = 0
    assistant_records = 0
    seen_usage_keys: set[tuple[str | None, str | None]] = set()
    duplicate_usage_records = 0
    human_prompts = 0
    confirmed_human_prompts = 0
    injected_user_messages = 0
    tool_errors = 0
    saw_interrupt = False
    sequence = 0
    total_lines = parsed_lines = unparseable_lines = 0

    for line_number, record in iter_records(path):
        total_lines += 1
        if record is None:
            unparseable_lines += 1
            warnings.append(f"unparseable JSON at line {line_number}")
            continue
        parsed_lines += 1

        event_type = record.get("type")
        if event_type not in KNOWN_EVENT_TYPES:
            key = str(event_type)
            unknown_events[key] = unknown_events.get(key, 0) + 1
            continue

        if sid := record.get("sessionId"):
            session_ids.add(str(sid))
        if cwd := record.get("cwd"):
            cwds[cwd] = cwds.get(cwd, 0) + 1
        if (branch := record.get("gitBranch")) and branch != "HEAD":
            branches[branch] = branches.get(branch, 0) + 1
        if version := record.get("version"):
            versions.add(str(version))
        if not slug and record.get("slug"):
            slug = str(record["slug"])

        timestamp = parse_timestamp(record.get("timestamp"))
        if timestamp:
            if first_ts is None or timestamp < first_ts:
                first_ts = timestamp
            if last_ts is None or timestamp > last_ts:
                last_ts = timestamp

        if event_type == "ai-title":
            title = record.get("aiTitle") or title
            continue
        if event_type == "custom-title":
            title = record.get("customTitle") or record.get("title") or title
            continue
        if event_type == "last-prompt":
            last_prompt = record.get("lastPrompt") or last_prompt
            continue
        if event_type == "cost-state":
            cost_state = record
            continue
        if event_type == "file-history-delta":
            tracking_path = record.get("trackingPath")
            backup = record.get("backup") or {}
            parent = backup.get("realParentDir")
            if isinstance(tracking_path, str) and tracking_path:
                absolute = tracking_path
                if parent and not tracking_path.startswith("/"):
                    absolute = str(Path(parent) / Path(tracking_path).name)
                files.add(
                    absolute,
                    ChangeType.MODIFIED,
                    "file-history",
                    parse_timestamp(record.get("timestamp")),
                )
            continue
        if event_type == "system":
            subtype = record.get("subtype")
            if subtype == "turn_duration":
                duration = record.get("durationMs")
                if isinstance(duration, (int, float)):
                    turn_duration_ms += int(duration)
            content = record.get("content")
            if isinstance(content, str) and any(
                marker in content for marker in _INTERRUPT_MARKERS
            ):
                saw_interrupt = True
            if isinstance(content, str) and content and subtype != "turn_duration":
                sequence += 1
                messages.append(
                    Message(
                        uuid=record.get("uuid"),
                        parent_uuid=record.get("parentUuid"),
                        role=MessageRole.SYSTEM,
                        message_type=MessageType.SYSTEM,
                        sequence=sequence,
                        timestamp=timestamp,
                        content=content,
                        is_meta=bool(record.get("isMeta")),
                        is_sidechain=bool(record.get("isSidechain")),
                    )
                )
            continue
        if event_type in {
            "attachment",
            "mode",
            "permission-mode",
            "file-history-snapshot",
            "queue-operation",
            "summary",
            "bridge-session",
            "atis-latch",
            "agent-name",
            "frame-link",
            "artifact-autoreact-ledger",
            "artifact-comment-monitor",
        }:
            continue

        message = record.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        blocks = (
            content
            if isinstance(content, list)
            else [{"type": "text", "text": content}]
            if isinstance(content, str)
            else []
        )

        if event_type == "user":
            tool_results = [
                b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"
            ]
            if tool_results:
                _absorb_tool_results(
                    record, tool_results, pending_tools, timestamp
                )
                tool_errors += sum(1 for b in tool_results if b.get("is_error"))
                sequence += 1
                messages.append(
                    Message(
                        uuid=record.get("uuid"),
                        parent_uuid=record.get("parentUuid"),
                        role=MessageRole.USER,
                        message_type=MessageType.TOOL_RESULT,
                        sequence=sequence,
                        timestamp=timestamp,
                        content=None,
                        prompt_id=record.get("promptId"),
                        is_sidechain=bool(record.get("isSidechain")),
                    )
                )
                continue

            text = _join_text(blocks)
            if text and any(marker in text for marker in _INTERRUPT_MARKERS):
                saw_interrupt = True
            if record.get("interruptedMessageId"):
                saw_interrupt = True
            origin = record.get("origin")
            origin_kind = origin.get("kind") if isinstance(origin, dict) else None
            message_type = _classify_user_record(record, origin_kind)
            if message_type is MessageType.HUMAN_PROMPT:
                human_prompts += 1
                if origin_kind == "human":
                    confirmed_human_prompts += 1
                if first_prompt is None and text:
                    first_prompt = text
                if text:
                    last_prompt = text
            else:
                injected_user_messages += 1
            sequence += 1
            messages.append(
                Message(
                    uuid=record.get("uuid"),
                    parent_uuid=record.get("parentUuid"),
                    role=MessageRole.USER,
                    message_type=message_type,
                    sequence=sequence,
                    timestamp=timestamp,
                    content=text,
                    origin_kind=origin_kind,
                    prompt_source=record.get("promptSource"),
                    prompt_id=record.get("promptId"),
                    is_sidechain=bool(record.get("isSidechain")),
                    is_meta=bool(record.get("isMeta")),
                )
            )
            continue

        # event_type == "assistant"
        assistant_records += 1
        model = message.get("model")
        if isinstance(model, str):
            models[model] = models.get(model, 0) + 1

        # Claude Code writes one `assistant` record per content block, and every
        # one of them repeats the SAME `usage` object for the whole API message.
        # Summing naively inflates token counts (2.3x on a real session here).
        # De-duplicating by (message id, request id) reproduces ccusage exactly.
        usage_key = (message.get("id"), record.get("requestId"))
        is_first_record_of_message = usage_key not in seen_usage_keys
        if is_first_record_of_message:
            seen_usage_keys.add(usage_key)
        else:
            duplicate_usage_records += 1

        usage_block = message.get("usage")
        if is_first_record_of_message and isinstance(usage_block, dict):
            counters = {
                "input_tokens": _as_int(usage_block.get("input_tokens")),
                "output_tokens": _as_int(usage_block.get("output_tokens")),
                "cache_creation_tokens": _as_int(
                    usage_block.get("cache_creation_input_tokens")
                ),
                "cache_read_tokens": _as_int(usage_block.get("cache_read_input_tokens")),
            }
            for key, value in counters.items():
                totals[key] += value
            bucket = per_model.setdefault(
                model or "unknown", dict.fromkeys(counters, 0)
            )
            for key, value in counters.items():
                bucket[key] += value

        text_parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                if isinstance(block.get("text"), str):
                    text_parts.append(block["text"])
            elif block_type == "thinking":
                thinking_blocks += 1
            elif block_type == "tool_use":
                sequence += 1
                call = _build_tool_call(record, block, timestamp, sequence)
                tool_calls.append(call)
                if call.tool_use_id:
                    pending_tools[call.tool_use_id] = call
                _record_file_from_tool_input(files, call, timestamp)
                messages.append(
                    Message(
                        uuid=record.get("uuid"),
                        parent_uuid=record.get("parentUuid"),
                        role=MessageRole.ASSISTANT,
                        message_type=MessageType.ASSISTANT_TOOL_USE,
                        sequence=sequence,
                        timestamp=timestamp,
                        content=call.tool_name,
                        model=model,
                        is_sidechain=bool(record.get("isSidechain")),
                    )
                )

        if text_parts:
            sequence += 1
            messages.append(
                Message(
                    uuid=record.get("uuid"),
                    parent_uuid=record.get("parentUuid"),
                    role=MessageRole.ASSISTANT,
                    message_type=MessageType.ASSISTANT_TEXT,
                    sequence=sequence,
                    timestamp=timestamp,
                    content="\n".join(text_parts),
                    model=model,
                    is_sidechain=bool(record.get("isSidechain")),
                )
            )

    # ---- assemble -------------------------------------------------------
    session_id = _pick_session_id(session_ids, path, warnings)
    primary_cwd = max(cwds.items(), key=lambda kv: kv[1])[0] if cwds else None
    project_path, project_path_source = _resolve_project_root(cwds, primary_cwd)

    for call in tool_calls:
        _record_file_from_tool_result(files, call)

    stats: dict[str, ToolStat] = {}
    for call in tool_calls:
        stat = stats.setdefault(call.tool_name, ToolStat(tool_name=call.tool_name, calls=0))
        stat.calls += 1
        if call.is_error:
            stat.errors += 1
        if call.duration_seconds is not None:
            stat.total_duration_seconds = (
                stat.total_duration_seconds or 0.0
            ) + call.duration_seconds

    file_list = files.result()
    duration = (last_ts - first_ts).total_seconds() if first_ts and last_ts else None
    status = _determine_status(
        path=path,
        last_ts=last_ts,
        now=now,
        active_threshold_minutes=active_threshold_minutes,
        saw_interrupt=saw_interrupt,
        assistant_messages=assistant_records,
    )

    usage = Usage(
        source="claude_transcript",
        models=[
            ModelUsage(
                model=name,
                total_tokens=sum(values.values()),
                **values,
            )
            for name, values in sorted(per_model.items())
        ],
        total_tokens=sum(totals.values()),
        first_activity=first_ts,
        last_activity=last_ts,
        collected_at=now,
        **totals,
    )
    if duplicate_usage_records:
        usage.notes.append(
            f"{duplicate_usage_records} assistant record(s) repeated the usage of "
            "an already-counted API message and were not double-counted"
        )
    if cost_state is not None:
        # A cost-state whose modelUsage is empty is a reset counter, not a real
        # zero. Claude Code writes one after a rate-limit checkpoint. Reporting
        # its 0 as the session cost would be inventing data, so it stays None.
        reported_cost = cost_state.get("totalCostUSD")
        has_model_usage = bool(cost_state.get("modelUsage"))
        if isinstance(reported_cost, (int, float)) and has_model_usage:
            usage.cost_usd = float(reported_cost)
            usage.notes.append("cost_usd taken from the transcript's cost-state event")
        else:
            usage.notes.append(
                "the transcript's cost-state event carries no model usage "
                "(a reset counter); cost left undetermined"
            )
        if cost_state.get("hasUnknownModelCost"):
            usage.notes.append("cost-state flagged a model with unknown pricing")

    session = Session(
        claude_session_id=session_id,
        project_path=project_path,
        project_name=Path(project_path).name if project_path else None,
        project_path_source=project_path_source,
        primary_cwd=primary_cwd,
        claude_project_key=project_key or path.parent.name,
        title=title,
        slug=slug,
        started_at=first_ts,
        ended_at=last_ts,
        duration_seconds=duration,
        status=status,
        first_prompt=first_prompt,
        last_prompt=last_prompt,
        models_used=sorted(models),
        claude_code_versions=sorted(versions),
        cwds=sorted(cwds),
        git_branches=sorted(branches),
    )

    metrics = SessionMetrics(
        message_count=len(messages),
        human_prompt_count=human_prompts,
        confirmed_human_prompt_count=confirmed_human_prompts,
        injected_user_message_count=injected_user_messages,
        assistant_message_count=len(seen_usage_keys),
        assistant_record_count=assistant_records,
        tool_call_count=len(tool_calls),
        tool_error_count=tool_errors,
        thinking_block_count=thinking_blocks,
        files_touched=len(file_list),
        files_modified=sum(
            1
            for f in file_list
            if f.change_type
            in (ChangeType.MODIFIED, ChangeType.CREATED, ChangeType.DELETED)
        ),
        files_read=sum(1 for f in file_list if f.change_type is ChangeType.READ),
        wall_clock_seconds=duration,
        active_turn_seconds=turn_duration_ms / 1000 if turn_duration_ms else None,
        api_duration_seconds=_api_duration_seconds(cost_state),
    )

    stat_result = path.stat()
    source = SessionSource(
        transcript_path=str(path),
        transcript_bytes=stat_result.st_size,
        transcript_mtime=datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc),
        transcript_sha256=file_sha256(path),
        total_lines=total_lines,
        parsed_lines=parsed_lines,
        unparseable_lines=unparseable_lines,
        unknown_event_types=unknown_events,
        collector_version=COLLECTOR_VERSION,
    )

    if unknown_events:
        warnings.append(
            "unknown event types encountered: "
            + ", ".join(f"{k}={v}" for k, v in sorted(unknown_events.items()))
        )

    return ParsedTranscript(
        session=session,
        metrics=metrics,
        messages=messages,
        tool_calls=tool_calls,
        tool_stats=sorted(stats.values(), key=lambda s: (-s.calls, s.tool_name)),
        files=file_list,
        usage=usage,
        source=source,
        warnings=warnings,
        cost_state=cost_state,
        usage_message_keys=seen_usage_keys,
    )


def _classify_user_record(
    record: dict[str, Any], origin_kind: str | None
) -> MessageType:
    """Separate real human input from user-role records Claude Code injects.

    Claude Code writes several things as ``type: "user"`` that no human typed:
    the summary produced when a conversation is compacted
    (``isCompactSummary``) and meta notes (``isMeta``). Counting those as
    prompts would inflate every interaction metric, so they get their own
    type. When ``origin.kind == "human"`` the record is human input, full stop.
    """
    if origin_kind == "human":
        return MessageType.HUMAN_PROMPT
    if record.get("isCompactSummary"):
        return MessageType.COMPACT_SUMMARY
    if record.get("isMeta") or record.get("isVisibleInTranscriptOnly"):
        return MessageType.INJECTED_USER
    if origin_kind is not None:
        return MessageType.INJECTED_USER
    # No origin field at all: written by a Claude Code version that predates
    # it. Treated as human input, but not counted as *confirmed*.
    return MessageType.HUMAN_PROMPT


def _resolve_project_root(
    cwds: dict[str, int], primary_cwd: str | None
) -> tuple[str | None, str | None]:
    """Pick the project root from every ``cwd`` the session recorded.

    A session routinely moves between subdirectories - on a real transcript the
    most frequent cwd was ``.../projeto-x/backend`` while the project is ``projeto-x``.
    The deepest directory containing all of them is the honest answer.

    Guarded against unrelated cwds: if the common ancestor is a filesystem or
    home root, the most frequent cwd is used instead rather than claiming the
    session belongs to ``/``.
    """
    if not cwds:
        return None, None
    if len(cwds) == 1:
        return primary_cwd, "primary_cwd"
    try:
        common = os.path.commonpath(list(cwds))
    except ValueError:  # different drives / relative paths mixed in
        return primary_cwd, "primary_cwd"
    depth = len(Path(common).parts)
    # "/", "/home", "/home/user" are not projects.
    if depth < 4:
        return primary_cwd, "primary_cwd"
    return common, "cwd_common_ancestor"


def _api_duration_seconds(cost_state: dict[str, Any] | None) -> float | None:
    if not cost_state or not cost_state.get("modelUsage"):
        return None
    value = cost_state.get("totalAPIDuration")
    return value / 1000 if isinstance(value, (int, float)) and value else None


def _as_int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _join_text(blocks: list[Any]) -> str | None:
    parts = [
        block["text"]
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return "\n".join(parts) if parts else None


def _pick_session_id(
    session_ids: set[str], path: Path, warnings: list[str]
) -> str:
    """The filename stem is the session id; records confirm it.

    Verified across every transcript on this machine: the stem always appears in
    the records' ``sessionId``. When it does not, the discrepancy is recorded
    rather than silently resolved.
    """
    stem = path.stem
    if stem in session_ids:
        return stem
    if session_ids:
        warnings.append(
            f"filename stem {stem!r} not found in record sessionIds {sorted(session_ids)!r}"
        )
        return sorted(session_ids)[0]
    return stem


def _build_tool_call(
    record: dict[str, Any],
    block: dict[str, Any],
    timestamp: datetime | None,
    sequence: int,
) -> ToolCall:
    tool_input = block.get("input")
    return ToolCall(
        tool_use_id=block.get("id"),
        tool_name=str(block.get("name") or "unknown"),
        input=tool_input if isinstance(tool_input, dict) else {},
        started_at=timestamp,
        assistant_uuid=record.get("uuid"),
        sequence=sequence,
    )


def _absorb_tool_results(
    record: dict[str, Any],
    tool_results: list[dict[str, Any]],
    pending: dict[str, ToolCall],
    timestamp: datetime | None,
) -> None:
    structured = record.get("toolUseResult")
    for block in tool_results:
        call = pending.pop(str(block.get("tool_use_id")), None)
        if call is None:
            continue
        call.finished_at = timestamp
        call.result_uuid = record.get("uuid")
        call.is_error = bool(block.get("is_error"))
        if call.started_at and timestamp:
            call.duration_seconds = (timestamp - call.started_at).total_seconds()
        text = _stringify_tool_output(block.get("content"))
        if text is not None:
            call.output_chars = len(text)
            call.output_preview = text[:2000]
        if isinstance(structured, dict):
            call.structured_result = structured


def _stringify_tool_output(content: Any) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def _record_file_from_tool_input(
    files: _FileAccumulator, call: ToolCall, timestamp: datetime | None
) -> None:
    mapping = FILE_TOOLS.get(call.tool_name)
    if mapping is None:
        return
    key, change_type = mapping
    file_path = call.input.get(key)
    if isinstance(file_path, str) and file_path:
        files.add(file_path, change_type, call.tool_name, timestamp)


def _record_file_from_tool_result(files: _FileAccumulator, call: ToolCall) -> None:
    """Refine a file touch using the structured result Claude Code stores.

    ``structuredPatch`` gives real +/- line counts, and Write's ``type`` field
    distinguishes a created file from an overwritten one.
    """
    structured = call.structured_result
    if not isinstance(structured, dict):
        return
    file_path = structured.get("filePath")
    if not isinstance(file_path, str) or not file_path:
        return
    insertions, deletions = _patch_line_counts(structured.get("structuredPatch"))
    result_type = structured.get("type")
    change_type = ChangeType.MODIFIED
    if result_type == "create":
        change_type = ChangeType.CREATED
    elif result_type == "update":
        change_type = ChangeType.MODIFIED
    elif call.tool_name == "Write":
        change_type = ChangeType.CREATED
    files.add(
        file_path,
        change_type,
        call.tool_name,
        call.finished_at or call.started_at,
        insertions=insertions or None,
        deletions=deletions or None,
    )


def _determine_status(
    *,
    path: Path,
    last_ts: datetime | None,
    now: datetime,
    active_threshold_minutes: int,
    saw_interrupt: bool,
    assistant_messages: int,
) -> SessionStatus:
    """Heuristic, and documented as such.

    Claude Code does not write an explicit "session ended" marker, so activity
    recency is the only available signal. A session whose transcript was touched
    within the threshold is reported as ACTIVE so the sync layer knows to
    re-read it later.
    """
    reference = last_ts
    if reference is None:
        try:
            reference = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return SessionStatus.UNKNOWN
    if now - reference <= timedelta(minutes=active_threshold_minutes):
        return SessionStatus.ACTIVE
    if saw_interrupt:
        return SessionStatus.INTERRUPTED
    if assistant_messages > 0:
        return SessionStatus.COMPLETED
    return SessionStatus.UNKNOWN
