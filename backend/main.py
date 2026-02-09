"""FastAPI application for Korchess."""

import json
import uuid
import asyncio
import io
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from db import (
    get_connection, init_db, upsert_game, get_openings_stats,
    upsert_import_status, get_import_status, get_games_by_opening,
    get_game_by_id, get_analysis, save_analysis,
    get_full_analysis, save_full_analysis,
    create_analysis_job, get_analysis_job, delete_analysis_job, count_analysis_jobs,
    ensure_games_schema, get_variations_stats,
)
from lichess import fetch_lichess_pgn, parse_pgn_games, LichessAPIError
from chesscom import fetch_chesscom_games, ChesscomAPIError
from opening_match import game_to_uci_plies, best_opening_match
import chess.pgn
from analysis import run_lightweight_analysis
from full_analysis import run_full_analysis, evaluate_position
from import_openings import main as seed_openings

# ============================================================================
# Site validation
# ============================================================================
VALID_SITES = {"lichess", "chesscom", "all"}

def validate_site(site: str) -> str:
    """Validate and normalize site parameter."""
    site = site.lower()
    if site not in VALID_SITES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid site. Must be one of: {', '.join(VALID_SITES)}"
        )
    return site


# ============================================================================
# Concurrency control for background analysis
# ============================================================================
MAX_CONCURRENT_ANALYSES = 2
active_analysis_count = 0
active_analysis_lock = asyncio.Lock()

app = FastAPI(
    title="Korchess API",
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
        "http://72.62.24.92:3000",
        "https://korchess.com",
        "https://www.korchess.com",
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
    """Opening statistics for a single opening key."""
    opening_key: str
    opening_label: str
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
    site: str
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
    opening_key: str
    opening_label: str
    variation_label: str | None = None


class VariationStats(BaseModel):
    """Variation statistics for a single opening key."""
    variation_key: str
    variation_label: str
    games: int
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
    status: str  # "completed" | "missing" | "processing"
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
    """Initialize database on startup and seed openings."""
    init_db()
    try:
        seed_openings()
    except Exception as exc:
        # Don't crash the app if seeding fails; just log the error.
        print(f"Opening seeding failed: {exc}")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.post("/api/import/lichess", response_model=ImportResponse)
async def import_lichess_games(request: ImportRequest):
    username = request.username.strip()
    max_games = request.max_games

    try:
        pgn_text = fetch_lichess_pgn(username, max_games)
    except LichessAPIError as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail=e.message)
        elif e.status_code == 429:
            raise HTTPException(status_code=429, detail=e.message)
        else:
            raise HTTPException(status_code=502, detail=e.message)

    if not pgn_text.strip():
        raise HTTPException(
            status_code=404,
            detail=f"No rated games found for user '{username}'."
        )

    # Store in database
    conn = get_connection()
    imported = 0
    try:
        ensure_games_schema(conn)
        games, skipped = parse_pgn_games(pgn_text, username, conn)

        if not games and skipped == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No games found for user '{username}'."
            )

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


@app.post("/api/import/chesscom", response_model=ImportResponse)
async def import_chesscom_games(request: ImportRequest):
    """
    Import games from Chess.com for a user.
    Fetches games via Chess.com API, parses, and stores in database.
    """
    username = request.username.strip()
    max_games = request.max_games

    # Fetch games from Chess.com
    try:
        games = fetch_chesscom_games(username, max_games)
    except ChesscomAPIError as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail=e.message)
        elif e.status_code == 429:
            raise HTTPException(status_code=429, detail=e.message)
        else:
            raise HTTPException(status_code=502, detail=e.message)

    # Check if we got any games
    if not games:
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}' on Chess.com."
        )

    # Store in database
    conn = get_connection()
    imported = 0
    skipped = 0
    try:
        ensure_games_schema(conn)
        for game in games:
            opening = None
            try:
                pgn_text = game.get("pgn", "")
                game_obj = chess.pgn.read_game(io.StringIO(pgn_text)) if pgn_text else None
                if game_obj:
                    uci_plies = game_to_uci_plies(game_obj, max_plies=40)
                    opening = best_opening_match(conn, uci_plies)
            except Exception:
                opening = None

            game["eco"] = opening["eco"] if opening else "UNKNOWN"
            game["opening_name"] = opening["name"] if opening else "Unknown"
            game["opening_id"] = opening["opening_id"] if opening else None
            game["opening_ply_count"] = opening["ply_count"] if opening else None

            if upsert_game(conn, game):
                imported += 1
            else:
                skipped += 1
        conn.commit()

        
        # Record import status
        from datetime import datetime, timezone
        imported_at = datetime.now(timezone.utc).isoformat()
        upsert_import_status(
            conn, username, "chesscom", 
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


@app.get("/api/openings/{site}/{username}", response_model=list[OpeningStats])
async def get_openings_report(
    site: str,
    username: str,
    color: str = Query(default="all", pattern="^(all|white|black)$"),
    time_class: str = Query(default="all", pattern="^(all|blitz|rapid|classical)$"),
    limit: int = Query(default=10, ge=1, le=100, description="Max number of openings to return (top by games)"),
):
    """
    Get aggregated opening statistics for a user.
    Returns top N openings by games played (default 10).
    Supports filtering by color, time control, and site.
    Site can be 'lichess', 'chesscom', or 'all'.
    """
    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    conn = get_connection()
    try:
        stats = get_openings_stats(conn, username, color, time_class, site, limit)
    finally:
        conn.close()

    if not stats:
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}' with the specified filters."
        )

    return [OpeningStats(**s) for s in stats]


