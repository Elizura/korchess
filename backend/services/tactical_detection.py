"""Deterministic tactical motif detection for deep move analysis."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import chess

from utils.insights_utils import cp_for_mover, clamp

_PIECE_CP = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

_PIECE_LABEL = {
    "p": "Pawn",
    "n": "Knight",
    "b": "Bishop",
    "r": "Rook",
    "q": "Queen",
    "k": "King",
}

_DETECTABLE_CLASSIFICATIONS = {"inaccuracy", "mistake", "blunder"}
_TARGETABLE_TYPES = {chess.KING, chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}




@dataclass(frozen=True)
class TacticalConfig:
    """Runtime knobs for tactical motif detection."""

    enabled: bool = True
    cp_loss_inaccuracy: int = 120
    cp_loss_mistake: int = 170
    cp_loss_blunder: int = 240
    max_pv_plies: int = 8
    forced_mate_plies: int = 6
    min_material_cp: int = 250

    @classmethod
    def from_env(cls) -> "TacticalConfig":
        return cls(
            enabled=_env_bool("TACTICS_ENABLED", True),
            cp_loss_inaccuracy=max(0, _env_int("TACTICS_CP_LOSS_INACCURACY", 120)),
            cp_loss_mistake=max(0, _env_int("TACTICS_CP_LOSS_MISTAKE", 170)),
            cp_loss_blunder=max(0, _env_int("TACTICS_CP_LOSS_BLUNDER", 240)),
            max_pv_plies=max(1, _env_int("TACTICS_MAX_PV_PLIES", 8)),
            forced_mate_plies=max(1, _env_int("TACTICS_FORCED_MATE_PLIES", 6)),
            min_material_cp=max(0, _env_int("TACTICS_MIN_MATERIAL_CP", 250)),
        )


def _to_move_color(fen: str) -> chess.Color | None:
    try:
        return chess.Board(fen).turn
    except ValueError:
        return None


def _parse_uci_move(board: chess.Board, uci: str | None) -> chess.Move | None:
    if not uci:
        return None
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        return None
    return move if move in board.legal_moves else None


def _captured_piece_type(board: chess.Board, move: chess.Move) -> int | None:
    if not board.is_capture(move):
        return None
    if board.is_en_passant(move):
        return chess.PAWN
    captured = board.piece_at(move.to_square)
    return captured.piece_type if captured else None


def _capture_square(board: chess.Board, move: chess.Move) -> int:
    if board.is_en_passant(move):
        if board.turn == chess.WHITE:
            return move.to_square - 8
        return move.to_square + 8
    return move.to_square


def _piece_symbol(piece_type: int | None) -> str | None:
    if piece_type is None:
        return None
    for symbol, kind in {
        "p": chess.PAWN,
        "n": chess.KNIGHT,
        "b": chess.BISHOP,
        "r": chess.ROOK,
        "q": chess.QUEEN,
        "k": chess.KING,
    }.items():
        if kind == piece_type:
            return symbol
    return None


def _piece_name_from_symbol(symbol: str | None) -> str | None:
    if not isinstance(symbol, str):
        return None
    return _PIECE_LABEL.get(symbol.lower())


def _piece_name_from_type(piece_type: int | None) -> str | None:
    return _piece_name_from_symbol(_piece_symbol(piece_type))


def _normalize_line_uci(line_uci: list[str] | None, max_plies: int) -> list[str]:
    return [uci for uci in (line_uci or []) if isinstance(uci, str)][:max_plies]


def _mate_against_mover(eval_payload: dict[str, Any] | None, mover_is_white: bool) -> int | None:
    if not isinstance(eval_payload, dict):
        return None
    mate_raw = eval_payload.get("mate")
    if not isinstance(mate_raw, int) or mate_raw == 0:
        return None
    if mover_is_white:
        return abs(mate_raw) if mate_raw < 0 else None
    return abs(mate_raw) if mate_raw > 0 else None


def _mate_for_mover(eval_payload: dict[str, Any] | None, mover_is_white: bool) -> int | None:
    if not isinstance(eval_payload, dict):
        return None
    mate_raw = eval_payload.get("mate")
    if not isinstance(mate_raw, int) or mate_raw == 0:
        return None
    if mover_is_white:
        return abs(mate_raw) if mate_raw > 0 else None
    return abs(mate_raw) if mate_raw < 0 else None


def _best_line_mate_for_mover(
    multi_pv: list[dict[str, Any]] | None, mover_is_white: bool
) -> tuple[int, list[str]]:
    if not isinstance(multi_pv, list) or not multi_pv:
        return (0, [])

    first = multi_pv[0] if isinstance(multi_pv[0], dict) else None
    if not isinstance(first, dict):
        return (0, [])

    mate_raw = first.get("mate")
    if not isinstance(mate_raw, int) or mate_raw == 0:
        return (0, [])

    if mover_is_white and mate_raw <= 0:
        return (0, [])
    if (not mover_is_white) and mate_raw >= 0:
        return (0, [])

    pv_uci = [uci for uci in (first.get("pv") or []) if isinstance(uci, str)]
    return (abs(mate_raw), pv_uci)


def _force_score_from_multipv(multi_pv: list[dict[str, Any]] | None, mover_is_white: bool) -> bool:
    if not isinstance(multi_pv, list) or len(multi_pv) < 2:
        return False

    first = multi_pv[0] if isinstance(multi_pv[0], dict) else None
    second = multi_pv[1] if isinstance(multi_pv[1], dict) else None
    if not first or not second:
        return False

    first_mate = first.get("mate")
    second_mate = second.get("mate")
    if isinstance(first_mate, int) and first_mate != 0:
        if mover_is_white and first_mate > 0:
            return not (isinstance(second_mate, int) and second_mate > 0)
        if (not mover_is_white) and first_mate < 0:
            return not (isinstance(second_mate, int) and second_mate < 0)

    first_cp = cp_for_mover(first.get("cp"), mover_is_white)
    second_cp = cp_for_mover(second.get("cp"), mover_is_white)
    if first_cp is None or second_cp is None:
        return False
    return (first_cp - second_cp) >= 200


def _format_piece_counts(symbols: list[str]) -> str:
    counts: dict[str, int] = {}
    for symbol in symbols:
        counts[symbol] = counts.get(symbol, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: _PIECE_CP.get({
        "p": chess.PAWN,
        "n": chess.KNIGHT,
        "b": chess.BISHOP,
        "r": chess.ROOK,
        "q": chess.QUEEN,
        "k": chess.KING,
    }[item[0]], 0), reverse=True)
    return " + ".join(f"{count} {_PIECE_LABEL.get(symbol, symbol)}" for symbol, count in ordered)


def _material_text(captured: list[str], lost: list[str]) -> str:
    if captured and not lost:
        return f"+{_format_piece_counts(captured)}"
    if lost and not captured:
        return f"-{_format_piece_counts(lost)}"
    if captured and lost:
        return f"+{_format_piece_counts(captured)} / -{_format_piece_counts(lost)}"
    return "Equal material"


def _material_from_line(
    board: chess.Board,
    line_uci: list[str] | None,
    mover_color: chess.Color,
    max_plies: int,
) -> dict[str, Any]:
    if not isinstance(line_uci, list):
        return {
            "cp_net_for_mover": 0,
            "text": "Equal material",
            "captured": [],
            "lost": [],
        }

    work = board.copy()
    cp_net = 0
    captured_symbols: list[str] = []
    lost_symbols: list[str] = []

    for uci in line_uci[:max_plies]:
        if not isinstance(uci, str):
            break
        move = _parse_uci_move(work, uci)
        if move is None:
            break

        piece_type = _captured_piece_type(work, move)
        if piece_type is not None:
            value = _PIECE_CP.get(piece_type, 0)
            symbol = _piece_symbol(piece_type)
            if work.turn == mover_color:
                cp_net += value
                if symbol:
                    captured_symbols.append(symbol)
            else:
                cp_net -= value
                if symbol:
                    lost_symbols.append(symbol)

        work.push(move)

    return {
        "cp_net_for_mover": cp_net,
        "text": _material_text(captured_symbols, lost_symbols),
        "captured": captured_symbols,
        "lost": lost_symbols,
    }


def _severity_score(cp_loss: int | None, mate_against: int | None) -> float:
    if mate_against is not None:
        return 1.0 if mate_against <= 2 else clamp(0.85 + (0.15 * (1 / mate_against)), 0.85, 1.0)
    if cp_loss is None:
        return 0.5
    return clamp(cp_loss / 500.0, 0.2, 0.98)


def _should_analyze(
    classification: str | None,
    cp_loss: int | None,
    eval_after: dict[str, Any] | None,
    mover_is_white: bool,
    multi_pv: list[dict[str, Any]] | None,
    cfg: TacticalConfig,
    eval_before: dict[str, Any] | None = None,
) -> bool:
    mate_against = _mate_against_mover(eval_after, mover_is_white)
    if mate_against is not None and mate_against <= cfg.forced_mate_plies:
        return True
    best_line_mate, _ = _best_line_mate_for_mover(multi_pv, mover_is_white)
    if best_line_mate > 0 and best_line_mate <= cfg.forced_mate_plies:
        return True
    mate_for_mover = _mate_for_mover(eval_before, mover_is_white)
    if mate_for_mover is not None and mate_for_mover <= cfg.forced_mate_plies:
        return True

    if not classification:
        return False
    cls = classification.lower()
    if cls not in _DETECTABLE_CLASSIFICATIONS:
        return False
    if cp_loss is None:
        return False

    if cls == "inaccuracy":
        return cp_loss >= cfg.cp_loss_inaccuracy
    if cls == "mistake":
        return cp_loss >= cfg.cp_loss_mistake
    if cls == "blunder":
        return cp_loss >= cfg.cp_loss_blunder
    return False


def _is_back_rank_pattern(board_after: chess.Board, mover_color: chess.Color) -> bool:
    king_sq = board_after.king(mover_color)
    if king_sq is None:
        return False

    king_rank = chess.square_rank(king_sq)
    home_rank = 0 if mover_color == chess.WHITE else 7
    if king_rank != home_rank:
        return False

    king_escape_count = sum(1 for mv in board_after.legal_moves if mv.from_square == king_sq)
    if king_escape_count > 1:
        return False

    front_rank = 1 if mover_color == chess.WHITE else 6
    king_file = chess.square_file(king_sq)
    front_pawn_blockers = 0
    for file_delta in (-1, 0, 1):
        file_index = king_file + file_delta
        if file_index < 0 or file_index > 7:
            continue
        sq = chess.square(file_index, front_rank)
        piece = board_after.piece_at(sq)
        if piece and piece.color == mover_color and piece.piece_type == chess.PAWN:
            front_pawn_blockers += 1
    if front_pawn_blockers == 0:
        return False

    attacker_color = not mover_color
    heavy_attack = False
    for attacker_sq in board_after.attackers(attacker_color, king_sq):
        piece = board_after.piece_at(attacker_sq)
        if piece and piece.color == attacker_color and piece.piece_type in {chess.ROOK, chess.QUEEN}:
            heavy_attack = True
            break

    return heavy_attack


def _forced_mate_detector(
    *,
    board_after: chess.Board,
    mover_color: chess.Color,
    mover_is_white: bool,
    eval_after: dict[str, Any] | None,
    pv_after_uci: list[str] | None,
    cp_loss: int | None,
    multi_pv: list[dict[str, Any]] | None,
    cfg: TacticalConfig,
) -> dict[str, Any] | None:
    mate_against = _mate_against_mover(eval_after, mover_is_white)
    if mate_against is None or mate_against > cfg.forced_mate_plies:
        return None

    pv_line = _normalize_line_uci(pv_after_uci, cfg.max_pv_plies)
    evidence = ["forced_mate_after_move"]
    if pv_line:
        evidence.append("mate_line_confirmed_in_pv_after")

    subtype = None
    if _is_back_rank_pattern(board_after, mover_color):
        subtype = "back_rank"
        evidence.append("back_rank_king_trapped")

    material_outcome = _material_from_line(board_after, pv_line, mover_color, cfg.max_pv_plies)
    return {
        "tactic_detected": True,
        "tactic_type": "FORCED_MATE",
        "tactic_types": ["FORCED_MATE"],
        "missed_move_uci": None,
        "missed_move_san": None,
        "line_source": "played_line",
        "material_outcome": material_outcome,
        "mate_outcome": {
            "is_mate_sequence": True,
            "mate_in": mate_against,
            "side_delivering_mate": "black" if mover_color == chess.WHITE else "white",
            "subtype": subtype,
        },
        "is_forced": True,
        "pv_uci": pv_line,
        "severity_score": round(_severity_score(cp_loss, mate_against), 3),
        "confidence": 0.97 if subtype == "back_rank" else 0.95,
        "evidence": evidence,
    }


def _missed_forced_mate_detector(
    *,
    board_before: chess.Board,
    best_move_uci: str | None,
    played_uci: str | None,
    pv_before_uci: list[str] | None,
    mover_color: chess.Color,
    mover_is_white: bool,
    cp_loss: int | None,
    multi_pv: list[dict[str, Any]] | None,
    cfg: TacticalConfig,
) -> dict[str, Any] | None:
    if not best_move_uci or (played_uci and best_move_uci == played_uci):
        return None

    mate_in, best_line_pv = _best_line_mate_for_mover(multi_pv, mover_is_white)
    if mate_in <= 0 or mate_in > cfg.forced_mate_plies:
        return None

    best_move = _parse_uci_move(board_before, best_move_uci)
    if best_move is None:
        return None

    try:
        best_move_san = board_before.san(best_move)
    except Exception:
        best_move_san = None

    pv_line = _normalize_line_uci(best_line_pv or pv_before_uci, cfg.max_pv_plies)
    material_outcome = _material_from_line(board_before, pv_line, mover_color, cfg.max_pv_plies)

    return {
        "tactic_detected": True,
        "tactic_type": "MISSED_FORCED_MATE",
        "tactic_types": ["MISSED_FORCED_MATE", "FORCED_MATE"],
        "missed_move_uci": best_move_uci,
        "missed_move_san": best_move_san,
        "line_source": "best_line",
        "material_outcome": material_outcome,
        "mate_outcome": {
            "is_mate_sequence": True,
            "mate_in": mate_in,
            "side_delivering_mate": "white" if mover_color == chess.WHITE else "black",
            "subtype": "missed_forcing_mate",
        },
        "is_forced": _force_score_from_multipv(multi_pv, mover_is_white),
        "pv_uci": pv_line,
        "severity_score": round(_severity_score(cp_loss, None), 3),
        "confidence": 0.93,
        "evidence": ["missed_forced_mate_in_best_line", "mate_line_present_in_multipv"],
    }


def _hanging_piece_detector(
    *,
    board_before: chess.Board,
    board_after: chess.Board,
    played_uci: str | None,
    pv_after_uci: list[str] | None,
    mover_color: chess.Color,
    cp_loss: int | None,
    cfg: TacticalConfig,
) -> dict[str, Any] | None:
    pv_line = _normalize_line_uci(pv_after_uci, cfg.max_pv_plies)
    if not pv_line:
        return None

    work = board_after.copy()
    candidate: dict[str, Any] | None = None
    for idx, uci in enumerate(pv_line):
        move = _parse_uci_move(work, uci)
        if move is None:
            break

        is_opponent_turn = work.turn != mover_color
        if is_opponent_turn and work.is_capture(move):
            capture_square = _capture_square(work, move)
            captured_piece = work.piece_at(capture_square)
            if (
                captured_piece
                and captured_piece.color == mover_color
                and captured_piece.piece_type != chess.KING
            ):
                captured_value = _PIECE_CP.get(captured_piece.piece_type, 0)
                if captured_value >= cfg.min_material_cp:
                    attacked_before_capture = work.is_attacked_by(not mover_color, capture_square)
                    defended_before_capture = work.is_attacked_by(mover_color, capture_square)
                    if attacked_before_capture and (not defended_before_capture or idx <= 2):
                        candidate = {
                            "pv_idx": idx,
                            "capture_square": capture_square,
                            "captured_piece": captured_piece,
                            "captured_value": captured_value,
                            "attacked_before_capture": attacked_before_capture,
                            "defended_before_capture": defended_before_capture,
                        }
                        break

        work.push(move)

    if not candidate:
        return None

    capture_square = int(candidate["capture_square"])
    captured_piece = candidate["captured_piece"]
    captured_value = int(candidate["captured_value"])
    defended_after = bool(candidate["defended_before_capture"])
    pv_idx = int(candidate["pv_idx"])

    opponent_color = not mover_color
    piece_before = board_before.piece_at(capture_square)
    attacked_before = board_before.is_attacked_by(opponent_color, capture_square)
    defended_before = board_before.is_attacked_by(mover_color, capture_square)
    hanging_before = bool(piece_before and piece_before.color == mover_color and attacked_before and not defended_before)
    played_move = _parse_uci_move(board_before, played_uci)
    moved_into_square = bool(played_move and played_move.to_square == capture_square)
    defender_removed = bool(
        piece_before and piece_before.color == mover_color and defended_before and not defended_after
    )
    if hanging_before and not moved_into_square and not defender_removed:
        return None

    evidence = ["attacked_undefended_piece"]
    if pv_idx == 0:
        evidence.append("immediate_capture_in_pv")
    else:
        evidence.append("delayed_capture_in_pv")

    if moved_into_square:
        evidence.append("moved_piece_into_hanging_square")
    elif defender_removed:
        evidence.append("defender_removed_by_played_move")

    material_outcome = _material_from_line(board_after, pv_line, mover_color, cfg.max_pv_plies)
    if material_outcome.get("cp_net_for_mover", 0) > -cfg.min_material_cp:
        return None

    return {
        "tactic_detected": True,
        "tactic_type": "HANGING_PIECE",
        "tactic_types": ["HANGING_PIECE"],
        "missed_move_uci": None,
        "missed_move_san": None,
        "line_source": "played_line",
        "hanging_piece_symbol": _piece_symbol(captured_piece.piece_type),
        "hanging_piece_name": _piece_name_from_type(captured_piece.piece_type),
        "hanging_piece_value_cp": captured_value,
        "material_outcome": material_outcome,
        "mate_outcome": {
            "is_mate_sequence": False,
            "mate_in": None,
            "side_delivering_mate": None,
            "subtype": None,
        },
        "is_forced": False,
        "pv_uci": pv_line,
        "severity_score": round(_severity_score(cp_loss, None), 3),
        "confidence": 0.9,
        "evidence": evidence,
    }


def _signed_step(value: int) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _line_direction(from_sq: int, to_sq: int) -> tuple[int, int] | None:
    from_file = chess.square_file(from_sq)
    from_rank = chess.square_rank(from_sq)
    to_file = chess.square_file(to_sq)
    to_rank = chess.square_rank(to_sq)
    df = to_file - from_file
    dr = to_rank - from_rank

    if df == 0 and dr != 0:
        return (0, _signed_step(dr))
    if dr == 0 and df != 0:
        return (_signed_step(df), 0)
    if abs(df) == abs(dr) and df != 0:
        return (_signed_step(df), _signed_step(dr))
    return None


def _first_piece_behind_target(
    board: chess.Board, front_square: int, direction: tuple[int, int]
) -> chess.Piece | None:
    file_idx = chess.square_file(front_square) + direction[0]
    rank_idx = chess.square_rank(front_square) + direction[1]
    while 0 <= file_idx <= 7 and 0 <= rank_idx <= 7:
        sq = chess.square(file_idx, rank_idx)
        piece = board.piece_at(sq)
        if piece:
            return piece
        file_idx += direction[0]
        rank_idx += direction[1]
    return None


def _fork_or_double_attack_detector(
    *,
    board_before: chess.Board,
    best_move_uci: str | None,
    played_uci: str | None,
    pv_before_uci: list[str] | None,
    mover_color: chess.Color,
    mover_is_white: bool,
    cp_loss: int | None,
    multi_pv: list[dict[str, Any]] | None,
    cfg: TacticalConfig,
) -> dict[str, Any] | None:
    best_move = _parse_uci_move(board_before, best_move_uci)
    if best_move is None:
        return None
    if played_uci and best_move_uci == played_uci:
        return None

    board_best = board_before.copy()
    try:
        best_move_san = board_best.san(best_move)
        board_best.push(best_move)
    except Exception:
        return None

    moved_piece = board_best.piece_at(best_move.to_square)
    if moved_piece is None or moved_piece.color != mover_color:
        return None

    opponent_color = not mover_color
    target_squares = []
    has_king_target = False
    high_value_targets = 0
    for sq in board_best.attacks(best_move.to_square):
        piece = board_best.piece_at(sq)
        if not piece or piece.color != opponent_color or piece.piece_type not in _TARGETABLE_TYPES:
            continue
        target_squares.append(sq)
        if piece.piece_type == chess.KING:
            has_king_target = True
        if _PIECE_CP.get(piece.piece_type, 0) >= 300:
            high_value_targets += 1

    if len(target_squares) < 2:
        return None
    if not has_king_target and high_value_targets < 2:
        return None

    material_outcome = _material_from_line(board_before, pv_before_uci, mover_color, cfg.max_pv_plies)
    if material_outcome["cp_net_for_mover"] < cfg.min_material_cp:
        return None

    is_fork = moved_piece.piece_type in {chess.KNIGHT, chess.PAWN} or has_king_target
    primary = "FORK" if is_fork else "DOUBLE_ATTACK"
    types = [primary]
    if primary == "FORK":
        types.append("DOUBLE_ATTACK")

    evidence = ["multi_target_attack_after_best_move", "tactical_gain_realized_in_pv"]
    if has_king_target and high_value_targets >= 1:
        evidence.append("king_plus_material_threat")

    return {
        "tactic_detected": True,
        "tactic_type": primary,
        "tactic_types": types,
        "missed_move_uci": best_move_uci,
        "missed_move_san": best_move_san,
        "line_source": "best_line",
        "material_outcome": material_outcome,
        "mate_outcome": {
            "is_mate_sequence": False,
            "mate_in": None,
            "side_delivering_mate": None,
            "subtype": None,
        },
        "is_forced": _force_score_from_multipv(multi_pv, mover_is_white),
        "pv_uci": [uci for uci in (pv_before_uci or []) if isinstance(uci, str)][: cfg.max_pv_plies],
        "severity_score": round(_severity_score(cp_loss, None), 3),
        "confidence": 0.88 if has_king_target else 0.83,
        "evidence": evidence,
    }


def _skewer_detector(
    *,
    board_before: chess.Board,
    best_move_uci: str | None,
    played_uci: str | None,
    pv_before_uci: list[str] | None,
    mover_color: chess.Color,
    mover_is_white: bool,
    cp_loss: int | None,
    multi_pv: list[dict[str, Any]] | None,
    cfg: TacticalConfig,
) -> dict[str, Any] | None:
    best_move = _parse_uci_move(board_before, best_move_uci)
    if best_move is None:
        return None
    if played_uci and best_move_uci == played_uci:
        return None

    board_best = board_before.copy()
    try:
        best_move_san = board_best.san(best_move)
        board_best.push(best_move)
    except Exception:
        return None

    moved_piece = board_best.piece_at(best_move.to_square)
    if moved_piece is None or moved_piece.color != mover_color:
        return None
    if moved_piece.piece_type not in {chess.BISHOP, chess.ROOK, chess.QUEEN}:
        return None

    opponent_color = not mover_color
    skewer_front: chess.Piece | None = None
    skewer_back: chess.Piece | None = None

    for attacked_square in board_best.attacks(best_move.to_square):
        front_piece = board_best.piece_at(attacked_square)
        if not front_piece or front_piece.color != opponent_color:
            continue
        if front_piece.piece_type not in {chess.KING, chess.QUEEN, chess.ROOK}:
            continue

        direction = _line_direction(best_move.to_square, attacked_square)
        if not direction:
            continue

        rear_piece = _first_piece_behind_target(board_best, attacked_square, direction)
        if not rear_piece or rear_piece.color != opponent_color or rear_piece.piece_type == chess.KING:
            continue

        front_value = _PIECE_CP.get(front_piece.piece_type, 0)
        rear_value = _PIECE_CP.get(rear_piece.piece_type, 0)
        if front_piece.piece_type != chess.KING and rear_value >= front_value:
            continue
        if rear_value < cfg.min_material_cp:
            continue

        skewer_front = front_piece
        skewer_back = rear_piece
        break

    if not skewer_front or not skewer_back:
        return None

    material_outcome = _material_from_line(board_before, pv_before_uci, mover_color, cfg.max_pv_plies)
    if material_outcome["cp_net_for_mover"] < cfg.min_material_cp:
        return None

    evidence = ["line_attack_with_piece_behind_target", "tactical_gain_realized_in_pv"]
    if skewer_front.piece_type == chess.KING:
        evidence.append("king_forced_to_move_reveals_piece")

    return {
        "tactic_detected": True,
        "tactic_type": "SKEWER",
        "tactic_types": ["SKEWER", "LINE_TACTIC"],
        "missed_move_uci": best_move_uci,
        "missed_move_san": best_move_san,
        "line_source": "best_line",
        "skewer_front_piece": _piece_name_from_type(skewer_front.piece_type),
        "skewer_rear_piece": _piece_name_from_type(skewer_back.piece_type),
        "material_outcome": material_outcome,
        "mate_outcome": {
            "is_mate_sequence": False,
            "mate_in": None,
            "side_delivering_mate": None,
            "subtype": None,
        },
        "is_forced": _force_score_from_multipv(multi_pv, mover_is_white),
        "pv_uci": _normalize_line_uci(pv_before_uci, cfg.max_pv_plies),
        "severity_score": round(_severity_score(cp_loss, None), 3),
        "confidence": 0.84,
        "evidence": evidence,
    }


def _missed_mate_from_eval_before(
    *,
    board_before: chess.Board,
    best_move_uci: str | None,
    played_uci: str | None,
    pv_before_uci: list[str] | None,
    mover_color: chess.Color,
    mover_is_white: bool,
    eval_before: dict[str, Any] | None,
    eval_after: dict[str, Any] | None,
    cp_loss: int | None,
    cfg: TacticalConfig,
) -> dict[str, Any] | None:
    """Detect missed checkmate using eval_before/eval_after when multi_pv is unavailable.

    If eval_before shows mate-for-mover (positive mate for white when mover is white,
    negative mate for black when mover is black) but eval_after no longer shows it,
    the user missed a forced mate.
    """
    if not best_move_uci or (played_uci and best_move_uci == played_uci):
        return None

    mate_for_mover = _mate_for_mover(eval_before, mover_is_white)
    if mate_for_mover is None or mate_for_mover > cfg.forced_mate_plies:
        return None

    mate_still_there = _mate_for_mover(eval_after, mover_is_white)
    if mate_still_there is not None and mate_still_there <= mate_for_mover:
        return None

    best_move = _parse_uci_move(board_before, best_move_uci)
    if best_move is None:
        return None

    try:
        best_move_san = board_before.san(best_move)
    except Exception:
        best_move_san = None

    pv_line = _normalize_line_uci(pv_before_uci, cfg.max_pv_plies)
    material_outcome = _material_from_line(board_before, pv_line, mover_color, cfg.max_pv_plies)

    return {
        "tactic_detected": True,
        "tactic_type": "MISSED_FORCED_MATE",
        "tactic_types": ["MISSED_FORCED_MATE", "FORCED_MATE"],
        "missed_move_uci": best_move_uci,
        "missed_move_san": best_move_san,
        "line_source": "best_line",
        "material_outcome": material_outcome,
        "mate_outcome": {
            "is_mate_sequence": True,
            "mate_in": mate_for_mover,
            "side_delivering_mate": "white" if mover_color == chess.WHITE else "black",
            "subtype": "missed_forcing_mate",
        },
        "is_forced": False,
        "pv_uci": pv_line,
        "severity_score": round(_severity_score(cp_loss, None), 3),
        "confidence": 0.90,
        "evidence": ["missed_forced_mate_from_eval_before", "mate_lost_after_move"],
    }


def detect_tactical_annotation(
    *,
    fen_before: str,
    fen_after: str,
    played_uci: str | None,
    best_move_uci: str | None,
    pv_before_uci: list[str] | None,
    pv_after_uci: list[str] | None,
    classification: str | None,
    cp_loss: int | None,
    eval_before: dict[str, Any] | None,
    eval_after: dict[str, Any] | None,
    multi_pv: list[dict[str, Any]] | None = None,
    config: TacticalConfig | None = None,
) -> dict[str, Any]:
    """Return structured tactical annotation for one deep-analyzed move."""
    cfg = config or TacticalConfig.from_env()
    if not cfg.enabled:
        return {"tactic_detected": False}

    mover_color = _to_move_color(fen_before)
    if mover_color is None:
        return {"tactic_detected": False}
    mover_is_white = mover_color == chess.WHITE

    if not _should_analyze(classification, cp_loss, eval_after, mover_is_white, multi_pv, cfg, eval_before=eval_before):
        return {"tactic_detected": False}

    try:
        board_before = chess.Board(fen_before)
        board_after = chess.Board(fen_after)
    except ValueError:
        return {"tactic_detected": False}

    forced = _forced_mate_detector(
        board_after=board_after,
        mover_color=mover_color,
        mover_is_white=mover_is_white,
        eval_after=eval_after,
        pv_after_uci=pv_after_uci,
        cp_loss=cp_loss,
        multi_pv=multi_pv,
        cfg=cfg,
    )
    if forced:
        return forced

    missed_mate = _missed_forced_mate_detector(
        board_before=board_before,
        best_move_uci=best_move_uci,
        played_uci=played_uci,
        pv_before_uci=pv_before_uci,
        mover_color=mover_color,
        mover_is_white=mover_is_white,
        cp_loss=cp_loss,
        multi_pv=multi_pv,
        cfg=cfg,
    )
    if missed_mate:
        return missed_mate

    missed_mate_from_eval = _missed_mate_from_eval_before(
        board_before=board_before,
        best_move_uci=best_move_uci,
        played_uci=played_uci,
        pv_before_uci=pv_before_uci,
        mover_color=mover_color,
        mover_is_white=mover_is_white,
        eval_before=eval_before,
        eval_after=eval_after,
        cp_loss=cp_loss,
        cfg=cfg,
    )
    if missed_mate_from_eval:
        return missed_mate_from_eval

    hanging = _hanging_piece_detector(
        board_before=board_before,
        board_after=board_after,
        played_uci=played_uci,
        pv_after_uci=pv_after_uci,
        mover_color=mover_color,
        cp_loss=cp_loss,
        cfg=cfg,
    )
    if hanging:
        return hanging

    skewer = _skewer_detector(
        board_before=board_before,
        best_move_uci=best_move_uci,
        played_uci=played_uci,
        pv_before_uci=pv_before_uci,
        mover_color=mover_color,
        mover_is_white=mover_is_white,
        cp_loss=cp_loss,
        multi_pv=multi_pv,
        cfg=cfg,
    )
    if skewer:
        return skewer

    fork_or_double = _fork_or_double_attack_detector(
        board_before=board_before,
        best_move_uci=best_move_uci,
        played_uci=played_uci,
        pv_before_uci=pv_before_uci,
        mover_color=mover_color,
        mover_is_white=mover_is_white,
        cp_loss=cp_loss,
        multi_pv=multi_pv,
        cfg=cfg,
    )
    if fork_or_double:
        return fork_or_double

    return {"tactic_detected": False}
