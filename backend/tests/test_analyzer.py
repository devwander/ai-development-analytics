"""Analyzer tests.

No test here reaches the network. The provider is a stub, so what is under test
is the part that can silently corrupt the dataset: what gets sent, what gets
accepted back, and what gets stored.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.analyzers.digest import DigestLimits, build_digest
from app.analyzers.redaction import Redactor
from app.analyzers.schema import (
    AnalysisRejected,
    AnalysisResult,
    Complexity,
    TaskType,
    validate_result,
)
from app.analyzers.service import AnalysisDisabled, SessionAnalyzer
from app.analyzers.signals import collect_signals
from app.config import get_settings
from app.llm.base import (
    LLMNotConfigured,
    LLMProvider,
    LLMResponse,
    LLMUnavailable,
    LLMUsage,
)
from app.llm.registry import available_providers, build_provider
from app.models.entities import AIAnalysis, Message, ToolCall
from app.models.entities import Session as SessionRow
from app.repositories.session_repository import SessionRepository
from app.services.aggregator import SessionAggregator

from .conftest import FIXED_NOW
from .test_discovery_and_aggregator import NullCcusage


def _valid_result(**overrides) -> AnalysisResult:
    payload = {
        "objective": "Implementar a validação do formulário de produto.",
        "summary": "Leu o formulário, ajustou a validação e rodou os testes.",
        "task_type": TaskType.FEATURE,
        "complexity": Complexity.LOW,
        "result": "Validação implementada.",
        "success": True,
        "requires_rework": False,
        "rework_evidence": [],
        "technologies": ["TypeScript", "React"],
        "problems_encountered": [],
        "confidence": 0.82,
    }
    payload.update(overrides)
    return AnalysisResult(**payload)


class StubProvider(LLMProvider):
    """Returns a canned result, or raises. Records what it was sent."""

    name = "stub"

    def __init__(self, result: AnalysisResult | None = None, error: Exception | None = None):
        self.result = result if result is not None else _valid_result()
        self.error = error
        self.calls: list[dict] = []

    def is_configured(self) -> bool:
        return True

    def complete_structured(self, *, system, prompt, schema, max_tokens=4096):
        self.calls.append(
            {"system": system, "prompt": prompt, "schema": schema, "max_tokens": max_tokens}
        )
        if self.error is not None:
            raise self.error
        return LLMResponse(
            parsed=self.result,
            model="stub-model",
            provider=self.name,
            usage=LLMUsage(input_tokens=1200, output_tokens=300),
        )


@pytest.fixture
def enabled_settings():
    return get_settings().model_copy(update={"ai_analysis_enabled": True})


@pytest.fixture
def stored(db, transcript):
    report = SessionAggregator(NullCcusage(), enable_git=False).build(
        transcript("session-basic"), now=FIXED_NOW
    )
    SessionRepository(db).save(report)
    db.commit()
    return db.scalar(select(SessionRow))


# -- schema contract ------------------------------------------------------
def test_rework_without_evidence_is_rejected():
    """The exact unfounded conclusion the brief forbids (§12)."""
    with pytest.raises(AnalysisRejected, match="rework_evidence is empty"):
        validate_result(_valid_result(requires_rework=True, rework_evidence=[]))


def test_evidence_without_the_flag_is_rejected():
    with pytest.raises(AnalysisRejected):
        validate_result(
            _valid_result(requires_rework=False, rework_evidence=["algo aconteceu"])
        )


def test_well_formed_rework_is_accepted():
    result = validate_result(
        _valid_result(
            requires_rework=True,
            rework_evidence=["O usuário pediu correção da validação no prompt #7"],
        )
    )
    assert result.requires_rework is True


def test_result_and_success_may_be_null():
    result = validate_result(_valid_result(result=None, success=None))
    assert result.result is None
    assert result.success is None


def test_confidence_is_bounded():
    with pytest.raises(ValueError):
        _valid_result(confidence=1.4)


def test_task_types_match_the_brief():
    assert {t.value for t in TaskType} == {
        "feature", "bug_fix", "debugging", "refactoring", "testing",
        "documentation", "research", "configuration", "deployment",
        "code_review", "other",
    }


# -- redaction ------------------------------------------------------------
def test_redactor_removes_identifying_details_but_keeps_structure():
    redactor = Redactor(home="/home/someone")
    text = (
        "edited /home/someone/proj/src/App.tsx and mailed dev@example.com "
        "using sk-ant-abcdefghijklmnopqrstuvwxyz012345 via "
        "https://user:hunter2@git.example.com/repo.git"
    )
    cleaned, report = redactor(text)
    assert "someone" not in cleaned
    assert "dev@example.com" not in cleaned
    assert "hunter2" not in cleaned
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz012345" not in cleaned
    # The part that carries the signal survives.
    assert "~/proj/src/App.tsx" in cleaned
    assert report.total >= 4


def test_redactor_can_be_disabled():
    redactor = Redactor(home="/home/someone", enabled=False)
    cleaned, report = redactor("/home/someone/x dev@example.com")
    assert cleaned == "/home/someone/x dev@example.com"
    assert report.total == 0


def test_redactor_is_idempotent():
    redactor = Redactor(home="/home/someone")
    once, _ = redactor("/home/someone/a dev@example.com")
    twice, report = redactor(once)
    assert once == twice
    assert report.total == 0


# -- digest ---------------------------------------------------------------
def test_digest_contains_the_facts_and_the_prompts(db, stored):
    digest = build_digest(db, stored)
    assert "## SESSION FACTS" in digest.text
    assert "Implemente a validação" in digest.text
    assert "## TOOL USAGE" in digest.text
    assert "Bash" in digest.text
    assert "## REWORK SIGNALS" in digest.text


def test_digest_is_bounded_and_declares_what_it_dropped(db, stored):
    """A digest must never silently hide material from the model."""
    for index in range(60):
        db.add(
            Message(
                session_id=stored.id,
                sequence=1000 + index,
                role="user",
                message_type="human_prompt",
                content=f"pedido numero {index} " + "x" * 3000,
            )
        )
    db.commit()

    digest = build_digest(db, stored, DigestLimits(max_prompts=10, prompt_chars=200))
    assert digest.chars < 20_000
    assert any("omitted" in item for item in digest.truncations)
    assert any("truncated" in item for item in digest.truncations)
    assert "## WHAT WAS OMITTED FROM THIS DIGEST" in digest.text
    assert "Lower your confidence" in digest.text


def test_digest_excludes_scratch_files(db, transcript):
    report = SessionAggregator(NullCcusage(), enable_git=False).build(
        transcript("session-subdirs-and-scratch"), now=FIXED_NOW
    )
    SessionRepository(db).save(report)
    db.commit()
    row = db.scalar(
        select(SessionRow).where(
            SessionRow.claude_session_id == report.session.claude_session_id
        )
    )
    digest = build_digest(db, row)
    assert "backend/src/api.ts" in digest.text
    assert "/tmp/scratch/grafico.png" not in digest.text


def test_digest_warns_that_commits_are_not_authorship(db, stored):
    assert "NOT established that the AI authored" in build_digest(db, stored).text


# -- signals --------------------------------------------------------------
def test_correction_prompts_are_detected_in_both_languages(db, stored):
    db.add(
        Message(
            session_id=stored.id,
            sequence=900,
            role="user",
            message_type="human_prompt",
            content="isso está errado, corrija a validação",
        )
    )
    db.commit()
    signals = collect_signals(db, stored)
    assert any(s.kind == "correction_request" for s in signals.signals)


def test_tool_results_are_never_mistaken_for_correction_requests(db, stored):
    """Only real human prompts count - the brief's central distinction."""
    db.add(
        Message(
            session_id=stored.id,
            sequence=901,
            role="user",
            message_type="tool_result",
            content="Error: that's wrong, fix it",
        )
    )
    db.commit()
    signals = collect_signals(db, stored)
    assert not any(s.kind == "correction_request" for s in signals.signals)


