"""Full move-by-move Stockfish analysis for chess games."""

import atexit
import chess
import chess.engine
import chess.pgn
import os
import io
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from dataclasses import dataclass

from tactical_detection import detect_tactical_annotation

STOCKFISH_PATH = "/usr/games/stockfish"
DEFAULT_DEPTH = 18
DEFAULT_TIME_MS = 1000  # ~0.2s per position
_CPU_COUNT = max(1, os.cpu_count() or 1)
FULL_ANALYSIS_POSITION_WORKERS = max(
    1,
    min(4, int(os.environ.get("FULL_ANALYSIS_POSITION_WORKERS", "1"))),
)
FULL_ANALYSIS_ENGINE_THREADS = max(
    1,
    int(os.environ.get("FULL_ANALYSIS_ENGINE_THREADS", "2" if _CPU_COUNT >= 4 else "1")),
)
FULL_ANALYSIS_ENGINE_HASH_MB = max(16, int(os.environ.get("FULL_ANALYSIS_ENGINE_HASH_MB", "128")))

_CLOCK_RE = re.compile(r"\[%clk\s+([0-9:.]+)\]")
_ELAPSED_RE = re.compile(r"\[%emt\s+([0-9:.]+)\]")
_ENGINE_LOCAL = threading.local()
_ENGINE_REGISTRY_LOCK = threading.Lock()
_ENGINE_REGISTRY: set[chess.engine.SimpleEngine] = set()


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
    review_tag: Optional[str] = None

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


REVIEW_BASE_CLASSIFICATIONS = {"best", "excellent", "good", "inaccuracy", "mistake", "blunder"}
REVIEW_TAG_ORDER = [
    "brilliant",
    "great",
    "book",
    "best",
    "excellent",
    "good",
    "inaccuracy",
    "mistake",
    "miss",
    "blunder",
]
REVIEW_LABELS_VERSION = "v1"


def _empty_review_counts() -> dict[str, int]:
    return {tag: 0 for tag in REVIEW_TAG_ORDER}


def _cp_for_mover(cp_white: int | None, mover_is_white: bool) -> int | None:
    if cp_white is None:
        return None
    return cp_white if mover_is_white else -cp_white


def _is_tactical_miss(tactical: dict[str, Any] | None, cp_loss: int | None) -> bool:
    if not isinstance(tactical, dict):
        return False

    missed_forced_mate = tactical.get("tactic_type") == "MISSED_FORCED_MATE"
    if missed_forced_mate:
        return True

    missed_move_uci = tactical.get("missed_move_uci")
    if isinstance(missed_move_uci, str) and missed_move_uci.strip():
        return isinstance(cp_loss, int) and cp_loss >= 120

    return False


def _derive_review_tag(
    *,
    ply: int,
    classification: str | None,
    cp_loss: int | None,
    tactical: dict[str, Any] | None,
    eval_before_cp: int | None,
    eval_after_cp: int | None,
    mover_is_white: bool,
    opening_ply_count: int | None,
) -> str | None:
    base_class = classification.lower() if isinstance(classification, str) else None
    if base_class not in REVIEW_BASE_CLASSIFICATIONS:
        base_class = None

    if isinstance(opening_ply_count, int) and opening_ply_count > 0 and (ply + 1) <= opening_ply_count:
        return "book"

    eval_before_for_mover = _cp_for_mover(eval_before_cp, mover_is_white)
    eval_after_for_mover = _cp_for_mover(eval_after_cp, mover_is_white)
    delta_cp_for_mover: int | None = None
    if eval_before_for_mover is not None and eval_after_for_mover is not None:
        delta_cp_for_mover = eval_after_for_mover - eval_before_for_mover

    is_great = (
        base_class == "best"
        and delta_cp_for_mover is not None
        and eval_before_cp is not None
        and delta_cp_for_mover >= 100
        and abs(eval_before_cp) <= 200
    )

    material_outcome = tactical.get("material_outcome") if isinstance(tactical, dict) else None
    material_cp_for_mover = (
        material_outcome.get("cp_net_for_mover")
        if isinstance(material_outcome, dict)
        else None
    )
    tactical_detected = bool(tactical.get("tactic_detected")) if isinstance(tactical, dict) else False
    is_brilliant = (
        is_great
        and tactical_detected
        and isinstance(material_cp_for_mover, int)
        and material_cp_for_mover <= -250
        and delta_cp_for_mover is not None
        and delta_cp_for_mover >= 0
    )

    if is_brilliant:
        return "brilliant"
    if is_great:
        return "great"

    if base_class in {"inaccuracy", "mistake", "blunder"} and _is_tactical_miss(tactical, cp_loss):
        return "miss"

    return base_class


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


