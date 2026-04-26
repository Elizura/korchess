"""Performance test for Stockfish analysis - sequential vs parallel vs cached."""

import io
import os
import queue
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import chess
import chess.engine
import chess.pgn
import psycopg
from dotenv import load_dotenv

from lmdb_magic.reader import lookup_fens

# Configuration
STOCKFISH_PATH = "/usr/games/stockfish"
SCAN_DEPTH = 8
CP_LOSS_THRESHOLD = 100  # Flag moves with cp_loss >= this
PARALLEL_WORKERS = 3  # Number of parallel engines (set to vCPU count)


def get_connection() -> psycopg.Connection:
    """Get database connection."""
    database_url = "postgresql://postgres:postgres@db:5432/korchess"
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg.connect(database_url, autocommit=False, connect_timeout=5)


def fetch_raw_games(conn: psycopg.Connection, username: str, limit: int = 250) -> list[dict]:
    """Fetch raw games from raw_games table."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, username, site, site_game_id, pgn
        FROM raw_games
        WHERE username = %s AND site = 'lichess'
        ORDER BY id
        LIMIT %s
    """, (username.lower(), limit))
    
    rows = cur.fetchall()
    games = []
    for row in rows:
        games.append({
            "id": row[0],
            "username": row[1],
            "site": row[2],
            "site_game_id": row[3],
            "pgn": row[4],
        })
    return games


def create_engine() -> chess.engine.SimpleEngine:
    """Create and configure Stockfish engine."""
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    try:
        engine.configure({"Threads": 1, "Hash": 64})
    except Exception:
        pass
    return engine


def score_to_cp(score: chess.engine.PovScore) -> int:
    """Convert engine score to centipawns from White's perspective."""
    pov_score = score.pov(chess.WHITE)
    if pov_score.is_mate():
        mate_in = pov_score.mate()
        return 10000 if mate_in > 0 else -10000
    return pov_score.score() or 0


def analyze_game_sequential(
    engine: chess.engine.SimpleEngine,
    pgn_text: str,
    username: str,
) -> dict[str, Any]:
    """
    Analyze a game move-by-move sequentially.
    
    For each user move:
    1. Analyze position BEFORE the move (get best move and eval)
    2. Push the move
    3. Analyze position AFTER the move (get new eval)
    4. Compute cp_loss = eval_before - eval_after (from user's perspective)
    5. Flag if cp_loss >= threshold
    
    Returns stats about the analysis.
    """
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if not game:
        return {"error": "Could not parse PGN"}
    
    # Determine user's color from PGN headers
    white_player = (game.headers.get("White") or "").lower()
    black_player = (game.headers.get("Black") or "").lower()
    username_lower = username.lower()
    
    if username_lower in white_player:
        user_is_white = True
    elif username_lower in black_player:
        user_is_white = False
    else:
        user_is_white = True  # Default assumption
    
    board = game.board()
    moves_list = list(game.mainline_moves())
    
    limit = chess.engine.Limit(depth=SCAN_DEPTH)  # depth-only for deterministic results
    
    flagged_moves: list[dict] = []
    total_user_moves = 0
    total_engine_calls = 0
    total_cp_loss = 0
    
    for ply, move in enumerate(moves_list):
        is_white_move = (ply % 2 == 0)
        is_user_move = (is_white_move == user_is_white)
        
        if not is_user_move:
            board.push(move)
            continue
        
        total_user_moves += 1
        fen_before = board.fen()
        side_to_move = board.turn
        move_san = board.san(move)
        move_uci = move.uci()
        
        # Analyze BEFORE position
        info_before = engine.analyse(board, limit)
        total_engine_calls += 1
        
        score_before = info_before.get("score")
        pv_before = info_before.get("pv", [])
        best_move = pv_before[0] if pv_before else None
        best_move_uci = best_move.uci() if best_move else None
        
        eval_before_cp = score_to_cp(score_before) if score_before else 0
        
        # Push the move
        board.push(move)
        fen_after = board.fen()
        
        # Skip if game is over
        if board.is_game_over():
            continue
        
        # Analyze AFTER position
        info_after = engine.analyse(board, limit)
        total_engine_calls += 1
        
        score_after = info_after.get("score")
        eval_after_cp = score_to_cp(score_after) if score_after else 0
        
        # Compute cp_loss from user's perspective
        if side_to_move == chess.WHITE:
            cp_loss = eval_before_cp - eval_after_cp
        else:
            cp_loss = eval_after_cp - eval_before_cp
        
        cp_loss = max(0, cp_loss)
        total_cp_loss += cp_loss
        
        # Flag if significant loss
        if cp_loss >= CP_LOSS_THRESHOLD:
            flagged_moves.append({
                "ply": ply,
                "san": move_san,
                "uci": move_uci,
                "cp_loss": cp_loss,
                "best_move": best_move_uci,
                "eval_before": eval_before_cp,
                "eval_after": eval_after_cp,
            })
    
    return {
        "total_user_moves": total_user_moves,
        "total_engine_calls": total_engine_calls,
        "flagged_moves": flagged_moves,
        "num_flagged": len(flagged_moves),
        "total_cp_loss": total_cp_loss,
        "avg_cp_loss": total_cp_loss / total_user_moves if total_user_moves > 0 else 0,
    }


