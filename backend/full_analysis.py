"""Full move-by-move Stockfish analysis for chess games."""

import chess
import chess.engine
import chess.pgn
import io
import math
import re
import time
from typing import Optional
from dataclasses import dataclass, asdict

from tactical_detection import detect_tactical_annotation

STOCKFISH_PATH = "/usr/games/stockfish"
DEFAULT_DEPTH = 18
DEFAULT_TIME_MS = 1000  # ~0.2s per position

_CLOCK_RE = re.compile(r"\[%clk\s+([0-9:.]+)\]")
_ELAPSED_RE = re.compile(r"\[%emt\s+([0-9:.]+)\]")


@dataclass
class EvalScore:
    """Engine evaluation score."""
    cp: Optional[int] = None  # Centipawns
    mate: Optional[int] = None  # Mate in N (positive = winning for side to move)
    depth: int = 0


@dataclass
class MoveEvaluation:
    """Evaluation for a single move."""
    ply: int
    san: str
    uci: str
    fen_before: str
    fen_after: str
    eval_before: Optional[dict] = None  # EvalScore as dict
    eval_after: Optional[dict] = None
    best_move_uci: Optional[str] = None
    best_move_san: Optional[str] = None
    pv: list[str] = None  # Principal variation
    classification: Optional[str] = None  # best/excellent/good/inaccuracy/mistake/blunder
    cp_loss: Optional[int] = None  # Centipawn loss vs best move
    tactical: Optional[dict] = None
    clock_seconds: Optional[int] = None
    time_spent_seconds: Optional[int] = None
    time_source: Optional[str] = None  # clock|elapsed|inferred|missing

    def __post_init__(self):
        if self.pv is None:
            self.pv = []


def classify_move(cp_loss: Optional[int]) -> Optional[str]:
    """
    Classify a move based on centipawn loss.
    Based on Lichess standards.
    """
    if cp_loss is None:
        return None
    
    # cp_loss should be positive (how much worse than best move)
    loss = abs(cp_loss)
    
    if loss == 0:
        return "best"
    elif loss < 10:
        return "excellent"
    elif loss < 30:
        return "good"
    elif loss < 100:
        return "inaccuracy"
    elif loss < 300:
        return "mistake"
    else:
        return "blunder"


def _clock_to_seconds(raw_value: str | None) -> int | None:
    if not raw_value:
        return None
    cleaned = raw_value.strip()
    if not cleaned:
        return None

    if ":" not in cleaned:
        try:
            return int(float(cleaned))
        except ValueError:
            return None

    parts = cleaned.split(":")
    try:
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return int(hours * 3600 + minutes * 60 + seconds)
        if len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            return int(minutes * 60 + seconds)
        if len(parts) == 1:
            return int(float(parts[0]))
    except ValueError:
        return None
    return None


def _extract_tag_seconds(comment: str | None, pattern: re.Pattern[str]) -> int | None:
    if not comment:
        return None
    match = pattern.search(comment)
    if not match:
        return None
    return _clock_to_seconds(match.group(1))


def score_to_dict(score: chess.engine.PovScore, perspective: chess.Color) -> dict:
    """
    Convert engine score to dict from given perspective.
    
    IMPORTANT: Always pass chess.WHITE to get stable evaluations.
    - Positive = good for White
    - Negative = good for Black
    This prevents evaluation sign from flipping based on side-to-move.
    """
    pov_score = score.pov(perspective)
    
    if pov_score.is_mate():
        mate_in = pov_score.mate()
        return {"cp": None, "mate": mate_in, "depth": 0}
    
    cp = pov_score.score()
    return {"cp": cp, "mate": None, "depth": 0}


def score_to_cp(score: chess.engine.PovScore, perspective: chess.Color) -> int:
    """
    Convert engine score to centipawns from given perspective. Mate = ±10000.
    
    IMPORTANT: Always pass chess.WHITE to get stable evaluations.
    - Positive = good for White
    - Negative = good for Black
    This prevents evaluation sign from flipping based on side-to-move.
    """
    pov_score = score.pov(perspective)
    
    if pov_score.is_mate():
        mate_in = pov_score.mate()
        if mate_in is not None:
            return 10000 if mate_in > 0 else -10000
        return 0
    
    return pov_score.score() or 0


