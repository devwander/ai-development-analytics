"""Tests for the transcript parser - the most critical component of the MVP."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest

from app.collectors.claude.parser import parse_transcript, parse_timestamp
from app.schemas.observed import ChangeType, MessageType, SessionStatus

from .conftest import FIXED_NOW


@pytest.fixture
def basic(transcript):
    return parse_transcript(transcript("session-basic"), now=FIXED_NOW)


# -- session identity & metadata -----------------------------------------
def test_session_id_comes_from_the_filename_stem(basic):
    assert basic.session.claude_session_id == "11111111-1111-1111-1111-111111111111"
    assert basic.warnings == []


def test_project_path_comes_from_cwd_not_from_the_directory_name(basic):
    assert basic.session.project_path == "/tmp/proj"
    assert basic.session.project_name == "proj"


def test_title_and_prompts_are_extracted(basic):
    assert basic.session.title == "Validação do formulário de produto"
    assert basic.session.first_prompt.startswith("Implemente a validação")
    assert basic.session.slug == "witty-fixture"


def test_timestamps_and_duration(basic):
    assert basic.session.started_at.isoformat() == "2026-08-01T10:00:00+00:00"
    assert basic.session.ended_at.isoformat() == "2026-08-01T10:00:35.500000+00:00"
    assert basic.session.duration_seconds == pytest.approx(35.5)
    # turn_duration is wall-clock-independent and collected separately.
    assert basic.metrics.active_turn_seconds == pytest.approx(35.0)


def test_status_is_completed_for_a_finished_session(basic):
    assert basic.session.status is SessionStatus.COMPLETED


# -- messages -------------------------------------------------------------
def test_messages_are_typed_and_ordered(basic):
    types = [m.message_type for m in basic.messages]
    assert types[0] is MessageType.HUMAN_PROMPT
    assert MessageType.ASSISTANT_TOOL_USE in types
    assert MessageType.TOOL_RESULT in types
    assert MessageType.ASSISTANT_TEXT in types
    assert [m.sequence for m in basic.messages] == sorted(
        m.sequence for m in basic.messages
    )


def test_tool_results_are_not_counted_as_human_prompts(basic):
    assert basic.metrics.human_prompt_count == 1
    assert basic.metrics.confirmed_human_prompt_count == 1
    assert basic.metrics.assistant_message_count == 4
    assert basic.metrics.assistant_record_count == 4
    assert basic.metrics.thinking_block_count == 1


def test_injected_user_records_are_separated_from_real_prompts(transcript):
    parsed = parse_transcript(transcript("session-injected-user"), now=FIXED_NOW)
    by_type = {m.uuid: m.message_type for m in parsed.messages}
    assert by_type["u1"] is MessageType.COMPACT_SUMMARY
    assert by_type["u2"] is MessageType.INJECTED_USER
    assert by_type["u3"] is MessageType.HUMAN_PROMPT
    assert by_type["u4"] is MessageType.INJECTED_USER  # origin.kind != "human"
    # No origin field at all: older Claude Code. Counted, but not "confirmed".
    assert by_type["u5"] is MessageType.HUMAN_PROMPT
    assert parsed.metrics.human_prompt_count == 2
    assert parsed.metrics.confirmed_human_prompt_count == 1
    assert parsed.metrics.injected_user_message_count == 3
    assert parsed.session.first_prompt == "continue"


# -- tool calls -----------------------------------------------------------
def test_tool_calls_are_paired_with_their_results(basic):
    calls = {c.tool_use_id: c for c in basic.tool_calls}
    assert set(calls) == {"toolu_1", "toolu_2", "toolu_3"}
    read = calls["toolu_1"]
    assert read.tool_name == "Read"
    assert read.input["file_path"] == "/tmp/proj/src/ProductForm.tsx"
    assert read.duration_seconds == pytest.approx(2.5)
    assert read.is_error is False


def test_tool_errors_are_counted(basic):
    assert basic.metrics.tool_error_count == 1
    bash = next(c for c in basic.tool_calls if c.tool_name == "Bash")
    assert bash.is_error is True
    stats = {s.tool_name: s for s in basic.tool_stats}
    assert stats["Bash"].errors == 1
    assert stats["Read"].errors == 0


def test_unmatched_tool_use_leaves_the_call_open(transcript):
    parsed = parse_transcript(transcript("session-incomplete"), now=FIXED_NOW)
    call = parsed.tool_calls[0]
    assert call.tool_name == "Bash"
    assert call.finished_at is None
    assert call.duration_seconds is None
    assert call.is_error is None  # unknown, not False


def test_interrupted_session_is_detected(transcript):
    parsed = parse_transcript(transcript("session-incomplete"), now=FIXED_NOW)
    assert parsed.session.status is SessionStatus.INTERRUPTED


# -- files ----------------------------------------------------------------
def test_file_touches_merge_and_prefer_the_strongest_change_type(basic):
    files = {f.file_path: f for f in basic.files}
    form = files["/tmp/proj/src/ProductForm.tsx"]
    # Read then Edit then file-history-delta: the write wins over the read.
    assert form.change_type is ChangeType.MODIFIED
    assert form.occurrences >= 3


def test_line_counts_come_from_the_structured_patch(basic):
    form = next(f for f in basic.files if f.file_path.endswith("ProductForm.tsx"))
    assert form.insertions == 2
    assert form.deletions == 1


def test_metrics_count_reads_and_writes_separately(basic):
    assert basic.metrics.files_modified == 1
    assert basic.metrics.files_read == 0  # the only read file was later edited


# -- usage ----------------------------------------------------------------
def test_usage_aggregates_cache_tokens_per_model(basic):
    usage = basic.usage
    assert usage.source == "claude_transcript"
    assert usage.input_tokens == 10 + 5 + 2 + 3
    assert usage.output_tokens == 40 + 20 + 8 + 15
    assert usage.cache_creation_tokens == 100 + 50 + 0 + 10
    assert usage.cache_read_tokens == 1000 + 2000 + 500 + 3000
    assert usage.total_tokens == sum(
        (usage.input_tokens, usage.output_tokens,
         usage.cache_creation_tokens, usage.cache_read_tokens)
    )
    models = {m.model: m for m in usage.models}
    assert set(models) == {"claude-opus-5", "claude-haiku-4-5-20251001"}
    assert models["claude-haiku-4-5-20251001"].cache_read_tokens == 500


def test_usage_is_not_double_counted_across_split_content_blocks(transcript):
    """Claude Code writes one record per content block, each repeating the same
    ``usage``. On a real session this inflated the total by 2.3x; after
    de-duplicating by (message id, request id) the total matched ccusage exactly.
    """
    parsed = parse_transcript(transcript("session-split-blocks"), now=FIXED_NOW)
    # 3 records for msg_A + 1 for msg_B, but only two API messages were billed.
    assert parsed.metrics.assistant_record_count == 4
    assert parsed.metrics.assistant_message_count == 2
    assert parsed.usage.input_tokens == 10 + 1
    assert parsed.usage.output_tokens == 20 + 2
    assert parsed.usage.cache_creation_tokens == 30 + 3
    assert parsed.usage.cache_read_tokens == 40 + 4
    assert any("not double-counted" in note for note in parsed.usage.notes)


def test_tool_calls_from_split_records_are_all_kept(transcript):
    """De-duplicating usage must not drop the tool calls in the extra records."""
    parsed = parse_transcript(transcript("session-split-blocks"), now=FIXED_NOW)
    assert {c.tool_use_id for c in parsed.tool_calls} == {"toolu_a", "toolu_b"}


def test_multiple_models_are_recorded(basic):
    assert basic.session.models_used == [
        "claude-haiku-4-5-20251001",
        "claude-opus-5",
    ]


def test_cost_comes_from_the_transcript_cost_state_event(basic):
    assert basic.usage.cost_usd == pytest.approx(0.1234)
    assert any("cost-state" in note for note in basic.usage.notes)
    assert basic.metrics.api_duration_seconds == pytest.approx(21.0)


# -- resilience -----------------------------------------------------------
def test_corrupted_lines_are_counted_not_fatal(transcript):
    parsed = parse_transcript(transcript("session-corrupted"), now=FIXED_NOW)
    assert parsed.session.claude_session_id == "22222222-2222-2222-2222-222222222222"
    # The NUL-padded line is stripped to empty and skipped; the truncated JSON,
    # the bare array and the plain text are all reported as unparseable.
    assert parsed.source.unparseable_lines == 3
    assert parsed.metrics.assistant_message_count == 2
    assert any("unparseable" in w for w in parsed.warnings)


def test_unknown_event_types_are_reported_without_breaking_parsing(transcript):
    parsed = parse_transcript(transcript("session-unknown-events"), now=FIXED_NOW)
    assert parsed.source.unknown_event_types == {
        "quantum-telemetry-v9": 2,
        "another-future-event": 1,
    }
    assert parsed.metrics.human_prompt_count == 1
    assert parsed.session.models_used == ["claude-sonnet-5"]
    assert any("unknown event types" in w for w in parsed.warnings)


def test_empty_transcript_yields_unknown_rather_than_invented_data(
    tmp_path: Path, fixtures_dir: Path
):
    target = tmp_path / "66666666-6666-6666-6666-666666666666.jsonl"
    target.write_bytes(b"")
    # Without events, status falls back to the file mtime; pin it well before
    # FIXED_NOW so the test does not depend on the real clock.
    old = (FIXED_NOW - timedelta(days=1)).timestamp()
    os.utime(target, (old, old))
    parsed = parse_transcript(target, now=FIXED_NOW)
    assert parsed.session.status is SessionStatus.UNKNOWN
    assert parsed.session.started_at is None
    assert parsed.session.duration_seconds is None
    assert parsed.session.project_path is None
    assert parsed.usage.total_tokens == 0
    assert parsed.usage.cost_usd is None


def test_provenance_is_recorded_for_auditability(basic, transcript):
    source = basic.source
    assert len(source.transcript_sha256) == 64
    assert source.transcript_bytes > 0
    assert source.total_lines == source.parsed_lines + source.unparseable_lines
    assert source.collector_version.startswith("claude-transcript/")


# -- helpers --------------------------------------------------------------
@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-08-01T10:00:00.000Z", "2026-08-01T10:00:00+00:00"),
        ("2026-08-01T07:00:00-03:00", "2026-08-01T10:00:00+00:00"),
        ("not a date", None),
        (None, None),
        (12345, None),
    ],
)
def test_parse_timestamp_normalises_to_utc(value, expected):
    result = parse_timestamp(value)
    assert (result.isoformat() if result else None) == expected


# -- project boundary & untrustworthy cost -------------------------------
def test_project_root_is_the_common_ancestor_of_every_cwd(transcript):
    """A session moving between backend/ and frontend/ belongs to their parent.

    On a real transcript the most frequent cwd was ``.../projeto-x/backend`` while
    the project is ``projeto-x``; naming the project after the busiest subdirectory
    was wrong.
    """
    parsed = parse_transcript(transcript("session-subdirs-and-scratch"), now=FIXED_NOW)
    assert parsed.session.project_path == "/home/dev/proj"
    assert parsed.session.project_name == "proj"
    assert parsed.session.project_path_source == "cwd_common_ancestor"
    # The raw observation is kept alongside the derived one.
    assert parsed.session.primary_cwd == "/home/dev/proj/backend"


def test_single_cwd_is_used_as_is(basic):
    assert basic.session.project_path == "/tmp/proj"
    assert basic.session.project_path_source == "primary_cwd"


def test_unrelated_cwds_do_not_collapse_the_project_to_a_filesystem_root(tmp_path):
    target = tmp_path / "aaaaaaaa-0000-0000-0000-000000000000.jsonl"
    target.write_text(
        '{"type":"user","uuid":"u1","sessionId":"aaaaaaaa-0000-0000-0000-000000000000",'
        '"timestamp":"2026-08-07T07:00:00.000Z","cwd":"/home/dev/a",'
        '"origin":{"kind":"human"},"message":{"role":"user","content":"x"}}\n'
        '{"type":"user","uuid":"u2","sessionId":"aaaaaaaa-0000-0000-0000-000000000000",'
        '"timestamp":"2026-08-07T07:00:01.000Z","cwd":"/opt/b",'
        '"origin":{"kind":"human"},"message":{"role":"user","content":"y"}}\n',
        encoding="utf-8",
    )
    parsed = parse_transcript(target, now=FIXED_NOW)
    assert parsed.session.project_path != "/"
    assert parsed.session.project_path_source == "primary_cwd"


def test_reset_cost_state_does_not_report_a_fake_zero_cost(transcript):
    """Claude Code writes a zeroed cost-state after a rate-limit checkpoint.

    Its ``totalCostUSD: 0`` with an empty ``modelUsage`` is a reset counter, not
    a session that cost nothing. Reporting it as $0.00 would be inventing data.
    """
    parsed = parse_transcript(transcript("session-subdirs-and-scratch"), now=FIXED_NOW)
    assert parsed.usage.cost_usd is None
    assert parsed.metrics.api_duration_seconds is None
    assert any("reset counter" in note for note in parsed.usage.notes)


def test_populated_cost_state_is_still_trusted(basic):
    assert basic.usage.cost_usd == pytest.approx(0.1234)
