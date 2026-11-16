"""Persistence tests: the repository must be idempotent and lossless."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.entities import (
    ChangedFile,
    GitCommit,
    Message,
    Project,
    SessionCommitLink,
    SessionSourceFile,
    SyncState,
    ToolCall,
    Usage,
)
from app.models.entities import Session as SessionRow
from app.repositories.sanitize import NullByteScrubber
from app.repositories.session_repository import SessionRepository
from app.services.aggregator import SessionAggregator

from .conftest import FIXED_NOW
from .test_discovery_and_aggregator import NullCcusage


def _report(transcript, name="session-basic"):
    return SessionAggregator(NullCcusage(), enable_git=False).build(
        transcript(name), now=FIXED_NOW
    )


def test_save_persists_every_observed_entity(db, transcript):
    report = _report(transcript)
    SessionRepository(db).save(report)
    db.commit()

    row = db.scalar(select(SessionRow))
    assert row.claude_session_id == "11111111-1111-1111-1111-111111111111"
    assert row.title == "Validação do formulário de produto"
    assert row.status == "completed"
    assert row.human_prompt_count == 1
    assert row.confirmed_human_prompt_count == 1
    assert row.assistant_message_count == 4
    assert row.tool_error_count == 1

    assert db.scalar(select(func.count(Message.id))) == len(report.messages)
    assert db.scalar(select(func.count(ToolCall.id))) == len(report.tool_calls)
    assert db.scalar(select(func.count(ChangedFile.id))) == len(report.files)
    assert db.scalar(select(func.count(SessionSourceFile.id))) == 1


def test_provenance_is_queryable(db, transcript):
    report = _report(transcript)
    SessionRepository(db).save(report)
    db.commit()
    source = db.scalar(select(SessionSourceFile))
    assert len(source.transcript_sha256) == 64
    assert source.collector_version.startswith("claude-transcript/")
    assert source.unparseable_lines == 0


def test_usage_rows_separate_totals_from_per_model_breakdown(db, transcript):
    SessionRepository(db).save(_report(transcript))
    db.commit()
    totals = db.scalars(select(Usage).where(Usage.model.is_(None))).all()
    per_model = db.scalars(select(Usage).where(Usage.model.is_not(None))).all()
    assert len(totals) == 1
    assert totals[0].source == "claude_transcript"
    assert {u.model for u in per_model} == {
        "claude-opus-5",
        "claude-haiku-4-5-20251001",
    }
    # The total must equal the sum of its parts, not double-count them.
    assert totals[0].total_tokens == sum(u.total_tokens for u in per_model)


def test_cost_stays_null_when_undetermined_never_zero(db, transcript):
    SessionRepository(db).save(
        _report(transcript, "session-subdirs-and-scratch")
    )
    db.commit()
    total = db.scalar(select(Usage).where(Usage.model.is_(None)))
    assert total.cost_usd is None


def test_resaving_replaces_children_instead_of_duplicating(db, transcript):
    repository = SessionRepository(db)
    report = _report(transcript)
    repository.save(report)
    db.commit()
    first = db.scalar(select(func.count(Message.id)))

    repository.save(report)
    db.commit()

    assert db.scalar(select(func.count(SessionRow.id))) == 1
    assert db.scalar(select(func.count(Message.id))) == first
    assert db.scalar(select(func.count(Usage.id))) == 3
    assert db.scalar(select(func.count(SessionSourceFile.id))) == 1


def test_project_is_created_once_and_reused(db, transcript):
    repository = SessionRepository(db)
    repository.save(_report(transcript))
    repository.save(_report(transcript, "session-subdirs-and-scratch"))
    db.commit()
    paths = {p.path for p in db.scalars(select(Project)).all()}
    assert paths == {"/tmp/proj", "/home/dev/proj"}


def test_sync_state_tracks_the_fingerprint_and_counts_runs(db, transcript):
    repository = SessionRepository(db)
    report = _report(transcript)
    repository.record_sync(report)
    db.commit()
    state = db.scalar(select(SyncState))
    assert state.sha256 == report.source.transcript_sha256
    assert state.size_bytes == report.source.transcript_bytes
    assert state.sync_count == 1
    assert state.session_status == "completed"

    repository.record_sync(report)
    db.commit()
    assert db.scalar(select(func.count(SyncState.id))) == 1
    assert db.scalar(select(SyncState)).sync_count == 2


def test_git_commits_and_evidence_bearing_links_are_stored(db, transcript, tmp_path):
    from datetime import timedelta

    from app.schemas.observed import GitCommit as GitCommitSchema
    from app.schemas.observed import GitCorrelation, GitInfo

    report = _report(transcript)
    when = report.session.ended_at + timedelta(minutes=5)
    report.git = GitInfo(
        is_repository=True,
        repository_root="/tmp/proj",
        repository_url="git@example.com:acme/app.git",
        current_branch="main",
        commits_in_window=[
            GitCommitSchema(
                commit_hash="a" * 40,
                message="feat: product form",
                committed_at=when,
                files_changed=1,
                insertions=10,
                deletions=2,
                changed_files=["src/ProductForm.tsx"],
            )
        ],
        correlations=[
            GitCorrelation(
                commit_hash="a" * 40,
                likely_related=True,
                overlapping_files=["src/ProductForm.tsx"],
                overlap_count=1,
                minutes_after_session_end=5.0,
                reasons=["1 file(s) also modified by Claude during the session"],
            )
        ],
    )
    SessionRepository(db).save(report)
    db.commit()

    commit = db.scalar(select(GitCommit))
    assert commit.insertions == 10
    assert commit.changed_files == ["src/ProductForm.tsx"]
    link = db.scalar(select(SessionCommitLink))
    assert link.likely_related is True
    assert link.reasons  # the evidence travels with the association
    project = db.scalar(select(Project))
    assert project.repository_url == "git@example.com:acme/app.git"
    # There is no authorship claim anywhere in the schema.
    assert not hasattr(link, "caused_by_ai")


# -- NUL bytes ------------------------------------------------------------
def test_scrubber_removes_nul_bytes_recursively_and_counts_them():
    scrub = NullByteScrubber()
    cleaned = scrub({"a": "x\x00y", "b": ["p\x00", {"c": "\x00\x00"}], "d": 7})
    assert cleaned == {"a": "xy", "b": ["p", {"c": ""}], "d": 7}
    assert scrub.affected_values == 3
    assert scrub.removed_bytes == 4
    assert scrub.dirty is True


def test_scrubber_leaves_clean_values_untouched():
    scrub = NullByteScrubber()
    payload = {"a": "fine", "b": [1, 2, None]}
    assert scrub(payload) == payload
    assert scrub.dirty is False


def test_nul_bytes_are_stripped_before_storage_and_reported(db, transcript):
    """PostgreSQL rejects NUL in text; a corrupted transcript must still store.

    The raw file keeps its bytes - only the stored copy is cleaned, and the
    session says so.
    """
    report = _report(transcript)
    report.messages[0].content = "antes\x00depois"
    report.tool_calls[0].output_preview = "saida\x00suja"
    SessionRepository(db).save(report)
    db.commit()

    row = db.scalar(select(SessionRow))
    message = db.scalar(select(Message).order_by(Message.sequence))
    assert "\x00" not in message.content
    assert message.content == "antesdepois"
    assert any("NUL byte" in w for w in row.collection_warnings)