def _register_engine(engine: chess.engine.SimpleEngine) -> None:
    with _ENGINE_REGISTRY_LOCK:
        _ENGINE_REGISTRY.add(engine)


def _unregister_engine(engine: chess.engine.SimpleEngine) -> None:
    with _ENGINE_REGISTRY_LOCK:
        _ENGINE_REGISTRY.discard(engine)


def _create_engine() -> chess.engine.SimpleEngine:
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    try:
        available = getattr(engine, "options", {})
        options: dict[str, Any] = {}
        if "Threads" in available:
            options["Threads"] = FULL_ANALYSIS_ENGINE_THREADS
        if "Hash" in available:
            options["Hash"] = FULL_ANALYSIS_ENGINE_HASH_MB
        if "UCI_AnalyseMode" in available:
            options["UCI_AnalyseMode"] = True
        if options:
            engine.configure(options)
    except Exception:
        # Keep analysis resilient even if a UCI option is unavailable.
        pass
    _register_engine(engine)
    return engine


def _close_engine(engine: chess.engine.SimpleEngine | None) -> None:
    if engine is None:
        return
    try:
        engine.quit()
    except Exception:
        pass
    _unregister_engine(engine)


def _close_thread_engine() -> None:
    engine = getattr(_ENGINE_LOCAL, "engine", None)
    if engine is None:
        return
    _close_engine(engine)
    _ENGINE_LOCAL.engine = None


def _get_thread_engine() -> chess.engine.SimpleEngine:
    engine = getattr(_ENGINE_LOCAL, "engine", None)
    if engine is None:
        engine = _create_engine()
        _ENGINE_LOCAL.engine = engine
    return engine


@atexit.register
def _shutdown_engines() -> None:
    with _ENGINE_REGISTRY_LOCK:
        engines = list(_ENGINE_REGISTRY)
        _ENGINE_REGISTRY.clear()
    for engine in engines:
        try:
            engine.quit()
        except Exception:
            pass


def _analyse_with_recovery(
    board: chess.Board,
    depth: int,
    time_limit_ms: int,
    multipv: int = 1,
) -> Any:
    limit = chess.engine.Limit(depth=depth, time=time_limit_ms / 1000)
    multipv = max(1, min(5, multipv))
    engine = _get_thread_engine()
    try:
        return engine.analyse(board, limit, multipv=multipv)
    except Exception:
        _close_thread_engine()
        engine = _get_thread_engine()
        return engine.analyse(board, limit, multipv=multipv)


def _analyse_fen_with_recovery(
    fen: str,
    depth: int,
    time_limit_ms: int,
    multipv: int,
) -> Any:
    try:
        board = chess.Board(fen)
    except Exception:
        return None
    try:
        return _analyse_with_recovery(board, depth, time_limit_ms, multipv=multipv)
    except Exception:
        return None


def _analyse_fen_chunk(
    fens: list[str],
    depth: int,
    time_limit_ms: int,
    multipv: int,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    try:
        for fen in fens:
            results[fen] = _analyse_fen_with_recovery(fen, depth, time_limit_ms, multipv)
    finally:
        # Worker threads are short-lived; close their engine process explicitly.
        _close_thread_engine()
    return results


def _analyse_positions_for_game(
    position_fens: list[str],
    depth: int,
    time_limit_ms: int,
    multipv: int,
) -> tuple[list[Any], int]:
    if not position_fens:
        return [], 0

    # Preserve order while deduplicating repeated positions.
    unique_fens = list(dict.fromkeys(position_fens))
    unique_count = len(unique_fens)

    worker_count = min(FULL_ANALYSIS_POSITION_WORKERS, unique_count)
    if worker_count <= 1:
        info_by_fen = {
            fen: _analyse_fen_with_recovery(fen, depth, time_limit_ms, multipv)
            for fen in unique_fens
        }
    else:
        chunk_size = math.ceil(unique_count / worker_count)
        chunks = [unique_fens[i : i + chunk_size] for i in range(0, unique_count, chunk_size)]
        info_by_fen: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(_analyse_fen_chunk, chunk, depth, time_limit_ms, multipv)
                for chunk in chunks
                if chunk
            ]
            for future in futures:
                try:
                    info_by_fen.update(future.result())
                except Exception:
                    continue

    ordered_infos = [info_by_fen.get(fen) for fen in position_fens]
    return ordered_infos, unique_count