@app.get("/api/import-status/{site}/{username}", response_model=ImportStatusResponse)
async def get_import_status_endpoint(site: str, username: str):
    """Get last import status and total games count for a user on a specific site."""
    site = validate_site(site)
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    conn = get_connection()
    try:
        status = get_import_status(conn, username, site)
    finally:
        conn.close()
    
    return ImportStatusResponse(**status)


@app.get("/api/games/{site}/{username}", response_model=OpeningGamesResponse)
async def get_games_for_opening(
    site: str,
    username: str,
    opening_key: str = Query(..., description="Opening key for the opening"),
    variation_key: str | None = Query(default=None, description="Variation key for the opening"),
    color: str = Query(default="all", pattern="^(all|white|black)$"),
    time_class: str = Query(default="all", pattern="^(all|blitz|rapid|classical)$"),
    result: str = Query(default="all", pattern="^(all|win|draw|loss)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Get recent games and summary for a user and opening with filters."""
    site = validate_site(site)
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    if not opening_key:
        raise HTTPException(status_code=400, detail="Opening key is required.")
    
    conn = get_connection()
    try:
        result_data = get_games_by_opening(
            conn, username, opening_key, variation_key, color, time_class, result, offset, limit, site
        )
    finally:
        conn.close()
    
    if result_data["summary"]["total_games"] == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No games found for {username} with opening {opening_key}."
        )
    
    return OpeningGamesResponse(**result_data)

@app.get("/api/openings/{site}/{username}/variations", response_model=list[VariationStats])
async def get_opening_variations(
    site: str,
    username: str,
    opening_key: str = Query(..., description="Opening key for the opening"),
    color: str = Query(default="all", pattern="^(all|white|black)$"),
    time_class: str = Query(default="all", pattern="^(all|blitz|rapid|classical)$"),
):
    """Get variation statistics for a user's opening key with filters."""
    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    if not opening_key:
        raise HTTPException(status_code=400, detail="Opening key is required.")

    conn = get_connection()
    try:
        stats = get_variations_stats(conn, username, opening_key, color, time_class, site)
    finally:
        conn.close()

    return [VariationStats(**s) for s in stats]



@app.get("/api/game/{site}/{username}/{game_id}", response_model=GameResponse)
async def get_game(site: str, username: str, game_id: str):
    """Get game metadata and PGN."""
    site = validate_site(site)
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    conn = get_connection()
    try:
        game = get_game_by_id(conn, username, game_id, site)
    finally:
        conn.close()
    
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Build site-specific URL
    if game["site"] == "lichess":
        game_url = f"https://lichess.org/{game_id}"
    elif game["site"] == "chesscom":
        game_url = f"https://www.chess.com/game/live/{game_id}"
    else:
        game_url = ""
    
    return GameResponse(
        username=username,
        game_id=game_id,
        pgn=game["pgn"] or "",
        played_at=game["played_at"],
        color=game["color"],
        result=game["result"],
        opponent=game["opponent"],
        lichess_url=game_url,  # Note: field name kept for backwards compatibility
        eco=game.get("eco"),
        opening_name=game.get("opening_name")
    )


@app.get("/api/analysis/{site}/{username}/{game_id}", response_model=AnalysisResponse)
async def get_analysis_endpoint(site: str, username: str, game_id: str):
    """Get cached analysis for a game."""
    site = validate_site(site)
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    conn = get_connection()
    try:
        cached = get_analysis(conn, username, game_id, site)
    finally:
        conn.close()
    
    if not cached:
        return AnalysisResponse(status="missing")
    
    return AnalysisResponse(
        status="ready",
        analysis=json.loads(cached["result_json"]),
        created_at=cached["created_at"]
    )


@app.post("/api/analysis/{site}/{username}/{game_id}", response_model=AnalysisResponse)
async def run_analysis_endpoint(site: str, username: str, game_id: str):
    """Run analysis on a game (or return cached)."""
    site = validate_site(site)
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    conn = get_connection()
    try:
        # Check cache first
        cached = get_analysis(conn, username, game_id, site)
        if cached:
            return AnalysisResponse(
                status="ready",
                analysis=json.loads(cached["result_json"]),
                created_at=cached["created_at"]
            )
        
        # Get game
        game = get_game_by_id(conn, username, game_id, site)
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
            conn, username, game_id, site,
            engine_name="stockfish",
            engine_version="15+",
            settings_json=json.dumps(settings),
            result_json=json.dumps(result)
        )
        conn.commit()
        
        # Get created_at
        saved = get_analysis(conn, username, game_id, site)
        
        return AnalysisResponse(
            status="ready",
            analysis=result,
            created_at=saved["created_at"] if saved else None
        )
    finally:
        conn.close()