class EnginePool:
    """Thread-safe pool of reusable Stockfish engines."""
    
    def __init__(self, size: int):
        self.size = size
        self._pool: queue.Queue[chess.engine.SimpleEngine] = queue.Queue()
        self._engines: list[chess.engine.SimpleEngine] = []
        
    def start(self) -> None:
        """Create all engines upfront."""
        print(f"  Creating {self.size} Stockfish engines...")
        for i in range(self.size):
            engine = create_engine()
            self._engines.append(engine)
            self._pool.put(engine)
        print(f"  Engine pool ready.")
    
    def acquire(self) -> chess.engine.SimpleEngine:
        """Get an engine from the pool (blocks if none available)."""
        return self._pool.get()
    
    def release(self, engine: chess.engine.SimpleEngine) -> None:
        """Return an engine to the pool."""
        self._pool.put(engine)
    
    def shutdown(self) -> None:
        """Close all engines."""
        for engine in self._engines:
            try:
                engine.quit()
            except Exception:
                pass
        self._engines.clear()


def analyze_game_with_pool(
    game: dict,
    username: str,
    pool: EnginePool,
) -> dict[str, Any]:
    """Analyze a game using an engine from the pool."""
    engine = pool.acquire()
    try:
        start = time.perf_counter()
        result = analyze_game_sequential(engine, game["pgn"], username)
        elapsed = time.perf_counter() - start
        result["elapsed"] = elapsed
        result["game_id"] = game["site_game_id"]
        return result
    finally:
        pool.release(engine)


def run_sequential_test(games: list[dict], username: str) -> dict[str, Any]:
    """Run sequential analysis (one game at a time, single engine)."""
    print(f"\nStarting Stockfish engine (depth={SCAN_DEPTH})...")
    engine = create_engine()
    
    try:
        total_time = 0.0
        total_engine_calls = 0
        total_flagged = 0
        all_flagged_moves: list[dict] = []
        
        for i, game in enumerate(games):
            start = time.perf_counter()
            result = analyze_game_sequential(engine, game["pgn"], username)
            elapsed = time.perf_counter() - start
            
            total_time += elapsed
            total_engine_calls += result.get("total_engine_calls", 0)
            total_flagged += result.get("num_flagged", 0)
            
            for fm in result.get("flagged_moves", []):
                fm["game_id"] = game["site_game_id"]
                all_flagged_moves.append(fm)
            
            print(f"  Game {i+1}/{len(games)}: {elapsed:.2f}s, "
                  f"{result.get('total_user_moves', 0)} moves, "
                  f"{result.get('num_flagged', 0)} flagged")
        
        return {
            "total_time": total_time,
            "total_engine_calls": total_engine_calls,
            "total_flagged": total_flagged,
            "all_flagged_moves": all_flagged_moves,
        }
    finally:
        engine.quit()


