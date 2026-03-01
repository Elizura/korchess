"use client";

import React, { useMemo, useRef, useEffect } from "react";
import {
  MoveTree,
  MoveNode,
  ReviewTag,
  toReviewTag,
  plyToMoveNumber,
} from "@/lib/moveTree";

interface MoveListProps {
  tree: MoveTree;
  currentId: string;
  onSelectMove: (nodeId: string) => void;
  onDeleteVariation?: (nodeId: string) => void;
  maxHeight?: number;
}

function getReviewTag(node: MoveNode): ReviewTag | undefined {
  return node.reviewTag ?? toReviewTag(undefined, node.classification, node.cpLoss);
}

function getReviewTagBg(reviewTag?: ReviewTag): string {
  switch (reviewTag) {
    case "book":
      return "bg-cyan-500/18";
    case "brilliant":
      return "bg-teal-500/15";
    case "great":
      return "bg-sky-500/15";
    case "best":
    case "excellent":
      return "bg-emerald-500/15";
    case "good":
      return "bg-lime-500/12";
    case "inaccuracy":
      return "bg-amber-500/26";
    case "mistake":
      return "bg-orange-600/30";
    case "miss":
      return "bg-red-500/16";
    case "blunder":
      return "bg-red-600/28";
    default:
      return "";
  }
}

function getReviewTagBadgeClass(reviewTag?: ReviewTag): string {
  switch (reviewTag) {
    case "book":
      return "border-cyan-300/55 bg-cyan-500/20 text-cyan-100";
    case "brilliant":
      return "border-teal-300/45 bg-teal-500/20 text-teal-100";
    case "great":
      return "border-sky-300/45 bg-sky-500/22 text-sky-100";
    case "best":
      return "border-emerald-300/45 bg-emerald-500/20 text-emerald-100";
    case "excellent":
      return "border-green-300/40 bg-green-500/18 text-green-100";
    case "good":
      return "border-lime-300/40 bg-lime-500/16 text-lime-100";
    case "inaccuracy":
      return "border-amber-300/60 bg-amber-500/26 text-amber-50";
    case "mistake":
      return "border-orange-400/65 bg-orange-600/30 text-orange-50";
    case "miss":
      return "border-rose-300/45 bg-rose-500/20 text-rose-100";
    case "blunder":
      return "border-red-500/70 bg-red-600/35 text-red-50";
    default:
      return "border-[color:var(--zen-border)] bg-[color:var(--zen-surface-2)] text-[color:var(--zen-muted)]";
  }
}

interface MoveButtonProps {
  node: MoveNode;
  isSelected: boolean;
  onClick: () => void;
  showMoveNumber?: boolean;
}

function MoveButton({ node, isSelected, onClick, showMoveNumber }: MoveButtonProps) {
  const { moveNumber, isWhite } = plyToMoveNumber(node.ply);
  const reviewTag = getReviewTag(node);
  const bgClass = isSelected
    ? "bg-[color:var(--zen-accent)] text-white"
    : getReviewTagBg(reviewTag);

  return (
    <button
      onClick={onClick}
      className={`
        inline-flex items-center px-1.5 py-0.5 rounded text-sm font-mono
        hover:bg-[color:var(--zen-accent-2)] transition-colors
        ${bgClass}
        ${isSelected ? "ring-2 ring-[color:var(--zen-accent)]" : ""}
      `}
    >
      {showMoveNumber && (
        <span className="text-[color:var(--zen-muted)] mr-1">
          {moveNumber}.{!isWhite && ".."}
        </span>
      )}
      <span className={isSelected ? "text-white" : "text-[color:var(--zen-text)]"}>{node.san}</span>
    </button>
  );
}

interface VariationLineProps {
  tree: MoveTree;
  startNodeId: string;
  currentId: string;
  onSelectMove: (nodeId: string) => void;
  depth: number;
}

function VariationLine({
  tree,
  startNodeId,
  currentId,
  onSelectMove,
  depth,
}: VariationLineProps) {
  const nodes: MoveNode[] = [];
  let nodeId: string | null = startNodeId;

  // Collect nodes in this line (follow first child until end or branch)
  while (nodeId) {
    const node = tree.nodes.get(nodeId);
    if (!node) break;
    nodes.push(node);
    // Stop if there are multiple children (branch point)
    if (node.children.length !== 1) break;
    nodeId = node.children[0];
  }

  // Get the last node to check for branches
  const lastNode = nodes[nodes.length - 1];
  const branches = lastNode?.children.slice(1) || []; // Skip first child (mainline)

  return (
    <span className="inline">
      {depth > 0 && <span className="text-[color:var(--zen-muted)] mx-1">(</span>}
      {nodes.map((node, idx) => {
        const { moveNumber, isWhite } = plyToMoveNumber(node.ply);
        const showNumber = idx === 0 || isWhite;
        
        return (
          <span key={node.id} className="inline">
            <MoveButton
              node={node}
              isSelected={node.id === currentId}
              onClick={() => onSelectMove(node.id)}
              showMoveNumber={showNumber}
            />
            {idx < nodes.length - 1 && <span className="mx-0.5" />}
          </span>
        );
      })}
      {/* Render first child continuation if exists */}
      {lastNode && lastNode.children.length > 0 && (
        <>
          <span className="mx-0.5" />
          <VariationLine
            tree={tree}
            startNodeId={lastNode.children[0]}
            currentId={currentId}
            onSelectMove={onSelectMove}
            depth={depth}
          />
        </>
      )}
      {/* Render variations (other children) */}
      {branches.map((branchId) => (
        <span key={branchId} className="text-[color:var(--zen-muted)] text-sm">
          <VariationLine
            tree={tree}
            startNodeId={branchId}
            currentId={currentId}
            onSelectMove={onSelectMove}
            depth={depth + 1}
          />
        </span>
      ))}
      {depth > 0 && <span className="text-[color:var(--zen-muted)] mx-1">)</span>}
    </span>
  );
}

