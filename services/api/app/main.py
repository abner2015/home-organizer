"""FastAPI application entry point."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import assets as assets_v1
from app.api.v1 import auth as auth_v1
from app.api.v1 import files as files_v1
from app.api.v1 import homes as homes_v1
from app.api.v1 import items as items_v1
from app.api.v1 import recommendations as recommendations_v1
from app.api.v1 import search as search_v1
from app.api.v1 import structure as structure_v1
from app.api.v1 import uploads as uploads_v1
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.health import check_database, check_redis
from app.core.logging import configure_logging, get_logger
from app.core.request_id import RequestIdMiddleware
from app.db.session import dispose_engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging on startup; dispose DB engine on shutdown."""
    configure_logging(settings.log_level)
    logger.info(
        "application.startup",
        env=settings.app_env,
        version=settings.app_version,
        debug=settings.debug,
    )
    yield
    logger.info("application.shutdown")
    await dispose_engine()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI 家庭收纳管家 Backend API",
    lifespan=lifespan,
    debug=settings.debug,
)

# Middleware (request-id FIRST so every subsequent handler sees it)
app.add_middleware(RequestIdMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Exception handlers
register_exception_handlers(app)

# Routers (versioned)
app.include_router(auth_v1.router, prefix="/api/v1")
app.include_router(assets_v1.router, prefix="/api/v1")
# Registered unconditionally; it self-404s unless storage_backend == "local".
app.include_router(files_v1.router, prefix="/api/v1")
app.include_router(homes_v1.router, prefix="/api/v1")
app.include_router(homes_v1.rooms_router, prefix="/api/v1")
app.include_router(items_v1.router, prefix="/api/v1")
app.include_router(recommendations_v1.router, prefix="/api/v1")
app.include_router(search_v1.router, prefix="/api/v1")
# Storage-structure writes. ``/homes`` and ``/rooms`` each carry two routers
# (reads in homes_v1, writes here); FastAPI matches on the full path *and*
# method, so ``POST /rooms/{id}/storage-units`` does not shadow the GET.
app.include_router(structure_v1.router, prefix="/api/v1")
app.include_router(structure_v1.rooms_router, prefix="/api/v1")
app.include_router(structure_v1.units_router, prefix="/api/v1")
app.include_router(structure_v1.sections_router, prefix="/api/v1")
app.include_router(structure_v1.slots_router, prefix="/api/v1")
app.include_router(uploads_v1.router, prefix="/api/v1")


@app.get("/health", tags=["meta"])
async def health() -> dict[str, object]:
    """Liveness + readiness check.

    Returns 200 even when DB/Redis are down; reports per-dependency status.
    The frontend / load balancer can interpret `status: degraded` as
    "app is up but a dependency is unreachable".
    """
    db_ok, db_error = await check_database()
    redis_ok, redis_error = await check_redis()
    overall = "ok" if (db_ok and redis_ok) else "degraded"
    return {
        "status": overall,
        "app": {
            "name": settings.app_name,
            "version": settings.app_version,
            "env": settings.app_env,
        },
        "checks": {
            "database": {"ok": db_ok, "error": db_error},
            "redis": {"ok": redis_ok, "error": redis_error},
        },
    }


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    """Root endpoint with service info."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/health",
    }
