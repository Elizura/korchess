"""Position evaluation endpoint."""

from fastapi import APIRouter, HTTPException, Depends

import psycopg

from full_analysis import evaluate_position
from schemas import EvalRequest, EvalResponse
from auth import get_registered_user
from dependencies import get_db

router = APIRouter(tags=["eval"])


@router.post("/eval", response_model=EvalResponse)
async def evaluate_position_endpoint(
    request: EvalRequest,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
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
