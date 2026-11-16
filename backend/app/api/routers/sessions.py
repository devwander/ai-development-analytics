from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Select, case, func, select

from app.api.deps import DbDep, LimitDep, OffsetDep, PeriodDep, SessionDep, SettingsDep
from app.models.entities import (
    AIAnalysis,
    SessionSurvey,
    ChangedFile,
    GitCommit,
    Message,
    Project,
    SessionCommitLink,
    ToolCall,
    Usage,
)
from app.models.entities import Session as SessionRow
from app.schemas.api import (
    AnalysisOut,
    SourceOut,
    SurveyIn,
    SurveyOut,
    ChangedFileOut,
    CommitAssociation,
    GitResponse,
    MessageOut,
    Page,
    SessionDetail,
    SessionExportOut,
    SessionSummary,
    ToolCallOut,
    ToolsResponse,
    ToolStatOut,
    UsageOut,
    UsageResponse,
)
from app.analyzers.service import AnalysisDisabled, SessionAnalyzer
from app.llm.base import LLMNotConfigured
from app.services.analytics import SOURCE_PRIORITY, preferred_usage

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _count(db, query: Select) -> int:
    return db.scalar(select(func.count()).select_from(query.subquery())) or 0


def _tool_stats(db, session_id: int) -> list[ToolStatOut]:
    return [
        ToolStatOut(tool_name=row.tool_name, calls=row.calls, errors=int(row.errors or 0))
        for row in db.execute(
            select(
                ToolCall.tool_name,
                func.count().label("calls"),
                func.sum(case((ToolCall.is_error.is_(True), 1), else_=0)).label("errors"),
            )
            .where(ToolCall.session_id == session_id)
            .group_by(ToolCall.tool_name)
            .order_by(func.count().desc())
        ).all()
    ]


def _summary(row: SessionRow, project_name, usage_row) -> SessionSummary:
    summary = SessionSummary.model_validate(row)
    summary.project_name = project_name
    if usage_row is not None:
        summary.total_tokens = usage_row.total_tokens
        summary.cost_usd = usage_row.cost_usd
        summary.usage_source = usage_row.source
    return summary


@router.get("", response_model=Page[SessionSummary], summary="List sessions")
def list_sessions(
    db: DbDep,
    period: PeriodDep,
    limit: LimitDep = 50,
    offset: OffsetDep = 0,
    project_id: Annotated[int | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(description="Matches title and prompts")] = None,
) -> Page[SessionSummary]:
    usage = preferred_usage()
    query = (
        select(SessionRow, Project.name, usage)
        .outerjoin(Project, Project.id == SessionRow.project_id)
        .outerjoin(usage, usage.c.session_id == SessionRow.id)
    )
    if project_id is not None:
        query = query.where(SessionRow.project_id == project_id)
    start, end = period.start_at(), period.end_at()
    if start is not None:
        query = query.where(SessionRow.started_at >= start)
    if end is not None:
        query = query.where(SessionRow.started_at < end)
    if status_filter:
        query = query.where(SessionRow.status == status_filter)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            SessionRow.title.ilike(pattern)
            | SessionRow.first_prompt.ilike(pattern)
            | SessionRow.last_prompt.ilike(pattern)
        )

    total = _count(db, query)
    rows = db.execute(
        query.order_by(SessionRow.started_at.desc().nulls_last())
        .limit(limit)
        .offset(offset)
    ).all()

    analysed = {
        session_id
        for (session_id,) in db.execute(
            select(AIAnalysis.session_id).distinct()
        ).all()
    }
    items = []
    for row in rows:
        summary = _summary(row[0], row[1], row)
        summary.has_analysis = row[0].id in analysed
        items.append(summary)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{session_id}", response_model=SessionDetail, summary="Get one session")
def get_session(db: DbDep, session: SessionDep) -> SessionDetail:
    usage = preferred_usage()
    usage_row = db.execute(
        select(usage).where(usage.c.session_id == session.id)
    ).one_or_none()
    project = db.get(Project, session.project_id) if session.project_id else None

    detail = SessionDetail.model_validate(session)
    detail.project_name = project.name if project else None
    detail.project_path = project.path if project else None
    if usage_row is not None:
        detail.total_tokens = usage_row.total_tokens
        detail.cost_usd = usage_row.cost_usd
        detail.usage_source = usage_row.source
    detail.has_analysis = bool(
        db.scalar(select(func.count(AIAnalysis.id)).where(AIAnalysis.session_id == session.id))
    )
    detail.files = [
        ChangedFileOut.model_validate(f)
        for f in db.scalars(
            select(ChangedFile)
            .where(ChangedFile.session_id == session.id)
            .order_by(ChangedFile.file_path)
        ).all()
    ]
    if session.source_file is not None:
        from pathlib import Path as _Path

        source = SourceOut.model_validate(session.source_file)
        source.auditable = any(
            candidate and _Path(candidate).exists()
            for candidate in (
                session.source_file.transcript_path,
                session.source_file.archive_path,
            )
        )
        detail.source = source
    detail.has_survey = session.survey is not None
    return detail


