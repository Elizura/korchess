"""FEN normalization utilities for LMDB cache keys."""


def fen_to_4field(fen: str) -> str:
    """Strip a FEN to its first 4 fields (pieces, side, castling, ep).

    The Lichess eval dataset uses 4-field FENs as keys because halfmove
    clock and fullmove number don't affect position evaluation.
    """
    parts = fen.split()
    if len(parts) < 4:
        return fen
    return " ".join(parts[:4])
