"""Focused regression tests for tactical motif detection."""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.append(str(pathlib.Path(__file__).resolve().parent))

from tactical_detection import TacticalConfig, detect_tactical_annotation


CFG = TacticalConfig(
    enabled=True,
    cp_loss_inaccuracy=120,
    cp_loss_mistake=170,
    cp_loss_blunder=240,
    max_pv_plies=8,
    forced_mate_plies=6,
    min_material_cp=250,
)


class TacticalDetectionTest(unittest.TestCase):
    def test_detects_hanging_rook(self) -> None:
        result = detect_tactical_annotation(
            fen_before="6k1/8/8/q7/8/8/8/1R4K1 w - - 0 1",
            fen_after="6k1/8/8/q7/8/8/8/R5K1 b - - 1 1",
            played_uci="b1a1",
            best_move_uci="b1b8",
            pv_before_uci=["b1b8", "g8f7"],
            pv_after_uci=["a5a1", "g1f2"],
            classification="blunder",
            cp_loss=420,
            eval_before={"cp": 30},
            eval_after={"cp": -520},
            multi_pv=[{"cp": 300, "pv": ["b1b8"]}, {"cp": -200, "pv": ["b1a1"]}],
            config=CFG,
        )

        self.assertTrue(result.get("tactic_detected"))
        self.assertEqual(result.get("tactic_type"), "HANGING_PIECE")
        self.assertEqual(result.get("hanging_piece_name"), "Rook")

    def test_detects_missed_forced_mate(self) -> None:
        result = detect_tactical_annotation(
            fen_before="7k/6Q1/5K2/8/8/8/8/8 w - - 0 1",
            fen_after="7k/7Q/5K2/8/8/8/8/8 b - - 1 1",
            played_uci="g7h7",
            best_move_uci="g7g8",
            pv_before_uci=["g7g8"],
            pv_after_uci=["h8h7"],
            classification=None,
            cp_loss=None,
            eval_before={"cp": 850},
            eval_after={"cp": 700},
            multi_pv=[{"mate": 1, "pv": ["g7g8"]}],
            config=CFG,
        )

        self.assertTrue(result.get("tactic_detected"))
        self.assertEqual(result.get("tactic_type"), "MISSED_FORCED_MATE")
        self.assertEqual(result.get("line_source"), "best_line")
        self.assertEqual(result.get("mate_outcome", {}).get("mate_in"), 1)

    def test_detects_missed_skewer(self) -> None:
        result = detect_tactical_annotation(
            fen_before="3qk3/8/8/8/8/8/8/4K2R w - - 0 1",
            fen_after="3qk3/8/8/8/8/8/7R/4K3 b - - 1 1",
            played_uci="h1h2",
            best_move_uci="h1h8",
            pv_before_uci=["h1h8", "e8e7", "h8d8"],
            pv_after_uci=["e8f7"],
            classification="mistake",
            cp_loss=300,
            eval_before={"cp": 60},
            eval_after={"cp": -260},
            multi_pv=[{"cp": 640, "pv": ["h1h8", "e8e7", "h8d8"]}, {"cp": 120, "pv": ["h1h2"]}],
            config=CFG,
        )

        self.assertTrue(result.get("tactic_detected"))
        self.assertEqual(result.get("tactic_type"), "SKEWER")
        self.assertEqual(result.get("line_source"), "best_line")
        self.assertEqual(result.get("skewer_front_piece"), "King")
        self.assertEqual(result.get("skewer_rear_piece"), "Queen")

    def test_detects_missed_knight_fork(self) -> None:
        result = detect_tactical_annotation(
            fen_before="r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1",
            fen_after="r3k3/8/3N4/8/8/8/8/4K3 b - - 1 1",
            played_uci="b5d6",
            best_move_uci="b5c7",
            pv_before_uci=["b5c7", "e8d7", "c7a8"],
            pv_after_uci=["e8f7"],
            classification="mistake",
            cp_loss=260,
            eval_before={"cp": 40},
            eval_after={"cp": -230},
            multi_pv=[{"cp": 520, "pv": ["b5c7", "e8d7", "c7a8"]}, {"cp": 80, "pv": ["b5d6"]}],
            config=CFG,
        )

        self.assertTrue(result.get("tactic_detected"))
        self.assertEqual(result.get("tactic_type"), "FORK")
        self.assertEqual(result.get("line_source"), "best_line")
        self.assertEqual(result.get("missed_move_uci"), "b5c7")


if __name__ == "__main__":
    unittest.main()
