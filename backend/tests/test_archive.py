"""Tests for the transcript archive.

It exists because Claude Code prunes its own transcripts: 8 of 53 sessions in
the real database pointed at files that no longer existed, and their numbers
were still on screen with a broken provenance chain behind them.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.collectors.claude.parser import file_sha256
from app.models.entities import SessionSourceFile
from app.models.entities import Session as SessionRow
from app.services import analytics
from app.services.archive import TranscriptArchive
from app.services.sync import SyncService

from .conftest import FIXTURES


def _archive(tmp_path: Path, enabled: bool = True) -> TranscriptArchive:
    return TranscriptArchive(tmp_path / "archive", enabled=enabled)


def test_store_copies_and_verifies_the_hash(tmp_path: Path):
    source = FIXTURES / "session-basic.jsonl"
    digest = file_sha256(source)
    archive = _archive(tmp_path)

    result = archive.store(source, project_key="-tmp-proj", session_id="abc", sha256=digest)
    assert result.copied is True
    assert result.path is not None and result.path.exists()
    assert file_sha256(result.path) == digest
    assert result.bytes_written == source.stat().st_size


def test_an_identical_copy_is_not_rewritten(tmp_path: Path):
    """Re-archiving unchanged files every sync would rewrite ~117 MB for nothing."""
    source = FIXTURES / "session-basic.jsonl"
    digest = file_sha256(source)
    archive = _archive(tmp_path)

    archive.store(source, project_key="-tmp-proj", session_id="abc", sha256=digest)
    again = archive.store(source, project_key="-tmp-proj", session_id="abc", sha256=digest)
    assert again.copied is False
    assert again.path is not None


def test_a_changed_transcript_replaces_its_copy(tmp_path: Path):
    source = tmp_path / "growing.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    archive = _archive(tmp_path)
    archive.store(
        source, project_key="-p", session_id="s", sha256=file_sha256(source)
    )

    source.write_text('{}\n{"more": true}\n', encoding="utf-8")
    digest = file_sha256(source)
    result = archive.store(source, project_key="-p", session_id="s", sha256=digest)
    assert result.copied is True
    assert file_sha256(result.path) == digest


def test_a_hash_mismatch_is_reported_rather_than_stored(tmp_path: Path):
    """An archive that silently differs from the source is worse than none."""
    source = FIXTURES / "session-basic.jsonl"
    result = _archive(tmp_path).store(
        source, project_key="-p", session_id="s", sha256="0" * 64
    )
    assert result.path is None
    assert result.error is not None and "expected" in result.error


def test_archiving_can_be_disabled(tmp_path: Path):
    result = _archive(tmp_path, enabled=False).store(
        FIXTURES / "session-basic.jsonl",
        project_key="-p",
        session_id="s",
        sha256="x",
    )
    assert result.path is None and result.copied is False
    assert not (tmp_path / "archive").exists()


def test_resolve_prefers_the_original_then_falls_back(tmp_path: Path):
    original = tmp_path / "original.jsonl"
    archived = tmp_path / "archived.jsonl"
    archived.write_text("{}\n", encoding="utf-8")
    archive = _archive(tmp_path)

    assert archive.resolve(str(original), str(archived))[1] == "archive"
    original.write_text("{}\n", encoding="utf-8")
    assert archive.resolve(str(original), str(archived))[1] == "original"
    assert archive.resolve(None, None) == (None, "missing")


# -- integration with sync ------------------------------------------------
def test_sync_archives_each_transcript(db, claude_projects, tmp_path):
    from app.services.aggregator import SessionAggregator

    from .test_discovery_and_aggregator import NullCcusage

    service = SyncService(
        db,
        SessionAggregator(NullCcusage(), enable_git=False),
        claude_data_path=claude_projects,
        archive=_archive(tmp_path),
    )
    summary = service.sync_all()
    assert summary.archived_files == 2
    assert summary.archived_bytes > 0
    assert not summary.archive_errors

    sources = db.scalars(select(SessionSourceFile)).all()
    assert all(source.archive_path for source in sources)
    assert all(Path(source.archive_path).exists() for source in sources)
    assert all(source.archived_at is not None for source in sources)


def test_resyncing_keeps_the_archive_reference(db, claude_projects, tmp_path):
    """A re-save replaces the provenance row; the archive must survive it."""
    from app.services.aggregator import SessionAggregator

    from .test_discovery_and_aggregator import NullCcusage

    service = SyncService(
        db,
        SessionAggregator(NullCcusage(), enable_git=False),
        claude_data_path=claude_projects,
        archive=_archive(tmp_path),
    )
    service.sync_all()
    service.force = True
    service.sync_all()

    sources = db.scalars(select(SessionSourceFile)).all()
    assert len(sources) == 2
    assert all(source.archive_path for source in sources)


def test_archive_failure_does_not_abort_the_sync(db, claude_projects, tmp_path):
    """Collected data stays valid without an archive - it just is not auditable."""
    from app.services.aggregator import SessionAggregator

    from .test_discovery_and_aggregator import NullCcusage

    class BrokenArchive(TranscriptArchive):
        def store(self, *args, **kwargs):
            from app.services.archive import ArchiveResult

            return ArchiveResult(path=None, copied=False, error="disco cheio")

    service = SyncService(
        db,
        SessionAggregator(NullCcusage(), enable_git=False),
        claude_data_path=claude_projects,
        archive=BrokenArchive(tmp_path / "archive"),
    )
    summary = service.sync_all()
    assert summary.processed == 2
    assert not summary.failures
    assert len(summary.archive_errors) == 2
    assert db.scalar(select(SessionRow)) is not None


# -- surfaced as a validity threat ----------------------------------------
def test_a_session_with_no_readable_source_is_reported_unauditable(
    db, claude_projects, tmp_path
):
    from app.services.aggregator import SessionAggregator

    from .test_discovery_and_aggregator import NullCcusage

    service = SyncService(
        db,
        SessionAggregator(NullCcusage(), enable_git=False),
        claude_data_path=claude_projects,
        archive=_archive(tmp_path, enabled=False),
    )
    service.sync_all()
    assert analytics.validity_threats(db)["transcripts_unauditable"] == 0

    # Claude Code prunes the originals and nothing was archived.
    for path in (claude_projects / "projects" / "-tmp-proj").glob("*.jsonl"):
        path.unlink()
    threats = analytics.validity_threats(db)
    assert threats["transcripts_original_missing"] == 2
    assert threats["transcripts_unauditable"] == 2
    assert threats["transcripts_archived"] == 0


def test_an_archived_session_stays_auditable_after_the_original_is_pruned(
    db, claude_projects, tmp_path
):
    from app.services.aggregator import SessionAggregator

    from .test_discovery_and_aggregator import NullCcusage

    service = SyncService(
        db,
        SessionAggregator(NullCcusage(), enable_git=False),
        claude_data_path=claude_projects,
        archive=_archive(tmp_path),
    )
    service.sync_all()
    for path in (claude_projects / "projects" / "-tmp-proj").glob("*.jsonl"):
        path.unlink()

    threats = analytics.validity_threats(db)
    assert threats["transcripts_original_missing"] == 2
    assert threats["transcripts_archived"] == 2
    # The bytes still exist, so the chain back to source is intact.
    assert threats["transcripts_unauditable"] == 0


def test_coverage_reports_sessions_with_no_project(db, transcript):
    from app.repositories.session_repository import SessionRepository
    from app.services.aggregator import SessionAggregator

    from .conftest import FIXED_NOW
    from .test_discovery_and_aggregator import NullCcusage

    report = SessionAggregator(NullCcusage(), enable_git=False).build(
        transcript("session-basic"), now=FIXED_NOW
    )
    SessionRepository(db).save(report)
    db.commit()
    assert analytics.coverage(db)["without_project"] == 0

    # An empty transcript has no cwd, so it belongs to no project - and would
    # otherwise make the global total exceed the sum of the project rows.
    row = db.scalar(select(SessionRow))
    row.project_id = None
    db.commit()
    assert analytics.coverage(db)["without_project"] == 1
