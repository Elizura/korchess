"""FastAPI application for Korchess."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from db import init_db
from routers import health, import_ as import_router, openings, games, analysis, eval as eval_router, auth as auth_router

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


app.include_router(health.router)
app.include_router(auth_router.router, prefix="/api")
app.include_router(import_router.router, prefix="/api/import")
app.include_router(openings.router, prefix="/api")
app.include_router(games.router, prefix="/api")
app.include_router(analysis.router, prefix="/api/analysis")
app.include_router(eval_router.router, prefix="/api")
