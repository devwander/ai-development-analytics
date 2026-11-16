"""Collect token/cost metrics from the locally installed ``ccusage`` binary.

Verified against **ccusage 20.0.20** on this machine. Two facts about that
version drive the implementation and must not be assumed for other versions:

1. ``ccusage session --json`` returns ``{"session": [...], "totals": {...}}``
   and each row identifies the session in a field named **``period``** - not
   ``sessionId``. The rows carry only ``metadata.lastActivity``.
2. ``ccusage session --json --id <uuid>`` returns a *different* shape:
   ``{"entries": [...], "sessionId", "totalCost", "totalTokens"}`` with one
   entry per API request, each timestamped. An unknown id yields JSON ``null``
   with exit code 0 - not an error.

Cost figures are taken from ccusage verbatim. This module never recomputes
pricing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.collectors.claude.parser import parse_timestamp
from app.schemas.observed import ModelUsage, Usage

DEFAULT_TIMEOUT_SECONDS = 120


class CcusageError(RuntimeError):
    pass


class CcusageUnavailable(CcusageError):
    """The binary is missing or not runnable. Collection must continue."""


@dataclass
class CcusageRunner:
    binary: str = "ccusage"
    timeout: int = DEFAULT_TIMEOUT_SECONDS

    def is_available(self) -> bool:
        return shutil.which(self.binary) is not None

    def version(self) -> str:
        return self._run(["--version"]).strip()

    def _run(self, args: list[str]) -> str:
        if not self.is_available():
            raise CcusageUnavailable(f"{self.binary!r} not found on PATH")
        try:
            completed = subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except OSError as exc:  # pragma: no cover - defensive
            raise CcusageUnavailable(str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise CcusageError(f"ccusage timed out after {self.timeout}s") from exc
        if completed.returncode != 0:
            raise CcusageError(
                f"ccusage exited {completed.returncode}: "
                f"{(completed.stderr or completed.stdout).strip()[:500]}"
            )
        return completed.stdout

    def _run_json(self, args: list[str]) -> Any:
        raw = self._run(args)
        try:
            return json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise CcusageError(f"ccusage did not return valid JSON: {raw[:300]!r}") from exc

    def session_index(self) -> dict[str, dict[str, Any]]:
        """All sessions ccusage knows about, keyed by session id."""
        payload = self._run_json(["session", "--json"])
        if not isinstance(payload, dict):
            raise CcusageError("unexpected ccusage session payload")
        rows = payload.get("session")
        if not isinstance(rows, list):
            raise CcusageError("ccusage session payload has no 'session' list")
        index: dict[str, dict[str, Any]] = {}
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("period"), str):
                index[row["period"]] = row
        return index

    def session_detail(self, session_id: str) -> dict[str, Any] | None:
        """Per-request entries for one session, or ``None`` if unknown to ccusage."""
        payload = self._run_json(["session", "--json", "--id", session_id])
        return payload if isinstance(payload, dict) else None


class CcusageCollector:
    def __init__(self, runner: CcusageRunner | None = None) -> None:
        self.runner = runner or CcusageRunner()
        self._index: dict[str, dict[str, Any]] | None = None

    def index(self) -> dict[str, dict[str, Any]]:
        if self._index is None:
            self._index = self.runner.session_index()
        return self._index

    def collect(self, session_id: str) -> Usage | None:
        """Usage for one session, or ``None`` when ccusage has no data for it.

        ``None`` is a meaningful answer, not a failure: ccusage de-duplicates by
        message id across the whole dataset, so a resumed/forked session whose
        messages were already counted under its parent legitimately has no row.
        Callers should fall back to transcript-derived usage and say so.
        """
        row = self.index().get(session_id)
        if row is None:
            return None

        now = datetime.now(timezone.utc)
        usage = Usage(
            source="ccusage",
            input_tokens=_int(row.get("inputTokens")),
            output_tokens=_int(row.get("outputTokens")),
            cache_creation_tokens=_int(row.get("cacheCreationTokens")),
            cache_read_tokens=_int(row.get("cacheReadTokens")),
            total_tokens=_int(row.get("totalTokens")),
            cost_usd=_float(row.get("totalCost")),
            last_activity=parse_timestamp((row.get("metadata") or {}).get("lastActivity")),
            collected_at=now,
            models=[
                ModelUsage(
                    model=str(breakdown.get("modelName") or "unknown"),
                    input_tokens=_int(breakdown.get("inputTokens")),
                    output_tokens=_int(breakdown.get("outputTokens")),
                    cache_creation_tokens=_int(breakdown.get("cacheCreationTokens")),
                    cache_read_tokens=_int(breakdown.get("cacheReadTokens")),
                    total_tokens=sum(
                        _int(breakdown.get(key))
                        for key in (
                            "inputTokens",
                            "outputTokens",
                            "cacheCreationTokens",
                            "cacheReadTokens",
                        )
                    ),
                    cost_usd=_float(breakdown.get("cost")),
                )
                for breakdown in row.get("modelBreakdowns") or []
                if isinstance(breakdown, dict)
            ],
        )

        # The index has no first-activity field; the per-session detail does.
        try:
            detail = self.runner.session_detail(session_id)
        except CcusageError:
            detail = None
        if detail:
            timestamps = sorted(
                ts
                for entry in detail.get("entries") or []
                if isinstance(entry, dict)
                and (ts := parse_timestamp(entry.get("timestamp"))) is not None
            )
            if timestamps:
                usage.first_activity = timestamps[0]
                usage.last_activity = usage.last_activity or timestamps[-1]
            usage.notes.append(
                f"{len(detail.get('entries') or [])} API requests reported by ccusage"
            )
        return usage


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None