def _normalize_analysis_payload(info: Any, depth: int) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    if isinstance(info, list):
        main_info = info[0] if info else {}
        multi_pv_data = []
        for line in info:
            if not isinstance(line, dict):
                continue
            pv_score = line.get("score")
            pv_moves = line.get("pv", [])
            if pv_score:
                score_dict = score_to_dict(pv_score, chess.WHITE)
                score_dict["depth"] = line.get("depth", depth)
                multi_pv_data.append(
                    {
                        **score_dict,
                        "pv": [m.uci() for m in pv_moves[:8]],
                    }
                )
        return (main_info if isinstance(main_info, dict) else {}), (multi_pv_data or None)
    if isinstance(info, dict):
        return info, None
    return {}, None


def _extract_eval_components(main_info: dict[str, Any], depth: int) -> tuple[dict[str, Any] | None, int, list[str], chess.Move | None]:
    pv_moves = main_info.get("pv", []) if isinstance(main_info, dict) else []
    pv_uci = [move.uci() for move in pv_moves[:8]]
    best_move = pv_moves[0] if pv_moves else None
    score = main_info.get("score") if isinstance(main_info, dict) else None
    if score:
        eval_dict = score_to_dict(score, chess.WHITE)
        eval_dict["depth"] = main_info.get("depth", depth)
        eval_cp = score_to_cp(score, chess.WHITE)
    else:
        eval_dict = None
        eval_cp = 0
    return eval_dict, eval_cp, pv_uci, best_move if isinstance(best_move, chess.Move) else None


