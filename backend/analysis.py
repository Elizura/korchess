"""Lightweight Stockfish analysis for chess games."""

import chess
import chess.engine
import chess.pgn
import io
import math
from typing import Optional

STOCKFISH_PATH = "/usr/games/stockfish"
TIME_PER_POSITION_MS = 150  # ~0.15s per position


def run_lightweight_analysis(pgn_string: str, user_color: str) -> dict:
    """
    Run lightweight analysis on a game.
    Evaluates: opening exit (move 10), checkpoints (20, 30, 40),
    and finds biggest mistake among checkpoint moves.
    
    Args:
        pgn_string: The PGN of the game
        user_color: "white" or "black" - the user's color in the game
        
    Returns:
        Analysis result dict with opening_eval_cp, checkpoints, biggest_mistake, accuracy, meta
    """
    # Parse PGN
    game = chess.pgn.read_game(io.StringIO(pgn_string))
    if not game:
        raise ValueError("Invalid PGN")
    
    board = game.board()
    moves = list(game.mainline_moves())
    
    # Determine sign flip: Stockfish gives White perspective
    # If user is Black, flip so positive = good for user
    sign = 1 if user_color == "white" else -1
    
    # Checkpoints to evaluate (fullmoves)
    checkpoint_fullmoves = [10, 20, 30, 40]
    
    # Build positions at each checkpoint
    positions = []
    current_board = board.copy()
    opening_exit_added = False
    
    for ply, move in enumerate(moves):
        fullmove = (ply // 2) + 1
        is_user_move = (ply % 2 == 0 and user_color == "white") or \
                       (ply % 2 == 1 and user_color == "black")
        
        # Record position BEFORE move if this is a checkpoint user move
        if fullmove in checkpoint_fullmoves and is_user_move:
            positions.append({
                "ply": ply,
                "fullmove": fullmove,
                "fen_before": current_board.fen(),
                "move_uci": move.uci(),
                "move_san": current_board.san(move),
                "type": "checkpoint"
            })
        
        current_board.push(move)
        
        # Record position AFTER move 10 for opening eval
        if fullmove == 10 and ply == 19 and not opening_exit_added:
            positions.append({
                "ply": ply + 1,
                "fullmove": 10,
                "fen_after": current_board.fen(),
                "type": "opening_exit"
            })
            opening_exit_added = True
    
    # If game ended before move 10, use final position as opening exit
    final_ply = len(moves)
    final_fullmove = (final_ply // 2) + 1
    if not opening_exit_added:
        positions.append({
            "ply": final_ply,
            "fullmove": final_fullmove,
            "fen_after": current_board.fen(),
            "type": "opening_exit"
        })
    
    # Run Stockfish
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    
    try:
        results = {
            "opening_eval_cp": None,
            "checkpoints": [],
            "biggest_mistake": None,
            "accuracy": 100,
            "meta": {"time_ms": TIME_PER_POSITION_MS, "positions_analyzed": 0}
        }
        
        deltas = []
        
        for pos in positions:
            if pos["type"] == "opening_exit":
                # Evaluate opening exit position
                eval_board = chess.Board(pos["fen_after"])
                info = engine.analyse(eval_board, chess.engine.Limit(time=TIME_PER_POSITION_MS/1000))
                eval_cp = _score_to_cp(info["score"], sign)
                results["opening_eval_cp"] = eval_cp
                results["checkpoints"].append({
                    "fullmove": pos["fullmove"],
                    "ply": pos["ply"],
                    "eval_cp": eval_cp,
                    "best_uci": None
                })
                results["meta"]["positions_analyzed"] += 1
                
            elif pos["type"] == "checkpoint":
                # Evaluate before and after user's move
                board_before = chess.Board(pos["fen_before"])
                
                # Get eval and best move BEFORE
                info_before = engine.analyse(
                    board_before, 
                    chess.engine.Limit(time=TIME_PER_POSITION_MS/1000)
                )
                eval_before = _score_to_cp(info_before["score"], sign)
                best_move = info_before.get("pv", [None])[0]
                best_san = board_before.san(best_move) if best_move else None
                
                # Apply user's move and evaluate AFTER
                board_after = board_before.copy()
                board_after.push(chess.Move.from_uci(pos["move_uci"]))
                info_after = engine.analyse(
                    board_after,
                    chess.engine.Limit(time=TIME_PER_POSITION_MS/1000)
                )
                eval_after = _score_to_cp(info_after["score"], sign)
                
                delta = eval_after - eval_before
                deltas.append(delta)
                
                results["checkpoints"].append({
                    "fullmove": pos["fullmove"],
                    "ply": pos["ply"],
                    "eval_cp": eval_after,
                    "best_uci": best_move.uci() if best_move else None
                })
                results["meta"]["positions_analyzed"] += 2
                
                # Track biggest mistake
                if delta < -20:  # Only count as mistake if meaningful
                    if results["biggest_mistake"] is None or delta < results["biggest_mistake"]["delta_cp"]:
                        results["biggest_mistake"] = {
                            "fullmove": pos["fullmove"],
                            "ply": pos["ply"],
                            "played_san": pos["move_san"],
                            "best_san": best_san,
                            "delta_cp": delta,
                            "eval_before_cp": eval_before,
                            "eval_after_cp": eval_after
                        }
        
        # Calculate accuracy
        if deltas:
            penalties = [min(max(-d, 0), 600) for d in deltas]
            avg_penalty = sum(penalties) / len(penalties)
            results["accuracy"] = round(100 * math.exp(-avg_penalty / 250))
        
        return results
        
    finally:
        engine.quit()


def _score_to_cp(score: chess.engine.PovScore, sign: int) -> int:
    """Convert engine score to centipawns from user perspective."""
    if score.is_mate():
        mate_in = score.white().mate()
        if mate_in is not None:
            if mate_in > 0:
                return sign * 10000
            else:
                return sign * -10000
        return 0
    cp = score.white().score()
    return sign * cp if cp is not None else 0
