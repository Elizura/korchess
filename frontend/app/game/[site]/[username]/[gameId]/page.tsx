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
  formatEval,
} from "@/lib/moveTree";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

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
  created_at: string | null;
}

interface EvalResponse {
  eval: {
    cp?: number;
    mate?: number;
    depth: number;
    pv_uci: string[];
    pv_san: string[];
  } | null;
  multipv: Array<{
    cp?: number;
    mate?: number;
    depth: number;
    pv_uci: string[];
    pv_san: string[];
  }> | null;
  fen: string;
}

export default function GameAnalyzerPage() {
  const params = useParams();
  const site = params.site as string;
  const username = decodeURIComponent(params.username as string);
  const gameId = params.gameId as string;
  const { data: session } = useSession();

  const authHeaders = useMemo(() => {
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
  const [analysisStatus, setAnalysisStatus] = useState<"completed" | "missing" | "processing" | "loading">("loading");
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  
  // Polling for async analysis
  const pollInterval = useRef<NodeJS.Timeout | null>(null);
  const analysisStartTime = useRef<number | null>(null);
  
  // Board settings
  const [orientation, setOrientation] = useState<"white" | "black">("white");
  const [showCoordinates, setShowCoordinates] = useState(true);
  const [showArrows, setShowArrows] = useState(true);
  const [multiPv, setMultiPv] = useState(3);
  const [depth, setDepth] = useState(18);
  
  // Engine lines for current position
  const [engineLines, setEngineLines] = useState<EvalResponse["multipv"] | null>(null);
  const [isEvaluating, setIsEvaluating] = useState(false);

  // Track if auto-analysis was already triggered to prevent duplicates
  const autoAnalysisTriggered = useRef(false);

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
    if (!currentNode?.eval) return null;
    return {
      cp: currentNode.eval.cp,
      mate: currentNode.eval.mate,
    };
  }, [currentNode]);

  // Get best move for current position
  const bestMove = useMemo(() => {
    if (!currentNode?.bestMove) return null;
    const uci = currentNode.bestMove.uci;
    if (uci.length >= 4) {
      return {
        from: uci.slice(0, 2),
        to: uci.slice(2, 4),
      };
    }
    return null;
  }, [currentNode]);

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

  // User accuracy (based on their color)
  const userAccuracy = useMemo(() => {
    if (!analysisData?.summary || !game) return null;
    return game.color === "white"
      ? analysisData.summary.accuracy_white
      : analysisData.summary.accuracy_black;
  }, [analysisData, game]);

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

        // Evaluate the new position
        evaluatePosition(chess.fen());

        return true;
      } catch {
        return false;
      }
    },
    [currentFen]
  );

  // Evaluate a position with the engine
  const evaluatePosition = async (fen: string) => {
    if (!session?.idToken) {
      setError("Please sign in with Google to continue.");
      return;
    }
    setIsEvaluating(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/eval`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders },
        body: JSON.stringify({ fen, depth, multipv: multiPv }),
      });

      if (res.ok) {
        const data: EvalResponse = await res.json();
        setEngineLines(data.multipv || (data.eval ? [data.eval] : null));
      }
    } catch (err) {
      console.error("Evaluation failed:", err);
    } finally {
      setIsEvaluating(false);
    }
  };

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

  // Fetch game data on mount
  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      setError(null);

      try {
        if (!session?.idToken) {
          setError("Please sign in with Google to continue.");
          return;
        }
        // Fetch game data
        const gameRes = await fetch(
          `${API_BASE_URL}/api/game/${site}/${encodeURIComponent(username)}/${gameId}`,
          { headers: { ...authHeaders } }
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

        // Fetch full analysis status
        const analysisRes = await fetch(
          `${API_BASE_URL}/api/analysis/${site}/${encodeURIComponent(username)}/${gameId}/full?depth=${depth}&multipv=${multiPv}`,
          { headers: { ...authHeaders } }
        );
        if (analysisRes.ok) {
          const data: FullAnalysisResponse = await analysisRes.json();
          if (data.status === "completed" && data.analysis) {
            setAnalysisData(data.analysis);
            setAnalysisStatus("completed");

            // Rebuild tree with analysis data
            const tree = buildTreeFromAnalysis(data.analysis.moves);
            setMoveTree(tree);
          } else if (data.status === "processing") {
            // Analysis is running - set state, polling will be started by separate effect
            setAnalysisStatus("processing");
            setAnalyzing(true);
            if (!analysisStartTime.current) {
              analysisStartTime.current = Date.now();
            }
          } else {
            setAnalysisStatus("missing");
          }
        } else {
          setAnalysisStatus("missing");
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
  }, [username, gameId, depth, multiPv, session?.idToken, authHeaders]);

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
          `${API_BASE_URL}/api/analysis/${site}/${encodeURIComponent(username)}/${gameId}/full?depth=${depth}&multipv=${multiPv}`,
          { headers: { ...authHeaders } }
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
    analysisStartTime.current = Date.now();

    console.log(`[Analysis] Starting analysis for game ${gameId} (depth=${depth}, multipv=${multiPv})`);

    try {
      const res = await fetch(
        `${API_BASE_URL}/api/analysis/${site}/${encodeURIComponent(username)}/${gameId}/full?depth=${depth}&multipv=${multiPv}`,
        { method: "POST", headers: { ...authHeaders } }
      );

      // Handle 429 Too Many Requests
      if (res.status === 429) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || "Server is busy (max 2 analyses at once). Please try again shortly.");
        setAnalyzing(false);
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
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Analysis failed";
      console.error(`[Analysis] Failed:`, err);
      setError(errorMessage);
      setAnalyzing(false);
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
    return () => stopPolling();
  }, [stopPolling]);

  // Auto-start analysis if missing (only once per page load)
  useEffect(() => {
    if (analysisStatus === "missing" && !analyzing && game && !autoAnalysisTriggered.current) {
      autoAnalysisTriggered.current = true;
      console.log("[Analysis] Auto-starting analysis for missing game");
      runAnalysis();
    }
  }, [analysisStatus, analyzing, game, runAnalysis]);

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
                  onRunAnalysis={analysisStatus === "missing" ? runAnalysis : undefined}
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
                {analysisData?.meta.depth && (
                  <span className="font-normal text-[color:var(--zen-muted)] ml-2">
                    Depth {analysisData.meta.depth}
                  </span>
                )}
              </h3>

              {analysisStatus === "missing" && !analyzing && (
                <div className="text-center py-6">
                  <button
                    onClick={runAnalysis}
                    className="zen-pill px-6 py-3 text-sm font-medium bg-[color:var(--zen-accent-2)] hover:bg-[color:var(--zen-accent)] hover:text-white transition"
                  >
                    Run Full Analysis
                  </button>
                  <p className="text-xs text-[color:var(--zen-muted)] mt-2">
                    Analyzes every move (~20-40 seconds)
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

              {!analyzing && currentNode && (
                <EngineLines
                  lines={
                    currentNode.eval?.multiPv ||
                    engineLines ||
                    (currentNode.eval
                      ? [
                          {
                            cp: currentNode.eval.cp,
                            mate: currentNode.eval.mate,
                            depth: currentNode.eval.depth,
                            pv: currentNode.eval.pv || [],
                          },
                        ]
                      : null)
                  }
                  depth={currentNode.eval?.depth || analysisData?.meta.depth}
                  isLoading={isEvaluating}
                />
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
