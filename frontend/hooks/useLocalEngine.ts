"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chess } from "chess.js";

type EngineLine = {
  cp?: number;
  mate?: number;
  depth: number;
  pv_uci: string[];
  pv_san: string[];
};

type EvalResult = {
  eval: EngineLine | null;
  multipv: EngineLine[] | null;
  fen: string;
  depth: number;
  multiPv: number;
};

type WorkerReadyMessage = { type: "ready" };
type WorkerErrorMessage = { type: "error"; message: string; id?: string };
type WorkerResultMessage = {
  type: "result";
  id: string;
  fen: string;
  depth: number;
  multiPv: number;
  final: boolean;
  eval: {
    cp?: number;
    mate?: number;
    depth: number;
    pv_uci: string[];
  } | null;
  multipv: Array<{
    cp?: number;
    mate?: number;
    depth: number;
    pv_uci: string[];
  }> | null;
};
type WorkerMessage = WorkerReadyMessage | WorkerErrorMessage | WorkerResultMessage;

function toSanLine(fen: string, pvUci: string[]): string[] {
  try {
    const chess = new Chess(fen);
    const san: string[] = [];
    for (const uci of pvUci) {
      if (uci.length < 4) break;
      const from = uci.slice(0, 2);
      const to = uci.slice(2, 4);
      const promotion = uci.length > 4 ? uci.slice(4, 5) : undefined;
      const move = chess.move({ from, to, promotion });
      if (!move) break;
      san.push(move.san);
    }
    return san;
  } catch {
    return [];
  }
}

function withSan(fen: string, line: { cp?: number; mate?: number; depth: number; pv_uci: string[] }): EngineLine {
  return {
    cp: line.cp,
    mate: line.mate,
    depth: line.depth,
    pv_uci: line.pv_uci,
    pv_san: toSanLine(fen, line.pv_uci),
  };
}

export function useLocalEngine() {
  const workerRef = useRef<Worker | null>(null);
  const cacheRef = useRef<Map<string, EvalResult>>(new Map());
  const pendingRequestRef = useRef<string | null>(null);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const queuedEvaluationRef = useRef<{
    fen: string;
    depth: number;
    multiPv: number;
    force: boolean;
  } | null>(null);

  const [isReady, setIsReady] = useState(false);
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentResult, setCurrentResult] = useState<EvalResult | null>(null);

  const buildCacheKey = useCallback((fen: string, depth: number, multiPv: number) => {
    return `${fen}__d${depth}__m${multiPv}`;
  }, []);

  const dispatchEvaluation = useCallback(
    (fen: string, depth: number, multiPv: number, force: boolean) => {
      const cacheKey = buildCacheKey(fen, depth, multiPv);
      if (!force) {
        const cached = cacheRef.current.get(cacheKey);
        if (cached) {
          setCurrentResult(cached);
          setIsEvaluating(false);
          return;
        }
      }

      const worker = workerRef.current;
      if (!worker) {
        queuedEvaluationRef.current = { fen, depth, multiPv, force };
        return;
      }

      const requestId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      pendingRequestRef.current = requestId;
      setError(null);
      setIsEvaluating(true);

      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
      debounceTimerRef.current = setTimeout(() => {
        workerRef.current?.postMessage({
          type: "evaluate",
          id: requestId,
          fen,
          depth,
          multiPv,
        });
      }, 40);
    },
    [buildCacheKey]
  );

  useEffect(() => {
    const worker = new Worker(new URL("../workers/stockfish.worker.ts", import.meta.url), {
      type: "module",
    });
    workerRef.current = worker;

    worker.onmessage = (event: MessageEvent<WorkerMessage>) => {
      const message = event.data;
      if (message.type === "ready") {
        setIsReady(true);
        setError(null);
        const queued = queuedEvaluationRef.current;
        if (queued) {
          queuedEvaluationRef.current = null;
          dispatchEvaluation(queued.fen, queued.depth, queued.multiPv, queued.force);
        }
        return;
      }

      if (message.type === "error") {
        if (!message.id || message.id === pendingRequestRef.current) {
          setIsEvaluating(false);
          setError(message.message);
        }
        return;
      }

      if (message.type === "result") {
        if (message.id !== pendingRequestRef.current) {
          return;
        }

        const multipv = message.multipv
          ? message.multipv.map((line) => withSan(message.fen, line))
          : message.eval
          ? [withSan(message.fen, message.eval)]
          : null;
        const evalLine = message.eval ? withSan(message.fen, message.eval) : null;

        const result: EvalResult = {
          eval: evalLine,
          multipv,
          fen: message.fen,
          depth: message.depth,
          multiPv: message.multiPv,
        };
        setCurrentResult(result);
        setError(null);
        if (message.final) {
          cacheRef.current.set(buildCacheKey(message.fen, message.depth, message.multiPv), result);
          setIsEvaluating(false);
        }
      }
    };

    worker.postMessage({ type: "init" });

    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
      queuedEvaluationRef.current = null;
      worker.terminate();
      workerRef.current = null;
    };
  }, [buildCacheKey, dispatchEvaluation]);

  const evaluateFen = useCallback(
    (fen: string, options?: { depth?: number; multiPv?: number; force?: boolean }) => {
      const depth = Math.max(8, Math.min(options?.depth ?? 12, 16));
      const multiPv = Math.max(1, Math.min(options?.multiPv ?? 2, 3));
      const force = options?.force ?? false;
      const cacheKey = buildCacheKey(fen, depth, multiPv);

      if (!force) {
        const cached = cacheRef.current.get(cacheKey);
        if (cached) {
          setCurrentResult(cached);
          setIsEvaluating(false);
          return;
        }
      }

      if (!workerRef.current || !isReady) {
        queuedEvaluationRef.current = { fen, depth, multiPv, force };
        return;
      }

      dispatchEvaluation(fen, depth, multiPv, force);
    },
    [buildCacheKey, dispatchEvaluation, isReady]
  );

  const getCachedFen = useCallback(
    (fen: string, options?: { depth?: number; multiPv?: number }) => {
      const depth = Math.max(8, Math.min(options?.depth ?? 12, 16));
      const multiPv = Math.max(1, Math.min(options?.multiPv ?? 2, 3));
      return cacheRef.current.get(buildCacheKey(fen, depth, multiPv)) || null;
    },
    [buildCacheKey]
  );

  return useMemo(
    () => ({
      isReady,
      isEvaluating,
      error,
      currentResult,
      evaluateFen,
      getCachedFen,
    }),
    [isReady, isEvaluating, error, currentResult, evaluateFen, getCachedFen]
  );
}
