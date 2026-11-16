"""Locate the Claude Code data directory, its projects and its session files.

Important finding from environment inspection: the directory names under
``~/.claude/projects`` are a LOSSY encoding of the filesystem path. Claude Code
replaces both ``/`` and ``-`` (and spaces) with ``-``, so
``-home-user-Dev-my-project-name`` is ambiguous between
``/home/user/Dev/my-project-name`` and
``/home/user/Dev/my/project/name``.

The encoded name is therefore treated as an opaque key only. The authoritative
project path is the ``cwd`` field recorded inside the transcript records.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CLAUDE_DIRS = ("~/.claude", "~/.config/claude")


def resolve_claude_data_path(configured: str | None = None) -> Path:
    """Return the Claude Code data directory.

    ``configured`` may be an explicit path or the string ``"auto"``/``None``.
    """
    if configured and configured != "auto":
        path = Path(configured).expanduser()
        if not path.is_dir():
            raise FileNotFoundError(f"CLAUDE_DATA_PATH does not exist: {path}")
        return path

    env = os.environ.get("CLAUDE_CONFIG_DIR")
    candidates = [env] if env else []
    candidates.extend(DEFAULT_CLAUDE_DIRS)
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if (path / "projects").is_dir():
            return path
    raise FileNotFoundError(
        "Could not locate the Claude Code data directory. "
        "Set CLAUDE_DATA_PATH explicitly."
    )


@dataclass
class SessionFile:
    session_id: str
    path: Path
    project_key: str
    size_bytes: int
    mtime: datetime

    @property
    def is_empty(self) -> bool:
        return self.size_bytes == 0


@dataclass
class DiscoveredProject:
    """A Claude Code project directory plus the real paths it points at."""

    project_key: str
    directory: Path
    session_files: list[SessionFile] = field(default_factory=list)
    # Populated by ``resolve_project_paths``; empty for projects whose
    # transcripts contain no ``cwd`` (e.g. zero-byte session files).
    cwd_counts: dict[str, int] = field(default_factory=dict)

    @property
    def primary_path(self) -> str | None:
        """The most frequently recorded cwd, or ``None`` if undeterminable."""
        if not self.cwd_counts:
            return None
        return max(self.cwd_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]

    @property
    def name(self) -> str:
        path = self.primary_path
        return Path(path).name if path else self.project_key


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def discover_projects(data_path: Path) -> list[DiscoveredProject]:
    projects_dir = data_path / "projects"
    if not projects_dir.is_dir():
        return []

    projects: list[DiscoveredProject] = []
    for directory in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        files = []
        for jsonl in sorted(directory.glob("*.jsonl")):
            stat = jsonl.stat()
            files.append(
                SessionFile(
                    session_id=jsonl.stem,
                    path=jsonl,
                    project_key=directory.name,
                    size_bytes=stat.st_size,
                    mtime=_mtime(jsonl),
                )
            )
        projects.append(
            DiscoveredProject(
                project_key=directory.name, directory=directory, session_files=files
            )
        )
    return projects


def resolve_project_paths(
    project: DiscoveredProject, max_lines_per_file: int = 200
) -> DiscoveredProject:
    """Fill ``cwd_counts`` by sampling the head of each transcript.

    Only the first ``max_lines_per_file`` lines are read: ``cwd`` is present on
    virtually every record, so a head sample is enough and keeps discovery cheap
    on multi-megabyte transcripts.
    """
    counts: dict[str, int] = {}
    for session_file in project.session_files:
        try:
            with session_file.path.open(encoding="utf-8", errors="replace") as handle:
                for index, line in enumerate(handle):
                    if index >= max_lines_per_file:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    cwd = record.get("cwd")
                    if isinstance(cwd, str) and cwd:
                        counts[cwd] = counts.get(cwd, 0) + 1
        except OSError:
            continue
    project.cwd_counts = counts
    return project


def discover_sessions(data_path: Path) -> list[SessionFile]:
    """All session files across all projects, newest first."""
    sessions = [
        session_file
        for project in discover_projects(data_path)
        for session_file in project.session_files
    ]
    return sorted(sessions, key=lambda s: s.mtime, reverse=True)


def find_session_file(data_path: Path, session_id: str) -> SessionFile | None:
    for session_file in discover_sessions(data_path):
        if session_file.session_id == session_id:
            return session_file
    return None
