"use client";

import React, { useMemo, useCallback } from "react";
import { Chessboard } from "react-chessboard";
import { Chess, Square } from "chess.js";

interface AnalysisBoardProps {
  fen: string;
  orientation?: "white" | "black";
  onMove?: (from: string, to: string, promotion?: string) => boolean;
  bestMove?: { from: string; to: string } | null;
  lastMove?: { from: string; to: string } | null;
  showArrows?: boolean;
  showCoordinates?: boolean;
  boardWidth?: number;
}

// Classic brown/tan board colors (high contrast)
const LIGHT_SQUARE_COLOR = "#f0d9b5";
const DARK_SQUARE_COLOR = "#b58863";
const HIGHLIGHT_COLOR = "rgba(255, 255, 0, 0.4)";
const BEST_MOVE_COLOR = "rgba(0, 128, 0, 0.6)";

export default function AnalysisBoard({
  fen,
  orientation = "white",
  onMove,
  bestMove,
  lastMove,
  showArrows = true,
  showCoordinates = true,
  boardWidth = 480,
}: AnalysisBoardProps) {
  // Create chess instance for move validation
  const chess = useMemo(() => {
    try {
      return new Chess(fen);
    } catch {
      return new Chess();
    }
  }, [fen]);

  // Custom square styles for highlighting
  const customSquareStyles = useMemo(() => {
    const styles: Record<string, React.CSSProperties> = {};

    // Highlight last move squares
    if (lastMove) {
      styles[lastMove.from] = {
        backgroundColor: HIGHLIGHT_COLOR,
      };
      styles[lastMove.to] = {
        backgroundColor: HIGHLIGHT_COLOR,
      };
    }

    return styles;
  }, [lastMove]);

  // Custom arrows for best move
  const customArrows = useMemo(() => {
    if (!showArrows || !bestMove) return [];
    
    return [
      [bestMove.from as Square, bestMove.to as Square, BEST_MOVE_COLOR] as [Square, Square, string],
    ];
  }, [showArrows, bestMove]);

  // Handle piece drop (drag and drop)
  const handlePieceDrop = useCallback(
    (sourceSquare: string, targetSquare: string, piece: string): boolean => {
      if (!onMove) return false;

      // Check if this is a valid move
      try {
        const tempChess = new Chess(fen);
        
        // Determine promotion piece if pawn reaching last rank
        let promotion: string | undefined;
        if (piece.toLowerCase().includes("p")) {
          const targetRank = targetSquare[1];
          if (targetRank === "8" || targetRank === "1") {
            promotion = "q"; // Default to queen
          }
        }

        // Try to make the move to validate
        const move = tempChess.move({
          from: sourceSquare,
          to: targetSquare,
          promotion,
        });

        if (move) {
          return onMove(sourceSquare, targetSquare, promotion);
        }
      } catch {
        return false;
      }

      return false;
    },
    [fen, onMove]
  );

  // Handle square click (for keyboard-style movement)
  const handleSquareClick = useCallback(
    (square: string) => {
      // Could implement click-to-move here if needed
    },
    []
  );

  // Check if a piece can be dragged (only if it's the turn of that color)
  const isDraggablePiece = useCallback(
    ({ piece }: { piece: string }): boolean => {
      if (!onMove) return false;
      
      const turn = chess.turn();
      const pieceColor = piece[0]; // 'w' or 'b'
      
      return (turn === "w" && pieceColor === "w") || (turn === "b" && pieceColor === "b");
    },
    [chess, onMove]
  );

  return (
    <div className="relative">
      <Chessboard
        position={fen}
        boardOrientation={orientation}
        onPieceDrop={handlePieceDrop}
        onSquareClick={handleSquareClick}
        isDraggablePiece={isDraggablePiece}
        boardWidth={boardWidth}
        customSquareStyles={customSquareStyles}
        customArrows={customArrows}
        showBoardNotation={showCoordinates}
        customDarkSquareStyle={{ backgroundColor: DARK_SQUARE_COLOR }}
        customLightSquareStyle={{ backgroundColor: LIGHT_SQUARE_COLOR }}
        customBoardStyle={{
          borderRadius: "4px",
          boxShadow: "0 4px 12px rgba(0, 0, 0, 0.15)",
        }}
        arePiecesDraggable={!!onMove}
        animationDuration={150}
      />
    </div>
  );
}