def run_parallel_test(games: list[dict], username: str, max_workers: int = PARALLEL_WORKERS) -> dict[str, Any]:
    """Run parallel analysis with reusable engine pool."""
    print(f"\nStarting engine pool with {max_workers} engines (depth={SCAN_DEPTH})...")
    
    pool = EnginePool(max_workers)
    pool.start()
    
    total_engine_calls = 0
    total_flagged = 0
    all_flagged_moves: list[dict] = []
    completed = 0
    
    start_time = time.perf_counter()
    
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(analyze_game_with_pool, game, username, pool): game
                for game in games
            }
            
            for future in as_completed(futures):
                completed += 1
                try:
                    result = future.result()
                    total_engine_calls += result.get("total_engine_calls", 0)
                    total_flagged += result.get("num_flagged", 0)
                    
                    for fm in result.get("flagged_moves", []):
                        fm["game_id"] = result.get("game_id")
                        all_flagged_moves.append(fm)
                    
                    print(f"  Game {completed}/{len(games)} ({result.get('game_id', '?')}): "
                          f"{result.get('elapsed', 0):.2f}s, "
                          f"{result.get('total_user_moves', 0)} moves, "
                          f"{result.get('num_flagged', 0)} flagged")
                except Exception as e:
                    print(f"  Game {completed}/{len(games)}: ERROR - {e}")
    finally:
        pool.shutdown()
        print("  Engine pool shut down.")
    
    total_time = time.perf_counter() - start_time
    
    return {
        "total_time": total_time,
        "total_engine_calls": total_engine_calls,
        "total_flagged": total_flagged,
        "all_flagged_moves": all_flagged_moves,
    }


def analyze_game_cached(
    engine: chess.engine.SimpleEngine,
    pgn_text: str,
    username: str,
) -> dict[str, Any]:
    """
    Analyze a game using LMDB cache first, Stockfish only for misses.
    
    Phase 1: Collect all FENs needed
    Phase 2a: Bulk lookup in LMDB cache
    Phase 2b: Stockfish only for cache misses
    Phase 3: Compute cp_loss and flag moves
    """
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if not game:
        return {"error": "Could not parse PGN"}
    
    white_player = (game.headers.get("White") or "").lower()
    black_player = (game.headers.get("Black") or "").lower()
    username_lower = username.lower()
    
    if username_lower in white_player:
        user_is_white = True
    elif username_lower in black_player:
        user_is_white = False
    else:
        user_is_white = True
    
    board = game.board()
    moves_list = list(game.mainline_moves())
    limit = chess.engine.Limit(depth=SCAN_DEPTH)
    
    # Phase 1: Collect all FENs and move data
    user_moves_data: list[dict] = []
    fens_to_analyze: set[str] = set()
    current_board = board.copy()
    
    for ply, move in enumerate(moves_list):
        is_white_move = (ply % 2 == 0)
        is_user_move = (is_white_move == user_is_white)
        
        fen_before = current_board.fen()
        side_to_move = current_board.turn
        move_san = current_board.san(move)
        move_uci = move.uci()
        
        current_board.push(move)
        
        if not is_user_move:
            continue
        
        fen_after = current_board.fen()
        is_game_over = current_board.is_game_over()
        
        user_moves_data.append({
            "ply": ply,
            "move_san": move_san,
            "move_uci": move_uci,
            "fen_before": fen_before,
            "fen_after": fen_after,
            "side_to_move": side_to_move,
            "is_game_over": is_game_over,
        })
        
        fens_to_analyze.add(fen_before)
        if not is_game_over:
            fens_to_analyze.add(fen_after)
    
    unique_fens = list(fens_to_analyze)
    
    # Phase 2a: LMDB bulk lookup
    info_by_fen: dict[str, Any] = lookup_fens(unique_fens)
    lmdb_hits = len(info_by_fen)
    remaining_fens = [f for f in unique_fens if f not in info_by_fen]
    
    # Phase 2b: Stockfish for misses
    total_engine_calls = 0
    for fen in remaining_fens:
        try:
            fen_board = chess.Board(fen)
            info = engine.analyse(fen_board, limit)
            info_by_fen[fen] = info
            total_engine_calls += 1
        except Exception:
            pass
    
    # Phase 3: Process results
    flagged_moves: list[dict] = []
    total_user_moves = len(user_moves_data)
    total_cp_loss = 0
    
    for move_data in user_moves_data:
        fen_before = move_data["fen_before"]
        fen_after = move_data["fen_after"]
        side_to_move = move_data["side_to_move"]
        is_game_over = move_data["is_game_over"]
        
        info_before = info_by_fen.get(fen_before)
        if info_before is None:
            continue
        
        score_before = info_before.get("score")
        pv_before = info_before.get("pv", [])
        best_move = pv_before[0] if pv_before else None
        best_move_uci = best_move.uci() if best_move else None
        
        eval_before_cp = score_to_cp(score_before) if score_before else 0
        
        if is_game_over:
            continue
        
        info_after = info_by_fen.get(fen_after)
        if info_after is None:
            continue
        
        score_after = info_after.get("score")
        eval_after_cp = score_to_cp(score_after) if score_after else 0
        
        if side_to_move == chess.WHITE:
            cp_loss = eval_before_cp - eval_after_cp
        else:
            cp_loss = eval_after_cp - eval_before_cp
        
        cp_loss = max(0, cp_loss)
        total_cp_loss += cp_loss
        
        if cp_loss >= CP_LOSS_THRESHOLD:
            flagged_moves.append({
                "ply": move_data["ply"],
                "san": move_data["move_san"],
                "uci": move_data["move_uci"],
                "cp_loss": cp_loss,
                "best_move": best_move_uci,
                "eval_before": eval_before_cp,
                "eval_after": eval_after_cp,
            })
    
    return {
        "total_user_moves": total_user_moves,
        "total_engine_calls": total_engine_calls,
        "lmdb_hits": lmdb_hits,
        "lmdb_misses": len(remaining_fens),
        "flagged_moves": flagged_moves,
        "num_flagged": len(flagged_moves),
        "total_cp_loss": total_cp_loss,
        "avg_cp_loss": total_cp_loss / total_user_moves if total_user_moves > 0 else 0,
    }


