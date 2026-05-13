"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useState, useEffect, useMemo, useCallback, useRef, type CSSProperties } from "react";
import { Chess } from "chess.js";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { trackEvent, withTrackingHeaders } from "@/lib/analytics/client";
import { API_BASE_URL } from "@/lib/api-url";

import AnalysisBoard from "@/components/analysis/AnalysisBoard";
import EvalBar from "@/components/analysis/EvalBar";
import MoveList from "@/components/analysis/MoveList";
import EngineLines from "@/components/analysis/EngineLines";
import BoardControls from "@/components/analysis/BoardControls";
import { useLocalEngine } from "@/hooks/useLocalEngine";
import {
  MoveTree,
  ReviewTag,
  TacticalAnnotation,
  createMoveTree,
  buildTreeFromAnalysis,
  addMove,
  navigateTo,
  goToStart,
  goToEnd,
  goBack,
  goForward,
  toReviewTag,
} from "@/lib/moveTree";

const LOCAL_ENGINE_DEPTH = 18;
const LOCAL_ENGINE_MULTIPV = 1;

interface GameData {
  username: string;
  game_id: string;
  pgn: string;
  played_at: string | null;
  color: string;
  result: string;
  opponent: string | null;
  lichess_url: string;
  eco: string | null;
  opening_name: string | null;
  opening_ply_count?: number | null;
}

interface MoveEvaluation {
  ply: number;
  san: string;
  uci: string;
  fen_before: string;
  fen_after: string;
  eval_before: { cp?: number; mate?: number; depth?: number } | null;
  eval_after: { cp?: number; mate?: number; depth?: number } | null;
  best_move_uci: string | null;
  best_move_san: string | null;
  pv: string[];
  classification: string | null;
  review_tag?: ReviewTag | null;
  cp_loss: number | null;
  clock_seconds?: number | null;
  time_spent_seconds?: number | null;
  time_source?: "clock" | "elapsed" | "inferred" | "missing" | null;
  multi_pv?: Array<{ cp?: number; mate?: number; pv: string[] }>;
  tactical?: TacticalAnnotation | null;
}

interface FullAnalysisSummary {
  accuracy_white: number;
  accuracy_black: number;
  opening_name: string;
  opening_eval: { cp?: number; mate?: number } | null;
  total_moves: number;
  white_player: string;
  black_player: string;
}

interface FullAnalysisResponse {
  status: "completed" | "missing" | "processing";
  analysis: {
    moves: MoveEvaluation[];
    summary: FullAnalysisSummary;
    meta: {
      engine: string;
      depth: number;
      multipv: number;
      time_per_position_ms: number;
      total_time_ms: number;
      positions_analyzed: number;
      unique_positions_analyzed?: number;
      position_workers?: number;
      review_counts_white?: Partial<Record<ReviewTag, number>>;
      review_counts_black?: Partial<Record<ReviewTag, number>>;
      review_labels_version?: string;
    };
  } | null;
  insights?: SingleGameInsightsResponse | null;
  created_at: string | null;
}

interface AIInsightsResponse {
  status: "ready" | "analysis_missing" | "quota_exceeded" | "generation_failed";
  insights: SingleGameInsightsResponse | null;
  created_at: string | null;
  detail?: string | null;
}

type LessonConsentDecision = "consented" | "declined";

interface LessonConsentResponse {
  channel: "email_lessons";
  state: "consented" | "declined" | "unknown";
  consented: boolean;
  last_decision_at: string | null;
}

interface InsightEvidence {
  ply: number;
  move_index?: number | null;
  uci?: string | null;
  san?: string | null;
}

interface InsightAnchor {
  ply: number;
  move_index: number;
  uci?: string | null;
  san?: string | null;
  fen_before?: string | null;
  fen_after?: string | null;
}

interface InsightEvent {
  event_id: string;
  label_enum: string;
  label: string;
  ply: number;
  actor?: "user" | "opponent";
  phase?: "opening" | "middlegame" | "endgame";
  pre_eval_cp?: number;
  post_eval_cp?: number;
  swing_cp?: number;
  severity?: string;
  severity_score?: number;
  priority?: number;
  is_decisive?: boolean;
  lost_advantage_cp?: number;
  delta_cp?: number;
  cp_loss?: number;
  persisted_ratio?: number;
  anchor?: InsightAnchor;
  confidence?: number;
  evidence?: InsightEvidence[];
}

interface SingleGameInsightCard {
  label_enum: string;
  confidence: number;
  evidence: Array<InsightEvidence | InsightEvent>;
}

interface GameInsightsNarrationSection {
  heading: string;
  bullets: string[];
}

interface GameInsightsNarration {
  title: string;
  one_liner: string;
  confidence_note: string;
  sections: GameInsightsNarrationSection[];
  labels: {
    decisive_phase: "opening" | "middlegame" | "endgame" | "unknown";
    player_style: string;
  };
}

interface GameInsightsNarrationMeta {
  source?: string;
  cache_key?: string;
  schema_version?: string;
  model?: string;
  generated_at?: string;
  reason?: string;
}

interface SingleGameInsightsResponse {
  status: "ready" | "analysis_missing" | "analysis_processing";
  version?: string;
  analysis_ref?: {
    site: string;
    game_id: string;
    username?: string;
    depth: number;
    multipv: number;
  };
  cards?: Record<string, SingleGameInsightCard>;
  result_cause?: {
    label_enum: string;
    primary_reason_code: string;
    secondary_reason_code: string;
    primary_label: string;
    secondary_label: string;
    cause_hierarchy_version: string;
    factor_impacts: {
      self_errors: number;
      opponent_errors: number;
      conversion: number;
      resilience: number;
      time_pressure: number;
    };
    confidence: number;
    evidence: InsightEvidence[];
  };
  decisive_phase?: {
    label_enum: string;
    decisive_phase: "opening" | "middlegame" | "endgame" | "mixed";
    confidence: number;
  };
  turning_points?: {
    label_enum: string;
    confidence: number;
    events: InsightEvent[];
  };
  missed_winning_chances?: {
    label_enum: string;
    count: number;
    confidence: number;
    events: InsightEvent[];
  };
  got_away_with_it?: {
    label_enum: string;
    count: number;
    confidence: number;
    events: InsightEvent[];
  };
  conversion_quality?: {
    label_enum: string;
    available: boolean;
    score: number | null;
    grade: string;
    opportunities: number;
    confidence: number;
    reason?: string;
  };
  resilience_quality?: {
    label_enum: string;
    available: boolean;
    score: number | null;
    grade: string;
    defense_opportunities: number;
    confidence: number;
    reason?: string;
  };
  phase_grades?: {
    label_enum: string;
    opening: {
      score: number | null;
      grade: string;
      evaluation_state: "scored" | "not_reached" | "too_short";
      confidence: number;
    };
    middlegame: {
      score: number | null;
      grade: string;
      evaluation_state: "scored" | "not_reached" | "too_short";
      confidence: number;
    };
    endgame: {
      score: number | null;
      grade: string;
      evaluation_state: "scored" | "not_reached" | "too_short";
      confidence: number;
    };
  };
  game_character?: {
    label_enum:
      | "defensive_grind"
      | "advantage_lost"
      | "stable"
      | "volatile"
      | "sharp"
      | "technical"
      | "chaotic"
      | "controlled";
    label: string;
    sublabel?: string;
    confidence: number;
  };
  time_pressure_collapse?: {
    label_enum: string;
    status: "detected" | "not_detected" | "insufficient_data" | "unavailable";
    status_reason: string;
    low_time_threshold_s: number | null;
    low_time_moves: number;
    normal_time_moves: number;
    avg_cp_low: number | null;
    avg_cp_normal: number | null;
    cp_drop: number | null;
    blunder_rate_low: number | null;
    blunder_rate_normal: number | null;
    blunder_delta: number | null;
    critical_low_time_swings: number;
    data_quality: {
      user_moves: number;
      clock_moves: number;
      time_spent_moves: number;
      missing_time_moves: number;
    };
    confidence: number;
  };
  confidence?: number;
  narration?: GameInsightsNarration;
  narration_meta?: GameInsightsNarrationMeta;
}

type AiSectionTab =
  | "result_summary"
  | "turning_points"
  | "what_you_did_well"
  | "what_to_improve"
  | "next_game_focus";

const AI_SECTION_TABS: Array<{ id: AiSectionTab; label: string; heading: string }> = [
  { id: "result_summary", label: "Result summary", heading: "Result summary" },
  { id: "turning_points", label: "Turning points", heading: "Turning points" },
  { id: "what_you_did_well", label: "What you did well", heading: "What you did well" },
  { id: "what_to_improve", label: "What to improve", heading: "What to improve" },
  { id: "next_game_focus", label: "Next game focus", heading: "Next game focus" },
];

const AI_REQUEST_LOADING_STEPS = [
  "Analyzing your games",
  "Spotting blunders and tactical misses",
  "Finding recurring mistakes",
  "Preparing your next-game focus",
];

const ANON_MOCK_INSIGHTS_VARIANTS: Record<AiSectionTab, string[][]> = {
  result_summary: [
    [
      "You kept practical chances but drifted after one critical middlegame decision.",
      "The position stayed balanced for long stretches before momentum flipped quickly.",
      "Your result mostly hinged on conversion quality in the final phase.",
    ],
    [
      "You built a playable structure and found active pieces in early play.",
      "A single unstable sequence changed the evaluation and reduced your counterplay.",
      "The game narrative suggests strong ideas, but inconsistent execution under pressure.",
    ],
    [
      "Your opening setup created a solid platform for a competitive middlegame.",
      "Key initiative shifted after a forcing line that favored your opponent’s activity.",
      "Overall pattern: good direction, but critical accuracy dipped at decisive moments.",
    ],
  ],
  turning_points: [
    [
      "Around move 14, a forcing continuation gave your opponent the easier plan.",
      "Near move 21, the position simplified into a structure where your weaknesses were fixed.",
      "Late transition to endgame reduced your tactical resources and recovery chances.",
    ],
    [
      "A central break in the middlegame opened lines against your king safety setup.",
      "One exchange decision handed long-term square control to your opponent.",
      "The final conversion phase became technical after your active counterplay disappeared.",
    ],
    [
      "A tempo-loss sequence allowed your opponent to seize initiative in the center.",
      "A defensive inaccuracy turned a holdable position into a difficult defense.",
      "After simplification, your opponent converted with fewer practical risks.",
    ],
  ],
  what_you_did_well: [
    [
      "You consistently developed with purpose and avoided early tactical collapses.",
      "Your piece coordination created useful counterplay windows in the middlegame.",
      "You identified practical resources even when objective evaluation worsened.",
    ],
    [
      "You managed transitions between phases with clear strategic intent.",
      "Your move choices often prioritized activity over passive defense.",
      "You stayed resilient and kept the game complex for a long time.",
    ],
    [
      "You found several stabilizing moves after temporary pressure spikes.",
      "Your structure management delayed direct breakthroughs against your king.",
      "You repeatedly chose plans that preserved practical winning chances.",
    ],
  ],
  what_to_improve: [
    [
      "Recheck forcing replies before committing to irreversible pawn moves.",
      "When ahead in development, convert with simple plans instead of sharp complications.",
      "In equal positions, reduce risk by improving king safety before expansion.",
    ],
    [
      "Prioritize threat detection in transitions from opening to middlegame.",
      "Avoid structural concessions unless they create immediate active compensation.",
      "In tense positions, use one extra move to complete piece coordination first.",
    ],
    [
      "Convert small advantages by limiting counterplay rather than racing attacks.",
      "Treat exchange decisions as strategic commitments and evaluate resulting endgames.",
      "Under pressure, choose robust defensive resources over speculative activity.",
    ],
  ],
  next_game_focus: [
    [
      "Focus on spotting forcing tactical ideas one move earlier.",
      "Build a repeatable checklist for high-volatility middlegame decisions.",
      "Practice clean conversion technique from equal and slightly better endgames.",
    ],
    [
      "Emphasize king safety when central files open unexpectedly.",
      "Use a slower decision cadence in critical transition moments.",
      "Aim for stable advantages before launching tactical sequences.",
    ],
    [
      "Train pattern recognition for initiative swings after exchanges.",
      "Improve time allocation around your opponent’s forcing options.",
      "Prioritize practical simplification when your position is objectively better.",
    ],
  ],
};

