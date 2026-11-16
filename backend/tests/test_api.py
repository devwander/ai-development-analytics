"""REST API tests.

The endpoints are thin, so these focus on the two things that can silently
produce wrong numbers: pagination/filtering, and how usage sources are combined.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_db
from app.main import app
from app.repositories.session_repository import SessionRepository
from app.schemas.observed import (
    GitCommit,
    GitCorrelation,
    GitInfo,
    ModelUsage,
    Usage,
)
from app.services.aggregator import SessionAggregator

from .conftest import FIXED_NOW
from .test_discovery_and_aggregator import NullCcusage


def _build(transcript, name):
    return SessionAggregator(NullCcusage(), enable_git=False).build(
        transcript(name), now=FIXED_NOW
    )


def _ccusage_usage(total_tokens: int, cost: float) -> Usage:
    return Usage(
        source="ccusage",
        total_tokens=total_tokens,
        input_tokens=1,
        output_tokens=2,
        cache_creation_tokens=3,
        cache_read_tokens=total_tokens - 6,
        cost_usd=cost,
        collected_at=FIXED_NOW,
        models=[
            ModelUsage(model="claude-opus-5", total_tokens=total_tokens, cost_usd=cost)
        ],
    )


@pytest.fixture
def seeded(db, transcript):
    """Two sessions: one with both usage sources, one with only the transcript.

    That asymmetry is the real shape of the data - ccusage has no row for a
    resumed session - and it is what the aggregate queries must handle.
    """
    repository = SessionRepository(db)

    with_ccusage = _build(transcript, "session-basic")
    with_ccusage.usage.append(_ccusage_usage(500_000, 1.25))
    with_ccusage.git = GitInfo(
        is_repository=True,
        repository_root="/tmp/proj",
        repository_url="git@example.com:acme/app.git",
        commits_in_window=[
            GitCommit(
                commit_hash="a" * 40,
                message="feat: product form",
                committed_at=with_ccusage.session.ended_at + timedelta(minutes=3),
                files_changed=1,
                insertions=10,
                deletions=2,
                changed_files=["src/ProductForm.tsx"],
            )
        ],
        correlations=[
            GitCorrelation(
                commit_hash="a" * 40,
                likely_related=True,
                overlapping_files=["src/ProductForm.tsx"],
                overlap_count=1,
                minutes_after_session_end=3.0,
                reasons=["1 file(s) also modified by Claude during the session"],
            )
        ],
    )
    repository.save(with_ccusage)

    transcript_only = _build(transcript, "session-subdirs-and-scratch")
    repository.save(transcript_only)
    db.commit()
    return {
        "with_ccusage": with_ccusage.session.claude_session_id,
        "transcript_only": transcript_only.session.claude_session_id,
    }


@pytest.fixture
def client(db, seeded):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        test_client.seeded = seeded
        yield test_client
    app.dependency_overrides.clear()


# -- system ---------------------------------------------------------------
def test_health_reports_configuration(client):
    body = client.get("/health").json()
    assert body["ai_analysis_enabled"] is False
    assert "database" in body


def test_openapi_exposes_every_endpoint_from_the_brief(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {
        "/projects",
        "/projects/{project_id}",
        "/sessions",
        "/sessions/{session_id}",
        "/sessions/{session_id}/messages",
        "/sessions/{session_id}/tools",
        "/sessions/{session_id}/usage",
        "/sessions/{session_id}/git",
        "/sessions/{session_id}/analysis",
        "/sessions/{session_id}/analyze",
        "/sessions/{session_id}/export",
        "/analytics/overview",
        "/analytics/projects/{project_id}",
    } <= paths


# -- projects -------------------------------------------------------------
def test_list_projects_returns_metrics(client):
    """Keyed by path, not name: two distinct projects can share a basename."""
    projects = client.get("/projects").json()
    assert len(projects) == 2
    by_path = {p["path"]: p for p in projects}
    billed = by_path["/tmp/proj"]
    assert billed["sessions"] == 1
    assert billed["cost_usd"] == pytest.approx(1.25)
    assert billed["total_tokens"] == 500_000
    assert billed["repository_url"] == "git@example.com:acme/app.git"
    # The transcript-only project could not determine a cost: null, not zero.
    assert by_path["/home/dev/proj"]["cost_usd"] is None
    assert by_path["/home/dev/proj"]["sessions"] == 1


def test_unknown_project_is_404(client):
    assert client.get("/projects/99999").status_code == 404


# -- sessions -------------------------------------------------------------
def test_list_sessions_is_paginated(client):
    body = client.get("/sessions?limit=1").json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["limit"] == 1 and body["offset"] == 0
    second = client.get("/sessions?limit=1&offset=1").json()
    assert second["items"][0]["claude_session_id"] != body["items"][0]["claude_session_id"]


def test_session_list_carries_the_usage_source(client):
    items = {s["claude_session_id"]: s for s in client.get("/sessions").json()["items"]}
    assert items[client.seeded["with_ccusage"]]["usage_source"] == "ccusage"
    assert items[client.seeded["transcript_only"]]["usage_source"] == "claude_transcript"


@pytest.mark.parametrize(
    "query,expected",
    [("status=completed", 2), ("status=active", 0), ("search=validação", 1)],
)
def test_session_filters(client, query, expected):
    assert client.get(f"/sessions?{query}").json()["total"] == expected


def test_session_detail_includes_files_and_provenance(client):
    body = client.get(f"/sessions/{client.seeded['with_ccusage']}").json()
    assert body["project_name"] == "proj"
    assert body["confirmed_human_prompt_count"] == 1
    assert len(body["source"]["transcript_sha256"]) == 64
    assert any(f["file_path"].endswith("ProductForm.tsx") for f in body["files"])


def test_session_can_be_addressed_by_numeric_id(client):
    uuid = client.seeded["with_ccusage"]
    numeric = client.get(f"/sessions/{uuid}").json()["id"]
    assert client.get(f"/sessions/{numeric}").json()["claude_session_id"] == uuid


def test_unknown_session_is_404(client):
    assert client.get("/sessions/does-not-exist").status_code == 404


# -- messages / tools -----------------------------------------------------
def test_messages_can_be_filtered_to_real_human_prompts(client):
    session_id = client.seeded["with_ccusage"]
    everything = client.get(f"/sessions/{session_id}/messages").json()
    prompts = client.get(
        f"/sessions/{session_id}/messages?message_type=human_prompt"
    ).json()
    assert prompts["total"] == 1
    assert prompts["total"] < everything["total"]
    assert prompts["items"][0]["origin_kind"] == "human"


def test_tools_returns_stats_and_paginated_calls(client):
    body = client.get(f"/sessions/{client.seeded['with_ccusage']}/tools?limit=1").json()
    stats = {s["tool_name"]: s for s in body["stats"]}
    assert stats["Bash"]["errors"] == 1
    assert stats["Read"]["errors"] == 0
    assert body["calls"]["total"] == 3
    assert len(body["calls"]["items"]) == 1


def test_tools_can_be_filtered_by_name(client):
    body = client.get(
        f"/sessions/{client.seeded['with_ccusage']}/tools?tool_name=Edit"
    ).json()
    assert body["calls"]["total"] == 1
    assert body["calls"]["items"][0]["tool_name"] == "Edit"


# -- usage ----------------------------------------------------------------
def test_usage_exposes_every_source_and_names_the_preferred_one(client):
    body = client.get(f"/sessions/{client.seeded['with_ccusage']}/usage").json()
    assert {t["source"] for t in body["totals"]} == {"ccusage", "claude_transcript"}
    assert body["preferred"]["source"] == "ccusage"
    # The two totals describe the same tokens; a caller must not add them.
    assert body["preferred"]["total_tokens"] == 500_000


def test_preferred_usage_falls_back_to_the_transcript(client):
    body = client.get(f"/sessions/{client.seeded['transcript_only']}/usage").json()
    assert body["preferred"]["source"] == "claude_transcript"
    assert body["preferred"]["cost_usd"] is None


# -- git ------------------------------------------------------------------
def test_git_associations_carry_their_evidence(client):
    body = client.get(f"/sessions/{client.seeded['with_ccusage']}/git").json()
    assert body["likely_related_count"] == 1
    association = body["associations"][0]
    assert association["likely_related"] is True
    assert association["reasons"]
    assert association["commit"]["insertions"] == 10
    # No endpoint may ever assert authorship.
    assert "caused_by_ai" not in association


def test_git_is_empty_for_a_session_without_a_repository(client):
    body = client.get(f"/sessions/{client.seeded['transcript_only']}/git").json()
    assert body["associations"] == []
    assert body["likely_related_count"] == 0


# -- export -----------------------------------------------------------------
def test_export_bundles_every_section_unpaginated(client):
    session_id = client.seeded["with_ccusage"]
    body = client.get(f"/sessions/{session_id}/export").json()
    assert body["session"]["claude_session_id"] == session_id
    assert len(body["messages"]) == body["session"]["message_count"]
    assert len(body["tool_calls"]) == 3
    assert {s["source"] for s in body["usage"]["totals"]} == {"ccusage", "claude_transcript"}
    assert body["git"]["likely_related_count"] == 1
    assert body["analysis"] is None
    assert body["survey"] is None


def test_export_is_addressable_by_numeric_id(client):
    uuid = client.seeded["transcript_only"]
    numeric = client.get(f"/sessions/{uuid}").json()["id"]
    body = client.get(f"/sessions/{numeric}/export").json()
    assert body["session"]["claude_session_id"] == uuid
    assert body["git"]["associations"] == []


# -- analysis (inferred) --------------------------------------------------
def test_analysis_is_404_until_the_analyzer_runs(client):
    assert client.get(f"/sessions/{client.seeded['with_ccusage']}/analysis").status_code == 404


def test_analyze_refuses_explicitly_when_disabled(client):
    response = client.post(f"/sessions/{client.seeded['with_ccusage']}/analyze")
    assert response.status_code == 503
    assert "AI_ANALYSIS_ENABLED" in response.json()["detail"]


def test_analyze_without_credentials_is_503_not_502(client, monkeypatch):
    """A missing key is a configuration problem, not a failed analysis."""
    from app.config import get_settings

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    enabled = get_settings().model_copy(
        update={"ai_analysis_enabled": True, "llm_api_key": ""}
    )
    app.dependency_overrides[get_settings] = lambda: enabled
    try:
        response = client.post(f"/sessions/{client.seeded['with_ccusage']}/analyze")
        assert response.status_code == 503
        assert "credentials" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_analyze_stores_and_returns_the_inferred_analysis(client, db, monkeypatch):
    """The endpoint marks its payload as inferred, unlike every other one."""
    from app.config import get_settings

    from .test_analyzer import StubProvider

    enabled = get_settings().model_copy(update={"ai_analysis_enabled": True})
    app.dependency_overrides[get_settings] = lambda: enabled
    stub = StubProvider()
    monkeypatch.setattr(
        "app.analyzers.service.build_provider", lambda settings: stub
    )
    try:
        session_id = client.seeded["with_ccusage"]
        created = client.post(f"/sessions/{session_id}/analyze")
        assert created.status_code == 201
        body = created.json()
        assert body["inferred"] is True
        assert body["task_type"] == "feature"
        assert body["confidence"] == pytest.approx(0.82)

        fetched = client.get(f"/sessions/{session_id}/analysis")
        assert fetched.status_code == 200
        assert fetched.json()["objective"] == body["objective"]
        assert client.get(f"/sessions/{session_id}").json()["has_analysis"] is True
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_overview_counts_inferred_task_types_once_analysed(client, monkeypatch):
    from app.config import get_settings

    from .test_analyzer import StubProvider

    enabled = get_settings().model_copy(update={"ai_analysis_enabled": True})
    app.dependency_overrides[get_settings] = lambda: enabled
    monkeypatch.setattr(
        "app.analyzers.service.build_provider", lambda settings: StubProvider()
    )
    try:
        client.post(f"/sessions/{client.seeded['with_ccusage']}/analyze")
        assert client.get("/analytics/overview").json()["task_types"] == {"feature": 1}
    finally:
        app.dependency_overrides.pop(get_settings, None)


# -- analytics ------------------------------------------------------------
def test_overview_counts_each_session_once_across_sources(client):
    """The regression this whole design exists to prevent.

    One session has both a ccusage and a transcript total for the same tokens.
    Summing the usages table would report 500,000 + 6,051; the aggregate must
    report 500,000 for it.
    """
    body = client.get("/analytics/overview").json()
    assert body["sessions"] == 2
    assert body["usage_sources"] == {"ccusage": 1, "claude_transcript": 1}
    assert body["sessions_without_usage"] == 0

    per_session = client.get("/sessions").json()["items"]
    assert body["total_tokens"] == sum(s["total_tokens"] for s in per_session)
    assert body["cost_usd"] == pytest.approx(1.25)


def test_overview_reports_inferred_task_types_as_empty(client):
    assert client.get("/analytics/overview").json()["task_types"] == {}


def test_overview_buckets_sessions_by_day(client):
    per_day = client.get("/analytics/overview").json()["per_day"]
    assert per_day
    assert sum(bucket["sessions"] for bucket in per_day) == 2
    assert all(bucket["day"] for bucket in per_day)


def test_project_analytics_is_scoped(client):
    project_id = next(
        p["id"] for p in client.get("/projects").json() if p["path"] == "/tmp/proj"
    )
    body = client.get(f"/analytics/projects/{project_id}").json()
    assert body["sessions"] == 1
    assert body["project"]["name"] == "proj"
    assert body["total_tokens"] == 500_000
    assert {t["tool_name"] for t in body["tools"]} == {"Read", "Edit", "Bash"}


# -- research endpoints ---------------------------------------------------
def test_overview_carries_the_caveats(client):
    """A productivity number without coverage beside it reads as more solid."""
    body = client.get("/analytics/overview").json()
    assert body["coverage"]["sessions"] == 2
    # Only one of the two seeded sessions has a linked commit.
    assert body["coverage"]["with_linked_commit"] == 1

    attribution = body["attribution"]
    assert attribution["commit_insertions"] == 10  # the seeded commit
    assert attribution["attribution_ratio"] == pytest.approx(
        attribution["ai_insertions"] / attribution["commit_insertions"]
    )
    assert "claude_code_versions" in body["validity"]
    assert body["survey"]["responses"] == 0


def test_project_analytics_includes_the_research_panels(client):
    project_id = next(
        p["id"] for p in client.get("/projects").json() if p["path"] == "/tmp/proj"
    )
    body = client.get(f"/analytics/projects/{project_id}").json()
    for key in ("attribution", "coverage", "validity", "effort_shape", "survey"):
        assert key in body, key
    assert body["baseline"] is None  # not computed for this project
    assert body["coverage"]["with_linked_commit"] == 1


def test_project_comparison_lists_every_project_normalised(client):
    rows = client.get("/analytics/projects").json()
    assert len(rows) == 2
    by_name = {row["name"]: row for row in rows}
    for row in rows:
        assert row["active_turn_hours"] >= 0
        assert "attribution_ratio" in row
        assert "commit_coverage" in row
    assert set(by_name) == {"proj"}  # both fixtures share a basename
    assert sum(row["sessions"] for row in rows) == 2


def test_survey_round_trip(client):
    session_id = client.seeded["with_ccusage"]
    assert client.get(f"/sessions/{session_id}/survey").status_code == 404

    created = client.put(
        f"/sessions/{session_id}/survey",
        json={"perceived_quality": 4, "trust_in_output": 5, "rework_reported": True},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["declared"] is True
    assert body["perceived_quality"] == 4
    # A question that was not asked stays null, not 0.
    assert body["perceived_productivity"] is None

    assert client.get(f"/sessions/{session_id}/survey").json()["trust_in_output"] == 5
    assert client.get(f"/sessions/{session_id}").json()["has_survey"] is True


def test_survey_replaces_rather_than_accumulates(client):
    session_id = client.seeded["with_ccusage"]
    client.put(f"/sessions/{session_id}/survey", json={"perceived_quality": 2})
    client.put(f"/sessions/{session_id}/survey", json={"perceived_quality": 5})
    assert client.get(f"/sessions/{session_id}/survey").json()["perceived_quality"] == 5
    assert client.get("/analytics/overview").json()["survey"]["responses"] == 1


@pytest.mark.parametrize("value", [0, 6, -1])
def test_survey_rejects_answers_outside_the_likert_scale(client, value):
    response = client.put(
        f"/sessions/{client.seeded['with_ccusage']}/survey",
        json={"perceived_quality": value},
    )
    assert response.status_code == 422


def test_survey_on_an_unknown_session_is_404(client):
    assert client.put("/sessions/nope/survey", json={"perceived_quality": 3}).status_code == 404


# -- period filter --------------------------------------------------------
def test_period_filter_scopes_the_overview(client):
    unfiltered = client.get("/analytics/overview").json()
    assert unfiltered["sessions"] == 2

    # The fixtures start in August 2026; a later window matches nothing.
    empty = client.get("/analytics/overview?since=2030-01-01").json()
    assert empty["sessions"] == 0
    assert empty["cost_usd"] is None  # no rows, so no cost - not zero
    assert empty["per_day"] == []


def test_period_filter_applies_to_every_scoped_endpoint(client):
    late = "?since=2030-01-01"
    assert client.get(f"/sessions{late}").json()["total"] == 0
    assert client.get(f"/analytics/projects{late}").json()[0]["sessions"] == 0
    project_id = next(
        p["id"] for p in client.get("/projects").json() if p["path"] == "/tmp/proj"
    )
    scoped = client.get(f"/analytics/projects/{project_id}{late}").json()
    assert scoped["sessions"] == 0
    assert scoped["coverage"]["with_linked_commit"] == 0


def test_project_directory_keeps_projects_outside_the_window(client):
    """Zeros, not a shorter list - the outer join must survive the filter."""
    assert len(client.get("/projects?since=2030-01-01").json()) == 2


def test_baseline_is_not_reshaped_by_the_period_filter(client, db):
    """It is a fixed pair of windows around adoption, not a slice of the data."""
    from datetime import datetime, timezone

    from app.models.entities import Project, ProjectBaseline

    project = db.scalar(select(Project).where(Project.path == "/tmp/proj"))
    db.add(
        ProjectBaseline(
            project_id=project.id,
            adoption_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            window_days=30,
            before_commits=10,
            before_active_days=5,
            after_commits=20,
            after_active_days=5,
        )
    )
    db.commit()

    for query in ("", "?since=2030-01-01"):
        baseline = client.get(f"/analytics/projects/{project.id}{query}").json()["baseline"]
        assert baseline["before_commits"] == 10
        assert baseline["after_commits"] == 20


def test_an_inverted_range_is_rejected(client):
    response = client.get("/analytics/overview?since=2026-08-10&until=2026-08-01")
    assert response.status_code == 422
    assert "after" in response.json()["detail"]


def test_a_malformed_date_is_rejected(client):
    assert client.get("/analytics/overview?since=ontem").status_code == 422


# -- conversation ---------------------------------------------------------
def test_messages_can_combine_several_types(client):
    """Reading a conversation needs prompts and replies in one ordered list."""
    session_id = client.seeded["with_ccusage"]
    prompts = client.get(
        f"/sessions/{session_id}/messages?message_type=human_prompt"
    ).json()
    replies = client.get(
        f"/sessions/{session_id}/messages?message_type=assistant_text"
    ).json()
    both = client.get(
        f"/sessions/{session_id}/messages"
        "?message_type=human_prompt&message_type=assistant_text"
    ).json()

    assert both["total"] == prompts["total"] + replies["total"]
    # Transcript order, not grouped by type.
    sequences = [m["sequence"] for m in both["items"]]
    assert sequences == sorted(sequences)
    assert {m["message_type"] for m in both["items"]} == {
        "human_prompt",
        "assistant_text",
    }


def test_message_content_is_returned_whole(client, db):
    """The text is the object of study; truncating it changes the evidence."""
    from app.models.entities import Message

    long_text = "x" * 5000
    row = db.scalar(select(Message).where(Message.message_type == "human_prompt"))
    row.content = long_text
    db.commit()

    body = client.get(
        f"/sessions/{client.seeded['with_ccusage']}/messages?message_type=human_prompt"
    ).json()
    assert body["items"][0]["content"] == long_text


def test_an_unknown_message_type_matches_nothing_rather_than_everything(client):
    body = client.get(
        f"/sessions/{client.seeded['with_ccusage']}/messages?message_type=inexistente"
    ).json()
    assert body["total"] == 0


def test_messages_without_a_type_filter_return_every_kind(client):
    session_id = client.seeded["with_ccusage"]
    everything = client.get(f"/sessions/{session_id}/messages?limit=500").json()
    prompts = client.get(
        f"/sessions/{session_id}/messages?message_type=human_prompt"
    ).json()
    assert everything["total"] > prompts["total"]
    assert "tool_result" in {m["message_type"] for m in everything["items"]}
