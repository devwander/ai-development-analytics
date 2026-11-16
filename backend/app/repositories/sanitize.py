"""Make observed text storable in PostgreSQL without losing information.

PostgreSQL rejects NUL (0x00) bytes in ``text`` and in JSONB strings. Real
transcripts contain them: a corrupted region of one file on this machine is
NUL-padded, and that padding reaches tool output. The collector must not drop
the whole session because of it.

NUL bytes are removed at the persistence boundary only - the parser keeps the
transcript's bytes as they are, and the original file is never modified. Every
removal is counted so the session carries a warning saying its stored text was
altered.
"""

from __future__ import annotations

from typing import Any


class NullByteScrubber:
    """Removes NUL bytes recursively, counting how many values it touched."""

    def __init__(self) -> None:
        self.affected_values = 0
        self.removed_bytes = 0

    def __call__(self, value: Any) -> Any:
        if isinstance(value, str):
            count = value.count("\x00")
            if not count:
                return value
            self.affected_values += 1
            self.removed_bytes += count
            return value.replace("\x00", "")
        if isinstance(value, list):
            return [self(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self(item) for item in value)
        if isinstance(value, dict):
            return {self(key): self(item) for key, item in value.items()}
        return value

    @property
    def dirty(self) -> bool:
        return self.affected_values > 0

    def warning(self) -> str:
        return (
            f"{self.removed_bytes} NUL byte(s) across {self.affected_values} value(s) "
            "were removed before storage (PostgreSQL forbids them in text); "
            "the original transcript is unchanged"
        )