def run_full_analysis(
    pgn_string: str,
    depth: int = DEFAULT_DEPTH,
    multipv: int = 1,
    time_limit_ms: int = DEFAULT_TIME_MS
) -> dict:
    """
    Run full move-by-move analysis on a game.
    
    Args:
        pgn_string: The PGN of the game
        depth: Analysis depth (default 18)
        multipv: Number of principal variations to compute (1-5)
        time_limit_ms: Time limit per position in milliseconds
        
    Returns:
        Dict with moves, summary, and meta information
    """
    start_time = time.time()
    
    # Parse PGN
    game = chess.pgn.read_game(io.StringIO(pgn_string))
    if not game:
        raise ValueError("Invalid PGN")
    
    # Get game info
    headers = game.headers
    white_player = headers.get("White", "Unknown")
    black_player = headers.get("Black", "Unknown")
    opening_name = headers.get("Opening", headers.get("ECO", "Unknown"))
    
    board = game.board()
    moves_list = list(game.mainline_moves())
    move_nodes = list(game.mainline())
    move_nodes = move_nodes[1:] if move_nodes else []
    
    # Clamp multipv
    multipv = max(1, min(5, multipv))
    
    # Start engine
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    
    try:
        # Note: Do NOT use engine.configure({"MultiPV": multipv}) here
        # The multipv parameter is passed directly to engine.analyse()
        
        move_evaluations: list[dict] = []
        white_cp_losses: list[int] = []
        black_cp_losses: list[int] = []
        previous_clock_white: int | None = None
        previous_clock_black: int | None = None
        
        # We need to evaluate position BEFORE each move to get best move
        # Then compare played move vs best move
        current_board = board.copy()
        
        for ply, move in enumerate(moves_list):
            fen_before = current_board.fen()
            side_to_move = current_board.turn  # WHITE = True, BLACK = False

            # Parse PGN time tags for this ply: [%clk ...] and [%emt ...]
            node = move_nodes[ply] if ply < len(move_nodes) else None
            comment = node.comment if node else None
            clock_seconds = _extract_tag_seconds(comment, _CLOCK_RE)
            elapsed_seconds = _extract_tag_seconds(comment, _ELAPSED_RE)

            actor_is_white = side_to_move == chess.WHITE
            previous_clock = previous_clock_white if actor_is_white else previous_clock_black
            inferred_spent: int | None = None
            if elapsed_seconds is None and clock_seconds is not None and previous_clock is not None:
                # No increment metadata available; infer only when monotonic.
                delta = previous_clock - clock_seconds
                if delta >= 0:
                    inferred_spent = int(delta)

            if clock_seconds is not None:
                if actor_is_white:
                    previous_clock_white = clock_seconds
                else:
                    previous_clock_black = clock_seconds

            time_spent_seconds = elapsed_seconds if elapsed_seconds is not None else inferred_spent
            if elapsed_seconds is not None:
                time_source = "elapsed"
            elif inferred_spent is not None:
                time_source = "inferred"
            elif clock_seconds is not None:
                time_source = "clock"
            else:
                time_source = "missing"
            
            # Analyze position BEFORE the move
            try:
                info_before = engine.analyse(
                    current_board,
                    chess.engine.Limit(depth=depth, time=time_limit_ms / 1000),
                    multipv=multipv
                )
            except Exception:
                info_before = None
            
            # Handle multipv results (returns list when multipv > 1)
            if isinstance(info_before, list):
                main_info = info_before[0] if info_before else {}
                multi_pv_data = []
                for info in info_before:
                    pv_score = info.get("score")
                    pv_moves = info.get("pv", [])
                    if pv_score:
                        # Always use White POV for stable evaluations
                        score_dict = score_to_dict(pv_score, chess.WHITE)
                        score_dict["depth"] = info.get("depth", depth)
                        multi_pv_data.append({
                            **score_dict,
                            "pv": [m.uci() for m in pv_moves[:8]]
                        })
            else:
                main_info = info_before or {}
                multi_pv_data = None
            
            # Extract best move and eval before
            best_move = main_info.get("pv", [None])[0] if main_info else None
            pv_moves = main_info.get("pv", []) if main_info else []
            pv_before_uci = [m.uci() for m in pv_moves[:8]]
            score_before = main_info.get("score") if main_info else None
            
            if score_before:
                # Always use White POV for stable evaluations
                eval_before_dict = score_to_dict(score_before, chess.WHITE)
                eval_before_dict["depth"] = main_info.get("depth", depth)
                eval_before_cp = score_to_cp(score_before, chess.WHITE)
            else:
                eval_before_dict = None
                eval_before_cp = 0
            
            best_move_san = current_board.san(best_move) if best_move else None
            best_move_uci = best_move.uci() if best_move else None
            
            # Get move SAN before pushing
            move_san = current_board.san(move)
            move_uci = move.uci()
            
            # Apply the move
            current_board.push(move)
            fen_after = current_board.fen()
            
            # Analyze position AFTER the move (to get eval after)
            try:
                info_after = engine.analyse(
                    current_board,
                    chess.engine.Limit(depth=depth, time=time_limit_ms / 1000)
                )
            except Exception:
                info_after = None
            
            if isinstance(info_after, list):
                info_after = info_after[0] if info_after else {}
            pv_after_moves = info_after.get("pv", []) if info_after else []
            pv_after_uci = [m.uci() for m in pv_after_moves[:8]]
            
            score_after = info_after.get("score") if info_after else None
            
            if score_after:
                # Always use White POV for stable evaluations
                eval_after_dict = score_to_dict(score_after, chess.WHITE)
                eval_after_dict["depth"] = info_after.get("depth", depth)
                eval_after_cp = score_to_cp(score_after, chess.WHITE)
            else:
                eval_after_dict = None
                eval_after_cp = 0
            
            # Calculate CP loss (how much worse than best move)
            # If played move is best move, cp_loss = 0
            # Otherwise, calculate loss from the mover's perspective:
            # - For White: loss = eval_before - eval_after (White eval should drop if bad move)
            # - For Black: loss = eval_after - eval_before (White eval should rise if Black makes bad move)
            if best_move and move == best_move:
                cp_loss = 0
            elif eval_before_dict and eval_after_dict:
                if side_to_move == chess.WHITE:
                    # White's perspective: loss when eval drops
                    cp_loss = eval_before_cp - eval_after_cp
                else:
                    # Black's perspective: loss when White's eval rises
                    cp_loss = eval_after_cp - eval_before_cp
                cp_loss = max(0, cp_loss)  # Loss should be non-negative
            else:
                cp_loss = None
            
            classification = classify_move(cp_loss)

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
                multi_pv=multi_pv_data,
            )
            
            # Track for accuracy calculation
            if cp_loss is not None:
                if side_to_move == chess.WHITE:
                    white_cp_losses.append(cp_loss)
                else:
                    black_cp_losses.append(cp_loss)
            
            # Build move evaluation
            move_eval = {
                "ply": ply,
                "san": move_san,
                "uci": move_uci,
                "fen_before": fen_before,
                "fen_after": fen_after,
                "eval_before": eval_before_dict,
                "eval_after": eval_after_dict,
                "best_move_uci": best_move_uci,
                "best_move_san": best_move_san,
                "pv": pv_before_uci,
                "classification": classification,
                "cp_loss": cp_loss,
                "clock_seconds": clock_seconds,
                "time_spent_seconds": time_spent_seconds,
                "time_source": time_source,
                "tactical": tactical,
            }
            
            # Add multipv data if available
            if multi_pv_data and len(multi_pv_data) > 1:
                move_eval["multi_pv"] = multi_pv_data
            
            move_evaluations.append(move_eval)
        
        # Calculate accuracy for each side
        def calc_accuracy(losses: list[int]) -> int:
            if not losses:
                return 100
            # Use exponential decay based on average loss
            # Clamp individual losses to 600
            clamped = [min(loss, 600) for loss in losses]
            avg_loss = sum(clamped) / len(clamped)
            # Formula: accuracy = 100 * e^(-avg_loss / 250)
            accuracy = 100 * math.exp(-avg_loss / 250)
            return round(max(0, min(100, accuracy)))
        
        accuracy_white = calc_accuracy(white_cp_losses)
        accuracy_black = calc_accuracy(black_cp_losses)
        
        # Get opening evaluation (after move 10 or final position if shorter)
        opening_ply = min(20, len(move_evaluations))  # After move 10 (ply 20)
        if opening_ply > 0 and move_evaluations:
            opening_eval = move_evaluations[opening_ply - 1].get("eval_after")
        else:
            opening_eval = None
        
        elapsed_ms = int((time.time() - start_time) * 1000)
        
        return {
            "moves": move_evaluations,
            "summary": {
                "accuracy_white": accuracy_white,
                "accuracy_black": accuracy_black,
                "opening_name": opening_name,
                "opening_eval": opening_eval,
                "total_moves": len(moves_list),
                "white_player": white_player,
                "black_player": black_player,
            },
            "meta": {
                "engine": "stockfish",
                "depth": depth,
                "multipv": multipv,
                "time_per_position_ms": time_limit_ms,
                "total_time_ms": elapsed_ms,
                "positions_analyzed": len(moves_list) * 2,  # before + after each
            }
        }
        
    finally:
        engine.quit()


