/**
 * Move Tree Data Structure and Operations
 * 
 * Manages a tree of chess positions with variations.
 * Each node represents a position after a move.
 */

export interface EvalData {
  cp?: number;       // Centipawns (from side-to-move perspective)
  mate?: number;     // Mate in N (positive = winning)
  depth: number;
  pv?: string[];     // Principal variation (UCI moves)
  pvSan?: string[];  // Principal variation (SAN moves)
  multiPv?: Array<{
    cp?: number;
    mate?: number;
    pv: string[];
  }>;
}

export type MoveClassification = 
  | 'best' 
  | 'excellent' 
  | 'good' 
  | 'inaccuracy' 
  | 'mistake' 
  | 'blunder';

export interface MoveNode {
  id: string;                        // Unique ID (e.g., "root", "0", "0-1", "0-1-0")
  fen: string;                       // Position FEN
  san: string | null;                // Move in SAN (null for root)
  uci: string | null;                // Move in UCI
  ply: number;                       // Half-move count (0 = start)
  
  // Engine evaluation
  eval?: EvalData;
  
  // Classification
  classification?: MoveClassification;
  cpLoss?: number;                   // Centipawn loss vs best move
  
  // Best move suggestion
  bestMove?: {
    uci: string;
    san: string;
  };
  
  // Tree structure
  parent: string | null;             // Parent node ID
  children: string[];                // Child node IDs (mainline first, then variations)
  isMainline: boolean;
}

export interface MoveTree {
  nodes: Map<string, MoveNode>;
  rootId: string;
  currentId: string;                 // Currently selected position
}

/**
 * Create a new move tree with just the starting position
 */
export function createMoveTree(startFen: string = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'): MoveTree {
  const rootNode: MoveNode = {
    id: 'root',
    fen: startFen,
    san: null,
    uci: null,
    ply: 0,
    parent: null,
    children: [],
    isMainline: true,
  };

  const nodes = new Map<string, MoveNode>();
  nodes.set('root', rootNode);

  return {
    nodes,
    rootId: 'root',
    currentId: 'root',
  };
}

/**
 * Generate a unique node ID
 */
function generateNodeId(parentId: string, variationIndex: number): string {
  if (parentId === 'root') {
    return String(variationIndex);
  }
  return `${parentId}-${variationIndex}`;
}

/**
 * Add a move from the current position
 * If the move already exists as a child, navigate to it
 * Otherwise, create a new variation
 */
export function addMove(
  tree: MoveTree,
  san: string,
  uci: string,
  newFen: string,
  evalData?: EvalData,
  classification?: MoveClassification,
  cpLoss?: number,
  bestMove?: { uci: string; san: string }
): MoveTree {
  const currentNode = tree.nodes.get(tree.currentId);
  if (!currentNode) return tree;

  // Check if this move already exists as a child
  for (const childId of currentNode.children) {
    const child = tree.nodes.get(childId);
    if (child && child.uci === uci) {
      // Move exists, just navigate to it
      return {
        ...tree,
        currentId: childId,
      };
    }
  }

  // Create new node
  const variationIndex = currentNode.children.length;
  const newId = generateNodeId(tree.currentId, variationIndex);
  
  const newNode: MoveNode = {
    id: newId,
    fen: newFen,
    san,
    uci,
    ply: currentNode.ply + 1,
    eval: evalData,
    classification,
    cpLoss,
    bestMove,
    parent: tree.currentId,
    children: [],
    isMainline: variationIndex === 0, // First child is mainline
  };

  // Create new nodes map with updated parent and new child
  const newNodes = new Map(tree.nodes);
  newNodes.set(newId, newNode);
  
  // Update parent's children
  const updatedParent: MoveNode = {
    ...currentNode,
    children: [...currentNode.children, newId],
  };
  newNodes.set(tree.currentId, updatedParent);

  return {
    ...tree,
    nodes: newNodes,
    currentId: newId,
  };
}

/**
 * Navigate to a specific node
 */
export function navigateTo(tree: MoveTree, nodeId: string): MoveTree {
  if (!tree.nodes.has(nodeId)) return tree;
  return {
    ...tree,
    currentId: nodeId,
  };
}

