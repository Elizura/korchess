/// <reference lib="webworker" />

type EvalLine = {
  cp?: number;
  mate?: number;
  depth: number;
  pv_uci: string[];
};

type ActiveTask = {
  id: string;
  fen: string;
  depth: number;
  multiPv: number;
  lines: Map<number, EvalLine>;
  lastEmitAt: number;
};

type EngineCandidate = {
  scriptUrl: string;
  wasmUrl: string;
  label: string;
};

type InitMessage = { type: "init" };
type EvaluateMessage = {
  type: "evaluate";
  id: string;
  fen: string;
  depth: number;
  multiPv: number;
};
type IncomingMessage = InitMessage | EvaluateMessage;

type ReadyMessage = { type: "ready" };
type ErrorMessage = { type: "error"; message: string; id?: string };
type ResultMessage = {
  type: "result";
  id: string;
  fen: string;
  depth: number;
  multiPv: number;
  final: boolean;
  eval: EvalLine | null;
  multipv: EvalLine[] | null;
};
type OutgoingMessage = ReadyMessage | ErrorMessage | ResultMessage;

const ctx: DedicatedWorkerGlobalScope = self as unknown as DedicatedWorkerGlobalScope;

let engine: Worker | null = null;
let activeTask: ActiveTask | null = null;
let queuedTask: EvaluateMessage | null = null;
let stoppingForQueuedTask = false;
let initialized = false;
let initializing = false;
let initTimer: ReturnType<typeof setTimeout> | null = null;
let lastInitError: string | null = null;

const ENGINE_CANDIDATES: EngineCandidate[] = [
  {
    scriptUrl: "/stockfish/stockfish-nnue-16-single.js",
    wasmUrl: "/stockfish/stockfish-nnue-16-single.wasm",
    label: "local-public-assets",
  },
  {
    scriptUrl: "stockfish/stockfish-nnue-16-single.js",
    wasmUrl: "stockfish/stockfish-nnue-16-single.wasm",
    label: "relative-assets",
  },
];

function createEngineWorker(candidate: EngineCandidate): Worker {
  // nmrugg stockfish.js worker build expects wasm URL via location hash:
  // "stockfish.js#<wasmPath>"
  // The script itself appends ",worker" internally for nested workers.
  const workerUrl = `${candidate.scriptUrl}#${encodeURIComponent(candidate.wasmUrl)}`;
  return new Worker(workerUrl);
}

function postMessageToMain(message: OutgoingMessage): void {
  ctx.postMessage(message);
}

function parseScorePerspective(fen: string, line: EvalLine): EvalLine {
  const sideToMove = fen.split(" ")[1];
  if (sideToMove === "w") {
    return line;
  }
  return {
    ...line,
    cp: line.cp !== undefined ? -line.cp : undefined,
    mate: line.mate !== undefined ? -line.mate : undefined,
  };
}

function parseMultiPvIndex(raw: string): number {
  const match = raw.match(/\bmultipv\s+(\d+)/);
  if (!match) return 1;
  const value = Number(match[1]);
  if (!Number.isFinite(value) || value < 1) return 1;
  return value;
}

function parseEngineInfo(raw: string): EvalLine | null {
  if (!raw.startsWith("info ") || raw.includes(" string ")) {
    return null;
  }

  const depthMatch = raw.match(/\bdepth\s+(\d+)/);
  const scoreMatch = raw.match(/\bscore\s+(cp|mate)\s+(-?\d+)/);
  const pvSplit = raw.split(" pv ");
  if (!depthMatch || !scoreMatch || pvSplit.length < 2) {
    return null;
  }

  const depth = Number(depthMatch[1]);
  const scoreType = scoreMatch[1];
  const scoreValue = Number(scoreMatch[2]);
  const pv_uci = pvSplit[1].trim().split(/\s+/).filter(Boolean).slice(0, 12);
  if (!Number.isFinite(depth) || !Number.isFinite(scoreValue) || pv_uci.length === 0) {
    return null;
  }

  if (scoreType === "cp") {
    return { cp: scoreValue, depth, pv_uci };
  }

  return { mate: scoreValue, depth, pv_uci };
}

function emitResult(task: ActiveTask, final: boolean): void {
  const sortedLines = Array.from(task.lines.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([, line]) => parseScorePerspective(task.fen, line));

  postMessageToMain({
    type: "result",
    id: task.id,
    fen: task.fen,
    depth: task.depth,
    multiPv: task.multiPv,
    final,
    eval: sortedLines[0] || null,
    multipv: sortedLines.length > 0 ? sortedLines : null,
  });
}

function maybeEmitProgress(task: ActiveTask, lineDepth: number): void {
  const now = Date.now();
  if (lineDepth < Math.max(6, Math.floor(task.depth * 0.55))) {
    return;
  }
  if (now - task.lastEmitAt < 90) {
    return;
  }
  task.lastEmitAt = now;
  emitResult(task, false);
}

function finalizeActiveTask(): void {
  if (!activeTask) return;
  emitResult(activeTask, true);
  activeTask = null;
}

