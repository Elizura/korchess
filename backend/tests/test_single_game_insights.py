"""Regression tests for tactical turning-point surfacing."""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.append(str(pathlib.Path(__file__).resolve().parent))

from single_game_insights import (
    _detect_turning_points,
    _grade_to_rating_5,
    _score_to_rating_5,
    _tactical_turning_reason,
)


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

    def test_score_to_rating_5_boundaries(self) -> None:
        self.assertEqual(_score_to_rating_5(100.0), 5)
        self.assertEqual(_score_to_rating_5(85.0), 5)
        self.assertEqual(_score_to_rating_5(84.9), 4)
        self.assertEqual(_score_to_rating_5(70.0), 4)
        self.assertEqual(_score_to_rating_5(55.0), 3)
        self.assertEqual(_score_to_rating_5(40.0), 2)
        self.assertEqual(_score_to_rating_5(39.9), 1)
        self.assertIsNone(_score_to_rating_5(None))

    def test_grade_to_rating_5_mapping(self) -> None:
        self.assertEqual(_grade_to_rating_5("A"), 5)
        self.assertEqual(_grade_to_rating_5("b"), 4)
        self.assertEqual(_grade_to_rating_5("C"), 3)
        self.assertEqual(_grade_to_rating_5("D"), 2)
        self.assertEqual(_grade_to_rating_5("E"), 1)
        self.assertIsNone(_grade_to_rating_5("N/A"))

    def test_turning_points_are_chronological_and_keep_all_detected_events(self) -> None:
        rows = [
            {
                "ply": 11,
                "move_index": 10,
                "actor": "user",
                "phase": "middlegame",
                "eval_before": 180,
                "eval_after": -700,
                "delta": -880,
                "swing_abs": 880,
                "cp_loss": 320,
                "classification": "blunder",
                "san": "Qe2",
                "uci": "d1e2",
                "fen_before": "",
                "fen_after": "",
                "tactical": None,
            },
            {
                "ply": 3,
                "move_index": 2,
                "actor": "user",
                "phase": "opening",
                "eval_before": 120,
                "eval_after": -520,
                "delta": -640,
                "swing_abs": 640,
                "cp_loss": 280,
                "classification": "mistake",
                "san": "Nc3",
                "uci": "b1c3",
                "fen_before": "",
                "fen_after": "",
                "tactical": None,
            },
            {
                "ply": 15,
                "move_index": 14,
                "actor": "opponent",
                "phase": "middlegame",
                "eval_before": -140,
                "eval_after": 620,
                "delta": 760,
                "swing_abs": 760,
                "cp_loss": 260,
                "classification": "mistake",
                "san": "Qh4",
                "uci": "d8h4",
                "fen_before": "",
                "fen_after": "",
                "tactical": None,
            },
            {
                "ply": 7,
                "move_index": 6,
                "actor": "user",
                "phase": "opening",
                "eval_before": 90,
                "eval_after": -560,
                "delta": -650,
                "swing_abs": 650,
                "cp_loss": 240,
                "classification": "mistake",
                "san": "Be2",
                "uci": "f1e2",
                "fen_before": "",
                "fen_after": "",
                "tactical": None,
            },
        ]

        turning = _detect_turning_points(rows)
        events = turning.get("events") or []
        self.assertEqual(len(events), 4)
        self.assertEqual([event["ply"] for event in events], [3, 7, 11, 15])


if __name__ == "__main__":
    unittest.main()