/**
 * Go to the start position
 */
export function goToStart(tree: MoveTree): MoveTree {
  return {
    ...tree,
    currentId: tree.rootId,
  };
}

/**
 * Go to the end of the mainline
 */
export function goToEnd(tree: MoveTree): MoveTree {
  let currentId = tree.currentId;
  let node = tree.nodes.get(currentId);
  
  while (node && node.children.length > 0) {
    // Follow the first (mainline) child
    currentId = node.children[0];
    node = tree.nodes.get(currentId);
  }
  
  return {
    ...tree,
    currentId,
  };
}

/**
 * Go back one move (to parent)
 */
export function goBack(tree: MoveTree): MoveTree {
  const currentNode = tree.nodes.get(tree.currentId);
  if (!currentNode || !currentNode.parent) return tree;
  
  return {
    ...tree,
    currentId: currentNode.parent,
  };
}

/**
 * Go forward one move (to first child)
 */
export function goForward(tree: MoveTree): MoveTree {
  const currentNode = tree.nodes.get(tree.currentId);
  if (!currentNode || currentNode.children.length === 0) return tree;
  
  return {
    ...tree,
    currentId: currentNode.children[0],
  };
}

/**
 * Go to previous variation at the same level
 */
export function goToPreviousVariation(tree: MoveTree): MoveTree {
  const currentNode = tree.nodes.get(tree.currentId);
  if (!currentNode || !currentNode.parent) return tree;
  
  const parent = tree.nodes.get(currentNode.parent);
  if (!parent) return tree;
  
  const currentIndex = parent.children.indexOf(tree.currentId);
  if (currentIndex <= 0) return tree;
  
  return {
    ...tree,
    currentId: parent.children[currentIndex - 1],
  };
}

/**
 * Go to next variation at the same level
 */
export function goToNextVariation(tree: MoveTree): MoveTree {
  const currentNode = tree.nodes.get(tree.currentId);
  if (!currentNode || !currentNode.parent) return tree;
  
  const parent = tree.nodes.get(currentNode.parent);
  if (!parent) return tree;
  
  const currentIndex = parent.children.indexOf(tree.currentId);
  if (currentIndex < 0 || currentIndex >= parent.children.length - 1) return tree;
  
  return {
    ...tree,
    currentId: parent.children[currentIndex + 1],
  };
}

/**
 * Delete a variation (node and all its descendants)
 */
export function deleteVariation(tree: MoveTree, nodeId: string): MoveTree {
  const node = tree.nodes.get(nodeId);
  if (!node || !node.parent) return tree; // Can't delete root
  
  const newNodes = new Map(tree.nodes);
  
  // Remove from parent's children
  const parent = newNodes.get(node.parent);
  if (parent) {
    const updatedParent: MoveNode = {
      ...parent,
      children: parent.children.filter(id => id !== nodeId),
    };
    newNodes.set(parent.id, updatedParent);
  }
  
  // Delete node and all descendants
  const toDelete = [nodeId];
  while (toDelete.length > 0) {
    const id = toDelete.pop()!;
    const n = newNodes.get(id);
    if (n) {
      toDelete.push(...n.children);
      newNodes.delete(id);
    }
  }
  
  // If current node was deleted, go to parent
  let newCurrentId = tree.currentId;
  if (!newNodes.has(newCurrentId)) {
    newCurrentId = node.parent;
  }
  
  return {
    ...tree,
    nodes: newNodes,
    currentId: newCurrentId,
  };
}

/**
 * Update evaluation for a node
 */
export function updateNodeEval(
  tree: MoveTree,
  nodeId: string,
  evalData: EvalData,
  classification?: MoveClassification,
  cpLoss?: number
): MoveTree {
  const node = tree.nodes.get(nodeId);
  if (!node) return tree;
  
  const newNodes = new Map(tree.nodes);
  newNodes.set(nodeId, {
    ...node,
    eval: evalData,
    classification: classification ?? node.classification,
    cpLoss: cpLoss ?? node.cpLoss,
  });
  
  return {
    ...tree,
    nodes: newNodes,
  };
}