function normalizeEvaluateMessage(task: EvaluateMessage): EvaluateMessage {
  return {
    ...task,
    depth: Math.max(8, Math.min(task.depth, 18)),
    multiPv: Math.max(1, Math.min(task.multiPv, 3)),
  };
}

function startTask(task: EvaluateMessage): void {
  if (!engine) return;

  activeTask = {
    id: task.id,
    fen: task.fen,
    depth: task.depth,
    multiPv: task.multiPv,
    lines: new Map<number, EvalLine>(),
    lastEmitAt: 0,
  };

  engine.postMessage(`setoption name MultiPV value ${task.multiPv}`);
  engine.postMessage(`position fen ${task.fen}`);
  engine.postMessage(`go movetime ${computeMoveTimeMs(task.depth, task.multiPv)}`);
}

function maybeStartQueuedTask(): void {
  if (!initialized || !engine) return;
  const nextTask = queuedTask;
  queuedTask = null;
  stoppingForQueuedTask = false;
  if (!nextTask) return;
  startTask(nextTask);
}

function failInitialization(message: string): void {
  initializing = false;
  queuedTask = null;
  stoppingForQueuedTask = false;
  activeTask = null;
  if (initTimer) {
    clearTimeout(initTimer);
    initTimer = null;
  }
  const finalMessage =
    message === "Unable to initialize local Stockfish engine." && lastInitError
      ? `${message} Last error: ${lastInitError}`
      : message;
  postMessageToMain({ type: "error", message: finalMessage });
}

function bindEngineMessages(candidateEngine: Worker): void {
  candidateEngine.onmessage = (event) => {
    const text = String(event.data ?? "");

    if (text === "readyok") {
      if (initTimer) {
        clearTimeout(initTimer);
        initTimer = null;
      }
      engine = candidateEngine;
      initialized = true;
      initializing = false;
      lastInitError = null;
      postMessageToMain({ type: "ready" });
      return;
    }

    if (!activeTask) {
      if (text.startsWith("bestmove") && stoppingForQueuedTask) {
        maybeStartQueuedTask();
      }
      return;
    }

    if (text.startsWith("bestmove")) {
      finalizeActiveTask();
      maybeStartQueuedTask();
      return;
    }

    const parsed = parseEngineInfo(text);
    if (!parsed) {
      return;
    }

    const multipvIndex = parseMultiPvIndex(text);
    const task = activeTask;
    task.lines.set(multipvIndex, parsed);
    maybeEmitProgress(task, parsed.depth);
  };
}

function tryInitCandidate(index: number): void {
  if (index >= ENGINE_CANDIDATES.length) {
    failInitialization("Unable to initialize local Stockfish engine.");
    return;
  }

  const candidateMeta = ENGINE_CANDIDATES[index];
  let candidate: Worker;
  try {
    candidate = createEngineWorker(candidateMeta);
  } catch {
    tryInitCandidate(index + 1);
    return;
  }

  let didFallback = false;
  const fallbackOnce = () => {
    if (didFallback || initialized) {
      return;
    }
    didFallback = true;
    if (initTimer) {
      clearTimeout(initTimer);
      initTimer = null;
    }
    candidate.terminate();
    tryInitCandidate(index + 1);
  };

  bindEngineMessages(candidate);
  candidate.onerror = (event: ErrorEvent) => {
    const details = event.message ? ` (${event.message})` : "";
    lastInitError = `candidate '${candidateMeta.label}' failed${details}`;
    fallbackOnce();
  };

  candidate.postMessage("uci");
  candidate.postMessage("setoption name UCI_AnalyseMode value true");
  candidate.postMessage("setoption name Threads value 1");
  candidate.postMessage("setoption name Hash value 16");
  candidate.postMessage("isready");

  initTimer = setTimeout(() => {
    fallbackOnce();
  }, 15000);
}

function initEngine(): void {
  if (initialized) {
    postMessageToMain({ type: "ready" });
    return;
  }
  if (initializing) {
    return;
  }
  initializing = true;
  tryInitCandidate(0);
}

function computeMoveTimeMs(depth: number, multiPv: number): number {
  const fromDepth = 45 + depth * 11;
  const multiPvPenalty = (multiPv - 1) * 20;
  return Math.max(65, Math.min(fromDepth + multiPvPenalty, 260));
}

function evaluate(task: EvaluateMessage): void {
  if (!engine || !initialized) {
    postMessageToMain({
      type: "error",
      id: task.id,
      message: "Local engine is not ready yet.",
    });
    return;
  }

  const normalizedTask = normalizeEvaluateMessage(task);

  if (activeTask) {
    queuedTask = normalizedTask;
    if (!stoppingForQueuedTask) {
      stoppingForQueuedTask = true;
      engine.postMessage("stop");
    }
    return;
  }

  if (stoppingForQueuedTask) {
    queuedTask = normalizedTask;
    return;
  }

  startTask(normalizedTask);
}

ctx.onmessage = (event: MessageEvent<IncomingMessage>) => {
  const message = event.data;
  if (message.type === "init") {
    initEngine();
    return;
  }

  if (message.type === "evaluate") {
    evaluate(message);
  }
};
