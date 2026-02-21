"""Regression tests for tactical turning-point surfacing."""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.append(str(pathlib.Path(__file__).resolve().parent))

from single_game_insights import _detect_turning_points, _tactical_turning_reason


class SingleGameInsightsTacticalTest(unittest.TestCase):
    def test_hanging_piece_reason_text(self) -> None:
        reason = _tactical_turning_reason(
            {
                "tactic_type": "HANGING_PIECE",
                "line_source": "played_line",
                "hanging_piece_name": "Rook",
            }
        )
        self.assertIsNotNone(reason)
        self.assertEqual(reason.get("reason_text"), "hung a rook")

    def test_tactical_turning_point_survives_local_dedupe(self) -> None:
        rows = [
            {
                "ply": 25,
                "move_index": 24,
                "actor": "user",
                "phase": "middlegame",
                "eval_before": -120,
                "eval_after": -360,
                "delta": -240,
                "swing_abs": 240,
                "cp_loss": 280,
                "classification": "blunder",
                "san": "Ra1",
                "uci": "b1a1",
                "fen_before": "",
                "fen_after": "",
                "tactical": {
                    "tactic_type": "HANGING_PIECE",
                    "line_source": "played_line",
                    "hanging_piece_name": "Rook",
                },
            },
            {
                "ply": 26,
                "move_index": 25,
                "actor": "opponent",
                "phase": "middlegame",
                "eval_before": -360,
                "eval_after": -610,
                "delta": -250,
                "swing_abs": 250,
                "cp_loss": 90,
                "classification": "inaccuracy",
                "san": "Qxa1",
                "uci": "a5a1",
                "fen_before": "",
                "fen_after": "",
                "tactical": None,
            },
            {
                "ply": 30,
                "move_index": 29,
                "actor": "user",
                "phase": "middlegame",
                "eval_before": -100,
                "eval_after": -500,
                "delta": -400,
                "swing_abs": 400,
                "cp_loss": 360,
                "classification": "blunder",
                "san": "Qe2",
                "uci": "d1e2",
                "fen_before": "",
                "fen_after": "",
                "tactical": None,
            },
        ]

        turning = _detect_turning_points(rows)
        events = turning.get("events") or []
        self.assertTrue(events)
        tactical_events = [event for event in events if isinstance(event.get("tactical"), dict)]
        self.assertTrue(tactical_events)
        self.assertEqual(tactical_events[0].get("ply"), 25)
        self.assertEqual(tactical_events[0].get("tactical", {}).get("reason_text"), "hung a rook")


if __name__ == "__main__":
    unittest.main()
