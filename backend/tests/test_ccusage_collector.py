"""Tests for the ccusage collector.

The payload shapes below are copied from real ``ccusage 20.0.20`` output on
this machine, including the two surprises: the session id lives in ``period``
in the index, and an unknown id returns JSON ``null`` with exit code 0.
"""

from __future__ import annotations

import json

import pytest

from app.collectors.ccusage.collector import (
    CcusageCollector,
    CcusageError,
    CcusageRunner,
    CcusageUnavailable,
)

INDEX_PAYLOAD = {
    "session": [
        {
            "agent": "claude",
            "period": "024411c9-d224-4e18-bf37-a3322d1941be",
            "inputTokens": 2286,
            "outputTokens": 950,
            "cacheCreationTokens": 49548,
            "cacheReadTokens": 74538,
            "totalTokens": 127322,
            "totalCost": 0.567929,
            "modelsUsed": ["claude-opus-5"],
            "modelBreakdowns": [
                {
                    "modelName": "claude-opus-5",
                    "inputTokens": 2286,
                    "outputTokens": 950,
                    "cacheCreationTokens": 49548,
                    "cacheReadTokens": 74538,
                    "cost": 0.567929,
                }
            ],
            "metadata": {"lastActivity": "2026-08-04T13:22:31.765Z"},
        }
    ],
    "totals": {"totalTokens": 127322, "totalCost": 0.567929},
}

DETAIL_PAYLOAD = {
    "sessionId": "024411c9-d224-4e18-bf37-a3322d1941be",
    "totalCost": 0.567929,
    "totalTokens": 127322,
    "entries": [
        {"timestamp": "2026-08-04T13:22:26.231Z", "model": "claude-opus-5",
         "inputTokens": 2, "outputTokens": 392, "cacheCreationTokens": 48886,
         "cacheReadTokens": 12826, "costUSD": 0.0},
        {"timestamp": "2026-08-04T13:22:31.765Z", "model": "claude-opus-5",
         "inputTokens": 2284, "outputTokens": 558, "cacheCreationTokens": 662,
         "cacheReadTokens": 61712, "costUSD": 0.0},
    ],
}


class FakeRunner(CcusageRunner):
    """Stands in for the binary; records the exact argv it was asked to run."""

    def __init__(self, responses: dict[tuple[str, ...], object], available: bool = True):
        super().__init__(binary="ccusage-fake")
        self.responses = responses
        self.available = available
        self.calls: list[tuple[str, ...]] = []

    def is_available(self) -> bool:  # type: ignore[override]
        return self.available

    def _run(self, args: list[str]) -> str:  # type: ignore[override]
        if not self.available:
            raise CcusageUnavailable("not installed")
        key = tuple(args)
        self.calls.append(key)
        if key not in self.responses:
            raise CcusageError(f"unexpected argv: {args}")
        value = self.responses[key]
        if isinstance(value, Exception):
            raise value
        return json.dumps(value)


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner(
        {
            ("session", "--json"): INDEX_PAYLOAD,
            ("session", "--json", "--id", "024411c9-d224-4e18-bf37-a3322d1941be"): DETAIL_PAYLOAD,
        }
    )


def test_session_id_is_read_from_the_period_field(runner):
    index = CcusageCollector(runner).index()
    assert list(index) == ["024411c9-d224-4e18-bf37-a3322d1941be"]


def test_collect_maps_tokens_and_cost_verbatim(runner):
    usage = CcusageCollector(runner).collect("024411c9-d224-4e18-bf37-a3322d1941be")
    assert usage is not None
    assert usage.source == "ccusage"
    assert usage.input_tokens == 2286
    assert usage.cache_creation_tokens == 49548
    assert usage.cache_read_tokens == 74538
    assert usage.total_tokens == 127322
    # Cost is taken as given: the collector never recomputes pricing.
    assert usage.cost_usd == pytest.approx(0.567929)


def test_model_breakdown_is_preserved(runner):
    usage = CcusageCollector(runner).collect("024411c9-d224-4e18-bf37-a3322d1941be")
    assert [m.model for m in usage.models] == ["claude-opus-5"]
    assert usage.models[0].total_tokens == 2286 + 950 + 49548 + 74538


def test_first_activity_comes_from_the_per_session_detail(runner):
    usage = CcusageCollector(runner).collect("024411c9-d224-4e18-bf37-a3322d1941be")
    assert usage.first_activity.isoformat() == "2026-08-04T13:22:26.231000+00:00"
    assert usage.last_activity.isoformat() == "2026-08-04T13:22:31.765000+00:00"
    assert any("2 API requests" in note for note in usage.notes)


def test_unknown_session_returns_none_not_zeroes(runner):
    assert CcusageCollector(runner).collect("does-not-exist") is None


def test_index_is_fetched_once_and_reused(runner):
    collector = CcusageCollector(runner)
    collector.collect("024411c9-d224-4e18-bf37-a3322d1941be")
    collector.collect("does-not-exist")
    assert runner.calls.count(("session", "--json")) == 1


def test_missing_binary_raises_a_distinguishable_error():
    collector = CcusageCollector(FakeRunner({}, available=False))
    with pytest.raises(CcusageUnavailable):
        collector.index()


def test_invalid_json_is_reported_as_a_ccusage_error():
    class BrokenRunner(FakeRunner):
        def _run(self, args):  # type: ignore[override]
            return "<html>not json</html>"

    with pytest.raises(CcusageError, match="valid JSON"):
        CcusageCollector(BrokenRunner({})).index()


def test_detail_failure_does_not_lose_the_index_data(runner):
    runner.responses[
        ("session", "--json", "--id", "024411c9-d224-4e18-bf37-a3322d1941be")
    ] = CcusageError("boom")
    usage = CcusageCollector(runner).collect("024411c9-d224-4e18-bf37-a3322d1941be")
    assert usage.total_tokens == 127322
    assert usage.first_activity is None  # unknown, not invented