# ============================================================================
# Background Analysis Task
# ============================================================================

async def run_analysis_background(
    job_id: str,
    username: str,
    game_id: str,
    pgn: str,
    depth: int,
    multipv: int,
    site: str
):
    """Background task to run Stockfish analysis."""
    global active_analysis_count
    
    try:
        # Run analysis in thread pool (CPU-bound operation)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: run_full_analysis(pgn, depth, multipv)
        )
        
        # Save results to full_analysis table
        conn = get_connection()
        try:
            save_full_analysis(
                conn, username, game_id,
                depth=depth,
                multipv=multipv,
                moves_json=json.dumps(result["moves"]),
                summary_json=json.dumps(result["summary"]),
                meta_json=json.dumps(result["meta"]),
                site=site
            )
            delete_analysis_job(conn, job_id)
            conn.commit()
            print(f"[Analysis] Completed for game {game_id} on {site}")
        finally:
            conn.close()
            
    except Exception as e:
        # On failure, just delete the job so user can retry
        print(f"[Analysis] Failed for game {game_id} on {site}: {e}")
        conn = get_connection()
        try:
            delete_analysis_job(conn, job_id)
            conn.commit()
        finally:
            conn.close()
            
    finally:
        async with active_analysis_lock:
            active_analysis_count -= 1
            print(f"[Analysis] Active count now: {active_analysis_count}")


@app.get("/api/analysis/{site}/{username}/{game_id}/full", response_model=FullAnalysisResponse)
async def get_full_analysis_endpoint(
    site: str,
    username: str,
    game_id: str,
    depth: int = Query(default=18, ge=1, le=30),
    multipv: int = Query(default=1, ge=1, le=5)
):
    """Get full analysis status for a game."""
    site = validate_site(site)
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    conn = get_connection()
    try:
        # Check if completed analysis exists
        cached = get_full_analysis(conn, username, game_id, depth, multipv, site)
        if cached:
            return FullAnalysisResponse(
                status="completed",
                analysis={
                    "moves": json.loads(cached["moves_json"]),
                    "summary": json.loads(cached["summary_json"]),
                    "meta": json.loads(cached["meta_json"])
                },
                created_at=cached["created_at"]
            )
        
        # Check if job is currently processing
        job = get_analysis_job(conn, username, game_id, depth, multipv, site)
        if job:
            return FullAnalysisResponse(status="processing")
        
        # Nothing exists
        return FullAnalysisResponse(status="missing")
    finally:
        conn.close()


@app.post("/api/analysis/{site}/{username}/{game_id}/full", response_model=FullAnalysisResponse)
async def run_full_analysis_endpoint(
    site: str,
    username: str,
    game_id: str,
    depth: int = Query(default=18, ge=1, le=30),
    multipv: int = Query(default=1, ge=1, le=5)
):
    """Start full move-by-move analysis on a game (async with background task)."""
    global active_analysis_count
    
    site = validate_site(site)
    username = username.strip()
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    
    conn = get_connection()
    try:
        # 1. Check if completed analysis already exists
        cached = get_full_analysis(conn, username, game_id, depth, multipv, site)
        if cached:
            return FullAnalysisResponse(
                status="completed",
                analysis={
                    "moves": json.loads(cached["moves_json"]),
                    "summary": json.loads(cached["summary_json"]),
                    "meta": json.loads(cached["meta_json"])
                },
                created_at=cached["created_at"]
            )
        
        # 2. Check if job is already processing
        existing_job = get_analysis_job(conn, username, game_id, depth, multipv, site)
        if existing_job:
            return FullAnalysisResponse(status="processing")
        
        # 3. Check concurrency limit
        async with active_analysis_lock:
            if active_analysis_count >= MAX_CONCURRENT_ANALYSES:
                raise HTTPException(
                    status_code=429,
                    detail="Server busy. Max 2 analyses can run at once. Try again shortly."
                )
            active_analysis_count += 1
            print(f"[Analysis] Starting new analysis. Active count: {active_analysis_count}")
        
        # 4. Get game PGN
        game = get_game_by_id(conn, username, game_id, site)
        if not game:
            async with active_analysis_lock:
                active_analysis_count -= 1
            raise HTTPException(status_code=404, detail="Game not found")
        
        if not game.get("pgn"):
            async with active_analysis_lock:
                active_analysis_count -= 1
            raise HTTPException(status_code=400, detail="Game has no PGN")
        
        # 5. Create job and start background task
        job_id = str(uuid.uuid4())
        create_analysis_job(conn, job_id, username, game_id, depth, multipv, site)
        conn.commit()
        
        # Start background task (non-blocking)
        asyncio.create_task(run_analysis_background(
            job_id, username, game_id, game["pgn"], depth, multipv, site
        ))
        
        return FullAnalysisResponse(status="processing")
        
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
