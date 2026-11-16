"""FastAPI application.

Read-only over the collected data, with one exception: ``POST
/sessions/{id}/analyze``, reserved for the LLM analyzer. Collection itself runs
through the CLI (``python -m app.cli.main sync``), so the API never touches the
developer's local Claude Code data.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routers import analytics, projects, sessions
from app.config import get_settings
from app.db.base import get_engine

DESCRIPTION = """
Observability for AI-assisted software development.

**Observed vs inferred.** Every endpoint returns data collected from a source
(Claude Code transcripts, ccusage, git) except `/sessions/{id}/analysis`, which
returns LLM output and is marked `inferred: true` with a confidence score.

**Token and cost figures carry their source.** ccusage and the transcript
describe the same tokens and disagree by construction, so they are never summed;
each session contributes one row, and `usage_source` says which.
"""

app = FastAPI(
    title="AI Development Analytics",
    description=DESCRIPTION,
    version="0.1.0",
    openapi_tags=[
        {"name": "projects", "description": "Projects detected from session cwds."},
        {"name": "sessions", "description": "Claude Code sessions and their events."},
        {"name": "analytics", "description": "Aggregates. Never sums usage sources."},
        {"name": "system", "description": "Health and metadata."},
    ],
)

# The dashboard (Etapa 10) runs on a different origin in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(sessions.router)
app.include_router(analytics.router)


@app.get("/health", tags=["system"], summary="Liveness and database reachability")
def health() -> dict[str, object]:
    settings = get_settings()
    database_ok = True
    error: str | None = None
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        database_ok = False
        error = f"{type(exc).__name__}: {exc}"
    return {
        "status": "ok" if database_ok else "degraded",
        "database": database_ok,
        "database_error": error,
        "ai_analysis_enabled": settings.ai_analysis_enabled,
    }
