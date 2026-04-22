"""Regression tests for deep-analysis review label derivation."""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.append(str(pathlib.Path(__file__).resolve().parent))

from services.full_analysis import _derive_review_tag


class ReviewTagDerivationTest(unittest.TestCase):
    def test_book_takes_precedence(self) -> None:
        tag = _derive_review_tag(
            ply=2,
            classification="mistake",
            cp_loss=220,
            tactical={"missed_move_uci": "e2e4", "tactic_detected": True},
            eval_before_cp=0,
            eval_after_cp=-200,
            mover_is_white=True,
            opening_ply_count=8,
        )
        self.assertEqual(tag, "book")

    def test_miss_requires_threshold_for_non_mate(self) -> None:
        below_threshold = _derive_review_tag(
            ply=16,
            classification="mistake",
            cp_loss=110,
            tactical={"missed_move_uci": "g2g4"},
            eval_before_cp=20,
            eval_after_cp=-90,
            mover_is_white=True,
            opening_ply_count=None,
        )
        self.assertEqual(below_threshold, "mistake")

        at_threshold = _derive_review_tag(
            ply=16,
            classification="mistake",
            cp_loss=140,
            tactical={"missed_move_uci": "g2g4"},
            eval_before_cp=20,
            eval_after_cp=-120,
            mover_is_white=True,
            opening_ply_count=None,
        )
        self.assertEqual(at_threshold, "miss")

    def test_forced_mate_miss_does_not_require_cp_threshold(self) -> None:
        tag = _derive_review_tag(
            ply=24,
            classification="inaccuracy",
            cp_loss=20,
            tactical={"tactic_type": "MISSED_FORCED_MATE"},
            eval_before_cp=100,
            eval_after_cp=-50,
            mover_is_white=True,
            opening_ply_count=None,
        )
        self.assertEqual(tag, "miss")

    def test_great_detected_for_black_when_eval_swings_toward_black(self) -> None:
        tag = _derive_review_tag(
            ply=9,
            classification="best",
            cp_loss=0,
            tactical=None,
            eval_before_cp=80,
            eval_after_cp=-40,
            mover_is_white=False,
            opening_ply_count=None,
        )
        self.assertEqual(tag, "great")

    def test_brilliant_requires_tactical_and_sacrifice(self) -> None:
        tag = _derive_review_tag(
            ply=13,
            classification="best",
            cp_loss=0,
            tactical={
                "tactic_detected": True,
                "material_outcome": {"cp_net_for_mover": -300},
            },
            eval_before_cp=40,
            eval_after_cp=180,
            mover_is_white=True,
            opening_ply_count=None,
        )
        self.assertEqual(tag, "brilliant")


if __name__ == "__main__":
    unittest.main()
