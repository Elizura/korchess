"""FastAPI application for Openingscope."""

import json
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from db import (
    get_connection, init_db, upsert_game, get_openings_stats,
    upsert_import_status, get_import_status, get_games_by_opening,
    get_game_by_id, get_analysis, save_analysis,
    get_full_analysis, save_full_analysis
)
from lichess import fetch_lichess_pgn, parse_pgn_games, LichessAPIError
from analysis import run_lightweight_analysis
from full_analysis import run_full_analysis, evaluate_position

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


class OpeningSummary(BaseModel):
    """Summary stats for an opening."""
    total_games: int
    wins: int
    draws: int
    losses: int
    score_pct: float


class OpeningGamesResponse(BaseModel):
    """Response with summary and games."""
    summary: OpeningSummary
    games: list[GameDetail]


class GameResponse(BaseModel):
    """Response for single game."""
    username: str
    game_id: str
    pgn: str
    played_at: str | None
    color: str
    result: str
    opponent: str | None
    lichess_url: str
    eco: str | None
    opening_name: str | None


class AnalysisResult(BaseModel):
    """Analysis result structure."""
    opening_eval_cp: int | None
    checkpoints: list[dict]
    biggest_mistake: dict | None
    accuracy: int
    meta: dict


class AnalysisResponse(BaseModel):
    """Response for analysis endpoint."""
    status: str  # "ready" | "missing"
    analysis: AnalysisResult | None = None
    created_at: str | None = None


class EvalScore(BaseModel):
    """Engine evaluation score."""
    cp: int | None = None
    mate: int | None = None
    depth: int = 0


class MoveEvaluation(BaseModel):
    """Evaluation for a single move."""
    ply: int
    san: str
    uci: str
    fen_before: str
    fen_after: str
    eval_before: dict | None = None
    eval_after: dict | None = None
    best_move_uci: str | None = None
    best_move_san: str | None = None
    pv: list[str] = []
    classification: str | None = None
    cp_loss: int | None = None
    multi_pv: list[dict] | None = None


class FullAnalysisSummary(BaseModel):
    """Summary of full analysis."""
    accuracy_white: int
    accuracy_black: int
    opening_name: str
    opening_eval: dict | None = None
    total_moves: int
    white_player: str
    black_player: str


class FullAnalysisMeta(BaseModel):
    """Metadata for full analysis."""
    engine: str
    depth: int
    multipv: int
    time_per_position_ms: int
    total_time_ms: int
    positions_analyzed: int


class FullAnalysisResult(BaseModel):
    """Full analysis result."""
    moves: list[MoveEvaluation]
    summary: FullAnalysisSummary
    meta: FullAnalysisMeta


class FullAnalysisResponse(BaseModel):
    """Response for full analysis endpoint."""
    status: str  # "ready" | "missing" | "running"
    analysis: FullAnalysisResult | None = None
    created_at: str | None = None


class EvalRequest(BaseModel):
    """Request body for position evaluation."""
    fen: str
    depth: int = Field(default=18, ge=1, le=30)
    multipv: int = Field(default=1, ge=1, le=5)


class EvalLineResult(BaseModel):
    """Single evaluation line."""
    cp: int | None = None
    mate: int | None = None
    depth: int = 0
    pv_uci: list[str] = []
    pv_san: list[str] = []


class EvalResponse(BaseModel):
    """Response for position evaluation."""
    eval: EvalLineResult | None = None
    multipv: list[EvalLineResult] | None = None
    fen: str


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
    color: str = Query(default="all", pattern="^(all|white|black)$"),
    time_class: str = Query(default="all", pattern="^(all|blitz|rapid|classical)$"),
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


@app.get("/api/games/lichess/{username}", response_model=OpeningGamesResponse)
async def get_games_for_opening(
    username: str,
    eco: str = Query(..., description="ECO code for the opening"),
    color: str = Query(default="all", pattern="^(all|white|black)$"),
    time_class: str = Query(default="all", pattern="^(all|blitz|rapid|classical)$"),
    result: str = Query(default="all", pattern="^(all|win|draw|loss)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=50)
):
    """Get recent games and summary for a user and opening with filters."""
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    if not eco:
        raise HTTPException(status_code=400, detail="ECO code is required.")
    
    conn = get_connection()
    try:
        result_data = get_games_by_opening(
            conn, username, eco, color, time_class, result, offset, limit
        )
    finally:
        conn.close()
    
    if result_data["summary"]["total_games"] == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No games found for {username} with opening {eco}."
        )
    
    return OpeningGamesResponse(**result_data)


@app.get("/api/game/lichess/{username}/{game_id}", response_model=GameResponse)
async def get_game(username: str, game_id: str):
    """Get game metadata and PGN."""
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    conn = get_connection()
    try:
        game = get_game_by_id(conn, username, game_id)
    finally:
        conn.close()
    
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    return GameResponse(
        username=username,
        game_id=game_id,
        pgn=game["pgn"] or "",
        played_at=game["played_at"],
        color=game["color"],
        result=game["result"],
        opponent=game["opponent"],
        lichess_url=f"https://lichess.org/{game_id}",
        eco=game.get("eco"),
        opening_name=game.get("opening_name")
    )


