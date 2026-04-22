"""Lightweight quick-scan analysis for batch tactical problem detection."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import chess
import chess.pgn

from repository.db import (
    create_scan_job,
    get_active_scan_job,
    get_connection,
    get_games_for_insights,
    get_player_insights,
    get_quick_scan_results,
    get_scanned_game_ids,
    update_scan_job,
    upsert_game_quick_scan,
    upsert_player_insights,
)
from services.full_analysis import (
    _analyse_fen_chunk,
    _compute_cp_loss,
    classify_move,
    score_to_cp,
)
from services.insights_aggregate import aggregate_scan_features
from utils.insights_utils import phase_for_ply, utc_now_iso
from utils.quick_scan_constants import (
    MAX_CONCURRENT_SCANS,
    QUICK_SCAN_CONCURRENCY,
    QUICK_SCAN_CP_THRESHOLD,
    QUICK_SCAN_DEPTH,
    QUICK_SCAN_MAX_GAMES,
    QUICK_SCAN_POSITION_WORKERS,
    QUICK_SCAN_TIME_MS,
)
from services.tactical_detection import detect_tactical_annotation

logger = logging.getLogger(__name__)

_SCAN_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_SCANS)


def run_quick_scan_single(
    game_row: dict[str, Any],
    username: str,
) -> dict[str, Any]:
    """Run a quick low-depth scan on a single game, analyzing only the user's moves.

    Uses a three-phase approach for performance:
    1. Collect all FEN positions that need analysis (no engine calls)
    2. Batch-analyze all unique FENs in parallel using ThreadPoolExecutor
    3. Process results using the pre-computed analysis lookup

    Returns a dict with 'problems', 'move_stats', and 'summary' keys.
    """
    pgn = game_row.get("pgn") or ""
    if not pgn.strip():
        return _empty_scan_result()

    game = chess.pgn.read_game(io.StringIO(pgn))
    if not game:
        return _empty_scan_result()

    color = (game_row.get("color") or "white").lower()
    user_is_white = color == "white"

    board = game.board()
    moves_list = list(game.mainline_moves())
    if not moves_list:
        return _empty_scan_result()

    opening_end_ply = 20
    total_plies = len(moves_list)
    endgame_start_ply = max(40, int(total_plies * 0.7)) if total_plies > 30 else None

    # -------------------------------------------------------------------------
    # PHASE 1: Collect all FEN positions that need analysis (no engine calls)
    # -------------------------------------------------------------------------
    user_moves_data: list[dict[str, Any]] = []
    fens_to_analyze: set[str] = set()
    current_board = board.copy()

    for ply, move in enumerate(moves_list):
        is_white_move = ply % 2 == 0
        is_user_move = (is_white_move and user_is_white) or (
            not is_white_move and not user_is_white
        )

        fen_before = current_board.fen()
        side_to_move = current_board.turn
        move_uci = move.uci()
        move_san = current_board.san(move)

        current_board.push(move)

        if not is_user_move:
            continue

        fen_after = current_board.fen()
        is_checkmate = current_board.is_checkmate()
        is_game_over = current_board.is_game_over()

        user_moves_data.append({
            "ply": ply,
            "move": move,
            "move_uci": move_uci,
            "move_san": move_san,
            "fen_before": fen_before,
            "fen_after": fen_after,
            "side_to_move": side_to_move,
            "is_checkmate": is_checkmate,
            "is_game_over": is_game_over,
        })

        fens_to_analyze.add(fen_before)
        if not is_checkmate and not is_game_over:
            fens_to_analyze.add(fen_after)

    if not user_moves_data:
        return _empty_scan_result()

    # -------------------------------------------------------------------------
    # PHASE 2: Batch analyze all unique FENs in parallel
    # -------------------------------------------------------------------------
    unique_fens = list(fens_to_analyze)
    info_by_fen: dict[str, Any] = {}

    if unique_fens:
        worker_count = min(QUICK_SCAN_POSITION_WORKERS, len(unique_fens))
        if worker_count <= 1:
            info_by_fen = _analyse_fen_chunk(
                unique_fens, QUICK_SCAN_DEPTH, QUICK_SCAN_TIME_MS, 1
            )
        else:
            chunk_size = math.ceil(len(unique_fens) / worker_count)
            chunks = [
                unique_fens[i : i + chunk_size]
                for i in range(0, len(unique_fens), chunk_size)
            ]
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = [
                    executor.submit(
                        _analyse_fen_chunk,
                        chunk,
                        QUICK_SCAN_DEPTH,
                        QUICK_SCAN_TIME_MS,
                        1,
                    )
                    for chunk in chunks
                    if chunk
                ]
                for future in futures:
                    try:
                        info_by_fen.update(future.result())
                    except Exception:
                        continue

    # -------------------------------------------------------------------------
    # PHASE 3: Process results using pre-computed analysis lookup
    # -------------------------------------------------------------------------
    problems: list[dict[str, Any]] = []
    phase_cp_losses: dict[str, list[int]] = {
        "opening": [],
        "middlegame": [],
        "endgame": [],
    }
    blunders = 0
    mistakes = 0
    inaccuracies = 0
    total_user_moves = len(user_moves_data)

    for move_data in user_moves_data:
        ply = move_data["ply"]
        move = move_data["move"]
        move_uci = move_data["move_uci"]
        move_san = move_data["move_san"]
        fen_before = move_data["fen_before"]
        fen_after = move_data["fen_after"]
        side_to_move = move_data["side_to_move"]
        is_checkmate = move_data["is_checkmate"]
        is_game_over = move_data["is_game_over"]

        info_before = info_by_fen.get(fen_before)
        if info_before is None:
            continue

        if isinstance(info_before, list):
            info_before = info_before[0] if info_before else {}

        score_before = info_before.get("score")
        pv_before = info_before.get("pv", [])
        best_move_obj = pv_before[0] if pv_before else None
        best_move_uci = best_move_obj.uci() if best_move_obj else None

        eval_before_cp = score_to_cp(score_before, chess.WHITE) if score_before else 0
        eval_before_dict = (
            {"cp": eval_before_cp, "mate": None}
            if score_before
            else None
        )
        if score_before and score_before.pov(chess.WHITE).is_mate():
            mate_val = score_before.pov(chess.WHITE).mate()
            eval_before_dict = {"cp": None, "mate": mate_val}

        if is_checkmate or is_game_over:
            continue

        info_after = info_by_fen.get(fen_after)
        if info_after is None:
            continue

        if isinstance(info_after, list):
            info_after = info_after[0] if info_after else {}

        score_after = info_after.get("score")
        pv_after = info_after.get("pv", [])
        pv_after_uci = [m.uci() for m in pv_after[:8]] if pv_after else []

        eval_after_cp = score_to_cp(score_after, chess.WHITE) if score_after else 0
        eval_after_dict = (
            {"cp": eval_after_cp, "mate": None}
            if score_after
            else None
        )
        if score_after and score_after.pov(chess.WHITE).is_mate():
            mate_val = score_after.pov(chess.WHITE).mate()
            eval_after_dict = {"cp": None, "mate": mate_val}

        cp_loss = _compute_cp_loss(
            move_uci,
            best_move_uci,
            eval_before_dict,
            eval_after_dict,
            eval_before_cp,
            eval_after_cp,
            side_to_move,
        )

        if cp_loss is None:
            continue

        phase = phase_for_ply(ply + 1, opening_end_ply, endgame_start_ply)
        phase_cp_losses[phase].append(cp_loss)

        classification = classify_move(cp_loss)
        if classification == "blunder":
            blunders += 1
        elif classification == "mistake":
            mistakes += 1
        elif classification == "inaccuracy":
            inaccuracies += 1

        if classification not in ("blunder"):
            continue

        if cp_loss < QUICK_SCAN_CP_THRESHOLD:
            continue

        pv_before_uci = [m.uci() for m in pv_before[:8]] if pv_before else []
        best_move_san = None
        if best_move_obj is not None:
            try:
                best_move_san = chess.Board(fen_before).san(best_move_obj)
            except Exception:
                pass

        tactic_type = None
        tactic_types: list[str] = []
        try:
            tactical = detect_tactical_annotation(
                fen_before=fen_before,
                fen_after=fen_after,
                played_uci=move_uci,
                best_move_uci=best_move_uci,
                pv_before_uci=pv_before_uci,
                pv_after_uci=pv_after_uci,
                classification=classification,
                cp_loss=cp_loss,
                eval_before=eval_before_dict,
                eval_after=eval_after_dict,
            )
            if tactical.get("tactic_detected"):
                tactic_type = tactical.get("tactic_type")
                tactic_types = tactical.get("tactic_types") or (
                    [tactic_type] if tactic_type else []
                )
        except Exception:
            pass

        problems.append({
            "ply": ply,
            "san": move_san,
            "classification": classification,
            "cp_loss": cp_loss,
            "phase": phase,
            "fen_before": fen_before,
            "best_move_san": best_move_san,
            "best_move_uci": best_move_uci,
            "tactic_type": tactic_type,
            "tactic_types": tactic_types,
        })

    return {
        "problems": problems,
        "move_stats": {
            "total_user_moves": total_user_moves,
            "phase_cp_losses": {k: v for k, v in phase_cp_losses.items()},
        },
        "summary": {
            "blunders": blunders,
            "mistakes": mistakes,
            "inaccuracies": inaccuracies,
        },
    }


def _empty_scan_result() -> dict[str, Any]:
    return {
        "problems": [],
        "move_stats": {
            "total_user_moves": 0,
            "phase_cp_losses": {"opening": [], "middlegame": [], "endgame": []},
        },
        "summary": {"blunders": 0, "mistakes": 0, "inaccuracies": 0},
    }


async def run_quick_scan_batch(
    job_id: str,
    username: str,
    site: str = "all",
) -> None:
    """Scan all unscanned games for a username, saving results progressively."""
    canonical = username.strip().lower()
    started_at = utc_now_iso()

    conn = get_connection()
    try:
        update_scan_job(conn, job_id, status="running", started_at=started_at)
        conn.commit()
    finally:
        conn.close()

    try:
        conn = get_connection()
        try:
            games = get_games_for_insights(
                conn,
                username=canonical,
                site=site,
                limit=QUICK_SCAN_MAX_GAMES,
            )
            already_scanned = get_scanned_game_ids(conn, canonical, site)
        finally:
            conn.close()

        to_scan = [
            g for g in games
            if (g["site"], g["site_game_id"]) not in already_scanned
        ]

        if not to_scan:
            conn = get_connection()
            try:
                update_scan_job(
                    conn, job_id,
                    status="completed",
                    games_done=0,
                    finished_at=utc_now_iso(),
                )
                conn.commit()
            finally:
                conn.close()
            await _merge_and_update_insights(canonical, site, job_id)
            return

        conn = get_connection()
        try:
            update_scan_job(conn, job_id, games_done=0)
            conn.commit()
        finally:
            conn.close()

        semaphore = asyncio.Semaphore(QUICK_SCAN_CONCURRENCY)
        done_count = 0

        async def _scan_one(game: dict[str, Any]) -> None:
            nonlocal done_count
            async with semaphore:
                try:
                    result = await asyncio.to_thread(
                        run_quick_scan_single, game, canonical
                    )
                    problems_str = json.dumps(result)
                    summary_str = json.dumps(result.get("summary", {}))

                    conn2 = get_connection()
                    try:
                        upsert_game_quick_scan(
                            conn2,
                            username=canonical,
                            site=game["site"],
                            site_game_id=game["site_game_id"],
                            problems_json=problems_str,
                            summary_json=summary_str,
                        )
                        conn2.commit()
                    finally:
                        conn2.close()
                except Exception:
                    logger.exception(
                        "Quick scan failed for game %s/%s",
                        game.get("site"),
                        game.get("site_game_id"),
                    )

                done_count += 1
                if done_count % 5 == 0 or done_count == len(to_scan):
                    conn3 = get_connection()
                    try:
                        update_scan_job(conn3, job_id, games_done=done_count)
                        conn3.commit()
                    finally:
                        conn3.close()

        tasks = [asyncio.create_task(_scan_one(game)) for game in to_scan]
        await asyncio.gather(*tasks)

        conn = get_connection()
        try:
            update_scan_job(
                conn, job_id,
                status="completed",
                games_done=done_count,
                finished_at=utc_now_iso(),
            )
            conn.commit()
        finally:
            conn.close()

        await _merge_and_update_insights(canonical, site, job_id)

    except Exception as exc:
        logger.exception("Quick scan batch failed: %s", exc)
        conn = get_connection()
        try:
            update_scan_job(
                conn, job_id,
                status="failed",
                error=str(exc),
                finished_at=utc_now_iso(),
            )
            conn.commit()
        finally:
            conn.close()


async def _merge_and_update_insights(
    username: str,
    site: str,
    job_id: str,
) -> None:
    """Merge scan aggregates into the player_insights row."""
    conn = get_connection()
    try:
        scan_rows = get_quick_scan_results(conn, username, site)
        existing = get_player_insights(conn, username, site)
    finally:
        conn.close()

    if not scan_rows:
        return

    scan_agg = aggregate_scan_features(scan_rows)

    features = (existing.get("features") or {}) if existing else {}
    features["performance"] = features.get("performance", {})
    features["performance"]["phase"] = scan_agg.get("phase_performance", {})
    features["recurring_themes"] = scan_agg.get("theme_items", [])
    features["time_pressure"] = features.get("time_pressure", {})
    features["time_pressure"]["blunders_total"] = scan_agg.get("total_blunders", 0)
    features["time_pressure"]["blunders_from_scan"] = True
    features["scan_aggregate"] = scan_agg

    conn = get_connection()
    try:
        if existing:
            upsert_player_insights(
                conn,
                username=username,
                site=site,
                status="complete",
                feature_version=existing.get("feature_version", ""),
                narrative_version=existing.get("narrative_version", ""),
                coverage=existing.get("coverage") or {},
                features=features,
                fact_map=existing.get("fact_map") or {},
                narrative=existing.get("narrative") or {},
                source_job_id=job_id,
            )
        conn.commit()
    finally:
        conn.close()


def schedule_quick_scan(
    username: str,
    site: str = "all",
) -> dict[str, Any]:
    """Create a quick-scan job and fire-and-forget the background task."""
    canonical = username.strip().lower()
    conn = get_connection()
    try:
        active = get_active_scan_job(conn, canonical, site)
        if active:
            return {"scheduled": False, "job": active}

        games = get_games_for_insights(
            conn,
            username=canonical,
            site=site,
            limit=QUICK_SCAN_MAX_GAMES,
        )
        already_scanned = get_scanned_game_ids(conn, canonical, site)
        to_scan = [
            g for g in games
            if (g["site"], g["site_game_id"]) not in already_scanned
        ]
        total = len(to_scan)

        if total == 0:
            return {"scheduled": False, "reason": "no_new_games"}

        job_id = str(uuid.uuid4())
        create_scan_job(conn, job_id, canonical, site, total)
        conn.commit()
    finally:
        conn.close()

    from tasks import run_scan
    run_scan.delay(job_id, canonical, site)

    return {
        "scheduled": True,
        "job": {"id": job_id, "status": "queued", "total_games": total},
    }