export default function MoveList({
  tree,
  currentId,
  onSelectMove,
  onDeleteVariation,
  maxHeight = 300,
}: MoveListProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to keep current move visible
  useEffect(() => {
    if (containerRef.current) {
      const selected = containerRef.current.querySelector('[data-selected="true"]');
      if (selected) {
        selected.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    }
  }, [currentId]);

  // Build move pairs for two-column display
  const movePairs = useMemo(() => {
    const pairs: Array<{
      moveNumber: number;
      white: MoveNode | null;
      black: MoveNode | null;
      whiteVariations: string[];
      blackVariations: string[];
    }> = [];

    // Get mainline nodes
    const root = tree.nodes.get(tree.rootId);
    if (!root) return pairs;

    let currentNode = root;
    let moveNumber = 1;

    while (currentNode.children.length > 0) {
      const whiteNode = tree.nodes.get(currentNode.children[0]);
      if (!whiteNode) break;

      // Get variations from this point
      const whiteVariations = currentNode.children.slice(1);

      let blackNode: MoveNode | null = null;
      let blackVariations: string[] = [];

      if (whiteNode.children.length > 0) {
        blackNode = tree.nodes.get(whiteNode.children[0]) || null;
        blackVariations = whiteNode.children.slice(1);
      }

      pairs.push({
        moveNumber,
        white: whiteNode,
        black: blackNode,
        whiteVariations,
        blackVariations,
      });

      moveNumber++;

      if (blackNode) {
        currentNode = blackNode;
      } else {
        break;
      }
    }

    return pairs;
  }, [tree]);

  if (movePairs.length === 0) {
    return (
      <div className="text-[color:var(--zen-muted)] text-center py-4 text-sm">
        No moves yet
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="move-list-scroll overflow-y-auto font-mono text-sm"
      style={{ maxHeight }}
    >
      <div className="space-y-0.5">
        {movePairs.map((pair) => (
          <div key={pair.moveNumber} className="flex items-start gap-x-1">
            {/* Move number */}
            <div className="w-8 flex-shrink-0 text-[color:var(--zen-muted)] text-right pr-2">
              {pair.moveNumber}.
            </div>
            
            {/* White move */}
            <div className="w-[7.5rem] flex-shrink-0" data-selected={pair.white?.id === currentId}>
              {pair.white && (
                <MoveButton
                  node={pair.white}
                  isSelected={pair.white.id === currentId}
                  onClick={() => onSelectMove(pair.white!.id)}
                />
              )}
            </div>
            
            {/* Black move */}
            <div className="w-[7.5rem] flex-shrink-0" data-selected={pair.black?.id === currentId}>
              {pair.black && (
                <MoveButton
                  node={pair.black}
                  isSelected={pair.black.id === currentId}
                  onClick={() => onSelectMove(pair.black!.id)}
                />
              )}
            </div>

            {/* Variations indicator */}
            {(pair.whiteVariations.length > 0 || pair.blackVariations.length > 0) && (
              <div className="text-xs text-[color:var(--zen-muted)] ml-2">
                {pair.whiteVariations.length + pair.blackVariations.length} var
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Render variations below */}
      {movePairs.some((p) => p.whiteVariations.length > 0 || p.blackVariations.length > 0) && (
        <div className="mt-3 pt-3 border-t border-[color:var(--zen-border)]">
          <div className="text-xs text-[color:var(--zen-muted)] mb-2">Variations:</div>
          {movePairs.map((pair) => (
            <React.Fragment key={`var-${pair.moveNumber}`}>
              {pair.whiteVariations.map((varId) => (
                <div key={varId} className="text-sm text-[color:var(--zen-muted)] mb-1 pl-2">
                  <span className="text-[color:var(--zen-muted)]">{pair.moveNumber}.</span>
                  <VariationLine
                    tree={tree}
                    startNodeId={varId}
                    currentId={currentId}
                    onSelectMove={onSelectMove}
                    depth={1}
                  />
                </div>
              ))}
              {pair.blackVariations.map((varId) => (
                <div key={varId} className="text-sm text-[color:var(--zen-muted)] mb-1 pl-2">
                  <span className="text-[color:var(--zen-muted)]">{pair.moveNumber}...</span>
                  <VariationLine
                    tree={tree}
                    startNodeId={varId}
                    currentId={currentId}
                    onSelectMove={onSelectMove}
                    depth={1}
                  />
                </div>
              ))}
            </React.Fragment>
          ))}
        </div>
      )}
    </div>
  );
}
