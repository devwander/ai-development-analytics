"""Integration tests against the real Claude Code data on this machine.

These skip automatically when the environment has no Claude Code data, so the
suite stays runnable in CI and in Docker. They exist to catch schema drift:
if Claude Code changes its transcript format, the parser starts reporting
unknown event types and these tests notice.
"""

from __future__ import annotations

import pytest

from app.collectors.ccusage.collector import CcusageCollector, CcusageRunner
from app.collectors.claude.discovery import (
    discover_projects,
    discover_sessions,
    resolve_claude_data_path,
    resolve_project_paths,
)
from app.collectors.claude.parser import parse_transcript
from app.services.aggregator import SessionAggregator


@pytest.fixture(scope="module")
def data_path():
    try:
        return resolve_claude_data_path(None)
    except FileNotFoundError:
        pytest.skip("no Claude Code data directory on this machine")


@pytest.fixture(scope="module")
def sessions(data_path):
    found = [s for s in discover_sessions(data_path) if not s.is_empty]
    if not found:
        pytest.skip("no non-empty transcripts available")
    return found


def test_every_transcript_parses_without_raising(sessions):
    """The whole point of the parser: no transcript may ever crash collection."""
    for session_file in sessions:
        parsed = parse_transcript(session_file.path, project_key=session_file.project_key)
        assert parsed.session.claude_session_id
        assert parsed.source.total_lines >= parsed.source.parsed_lines


def test_no_unknown_event_types_across_the_real_dataset(sessions):
    """Fails when Claude Code introduces an event type the parser has not seen.

    That is the intended behaviour: schema drift should be loud.
    """
    unknown: dict[str, int] = {}
    for session_file in sessions:
        parsed = parse_transcript(session_file.path)
        for name, count in parsed.source.unknown_event_types.items():
            unknown[name] = unknown.get(name, 0) + count
    assert unknown == {}, f"unhandled transcript event types: {unknown}"


def test_session_id_always_matches_the_filename(sessions):
    for session_file in sessions:
        parsed = parse_transcript(session_file.path)
        assert parsed.session.claude_session_id == session_file.session_id


def test_every_project_path_resolves_from_cwd(data_path):
    for project in discover_projects(data_path):
        resolve_project_paths(project)
        if not any(not f.is_empty for f in project.session_files):
            continue
        assert project.primary_path, f"{project.project_key} has no resolvable cwd"


@pytest.mark.parametrize("_", [0])
def test_ccusage_index_matches_transcript_session_ids(sessions, _):
    runner = CcusageRunner()
    if not runner.is_available():
        pytest.skip("ccusage is not installed")
    index = CcusageCollector(runner).index()
    if not index:
        pytest.skip("ccusage reported no sessions")
    known = {s.session_id for s in sessions}
    # Every session ccusage knows about must exist as a transcript. The reverse
    # does not hold: ccusage de-duplicates resumed sessions away.
    assert set(index) <= known


def test_end_to_end_report_for_the_largest_session(sessions):
    largest = max(sessions, key=lambda s: s.size_bytes)
    report = SessionAggregator().build(largest)
    assert report.session.claude_session_id == largest.session_id
    assert report.source.transcript_sha256
    assert report.usage  # at least the transcript-derived usage is present
    assert report.model_dump(mode="json")


def test_transcript_usage_reproduces_ccusage_on_real_sessions(sessions):
    """The strongest available cross-check on the two independent sources.

    ccusage parses the same JSONL files but de-duplicates by message id across
    the *whole* dataset. Sessions that share messages with another transcript
    (resumed or forked - see ADR 0002) therefore cannot match per-session by
    construction, and are excluded. For every session that owns its messages
    outright, the parser must reproduce ccusage exactly.
    """
    runner = CcusageRunner()
    if not runner.is_available():
        pytest.skip("ccusage is not installed")
    index = CcusageCollector(runner).index()
    if not index:
        pytest.skip("ccusage reported no sessions")

    parsed_by_session = {
        s.session_id: parse_transcript(s.path) for s in sessions
    }
    owners: dict[tuple, set[str]] = {}
    for session_id, parsed in parsed_by_session.items():
        for key in parsed.usage_message_keys:
            owners.setdefault(key, set()).add(session_id)

    compared, skipped, mismatches = 0, 0, []
    for session_id, parsed in parsed_by_session.items():
        row = index.get(session_id)
        if row is None:
            continue
        if any(len(owners[key]) > 1 for key in parsed.usage_message_keys):
            skipped += 1
            continue
        compared += 1
        if parsed.usage.total_tokens != row["totalTokens"]:
            mismatches.append(
                (session_id, parsed.usage.total_tokens, row["totalTokens"])
            )

    assert compared > 0, "no independent session could be compared"
    assert not mismatches, f"token totals diverge from ccusage: {mismatches}"


def test_shared_message_ids_identify_resumed_sessions(sessions):
    """Documents the de-duplication overlap as a measurable property."""
    owners: dict[tuple, set[str]] = {}
    for session_file in sessions:
        parsed = parse_transcript(session_file.path)
        for key in parsed.usage_message_keys:
            owners.setdefault(key, set()).add(session_file.session_id)
    shared = {k: v for k, v in owners.items() if len(v) > 1}
    # Not an assertion about this machine's numbers - just that the signal is
    # computable, which is what the persistence layer will need.
    assert isinstance(shared, dict)
    assert all(len(v) > 1 for v in shared.values())