@router.get(
    "/{session_id}/messages",
    response_model=Page[MessageOut],
    summary="Messages of a session",
)
def get_messages(
    db: DbDep,
    session: SessionDep,
    limit: LimitDep = 100,
    offset: OffsetDep = 0,
    message_type: Annotated[
        list[str] | None,
        Query(
            description=(
                "Repeatable. One of: human_prompt, tool_result, assistant_text, "
                "assistant_tool_use, compact_summary, injected_user, system. "
                "Filter on human_prompt alone to get what the developer actually "
                "typed; pass human_prompt and assistant_text together to read the "
                "conversation."
            )
        ),
    ] = None,
) -> Page[MessageOut]:
    """Messages of a session, in transcript order.

    Content is returned whole. A prompt here reaches 27,822 characters and an
    assistant reply 19,275 - truncating either would quietly change what a
    reader thinks was said, which matters when the text is the object of study.
    """
    query = select(Message).where(Message.session_id == session.id)
    if message_type:
        query = query.where(Message.message_type.in_(message_type))
    total = _count(db, query)
    rows = db.scalars(
        query.order_by(Message.sequence).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[MessageOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{session_id}/tools", response_model=ToolsResponse, summary="Tool usage"
)
def get_tools(
    db: DbDep,
    session: SessionDep,
    limit: LimitDep = 100,
    offset: OffsetDep = 0,
    tool_name: Annotated[str | None, Query()] = None,
) -> ToolsResponse:
    stats = _tool_stats(db, session.id)
    query = select(ToolCall).where(ToolCall.session_id == session.id)
    if tool_name:
        query = query.where(ToolCall.tool_name == tool_name)
    total = _count(db, query)
    calls = db.scalars(
        query.order_by(ToolCall.sequence).limit(limit).offset(offset)
    ).all()
    return ToolsResponse(
        stats=stats,
        calls=Page(
            items=[ToolCallOut.model_validate(c) for c in calls],
            total=total,
            limit=limit,
            offset=offset,
        ),
    )


@router.get(
    "/{session_id}/usage", response_model=UsageResponse, summary="Tokens and cost"
)
def get_usage(db: DbDep, session: SessionDep) -> UsageResponse:
    """Every source's figures, plus the one a caller should use.

    ``totals`` must never be summed: ccusage and the transcript describe the
    same tokens (ADR 0002). ``preferred`` picks one - ccusage when it has data
    for this session, the transcript otherwise.
    """
    rows = db.scalars(
        select(Usage).where(Usage.session_id == session.id)
    ).all()
    totals = [UsageOut.model_validate(u) for u in rows if u.model is None]
    by_model = [UsageOut.model_validate(u) for u in rows if u.model is not None]
    preferred = None
    for source in SOURCE_PRIORITY:
        preferred = next((t for t in totals if t.source == source), None)
        if preferred is not None:
            break
    return UsageResponse(preferred=preferred, totals=totals, by_model=by_model)


@router.get(
    "/{session_id}/git", response_model=GitResponse, summary="Associated commits"
)
def get_git(db: DbDep, session: SessionDep) -> GitResponse:
    """Commits associated with this session, each with its evidence.

    ``likely_related`` is an association, not a statement that the AI authored
    the commit. The reasons that produced it are always included.
    """
    project = db.get(Project, session.project_id) if session.project_id else None
    rows = db.execute(
        select(SessionCommitLink, GitCommit)
        .join(GitCommit, GitCommit.id == SessionCommitLink.git_commit_id)
        .where(SessionCommitLink.session_id == session.id)
        .order_by(
            SessionCommitLink.likely_related.desc(), GitCommit.committed_at.desc()
        )
    ).all()
    associations = [
        CommitAssociation(
            commit=commit,
            likely_related=link.likely_related,
            overlap_count=link.overlap_count,
            overlapping_files=link.overlapping_files,
            minutes_after_session_end=link.minutes_after_session_end,
            within_session_window=link.within_session_window,
            reasons=link.reasons,
        )
        for link, commit in rows
    ]
    return GitResponse(
        repository_url=project.repository_url if project else None,
        is_git_repository=project.is_git_repository if project else None,
        branches_during_session=session.git_branches or [],
        associations=associations,
        likely_related_count=sum(1 for a in associations if a.likely_related),
    )


@router.get(
    "/{session_id}/analysis",
    response_model=AnalysisOut,
    summary="LLM analysis (inferred data)",
    responses={404: {"description": "No analysis has been produced for this session"}},
)
def get_analysis(db: DbDep, session: SessionDep) -> AIAnalysis:
    analysis = db.scalar(
        select(AIAnalysis)
        .where(AIAnalysis.session_id == session.id)
        .order_by(AIAnalysis.created_at.desc())
    )
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no analysis for this session",
        )
    return analysis