def run_cached_test(games: list[dict], username: str) -> dict[str, Any]:
    """Run cached analysis (LMDB first, Stockfish for misses only)."""
    print(f"\nStarting Stockfish engine for cache misses (depth={SCAN_DEPTH})...")
    engine = create_engine()
    
    try:
        total_time = 0.0
        total_engine_calls = 0
        total_lmdb_hits = 0
        total_lmdb_misses = 0
        total_flagged = 0
        all_flagged_moves: list[dict] = []
        
        for i, game in enumerate(games):
            start = time.perf_counter()
            result = analyze_game_cached(engine, game["pgn"], username)
            elapsed = time.perf_counter() - start
            
            total_time += elapsed
            total_engine_calls += result.get("total_engine_calls", 0)
            total_lmdb_hits += result.get("lmdb_hits", 0)
            total_lmdb_misses += result.get("lmdb_misses", 0)
            total_flagged += result.get("num_flagged", 0)
            
            for fm in result.get("flagged_moves", []):
                fm["game_id"] = game["site_game_id"]
                all_flagged_moves.append(fm)
            
            hit_rate = (result.get("lmdb_hits", 0) / 
                       (result.get("lmdb_hits", 0) + result.get("lmdb_misses", 0)) * 100
                       if (result.get("lmdb_hits", 0) + result.get("lmdb_misses", 0)) > 0 else 0)
            
            print(f"  Game {i+1}/{len(games)}: {elapsed:.2f}s, "
                  f"{result.get('total_user_moves', 0)} moves, "
                  f"cache hit {hit_rate:.0f}%, "
                  f"{result.get('num_flagged', 0)} flagged")
        
        return {
            "total_time": total_time,
            "total_engine_calls": total_engine_calls,
            "total_lmdb_hits": total_lmdb_hits,
            "total_lmdb_misses": total_lmdb_misses,
            "total_flagged": total_flagged,
            "all_flagged_moves": all_flagged_moves,
        }
    finally:
        engine.quit()


def analyze_fens_parallel(
    fens: list[str],
    pool: EnginePool,
    max_workers: int = PARALLEL_WORKERS,
) -> dict[str, Any]:
    """Analyze a list of FENs in parallel using an engine pool."""
    if not fens:
        return {}
    
    results: dict[str, Any] = {}
    limit = chess.engine.Limit(depth=SCAN_DEPTH)
    
    def analyze_single_fen(fen: str) -> tuple[str, Any]:
        engine = pool.acquire()
        try:
            board = chess.Board(fen)
            info = engine.analyse(board, limit)
            return fen, info
        except Exception:
            return fen, None
        finally:
            pool.release(engine)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(analyze_single_fen, fen): fen for fen in fens}
        for future in as_completed(futures):
            try:
                fen, info = future.result()
                if info is not None:
                    results[fen] = info
            except Exception:
                pass
    
    return results