def run_full_analysis(
    pgn_string: str,
    depth: int = DEFAULT_DEPTH,
    multipv: int = 1,
    time_limit_ms: int = DEFAULT_TIME_MS,
    opening_ply_count: int | None = None,
) -> dict:
    """
    Run full move-by-move analysis on a game.
    
    Args:
        pgn_string: The PGN of the game
        depth: Analysis depth (default 18)
        multipv: Number of principal variations to compute (1-5)
        time_limit_ms: Time limit per position in milliseconds
        opening_ply_count: Opening-book ply depth for canonical `book` labeling
        
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

    move_evaluations: list[dict] = []
    white_cp_losses: list[int] = []
    black_cp_losses: list[int] = []
    review_counts_white = _empty_review_counts()
    review_counts_black = _empty_review_counts()
    previous_clock_white: int | None = None
    previous_clock_black: int | None = None
    current_board = board.copy()
    move_records: list[dict[str, Any]] = []
    position_fens: list[str] = [current_board.fen()]

    for ply, move in enumerate(moves_list):
        fen_before = current_board.fen()
        side_to_move = current_board.turn

        node = move_nodes[ply] if ply < len(move_nodes) else None
        comment = node.comment if node else None
        clock_seconds = _extract_tag_seconds(comment, _CLOCK_RE)
        elapsed_seconds = _extract_tag_seconds(comment, _ELAPSED_RE)

        actor_is_white = side_to_move == chess.WHITE
        previous_clock = previous_clock_white if actor_is_white else previous_clock_black
        inferred_spent: int | None = None
        if elapsed_seconds is None and clock_seconds is not None and previous_clock is not None:
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

        move_san = current_board.san(move)
        move_uci = move.uci()
        current_board.push(move)
        fen_after = current_board.fen()
        position_fens.append(fen_after)

        move_records.append(
            {
                "ply": ply,
                "fen_before": fen_before,
                "fen_after": fen_after,
                "side_to_move": side_to_move,
                "move_san": move_san,
                "move_uci": move_uci,
                "clock_seconds": clock_seconds,
                "time_spent_seconds": time_spent_seconds,
                "time_source": time_source,
            }
        )

    position_infos, unique_positions_analyzed = _analyse_positions_for_game(
        position_fens,
        depth,
        time_limit_ms,
        multipv,
    )

    for record in move_records:
        ply = record["ply"]
        fen_before = record["fen_before"]
        fen_after = record["fen_after"]
        side_to_move = record["side_to_move"]
        move_san = record["move_san"]
        move_uci = record["move_uci"]

        current_info = position_infos[ply] if ply < len(position_infos) else None
        next_info = position_infos[ply + 1] if (ply + 1) < len(position_infos) else None

        main_info, multi_pv_data = _normalize_analysis_payload(current_info, depth)
        eval_before_dict, eval_before_cp, pv_before_uci, best_move = _extract_eval_components(main_info, depth)

        best_move_san = None
        if best_move is not None:
            try:
                best_move_san = chess.Board(fen_before).san(best_move)
            except Exception:
                best_move_san = None
        best_move_uci = best_move.uci() if best_move is not None else None

        next_main_info, _ = _normalize_analysis_payload(next_info, depth)
        eval_after_dict, eval_after_cp, pv_after_uci, _ = _extract_eval_components(next_main_info, depth)

        if best_move_uci and move_uci == best_move_uci:
            cp_loss = 0
        elif eval_before_dict and eval_after_dict:
            if side_to_move == chess.WHITE:
                cp_loss = eval_before_cp - eval_after_cp
            else:
                cp_loss = eval_after_cp - eval_before_cp
            cp_loss = max(0, cp_loss)
        else:
            cp_loss = None

        classification = classify_move(cp_loss)
        eval_before_cp_for_review = eval_before_cp if eval_before_dict is not None else None
        eval_after_cp_for_review = eval_after_cp if eval_after_dict is not None else None

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
        review_tag = _derive_review_tag(
            ply=ply,
            classification=classification,
            cp_loss=cp_loss,
            tactical=tactical,
            eval_before_cp=eval_before_cp_for_review,
            eval_after_cp=eval_after_cp_for_review,
            mover_is_white=side_to_move == chess.WHITE,
            opening_ply_count=opening_ply_count,
        )

        if cp_loss is not None:
            if side_to_move == chess.WHITE:
                white_cp_losses.append(cp_loss)
            else:
                black_cp_losses.append(cp_loss)

        if isinstance(review_tag, str):
            if side_to_move == chess.WHITE:
                if review_tag in review_counts_white:
                    review_counts_white[review_tag] += 1
            else:
                if review_tag in review_counts_black:
                    review_counts_black[review_tag] += 1

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
            "clock_seconds": record["clock_seconds"],
            "time_spent_seconds": record["time_spent_seconds"],
            "time_source": record["time_source"],
            "tactical": tactical,
            "review_tag": review_tag,
        }

        if multi_pv_data and len(multi_pv_data) > 1:
            move_eval["multi_pv"] = multi_pv_data

        move_evaluations.append(move_eval)

    def calc_accuracy(losses: list[int]) -> int:
        if not losses:
            return 100
        clamped = [min(loss, 600) for loss in losses]
        avg_loss = sum(clamped) / len(clamped)
        accuracy = 100 * math.exp(-avg_loss / 250)
        return round(max(0, min(100, accuracy)))

    accuracy_white = calc_accuracy(white_cp_losses)
    accuracy_black = calc_accuracy(black_cp_losses)

    opening_ply = min(20, len(move_evaluations))
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
            "positions_analyzed": len(moves_list) + 1,
            "unique_positions_analyzed": unique_positions_analyzed,
            "position_workers": min(FULL_ANALYSIS_POSITION_WORKERS, max(1, unique_positions_analyzed)),
            "review_counts_white": review_counts_white,
            "review_counts_black": review_counts_black,
            "review_labels_version": REVIEW_LABELS_VERSION,
        },
    }


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
    
    try:
        info = _analyse_with_recovery(board, depth, time_limit_ms, multipv=multipv)
    except Exception:
        info = None

    if isinstance(info, list):
        results = []
        for line_info in info:
            if not isinstance(line_info, dict):
                continue
            score = line_info.get("score")
            pv = line_info.get("pv", [])
            if score:
                score_dict = score_to_dict(score, chess.WHITE)
                score_dict["depth"] = line_info.get("depth", depth)

                pv_san = []
                temp_board = board.copy()
                for move in pv[:10]:
                    try:
                        pv_san.append(temp_board.san(move))
                        temp_board.push(move)
                    except Exception:
                        break

                results.append(
                    {
                        **score_dict,
                        "pv_uci": [move.uci() for move in pv[:10]],
                        "pv_san": pv_san,
                    }
                )

        if results:
            return {
                "eval": results[0],
                "multipv": results if len(results) > 1 else None,
                "fen": fen,
            }
    elif isinstance(info, dict):
        score = info.get("score")
        pv = info.get("pv", [])

        if score:
            score_dict = score_to_dict(score, chess.WHITE)
            score_dict["depth"] = info.get("depth", depth)

            pv_san = []
            temp_board = board.copy()
            for move in pv[:10]:
                try:
                    pv_san.append(temp_board.san(move))
                    temp_board.push(move)
                except Exception:
                    break

            return {
                "eval": {
                    **score_dict,
                    "pv_uci": [move.uci() for move in pv[:10]],
                    "pv_san": pv_san,
                },
                "multipv": None,
                "fen": fen,
            }

    return {"eval": None, "multipv": None, "fen": fen}