@router.post(
    "/{session_id}/analyze",
    response_model=AnalysisOut,
    status_code=status.HTTP_201_CREATED,
    summary="Run the LLM analysis (inferred data)",
    responses={
        502: {"description": "The provider failed, or returned an invalid result"},
        503: {"description": "AI_ANALYSIS_ENABLED is false, or no provider is configured"},
    },
)
def analyze(db: DbDep, session: SessionDep, settings: SettingsDep) -> AIAnalysis:
    """Analyse one session and store the result in ``ai_analyses``.

    Sends a bounded, redacted digest to the configured provider - never the raw
    transcript. Disabled by default; objective collection does not depend on it.

    A provider failure returns an error. It never stores a fabricated analysis:
    an absent judgement is a correct answer, an invented one is not.
    """
    analyzer = SessionAnalyzer(db, settings=settings)
    try:
        outcome = analyzer.analyze(session)
    except AnalysisDisabled as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except LLMNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    if not outcome.ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=outcome.error
        )
    db.commit()
    return outcome.analysis


@router.get(
    "/{session_id}/survey",
    response_model=SurveyOut,
    summary="Self-reported perception (declared data)",
    responses={404: {"description": "No survey response for this session"}},
)
def get_survey(db: DbDep, session: SessionDep) -> SessionSurvey:
    """What the developer said about the session.

    Perceived quality cannot be read out of a transcript, so it is neither
    observed nor inferred - it is declared, and the response model marks it.
    """
    survey = db.scalar(
        select(SessionSurvey).where(SessionSurvey.session_id == session.id)
    )
    if survey is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no survey response for this session",
        )
    return survey


@router.put(
    "/{session_id}/survey",
    response_model=SurveyOut,
    summary="Record or replace the survey response",
)
def put_survey(db: DbDep, session: SessionDep, payload: SurveyIn) -> SessionSurvey:
    """One response per session, replaced on re-submission.

    PUT rather than POST: a second answer for the same session corrects the
    first rather than adding a second opinion from the same person.
    """
    survey = db.scalar(
        select(SessionSurvey).where(SessionSurvey.session_id == session.id)
    )
    if survey is None:
        survey = SessionSurvey(session_id=session.id)
        db.add(survey)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(survey, field, value)
    db.commit()
    db.refresh(survey)
    return survey


@router.get(
    "/{session_id}/export",
    response_model=SessionExportOut,
    summary="Full session export (every collected field, one document)",
)
def export_session(db: DbDep, session: SessionDep) -> SessionExportOut:
    """Everything the API knows about one session, in a single document.

    Backs the "download session" selector in the dashboard. Unlike the
    listing endpoints above, nothing here is paginated.
    """
    messages = [
        MessageOut.model_validate(m)
        for m in db.scalars(
            select(Message)
            .where(Message.session_id == session.id)
            .order_by(Message.sequence)
        ).all()
    ]
    tool_calls = [
        ToolCallOut.model_validate(c)
        for c in db.scalars(
            select(ToolCall)
            .where(ToolCall.session_id == session.id)
            .order_by(ToolCall.sequence)
        ).all()
    ]
    analysis = db.scalar(
        select(AIAnalysis)
        .where(AIAnalysis.session_id == session.id)
        .order_by(AIAnalysis.created_at.desc())
    )
    survey = db.scalar(select(SessionSurvey).where(SessionSurvey.session_id == session.id))

    return SessionExportOut(
        generated_at=datetime.now(timezone.utc),
        session=get_session(db, session),
        messages=messages,
        tool_calls=tool_calls,
        tool_stats=_tool_stats(db, session.id),
        usage=get_usage(db, session),
        git=get_git(db, session),
        analysis=AnalysisOut.model_validate(analysis) if analysis else None,
        survey=SurveyOut.model_validate(survey) if survey else None,
    )
