"""Orchestrate the analysis pipeline and store its result.

    stored session -> digest (bounded) -> redaction -> LLM -> validated JSON -> ai_analyses

Two rules hold throughout:

* nothing is sent anywhere unless ``AI_ANALYSIS_ENABLED`` is true (§19);
* a failure is reported, never papered over with a fabricated analysis (§26).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.analyzers.digest import DigestLimits, SessionDigest, build_digest
from app.analyzers.redaction import RedactionReport, Redactor
from app.analyzers.schema import AnalysisRejected, AnalysisResult, validate_result
from app.config import Settings, get_settings
from app.llm.base import LLMError, LLMNotConfigured, LLMProvider, LLMResponse
from app.llm.registry import build_provider
from app.models.entities import AIAnalysis
from app.models.entities import Session as SessionRow

#: Bump when the instructions change materially - stored with every row so an
#: analysis can be traced to the prompt that produced it.
PROMPT_VERSION = "1.0"

SYSTEM_PROMPT = """\
You analyse a single software-development session carried out with an AI coding \
assistant, for a research dataset about AI-assisted development.

You are given a DIGEST: a bounded, factual extract of one session. It is not the \
full transcript. Everything under "SESSION FACTS" was measured from the record.

Rules:

1. Judge only from the digest. If it does not show something, say so instead of \
inventing it. `result` and `success` may be null; a null is a correct answer, a \
plausible-sounding guess is not.
2. Never restate or contradict the measured facts. Token counts, durations, tool \
counts and file lists are already known - your job is interpretation, not \
recounting.
3. `requires_rework` is true only when the digest shows work being redone: a \
correction the developer asked for, a change reverted, the same thing built \
twice, a solution rejected, or failing tests forcing new edits. A long session, \
many prompts, or many tool calls are NOT rework - iterating toward a goal is \
ordinary work.
4. The REWORK SIGNALS section lists computed candidates, not conclusions. Some \
are false positives by construction - a prompt reading "correct me if I am wrong" \
matches a correction pattern without being a correction. Quote only signals the \
surrounding context actually supports, and ignore the rest.
5. Every item in `rework_evidence` must be a concrete observation a reader could \
verify in the session. If you cannot write one, `requires_rework` is false.
6. `technologies` lists what the session actually worked with, not what the \
project might use in general.
7. Set `confidence` honestly. If the digest says material was omitted, or the \
session's purpose is ambiguous, lower it.

Answer only in the required structured format."""


@dataclass
class AnalysisOutcome:
    session_id: str
    analysis: AIAnalysis | None
    digest: SessionDigest
    redaction: RedactionReport
    provider: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.analysis is not None


class AnalysisDisabled(RuntimeError):
    """AI_ANALYSIS_ENABLED is false. Collection is unaffected."""


class SessionAnalyzer:
    def __init__(
        self,
        db: DbSession,
        *,
        settings: Settings | None = None,
        provider: LLMProvider | None = None,
        limits: DigestLimits | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._provider = provider
        self.limits = limits or DigestLimits()

    def provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = build_provider(self.settings)
        return self._provider

    def redactor(self) -> Redactor:
        return Redactor(
            home=Path.home(), enabled=self.settings.llm_redact_enabled
        )

    def prepare(self, session: SessionRow) -> tuple[SessionDigest, RedactionReport]:
        """Build the digest and redact it. Sends nothing anywhere.

        Exposed on its own so ``analyze --dry-run`` can show exactly what would
        leave the machine before anyone approves it.
        """
        digest = build_digest(self.db, session, self.limits)
        text, report = self.redactor()(digest.text)
        digest.text = text
        return digest, report

    def analyze(self, session: SessionRow, *, store: bool = True) -> AnalysisOutcome:
        if not self.settings.ai_analysis_enabled:
            raise AnalysisDisabled(
                "AI_ANALYSIS_ENABLED is false; no data was sent. "
                "Objective collection is unaffected."
            )

        digest, redaction = self.prepare(session)
        try:
            provider = self.provider()
        except LLMNotConfigured:
            # A configuration problem is the caller's to fix, not a failed
            # analysis to record. It propagates so the API can answer 503
            # rather than 502.
            raise

        try:
            response: LLMResponse[AnalysisResult] = provider.complete_structured(
                system=SYSTEM_PROMPT,
                prompt=digest.text,
                schema=AnalysisResult,
                max_tokens=self.settings.llm_max_tokens,
            )
            result = validate_result(response.parsed)
        except LLMNotConfigured:
            raise
        except (LLMError, AnalysisRejected) as exc:
            return AnalysisOutcome(
                session_id=session.claude_session_id,
                analysis=None,
                digest=digest,
                redaction=redaction,
                error=f"{type(exc).__name__}: {exc}",
            )

        analysis = self._to_row(session, result, response)
        if store:
            self.db.add(analysis)
            self.db.flush()
        return AnalysisOutcome(
            session_id=session.claude_session_id,
            analysis=analysis,
            digest=digest,
            redaction=redaction,
            provider=response.provider,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    def _to_row(
        self,
        session: SessionRow,
        result: AnalysisResult,
        response: LLMResponse[AnalysisResult],
    ) -> AIAnalysis:
        return AIAnalysis(
            session_id=session.id,
            objective=result.objective,
            summary=result.summary,
            result=result.result,
            task_type=result.task_type.value,
            complexity=result.complexity.value,
            success=result.success,
            requires_rework=result.requires_rework,
            rework_evidence=list(result.rework_evidence),
            technologies=list(result.technologies),
            problems_encountered=list(result.problems_encountered),
            confidence=result.confidence,
            model=response.model,
            provider=response.provider,
            prompt_version=PROMPT_VERSION,
            created_at=datetime.now(timezone.utc),
        )

    # -- helpers ----------------------------------------------------------
    def latest_analysis(self, session: SessionRow) -> AIAnalysis | None:
        return self.db.scalar(
            select(AIAnalysis)
            .where(AIAnalysis.session_id == session.id)
            .order_by(AIAnalysis.created_at.desc())
        )

    def unanalysed_sessions(self, limit: int | None = None) -> list[SessionRow]:
        """Sessions with no analysis yet, largest first.

        Active sessions are skipped: they are still being written, so analysing
        one would judge an unfinished story.
        """
        analysed = select(AIAnalysis.session_id).distinct()
        query = (
            select(SessionRow)
            .where(
                SessionRow.id.not_in(analysed),
                SessionRow.status != "active",
                SessionRow.human_prompt_count > 0,
            )
            .order_by(SessionRow.tool_call_count.desc())
        )
        if limit:
            query = query.limit(limit)
        return list(self.db.scalars(query).all())