def analyze_game_cached_parallel(
    pgn_text: str,
    username: str,
    pool: EnginePool,
    max_workers: int = PARALLEL_WORKERS,
) -> dict[str, Any]:
    """
    Analyze a game using LMDB cache first, then parallel Stockfish for misses.
    
    Phase 1: Collect all FENs needed
    Phase 2a: Bulk lookup in LMDB cache
    Phase 2b: Parallel Stockfish for cache misses
    Phase 3: Compute cp_loss and flag moves
    """
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if not game:
        return {"error": "Could not parse PGN"}
    
    white_player = (game.headers.get("White") or "").lower()
    black_player = (game.headers.get("Black") or "").lower()
    username_lower = username.lower()
    
    if username_lower in white_player:
        user_is_white = True
    elif username_lower in black_player:
        user_is_white = False
    else:
        user_is_white = True
    
    board = game.board()
    moves_list = list(game.mainline_moves())
    
    # Phase 1: Collect all FENs and move data
    user_moves_data: list[dict] = []
    fens_to_analyze: set[str] = set()
    current_board = board.copy()
    
    for ply, move in enumerate(moves_list):
        is_white_move = (ply % 2 == 0)
        is_user_move = (is_white_move == user_is_white)
        
        fen_before = current_board.fen()
        side_to_move = current_board.turn
        move_san = current_board.san(move)
        move_uci = move.uci()
        
        current_board.push(move)
        
        if not is_user_move:
            continue
        
        fen_after = current_board.fen()
        is_game_over = current_board.is_game_over()
        
        user_moves_data.append({
            "ply": ply,
            "move_san": move_san,
            "move_uci": move_uci,
            "fen_before": fen_before,
            "fen_after": fen_after,
            "side_to_move": side_to_move,
            "is_game_over": is_game_over,
        })
        
        fens_to_analyze.add(fen_before)
        if not is_game_over:
            fens_to_analyze.add(fen_after)
    
    unique_fens = list(fens_to_analyze)
    
    # Phase 2a: LMDB bulk lookup
    info_by_fen: dict[str, Any] = lookup_fens(unique_fens)
    lmdb_hits = len(info_by_fen)
    remaining_fens = [f for f in unique_fens if f not in info_by_fen]
    
    # Phase 2b: Parallel Stockfish for misses
    if remaining_fens:
        stockfish_results = analyze_fens_parallel(remaining_fens, pool, max_workers)
        info_by_fen.update(stockfish_results)
    
    total_engine_calls = len(remaining_fens)
    
    # Phase 3: Process results
    flagged_moves: list[dict] = []
    total_user_moves = len(user_moves_data)
    total_cp_loss = 0
    
    for move_data in user_moves_data:
        fen_before = move_data["fen_before"]
        fen_after = move_data["fen_after"]
        side_to_move = move_data["side_to_move"]
        is_game_over = move_data["is_game_over"]
        
        info_before = info_by_fen.get(fen_before)
        if info_before is None:
            continue
        
        score_before = info_before.get("score")
        pv_before = info_before.get("pv", [])
        best_move = pv_before[0] if pv_before else None
        best_move_uci = best_move.uci() if best_move else None
        
        eval_before_cp = score_to_cp(score_before) if score_before else 0
        
        if is_game_over:
            continue
        
        info_after = info_by_fen.get(fen_after)
        if info_after is None:
            continue
        
        score_after = info_after.get("score")
        eval_after_cp = score_to_cp(score_after) if score_after else 0
        
        if side_to_move == chess.WHITE:
            cp_loss = eval_before_cp - eval_after_cp
        else:
            cp_loss = eval_after_cp - eval_before_cp
        
        cp_loss = max(0, cp_loss)
        total_cp_loss += cp_loss
        
        if cp_loss >= CP_LOSS_THRESHOLD:
            flagged_moves.append({
                "ply": move_data["ply"],
                "san": move_data["move_san"],
                "uci": move_data["move_uci"],
                "cp_loss": cp_loss,
                "best_move": best_move_uci,
                "eval_before": eval_before_cp,
                "eval_after": eval_after_cp,
            })
    
    return {
        "total_user_moves": total_user_moves,
        "total_engine_calls": total_engine_calls,
        "lmdb_hits": lmdb_hits,
        "lmdb_misses": len(remaining_fens),
        "flagged_moves": flagged_moves,
        "num_flagged": len(flagged_moves),
        "total_cp_loss": total_cp_loss,
        "avg_cp_loss": total_cp_loss / total_user_moves if total_user_moves > 0 else 0,
    }


