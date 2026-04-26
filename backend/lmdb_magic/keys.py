"""FEN normalization utilities for LMDB cache keys."""


def _ep_is_legal(board_part: str, side: str, ep_square: str) -> bool:
    """Check if an en passant capture is actually possible.

    The Lichess eval dataset normalizes the ep field to '-' when no
    enemy pawn can actually capture en passant.  python-chess always
    sets the ep square after a double pawn push regardless, so we need
    to replicate the Lichess convention.
    """
    if len(ep_square) != 2:
        return False

    file = ord(ep_square[0]) - ord("a")  # 0-7
    ranks = board_part.split("/")

    # The capturing pawn sits on the 4th or 5th rank depending on side.
    # side == 'w': black just pushed, ep square is on rank 3 (index 5 in
    #   ranks[]), capturing white pawn is on rank 4 (index 4).
    # side == 'b': white just pushed, ep square is on rank 6 (index 2 in
    #   ranks[]), capturing black pawn is on rank 5 (index 3).
    if side == "w":
        capture_rank = ranks[4]  # rank 4 (0-indexed from top)
        friendly_pawn = "P"
    else:
        capture_rank = ranks[3]  # rank 5
        friendly_pawn = "p"

    # Expand the rank string to 8 characters (replace digits with dots).
    expanded = ""
    for ch in capture_rank:
        if ch.isdigit():
            expanded += "." * int(ch)
        else:
            expanded += ch

    # Check adjacent files for a friendly pawn.
    for adj in (file - 1, file + 1):
        if 0 <= adj < 8 and expanded[adj] == friendly_pawn:
            return True
    return False


def fen_to_4field(fen: str) -> str:
    """Strip a FEN to its first 4 fields (pieces, side, castling, ep).

    The Lichess eval dataset uses 4-field FENs as keys because halfmove
    clock and fullmove number don't affect position evaluation.

    The ep square is normalized to '-' when no en passant capture is
    actually legal, matching the Lichess convention.
    """
    parts = fen.split()
    if len(parts) < 4:
        return fen

    board_part, side, castling, ep = parts[0], parts[1], parts[2], parts[3]

    if ep != "-" and not _ep_is_legal(board_part, side, ep):
        ep = "-"

    return f"{board_part} {side} {castling} {ep}"