def evaluate_position(
    fen: str,
    depth: int = DEFAULT_DEPTH,
    multipv: int = 1,
    time_limit_ms: int = DEFAULT_TIME_MS
) -> dict:
    """
    Evaluate a single position.
    
    Args:
        fen: Position in FEN notation
        depth: Analysis depth
        multipv: Number of principal variations
        time_limit_ms: Time limit in milliseconds
        
    Returns:
        Dict with eval, pv, and multipv data
    """
    board = chess.Board(fen)
    
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    
    try:
        # Note: Do NOT use engine.configure({"MultiPV": multipv}) here
        # The multipv parameter is passed directly to engine.analyse()
        
        info = engine.analyse(
            board,
            chess.engine.Limit(depth=depth, time=time_limit_ms / 1000),
            multipv=multipv
        )
        
        # Handle multipv results
        if isinstance(info, list):
            results = []
            for line_info in info:
                score = line_info.get("score")
                pv = line_info.get("pv", [])
                if score:
                    # Always use White POV for stable evaluations
                    score_dict = score_to_dict(score, chess.WHITE)
                    score_dict["depth"] = line_info.get("depth", depth)
                    
                    # Convert PV to SAN
                    pv_san = []
                    temp_board = board.copy()
                    for m in pv[:10]:
                        try:
                            pv_san.append(temp_board.san(m))
                            temp_board.push(m)
                        except:
                            break
                    
                    results.append({
                        **score_dict,
                        "pv_uci": [m.uci() for m in pv[:10]],
                        "pv_san": pv_san,
                    })
            
            if results:
                return {
                    "eval": results[0],
                    "multipv": results if len(results) > 1 else None,
                    "fen": fen,
                }
        else:
            score = info.get("score")
            pv = info.get("pv", [])
            
            if score:
                # Always use White POV for stable evaluations
                score_dict = score_to_dict(score, chess.WHITE)
                score_dict["depth"] = info.get("depth", depth)
                
                # Convert PV to SAN
                pv_san = []
                temp_board = board.copy()
                for m in pv[:10]:
                    try:
                        pv_san.append(temp_board.san(m))
                        temp_board.push(m)
                    except:
                        break
                
                return {
                    "eval": {
                        **score_dict,
                        "pv_uci": [m.uci() for m in pv[:10]],
                        "pv_san": pv_san,
                    },
                    "multipv": None,
                    "fen": fen,
                }
        
        return {"eval": None, "multipv": None, "fen": fen}
        
    finally:
        engine.quit()