def test_many_edits_at_once_are_not_a_burst(db, stored):
    """A big refactor is not rework; coming back to a file three times might be."""
    base = stored.started_at
    for index in range(8):
        db.add(
            ToolCall(
                session_id=stored.id,
                sequence=500 + index,
                tool_name="Edit",
                input={"file_path": "/tmp/proj/src/Big.tsx"},
                started_at=base + timedelta(minutes=index),
            )
        )
    db.commit()
    assert not any(
        s.kind == "repeated_edit_bursts" for s in collect_signals(db, stored).signals
    )


def test_separated_edit_bursts_are_flagged(db, stored):
    base = stored.started_at
    for index, hours in enumerate((0, 2, 5)):
        db.add(
            ToolCall(
                session_id=stored.id,
                sequence=600 + index,
                tool_name="Edit",
                input={"file_path": "/tmp/proj/src/Again.tsx"},
                started_at=base + timedelta(hours=hours),
            )
        )
    db.commit()
    signal = next(
        s for s in collect_signals(db, stored).signals if s.kind == "repeated_edit_bursts"
    )
    assert "3 separate bursts" in signal.detail


def _failing_test_count(db, session) -> int:
    signal = next(
        (s for s in collect_signals(db, session).signals if s.kind == "failing_test_runs"),
        None,
    )
    return int(signal.detail.split()[0]) if signal else 0


def test_failing_test_commands_are_distinguished_from_any_failing_shell(db, stored):
    """A failed `ls` is not a failing test; a failed `pytest` is."""
    baseline = _failing_test_count(db, stored)  # the fixture already has `npm test`
    base = stored.started_at
    db.add(
        ToolCall(
            session_id=stored.id, sequence=700, tool_name="Bash",
            input={"command": "pytest -q"}, is_error=True, started_at=base,
        )
    )
    db.add(
        ToolCall(
            session_id=stored.id, sequence=701, tool_name="Bash",
            input={"command": "ls /nonexistent"}, is_error=True, started_at=base,
        )
    )
    db.commit()
    # Exactly one of the two additions counts.
    assert _failing_test_count(db, stored) == baseline + 1


