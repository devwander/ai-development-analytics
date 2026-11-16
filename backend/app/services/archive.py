"""Keep a copy of every transcript the collector reads.

Claude Code prunes its own transcript files. When this was written, **8 of 53**
sessions in the database pointed at files that no longer existed - including one
of 6.2 MB with 41 prompts. Their numbers were still on screen, but the chain
``metric -> session -> event -> raw bytes`` was broken and could not be audited.

The brief requires preserving raw data whenever possible, so the sync copies each
transcript into a local archive on first read. The copy is verified by SHA-256
against what the parser hashed: an archive that silently differs from the source
would be worse than none.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.collectors.claude.parser import file_sha256


@dataclass
class ArchiveResult:
    path: Path | None
    copied: bool
    bytes_written: int = 0
    error: str | None = None


class TranscriptArchive:
    def __init__(self, root: str | Path, *, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled

    def path_for(self, project_key: str, session_id: str) -> Path:
        # The project key is already a filesystem-safe token from Claude Code.
        return self.root / project_key / f"{session_id}.jsonl"

    def store(
        self, source: Path, *, project_key: str, session_id: str, sha256: str
    ) -> ArchiveResult:
        """Copy the transcript unless an identical copy is already there.

        Re-archiving an unchanged file every sync would rewrite ~150 MB for
        nothing, so the existing copy's digest decides.
        """
        if not self.enabled:
            return ArchiveResult(path=None, copied=False)

        target = self.path_for(project_key, session_id)
        try:
            if target.exists() and file_sha256(target) == sha256:
                return ArchiveResult(path=target, copied=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError as exc:
            # Archiving must never abort a sync: the collected data is still
            # valid, it just cannot be re-audited from bytes later.
            return ArchiveResult(path=None, copied=False, error=str(exc))

        stored = file_sha256(target)
        if stored != sha256:
            return ArchiveResult(
                path=None,
                copied=False,
                error=f"archived copy hashes {stored[:12]}, expected {sha256[:12]}",
            )
        return ArchiveResult(
            path=target, copied=True, bytes_written=target.stat().st_size
        )

    def resolve(self, original: str | None, archived: str | None) -> tuple[Path | None, str]:
        """Best available source for a transcript, and where it came from."""
        if original and Path(original).exists():
            return Path(original), "original"
        if archived and Path(archived).exists():
            return Path(archived), "archive"
        return None, "missing"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