/**
 * Get the path from root to a specific node
 */
export function getPathToNode(tree: MoveTree, nodeId: string): MoveNode[] {
  const path: MoveNode[] = [];
  let currentId: string | null = nodeId;
  
  while (currentId) {
    const node = tree.nodes.get(currentId);
    if (!node) break;
    path.unshift(node);
    currentId = node.parent;
  }
  
  return path;
}

/**
 * Get the mainline moves as an array
 */
export function getMainline(tree: MoveTree): MoveNode[] {
  const mainline: MoveNode[] = [];
  let currentId = tree.rootId;
  
  while (currentId) {
    const node = tree.nodes.get(currentId);
    if (!node) break;
    
    if (node.san !== null) {
      mainline.push(node);
    }
    
    if (node.children.length === 0) break;
    currentId = node.children[0]; // Follow mainline (first child)
  }
  
  return mainline;
}

/**
 * Build a tree from analysis data (array of move evaluations)
 */
export function buildTreeFromAnalysis(
  moves: Array<{
    ply: number;
    san: string;
    uci: string;
    fen_before: string;
    fen_after: string;
    eval_before?: { cp?: number; mate?: number; depth?: number } | null;
    eval_after?: { cp?: number; mate?: number; depth?: number } | null;
    best_move_uci?: string | null;
    best_move_san?: string | null;
    pv?: string[];
    classification?: string | null;
    cp_loss?: number | null;
  }>,
  startFen?: string
): MoveTree {
  // Use first move's fen_before as start position if available
  const initialFen = startFen || (moves.length > 0 ? moves[0].fen_before : undefined);
  let tree = createMoveTree(initialFen);
  
  for (const move of moves) {
    const evalData: EvalData | undefined = move.eval_after ? {
      cp: move.eval_after.cp ?? undefined,
      mate: move.eval_after.mate ?? undefined,
      depth: move.eval_after.depth || 0,
      pv: move.pv,
    } : undefined;
    
    const bestMove = move.best_move_uci && move.best_move_san ? {
      uci: move.best_move_uci,
      san: move.best_move_san,
    } : undefined;
    
    tree = addMove(
      tree,
      move.san,
      move.uci,
      move.fen_after,
      evalData,
      move.classification as MoveClassification | undefined,
      move.cp_loss ?? undefined,
      bestMove
    );
  }
  
  // Go back to start
  tree = goToStart(tree);
  
  return tree;
}

/**
 * Get move number and color for a ply
 */
export function plyToMoveNumber(ply: number): { moveNumber: number; isWhite: boolean } {
  return {
    moveNumber: Math.floor((ply + 1) / 2),
    isWhite: ply % 2 === 0,
  };
}

/**
 * Format evaluation for display
 */
export function formatEval(evalData?: EvalData): string {
  if (!evalData) return '';
  
  if (evalData.mate !== undefined && evalData.mate !== null) {
    const sign = evalData.mate > 0 ? '+' : '';
    return `M${sign}${evalData.mate}`;
  }
  
  if (evalData.cp !== undefined && evalData.cp !== null) {
    const value = evalData.cp / 100;
    const sign = value > 0 ? '+' : '';
    return `${sign}${value.toFixed(1)}`;
  }
  
  return '';
}

/**
 * Get classification color class
 */
export function getClassificationColor(classification?: MoveClassification): string {
  switch (classification) {
    case 'best':
    case 'excellent':
      return 'text-green-500';
    case 'good':
      return 'text-green-400';
    case 'inaccuracy':
      return 'text-yellow-500';
    case 'mistake':
      return 'text-orange-500';
    case 'blunder':
      return 'text-red-500';
    default:
      return '';
  }
}

/**
 * Get classification annotation symbol
 */
export function getClassificationSymbol(classification?: MoveClassification): string {
  switch (classification) {
    case 'best':
      return '!!';
    case 'excellent':
      return '!';
    case 'good':
      return '';
    case 'inaccuracy':
      return '?!';
    case 'mistake':
      return '?';
    case 'blunder':
      return '??';
    default:
      return '';
  }
}