def run_cached_parallel_test(
    games: list[dict],
    username: str,
    max_workers: int = PARALLEL_WORKERS,
) -> dict[str, Any]:
    """Run cached + parallel analysis (LMDB first, parallel Stockfish for misses)."""
    print(f"\nStarting engine pool with {max_workers} engines for cache misses (depth={SCAN_DEPTH})...")
    
    pool = EnginePool(max_workers)
    pool.start()
    
    total_time = 0.0
    total_engine_calls = 0
    total_lmdb_hits = 0
    total_lmdb_misses = 0
    total_flagged = 0
    all_flagged_moves: list[dict] = []
    
    start_time = time.perf_counter()
    
    try:
        for i, game in enumerate(games):
            game_start = time.perf_counter()
            result = analyze_game_cached_parallel(
                game["pgn"], username, pool, max_workers
            )
            game_elapsed = time.perf_counter() - game_start
            
            total_engine_calls += result.get("total_engine_calls", 0)
            total_lmdb_hits += result.get("lmdb_hits", 0)
            total_lmdb_misses += result.get("lmdb_misses", 0)
            total_flagged += result.get("num_flagged", 0)
            
            for fm in result.get("flagged_moves", []):
                fm["game_id"] = game["site_game_id"]
                all_flagged_moves.append(fm)
            
            hit_rate = (result.get("lmdb_hits", 0) / 
                       (result.get("lmdb_hits", 0) + result.get("lmdb_misses", 0)) * 100
                       if (result.get("lmdb_hits", 0) + result.get("lmdb_misses", 0)) > 0 else 0)
            
            print(f"  Game {i+1}/{len(games)}: {game_elapsed:.2f}s, "
                  f"{result.get('total_user_moves', 0)} moves, "
                  f"cache hit {hit_rate:.0f}%, "
                  f"{result.get('num_flagged', 0)} flagged")
    finally:
        pool.shutdown()
        print("  Engine pool shut down.")
    
    total_time = time.perf_counter() - start_time
    
    return {
        "total_time": total_time,
        "total_engine_calls": total_engine_calls,
        "total_lmdb_hits": total_lmdb_hits,
        "total_lmdb_misses": total_lmdb_misses,
        "total_flagged": total_flagged,
        "all_flagged_moves": all_flagged_moves,
    }


def print_summary(label: str, num_games: int, stats: dict[str, Any]) -> None:
    """Print summary statistics."""
    print(f"\n{'='*70}")
    print(f"SUMMARY ({label})")
    print("="*70)
    print(f"Total games analyzed: {num_games}")
    print(f"Total wall-clock time: {stats['total_time']:.2f}s")
    print(f"Average time per game: {stats['total_time']/num_games:.2f}s")
    print(f"Total engine calls: {stats['total_engine_calls']}")
    if stats['total_engine_calls'] > 0:
        print(f"Average time per engine call: {(stats['total_time']/stats['total_engine_calls'])*1000:.1f}ms")
    if "total_lmdb_hits" in stats:
        total_lookups = stats["total_lmdb_hits"] + stats["total_lmdb_misses"]
        hit_rate = stats["total_lmdb_hits"] / total_lookups * 100 if total_lookups > 0 else 0
        print(f"LMDB cache hits: {stats['total_lmdb_hits']} / {total_lookups} ({hit_rate:.1f}%)")
        print(f"Engine calls saved: {stats['total_lmdb_hits']}")
    print(f"Total flagged moves (cp_loss >= {CP_LOSS_THRESHOLD}): {stats['total_flagged']}")