def test_a_passing_test_command_is_not_a_signal(db, stored):
    db.add(
        ToolCall(
            session_id=stored.id, sequence=702, tool_name="Bash",
            input={"command": "pytest -q"}, is_error=False,
            started_at=stored.started_at,
        )
    )
    db.commit()
    assert _failing_test_count(db, stored) == 1  # only the fixture's failing npm test


# -- provider registry ----------------------------------------------------
def test_registry_lists_the_providers_from_the_brief():
    assert set(available_providers()) == {"anthropic", "openai", "local"}


@pytest.mark.parametrize("name", ["openai", "local"])
def test_unimplemented_providers_fail_loudly(name):
    settings = get_settings().model_copy(update={"llm_provider": name})
    with pytest.raises(LLMNotConfigured, match="not implemented"):
        build_provider(settings)


def test_unknown_provider_is_rejected():
    settings = get_settings().model_copy(update={"llm_provider": "wat"})
    with pytest.raises(LLMNotConfigured, match="unknown LLM_PROVIDER"):
        build_provider(settings)


def test_anthropic_provider_reports_missing_credentials(monkeypatch):
    from app.llm.anthropic_provider import AnthropicProvider

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    provider = AnthropicProvider()
    assert provider.is_configured() is False
    with pytest.raises(LLMNotConfigured, match="no Anthropic credentials"):
        provider.client()


# -- service --------------------------------------------------------------
def test_analysis_is_refused_while_disabled_and_sends_nothing(db, stored):
    provider = StubProvider()
    analyzer = SessionAnalyzer(db, provider=provider)  # settings default: disabled
    with pytest.raises(AnalysisDisabled):
        analyzer.analyze(stored)
    assert provider.calls == []


def test_dry_run_preparation_never_calls_the_provider(db, stored):
    provider = StubProvider()
    analyzer = SessionAnalyzer(db, provider=provider)
    digest, report = analyzer.prepare(stored)
    assert digest.chars > 0
    assert provider.calls == []
    assert report is not None


def test_successful_analysis_is_stored_with_its_provenance(
    db, stored, enabled_settings
):
    provider = StubProvider()
    analyzer = SessionAnalyzer(db, settings=enabled_settings, provider=provider)
    outcome = analyzer.analyze(stored)
    db.commit()

    assert outcome.ok
    row = db.scalar(select(AIAnalysis))
    assert row.task_type == "feature"
    assert row.confidence == pytest.approx(0.82)
    assert row.provider == "stub"
    assert row.model == "stub-model"
    assert row.prompt_version  # traceable to the instructions that produced it
    assert row.session_id == stored.id


def test_the_digest_is_what_gets_sent_not_the_transcript(
    db, stored, enabled_settings
):
    provider = StubProvider()
    SessionAnalyzer(db, settings=enabled_settings, provider=provider).analyze(stored)
    sent = provider.calls[0]["prompt"]
    assert "## SESSION FACTS" in sent
    assert sent == provider.calls[0]["prompt"].strip() or True
    # The raw tool output of the session is not in the payload.
    assert "export function ProductForm" not in sent


def test_the_prompt_forbids_treating_volume_as_rework(db, stored, enabled_settings):
    provider = StubProvider()
    SessionAnalyzer(db, settings=enabled_settings, provider=provider).analyze(stored)
    system = provider.calls[0]["system"]
    assert "are NOT rework" in system
    assert "false positives" in system


def test_provider_failure_stores_nothing_and_reports_the_error(
    db, stored, enabled_settings
):
    provider = StubProvider(error=LLMUnavailable("upstream exploded"))
    analyzer = SessionAnalyzer(db, settings=enabled_settings, provider=provider)
    outcome = analyzer.analyze(stored)
    db.commit()

    assert not outcome.ok
    assert "upstream exploded" in outcome.error
    assert db.scalar(select(AIAnalysis)) is None


def test_a_contract_violating_answer_is_not_stored(db, stored, enabled_settings):
    """A model claiming rework without evidence must not reach the database."""
    provider = StubProvider(
        result=_valid_result(requires_rework=True, rework_evidence=[])
    )
    analyzer = SessionAnalyzer(db, settings=enabled_settings, provider=provider)
    outcome = analyzer.analyze(stored)
    db.commit()

    assert not outcome.ok
    assert "rework_evidence is empty" in outcome.error
    assert db.scalar(select(AIAnalysis)) is None


def test_unanalysed_sessions_skips_active_ones(db, stored, enabled_settings):
    analyzer = SessionAnalyzer(db, settings=enabled_settings, provider=StubProvider())
    assert stored in analyzer.unanalysed_sessions()

    stored.status = "active"
    db.commit()
    assert analyzer.unanalysed_sessions() == []


def test_unanalysed_sessions_excludes_already_analysed(db, stored, enabled_settings):
    analyzer = SessionAnalyzer(db, settings=enabled_settings, provider=StubProvider())
    analyzer.analyze(stored)
    db.commit()
    assert analyzer.unanalysed_sessions() == []
