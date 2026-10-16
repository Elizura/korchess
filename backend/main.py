"""FastAPI application for Korchess."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from repository.db import clear_analysis_jobs, init_db
from repository.db_connection import close_pool, get_connection, init_pool
from services.full_analysis import init_engine_pool, shutdown_engine_pool
from lmdb_magic.reader import close as close_lmdb
from routers import (
    health,
    import_ as import_router,
    openings,
    games,
    analysis,
    auth as auth_router,
    insights as insights_router,
    analytics as analytics_router,
    quick_scan as quick_scan_router,
    profiles as profiles_router,
)

app = FastAPI(
    title="Korchess API",
    description="Chess opening performance analysis from Lichess games",
    version="1.0.0",
)

IS_PROD = os.environ.get("ENV", "development").lower() == "production"

CORS_ORIGINS = ["https://korchess.com", "https://www.korchess.com"]
if not IS_PROD:
    # Plain-http and local dev origins must never be trusted with credentials in production
    CORS_ORIGINS += [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3005",
        "http://127.0.0.1:3005",
        "http://korchess.com",
        "http://www.korchess.com",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    init_pool()
    init_db()
    # Deep analysis runs as an in-process task (single uvicorn process), so any
    # job row still here was orphaned by the previous process and would
    # otherwise report "processing" forever.
    with get_connection() as conn:
        cleared = clear_analysis_jobs(conn)
        conn.commit()
    if cleared:
        print(f"[Startup] Cleared {cleared} orphaned analysis job(s)")
    init_engine_pool()


@app.on_event("shutdown")
async def shutdown_event():
    shutdown_engine_pool()
    close_lmdb()
    close_pool()


app.include_router(health.router)
app.include_router(auth_router.router, prefix="/api/v1")
app.include_router(import_router.router, prefix="/api/v1/import")
app.include_router(openings.router, prefix="/api/v1")
app.include_router(games.router, prefix="/api/v1")
app.include_router(analysis.router, prefix="/api/v1/analysis")
app.include_router(insights_router.router, prefix="/api/v1")
app.include_router(analytics_router.router, prefix="/api/v1")
app.include_router(quick_scan_router.router, prefix="/api/v1")
app.include_router(profiles_router.router, prefix="/api/v1/profiles")
