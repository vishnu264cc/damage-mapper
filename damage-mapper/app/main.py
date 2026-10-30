from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.api.routes.ai import router as ai_router
from app.api.routes.auth import router as auth_router
from app.api.routes.geospatial import router as geospatial_router
from app.api.routes.health import router as health_router
from app.api.routes.incidents import (
    private_upload_router,
    router as incidents_router,
)
from app.api.routes.prioritization import router as prioritization_router
from app.core.config import STATIC, UPLOADS, get_settings
from app.models import (  # noqa: F401 - register every model with SQLAlchemy metadata
    AuditLog,
    AuthBootstrapClaim,
    AuthRateLimit,
    AuthSession,
    DuplicateGroup,
    Incident,
    IncidentImage,
    ResponderAssignment,
    ResponderLocation,
    User,
    VerificationHistory,
)
from app.services.uploads import StorageUnavailableError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if get_settings().storage_backend == "local":
        UPLOADS.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Damage Mapper API",
    description=(
        "AI-assisted disaster damage assessment and response prioritization. "
        "Machine-generated assessments require authorized human verification."
    ),
    version="2.0.0",
    lifespan=lifespan,
)
settings = get_settings()
if settings.trusted_hosts:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.trusted_hosts,
    )
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(ai_router)
app.include_router(incidents_router)
app.include_router(private_upload_router)
app.include_router(prioritization_router)
app.include_router(geospatial_router)


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/healthz", include_in_schema=False)
def render_health_check(session: Session = Depends(get_db)) -> Response:
    try:
        session.execute(select(Incident.id).limit(1))
    except SQLAlchemyError:
        logger.exception("Render health check could not reach the database")
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "database": "unavailable"},
        )
    return JSONResponse(content={"status": "ok", "database": "ok"})


@app.exception_handler(IntegrityError)
async def handle_integrity_error(_: Request, error: IntegrityError) -> JSONResponse:
    logger.info("Database constraint rejected a request: %s", error.orig)
    return JSONResponse(
        status_code=409,
        content={"detail": "The request conflicts with an existing database record"},
    )


@app.exception_handler(SQLAlchemyError)
async def handle_database_error(_: Request, error: SQLAlchemyError) -> JSONResponse:
    logger.exception("Database request failed", exc_info=error)
    return JSONResponse(
        status_code=503,
        content={"detail": "The database operation could not be completed"},
    )


@app.exception_handler(StorageUnavailableError)
async def handle_storage_error(
    _: Request, error: StorageUnavailableError
) -> JSONResponse:
    logger.error("Image storage operation failed: %s", error)
    return JSONResponse(
        status_code=503,
        content={"detail": "Image storage is temporarily unavailable"},
    )
