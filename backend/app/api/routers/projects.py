from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DbDep, PeriodDep, ProjectDep
from app.models.entities import Project
from app.schemas.api import ProjectMetrics, ProjectOut
from app.services import analytics

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectMetrics], summary="List projects")
def list_projects(db: DbDep, period: PeriodDep) -> list[ProjectMetrics]:
    """Projects with their aggregate metrics, most expensive first.

    Token and cost figures come from one source per session; see
    ``app/services/analytics.py`` for why they are never summed across sources.
    """
    return [
        ProjectMetrics(**row)
        for row in analytics.project_metrics(db, period=period)
    ]


@router.get("/{project_id}", response_model=ProjectOut, summary="Get one project")
def get_project(project: ProjectDep) -> Project:
    return project