const stableIndexFromSeed = (seed: string, modulo: number): number => {
  if (modulo <= 0) return 0;
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  }
  return hash % modulo;
};

const REVIEW_ROW_ORDER: ReviewTag[] = [
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
];

const REVIEW_SUMMARY_ROW_ORDER: ReviewTag[] = [
  "brilliant",
  "great",
  "book",
  "best",
  "good",
  "inaccuracy",
  "mistake",
  "blunder",
];

const REVIEW_LABELS: Record<ReviewTag, string> = {
  brilliant: "Brilliant",
  great: "Great",
  book: "Book",
  best: "Best",
  excellent: "Excellent",
  good: "Good",
  inaccuracy: "Inaccuracy",
  mistake: "Mistake",
  miss: "Miss",
  blunder: "Blunder",
};

const REVIEW_SYMBOLS: Record<ReviewTag, string> = {
  brilliant: "!!",
  great: "!",
  book: "📘",
  best: "⭐",
  excellent: "👍",
  good: "✅",
  inaccuracy: "?!",
  mistake: "?",
  miss: "❌",
  blunder: "??",
};

const REVIEW_ROW_TONES: Record<ReviewTag, string> = {
  brilliant: "text-teal-300",
  great: "text-sky-300",
  book: "text-cyan-300",
  best: "text-emerald-300",
  excellent: "text-green-300",
  good: "text-lime-300",
  inaccuracy: "text-amber-300",
  mistake: "text-orange-400",
  miss: "text-rose-300",
  blunder: "text-red-500",
};

const REVIEW_BADGE_TONES: Record<ReviewTag, string> = {
  brilliant: "border-teal-300/45 bg-teal-500/20 text-teal-100",
  great: "border-sky-300/45 bg-sky-500/22 text-sky-100",
  book: "border-cyan-300/55 bg-cyan-500/20 text-cyan-100",
  best: "border-emerald-300/45 bg-emerald-500/20 text-emerald-100",
  excellent: "border-green-300/40 bg-green-500/18 text-green-100",
  good: "border-lime-300/40 bg-lime-500/16 text-lime-100",
  inaccuracy: "border-amber-300/60 bg-amber-500/26 text-amber-50",
  mistake: "border-orange-400/65 bg-orange-600/30 text-orange-50",
  miss: "border-rose-300/45 bg-rose-500/20 text-rose-100",
  blunder: "border-red-500/70 bg-red-600/35 text-red-50",
};

type ReviewCounts = Record<ReviewTag, number>;

const createEmptyReviewCounts = (): ReviewCounts => ({
  brilliant: 0,
  great: 0,
  book: 0,
  best: 0,
  excellent: 0,
  good: 0,
  inaccuracy: 0,
  mistake: 0,
  miss: 0,
  blunder: 0,
});

const NARRATION_COMPARISON_STOPWORDS = new Set([
  "a",
  "an",
  "and",
  "are",
  "as",
  "at",
  "be",
  "but",
  "by",
  "for",
  "from",
  "in",
  "into",
  "is",
  "it",
  "of",
  "on",
  "or",
  "that",
  "the",
  "this",
  "to",
  "was",
  "were",
  "with",
  "you",
  "your",
]);

const normalizeNarrationComparisonText = (text: string): string =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();

const narrationComparisonTokens = (text: string): string[] => {
  const normalized = normalizeNarrationComparisonText(text);
  if (!normalized) return [];
  return normalized
    .split(" ")
    .filter((token) => token.length > 2 && !NARRATION_COMPARISON_STOPWORDS.has(token));
};

const narrationTextsAreNearDuplicate = (a: string, b: string): boolean => {
  const normalizedA = normalizeNarrationComparisonText(a);
  const normalizedB = normalizeNarrationComparisonText(b);
  if (!normalizedA || !normalizedB) return false;
  if (normalizedA === normalizedB) return true;
  if (normalizedA.includes(normalizedB) || normalizedB.includes(normalizedA)) return true;

  const tokensA = narrationComparisonTokens(normalizedA);
  const tokensB = narrationComparisonTokens(normalizedB);
  if (!tokensA.length || !tokensB.length) return false;

  const tokenSetB = new Set(tokensB);
  let shared = 0;
  for (const token of tokensA) {
    if (tokenSetB.has(token)) {
      shared += 1;
    }
  }
  const overlapA = shared / tokensA.length;
  const overlapB = shared / tokensB.length;
  return overlapA >= 0.72 || overlapB >= 0.72 || (shared >= 4 && overlapA >= 0.6 && overlapB >= 0.6);
};

const compactNarrationBullets = ({
  bullets,
  context = [],
  maxCount = 4,
}: {
  bullets: string[];
  context?: string[];
  maxCount?: number;
}): string[] => {
  if (!bullets.length) return [];
  const compacted: string[] = [];
  for (const bullet of bullets) {
    const trimmed = bullet.trim();
    if (!trimmed) continue;
    if (context.some((candidate) => narrationTextsAreNearDuplicate(trimmed, candidate))) continue;
    if (compacted.some((candidate) => narrationTextsAreNearDuplicate(trimmed, candidate))) continue;
    compacted.push(trimmed);
    if (compacted.length >= maxCount) break;
  }
  return compacted;
};

