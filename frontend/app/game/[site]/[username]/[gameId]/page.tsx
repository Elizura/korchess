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
  const [activeInsightEventId, setActiveInsightEventId] = useState<string | null>(null);
  
  // Polling for async analysis
  const pollInterval = useRef<NodeJS.Timeout | null>(null);
  const analysisStartTime = useRef<number | null>(null);
  const insightHighlightTimer = useRef<NodeJS.Timeout | null>(null);
  
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

  const formatCp = useCallback((cp?: number | null) => {
    if (cp === undefined || cp === null) return "—";
    const value = (cp / 100).toFixed(2);
    return `${cp > 0 ? "+" : ""}${value}`;
  }, []);

  const formatPhaseState = useCallback(
    (phase?: { evaluation_state: "scored" | "not_reached" | "too_short"; grade: string; score: number | null }) => {
      if (!phase) return "N/A";
      if (phase.evaluation_state === "not_reached") return "Not reached";
      if (phase.evaluation_state === "too_short") return "Too short to evaluate";
      return `${phase.grade}${phase.score !== null ? ` (${phase.score.toFixed(1)})` : ""}`;
    },
    []
  );

  const jumpToInsightEvent = useCallback(
    (event: InsightEvent) => {
      const ply = event.anchor?.ply ?? event.ply;
      if (!ply) return;
      const nodeId = nodeIdByPly.get(ply);
      if (!nodeId) return;

      setMoveTree((tree) => navigateTo(tree, nodeId));
      setActiveInsightEventId(event.event_id);

      if (insightHighlightTimer.current) {
        clearTimeout(insightHighlightTimer.current);
      }
      insightHighlightTimer.current = setTimeout(() => {
        setActiveInsightEventId(null);
      }, 1400);
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
        const res = await fetch(
          `${API_BASE_URL}/api/v1/analysis/${site}/${encodeURIComponent(username)}/${gameId}/full?depth=${depth}&multipv=${multiPv}`,
          { headers: authHeaders }
        );
        const data: FullAnalysisResponse = await res.json();
        
        if (data.status === "completed") {
          stopPolling();
          handleAnalysisReady(data);
        } else if (data.status === "missing") {
          // Job failed/disappeared - stop polling
          console.log("[Analysis] Job disappeared (likely failed). User can retry.");
          stopPolling();
          setAnalyzing(false);
          setAnalysisStatus("missing");
          analysisStartTime.current = null;
        }
        // If still "processing", continue polling
      } catch (err) {
        // Network error - keep polling
        console.error("[Analysis] Polling error:", err);
      }
    }, 2000); // Poll every 2 seconds
  }, [username, gameId, depth, multiPv, stopPolling, handleAnalysisReady, authHeaders]);

  // Run full analysis (starts background job and begins polling)
  const runAnalysis = useCallback(async () => {
    if (!session?.idToken) {
      setError("Please sign in with Google to continue.");
      return;
    }
    setAnalyzing(true);
    setError(null);
    setSuccessMessage(null);
    setSingleInsights(null);
    setSingleInsightsStatus("idle");
    analysisStartTime.current = Date.now();

    console.log(`[Analysis] Starting analysis for game ${gameId} (depth=${depth}, multipv=${multiPv})`);

    try {
      const res = await fetch(
        `${API_BASE_URL}/api/v1/analysis/${site}/${encodeURIComponent(username)}/${gameId}/full?depth=${depth}&multipv=${multiPv}`,
        { method: "POST", headers: authHeaders }
      );

      // Handle 429 Too Many Requests
      if (res.status === 429) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || "Server is busy (max 2 analyses at once). Please try again shortly.");
        setAnalyzing(false);
        setAnalysisStatus("missing");
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
        setAnalysisStatus("missing");
        analysisStartTime.current = null;
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Analysis failed";
      console.error(`[Analysis] Failed:`, err);
      setError(errorMessage);
      setAnalyzing(false);
      setAnalysisStatus("missing");
      analysisStartTime.current = null;
    }
  }, [username, gameId, depth, multiPv, handleAnalysisReady, session?.idToken, authHeaders]);

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
      if (insightHighlightTimer.current) {
        clearTimeout(insightHighlightTimer.current);
      }
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
                  onRunAnalysis={undefined}
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

              {analysisStatus !== "completed" && !analyzing && (
                <div className="text-center py-6">
                  <button
                    onClick={runAnalysis}
                    className="zen-pill px-6 py-3 text-sm font-medium bg-[color:var(--zen-accent-2)] hover:bg-[color:var(--zen-accent)] hover:text-white transition"
                  >
                    Request in-depth analysis
                  </button>
                  <p className="text-xs text-[color:var(--zen-muted)] mt-2">
                    Runs backend deep analysis with accuracy + insights.
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
                <div className="flex items-center justify-between mb-2">
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
                </div>
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
              </div>
            )}

            {/* Deterministic single-game insights */}
            {analysisStatus === "completed" && (
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
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div className="zen-surface-flat p-3">
                        <p className="text-[11px] uppercase tracking-wide text-[color:var(--zen-muted)]">
                          Result Cause
                        </p>
                        <p className="text-sm font-medium text-[color:var(--zen-text)] mt-1">
                          {singleInsights.result_cause?.primary_label || "—"}
                        </p>
                        <p className="text-xs text-[color:var(--zen-muted)] mt-1">
                          Secondary: {singleInsights.result_cause?.secondary_label || "—"}
                        </p>
                      </div>
                      <div className="zen-surface-flat p-3">
                        <p className="text-[11px] uppercase tracking-wide text-[color:var(--zen-muted)]">
                          Character
                        </p>
                        <p className="text-sm font-medium text-[color:var(--zen-text)] mt-1">
                          {singleInsights.game_character?.label || "—"}
                        </p>
                        {singleInsights.game_character?.sublabel && (
                          <p className="text-xs text-[color:var(--zen-muted)] mt-1">
                            {singleInsights.game_character.sublabel}
                          </p>
                        )}
                      </div>
                    </div>

                    <div className="zen-surface-flat p-3">
                      <div className="flex items-center justify-between gap-2 mb-2">
                        <p className="text-[11px] uppercase tracking-wide text-[color:var(--zen-muted)]">
                          Turning Points (Top 3)
                        </p>
                        <p className="text-xs text-[color:var(--zen-muted)]">
                          Decisive phase: {singleInsights.decisive_phase?.decisive_phase || "mixed"}
                        </p>
                      </div>
                      <div className="space-y-2">
                        {(singleInsights.turning_points?.events || []).slice(0, 3).map((event) => (
                          <button
                            key={event.event_id}
                            type="button"
                            onClick={() => jumpToInsightEvent(event)}
                            className={`w-full text-left border px-2 py-2 transition ${
                              activeInsightEventId === event.event_id
                                ? "border-[color:var(--zen-accent)] bg-[color:var(--zen-accent)]/15"
                                : "border-[color:var(--zen-border)] hover:border-[color:var(--zen-accent)]/50"
                            }`}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-sm text-[color:var(--zen-text)]">
                                Ply {event.ply}: {formatCp(event.pre_eval_cp)} → {formatCp(event.post_eval_cp)}
                              </span>
                              <span className="text-xs text-[color:var(--zen-muted)]">
                                Severity {Math.round(event.severity_score || 0)}
                              </span>
                            </div>
                            <div className="flex items-center gap-2 mt-1">
                              <span className="text-xs text-[color:var(--zen-muted)]">
                                {event.actor === "user" ? "You" : "Opponent"} • {event.phase}
                              </span>
                              {event.is_decisive && (
                                <span className="text-[10px] px-1.5 py-0.5 border border-[color:var(--zen-accent)] text-[color:var(--zen-accent)]">
                                  Decisive Turning Point
                                </span>
                              )}
                            </div>
                          </button>
                        ))}
                        {(!singleInsights.turning_points?.events ||
                          singleInsights.turning_points.events.length === 0) && (
                          <p className="text-xs text-[color:var(--zen-muted)]">No major turning points detected.</p>
                        )}
                      </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div className="zen-surface-flat p-3">
                        <p className="text-[11px] uppercase tracking-wide text-[color:var(--zen-muted)] mb-2">
                          Missed Winning Chances
                        </p>
                        <div className="space-y-2">
                          {(singleInsights.missed_winning_chances?.events || []).slice(0, 3).map((event) => (
                            <button
                              key={event.event_id}
                              type="button"
                              onClick={() => jumpToInsightEvent(event)}
                              className={`w-full text-left border px-2 py-1.5 transition ${
                                activeInsightEventId === event.event_id
                                  ? "border-[color:var(--zen-accent)] bg-[color:var(--zen-accent)]/15"
                                  : "border-[color:var(--zen-border)] hover:border-[color:var(--zen-accent)]/50"
                              }`}
                            >
                              <p className="text-sm text-[color:var(--zen-text)]">
                                Ply {event.ply} • {event.label}
                              </p>
                              <p className="text-xs text-[color:var(--zen-muted)]">
                                Severity {Math.round(event.severity_score || 0)}
                              </p>
                            </button>
                          ))}
                          {(!singleInsights.missed_winning_chances?.events ||
                            singleInsights.missed_winning_chances.events.length === 0) && (
                            <p className="text-xs text-[color:var(--zen-muted)]">No missed winning chances detected.</p>
                          )}
                        </div>
                      </div>

                      <div className="zen-surface-flat p-3">
                        <p className="text-[11px] uppercase tracking-wide text-[color:var(--zen-muted)] mb-2">
                          Got Away With It
                        </p>
                        <div className="space-y-2">
                          {(singleInsights.got_away_with_it?.events || []).slice(0, 3).map((event) => (
                            <button
                              key={event.event_id}
                              type="button"
                              onClick={() => jumpToInsightEvent(event)}
                              className={`w-full text-left border px-2 py-1.5 transition ${
                                activeInsightEventId === event.event_id
                                  ? "border-[color:var(--zen-accent)] bg-[color:var(--zen-accent)]/15"
                                  : "border-[color:var(--zen-border)] hover:border-[color:var(--zen-accent)]/50"
                              }`}
                            >
                              <p className="text-sm text-[color:var(--zen-text)]">
                                Ply {event.ply} • {event.label}
                              </p>
                              <p className="text-xs text-[color:var(--zen-muted)]">
                                Severity {Math.round(event.severity_score || 0)}
                              </p>
                            </button>
                          ))}
                          {(!singleInsights.got_away_with_it?.events ||
                            singleInsights.got_away_with_it.events.length === 0) && (
                            <p className="text-xs text-[color:var(--zen-muted)]">No escape moments detected.</p>
                          )}
                        </div>
                      </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div className="zen-surface-flat p-3">
                        <p className="text-[11px] uppercase tracking-wide text-[color:var(--zen-muted)] mb-2">
                          Phase Grades
                        </p>
                        <div className="space-y-1.5 text-sm">
                          <p className="text-[color:var(--zen-text)]">
                            Opening: {formatPhaseState(singleInsights.phase_grades?.opening)}
                          </p>
                          <p className="text-[color:var(--zen-text)]">
                            Middlegame: {formatPhaseState(singleInsights.phase_grades?.middlegame)}
                          </p>
                          <p className="text-[color:var(--zen-text)]">
                            Endgame: {formatPhaseState(singleInsights.phase_grades?.endgame)}
                          </p>
                        </div>
                      </div>

                      <div className="zen-surface-flat p-3">
                        <p className="text-[11px] uppercase tracking-wide text-[color:var(--zen-muted)] mb-2">
                          Time Pressure
                        </p>
                        <p className="text-sm text-[color:var(--zen-text)]">
                          {singleInsights.time_pressure_collapse?.status === "detected"
                            ? "Collapse detected"
                            : singleInsights.time_pressure_collapse?.status === "not_detected"
                            ? "No collapse detected"
                            : singleInsights.time_pressure_collapse?.status === "insufficient_data"
                            ? "Insufficient data"
                            : "Unavailable"}
                        </p>
                        <p className="text-xs text-[color:var(--zen-muted)] mt-1">
                          Clock samples: {singleInsights.time_pressure_collapse?.data_quality.clock_moves ?? 0}/
                          {singleInsights.time_pressure_collapse?.data_quality.user_moves ?? 0}
                        </p>
                        {singleInsights.time_pressure_collapse?.avg_cp_low !== null &&
                          singleInsights.time_pressure_collapse?.avg_cp_normal !== null && (
                            <p className="text-xs text-[color:var(--zen-muted)] mt-1">
                              ACPL low vs normal:{" "}
                              {Math.round(singleInsights.time_pressure_collapse?.avg_cp_low || 0)} /{" "}
                              {Math.round(singleInsights.time_pressure_collapse?.avg_cp_normal || 0)}
                            </p>
                          )}
                      </div>
                    </div>
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
        </div>
      </div>
    </main>
  );
}
