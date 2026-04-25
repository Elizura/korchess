"""FastAPI application for Korchess."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from repository.db import init_db
from services.full_analysis import init_engine_pool, shutdown_engine_pool
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3005",
        "http://127.0.0.1:3005",
        "https://korchess.com",
        "https://www.korchess.com",
        "http://korchess.com",
        "http://www.korchess.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    init_db()
    init_engine_pool()


@app.on_event("shutdown")
async def shutdown_event():
    shutdown_engine_pool()


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
