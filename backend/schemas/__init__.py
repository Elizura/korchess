"""Pydantic request/response models for the API."""

from pydantic import BaseModel, Field


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