def run_performance_test(username: str = "elizura", num_games: int = 10, mode: str = "all") -> None:
    """Run analysis performance test.
    
    Args:
        username: Lichess username
        num_games: Number of games to analyze
        mode: "sequential", "parallel", "cached", "cached_parallel", or "all"
    """
    load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
    
    conn = get_connection()
    try:
        games = fetch_raw_games(conn, username, limit=num_games)
        print(f"Fetched {len(games)} games for {username}")
        
        if not games:
            print("No games found. Run dump_db.py first to populate raw_games table.")
            return
    finally:
        conn.close()
    
    seq_stats = None
    par_stats = None
    cached_stats = None
    cached_par_stats = None
    
    if mode in ("sequential", "all"):
        print("\n" + "="*70)
        print("SEQUENTIAL MODE (one game at a time)")
        print("="*70)
        seq_stats = run_sequential_test(games, username)
        print_summary("Sequential", len(games), seq_stats)
    
    if mode in ("parallel", "all"):
        print("\n" + "="*70)
        print(f"PARALLEL MODE ({PARALLEL_WORKERS} workers)")
        print("="*70)
        par_stats = run_parallel_test(games, username, PARALLEL_WORKERS)
        print_summary(f"Parallel ({PARALLEL_WORKERS} workers)", len(games), par_stats)
    
    if mode in ("cached", "all"):
        print("\n" + "="*70)
        print("CACHED MODE (LMDB first, sequential Stockfish for misses)")
        print("="*70)
        cached_stats = run_cached_test(games, username)
        print_summary("Cached (LMDB + Stockfish)", len(games), cached_stats)
    
    if mode in ("cached_parallel", "all"):
        print("\n" + "="*70)
        print(f"CACHED+PARALLEL MODE (LMDB first, {PARALLEL_WORKERS} parallel workers for misses)")
        print("="*70)
        cached_par_stats = run_cached_parallel_test(games, username, PARALLEL_WORKERS)
        print_summary(f"Cached+Parallel ({PARALLEL_WORKERS} workers)", len(games), cached_par_stats)
    
    if mode == "all" and seq_stats and par_stats and cached_stats and cached_par_stats:
        print(f"\n{'='*70}")
        print("COMPARISON")
        print("="*70)
        print(f"Sequential time:       {seq_stats['total_time']:.2f}s")
        print(f"Parallel time:         {par_stats['total_time']:.2f}s")
        print(f"Cached time:           {cached_stats['total_time']:.2f}s")
        print(f"Cached+Parallel time:  {cached_par_stats['total_time']:.2f}s")
        
        par_speedup = seq_stats["total_time"] / par_stats["total_time"] if par_stats["total_time"] > 0 else 0
        cached_speedup = seq_stats["total_time"] / cached_stats["total_time"] if cached_stats["total_time"] > 0 else 0
        cached_par_speedup = seq_stats["total_time"] / cached_par_stats["total_time"] if cached_par_stats["total_time"] > 0 else 0
        cached_par_vs_par = par_stats["total_time"] / cached_par_stats["total_time"] if cached_par_stats["total_time"] > 0 else 0
        
        print(f"\nParallel vs Sequential:        {par_speedup:.2f}x faster")
        print(f"Cached vs Sequential:          {cached_speedup:.2f}x faster")
        print(f"Cached+Parallel vs Sequential: {cached_par_speedup:.2f}x faster")
        print(f"Cached+Parallel vs Parallel:   {cached_par_vs_par:.2f}x faster")
        
        total_lookups = cached_par_stats["total_lmdb_hits"] + cached_par_stats["total_lmdb_misses"]
        hit_rate = cached_par_stats["total_lmdb_hits"] / total_lookups * 100 if total_lookups > 0 else 0
        engine_savings = (1 - cached_par_stats["total_engine_calls"] / seq_stats["total_engine_calls"]) * 100 if seq_stats["total_engine_calls"] > 0 else 0
        
        print(f"\nCache hit rate:                {hit_rate:.1f}%")
        print(f"Engine calls saved:            {engine_savings:.1f}%")


if __name__ == "__main__":
    username = "elizura"
    num_games = 250
    mode = "cached_parallel"  # sequential, parallel, cached, cached_parallel, or all
    
    print(f"Stockfish Analysis Performance Test")
    print(f"Username: {username}")
    print(f"Number of games: {num_games}")
    print(f"Mode: {mode}")
    print(f"Depth: {SCAN_DEPTH} (no time limit)")
    print(f"Parallel workers/engines: {PARALLEL_WORKERS}")
    print(f"CP loss threshold: {CP_LOSS_THRESHOLD}")
    print("="*70)
    
    run_performance_test(username, num_games, mode)