@app.get("/api/analysis/lichess/{username}/{game_id}", response_model=AnalysisResponse)
async def get_analysis_endpoint(username: str, game_id: str):
    """Get cached analysis for a game."""
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    conn = get_connection()
    try:
        cached = get_analysis(conn, username, game_id)
    finally:
        conn.close()
    
    if not cached:
        return AnalysisResponse(status="missing")
    
    return AnalysisResponse(
        status="ready",
        analysis=json.loads(cached["result_json"]),
        created_at=cached["created_at"]
    )


@app.post("/api/analysis/lichess/{username}/{game_id}", response_model=AnalysisResponse)
async def run_analysis_endpoint(username: str, game_id: str):
    """Run analysis on a game (or return cached)."""
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    conn = get_connection()
    try:
        # Check cache first
        cached = get_analysis(conn, username, game_id)
        if cached:
            return AnalysisResponse(
                status="ready",
                analysis=json.loads(cached["result_json"]),
                created_at=cached["created_at"]
            )
        
        # Get game
        game = get_game_by_id(conn, username, game_id)
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        
        if not game.get("pgn"):
            raise HTTPException(status_code=400, detail="Game has no PGN")
        
        # Run analysis
        try:
            result = run_lightweight_analysis(game["pgn"], game["color"])
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")
        
        # Save to cache
        settings = {"time_ms": 150, "checkpoints": [10, 20, 30, 40]}
        save_analysis(
            conn, username, game_id,
            engine_name="stockfish",
            engine_version="15+",
            settings_json=json.dumps(settings),
            result_json=json.dumps(result)
        )
        conn.commit()
        
        # Get created_at
        saved = get_analysis(conn, username, game_id)
        
        return AnalysisResponse(
            status="ready",
            analysis=result,
            created_at=saved["created_at"] if saved else None
        )
    finally:
        conn.close()


@app.get("/api/analysis/lichess/{username}/{game_id}/full", response_model=FullAnalysisResponse)
async def get_full_analysis_endpoint(
    username: str,
    game_id: str,
    depth: int = Query(default=18, ge=1, le=30),
    multipv: int = Query(default=1, ge=1, le=5)
):
    """Get cached full analysis for a game."""
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    conn = get_connection()
    try:
        cached = get_full_analysis(conn, username, game_id, depth, multipv)
    finally:
        conn.close()
    
    if not cached:
        return FullAnalysisResponse(status="missing")
    
    return FullAnalysisResponse(
        status="ready",
        analysis={
            "moves": json.loads(cached["moves_json"]),
            "summary": json.loads(cached["summary_json"]),
            "meta": json.loads(cached["meta_json"])
        },
        created_at=cached["created_at"]
    )


@app.post("/api/analysis/lichess/{username}/{game_id}/full", response_model=FullAnalysisResponse)
async def run_full_analysis_endpoint(
    username: str,
    game_id: str,
    depth: int = Query(default=18, ge=1, le=30),
    multipv: int = Query(default=1, ge=1, le=5)
):
    """Run full move-by-move analysis on a game (or return cached)."""
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    conn = get_connection()
    try:
        # Check cache first
        cached = get_full_analysis(conn, username, game_id, depth, multipv)
        if cached:
            return FullAnalysisResponse(
                status="ready",
                analysis={
                    "moves": json.loads(cached["moves_json"]),
                    "summary": json.loads(cached["summary_json"]),
                    "meta": json.loads(cached["meta_json"])
                },
                created_at=cached["created_at"]
            )
        
        # Get game
        game = get_game_by_id(conn, username, game_id)
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        
        if not game.get("pgn"):
            raise HTTPException(status_code=400, detail="Game has no PGN")
        
        # Run full analysis
        try:
            result = run_full_analysis(
                game["pgn"],
                depth=depth,
                multipv=multipv
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")
        
        # Save to cache
        save_full_analysis(
            conn, username, game_id,
            depth=depth,
            multipv=multipv,
            moves_json=json.dumps(result["moves"]),
            summary_json=json.dumps(result["summary"]),
            meta_json=json.dumps(result["meta"])
        )
        conn.commit()
        
        # Get created_at
        saved = get_full_analysis(conn, username, game_id, depth, multipv)
        
        return FullAnalysisResponse(
            status="ready",
            analysis=result,
            created_at=saved["created_at"] if saved else None
        )
    finally:
        conn.close()


@app.post("/api/eval", response_model=EvalResponse)
async def evaluate_position_endpoint(request: EvalRequest):
    """Evaluate a single position with Stockfish."""
    try:
        result = evaluate_position(
            fen=request.fen,
            depth=request.depth,
            multipv=request.multipv
        )
        return EvalResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {str(e)}")