export default function GameAnalyzerPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const site = params.site as string;
  const username = decodeURIComponent(params.username as string);
  const gameId = params.gameId as string;
  const initialPly = searchParams.get("ply") ? parseInt(searchParams.get("ply")!, 10) : null;
  const { accessToken } = useAuth();

  const authHeaders = useMemo((): Record<string, string> => {
    if (!accessToken) {
      return {};
    }
    return { Authorization: `Bearer ${accessToken}` };
  }, [accessToken]);

  // State
  const [game, setGame] = useState<GameData | null>(null);
  const [moveTree, setMoveTree] = useState<MoveTree>(createMoveTree());
  const [analysisData, setAnalysisData] = useState<FullAnalysisResponse["analysis"] | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysisStatus, setAnalysisStatus] = useState<"idle" | "completed" | "missing" | "processing">("idle");
  const [singleInsights, setSingleInsights] = useState<SingleGameInsightsResponse | null>(null);
  const [singleInsightsStatus, setSingleInsightsStatus] = useState<"idle" | "ready" | "error">("idle");
  const [aiInsightsLoading, setAiInsightsLoading] = useState(false);
  const [aiInsightsRequesting, setAiInsightsRequesting] = useState(false);
  const [aiRequestStepIndex, setAiRequestStepIndex] = useState(0);
  const [aiInsightsError, setAiInsightsError] = useState<string | null>(null);
  const [lessonConsentLoading, setLessonConsentLoading] = useState(false);
  const [lessonConsentSaving, setLessonConsentSaving] = useState(false);
  const [lessonConsentState, setLessonConsentState] = useState<LessonConsentResponse | null>(null);
  const [lessonConsentError, setLessonConsentError] = useState<string | null>(null);
  const [lastGeneratedInsightNonce, setLastGeneratedInsightNonce] = useState(0);
  const [dismissedNonce, setDismissedNonce] = useState<number | null>(null);
  const [activeAnalysisTab, setActiveAnalysisTab] = useState<"engine" | "ai">("engine");
  const [activeAiSectionTab, setActiveAiSectionTab] = useState<AiSectionTab>("result_summary");
  const [reviewMode, setReviewMode] = useState(false);
  const [reviewCurrentPly, setReviewCurrentPly] = useState<number | null>(null);
  
  // Polling for async analysis
  const pollInterval = useRef<NodeJS.Timeout | null>(null);
  const analysisStartTime = useRef<number | null>(null);
  const aiHydratedKey = useRef<string | null>(null);
  const lessonConsentFetchedForUser = useRef<string | null>(null);
  
  // Board settings
  const [orientation, setOrientation] = useState<"white" | "black">("white");
  const [showCoordinates, setShowCoordinates] = useState(true);
  const [showArrows, setShowArrows] = useState(true);
  const [multiPv, setMultiPv] = useState(3);
  const [depth, setDepth] = useState(18);

  const {
    currentResult: localEngineResult,
    evaluateFen,
    isEvaluating: isLocalEvaluating,
    error: localEngineError,
  } = useLocalEngine();

  // Get current node from tree
  const currentNode = useMemo(() => {
    return moveTree.nodes.get(moveTree.currentId) || null;
  }, [moveTree]);

  const currentTactical = useMemo(() => {
    if (!currentNode?.tactical?.tactic_detected) return null;
    return currentNode.tactical;
  }, [currentNode]);

  // Get current position FEN
  const currentFen = useMemo(() => {
    return currentNode?.fen || "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  }, [currentNode]);

  // Get current eval
  const currentEval = useMemo(() => {
    if (currentNode?.eval) {
      return {
        cp: currentNode.eval.cp,
        mate: currentNode.eval.mate,
      };
    }

    if (!localEngineResult?.eval || localEngineResult.fen !== currentFen) {
      return null;
    }

    return {
      cp: localEngineResult.eval.cp,
      mate: localEngineResult.eval.mate,
    };
  }, [currentNode, localEngineResult, currentFen]);

  // Get best move for current position
  const bestMove = useMemo(() => {
    const uci =
      currentNode?.bestMove?.uci ||
      (localEngineResult?.fen === currentFen
        ? localEngineResult.multipv?.[0]?.pv_uci?.[0]
        : undefined);

    if (!uci) return null;
    if (uci.length >= 4) {
      return {
        from: uci.slice(0, 2),
        to: uci.slice(2, 4),
      };
    }
    return null;
  }, [currentNode, localEngineResult, currentFen]);

  // Get last move
  const lastMove = useMemo(() => {
    if (!currentNode?.uci) return null;
    const uci = currentNode.uci;
    if (uci.length >= 4) {
      return {
        from: uci.slice(0, 2),
        to: uci.slice(2, 4),
      };
    }
    return null;
  }, [currentNode]);

  const displayEngineLines = useMemo(() => {
    if (currentNode?.eval?.multiPv && currentNode.eval.multiPv.length > 0) {
      return currentNode.eval.multiPv.map((line) => ({
        cp: line.cp,
        mate: line.mate,
        depth: currentNode.eval?.depth,
        pv: line.pv,
      }));
    }

    if (currentNode?.eval) {
      return [
        {
          cp: currentNode.eval.cp,
          mate: currentNode.eval.mate,
          depth: currentNode.eval.depth,
          pv: currentNode.eval.pv || [],
          pvSan: currentNode.eval.pvSan || [],
        },
      ];
    }

    if (localEngineResult?.fen === currentFen) {
      return localEngineResult.multipv;
    }

    return null;
  }, [currentNode, localEngineResult, currentFen]);

  const displayEngineDepth = useMemo(() => {
    if (currentNode?.eval?.depth) {
      return currentNode.eval.depth;
    }
    if (localEngineResult?.fen === currentFen) {
      return localEngineResult.depth;
    }
    return undefined;
  }, [currentNode, localEngineResult, currentFen]);

  // User accuracy (based on their color)
  const userAccuracy = useMemo(() => {
    if (!analysisData?.summary || !game) return null;
    return game.color === "white"
      ? analysisData.summary.accuracy_white
      : analysisData.summary.accuracy_black;
  }, [analysisData, game]);

  const reviewStats = useMemo(() => {
    if (!analysisData?.moves?.length || !game?.color) return null;

    const white = createEmptyReviewCounts();
    const black = createEmptyReviewCounts();

    const mergeMetaCounts = (
      target: ReviewCounts,
      source: Partial<Record<ReviewTag, number>> | undefined,
    ): boolean => {
      if (!source) return false;
      let hasAny = false;
      for (const tag of REVIEW_ROW_ORDER) {
        const value = source[tag];
        if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
          target[tag] = value;
          hasAny = true;
        }
      }
      return hasAny;
    };

    const hasWhiteMeta = mergeMetaCounts(white, analysisData.meta.review_counts_white);
    const hasBlackMeta = mergeMetaCounts(black, analysisData.meta.review_counts_black);
    const usedMetaCounts = hasWhiteMeta && hasBlackMeta;

    if (!usedMetaCounts) {
      Object.assign(white, createEmptyReviewCounts());
      Object.assign(black, createEmptyReviewCounts());
      const fallbackOpeningPlyCount =
        typeof game?.opening_ply_count === "number" && game.opening_ply_count > 0
          ? game.opening_ply_count
          : null;
      for (const move of analysisData.moves) {
        const isBookByOpening =
          fallbackOpeningPlyCount !== null && (move.ply + 1) <= fallbackOpeningPlyCount;
        const reviewTag = isBookByOpening
          ? "book"
          : toReviewTag(
            move.review_tag,
            move.classification,
            move.cp_loss ?? undefined,
          );
        if (!reviewTag) continue;
        const moverColor = move.ply % 2 === 0 ? "white" : "black";
        if (moverColor === "white") {
          white[reviewTag] += 1;
        } else {
          black[reviewTag] += 1;
        }
      }
    }

    const user = game.color === "white" ? white : black;
    const opponent = game.color === "white" ? black : white;

    const totalFor = (counts: ReviewCounts) =>
      REVIEW_ROW_ORDER.reduce((sum, key) => sum + counts[key], 0);

    return {
      white,
      black,
      user,
      opponent,
      userTotal: totalFor(user),
      opponentTotal: totalFor(opponent),
    };
  }, [
    game?.opening_ply_count,
    analysisData?.meta.review_counts_black,
    analysisData?.meta.review_counts_white,
    analysisData?.moves,
    game?.color,
  ]);

  const currentMoveQuality = useMemo(() => {
    const reviewTag = currentNode
      ? toReviewTag(currentNode.reviewTag, currentNode.classification, currentNode.cpLoss)
      : undefined;
    if (!reviewTag) return null;
    return {
      key: reviewTag,
      label: REVIEW_LABELS[reviewTag],
      symbol: REVIEW_SYMBOLS[reviewTag],
      tone: REVIEW_BADGE_TONES[reviewTag],
    };
  }, [currentNode]);

  const whitePlayerName = useMemo(() => {
    if (!game) return "White";
    return game.color === "white" ? username : (game.opponent || "Unknown");
  }, [game, username]);

  const blackPlayerName = useMemo(() => {
    if (!game) return "Black";
    return game.color === "black" ? username : (game.opponent || "Unknown");
  }, [game, username]);

  const opponentDisplayName = useMemo(() => {
    return game?.opponent || "Opponent";
  }, [game?.opponent]);

  const topSideColor = orientation === "white" ? "black" : "white";
  const bottomSideColor = orientation === "white" ? "white" : "black";

  const topPlayerName = topSideColor === "white" ? whitePlayerName : blackPlayerName;
  const bottomPlayerName = bottomSideColor === "white" ? whitePlayerName : blackPlayerName;

  const normalizedUserResult = useMemo(() => {
    const result = game?.result?.toLowerCase?.();
    if (result === "win" || result === "loss" || result === "draw") return result;
    return null;
  }, [game]);

  const invertResult = useCallback((result: "win" | "loss" | "draw") => {
    if (result === "win") return "loss";
    if (result === "loss") return "win";
    return "draw";
  }, []);

  const resultForSide = useCallback(
    (side: "white" | "black"): "win" | "loss" | "draw" | null => {
      if (!normalizedUserResult || !game?.color) return null;
      return side === game.color ? normalizedUserResult : invertResult(normalizedUserResult);
    },
    [game, invertResult, normalizedUserResult],
  );

  const formatResultLabel = useCallback((result: "win" | "loss" | "draw") => {
    if (result === "win") return "Win";
    if (result === "loss") return "Loss";
    return "Draw";
  }, []);

  const resultTextClasses = useCallback((result: "win" | "loss" | "draw") => {
    const base = "shrink-0 text-xs font-semibold";
    if (result === "win") return `${base} text-[color:var(--zen-success)]`;
    if (result === "loss") return `${base} text-[color:var(--zen-danger)]`;
    return `${base} text-[color:var(--zen-muted)]`;
  }, []);

  const topSideResult = useMemo(() => resultForSide(topSideColor), [resultForSide, topSideColor]);
  const bottomSideResult = useMemo(
    () => resultForSide(bottomSideColor),
    [bottomSideColor, resultForSide],
  );

  const getPlayerStripStyle = useCallback((side: "white" | "black"): CSSProperties => {
    if (side === "white") {
      return {
        background:
          "linear-gradient(90deg, rgba(255,255,255,0.98) 0%, rgba(255,255,255,0.88) 18%, rgba(255,255,255,0.42) 52%, rgba(255,255,255,0.08) 100%)",
        boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.45)",
      };
    }
    return {
      background:
        "linear-gradient(90deg, rgba(10,15,25,0.9) 0%, rgba(14,20,34,0.55) 48%, rgba(14,20,34,0.1) 100%)",
      boxShadow: "inset 0 0 0 1px rgba(148,163,184,0.18)",
    };
  }, []);

  const nodeIdByPly = useMemo(() => {
    const mapping = new Map<number, string>();
    moveTree.nodes.forEach((node) => {
      if (node.ply > 0 && !mapping.has(node.ply)) {
        mapping.set(node.ply, node.id);
      }
    });
    return mapping;
  }, [moveTree]);

  const plyToMoveNumber = useCallback((ply?: number | null) => {
    if (typeof ply !== "number" || !Number.isFinite(ply)) return null;
    return Math.max(1, Math.floor((ply + 1) / 2));
  }, []);

  const replacePlyWithMoveText = useCallback(
    (text: string) =>
      text.replace(/Ply\s+(\d+)/gi, (_full, rawPly) => {
        const parsed = Number(rawPly);
        const moveNumber = plyToMoveNumber(parsed);
        return moveNumber ? `Move ${moveNumber}` : `Ply ${rawPly}`;
      }),
    [plyToMoveNumber]
  );

  const extractExplicitPlyFromText = useCallback((text: string): number | null => {
    const plyMatch = text.match(/Ply\s+(\d+)/i);
    if (!plyMatch) return null;
    const parsed = Number(plyMatch[1]);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }, []);

  const extractMoveNumberFromText = useCallback((text: string): number | null => {
    const moveMatch = text.match(/(?:At\s+)?Move\s+(\d+)/i);
    if (!moveMatch) return null;
    const moveNumber = Number(moveMatch[1]);
    return Number.isFinite(moveNumber) && moveNumber > 0 ? moveNumber : null;
  }, []);

  const extractActorHintFromText = useCallback((text: string): "user" | "opponent" | null => {
    if (/\byour opponent\b/i.test(text)) return "opponent";
    if (/\byour move\b/i.test(text) || /\byou\b/i.test(text) || /\byour\b/i.test(text)) {
      return "user";
    }
    return null;
  }, []);

  const hasAiNarration = useMemo(() => {
    return Boolean(singleInsights?.narration && singleInsights?.narration_meta?.source === "gemini");
  }, [singleInsights?.narration, singleInsights?.narration_meta?.source]);

  const aiNarrationSectionsByHeading = useMemo(() => {
    const mapped = new Map<string, GameInsightsNarrationSection>();
    const sections = singleInsights?.narration?.sections || [];
    for (const section of sections) {
      mapped.set(section.heading.trim().toLowerCase(), section);
    }
    return mapped;
  }, [singleInsights?.narration?.sections]);

  const resultSummarySection = aiNarrationSectionsByHeading.get("result summary");
  const activeAiSectionHeading = AI_SECTION_TABS.find((tab) => tab.id === activeAiSectionTab)?.heading || "Result summary";
  const activeAiSection = aiNarrationSectionsByHeading.get(activeAiSectionHeading.toLowerCase());
  const turningNarrationSection = aiNarrationSectionsByHeading.get("turning points");
  const turningEvents = singleInsights?.turning_points?.events || [];
  const anonMockBulletsByTab = useMemo(() => {
    const seedBase = `${site}:${username}:${gameId}`.toLowerCase();
    const resolved = {} as Record<AiSectionTab, string[]>;
    for (const tab of AI_SECTION_TABS) {
      const variants = ANON_MOCK_INSIGHTS_VARIANTS[tab.id];
      const variantIndex = stableIndexFromSeed(`${seedBase}:${tab.id}`, variants.length);
      resolved[tab.id] = variants[variantIndex] || variants[0] || [];
    }
    return resolved;
  }, [gameId, site, username]);
  const activeAnonMockBullets = anonMockBulletsByTab[activeAiSectionTab] || [];

  const getInsightEventPly = useCallback((event?: InsightEvent | null): number | null => {
    if (!event) return null;
    const candidate = event.anchor?.ply ?? event.ply;
    if (typeof candidate !== "number" || !Number.isFinite(candidate) || candidate <= 0) {
      return null;
    }
    return candidate;
  }, []);

  const doesTurningBulletMatchIndexedEvent = useCallback((bullet: string, event?: InsightEvent | null) => {
    const eventPly = getInsightEventPly(event);
    if (!eventPly) return false;

    const explicitPly = extractExplicitPlyFromText(bullet);
    if (explicitPly !== null && explicitPly !== eventPly) {
      return false;
    }

    const moveNumber = extractMoveNumberFromText(bullet);
    if (moveNumber !== null && plyToMoveNumber(eventPly) !== moveNumber) {
      return false;
    }

    const actorHint = extractActorHintFromText(bullet);
    if (actorHint && event?.actor && actorHint !== event.actor) {
      return false;
    }

    return explicitPly !== null || moveNumber !== null;
  }, [
    extractActorHintFromText,
    extractExplicitPlyFromText,
    extractMoveNumberFromText,
    getInsightEventPly,
    plyToMoveNumber,
  ]);

  const hasGroundedTurningBullets = useMemo(() => {
    if (!turningNarrationSection?.bullets?.length || !turningEvents.length) {
      return false;
    }
    const compareCount = Math.min(turningNarrationSection.bullets.length, turningEvents.length);
    if (compareCount <= 0) return false;
    for (let idx = 0; idx < compareCount; idx += 1) {
      if (!doesTurningBulletMatchIndexedEvent(turningNarrationSection.bullets[idx], turningEvents[idx])) {
        return false;
      }
    }
    return true;
  }, [doesTurningBulletMatchIndexedEvent, turningEvents, turningNarrationSection?.bullets]);

  const resolveLegacyTurningPointEvent = useCallback((bullet: string): InsightEvent | null => {
    if (!turningEvents.length) return null;
    const actorHint = extractActorHintFromText(bullet);
    const explicitPly = extractExplicitPlyFromText(bullet);

    const selectFromCandidates = (candidates: InsightEvent[]): InsightEvent | null => {
      if (!candidates.length) return null;
      if (actorHint) {
        const actorExact = candidates.filter((event) => event.actor === actorHint);
        if (actorExact.length === 1) return actorExact[0];
        if (actorExact.length > 1) {
          candidates = actorExact;
        }
      }

      if (actorHint) {
        const moveNumber = extractMoveNumberFromText(bullet);
        if (moveNumber !== null && game?.color) {
          const expectedPly =
            actorHint === "user"
              ? (game.color === "white" ? (moveNumber * 2) - 1 : moveNumber * 2)
              : (game.color === "white" ? moveNumber * 2 : (moveNumber * 2) - 1);
          const parityMatch = candidates.find((event) => getInsightEventPly(event) === expectedPly);
          if (parityMatch) return parityMatch;
        }
      }

      return candidates.length === 1 ? candidates[0] : null;
    };

    if (explicitPly !== null) {
      const explicitCandidates = turningEvents.filter((event) => getInsightEventPly(event) === explicitPly);
      return selectFromCandidates(explicitCandidates);
    }

    const moveNumber = extractMoveNumberFromText(bullet);
    if (moveNumber === null) return null;
    const moveCandidates = turningEvents.filter((event) => {
      const eventPly = getInsightEventPly(event);
      return eventPly !== null && plyToMoveNumber(eventPly) === moveNumber;
    });
    return selectFromCandidates(moveCandidates);
  }, [
    extractActorHintFromText,
    extractExplicitPlyFromText,
    extractMoveNumberFromText,
    game?.color,
    getInsightEventPly,
    plyToMoveNumber,
    turningEvents,
  ]);

  const resolveTurningPointEventForBullet = useCallback((bullet: string, idx: number): InsightEvent | null => {
    let event: InsightEvent | null = null;

    if (hasGroundedTurningBullets) {
      const indexed = turningEvents[idx];
      if (indexed && doesTurningBulletMatchIndexedEvent(bullet, indexed)) {
        event = indexed;
      }
    } else {
      event = resolveLegacyTurningPointEvent(bullet);
    }

    const targetPly = getInsightEventPly(event);
    if (!event || !targetPly || !nodeIdByPly.has(targetPly)) {
      return null;
    }
    return event;
  }, [
    doesTurningBulletMatchIndexedEvent,
    getInsightEventPly,
    hasGroundedTurningBullets,
    nodeIdByPly,
    resolveLegacyTurningPointEvent,
    turningEvents,
  ]);

  const isObviousResultBullet = useCallback((bullet: string) => {
    const normalized = bullet.trim().toLowerCase();
    if (!normalized) return true;
    return (
      /^you\s+(won|lost|drew|draw)\s+this\s+game\.?$/.test(normalized) ||
      /^the\s+result\s+was\s+(a\s+)?(win|loss|draw)\.?$/.test(normalized)
    );
  }, []);

  const filteredResultSummaryBullets = useMemo(() => {
    const bullets = resultSummarySection?.bullets ?? [];
    const nonObviousBullets = bullets.filter((bullet) => !isObviousResultBullet(bullet));
    const title = singleInsights?.narration?.title ?? "";
    const oneLiner = singleInsights?.narration?.one_liner ?? "";
    return compactNarrationBullets({
      bullets: nonObviousBullets,
      context: [title, oneLiner],
      maxCount: 4,
    });
  }, [
    isObviousResultBullet,
    resultSummarySection?.bullets,
    singleInsights?.narration?.one_liner,
    singleInsights?.narration?.title,
  ]);

  const shouldShowResultSummaryOneLiner = useMemo(() => {
    const oneLiner = singleInsights?.narration?.one_liner?.trim() || "";
    if (!oneLiner) return false;
    const title = singleInsights?.narration?.title || "";
    if (narrationTextsAreNearDuplicate(oneLiner, title)) return false;
    return !filteredResultSummaryBullets.some((bullet) => narrationTextsAreNearDuplicate(oneLiner, bullet));
  }, [
    filteredResultSummaryBullets,
    singleInsights?.narration?.one_liner,
    singleInsights?.narration?.title,
  ]);

  const shouldShowLessonConsentCard = useMemo(() => {
    if (singleInsightsStatus !== "ready" || !singleInsights) return false;
    if (lastGeneratedInsightNonce <= 0) return false;
    if (lessonConsentLoading || !lessonConsentState) return false;
    if (lessonConsentState.consented) return false;
    if (dismissedNonce === lastGeneratedInsightNonce) return false;
    return true;
  }, [
    dismissedNonce,
    lastGeneratedInsightNonce,
    lessonConsentLoading,
    lessonConsentState,
    singleInsights,
    singleInsightsStatus,
  ]);

  const getNarrationSectionBadge = useCallback((heading: string) => {
    const key = heading.toLowerCase();
    if (key.includes("turning")) return "⚠";
    if (key.includes("well")) return "🛡";
    if (key.includes("improve")) return "⚡";
    if (key.includes("focus")) return "🎯";
    return "📋";
  }, []);

  const getNarrationSectionTone = useCallback((heading: string) => {
    const key = heading.toLowerCase();
    if (key.includes("turning")) {
      return {
        cardBorder: "border-amber-300/30",
        badgeBorder: "border-amber-300/40",
        badgeText: "text-amber-200/90",
        headingText: "text-amber-200/80",
      };
    }
    if (key.includes("well")) {
      return {
        cardBorder: "border-emerald-300/25",
        badgeBorder: "border-emerald-300/35",
        badgeText: "text-emerald-200/90",
        headingText: "text-emerald-200/80",
      };
    }
    if (key.includes("improve")) {
      return {
        cardBorder: "border-rose-300/30",
        badgeBorder: "border-rose-300/40",
        badgeText: "text-rose-200/90",
        headingText: "text-rose-200/80",
      };
    }
    if (key.includes("focus")) {
      return {
        cardBorder: "border-sky-300/25",
        badgeBorder: "border-sky-300/35",
        badgeText: "text-sky-200/90",
        headingText: "text-sky-200/80",
      };
    }
    return {
      cardBorder: "border-[color:var(--zen-accent)]/35",
      badgeBorder: "border-[color:var(--zen-border)]",
      badgeText: "text-[color:var(--zen-accent)]",
      headingText: "text-[color:var(--zen-muted)]",
    };
  }, []);

  const getNarrationBulletIcon = useCallback((heading: string, bullet: string) => {
    const key = heading.toLowerCase();
    const text = bullet.toLowerCase();

    if (key.includes("turning")) {
      if (text.includes("decisive") || text.includes("collapse") || text.includes("sealed")) return "💥";
      if (text.includes("improved") || text.includes("stabil")) return "🛡";
      return "⚔";
    }
    if (key.includes("well")) return "✅";
    if (key.includes("improve")) {
      if (text.includes("blunder") || text.includes("mistake") || text.includes("oversight")) return "🚨";
      return "⚠";
    }
    if (key.includes("focus")) return "🎯";
    if (text.includes("loss") || text.includes("failed")) return "⚠";
    if (text.includes("advantage") || text.includes("critical")) return "📌";
    return "•";
  }, []);

  const getTacticalIcon = useCallback((tactic?: string | null) => {
    if (tactic === "FORCED_MATE") return "☠";
    if (tactic === "MISSED_FORCED_MATE") return "♛";
    if (tactic === "HANGING_PIECE") return "🚨";
    if (tactic === "FORK") return "♞";
    if (tactic === "SKEWER") return "➤";
    if (tactic === "DOUBLE_ATTACK") return "⚔";
    return "🎯";
  }, []);

  const getTacticalLabel = useCallback((tactic?: string | null) => {
    if (tactic === "FORCED_MATE") return "Forced Mate";
    if (tactic === "MISSED_FORCED_MATE") return "Missed Forced Mate";
    if (tactic === "HANGING_PIECE") return "Hanging Piece";
    if (tactic === "FORK") return "Fork";
    if (tactic === "SKEWER") return "Skewer";
    if (tactic === "DOUBLE_ATTACK") return "Double Attack";
    return "Tactical";
  }, []);

  const getTacticalTone = useCallback((tactic?: string | null) => {
    if (tactic === "FORCED_MATE") {
      return {
        border: "border-[color:var(--zen-danger)]/40",
        badge: "border-[color:var(--zen-danger)]/50 text-[color:var(--zen-danger)]",
      };
    }
    if (tactic === "HANGING_PIECE") {
      return {
        border: "border-orange-300/35",
        badge: "border-orange-300/45 text-orange-200",
      };
    }
    if (tactic === "MISSED_FORCED_MATE" || tactic === "SKEWER") {
      return {
        border: "border-red-300/35",
        badge: "border-red-300/45 text-red-200",
      };
    }
    return {
      border: "border-[color:var(--zen-accent)]/35",
      badge: "border-[color:var(--zen-border)] text-[color:var(--zen-accent)]",
    };
  }, []);

  const buildTacticalSummary = useCallback(
    (tactical: TacticalAnnotation) => {
      const tacticType = tactical.tactic_type;
      const missed = tactical.missed_move_san || tactical.missed_move_uci || null;

      if (tacticType === "FORCED_MATE") {
        const mateIn = tactical.mate_outcome?.mate_in;
        const isBackRank = tactical.mate_outcome?.subtype === "back_rank";
        if (mateIn) {
          return isBackRank
            ? `Allowed a forced back-rank mating sequence (mate in ${mateIn}).`
            : `Allowed a forced mating sequence (mate in ${mateIn}).`;
        }
        return isBackRank
          ? "Allowed a forced back-rank mating sequence."
          : "Allowed a forced mating sequence.";
      }

      if (tacticType === "MISSED_FORCED_MATE") {
        const mateIn = tactical.mate_outcome?.mate_in;
        if (mateIn) {
          return missed
            ? `Missed ${missed}, which forced mate in ${mateIn}.`
            : `Missed a forcing line with mate in ${mateIn}.`;
        }
        return missed
          ? `Missed ${missed}, a forcing mating line.`
          : "Missed a forcing mating line.";
      }

      if (tacticType === "HANGING_PIECE") {
        const hangingPiece = tactical.hanging_piece_name?.toLowerCase();
        if (hangingPiece) {
          return `Hung a ${hangingPiece}; opponent had a concrete capture sequence.`;
        }
        return "Hung a piece; opponent had an immediate capture.";
      }

      if (tacticType === "SKEWER") {
        const front = tactical.skewer_front_piece?.toLowerCase();
        const rear = tactical.skewer_rear_piece?.toLowerCase();
        if (front && rear) {
          return missed
            ? `Missed ${missed}, a skewer on the ${front} with a ${rear} behind it.`
            : `Missed a skewer on the ${front} with a ${rear} behind it.`;
        }
        return missed
          ? `Missed ${missed}, a skewer tactic with concrete follow-up gain.`
          : "Missed a skewer tactic with concrete follow-up gain.";
      }

      if (tacticType === "FORK" || tacticType === "DOUBLE_ATTACK") {
        if (missed) {
          return `Missed ${missed}, a ${getTacticalLabel(tacticType).toLowerCase()} creating dual tactical threats.`;
        }
        return `Missed a ${getTacticalLabel(tacticType).toLowerCase()} sequence with concrete tactical gain.`;
      }

      return "A concrete tactical motif was detected in this line.";
    },
    [getTacticalLabel]
  );

  const jumpToInsightEvent = useCallback(
    (event: InsightEvent) => {
      const ply = event.anchor?.ply ?? event.ply;
      if (!ply) return;
      const nodeId = nodeIdByPly.get(ply);
      if (!nodeId) return;

      setMoveTree((tree) => navigateTo(tree, nodeId));
    },
    [nodeIdByPly]
  );

  // Navigation handlers
  const handleGoToStart = useCallback(() => {
    setMoveTree((tree) => goToStart(tree));
  }, []);

  const handleGoBack = useCallback(() => {
    setMoveTree((tree) => goBack(tree));
  }, []);

  const handleGoForward = useCallback(() => {
    setMoveTree((tree) => goForward(tree));
  }, []);

  const handleGoToEnd = useCallback(() => {
    setMoveTree((tree) => goToEnd(tree));
  }, []);

  const handleSelectMove = useCallback((nodeId: string) => {
    setMoveTree((tree) => navigateTo(tree, nodeId));
  }, []);

  const handleStartReview = useCallback(() => {
    const firstMoveNodeId = nodeIdByPly.get(1);
    if (!firstMoveNodeId) return;
    setReviewMode(true);
    setReviewCurrentPly(1);
    setMoveTree((tree) => navigateTo(tree, firstMoveNodeId));
    trackEvent("feature.usage", {
      properties: {
        feature: "game_review_start",
      },
    });
  }, [nodeIdByPly]);

  const handleExitReview = useCallback(() => {
    setReviewMode(false);
    setReviewCurrentPly(null);
    setMoveTree((tree) => goToStart(tree));
  }, []);

  useEffect(() => {
    if (!reviewMode) {
      if (reviewCurrentPly !== null) {
        setReviewCurrentPly(null);
      }
      return;
    }
    const node = moveTree.nodes.get(moveTree.currentId);
    const currentPly = node?.ply ?? null;
    setReviewCurrentPly(currentPly);
  }, [moveTree, reviewCurrentPly, reviewMode]);

  // Handle user making a move on the board
  const handleUserMove = useCallback(
    (from: string, to: string, promotion?: string): boolean => {
      try {
        const chess = new Chess(currentFen);
        const move = chess.move({ from, to, promotion });
        
        if (!move) return false;

        // Add move to tree as a variation
        setMoveTree((tree) =>
          addMove(tree, move.san, move.lan, chess.fen())
        );

        return true;
      } catch {
        return false;
      }
    },
    [currentFen]
  );

  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return;
      }
      
      switch (e.key) {
        case "ArrowLeft":
          e.preventDefault();
          handleGoBack();
          break;
        case "ArrowRight":
          e.preventDefault();
          handleGoForward();
          break;
        case "Home":
          e.preventDefault();
          handleGoToStart();
          break;
        case "End":
          e.preventDefault();
          handleGoToEnd();
          break;
        case "f":
        case "F":
          setOrientation((o) => (o === "white" ? "black" : "white"));
          break;
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleGoBack, handleGoForward, handleGoToStart, handleGoToEnd]);

  // Lightweight local analysis for the current position (no backend calls)
  useEffect(() => {
    if (!game) return;
    evaluateFen(currentFen, {
      depth: LOCAL_ENGINE_DEPTH,
      multiPv: LOCAL_ENGINE_MULTIPV,
    });
  }, [game, currentFen, evaluateFen]);

  // Fetch game data on mount
  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      setError(null);
      setAnalyzing(false);
      setAnalysisData(null);
      setAnalysisStatus("idle");
      setSingleInsights(null);
      setSingleInsightsStatus("idle");
      setAiInsightsError(null);
      setAiInsightsLoading(false);
      setAiInsightsRequesting(false);
      setReviewMode(false);
      setReviewCurrentPly(null);
      aiHydratedKey.current = null;
      trackEvent("analysis.light.start", {
        properties: {
          source: "game_page_load",
        },
      });

      try {
        // Fetch game data
        const gameRes = await fetch(
          `${API_BASE_URL}/api/v1/game/${site}/${encodeURIComponent(username)}/${gameId}`,
          { headers: withTrackingHeaders(authHeaders) }
        );
        if (!gameRes.ok) {
          const data = await gameRes.json().catch(() => ({}));
          throw new Error(data.detail || "Failed to fetch game");
        }
        const gameData: GameData = await gameRes.json();
        trackEvent("analysis.light.success", {
          properties: {
            source: "game_page_load",
          },
        });
        trackEvent("game.view", {
          properties: {
            source: "game_page",
          },
        });
        setGame(gameData);
        setOrientation(gameData.color === "black" ? "black" : "white");

        // Build initial move tree from PGN
        if (gameData.pgn) {
          try {
            const chess = new Chess();
            chess.loadPgn(gameData.pgn);
            const history = chess.history({ verbose: true });

            // Build simple tree from PGN
            let tree = createMoveTree();
            const tempChess = new Chess();
            for (const move of history) {
              tempChess.move(move.san);
              tree = addMove(tree, move.san, move.lan, tempChess.fen());
            }
            
            if (initialPly !== null && initialPly >= 0) {
              const treePly = initialPly + 1;
              let targetId: string | null = null;
              for (const node of Array.from(tree.nodes.values())) {
                if (node.ply === treePly) { targetId = node.id; break; }
              }
              tree = targetId ? navigateTo(tree, targetId) : goToStart(tree);
            } else {
              tree = goToStart(tree);
            }
            setMoveTree(tree);
          } catch (e) {
            console.error("Failed to parse PGN:", e);
          }
        }
      } catch (err) {
        trackEvent("analysis.light.failed", {
          properties: {
            source: "game_page_load",
            reason: err instanceof Error ? err.message : "An error occurred",
          },
        });
        setError(err instanceof Error ? err.message : "An error occurred");
      } finally {
        setLoading(false);
      }
    };

    if (username && gameId) {
      fetchData();
    }
  }, [site, username, gameId, authHeaders]);

  // Stop polling
  const stopPolling = useCallback(() => {
    if (pollInterval.current) {
      clearInterval(pollInterval.current);
      pollInterval.current = null;
    }
  }, []);

  const buildFullAnalysisUrl = useCallback(
    (force = false) => {
      const params = new URLSearchParams({
        depth: String(depth),
        multipv: String(multiPv),
      });
      if (force) {
        params.set("force", "1");
      }
      return `${API_BASE_URL}/api/v1/analysis/${site}/${encodeURIComponent(username)}/${gameId}/full?${params.toString()}`;
    },
    [depth, multiPv, site, username, gameId]
  );

  const buildAiInsightsUrl = useCallback(
    (force = false) => {
      const params = new URLSearchParams({
        depth: String(depth),
        multipv: String(multiPv),
      });
      if (force) {
        params.set("force", "1");
      }
      return `${API_BASE_URL}/api/v1/analysis/${site}/${encodeURIComponent(username)}/${gameId}/ai-insights?${params.toString()}`;
    },
    [depth, multiPv, site, username, gameId]
  );

  const lessonConsentUrl = `${API_BASE_URL}/api/v1/auth/lesson-consent`;
  const lessonConsentUserKey = session?.userId || session?.user?.email || null;

  const getSignupReturnPath = useCallback((): string => {
    if (typeof window !== "undefined") {
      return `${window.location.pathname}${window.location.search}`;
    }
    return `/game/${encodeURIComponent(site)}/${encodeURIComponent(username)}/${encodeURIComponent(gameId)}`;
  }, [site, username, gameId]);

  // Handle analysis completion
  const handleAnalysisReady = useCallback((data: FullAnalysisResponse) => {
    if (data.analysis) {
      setAnalysisData(data.analysis);
      setAnalysisStatus("completed");

      // Rebuild tree with analysis data
      let tree = buildTreeFromAnalysis(data.analysis.moves, undefined, game?.opening_ply_count);
      
      if (initialPly !== null && initialPly >= 0) {
        const treePly = initialPly + 1;
        for (const node of Array.from(tree.nodes.values())) {
          if (node.ply === treePly) {
            tree = navigateTo(tree, node.id);
            break;
          }
        }
      }
      
      setMoveTree(tree);
      setReviewMode(false);
      setReviewCurrentPly(null);

      // Log success
      const elapsedSeconds = analysisStartTime.current 
        ? Math.round((Date.now() - analysisStartTime.current) / 1000)
        : 0;
      console.log(`[Analysis] Completed in ${elapsedSeconds}s - ${data.analysis.moves.length} moves analyzed`);
    }
    setAnalyzing(false);
    analysisStartTime.current = null;
  }, [game?.opening_ply_count, initialPly]);

  // Hydrate cached in-depth analysis on page load (does not start a new job)
  useEffect(() => {
    const hydrateCachedAnalysis = async () => {
      if (!game) return;
      if (analysisStatus === "completed" || analyzing) return;

      try {
        const res = await fetch(buildFullAnalysisUrl(), { headers: withTrackingHeaders(authHeaders) });
        if (!res.ok) return;
        const data: FullAnalysisResponse = await res.json();
        if (data.status === "completed" && data.analysis) {
          handleAnalysisReady(data);
        }
      } catch (err) {
        console.error("[Analysis] Cached hydration check failed:", err);
      }
    };

    hydrateCachedAnalysis();
  }, [
    game,
    site,
    username,
    gameId,
    authHeaders,
    analysisStatus,
    analyzing,
    handleAnalysisReady,
    buildFullAnalysisUrl,
  ]);

  // Start polling for analysis status
  const startPolling = useCallback(() => {
    // Don't start if already polling
    if (pollInterval.current) {
      console.log("[Analysis] Polling already active, skipping...");
      return;
    }
    
    console.log("[Analysis] Starting polling...");
    pollInterval.current = setInterval(async () => {
      try {
        const res = await fetch(buildFullAnalysisUrl(), { headers: withTrackingHeaders(authHeaders) });
        const data: FullAnalysisResponse = await res.json();
        
        if (data.status === "completed") {
          trackEvent("analysis.deep.completed", {
            properties: {
              source: "polling",
            },
          });
          stopPolling();
          handleAnalysisReady(data);
        } else if (data.status === "missing") {
          // Job failed/disappeared - stop polling
          console.log("[Analysis] Job disappeared (likely failed). User can retry.");
          stopPolling();
          setAnalyzing(false);
          setAnalysisStatus(analysisData ? "completed" : "missing");
          analysisStartTime.current = null;
        }
        // If still "processing", continue polling
      } catch (err) {
        // Network error - keep polling
        console.error("[Analysis] Polling error:", err);
      }
    }, 2000); // Poll every 2 seconds
  }, [analysisData, stopPolling, handleAnalysisReady, authHeaders, buildFullAnalysisUrl]);

  // Run full analysis (starts background job and begins polling)
  const runAnalysis = useCallback(async (force = false) => {
    trackEvent("analysis.deep.requested", {
      properties: {
        force,
      },
    });
    const preserveCompletedState = force && analysisStatus === "completed";
    setAnalyzing(true);
    setError(null);
    setReviewMode(false);
    setReviewCurrentPly(null);
    if (!preserveCompletedState) {
      setSingleInsights(null);
      setSingleInsightsStatus("idle");
      setAiInsightsError(null);
      aiHydratedKey.current = null;
    }
    analysisStartTime.current = Date.now();

    console.log(
      `[Analysis] Starting analysis for game ${gameId} (depth=${depth}, multipv=${multiPv}, force=${force})`
    );

    try {
      const res = await fetch(buildFullAnalysisUrl(force), {
        method: "POST",
        headers: withTrackingHeaders(authHeaders),
      });

      // Handle 429 Too Many Requests
      if (res.status === 429) {
        const data = await res.json().catch(() => ({}));
        trackEvent("analysis.deep.failed", {
          properties: {
            reason: data.detail || "Rate limited",
          },
        });
        setError(data.detail || "Server is busy (max 2 analyses at once). Please try again shortly.");
        setAnalyzing(false);
        setAnalysisStatus(preserveCompletedState ? "completed" : "missing");
        analysisStartTime.current = null;
        return;
      }

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        trackEvent("analysis.deep.failed", {
          properties: {
            reason: data.detail || "Analysis failed",
          },
        });
        throw new Error(data.detail || "Analysis failed");
      }

      const data: FullAnalysisResponse = await res.json();
      
      if (data.status === "completed" && data.analysis) {
        // Already cached, use immediately
        trackEvent("analysis.deep.completed", {
          properties: {
            source: "cached_response",
          },
        });
        handleAnalysisReady(data);
      } else if (data.status === "processing") {
        // Analysis started - set state, polling will be started by separate effect
        trackEvent("analysis.deep.started", {
          properties: {
            force,
          },
        });
        setAnalysisStatus("processing");
        // analysisStatus change + analyzing=true will trigger polling effect
      } else {
        setAnalyzing(false);
        setAnalysisStatus(preserveCompletedState ? "completed" : "missing");
        analysisStartTime.current = null;
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Analysis failed";
      console.error(`[Analysis] Failed:`, err);
      trackEvent("analysis.deep.failed", {
        properties: {
          reason: errorMessage,
        },
      });
      setError(errorMessage);
      setAnalyzing(false);
      setAnalysisStatus(preserveCompletedState ? "completed" : "missing");
      analysisStartTime.current = null;
    }
  }, [
    gameId,
    depth,
    multiPv,
    analysisStatus,
    handleAnalysisReady,
    authHeaders,
    buildFullAnalysisUrl,
  ]);

  const handleAnalysisTabChange = useCallback((tab: "engine" | "ai") => {
    if (tab === activeAnalysisTab) {
      return;
    }
    setActiveAnalysisTab(tab);
    trackEvent("feature.usage", {
      properties: {
        feature: "game_analysis_tab_switch",
        tab,
      },
    });
  }, [activeAnalysisTab]);

  const centerAiSectionTab = useCallback((element?: HTMLElement | null) => {
    if (!element) return;
    element.scrollIntoView({
      behavior: "smooth",
      inline: "center",
      block: "nearest",
    });
  }, []);

  const handleAiSectionTabChange = useCallback((tab: AiSectionTab, tabElement?: HTMLButtonElement | null) => {
    if (tab === activeAiSectionTab) {
      centerAiSectionTab(tabElement);
      return;
    }
    setActiveAiSectionTab(tab);
    centerAiSectionTab(tabElement);
    trackEvent("feature.usage", {
      properties: {
        feature: "game_ai_section_tab_switch",
        tab,
      },
    });
  }, [activeAiSectionTab, centerAiSectionTab]);

  const aiInsightsCacheKey = useMemo(
    () => `${site}:${username}:${gameId}:${depth}:${multiPv}`,
    [site, username, gameId, depth, multiPv]
  );

  const hydrateLessonConsent = useCallback(async () => {
    if (!accessToken) return;
    setLessonConsentLoading(true);
    setLessonConsentError(null);
    try {
      const res = await fetch(lessonConsentUrl, {
        headers: withTrackingHeaders(authHeaders),
      });
      if (!res.ok) {
        throw new Error("Failed to load lesson consent status");
      }
      const data: LessonConsentResponse = await res.json();
      setLessonConsentState(data);
      lessonConsentFetchedForUser.current = lessonConsentUserKey;
    } catch {
      setLessonConsentError("Unable to load lesson-email preference right now.");
      setLessonConsentState(null);
      lessonConsentFetchedForUser.current = null;
    } finally {
      setLessonConsentLoading(false);
    }
  }, [authHeaders, accessToken, lessonConsentUrl, lessonConsentUserKey]);

  useEffect(() => {
    if (!accessToken) return;
    if (activeAnalysisTab !== "ai") return;
    if (!lessonConsentUserKey) return;
    if (lessonConsentFetchedForUser.current === lessonConsentUserKey) return;
    void hydrateLessonConsent();
  }, [
    activeAnalysisTab,
    hydrateLessonConsent,
    accessToken,
    lessonConsentUserKey,
  ]);

  const saveLessonConsentDecision = useCallback(async (decision: LessonConsentDecision) => {
    if (!accessToken) return;
    setLessonConsentSaving(true);
    setLessonConsentError(null);
    try {
      const res = await fetch(lessonConsentUrl, {
        method: "POST",
        headers: withTrackingHeaders({
          "Content-Type": "application/json",
          ...authHeaders,
        } as Record<string, string>),
        body: JSON.stringify({
          decision,
          source: "game_ai_summary",
          site,
          site_game_id: gameId,
          analysis_depth: depth,
          analysis_multipv: multiPv,
        }),
      });
      if (!res.ok) {
        throw new Error("Failed to save lesson consent decision");
      }
      const data: LessonConsentResponse = await res.json();
      setLessonConsentState(data);
      if (decision === "declined") {
        setDismissedNonce(lastGeneratedInsightNonce);
      } else {
        setDismissedNonce(null);
      }
      trackEvent("feature.usage", {
        properties: {
          feature: "lesson_email_consent",
          decision,
          source: "game_ai_summary",
          channel: "email_lessons",
        },
      });
    } catch {
      setLessonConsentError("Could not save your preference. Please try again.");
    } finally {
      setLessonConsentSaving(false);
    }
  }, [
    authHeaders,
    depth,
    gameId,
    accessToken,
    lastGeneratedInsightNonce,
    lessonConsentUrl,
    multiPv,
    site,
  ]);

  const hydrateAiInsights = useCallback(async () => {
    if (!accessToken || analysisStatus !== "completed") {
      return;
    }
    setAiInsightsLoading(true);
    setAiInsightsError(null);
    try {
      const res = await fetch(buildAiInsightsUrl(), {
        headers: withTrackingHeaders(authHeaders),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setSingleInsights(null);
        setSingleInsightsStatus("idle");
        setAiInsightsError(data.detail || "Unable to load AI insights.");
        return;
      }
      const data: AIInsightsResponse = await res.json();
      if (data.status === "ready" && data.insights) {
        setSingleInsights(data.insights);
        setSingleInsightsStatus("ready");
        setAiInsightsError(null);
        return;
      }
      setSingleInsights(null);
      setSingleInsightsStatus("idle");
      setAiInsightsError(null);
    } catch {
      setSingleInsights(null);
      setSingleInsightsStatus("idle");
      setAiInsightsError("Unable to load AI insights right now.");
    } finally {
      setAiInsightsLoading(false);
    }
  }, [analysisStatus, authHeaders, buildAiInsightsUrl, accessToken]);

  useEffect(() => {
    if (!accessToken || analysisStatus !== "completed") {
      return;
    }
    if (aiHydratedKey.current === aiInsightsCacheKey) {
      return;
    }
    aiHydratedKey.current = aiInsightsCacheKey;
    void hydrateAiInsights();
  }, [analysisStatus, aiInsightsCacheKey, hydrateAiInsights, accessToken]);

  useEffect(() => {
    if (!aiInsightsRequesting) {
      setAiRequestStepIndex(0);
      return;
    }
    const interval = setInterval(() => {
      setAiRequestStepIndex((prev) => (prev + 1) % AI_REQUEST_LOADING_STEPS.length);
    }, 1400);
    return () => clearInterval(interval);
  }, [aiInsightsRequesting]);

  const handleRequestAiInsights = useCallback(async () => {
    if (analysisStatus !== "completed") {
      setAiInsightsError("Run in-depth analysis before requesting AI insights.");
      return;
    }

    trackEvent("analysis.ai.requested", {
      properties: {
        source: "game_ai_tab",
      },
    });
    setAiInsightsRequesting(true);
    setAiInsightsError(null);

    try {
      const res = await fetch(buildAiInsightsUrl(), {
        method: "POST",
        headers: withTrackingHeaders(authHeaders),
      });
      const data: AIInsightsResponse = await res.json().catch(() => ({
        status: "generation_failed",
        insights: null,
        created_at: null,
        detail: "AI insights request failed.",
      }));

      if (!res.ok) {
        trackEvent("analysis.ai.failed", {
          properties: {
            reason: data.detail || `Status ${res.status}`,
          },
        });
        setAiInsightsError(data.detail || "AI insights request failed.");
        setSingleInsights(null);
        setSingleInsightsStatus("error");
        return;
      }

      if (data.status === "ready" && data.insights) {
        trackEvent("analysis.ai.completed", {
          properties: {
            source: "game_ai_tab",
          },
        });
        setSingleInsights(data.insights);
        setSingleInsightsStatus("ready");
        setAiInsightsError(null);
        setLastGeneratedInsightNonce((prev) => prev + 1);
        setDismissedNonce(null);
        setLessonConsentError(null);
        aiHydratedKey.current = aiInsightsCacheKey;
        return;
      }

      trackEvent("analysis.ai.failed", {
        properties: {
          reason: data.detail || data.status,
        },
      });
      setSingleInsights(null);
      setSingleInsightsStatus(data.status === "analysis_missing" ? "idle" : "error");
      setAiInsightsError(data.detail || "AI insights request failed.");
    } catch {
      trackEvent("analysis.ai.failed", {
        properties: {
          reason: "Network error",
        },
      });
      setSingleInsights(null);
      setSingleInsightsStatus("error");
      setAiInsightsError("AI insights request failed. Please try again.");
    } finally {
      setAiInsightsRequesting(false);
    }
  }, [
    aiInsightsCacheKey,
    analysisStatus,
    authHeaders,
    buildAiInsightsUrl,
  ]);

  useEffect(() => {
    if (!accessToken) return;
    if (!lessonConsentUserKey) return;
    if (lessonConsentFetchedForUser.current && lessonConsentFetchedForUser.current !== lessonConsentUserKey) {
      lessonConsentFetchedForUser.current = null;
      setLessonConsentState(null);
      setLessonConsentError(null);
      setLastGeneratedInsightNonce(0);
      setDismissedNonce(null);
    }
  }, [accessToken, lessonConsentUserKey]);

  // Start/stop polling based on status
  useEffect(() => {
    if (analysisStatus === "processing" && analyzing && !pollInterval.current) {
      startPolling();
    }
    
    // Cleanup on unmount or when no longer processing
    return () => {
      if (analysisStatus !== "processing") {
        stopPolling();
      }
    };
  }, [analysisStatus, analyzing, startPolling, stopPolling]);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  // Format date
  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return "Unknown date";
    try {
      return new Date(dateStr).toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      return "Unknown date";
    }
  };

  if (loading) {
    return (
      <main className="analysis-page min-h-screen py-8">
        <div className="max-w-[1380px] mx-auto px-4 relative z-10">
          <div className="py-10 flex justify-center">
            <div className="animate-spin rounded-full h-10 w-10 border border-[color:var(--zen-border)] border-t-[color:var(--zen-accent)]" />
          </div>
        </div>
      </main>
    );
  }

  if (error && !game) {
    return (
      <main className="analysis-page min-h-screen py-8">
        <div className="max-w-[1380px] mx-auto px-4 relative z-10">
          <div className="zen-surface p-8 text-center">
            <p className="text-[color:var(--zen-danger)] mb-4">{error}</p>
            <Link
              href="/"
              className="inline-block zen-pill px-4 py-2 text-sm hover:bg-[color:var(--zen-accent-2)] transition"
            >
              ← Back to home
            </Link>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="analysis-page min-h-screen py-6">
      <div className="max-w-[1380px] mx-auto px-4 relative z-10">
        {/* Main layout */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.24fr)_minmax(0,1fr)]">
          {/* Left: Board + Eval Bar */}
          <div className="lg:min-w-0">
            <div className="zen-surface zen-surface-no-backdrop p-4">
	              <div
	                className="mb-3 rounded-xl px-3 py-2"
	                style={getPlayerStripStyle(topSideColor)}
	              >
	                <div className="flex items-center justify-between gap-3">
	                  <p
	                    className={`truncate text-base font-semibold ${
	                      topSideColor === "white" ? "text-slate-900" : "text-slate-100"
	                    }`}
	                  >
	                    {topPlayerName}
	                  </p>
	                  {topSideResult && (
	                    <span className={resultTextClasses(topSideResult)}>
	                      {formatResultLabel(topSideResult)}
	                    </span>
	                  )}
	                </div>
	              </div>

              <div className="flex gap-3">
                {/* Eval bar */}
                <EvalBar eval={currentEval} orientation={orientation} height={600} />

                {/* Board */}
                <div className="flex-1">
                  <div className="relative inline-block">
                    <AnalysisBoard
                      fen={currentFen}
                      orientation={orientation}
                      onMove={reviewMode ? undefined : handleUserMove}
                      bestMove={showArrows ? bestMove : null}
                      lastMove={lastMove}
                      showArrows={showArrows}
                      showCoordinates={showCoordinates}
                      boardWidth={600}
                    />
                  </div>
                </div>
              </div>

	              <div
	                className="mt-3 rounded-xl px-3 py-2"
	                style={getPlayerStripStyle(bottomSideColor)}
	              >
	                <div className="flex items-center justify-between gap-3">
	                  <p
	                    className={`truncate text-base font-semibold ${
	                      bottomSideColor === "white" ? "text-slate-900" : "text-slate-100"
	                    }`}
	                  >
	                    {bottomPlayerName}
	                  </p>
	                  {bottomSideResult && (
	                    <span className={resultTextClasses(bottomSideResult)}>
	                      {formatResultLabel(bottomSideResult)}
	                    </span>
	                  )}
	                </div>
	              </div>

              {/* Controls */}
              <div className="mt-3">
                <BoardControls
                  onFirst={handleGoToStart}
                  onPrev={handleGoBack}
                  onNext={handleGoForward}
                  onLast={handleGoToEnd}
                  orientation={orientation}
                  onFlip={() => setOrientation((o) => (o === "white" ? "black" : "white"))}
                  showCoordinates={showCoordinates}
                  onToggleCoordinates={() => setShowCoordinates((v) => !v)}
                  showArrows={showArrows}
                  onToggleArrows={() => setShowArrows((v) => !v)}
                  lichessUrl={game?.lichess_url || (
                    site === "lichess" 
                      ? `https://lichess.org/${gameId}`
                      : `https://www.chess.com/game/live/${gameId}`
                  )}
                  site={site}
                />
              </div>
            </div>

          </div>

          {/* Right: Analysis panels */}
          <div className="lg:min-w-0">
            <div className="zen-surface p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => router.back()}
                    className="inline-flex items-center gap-1 rounded-lg border border-[color:var(--zen-border)] px-2.5 py-1 text-xs text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)] transition"
                  >
                    ← Back
                  </button>
                  {userAccuracy !== null && (
                    <div className="inline-flex items-center gap-1 rounded-lg border border-[color:var(--zen-border)] bg-[color:var(--zen-surface-2)] px-2.5 py-1">
                      <span className="text-xs font-semibold text-[color:var(--zen-text)]">{userAccuracy}%</span>
                      <span className="text-[10px] uppercase tracking-wide text-[color:var(--zen-muted)]">Accuracy</span>
                    </div>
                  )}
                </div>
              </div>

              <div
                role="tablist"
                aria-label="Game analysis panels"
                className="mb-4 flex w-full gap-2 rounded-xl border border-[color:var(--zen-border)] bg-[color:var(--zen-surface-2)] p-1"
              >
                <button
                  id="analysis-tab-engine"
                  role="tab"
                  type="button"
                  aria-selected={activeAnalysisTab === "engine"}
                  aria-controls="analysis-tab-panel-engine"
                  tabIndex={activeAnalysisTab === "engine" ? 0 : -1}
                  onClick={() => handleAnalysisTabChange("engine")}
                  className={[
                    "flex-1 rounded-lg px-4 py-2 text-center text-xs font-semibold uppercase tracking-wide transition",
                    activeAnalysisTab === "engine"
                      ? "bg-[color:var(--zen-accent-2)] text-[color:var(--zen-text)]"
                      : "text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)]",
                  ].join(" ")}
                >
                  Engine Analysis
                </button>
                <button
                  id="analysis-tab-ai"
                  role="tab"
                  type="button"
                  aria-selected={activeAnalysisTab === "ai"}
                  aria-controls="analysis-tab-panel-ai"
                  tabIndex={activeAnalysisTab === "ai" ? 0 : -1}
                  onClick={() => handleAnalysisTabChange("ai")}
                  className={[
                    "flex-1 rounded-lg px-4 py-2 text-center text-xs font-semibold uppercase tracking-wide transition",
                    activeAnalysisTab === "ai"
                      ? "bg-[color:var(--zen-accent-2)] text-[color:var(--zen-text)]"
                      : "text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)]",
                  ].join(" ")}
                >
                  <span className="inline-flex items-center justify-center gap-2">
                    <svg className="h-3.5 w-3.5 shrink-0" viewBox="0 0 24 24" aria-hidden="true">
                      <defs>
                        <linearGradient id="geminiSparkGradientTab" x1="0%" y1="0%" x2="100%" y2="100%">
                          <stop offset="0%" stopColor="#ef4444" />
                          <stop offset="28%" stopColor="#f59e0b" />
                          <stop offset="56%" stopColor="#22c55e" />
                          <stop offset="100%" stopColor="#3b82f6" />
                        </linearGradient>
                      </defs>
                      <path
                        d="M12 1.9c1.3 4 3.8 7.3 8.1 10.1-4.3 2.8-6.8 6.1-8.1 10.1-1.3-4-3.8-7.3-8.1-10.1 4.3-2.8 6.8-6.1 8.1-10.1z"
                        fill="url(#geminiSparkGradientTab)"
                      />
                    </svg>
                    AI Summary
                  </span>
                </button>
              </div>

              <div
                id="analysis-tab-panel-engine"
                role="tabpanel"
                aria-labelledby="analysis-tab-engine"
                hidden={activeAnalysisTab !== "engine"}
                className={activeAnalysisTab === "engine" ? "space-y-4" : "hidden"}
              >
                <div>
                  <h3 className="text-sm font-semibold text-[color:var(--zen-text)] mb-3">
                    Engine Analysis
                    {analysisStatus === "completed" && analysisData?.meta.depth ? (
                      <span className="font-normal text-[color:var(--zen-muted)] ml-2">
                        Depth {analysisData.meta.depth}
                      </span>
                    ) : (
                      <span className="font-normal text-[color:var(--zen-muted)] ml-2">
                        Local depth {LOCAL_ENGINE_DEPTH}
                      </span>
                    )}
                  </h3>

                  {!analyzing && analysisStatus !== "completed" && (
                    <div className="text-center py-6">
                      <button
                        onClick={() => runAnalysis(false)}
                        className="zen-pill px-6 py-3 text-sm font-medium bg-[color:var(--zen-accent-2)] hover:bg-[color:var(--zen-accent)] hover:text-white transition"
                      >
                        Request in-depth analysis
                      </button>
                      <p className="text-xs text-[color:var(--zen-muted)] mt-2">
                        Runs backend deep analysis with full engine output.
                      </p>
                    </div>
                  )}

                  {/* {!analyzing && analysisStatus === "completed" && (
                    <div className="text-center py-4">
                      <button
                        onClick={() => runAnalysis(true)}
                        className="zen-pill px-5 py-2.5 text-xs font-semibold uppercase tracking-wide text-[color:var(--zen-text)] border border-[color:var(--zen-border)] hover:border-[color:var(--zen-accent)] hover:text-[color:var(--zen-accent)] transition"
                      >
                        Force re-run in-depth analysis
                      </button>
                      <p className="text-xs text-[color:var(--zen-muted)] mt-2">
                        Bypasses cached deep analysis and starts a fresh backend run.
                      </p>
                    </div>
                  )} */}

                  {analyzing && (
                    <div className="text-center py-6">
                      <div className="inline-flex flex-col items-center gap-2">
                        <div className="inline-flex items-center gap-3 zen-pill px-6 py-3">
                          <div className="animate-spin rounded-full h-5 w-5 border-2 border-[color:var(--zen-border)] border-t-[color:var(--zen-accent)]" />
                          <span className="text-[color:var(--zen-text)]">Analyzing game...</span>
                        </div>
                        <p className="text-xs text-[color:var(--zen-muted)]">
                          Analysis runs in background. You'll see a notification when complete.
                        </p>
                      </div>
                    </div>
                  )}

                  {analysisStatus === "completed" && reviewStats && !reviewMode && (
                    <div className="zen-surface-flat p-3.5 mb-4 border border-[color:var(--zen-border)]">
                      <div className="flex flex-wrap items-end justify-between gap-3 mb-3">
                        <div>
                          <h4 className="text-sm font-semibold text-[color:var(--zen-text)]">Game Review</h4>
                          <p className="text-xs text-[color:var(--zen-muted)]">
                            Review labels and accuracy by side.
                          </p>
                        </div>
                      </div>
                      <div className="rounded-lg border border-[color:var(--zen-border)] overflow-hidden">
                        <div className="grid grid-cols-[1fr_8rem_8rem] gap-3 bg-[color:var(--zen-surface-2)] px-3 py-2 text-[11px] uppercase tracking-wide text-[color:var(--zen-muted)]">
                          <span>Category</span>
                          <span className="text-center">You</span>
                          <span className="text-center">{opponentDisplayName}</span>
                        </div>
                        <div className="divide-y divide-[color:var(--zen-border)]">
                          <div className="grid grid-cols-[1fr_8rem_8rem] items-center gap-3 px-3 py-2">
                            <span className="text-sm font-medium text-[color:var(--zen-muted)]">Player</span>
                            <span className="text-center text-sm font-semibold text-[color:var(--zen-text)]">
                              You
                            </span>
                            <span className="text-center text-sm font-semibold text-[color:var(--zen-text)] truncate">
                              {opponentDisplayName}
                            </span>
                          </div>
                          <div className="grid grid-cols-[1fr_8rem_8rem] items-center gap-3 px-3 py-2">
                            <span className="text-sm font-medium text-[color:var(--zen-muted)]">Accuracy</span>
                            <span className="text-center text-sm font-semibold text-[color:var(--zen-text)]">
                              {userAccuracy ?? "-"}
                            </span>
                            <span className="text-center text-sm font-semibold text-[color:var(--zen-text)]">
                              {analysisData?.summary
                                ? (game?.color === "white"
                                  ? analysisData.summary.accuracy_black
                                  : analysisData.summary.accuracy_white)
                                : "-"}
                            </span>
                          </div>
                          {REVIEW_SUMMARY_ROW_ORDER.map((category) => (
                            <div
                              key={`quality-row-${category}`}
                              className="grid grid-cols-[1fr_8rem_8rem] items-center gap-3 px-3 py-2"
                            >
                              <div className="inline-flex items-center gap-2">
                                <span
                                  className={`inline-flex min-w-[3rem] justify-center rounded-md border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${REVIEW_BADGE_TONES[category]}`}
                                >
                                  {REVIEW_SYMBOLS[category]}
                                </span>
                                <span className={`text-sm font-medium ${REVIEW_ROW_TONES[category]}`}>
                                  {REVIEW_LABELS[category]}
                                </span>
                              </div>
                              <span className={`text-center text-sm font-semibold ${REVIEW_ROW_TONES[category]}`}>
                                {reviewStats.user[category]}
                              </span>
                              <span className={`text-center text-sm font-semibold ${REVIEW_ROW_TONES[category]}`}>
                                {reviewStats.opponent[category]}
                              </span>
                            </div>
                          ))}
                        </div>
                        <div className="border-t border-[color:var(--zen-border)] bg-[color:var(--zen-surface-2)] px-3 py-3">
                          <button
                            type="button"
                            onClick={handleStartReview}
                            className="w-full rounded-xl bg-emerald-600 px-4 py-3 text-base font-semibold text-white transition hover:bg-emerald-500"
                          >
                            Start Review
                          </button>
                        </div>
                      </div>
                    </div>
                  )}

                {reviewMode && currentNode && currentNode.san && (
                  <div className="zen-surface-flat p-4">
                    <div className="mb-3">
                      {currentMoveQuality ? (
                        <span
                          className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-semibold uppercase tracking-wide ${currentMoveQuality.tone}`}
                        >
                          <span>{currentMoveQuality.symbol || "•"}</span>
                          <span>{currentMoveQuality.label}</span>
                        </span>
                      ) : (
                        <span className="text-xs uppercase tracking-wide text-[color:var(--zen-muted)]">
                          Current move
                        </span>
                      )}
                    </div>
                    <div className="text-2xl font-mono font-semibold text-[color:var(--zen-text)]">
                      {currentNode.san}
                    </div>
                    {currentNode.bestMove && currentNode.bestMove.san !== currentNode.san && (
                      <p className="text-sm text-[color:var(--zen-success)] mt-1">
                        Best: <span className="font-mono">{currentNode.bestMove.san}</span>
                      </p>
                    )}
                    {currentTactical && currentNode.classification === "blunder" && (
                      <div className={`zen-surface-flat mt-3 p-3 border ${getTacticalTone(currentTactical.tactic_type).border}`}>
                        <div className="flex items-center justify-between gap-2 mb-2">
                          <span
                            className={`inline-flex items-center gap-2 text-[11px] uppercase tracking-wide px-2 py-1 border ${getTacticalTone(currentTactical.tactic_type).badge}`}
                          >
                            <span>{getTacticalIcon(currentTactical.tactic_type)}</span>
                            <span>{getTacticalLabel(currentTactical.tactic_type)}</span>
                          </span>
                        </div>
                        <p className="text-sm text-[color:var(--zen-text)] leading-6">
                          {buildTacticalSummary(currentTactical)}
                        </p>
                        {currentTactical.missed_move_san && (
                          <p className="text-xs text-[color:var(--zen-muted)] mt-1.5">
                            Missed tactical move: <span className="font-mono">{currentTactical.missed_move_san}</span>
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {localEngineError && (
                  <p className="text-xs text-[color:var(--zen-muted)] mt-3">
                    Local engine: {localEngineError}
                  </p>
                )}
              </div>

                {reviewMode && (
                  <div className="zen-surface-flat p-4">
                    <div className="mb-3 flex items-center justify-between gap-2">
                      <h3 className="text-sm font-semibold text-[color:var(--zen-text)]">
                        Review Moves
                      </h3>
                    </div>
                    <MoveList
                      tree={moveTree}
                      currentId={moveTree.currentId}
                      onSelectMove={handleSelectMove}
                      maxHeight={280}
                    />
                    <div className="mt-3 border-t border-[color:var(--zen-border)] pt-3">
                      <button
                        type="button"
                        onClick={handleExitReview}
                        className="w-full rounded-xl bg-emerald-600 px-4 py-3 text-base font-semibold text-white transition hover:bg-emerald-500"
                      >
                        End Review
                      </button>
                    </div>
                  </div>
                )}

                {!reviewMode && analysisStatus !== "completed" && (
                  <div className="zen-surface-flat p-4">
                    <h3 className="text-sm font-semibold text-[color:var(--zen-text)] mb-3">Moves</h3>
                    <MoveList
                      tree={moveTree}
                      currentId={moveTree.currentId}
                      onSelectMove={handleSelectMove}
                      maxHeight={280}
                    />
                  </div>
                )}

                {error && (
                  <div className="zen-surface-flat p-4 border border-[color:var(--zen-danger)]/30">
                    <p className="text-sm text-[color:var(--zen-danger)]">{error}</p>
                  </div>
                )}
              </div>

              <div
                id="analysis-tab-panel-ai"
                role="tabpanel"
                aria-labelledby="analysis-tab-ai"
                hidden={activeAnalysisTab !== "ai"}
                className={activeAnalysisTab === "ai" ? "space-y-4" : "hidden"}
              >
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-[color:var(--zen-text)]">Game Insights</h3>
                  {singleInsights?.confidence !== undefined && (
                    <span className="text-xs text-[color:var(--zen-muted)]">
                      {Math.round((singleInsights.confidence || 0) * 100)}% confidence
                    </span>
                  )}
                </div>

                <div className="zen-surface-flat p-3 border border-[color:var(--zen-border)]">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                    <p className="text-xs text-[color:var(--zen-muted)]">
                      you can only do 2 AI insights per day
                    </p>
                    <button
                      type="button"
                      onClick={handleRequestAiInsights}
                      disabled={
                        aiInsightsRequesting ||
                        aiInsightsLoading ||
                        analysisStatus !== "completed"
                      }
                      className="zen-pill inline-flex items-center gap-2 px-4 py-2 text-sm font-medium bg-[color:var(--zen-accent-2)] hover:bg-[color:var(--zen-accent)] hover:text-white transition disabled:opacity-60 disabled:cursor-not-allowed"
                    >
                      {aiInsightsRequesting ? "Generating insights..." : "Request AI insights"}
                    </button>
                  </div>
                  {aiInsightsRequesting && (
                    <div className="mt-3 rounded-lg border border-[color:var(--zen-border)] bg-[color:var(--zen-surface-2)] p-3">
                      <div className="flex items-center gap-2">
                        <span className="inline-block h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-[color:var(--zen-accent)] border-t-transparent" />
                        <p className="text-sm font-medium text-[color:var(--zen-text)]">
                          {AI_REQUEST_LOADING_STEPS[aiRequestStepIndex]}...
                        </p>
                      </div>
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[color:var(--zen-border)]">
                        <div className="h-full w-1/2 rounded-full bg-[color:var(--zen-accent)] animate-pulse" />
                      </div>
                      <p className="mt-2 text-xs text-[color:var(--zen-muted)]">
                        Building account-specific AI insights.
                      </p>
                    </div>
                  )}
                  {aiInsightsLoading && !aiInsightsRequesting && (
                    <p className="mt-1 text-xs text-[color:var(--zen-muted)]">
                      Loading your saved AI insights...
                    </p>
                  )}
                </div>

                {analysisStatus !== "completed" && (
                  <div className="rounded-xl border border-amber-300/25 bg-amber-500/10 p-4 text-amber-50">
                    <div className="flex items-start gap-3">
                      <svg
                        className="mt-0.5 h-4 w-4 shrink-0 text-amber-200"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        aria-hidden="true"
                      >
                        <circle cx="12" cy="12" r="10" />
                        <path d="M12 16v-4" />
                        <path d="M12 8h.01" />
                      </svg>
                      <p className="text-sm font-medium">
                        Run in-depth analysis before requesting AI insights.
                      </p>
                    </div>
                  </div>
                )}

                {analysisStatus === "completed" && (
                  <>
                    {aiInsightsError && (
                      <p className="text-sm text-[color:var(--zen-danger)]">
                        {aiInsightsError}
                      </p>
                    )}
                    {singleInsightsStatus === "idle" && !aiInsightsRequesting && (
                      <p className="text-sm text-[color:var(--zen-muted)]">
                        Request AI insights to generate your account-specific summary.
                      </p>
                    )}

                    {singleInsightsStatus === "ready" && singleInsights && (
                      <div className="space-y-4">
                        {hasAiNarration && singleInsights.narration ? (
                          <>
                            <div
                              role="tablist"
                              aria-label="AI insight sections"
                              className="hide-scrollbar flex w-full gap-2 overflow-x-auto overflow-y-hidden rounded-xl border border-[color:var(--zen-border)] bg-[color:var(--zen-surface-2)] p-1"
                            >
                              {AI_SECTION_TABS.map((tab) => (
                                <button
                                  key={tab.id}
                                  id={`ai-section-tab-${tab.id}`}
                                  role="tab"
                                  type="button"
                                  aria-selected={activeAiSectionTab === tab.id}
                                  aria-controls={`ai-section-panel-${tab.id}`}
                                  tabIndex={activeAiSectionTab === tab.id ? 0 : -1}
                                  onClick={(event) =>
                                    handleAiSectionTabChange(tab.id, event.currentTarget)
                                  }
                                  className={[
                                    "whitespace-nowrap rounded-lg px-3 py-2 text-xs font-semibold uppercase tracking-wide transition",
                                    activeAiSectionTab === tab.id
                                      ? "bg-[color:var(--zen-accent-2)] text-[color:var(--zen-text)]"
                                      : "text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)]",
                                  ].join(" ")}
                                >
                                  {tab.label}
                                </button>
                              ))}
                            </div>

                            {activeAiSectionTab === "result_summary" ? (
                              <div
                                id="ai-section-panel-result_summary"
                                role="tabpanel"
                                aria-labelledby="ai-section-tab-result_summary"
                                className="zen-surface-flat p-4 md:p-5 space-y-4 border border-[color:var(--zen-accent)]/45"
                                style={{
                                  background:
                                    "linear-gradient(145deg, rgba(24,30,44,0.92), rgba(20,26,38,0.9) 52%, rgba(17,23,34,0.9))",
                                  boxShadow:
                                    "inset 0 0 0 1px rgba(120,132,160,0.2), inset 0 0 0 2px rgba(84,98,132,0.16), 0 10px 24px rgba(0,0,0,0.24)",
                                }}
                              >
                                <div className="flex items-start justify-between gap-3">
                                  <div>
                                    <p className="text-[11px] uppercase tracking-[0.18em] text-[color:var(--zen-muted)] mb-1">
                                      Result Summary
                                    </p>
                                    <p className="text-lg font-semibold text-[color:var(--zen-text)]">
                                      {singleInsights.narration.title}
                                    </p>
                                  </div>
                                  <span className="text-[11px] px-2 py-1 border border-[color:var(--zen-border)] text-[color:var(--zen-muted)] whitespace-nowrap bg-white/5">
                                    {Math.round((singleInsights.confidence || 0) * 100)}% confidence
                                  </span>
                                </div>

                                {shouldShowResultSummaryOneLiner && (
                                  <p className="text-[15px] leading-7 text-[color:var(--zen-text)]">
                                    {singleInsights.narration.one_liner}
                                  </p>
                                )}
                                <div className="flex flex-wrap gap-2">
                                  <span className="text-[11px] px-2 py-1 border border-[color:var(--zen-border)] text-[color:var(--zen-muted)] bg-white/5">
                                    Decisive phase: {singleInsights.narration.labels.decisive_phase}
                                  </span>
                                  <span className="text-[11px] px-2 py-1 border border-[color:var(--zen-border)] text-[color:var(--zen-muted)] bg-white/5">
                                    Style: {singleInsights.narration.labels.player_style}
                                  </span>
                                </div>

                                {filteredResultSummaryBullets.length ? (
                                  <ul className="space-y-2.5 border-t border-[color:var(--zen-border)]/60 pt-3 text-[15px] leading-7 text-[color:var(--zen-text)]">
                                    {filteredResultSummaryBullets.map((bullet, idx) => (
                                      <li key={`result-summary-${idx}`} className="flex gap-2">
                                        <span className="w-5 shrink-0 text-center text-[14px] text-[color:var(--zen-muted)]">
                                          {getNarrationBulletIcon(resultSummarySection?.heading || "Result summary", bullet)}
                                        </span>
                                        <span>{replacePlyWithMoveText(bullet)}</span>
                                      </li>
                                    ))}
                                  </ul>
                                ) : (
                                  <p className="text-sm text-[color:var(--zen-muted)]">
                                    No result-summary details were provided for this insight.
                                  </p>
                                )}
                              </div>
                            ) : (
                              <div
                                id={`ai-section-panel-${activeAiSectionTab}`}
                                role="tabpanel"
                                aria-labelledby={`ai-section-tab-${activeAiSectionTab}`}
                                className={`zen-surface-flat p-3.5 md:p-4 border ${
                                  getNarrationSectionTone(activeAiSectionHeading).cardBorder
                                }`}
                                style={{
                                  background:
                                    "linear-gradient(140deg, rgba(22,28,40,0.9), rgba(18,24,36,0.9) 60%, rgba(15,20,30,0.92))",
                                  boxShadow:
                                    "inset 0 0 0 1px rgba(124,136,164,0.2), inset 0 0 0 2px rgba(84,96,126,0.14)",
                                }}
                              >
                                <div className="flex items-center gap-2 mb-2">
                                  <span
                                    className={`inline-flex h-5 w-5 items-center justify-center text-[11px] font-semibold border ${
                                      getNarrationSectionTone(activeAiSectionHeading).badgeBorder
                                    } ${getNarrationSectionTone(activeAiSectionHeading).badgeText}`}
                                  >
                                    {getNarrationSectionBadge(activeAiSectionHeading)}
                                  </span>
                                  <p
                                    className={`text-[11px] uppercase tracking-[0.12em] ${
                                      getNarrationSectionTone(activeAiSectionHeading).headingText
                                    }`}
                                  >
                                    {activeAiSectionHeading}
                                  </p>
                                </div>

                                {activeAiSection?.bullets?.length ? (
                                  <ul className="space-y-2.5 text-[15px] leading-7 text-[color:var(--zen-text)]">
                                    {activeAiSection.bullets.map((bullet, idx) => (
                                      <li key={`${activeAiSection.heading}-${idx}`} className="flex gap-2">
                                        <span className="w-5 shrink-0 text-center text-[14px] text-[color:var(--zen-muted)]">
                                          {getNarrationBulletIcon(activeAiSection.heading, bullet)}
                                        </span>
                                        {(() => {
                                          const matchedEvent =
                                            activeAiSectionTab === "turning_points"
                                              ? resolveTurningPointEventForBullet(bullet, idx)
                                              : null;
                                          const displayText = replacePlyWithMoveText(bullet);
                                          if (matchedEvent) {
                                            return (
                                              <button
                                                type="button"
                                                onClick={() => jumpToInsightEvent(matchedEvent)}
                                                className="text-left underline decoration-dotted underline-offset-4 hover:text-[color:var(--zen-accent)] transition"
                                              >
                                                {displayText}
                                              </button>
                                            );
                                          }
                                          return <span>{displayText}</span>;
                                        })()}
                                      </li>
                                    ))}
                                  </ul>
                                ) : (
                                  <p className="text-sm text-[color:var(--zen-muted)]">
                                    No details are available for this section yet.
                                  </p>
                                )}
                              </div>
                            )}
                          </>
                        ) : (
                          <div className="zen-surface-flat p-4 border border-[color:var(--zen-border)]">
                            <p className="text-sm text-[color:var(--zen-muted)]">
                              AI review is not available for this game right now.
                            </p>
                          </div>
                        )}
                      </div>
                    )}

                    {shouldShowLessonConsentCard && (
                      <div className="zen-surface-flat p-4 md:p-5 border border-[color:var(--zen-border)] space-y-3">
                        <h3 className="text-base font-semibold text-[color:var(--zen-text)]">
                          Get tailored chess lessons by email
                        </h3>
                        <p className="text-sm text-[color:var(--zen-muted)]">
                          Based on your game-analysis gaps, we can send focused lessons to help you
                          improve.
                        </p>
                        {lessonConsentError && (
                          <p className="text-sm text-[color:var(--zen-danger)]">
                            {lessonConsentError}
                          </p>
                        )}
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() => void saveLessonConsentDecision("consented")}
                            disabled={lessonConsentSaving}
                            className="zen-pill px-4 py-2 text-sm font-medium bg-[color:var(--zen-accent-2)] hover:bg-[color:var(--zen-accent)] hover:text-white transition disabled:opacity-60 disabled:cursor-not-allowed"
                          >
                            {lessonConsentSaving ? "Saving..." : "Yes, send lessons"}
                          </button>
                          <button
                            type="button"
                            onClick={() => void saveLessonConsentDecision("declined")}
                            disabled={lessonConsentSaving}
                            className="rounded-lg border border-[color:var(--zen-border)] px-4 py-2 text-sm font-medium text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)] transition disabled:opacity-60 disabled:cursor-not-allowed"
                          >
                            No thanks
                          </button>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
