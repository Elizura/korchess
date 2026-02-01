"""FastAPI application for Openingscope."""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from db import get_connection, init_db, upsert_game, get_openings_stats, upsert_import_status, get_import_status, get_games_by_opening
from lichess import fetch_lichess_pgn, parse_pgn_games, LichessAPIError

app = FastAPI(
    title="Openingscope API",
    description="Chess opening performance analysis from Lichess games",
    version="1.0.0",
)

# CORS configuration for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://frontend:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ImportRequest(BaseModel):
    """Request body for importing Lichess games."""
    username: str = Field(..., min_length=1, max_length=50)
    max_games: int = Field(default=200, ge=1, le=500)


class ImportResponse(BaseModel):
    """Response for import endpoint."""
    username: str
    imported: int
    skipped: int


class OpeningStats(BaseModel):
    """Opening statistics for a single ECO."""
    eco: str
    opening_name: str
    games: int
    wins: int
    draws: int
    losses: int
    score_pct: float


class ImportStatusResponse(BaseModel):
    """Response for import status endpoint."""
    username: str
    imported_at: str | None
    last_imported: int | None
    last_skipped: int | None
    total_games: int


class GameDetail(BaseModel):
    """Details for a single game."""
    site_game_id: str
    played_at: str | None
    color: str
    result: str
    opponent: str | None
    opening_name: str
    lichess_url: str


@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    init_db()


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.post("/api/import/lichess", response_model=ImportResponse)
async def import_lichess_games(request: ImportRequest):
    """
    Import games from Lichess for a user.
    Fetches PGN, parses games, and stores in database.
    """
    username = request.username.strip()
    max_games = request.max_games

    # Fetch PGN from Lichess
    try:
        pgn_text = fetch_lichess_pgn(username, max_games)
    except LichessAPIError as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail=e.message)
        elif e.status_code == 429:
            raise HTTPException(status_code=429, detail=e.message)
        else:
            raise HTTPException(status_code=502, detail=e.message)

    # Check if we got any games
    if not pgn_text.strip():
        raise HTTPException(
            status_code=404,
            detail=f"No rated games found for user '{username}'."
        )

    # Parse games
    games, skipped = parse_pgn_games(pgn_text, username)

    if not games and skipped == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}'."
        )

    # Store in database
    conn = get_connection()
    imported = 0
    try:
        for game in games:
            if upsert_game(conn, game):
                imported += 1
            else:
                skipped += 1
        conn.commit()
        
        # Record import status
        from datetime import datetime, timezone
        imported_at = datetime.now(timezone.utc).isoformat()
        upsert_import_status(
            conn, username, "lichess", 
            imported, skipped, max_games, imported_at
        )
        conn.commit()
    finally:
        conn.close()

    return ImportResponse(
        username=username,
        imported=imported,
        skipped=skipped
    )


@app.get("/api/openings/lichess/{username}", response_model=list[OpeningStats])
async def get_openings_report(
    username: str,
    color: str = Query(default="all", regex="^(all|white|black)$"),
    time_class: str = Query(default="all", regex="^(all|blitz|rapid|classical)$"),
):
    """
    Get aggregated opening statistics for a user.
    Supports filtering by color and time control.
    """
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    conn = get_connection()
    try:
        stats = get_openings_stats(conn, username, color, time_class)
    finally:
        conn.close()

    if not stats:
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}' with the specified filters."
        )

    return [OpeningStats(**s) for s in stats]


@app.get("/api/import-status/lichess/{username}", response_model=ImportStatusResponse)
async def get_import_status_endpoint(username: str):
    """Get last import status and total games count for a user."""
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    conn = get_connection()
    try:
        status = get_import_status(conn, username, "lichess")
    finally:
        conn.close()
    
    return ImportStatusResponse(**status)


@app.get("/api/games/lichess/{username}", response_model=list[GameDetail])
async def get_games_for_opening(
    username: str,
    eco: str = Query(..., description="ECO code for the opening"),
    limit: int = Query(default=10, ge=1, le=50)
):
    """Get recent games for a user and opening."""
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    if not eco:
        raise HTTPException(status_code=400, detail="ECO code is required.")
    
    conn = get_connection()
    try:
        games = get_games_by_opening(conn, username, eco, limit)
    finally:
        conn.close()
    
    if not games:
        raise HTTPException(
            status_code=404,
            detail=f"No games found for {username} with opening {eco}."
        )
    
    return [GameDetail(**g) for g in games]

