from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import entities  # noqa: F401 (registers tables)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "claude"

#: Fixture transcripts are named descriptively, but real Claude Code names each
#: file after the session UUID. Tests that care about that mapping copy the
#: fixture to a UUID-named file first.
FIXTURE_SESSION_IDS = {
    "session-basic": "11111111-1111-1111-1111-111111111111",
    "session-corrupted": "22222222-2222-2222-2222-222222222222",
    "session-unknown-events": "33333333-3333-3333-3333-333333333333",
    "session-incomplete": "44444444-4444-4444-4444-444444444444",
    "session-injected-user": "55555555-5555-5555-5555-555555555555",
    "session-split-blocks": "88888888-8888-8888-8888-888888888888",
    "session-subdirs-and-scratch": "99999999-9999-9999-9999-999999999999",
}

#: Well after every fixture timestamp, so sessions are never judged "active".
FIXED_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def transcript(tmp_path: Path):
    """Return a callable that materialises a fixture under its real session id."""

    def _make(name: str) -> Path:
        source = FIXTURES / f"{name}.jsonl"
        session_id = FIXTURE_SESSION_IDS.get(name, name)
        target = tmp_path / f"{session_id}.jsonl"
        shutil.copyfile(source, target)
        return target

    return _make


@pytest.fixture
def claude_home(tmp_path: Path) -> Path:
    """A minimal ``~/.claude`` layout for discovery tests."""
    projects = tmp_path / "claude" / "projects"
    for key, cwd in (
        ("-tmp-proj", "/tmp/proj"),
        ("-tmp-with-dash-name", "/tmp/with-dash-name"),
    ):
        directory = projects / key
        directory.mkdir(parents=True)
        session_id = f"{abs(hash(key)) % 10**8:08d}-0000-0000-0000-000000000000"
        (directory / f"{session_id}.jsonl").write_text(
            '{"type":"user","uuid":"u1","sessionId":"%s","timestamp":'
            '"2026-08-01T10:00:00.000Z","cwd":"%s","origin":{"kind":"human"},'
            '"message":{"role":"user","content":"oi"}}\n' % (session_id, cwd),
            encoding="utf-8",
        )
    (projects / "-tmp-empty").mkdir()
    return tmp_path / "claude"


@pytest.fixture
def db():
    """A disposable database.

    SQLite keeps the suite fast and dependency-free; the entities deliberately
    use portable column types so the same DDL runs on PostgreSQL in production
    (where JSON becomes JSONB). PostgreSQL-specific behaviour - NUL rejection,
    the real migration - is covered by tests/test_postgres_integration.py.
    """
    # StaticPool + check_same_thread=False: FastAPI's TestClient runs the app
    # in a worker thread, and a default in-memory SQLite connection is bound to
    # the thread that created it.
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def claude_projects(tmp_path: Path) -> Path:
    """A ~/.claude layout populated from the fixture transcripts."""
    root = tmp_path / "claude"
    directory = root / "projects" / "-tmp-proj"
    directory.mkdir(parents=True)
    for name, session_id in (
        ("session-basic", "11111111-1111-1111-1111-111111111111"),
        ("session-unknown-events", "33333333-3333-3333-3333-333333333333"),
    ):
        shutil.copyfile(FIXTURES / f"{name}.jsonl", directory / f"{session_id}.jsonl")
    return root
