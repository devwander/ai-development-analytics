"""Build a bounded textual digest of a session for the LLM.

The brief forbids sending large files indiscriminately (§13). A single real
session here holds 2,396 transcript records and 415M tokens of context - it
cannot be sent, and most of it is noise for the question being asked.

The digest is deterministic and budgeted: every section has a hard cap, and
whenever something is dropped the digest says so explicitly, so the model can
lower its own confidence instead of guessing about material it never saw.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session as DbSession

from app.analyzers.signals import SignalSet, collect_signals, file_churn_summary
from app.models.entities import GitCommit, Message, Project, SessionCommitLink
from app.models.entities import Session as SessionRow
from app.models.entities import ToolCall
from app.services.analytics import preferred_usage


@dataclass
class DigestLimits:
    max_prompts: int = 40
    prompt_chars: int = 1200
    max_assistant_messages: int = 8
    assistant_chars: int = 700
    max_files: int = 60
    max_commits: int = 15
    max_signals: int = 20
    max_tools: int = 20


@dataclass
class SessionDigest:
    session_id: str
    text: str
    truncations: list[str] = field(default_factory=list)
    signals: SignalSet = field(default_factory=SignalSet)

    @property
    def chars(self) -> int:
        return len(self.text)


def _fmt_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "unknown"


def _fmt_minutes(seconds: float | None) -> str:
    return f"{seconds / 60:.0f} min" if seconds else "unknown"


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    text = (text or "").strip()
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + " [...truncated]", True


def _sample_middle_out(items: list, keep: int) -> tuple[list, int]:
    """Keep the first and last items; drop the middle.

    A session's intent lives at its start and its outcome at its end. The middle
    is where the grinding happens, and it is the cheapest thing to drop.
    """
    if len(items) <= keep:
        return items, 0
    head = keep // 2
    tail = keep - head
    return items[:head] + items[-tail:], len(items) - keep


def build_digest(
    db: DbSession, session: SessionRow, limits: DigestLimits | None = None
) -> SessionDigest:
    limits = limits or DigestLimits()
    truncations: list[str] = []
    lines: list[str] = []
    project = db.get(Project, session.project_id) if session.project_id else None

    # -- observed facts, stated once so the model never re-derives them ----
    lines.append("## SESSION FACTS (observed - do not restate or contradict)")
    lines.append(f"project: {project.name if project else 'unknown'}")
    lines.append(
        f"window: {_fmt_dt(session.started_at)} -> {_fmt_dt(session.ended_at)}"
    )
    lines.append(
        f"active work time: {_fmt_minutes(session.active_turn_seconds)} "
        f"(wall-clock span {_fmt_minutes(session.duration_seconds)} - sessions "
        f"straddle days, so wall-clock is not working time)"
    )
    lines.append(f"status: {session.status}")
    lines.append(f"models: {', '.join(session.models_used or []) or 'unknown'}")
    lines.append(
        f"human prompts: {session.human_prompt_count} "
        f"| assistant messages: {session.assistant_message_count} "
        f"| tool calls: {session.tool_call_count} "
        f"| tool errors: {session.tool_error_count}"
    )
    lines.append(
        f"files: {session.files_touched} touched, {session.files_modified} modified, "
        f"{session.files_outside_project} outside the project tree (scratch)"
    )
    if session.git_branches:
        lines.append(
            f"git branches during the session: {', '.join(session.git_branches)}"
        )
    # Same source preference the API uses, so the digest never quotes a
    # different number than the dashboard for the same session.
    preferred = preferred_usage()
    usage = db.execute(
        select(preferred).where(preferred.c.session_id == session.id)
    ).one_or_none()
    if usage:
        lines.append(
            f"tokens: {usage.total_tokens} (source: {usage.source})"
            + (f", cost US$ {usage.cost_usd:.2f}" if usage.cost_usd is not None else "")
        )
    if session.title:
        lines.append(f'title Claude Code gave the session: "{session.title}"')

    # -- what the developer actually asked for ----------------------------
    prompts = db.scalars(
        select(Message)
        .where(
            Message.session_id == session.id,
            Message.message_type == "human_prompt",
            Message.content.is_not(None),
        )
        .order_by(Message.sequence)
    ).all()
    kept_prompts, dropped = _sample_middle_out(prompts, limits.max_prompts)
    if dropped:
        truncations.append(
            f"{dropped} of {len(prompts)} human prompts omitted (middle)"
        )
    lines.append("")
    lines.append("## HUMAN PROMPTS (what the developer typed)")
    if not kept_prompts:
        lines.append("(none recorded)")
    for index, message in enumerate(kept_prompts):
        if dropped and index == limits.max_prompts // 2:
            lines.append(f"--- {dropped} prompt(s) omitted here ---")
        body, cut = _truncate(message.content, limits.prompt_chars)
        if cut:
            truncations.append(f"prompt #{message.sequence} truncated")
        lines.append(f"[#{message.sequence}] {body}")

    # -- how the assistant framed its work --------------------------------
    replies = db.scalars(
        select(Message)
        .where(
            Message.session_id == session.id,
            Message.message_type == "assistant_text",
            Message.content.is_not(None),
        )
        .order_by(Message.sequence)
    ).all()
    kept_replies, dropped_replies = _sample_middle_out(
        replies, limits.max_assistant_messages
    )
    if dropped_replies:
        truncations.append(
            f"{dropped_replies} of {len(replies)} assistant messages omitted (middle)"
        )
    lines.append("")
    lines.append("## ASSISTANT MESSAGES (opening and closing of the session)")
    if not kept_replies:
        lines.append("(none recorded)")
    for message in kept_replies:
        body, _ = _truncate(message.content, limits.assistant_chars)
        lines.append(f"[#{message.sequence}] {body}")

    # -- tools ------------------------------------------------------------
    tool_rows = db.execute(
        select(
            ToolCall.tool_name,
            func.count().label("calls"),
            func.sum(case((ToolCall.is_error.is_(True), 1), else_=0)).label("errors"),
        )
        .where(ToolCall.session_id == session.id)
        .group_by(ToolCall.tool_name)
        .order_by(func.count().desc())
        .limit(limits.max_tools)
    ).all()
    lines.append("")
    lines.append("## TOOL USAGE")
    if not tool_rows:
        lines.append("(none)")
    for row in tool_rows:
        suffix = f"  ({int(row.errors or 0)} errors)" if row.errors else ""
        lines.append(f"{row.tool_name}: {row.calls}{suffix}")

    # -- files ------------------------------------------------------------
    churn = file_churn_summary(db, session)
    kept_files = churn[: limits.max_files]
    if len(churn) > limits.max_files:
        truncations.append(
            f"{len(churn) - limits.max_files} of {len(churn)} files omitted"
        )
    lines.append("")
    lines.append("## PROJECT FILES CHANGED (scratch/temp files excluded)")
    lines.extend(
        kept_files
        or ["(none - the session may have edited files via shell commands)"]
    )

    # -- git --------------------------------------------------------------
    commit_rows = db.execute(
        select(GitCommit, SessionCommitLink)
        .join(SessionCommitLink, SessionCommitLink.git_commit_id == GitCommit.id)
        .where(
            SessionCommitLink.session_id == session.id,
            SessionCommitLink.likely_related.is_(True),
        )
        .order_by(GitCommit.committed_at.desc())
        .limit(limits.max_commits)
    ).all()
    lines.append("")
    lines.append("## COMMITS ASSOCIATED WITH THIS SESSION")
    lines.append(
        "(temporal + file-overlap association only - it is NOT established that "
        "the AI authored these commits)"
    )
    if not commit_rows:
        lines.append("(none)")
    for commit, _link in commit_rows:
        subject = (commit.message or "").splitlines()[0]
        lines.append(
            f"{commit.commit_hash[:10]}  +{commit.insertions}/-{commit.deletions} "
            f"in {commit.files_changed} file(s)  {subject}"
        )

    # -- candidate evidence, computed - not judged ------------------------
    signals = collect_signals(db, session)
    lines.append("")
    lines.append(
        "## REWORK SIGNALS (computed from the record - candidates, not conclusions)"
    )
    lines.append(
        'Each line is an observation. Some are false positives by nature - a '
        'prompt saying "correct me if I am wrong" matches a correction pattern '
        "without being one. Decide from the transcript, not from this list's length."
    )
    rendered = signals.render()[: limits.max_signals]
    lines.extend(rendered or ["(no signals detected)"])

    if truncations:
        lines.append("")
        lines.append("## WHAT WAS OMITTED FROM THIS DIGEST")
        lines.append(
            "Material below was not shown to you. Lower your confidence accordingly."
        )
        lines.extend(f"- {item}" for item in truncations)

    return SessionDigest(
        session_id=session.claude_session_id,
        text="\n".join(lines),
        truncations=truncations,
        signals=signals,
    )
