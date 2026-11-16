"""Tests for the metrics a study leans on.

These guard against the two ways a dashboard silently lies: a number that looks
like a measurement but is an artefact of a join, and a caveat that quietly stops
being reported.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.entities import (
    ChangedFile,
    GitCommit,
    Project,
    ProjectBaseline,
    SessionCommitLink,
    SessionSurvey,
)
from app.models.entities import Session as SessionRow
from app.repositories.session_repository import SessionRepository
from app.services import analytics
from app.services.aggregator import SessionAggregator
from app.services.baseline import BaselineError, baseline_rates, compute_baseline

from .conftest import FIXED_NOW
from .test_discovery_and_aggregator import NullCcusage


def _store(db, transcript, name="session-basic"):
    report = SessionAggregator(NullCcusage(), enable_git=False).build(
        transcript(name), now=FIXED_NOW
    )
    SessionRepository(db).save(report)
    db.commit()
    return db.scalar(
        select(SessionRow).where(
            SessionRow.claude_session_id == report.session.claude_session_id
        )
    )


def _link_commit(db, session, *, insertions: int, commit_hash: str = "a" * 40):
    commit = GitCommit(
        project_id=session.project_id,
        commit_hash=commit_hash,
        message="feat: algo",
        committed_at=(session.ended_at or FIXED_NOW) + timedelta(minutes=5),
        files_changed=1,
        insertions=insertions,
        deletions=0,
        changed_files=["src/ProductForm.tsx"],
    )
    db.add(commit)
    db.flush()
    db.add(
        SessionCommitLink(
            session_id=session.id, git_commit_id=commit.id, likely_related=True
        )
    )
    db.commit()
    return commit


# -- attribution ----------------------------------------------------------
def test_attribution_is_a_ratio_of_tracked_edits_to_committed_lines(db, transcript):
    session = _store(db, transcript)
    _link_commit(db, session, insertions=100)

    result = analytics.attribution(db)
    # The fixture's structuredPatch records 2 insertions on ProductForm.tsx.
    assert result["ai_insertions"] == 2
    assert result["commit_insertions"] == 100
    assert result["attribution_ratio"] == pytest.approx(0.02)


def test_attribution_ratio_is_null_when_no_commit_added_lines(db, transcript):
    _store(db, transcript)
    result = analytics.attribution(db)
    assert result["linked_commits"] == 0
    # Null, not zero: with no denominator there is no ratio to report.
    assert result["attribution_ratio"] is None


def test_a_commit_shared_by_two_sessions_is_counted_once(db, transcript):
    """The fan-out that inflated effort by 40x when first measured by hand."""
    first = _store(db, transcript)
    second = _store(db, transcript, "session-subdirs-and-scratch")
    second.project_id = first.project_id
    db.commit()

    commit = _link_commit(db, first, insertions=100)
    db.add(
        SessionCommitLink(
            session_id=second.id, git_commit_id=commit.id, likely_related=True
        )
    )
    db.commit()

    result = analytics.attribution(db)
    assert result["linked_commits"] == 1
    assert result["commit_insertions"] == 100  # not 200


def test_attribution_ignores_commits_without_evidence(db, transcript):
    session = _store(db, transcript)
    commit = GitCommit(
        project_id=session.project_id,
        commit_hash="b" * 40,
        message="chore: unrelated",
        insertions=500,
        deletions=0,
    )
    db.add(commit)
    db.flush()
    db.add(
        SessionCommitLink(
            session_id=session.id, git_commit_id=commit.id, likely_related=False
        )
    )
    db.commit()
    assert analytics.attribution(db)["commit_insertions"] == 0


def test_attribution_excludes_scratch_files(db, transcript):
    """Scratch files under /tmp are working artefacts, not delivered code."""
    session = _store(db, transcript, "session-subdirs-and-scratch")
    _link_commit(db, session, insertions=50)
    outside = db.scalar(
        select(ChangedFile).where(ChangedFile.is_within_project.is_(False))
    )
    assert outside is not None  # the fixture has one
    result = analytics.attribution(db)
    assert result["ai_files"] == 1  # only the in-project file


# -- coverage -------------------------------------------------------------
def test_coverage_separates_the_effort_base_from_the_delivery_base(db, transcript):
    session = _store(db, transcript)
    _store(db, transcript, "session-injected-user")
    _link_commit(db, session, insertions=10)

    result = analytics.coverage(db)
    assert result["sessions"] == 2
    assert result["with_prompts"] == 2
    # Only one session can support a claim about delivered code.
    assert result["with_linked_commit"] == 1
    assert result["with_analysis"] == 0
    assert result["with_survey"] == 0


def test_coverage_counts_survey_responses(db, transcript):
    session = _store(db, transcript)
    db.add(SessionSurvey(session_id=session.id, perceived_quality=4))
    db.commit()
    assert analytics.coverage(db)["with_survey"] == 1


# -- validity -------------------------------------------------------------
def test_validity_reports_tool_version_spread(db, transcript):
    session = _store(db, transcript)
    session.claude_code_versions = ["2.1.250", "2.1.251"]
    db.commit()
    result = analytics.validity_threats(db)
    assert result["claude_code_versions"] == ["2.1.250", "2.1.251"]


def test_validity_flags_sessions_that_edited_through_the_shell(db, transcript):
    session = _store(db, transcript)
    session.files_modified = 0
    session.tool_call_count = 500
    db.commit()
    assert analytics.validity_threats(db)["sessions_editing_via_shell"] == 1


def test_a_quiet_session_is_not_flagged_as_shell_editing(db, transcript):
    """Few tool calls and no file edits is a conversation, not a hidden write."""
    session = _store(db, transcript)
    session.files_modified = 0
    session.tool_call_count = 2
    db.commit()
    assert analytics.validity_threats(db)["sessions_editing_via_shell"] == 0


def test_validity_flags_sessions_ccusage_attributed_elsewhere(db, transcript):
    session = _store(db, transcript)  # NullCcusage: transcript usage only
    assert session.assistant_message_count > 0
    assert analytics.validity_threats(db)["sessions_likely_resumed"] == 1


# -- effort shape ---------------------------------------------------------
def test_effort_shape_reports_distribution_not_just_the_sum(db, transcript):
    session = _store(db, transcript)
    other = _store(db, transcript, "session-injected-user")
    session.active_turn_seconds = 3600.0
    other.active_turn_seconds = 600.0
    db.commit()

    result = analytics.effort_shape(db)
    assert result["sessions_measured"] == 2
    assert result["median_session_seconds"] == pytest.approx(2100.0)
    assert result["longest_session_seconds"] == pytest.approx(3600.0)
    # The busiest fifth (at this size, one session) holds most of the time.
    assert result["top_quintile_share"] == pytest.approx(3600 / 4200)


# -- survey ---------------------------------------------------------------
def test_survey_aggregate_reports_the_response_count_with_the_means(db, transcript):
    first = _store(db, transcript)
    second = _store(db, transcript, "session-injected-user")
    db.add(SessionSurvey(session_id=first.id, perceived_quality=5, rework_reported=True))
    db.add(SessionSurvey(session_id=second.id, perceived_quality=3, rework_reported=False))
    db.commit()

    result = analytics.survey_aggregate(db)
    assert result["responses"] == 2
    assert result["mean_perceived_quality"] == pytest.approx(4.0)
    assert result["rework_reported_count"] == 1
    # A skipped question is not a neutral answer.
    assert result["mean_trust_in_output"] is None


# -- comparison -----------------------------------------------------------
def test_project_comparison_does_not_let_commits_inflate_effort(db, transcript):
    """Joining sessions to commits in one query multiplies the effort totals."""
    session = _store(db, transcript)
    session.active_turn_seconds = 3600.0
    db.commit()
    for index in range(5):
        _link_commit(db, session, insertions=10, commit_hash=f"{index}" * 40)

    row = next(r for r in analytics.project_comparison(db) if r["sessions"] == 1)
    assert row["active_turn_hours"] == pytest.approx(1.0)  # not 5.0
    assert row["linked_commits"] == 5


def test_project_comparison_normalises_by_effort(db, transcript):
    session = _store(db, transcript)
    session.active_turn_seconds = 7200.0
    db.commit()
    row = next(r for r in analytics.project_comparison(db) if r["sessions"] == 1)
    assert row["prompts_per_hour"] == pytest.approx(session.human_prompt_count / 2)
    assert row["commit_coverage"] == 0.0


# -- baseline -------------------------------------------------------------
def test_baseline_requires_a_git_repository(db, transcript):
    session = _store(db, transcript)
    project = db.get(Project, session.project_id)
    with pytest.raises(BaselineError, match="not a git repository"):
        compute_baseline(db, project)


def test_baseline_rates_survive_windows_of_unequal_length():
    row = ProjectBaseline(
        project_id=1,
        adoption_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        window_days=90,
        before_commits=30,
        before_active_days=10,
        after_commits=9,
        after_active_days=3,
    )
    rates = baseline_rates(row)
    assert rates["before_commits_per_active_day"] == pytest.approx(3.0)
    assert rates["after_commits_per_active_day"] == pytest.approx(3.0)


def test_baseline_rate_is_null_when_there_were_no_active_days():
    row = ProjectBaseline(
        project_id=1,
        adoption_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        window_days=90,
        before_commits=0,
        before_active_days=0,
        after_commits=5,
        after_active_days=2,
    )
    rates = baseline_rates(row)
    # No baseline is a real answer; 0.0 would look like "they shipped nothing".
    assert rates["before_commits_per_active_day"] is None


# -- scoping --------------------------------------------------------------
def test_every_research_metric_can_be_scoped_to_one_project(db, transcript):
    first = _store(db, transcript)
    second = _store(db, transcript, "session-subdirs-and-scratch")
    assert first.project_id != second.project_id
    _link_commit(db, first, insertions=100)

    scoped = analytics.attribution(db, project_id=first.project_id)
    other = analytics.attribution(db, project_id=second.project_id)
    assert scoped["commit_insertions"] == 100
    assert other["commit_insertions"] == 0
    assert analytics.coverage(db, project_id=second.project_id)["sessions"] == 1


# -- period filtering -----------------------------------------------------
def _at(db, session, when: datetime) -> None:
    session.started_at = when
    db.commit()


def test_period_scopes_every_metric_consistently(db, transcript):
    """Filtered numbers must agree with each other, not just be smaller."""
    early = _store(db, transcript)
    late = _store(db, transcript, "session-injected-user")
    _at(db, early, datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc))
    _at(db, late, datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc))

    window = analytics.Period(since=date(2026, 8, 15), until=date(2026, 8, 31))
    assert analytics.overview(db, period=window)["sessions"] == 1
    assert analytics.coverage(db, period=window)["sessions"] == 1
    assert analytics.effort_shape(db, period=window)["sessions_measured"] <= 1
    assert analytics.overview(db)["sessions"] == 2


def test_until_includes_the_whole_final_day(db, transcript):
    """A reader asking for 20/08 means all of it, not midnight."""
    session = _store(db, transcript)
    _at(db, session, datetime(2026, 8, 20, 23, 30, tzinfo=timezone.utc))
    window = analytics.Period(since=date(2026, 8, 20), until=date(2026, 8, 20))
    assert analytics.overview(db, period=window)["sessions"] == 1


def test_a_session_without_a_start_time_falls_outside_every_range(db, transcript):
    session = _store(db, transcript)
    session.started_at = None
    db.commit()
    assert analytics.overview(db)["sessions"] == 1
    window = analytics.Period(since=date(2020, 1, 1), until=date(2030, 1, 1))
    assert analytics.overview(db, period=window)["sessions"] == 0


def test_attribution_respects_the_period(db, transcript):
    session = _store(db, transcript)
    _at(db, session, datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc))
    _link_commit(db, session, insertions=100)

    inside = analytics.Period(since=date(2026, 7, 1), until=date(2026, 8, 31))
    outside = analytics.Period(since=date(2026, 9, 1), until=date(2026, 9, 30))
    assert analytics.attribution(db, period=inside)["commit_insertions"] == 100
    assert analytics.attribution(db, period=outside)["commit_insertions"] == 0
    # An empty window has no denominator, so there is no ratio to report.
    assert analytics.attribution(db, period=outside)["attribution_ratio"] is None


def test_project_list_keeps_projects_with_no_session_in_range(db, transcript):
    """The directory must not lose a project because the window is narrow.

    Putting the date predicate in WHERE would turn the outer join into an inner
    join and make the project disappear instead of showing zeros.
    """
    session = _store(db, transcript)
    _at(db, session, datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc))

    rows = analytics.project_metrics(
        db, period=analytics.Period(since=date(2026, 9, 1))
    )
    assert len(rows) == 1
    assert rows[0]["sessions"] == 0
    assert rows[0]["cost_usd"] is None


def test_open_period_is_the_same_as_no_filter(db, transcript):
    _store(db, transcript)
    assert analytics.ALL_TIME.is_open
    assert (
        analytics.overview(db, period=analytics.ALL_TIME)["sessions"]
        == analytics.overview(db)["sessions"]
    )
