"""Discovery and aggregation tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.collectors.ccusage.collector import CcusageCollector, CcusageError
from app.collectors.claude.discovery import (
    discover_projects,
    discover_sessions,
    find_session_file,
    resolve_claude_data_path,
    resolve_project_paths,
)
from app.services.aggregator import SessionAggregator
from app.services.report import render_text_report

from .conftest import FIXED_NOW, FIXTURES


# -- discovery ------------------------------------------------------------
def test_discovery_finds_projects_and_sessions(claude_home: Path):
    projects = discover_projects(claude_home)
    assert {p.project_key for p in projects} == {
        "-tmp-proj",
        "-tmp-with-dash-name",
        "-tmp-empty",
    }
    assert len(discover_sessions(claude_home)) == 2


def test_project_path_is_resolved_from_cwd_not_from_the_lossy_directory_name(
    claude_home: Path,
):
    """``-tmp-with-dash-name`` is ambiguous: /tmp/with/dash/name or
    /tmp/with-dash-name. Only the recorded cwd can disambiguate it."""
    project = next(
        p for p in discover_projects(claude_home) if p.project_key == "-tmp-with-dash-name"
    )
    resolve_project_paths(project)
    assert project.primary_path == "/tmp/with-dash-name"
    assert project.name == "with-dash-name"


def test_project_without_transcripts_has_no_resolvable_path(claude_home: Path):
    project = next(
        p for p in discover_projects(claude_home) if p.project_key == "-tmp-empty"
    )
    resolve_project_paths(project)
    assert project.primary_path is None
    assert project.name == "-tmp-empty"  # falls back to the key, invents nothing


def test_resolve_claude_data_path_rejects_a_missing_explicit_path(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        resolve_claude_data_path(str(tmp_path / "nope"))


def test_find_session_file(claude_home: Path):
    session = discover_sessions(claude_home)[0]
    assert find_session_file(claude_home, session.session_id) is not None
    assert find_session_file(claude_home, "missing") is None


# -- aggregation ----------------------------------------------------------
class NullCcusage(CcusageCollector):
    """ccusage that knows nothing - the resumed-session case."""

    def __init__(self):
        pass

    def collect(self, session_id):
        return None


class BrokenCcusage(CcusageCollector):
    def __init__(self):
        pass

    def collect(self, session_id):
        raise CcusageError("ccusage exited 1")


@pytest.fixture
def basic_report(transcript):
    aggregator = SessionAggregator(NullCcusage(), enable_git=True)
    return aggregator.build(transcript("session-basic"), now=FIXED_NOW)


def test_report_joins_the_sources(basic_report):
    assert basic_report.session.claude_session_id.startswith("11111111")
    assert basic_report.messages
    assert basic_report.tool_calls
    assert basic_report.tool_stats
    assert basic_report.source is not None


def test_missing_ccusage_data_falls_back_to_the_transcript_and_says_so(basic_report):
    sources = [u.source for u in basic_report.usage]
    assert sources == ["claude_transcript"]
    assert any("de-duplicates" in w for w in basic_report.warnings)


def test_ccusage_failure_does_not_abort_collection(transcript):
    report = SessionAggregator(BrokenCcusage()).build(
        transcript("session-basic"), now=FIXED_NOW
    )
    assert report.usage[0].source == "claude_transcript"
    assert any("ccusage unavailable" in w for w in report.warnings)


def test_project_without_git_is_reported_as_such(basic_report):
    # The fixture's cwd (/tmp/proj) does not exist, so git cannot be collected.
    assert basic_report.git is not None
    assert basic_report.git.is_repository is False
    assert any("project path" in w for w in basic_report.warnings)


def test_git_collection_can_be_disabled(transcript):
    report = SessionAggregator(NullCcusage(), enable_git=False).build(
        transcript("session-basic"), now=FIXED_NOW
    )
    assert report.git is None


def test_report_serialises_to_json_without_loss(basic_report):
    payload = basic_report.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False)
    restored = json.loads(text)
    assert set(restored) >= {
        "schema_version", "generated_at", "session", "metrics",
        "messages", "tool_calls", "files", "usage", "git", "source",
    }
    assert restored["session"]["claude_session_id"].startswith("11111111")


def test_observed_report_carries_no_inferred_fields(basic_report):
    """Inferred data must not leak into the observed report."""
    payload = basic_report.model_dump(mode="json")
    forbidden = {"objective", "summary", "task_type", "complexity",
                 "requires_rework", "confidence"}
    assert forbidden.isdisjoint(payload)


def test_text_report_renders(basic_report):
    text = render_text_report(basic_report)
    assert "SESSION 11111111" in text
    assert "FERRAMENTAS" in text
    assert "PROVENIENCIA" in text
    assert "AI_ANALYSIS_ENABLED=false" in text


def test_empty_transcript_produces_a_report_with_nulls(tmp_path: Path):
    target = tmp_path / "77777777-7777-7777-7777-777777777777.jsonl"
    target.write_bytes(b"")
    report = SessionAggregator(NullCcusage()).build(target, now=FIXED_NOW)
    assert report.session.started_at is None
    assert report.session.duration_seconds is None
    assert report.metrics.tool_call_count == 0
    assert render_text_report(report)  # must not raise


def test_scratch_files_are_flagged_as_outside_the_project(transcript):
    report = SessionAggregator(NullCcusage(), enable_git=False).build(
        transcript("session-subdirs-and-scratch"), now=FIXED_NOW
    )
    by_path = {f.file_path: f for f in report.files}
    assert by_path["/home/dev/proj/backend/src/api.ts"].is_within_project is True
    assert by_path["/tmp/scratch/grafico.png"].is_within_project is False
    assert report.metrics.files_outside_project == 1
    assert any("outside the project tree" in w for w in report.warnings)
    # Flagged, never dropped: it remains observed data.
    assert report.metrics.files_touched == 2


def test_scratch_files_are_reported_separately_in_the_text_report(transcript):
    report = SessionAggregator(NullCcusage(), enable_git=False).build(
        transcript("session-subdirs-and-scratch"), now=FIXED_NOW
    )
    text = render_text_report(report)
    assert "backend/src/api.ts" in text  # shown relative to the project root
    assert "1 arquivo(s) fora do projeto" in text
