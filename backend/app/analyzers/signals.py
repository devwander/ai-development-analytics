"""Observational signals computed from stored events.

These are **observed**, not inferred: they come from counting and comparing
rows, and they would be identical on a second run. The brief is explicit that
the LLM must not be used for metrics obtainable directly from the data (§31),
so the analyzer computes candidate rework evidence here and asks the model only
to *interpret* it.

A signal is never a conclusion. "The same file was edited in 6 separate bursts"
is an observation; whether that constitutes rework is a judgement, and the
transcript may well show it was six different features in the same file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models.entities import ChangedFile, GitCommit, Message, SessionCommitLink
from app.models.entities import Session as SessionRow
from app.models.entities import ToolCall

#: Phrases a developer uses when rejecting or correcting previous work.
#: Portuguese and English, because real transcripts here mix both.
_CORRECTION_PATTERNS = (
    r"\bn[ãa]o (?:era|foi) (?:isso|bem assim)\b",
    r"\b(?:isso|est[áa]) errado\b",
    r"\bcorrij[ae]\b",
    r"\bconserta\b",
    r"\brefaz|refa[çc]a\b",
    r"\bdesfaz|desfa[çc]a\b",
    r"\breverta?\b",
    r"\bvolta (?:atr[áa]s|ao)\b",
    r"\bquebrou\b",
    r"\bcontinua (?:com )?(?:erro|falhando|quebrado)\b",
    r"\bthat'?s (?:wrong|not right)\b",
    r"\b(?:fix|revert|undo) (?:that|this|it)\b",
    r"\bstill (?:failing|broken|wrong)\b",
    r"\bdidn'?t work\b",
    r"\bbroke\b",
)
_CORRECTION_RE = re.compile("|".join(_CORRECTION_PATTERNS), re.IGNORECASE)

#: Commands whose failure means the code did not work, as opposed to a typo in
#: a shell one-liner.
_TEST_COMMAND_RE = re.compile(
    r"\b(pytest|jest|vitest|npm (?:run )?test|yarn test|pnpm test|go test|"
    r"cargo test|mvn test|gradle test|rspec|phpunit|tox|nox)\b",
    re.IGNORECASE,
)

#: Gap that separates two editing bursts on the same file.
_BURST_GAP = timedelta(minutes=20)


@dataclass
class Signal:
    kind: str
    detail: str
    #: Rows the signal was derived from, so a reader can go back to the source.
    references: list[str] = field(default_factory=list)

    def render(self) -> str:
        return f"[{self.kind}] {self.detail}"


@dataclass
class SignalSet:
    signals: list[Signal] = field(default_factory=list)

    def add(self, kind: str, detail: str, references: list[str] | None = None) -> None:
        self.signals.append(Signal(kind, detail, references or []))

    def __bool__(self) -> bool:
        return bool(self.signals)

    def render(self) -> list[str]:
        return [signal.render() for signal in self.signals]


def collect_signals(db: DbSession, session: SessionRow) -> SignalSet:
    signals = SignalSet()
    _correction_prompts(db, session, signals)
    _repeated_edit_bursts(db, session, signals)
    _failing_test_runs(db, session, signals)
    _repeated_tool_errors(db, session, signals)
    _reverted_commits(db, session, signals)
    return signals


def _correction_prompts(db: DbSession, session: SessionRow, out: SignalSet) -> None:
    """Only real human prompts count - not tool results or injected notes."""
    rows = db.scalars(
        select(Message)
        .where(
            Message.session_id == session.id,
            Message.message_type == "human_prompt",
            Message.content.is_not(None),
        )
        .order_by(Message.sequence)
    ).all()
    for message in rows:
        match = _CORRECTION_RE.search(message.content or "")
        if match is None:
            continue
        out.add(
            "correction_request",
            f'prompt #{message.sequence} contains "{match.group(0)}": '
            f"{_excerpt(message.content, match.start())}",
            [f"message:{message.sequence}"],
        )


def _repeated_edit_bursts(db: DbSession, session: SessionRow, out: SignalSet) -> None:
    """A file edited in several separate bursts, not just many times at once.

    Counting edits alone would flag any large refactor. Separating them by a
    20-minute gap is what distinguishes "wrote it once" from "came back to it".
    """
    rows = db.scalars(
        select(ToolCall)
        .where(
            ToolCall.session_id == session.id,
            ToolCall.tool_name.in_(("Edit", "Write", "NotebookEdit")),
        )
        .order_by(ToolCall.started_at)
    ).all()
    per_file: dict[str, list] = {}
    for call in rows:
        path = (call.input or {}).get("file_path") or (call.input or {}).get(
            "notebook_path"
        )
        if isinstance(path, str) and path and call.started_at:
            per_file.setdefault(path, []).append(call.started_at)

    for path, timestamps in per_file.items():
        timestamps.sort()
        bursts = 1
        for previous, current in zip(timestamps, timestamps[1:]):
            if current - previous > _BURST_GAP:
                bursts += 1
        if bursts >= 3:
            out.add(
                "repeated_edit_bursts",
                f"{path} was edited in {bursts} separate bursts "
                f"({len(timestamps)} edits total)",
                [f"file:{path}"],
            )


def _failing_test_runs(db: DbSession, session: SessionRow, out: SignalSet) -> None:
    rows = db.scalars(
        select(ToolCall)
        .where(ToolCall.session_id == session.id, ToolCall.tool_name == "Bash")
        .order_by(ToolCall.sequence)
    ).all()
    failures = [
        call
        for call in rows
        if call.is_error
        and _TEST_COMMAND_RE.search(str((call.input or {}).get("command", "")))
    ]
    if failures:
        out.add(
            "failing_test_runs",
            f"{len(failures)} test command(s) exited with an error during the session",
            [f"tool_call:{call.sequence}" for call in failures[:10]],
        )


def _repeated_tool_errors(db: DbSession, session: SessionRow, out: SignalSet) -> None:
    """The same tool failing repeatedly - a sign of fighting the environment."""
    rows = db.scalars(
        select(ToolCall)
        .where(ToolCall.session_id == session.id, ToolCall.is_error.is_(True))
        .order_by(ToolCall.sequence)
    ).all()
    per_tool: dict[str, int] = {}
    for call in rows:
        per_tool[call.tool_name] = per_tool.get(call.tool_name, 0) + 1
    for tool_name, count in sorted(per_tool.items(), key=lambda kv: -kv[1]):
        if count >= 3:
            out.add(
                "repeated_tool_errors",
                f"{tool_name} returned an error {count} times",
                [f"tool:{tool_name}"],
            )


def _reverted_commits(db: DbSession, session: SessionRow, out: SignalSet) -> None:
    rows = db.execute(
        select(GitCommit)
        .join(SessionCommitLink, SessionCommitLink.git_commit_id == GitCommit.id)
        .where(SessionCommitLink.session_id == session.id)
    ).scalars().all()
    for commit in rows:
        first_line = (commit.message or "").splitlines()[0] if commit.message else ""
        if re.match(r"^\s*(revert|Revert)\b", first_line):
            out.add(
                "reverted_commit",
                f"commit {commit.commit_hash[:10]} is a revert: {first_line}",
                [f"commit:{commit.commit_hash}"],
            )


def file_churn_summary(db: DbSession, session: SessionRow) -> list[str]:
    """Project files the session changed, most churn first."""
    rows = db.scalars(
        select(ChangedFile)
        .where(
            ChangedFile.session_id == session.id,
            ChangedFile.change_type.in_(("modified", "created", "deleted", "renamed")),
            ChangedFile.is_within_project.is_not(False),
        )
        .order_by(ChangedFile.file_path)
    ).all()
    return [
        f"{row.change_type:>8}  {row.file_path}"
        + (
            f"  (+{row.insertions or 0}/-{row.deletions or 0})"
            if row.insertions or row.deletions
            else ""
        )
        for row in rows
    ]


def _excerpt(text: str, position: int, radius: int = 90) -> str:
    start = max(0, position - radius)
    end = min(len(text), position + radius)
    snippet = text[start:end].replace("\n", " ").strip()
    return ("..." if start else "") + snippet + ("..." if end < len(text) else "")
