"""Incremental synchronisation tests.

The requirement from the brief: 100 sessions on the first run; on the second,
100 existing plus 3 new means 3 processed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models.entities import Message, Project, SyncState
from app.models.entities import Session as SessionRow
from app.services.aggregator import SessionAggregator
from app.services.sync import SyncDecision, SyncService

from .conftest import FIXTURES
from .test_discovery_and_aggregator import NullCcusage


@pytest.fixture
def service(db, claude_projects: Path) -> SyncService:
    return SyncService(
        db,
        SessionAggregator(NullCcusage(), enable_git=False),
        claude_data_path=claude_projects,
    )


def _add_transcript(claude_projects: Path, name: str, session_id: str) -> Path:
    target = claude_projects / "projects" / "-tmp-proj" / f"{session_id}.jsonl"
    shutil.copyfile(FIXTURES / f"{name}.jsonl", target)
    return target


def test_first_run_processes_everything(service, db):
    summary = service.sync_all()
    assert summary.count(SyncDecision.NEW) == 2
    assert summary.processed == 2
    assert not summary.failures
    assert db.scalar(select(func.count(SessionRow.id))) == 2
    assert db.scalar(select(func.count(SyncState.id))) == 2


def test_second_run_processes_nothing_when_nothing_changed(service, db):
    service.sync_all()
    summary = service.sync_all()
    assert summary.processed == 0
    assert summary.count(SyncDecision.SKIPPED_UNCHANGED) == 2


def test_only_the_new_transcript_is_processed(service, db, claude_projects):
    service.sync_all()
    _add_transcript(
        claude_projects, "session-injected-user", "55555555-5555-5555-5555-555555555555"
    )
    summary = service.sync_all()
    assert summary.count(SyncDecision.NEW) == 1
    assert summary.count(SyncDecision.SKIPPED_UNCHANGED) == 2
    assert summary.processed == 1
    assert db.scalar(select(func.count(SessionRow.id))) == 3


def test_a_grown_transcript_is_reprocessed_and_replaces_its_rows(
    service, db, claude_projects
):
    """A session that continued must be updated, not duplicated."""
    service.sync_all()
    before = db.scalar(select(func.count(Message.id)))

    target = claude_projects / "projects" / "-tmp-proj" / (
        "11111111-1111-1111-1111-111111111111.jsonl"
    )
    with target.open("a", encoding="utf-8") as handle:
        handle.write(
            '{"type":"user","uuid":"u9","sessionId":'
            '"11111111-1111-1111-1111-111111111111","timestamp":'
            '"2026-08-01T10:05:00.000Z","cwd":"/tmp/proj","origin":{"kind":"human"},'
            '"promptSource":"typed","message":{"role":"user","content":"mais uma coisa"}}\n'
        )

    summary = service.sync_all()
    assert summary.count(SyncDecision.CHANGED) == 1
    assert db.scalar(select(func.count(SessionRow.id))) == 2
    assert db.scalar(select(func.count(Message.id))) == before + 1
    row = db.scalar(
        select(SessionRow).where(
            SessionRow.claude_session_id == "11111111-1111-1111-1111-111111111111"
        )
    )
    assert row.human_prompt_count == 2
    assert row.last_prompt == "mais uma coisa"


def test_an_active_session_is_re_read_on_every_run(service, db, claude_projects):
    """Claude Code rewrites trailing records in place, so ACTIVE means re-read."""
    _add_transcript(
        claude_projects, "session-basic", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )
    service.sync_all()
    state = db.scalar(
        select(SyncState).where(
            SyncState.claude_session_id == "11111111-1111-1111-1111-111111111111"
        )
    )
    state.session_status = "active"
    db.commit()

    summary = service.sync_all()
    assert summary.count(SyncDecision.STILL_ACTIVE) == 1
    assert summary.processed == 1


def test_empty_transcripts_are_skipped_without_creating_a_session(
    service, db, claude_projects
):
    (claude_projects / "projects" / "-tmp-proj" / "empty.jsonl").write_bytes(b"")
    summary = service.sync_all()
    assert summary.count(SyncDecision.EMPTY) == 1
    assert db.scalar(select(func.count(SessionRow.id))) == 2


def test_full_flag_reprocesses_everything(service, db):
    service.sync_all()
    service.force = True
    summary = service.sync_all()
    assert summary.count(SyncDecision.FORCED) == 2
    assert summary.processed == 2
    assert db.scalar(select(func.count(SessionRow.id))) == 2


def test_one_broken_transcript_does_not_abort_the_run(
    db, claude_projects, monkeypatch
):
    from app.collectors.claude import parser as parser_module

    real_parse = parser_module.parse_transcript

    def exploding_parse(path, **kwargs):
        if "33333333" in str(path):
            raise RuntimeError("boom")
        return real_parse(path, **kwargs)

    monkeypatch.setattr("app.services.aggregator.parse_transcript", exploding_parse)
    service = SyncService(
        db,
        SessionAggregator(NullCcusage(), enable_git=False),
        claude_data_path=claude_projects,
    )
    summary = service.sync_all()
    assert summary.count(SyncDecision.FAILED) == 1
    assert summary.count(SyncDecision.NEW) == 1
    assert summary.failures[0].error.startswith("RuntimeError")
    # The healthy session still landed.
    assert db.scalar(select(func.count(SessionRow.id))) == 1


def test_orphan_projects_are_pruned(service, db):
    """Re-resolving project boundaries can strand a project with no sessions."""
    service.sync_all()
    synced = {p.path for p in db.scalars(select(Project)).all()}
    assert synced == {"/tmp/proj", "/tmp/proj3"}

    db.add(Project(path="/tmp/gone", name="gone"))
    db.commit()
    assert db.scalar(select(func.count(Project.id))) == 3

    service.sync_all()
    assert {p.path for p in db.scalars(select(Project)).all()} == synced


def test_decide_avoids_hashing_when_size_and_mtime_are_unchanged(
    service, db, claude_projects, monkeypatch
):
    """Fingerprint checks must stay cheap on multi-megabyte transcripts."""
    service.sync_all()

    calls = []
    real_sha = __import__(
        "app.collectors.claude.parser", fromlist=["file_sha256"]
    ).file_sha256

    def counting_sha(path, *args, **kwargs):
        calls.append(path)
        return real_sha(path, *args, **kwargs)

    monkeypatch.setattr("app.services.sync.file_sha256", counting_sha)
    from app.collectors.claude.discovery import discover_sessions

    for session_file in discover_sessions(claude_projects):
        decision, _ = service.decide(session_file)
        assert decision is SyncDecision.SKIPPED_UNCHANGED
    assert calls == []
