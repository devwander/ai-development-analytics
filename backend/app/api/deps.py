"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings, get_settings
from app.db.base import get_session_factory
from app.models.entities import Project
from app.models.entities import Session as SessionRow
from app.services.analytics import Period


def get_db() -> Iterator[DbSession]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


DbDep = Annotated[DbSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_session_or_404(
    db: DbDep,
    session_id: Annotated[
        str,
        Path(
            description=(
                "The Claude Code session UUID, or the numeric database id. "
                "The UUID is what appears in the transcript filename."
            )
        ),
    ],
) -> SessionRow:
    """Accept either identifier.

    Callers coming from the CLI or a transcript know the UUID; callers coming
    from a list endpoint have the numeric id. Refusing one of them would be
    gratuitous.
    """
    row = db.scalar(
        select(SessionRow).where(SessionRow.claude_session_id == session_id)
    )
    if row is None and session_id.isdigit():
        row = db.get(SessionRow, int(session_id))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"session not found: {session_id}",
        )
    return row


SessionDep = Annotated[SessionRow, Depends(get_session_or_404)]


def get_project_or_404(db: DbDep, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"project not found: {project_id}",
        )
    return project


ProjectDep = Annotated[Project, Depends(get_project_or_404)]

LimitDep = Annotated[int, Query(ge=1, le=500)]
OffsetDep = Annotated[int, Query(ge=0)]


def get_period(
    since: Annotated[
        date | None,
        Query(description="Início do período (YYYY-MM-DD), inclusivo."),
    ] = None,
    until: Annotated[
        date | None,
        Query(description="Fim do período (YYYY-MM-DD), inclusivo."),
    ] = None,
) -> Period:
    """The date range every scoped endpoint shares.

    Filtering is on the session's start time. A session with no recorded start
    time falls outside any range - it cannot be placed in time, and keeping it
    would make filtered totals disagree with the filtered session list.
    """
    if since and until and since > until:
        raise HTTPException(
            # 422 spelled out: Starlette renamed the constant and the old
            # name now warns on every use.
            status_code=422,
            detail=f"since ({since}) is after until ({until})",
        )
    return Period(since=since, until=until)


PeriodDep = Annotated[Period, Depends(get_period)]
