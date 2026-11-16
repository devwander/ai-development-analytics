from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DbDep, PeriodDep, ProjectDep
from app.models.entities import ProjectBaseline
from app.schemas.api import (
    BaselineOut,
    OverviewOut,
    ProjectAnalyticsOut,
    ProjectComparisonRow,
    ToolStatOut,
)
from app.services import analytics as analytics_service
from app.services.baseline import baseline_rates

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview", response_model=OverviewOut, summary="Global overview")
def overview(db: DbDep, period: PeriodDep) -> OverviewOut:
    """Aggregate metrics across every synced session.

    ``usage_sources`` reports how many sessions each source supplied a total
    for, so a reader can see the composition of ``total_tokens`` and
    ``cost_usd`` rather than trusting a single opaque number.

    ``coverage``, ``attribution`` and ``validity`` qualify the rest: they say
    how much of the sample supports a delivery claim, how much committed code
    can be traced to a tracked AI edit, and what properties of the dataset a
    reader has to keep in mind.

    ``task_types`` is inferred and stays empty until the LLM analyzer runs;
    ``survey`` is self-declared and stays empty until responses are recorded.
    """
    payload = analytics_service.overview(db, period=period)
    return OverviewOut(
        **payload,
        attribution=analytics_service.attribution(db, period=period),
        coverage=analytics_service.coverage(db, period=period),
        validity=analytics_service.validity_threats(db, period=period),
        effort_shape=analytics_service.effort_shape(db, period=period),
        survey=analytics_service.survey_aggregate(db, period=period),
    )


@router.get(
    "/projects",
    response_model=list[ProjectComparisonRow],
    summary="Projects side by side, normalised by effort",
)
def compare_projects(db: DbDep, period: PeriodDep) -> list[ProjectComparisonRow]:
    """Totals cannot be compared across projects of different sizes; rates can."""
    return [
        ProjectComparisonRow(**row)
        for row in analytics_service.project_comparison(db, period=period)
    ]


@router.get(
    "/projects/{project_id}",
    response_model=ProjectAnalyticsOut,
    summary="Overview scoped to one project",
)
def project_overview(
    db: DbDep, project: ProjectDep, period: PeriodDep
) -> ProjectAnalyticsOut:
    """Scoped to one project, and optionally to a date range.

    The baseline is deliberately **not** filtered: it is a fixed pair of windows
    around the adoption date, and re-slicing it by an arbitrary range would
    silently answer a different question than the one it was computed for.
    """
    payload = analytics_service.overview(db, project_id=project.id, period=period)
    stored_baseline = db.scalar(
        select(ProjectBaseline).where(ProjectBaseline.project_id == project.id)
    )
    baseline = None
    if stored_baseline is not None:
        baseline = BaselineOut.model_validate(stored_baseline)
        baseline.rates = baseline_rates(stored_baseline)

    return ProjectAnalyticsOut(
        **payload,
        project=project,
        tools=[
            ToolStatOut(**row)
            for row in analytics_service.tool_breakdown(db, project_id=project.id, period=period)
        ],
        attribution=analytics_service.attribution(db, project_id=project.id, period=period),
        coverage=analytics_service.coverage(db, project_id=project.id, period=period),
        validity=analytics_service.validity_threats(db, project_id=project.id, period=period),
        effort_shape=analytics_service.effort_shape(db, project_id=project.id, period=period),
        survey=analytics_service.survey_aggregate(db, project_id=project.id, period=period),
        baseline=baseline,
    )
