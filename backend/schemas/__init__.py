"""Pydantic request/response models for the API."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ImportRequest(BaseModel):
    """Request body for importing games."""
    username: str = Field(..., min_length=1, max_length=50)
    max_games: int = Field(default=500, ge=1, le=10000)


class ImportResponse(BaseModel):
    """Response for import endpoint."""
    username: str
    imported: int
    skipped: int
    is_sync: bool = False


class ImportHistoryItem(BaseModel):
    """Single item in import history."""
    username: str
    site: str
    imported_at: str


class ImportHistoryResponse(BaseModel):
    """Response for import history endpoint."""
    history: list[ImportHistoryItem]


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
    last_synced_at: str | None = None


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
    opening_ply_count: int | None = None


ReviewTag = Literal[
    "book",
    "brilliant",
    "great",
    "best",
    "excellent",
    "good",
    "inaccuracy",
    "mistake",
    "miss",
    "blunder",
]


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
    clock_seconds: int | None = None
    time_spent_seconds: int | None = None
    time_source: str | None = None
    tactical: dict | None = None
    review_tag: ReviewTag | None = None


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
    unique_positions_analyzed: int | None = None
    position_workers: int | None = None
    review_counts_white: dict[str, int] | None = None
    review_counts_black: dict[str, int] | None = None
    review_labels_version: str | None = None


class FullAnalysisResult(BaseModel):
    """Full analysis result."""
    moves: list[MoveEvaluation]
    summary: FullAnalysisSummary
    meta: FullAnalysisMeta


class FullAnalysisResponse(BaseModel):
    """Response for full analysis endpoint."""
    status: str  # "completed" | "missing" | "processing"
    analysis: FullAnalysisResult | None = None
    insights: dict | None = None
    created_at: str | None = None


class AIInsightsResponse(BaseModel):
    """Response for AI insights endpoint."""
    status: str  # "ready" | "analysis_missing" | "quota_exceeded" | "generation_failed"
    insights: dict | None = None
    created_at: str | None = None
    detail: str | None = None


LessonConsentDecision = Literal["consented", "declined"]
LessonConsentState = Literal["consented", "declined", "unknown"]
LessonConsentSource = Literal["game_ai_summary"]
LessonConsentChannel = Literal["email_lessons"]


class LessonConsentRequest(BaseModel):
    """Request to record a lesson-consent decision."""
    decision: LessonConsentDecision
    source: LessonConsentSource
    site: Literal["lichess", "chesscom"] | None = None
    site_game_id: str | None = None
    analysis_depth: int | None = Field(default=None, ge=1, le=40)
    analysis_multipv: int | None = Field(default=None, ge=1, le=8)


class LessonConsentResponse(BaseModel):
    """Current lesson-consent status for a user."""
    channel: LessonConsentChannel
    state: LessonConsentState
    consented: bool
    last_decision_at: str | None = None


class SingleGameInsightsResponse(BaseModel):
    """Response for deterministic single-game rule insights endpoint."""
    status: str  # "ready" | "analysis_missing" | "analysis_processing"
    version: str | None = None
    analysis_ref: dict | None = None
    cards: dict | None = None
    result_cause: dict | None = None
    decisive_phase: dict | None = None
    turning_points: dict | None = None
    missed_winning_chances: dict | None = None
    got_away_with_it: dict | None = None
    conversion_quality: dict | None = None
    resilience_quality: dict | None = None
    time_pressure_collapse: dict | None = None
    phase_grades: dict | None = None
    game_character: dict | None = None
    confidence: float | None = None
    meta: dict | None = None
    narration: dict | None = None
    narration_meta: dict | None = None


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


class InsightsRequest(BaseModel):
    """Request body for scheduling user insights refresh."""
    username: str = Field(..., min_length=1, max_length=50)
    site: str = Field(default="all", pattern="^(all|lichess|chesscom)$")
    force: bool = False


class InsightsClaim(BaseModel):
    """A grounded narrative claim that references fact IDs."""
    text: str
    fact_ids: list[str] = []


class InsightsJobStatus(BaseModel):
    """Background job status for insights generation."""
    id: str | None = None
    status: str
    stage: str
    reason: str | None = None
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class QuickScanProgress(BaseModel):
    """Progress of a background quick-scan job."""
    status: str  # "queued" | "running" | "completed" | "failed"
    done: int = 0
    total: int = 0


class QuickScanProblemItem(BaseModel):
    """A single tactical problem detected by the quick scan."""
    site: str | None = None
    site_game_id: str | None = None
    ply: int
    san: str
    classification: str | None = None
    phase: str
    tactic_type: str | None = None
    tactic_types: list[str] = []
    played_at: str | None = None
    opponent: str | None = None
    time_class: str | None = None


class ProblemsByThemeResponse(BaseModel):
    """Paginated response for problems filtered by theme."""
    items: list[QuickScanProblemItem] = []
    total_count: int = 0
    filtered_count: int = 0
    page: int = 0
    page_size: int = 8
    total_pages: int = 0
    available_time_controls: list[str] = []
    available_phases: list[str] = []


class ProblemSpotterData(BaseModel):
    """Aggregated tactical problem data for the dashboard."""
    total_problems: int = 0
    by_theme: list[dict] = []
    by_phase: dict[str, int] = {}
    by_classification: dict[str, int] = {}
    recent_problems: list[dict] = []


class InsightsProfileResponse(BaseModel):
    """Current AI insights state for a user."""
    username: str
    site: str
    lifecycle_status: str
    feature_version: str
    narrative_version: str
    updated_at: str | None = None
    coverage: dict | None = None
    features: dict | None = None
    narrative: dict | None = None
    active_job: InsightsJobStatus | None = None
    scan_progress: QuickScanProgress | None = None
    problem_spotter: ProblemSpotterData | None = None


class AnalyticsEventItem(BaseModel):
    """Single analytics event payload from client."""
    event_id: str | None = None
    event_name: str
    event_version: str | None = None
    occurred_at: str | None = None
    anonymous_id: str
    session_id: str
    path: str | None = None
    url: str | None = None
    referrer: str | None = None
    user_agent: str | None = None
    is_first_time: bool = False
    properties: dict[str, Any] = Field(default_factory=dict)


class AnalyticsEventsIngestRequest(BaseModel):
    """Batch analytics ingest request."""
    events: list[AnalyticsEventItem] = Field(..., min_length=1, max_length=100)


class AnalyticsEventsIngestResponse(BaseModel):
    """Batch analytics ingest response."""
    accepted: int


class AnalyticsIdentifyRequest(BaseModel):
    """Authenticated identity stitch request."""
    anonymous_id: str = Field(..., min_length=1, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
