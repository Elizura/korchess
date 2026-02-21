"use client";

import { useParams } from "next/navigation";
import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { Chess } from "chess.js";
import Link from "next/link";
import { useSession } from "next-auth/react";

import AnalysisBoard from "@/components/analysis/AnalysisBoard";
import EvalBar from "@/components/analysis/EvalBar";
import MoveList from "@/components/analysis/MoveList";
import EngineLines from "@/components/analysis/EngineLines";
import BoardControls from "@/components/analysis/BoardControls";
import { useLocalEngine } from "@/hooks/useLocalEngine";
import {
  MoveTree,
  MoveNode,
  TacticalAnnotation,
  createMoveTree,
  buildTreeFromAnalysis,
  addMove,
  navigateTo,
  goToStart,
  goToEnd,
  goBack,
  goForward,
} from "@/lib/moveTree";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
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
    };
  } | null;
  insights?: SingleGameInsightsResponse | null;
  created_at: string | null;
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

export default function GameAnalyzerPage() {
  const params = useParams();
  const site = params.site as string;
  const username = decodeURIComponent(params.username as string);
  const gameId = params.gameId as string;
  const { data: session } = useSession();

  const authHeaders = useMemo((): Record<string, string> => {
    if (!session?.idToken) {
      return {};
    }
    return { Authorization: `Bearer ${session.idToken}` };
  }, [session?.idToken]);

  // State
  const [game, setGame] = useState<GameData | null>(null);
  const [moveTree, setMoveTree] = useState<MoveTree>(createMoveTree());
  const [analysisData, setAnalysisData] = useState<FullAnalysisResponse["analysis"] | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysisStatus, setAnalysisStatus] = useState<"idle" | "completed" | "missing" | "processing">("idle");
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [singleInsights, setSingleInsights] = useState<SingleGameInsightsResponse | null>(null);
  const [singleInsightsStatus, setSingleInsightsStatus] = useState<"idle" | "ready" | "error">("idle");
  
  // Polling for async analysis
  const pollInterval = useRef<NodeJS.Timeout | null>(null);
  const analysisStartTime = useRef<number | null>(null);
  
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

  const extractPlyFromText = useCallback((text: string): number | null => {
    const match = text.match(/Ply\s+(\d+)/i);
    if (!match) return null;
    const parsed = Number(match[1]);
    return Number.isFinite(parsed) ? parsed : null;
  }, []);

  const hasAiNarration = useMemo(() => {
    return Boolean(singleInsights?.narration && singleInsights?.narration_meta?.source === "gemini");
  }, [singleInsights?.narration, singleInsights?.narration_meta?.source]);

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
      const materialText = tactical.material_outcome?.text;

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
          return materialText
            ? `Hung a ${hangingPiece} (${materialText}); opponent had a concrete capture sequence.`
            : `Hung a ${hangingPiece}; opponent had a concrete capture sequence.`;
        }
        return materialText
          ? `Hung material (${materialText}); opponent had an immediate capture.`
          : "Hung a piece; opponent had an immediate capture.";
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

      try {
        if (!session?.idToken) {
          setError("Please sign in with Google to continue.");
          return;
        }
        // Fetch game data
        const gameRes = await fetch(
          `${API_BASE_URL}/api/v1/game/${site}/${encodeURIComponent(username)}/${gameId}`,
          { headers: authHeaders }
        );
        if (!gameRes.ok) {
          const data = await gameRes.json().catch(() => ({}));
          throw new Error(data.detail || "Failed to fetch game");
        }
        const gameData: GameData = await gameRes.json();
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
            tree = goToStart(tree);
            setMoveTree(tree);
          } catch (e) {
            console.error("Failed to parse PGN:", e);
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "An error occurred");
      } finally {
        setLoading(false);
      }
    };

    if (username && gameId) {
      fetchData();
    }
  }, [username, gameId, session?.idToken, authHeaders]);

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

  // Handle analysis completion
  const handleAnalysisReady = useCallback((data: FullAnalysisResponse) => {
    if (data.analysis) {
      setAnalysisData(data.analysis);
      setAnalysisStatus("completed");
      setSingleInsights(data.insights || null);
      setSingleInsightsStatus(data.insights ? "ready" : "idle");

      // Rebuild tree with analysis data
      const tree = buildTreeFromAnalysis(data.analysis.moves);
      setMoveTree(tree);

      // Log and show success
      const elapsedSeconds = analysisStartTime.current 
        ? Math.round((Date.now() - analysisStartTime.current) / 1000)
        : 0;
      console.log(`[Analysis] Completed in ${elapsedSeconds}s - ${data.analysis.moves.length} moves analyzed`);
      
      setSuccessMessage(`Analysis complete! (${elapsedSeconds}s)`);
      setTimeout(() => setSuccessMessage(null), 5000);
    }
    setAnalyzing(false);
    analysisStartTime.current = null;
  }, []);

  // Hydrate cached in-depth analysis on page load (does not start a new job)
  useEffect(() => {
    const hydrateCachedAnalysis = async () => {
      if (!game || !session?.idToken) return;
      if (analysisStatus === "completed" || analyzing) return;

      try {
        const res = await fetch(buildFullAnalysisUrl(), { headers: authHeaders });
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
    session?.idToken,
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
        const res = await fetch(buildFullAnalysisUrl(), { headers: authHeaders });
        const data: FullAnalysisResponse = await res.json();
        
        if (data.status === "completed") {
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
    if (!session?.idToken) {
      setError("Please sign in with Google to continue.");
      return;
    }
    const preserveCompletedState = force && analysisStatus === "completed";
    setAnalyzing(true);
    setError(null);
    setSuccessMessage(null);
    if (!preserveCompletedState) {
      setSingleInsights(null);
      setSingleInsightsStatus("idle");
    }
    analysisStartTime.current = Date.now();

    console.log(
      `[Analysis] Starting analysis for game ${gameId} (depth=${depth}, multipv=${multiPv}, force=${force})`
    );

    try {
      const res = await fetch(buildFullAnalysisUrl(force), {
        method: "POST",
        headers: authHeaders,
      });

      // Handle 429 Too Many Requests
      if (res.status === 429) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || "Server is busy (max 2 analyses at once). Please try again shortly.");
        setAnalyzing(false);
        setAnalysisStatus(preserveCompletedState ? "completed" : "missing");
        analysisStartTime.current = null;
        return;
      }

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Analysis failed");
      }

      const data: FullAnalysisResponse = await res.json();
      
      if (data.status === "completed" && data.analysis) {
        // Already cached, use immediately
        handleAnalysisReady(data);
      } else if (data.status === "processing") {
        // Analysis started - set state, polling will be started by separate effect
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
    session?.idToken,
    authHeaders,
    buildFullAnalysisUrl,
  ]);

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
        <div className="max-w-6xl mx-auto px-4 relative z-10">
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
        <div className="max-w-6xl mx-auto px-4 relative z-10">
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
      {/* Success notification toast */}
      {successMessage && (
        <div className="fixed top-4 right-4 z-50 animate-in fade-in slide-in-from-top-2 duration-300">
          <div className="zen-surface px-4 py-3 flex items-center gap-3 shadow-lg">
            <div className="w-2 h-2 rounded-full bg-[color:var(--zen-success)] animate-pulse" />
            <span className="text-sm font-medium text-[color:var(--zen-success)]">{successMessage}</span>
          </div>
        </div>
      )}

      <div className="max-w-6xl mx-auto px-4 relative z-10">
        {/* Header */}
        <div className="mb-4 flex items-center justify-between flex-wrap gap-4">
          <div>
            <Link
              href={`/?user=${encodeURIComponent(username)}`}
              className="inline-flex items-center gap-2 text-sm zen-pill px-3 py-2 text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)] transition"
            >
              ← Back to openings
            </Link>
            <h1 className="text-xl font-semibold text-[color:var(--zen-text)] mt-3">
              {game?.opening_name || "Game Analysis"}
            </h1>
            <p className="text-sm text-[color:var(--zen-muted)]">
              {(game?.color ?? "white") === "white" ? (
                <>
                  <span className="text-[color:var(--zen-text)]">{username}</span>
                  <span className="mx-1.5 text-[color:var(--zen-muted)]">(White)</span>
                  <span className="mx-1">vs</span>
                  <span className="text-[color:var(--zen-text)]">{game?.opponent || "Unknown"}</span>
                  <span className="ml-1.5 text-[color:var(--zen-muted)]">(Black)</span>
                </>
              ) : (
                <>
                  <span className="text-[color:var(--zen-text)]">{username}</span>
                  <span className="mx-1.5 text-[color:var(--zen-muted)]">(Black)</span>
                  <span className="mx-1">vs</span>
                  <span className="text-[color:var(--zen-text)]">{game?.opponent || "Unknown"}</span>
                  <span className="ml-1.5 text-[color:var(--zen-muted)]">(White)</span>
                </>
              )}{" "}
              • {formatDate(game?.played_at ?? null)}
              <span
                className={`ml-2 font-medium ${
                  game?.result === "win"
                    ? "text-[color:var(--zen-success)]"
                    : game?.result === "loss"
                    ? "text-[color:var(--zen-danger)]"
                    : "text-[color:var(--zen-muted)]"
                }`}
              >
                {game?.result?.charAt(0).toUpperCase()}
                {game?.result?.slice(1)}
              </span>
            </p>
          </div>

          {/* Accuracy badge */}
          {userAccuracy !== null && (
            <div className="zen-surface-flat px-4 py-2 text-center">
              <div className="text-2xl font-bold text-[color:var(--zen-text)]">{userAccuracy}%</div>
              <div className="text-xs text-[color:var(--zen-muted)] uppercase tracking-wide">Accuracy</div>
            </div>
          )}
        </div>

        {/* Main layout */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
          {/* Left: Board + Eval Bar */}
          <div className="lg:col-span-7 lg:ml-4">
            <div className="zen-surface zen-surface-no-backdrop p-4">
              <div className="flex gap-3">
                {/* Eval bar */}
                <EvalBar eval={currentEval} orientation={orientation} height={600} />

                {/* Board */}
                <div className="flex-1">
                  <AnalysisBoard
                    fen={currentFen}
                    orientation={orientation}
                    onMove={handleUserMove}
                    bestMove={showArrows ? bestMove : null}
                    lastMove={lastMove}
                    showArrows={showArrows}
                    showCoordinates={showCoordinates}
                    boardWidth={600}
                  />
                </div>
              </div>

              {/* Controls */}
              <div className="mt-4">
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
                  multiPv={multiPv}
                  onMultiPvChange={setMultiPv}
                  depth={depth}
                  onDepthChange={setDepth}
                  isAnalyzing={analyzing}
                  onRunAnalysis={() => runAnalysis(analysisStatus === "completed")}
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
          <div className="lg:col-span-5 space-y-4">
            {/* Engine lines */}
            <div className="zen-surface p-4">
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

              {!analyzing && (
                <div className="text-center py-6">
                  <button
                    onClick={() => runAnalysis(analysisStatus === "completed")}
                    className="zen-pill px-6 py-3 text-sm font-medium bg-[color:var(--zen-accent-2)] hover:bg-[color:var(--zen-accent)] hover:text-white transition"
                  >
                    {analysisStatus === "completed"
                      ? "Re-run in-depth analysis"
                      : "Request in-depth analysis"}
                  </button>
                  <p className="text-xs text-[color:var(--zen-muted)] mt-2">
                    {analysisStatus === "completed"
                      ? "Starts a fresh backend deep analysis and replaces this result."
                      : "Runs backend deep analysis with accuracy + insights."}
                  </p>
                </div>
              )}

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

              {currentNode && (
                <EngineLines
                  lines={displayEngineLines}
                  depth={displayEngineDepth}
                  isLoading={!currentNode.eval && isLocalEvaluating}
                />
              )}

              {localEngineError && (
                <p className="text-xs text-[color:var(--zen-muted)] mt-3">
                  Local engine: {localEngineError}
                </p>
              )}
            </div>

            {/* Move list */}
            <div className="zen-surface p-4">
              <h3 className="text-sm font-semibold text-[color:var(--zen-text)] mb-3">Moves</h3>
              <MoveList
                tree={moveTree}
                currentId={moveTree.currentId}
                onSelectMove={handleSelectMove}
                maxHeight={280}
              />
            </div>

            {/* Current move info */}
            {currentNode && currentNode.san && (
              <div className="zen-surface p-4">
                {/* <div className="flex items-center justify-between mb-2">
                  <span className="text-sm text-[color:var(--zen-muted)]">Current Move</span>
                  {currentNode.classification && (
                    <span
                      className={`text-xs px-2 py-0.5 rounded font-medium ${
                        currentNode.classification === "blunder"
                          ? "bg-red-500/15 text-[color:var(--zen-danger)]"
                          : currentNode.classification === "mistake"
                          ? "bg-orange-500/15 text-orange-300"
                          : currentNode.classification === "inaccuracy"
                          ? "bg-yellow-500/15 text-yellow-300"
                          : currentNode.classification === "best" ||
                            currentNode.classification === "excellent"
                          ? "bg-emerald-500/15 text-[color:var(--zen-success)]"
                          : "bg-white/5 text-[color:var(--zen-text)]"
                      }`}
                    >
                      {currentNode.classification.charAt(0).toUpperCase() +
                        currentNode.classification.slice(1)}
                    </span>
                  )}
                </div> */}
                <div className="text-2xl font-mono font-semibold text-[color:var(--zen-text)]">
                  {currentNode.san}
                </div>
                {currentNode.cpLoss !== undefined && currentNode.cpLoss > 0 && (
                  <p className="text-sm text-[color:var(--zen-danger)] mt-1">
                    -{currentNode.cpLoss / 100} centipawns
                  </p>
                )}
                {currentNode.bestMove && currentNode.bestMove.san !== currentNode.san && (
                  <p className="text-sm text-[color:var(--zen-success)] mt-1">
                    Best: <span className="font-mono">{currentNode.bestMove.san}</span>
                  </p>
                )}
                {currentTactical && (
                  <div className={`zen-surface-flat mt-3 p-3 border ${getTacticalTone(currentTactical.tactic_type).border}`}>
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <span
                        className={`inline-flex items-center gap-2 text-[11px] uppercase tracking-wide px-2 py-1 border ${getTacticalTone(currentTactical.tactic_type).badge}`}
                      >
                        <span>{getTacticalIcon(currentTactical.tactic_type)}</span>
                        <span>{getTacticalLabel(currentTactical.tactic_type)}</span>
                      </span>
                      {typeof currentTactical.severity_score === "number" && (
                        <span className="text-xs text-[color:var(--zen-muted)]">
                          Severity {(currentTactical.severity_score * 100).toFixed(0)}
                        </span>
                      )}
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

            {/* Error display */}
            {error && (
              <div className="zen-surface-flat p-4 border border-[color:var(--zen-danger)]/30">
                <p className="text-sm text-[color:var(--zen-danger)]">{error}</p>
              </div>
            )}
          </div>

          {/* Deterministic single-game insights */}
          {analysisStatus === "completed" && (
            <div className="lg:col-span-12">
              <div className="zen-surface p-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-[color:var(--zen-text)]">Game Insights</h3>
                  {singleInsights?.confidence !== undefined && (
                    <span className="text-xs text-[color:var(--zen-muted)]">
                      {Math.round((singleInsights.confidence || 0) * 100)}% confidence
                    </span>
                  )}
                </div>

                {singleInsightsStatus === "error" && (
                  <p className="text-sm text-[color:var(--zen-danger)]">
                    Unable to load game insights right now.
                  </p>
                )}
                {singleInsightsStatus === "idle" && (
                  <p className="text-sm text-[color:var(--zen-muted)]">
                    Insights were not available for this deep analysis result.
                  </p>
                )}

                {singleInsightsStatus === "ready" && singleInsights && (
                  <div className="space-y-4">
                    {hasAiNarration && singleInsights.narration ? (
                      <div className="space-y-3">
                        <div
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
                                Mission Brief
                              </p>
                              <p className="text-lg font-semibold text-[color:var(--zen-text)]">
                                {singleInsights.narration.title}
                              </p>
                            </div>
                            <span className="text-[11px] px-2 py-1 border border-[color:var(--zen-border)] text-[color:var(--zen-muted)] whitespace-nowrap bg-white/5">
                              {Math.round((singleInsights.confidence || 0) * 100)}% confidence
                            </span>
                          </div>

                          <p className="text-[15px] leading-7 text-[color:var(--zen-text)]">
                            {singleInsights.narration.one_liner}
                          </p>
                          <p className="text-sm leading-6 text-[color:var(--zen-muted)]">
                            {singleInsights.narration.confidence_note}
                          </p>

                          <div className="flex flex-wrap gap-2">
                            <span className="text-[11px] px-2 py-1 border border-[color:var(--zen-border)] text-[color:var(--zen-muted)] bg-white/5">
                              Decisive phase: {singleInsights.narration.labels.decisive_phase}
                            </span>
                            <span className="text-[11px] px-2 py-1 border border-[color:var(--zen-border)] text-[color:var(--zen-muted)] bg-white/5">
                              Style: {singleInsights.narration.labels.player_style}
                            </span>
                          </div>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                          {singleInsights.narration.sections.map((section) => {
                            const sectionKey = section.heading.toLowerCase();
                            const isWideSection =
                              sectionKey.includes("result") ||
                              sectionKey.includes("turning") ||
                              sectionKey.includes("next");
                            const tone = getNarrationSectionTone(section.heading);
                            return (
                              <div
                                key={section.heading}
                                className={`zen-surface-flat p-3.5 md:p-4 border ${tone.cardBorder} ${
                                  isWideSection ? "md:col-span-2" : ""
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
                                    className={`inline-flex h-5 w-5 items-center justify-center text-[11px] font-semibold border ${tone.badgeBorder} ${tone.badgeText}`}
                                  >
                                    {getNarrationSectionBadge(section.heading)}
                                  </span>
                                  <p className={`text-[11px] uppercase tracking-[0.12em] ${tone.headingText}`}>
                                    {section.heading}
                                  </p>
                                </div>
                                <ul className="space-y-2.5 text-[15px] leading-7 text-[color:var(--zen-text)]">
                                  {section.bullets.map((bullet, idx) => (
                                    <li key={`${section.heading}-${idx}`} className="flex gap-2">
                                      <span className="w-5 shrink-0 text-center text-[14px] text-[color:var(--zen-muted)]">
                                        {getNarrationBulletIcon(section.heading, bullet)}
                                      </span>
                                      {(() => {
                                        const parsedPly = extractPlyFromText(bullet);
                                        const turningEvents = singleInsights.turning_points?.events || [];
                                        const matchedEvent =
                                          section.heading.toLowerCase().includes("turning") && parsedPly !== null
                                            ? turningEvents.find((event) => event.ply === parsedPly)
                                            : undefined;
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
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ) : (
                      <div className="zen-surface-flat p-4 border border-[color:var(--zen-border)]">
                        <p className="text-sm text-[color:var(--zen-muted)]">
                          AI review is not available for this game right now.
                        </p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
