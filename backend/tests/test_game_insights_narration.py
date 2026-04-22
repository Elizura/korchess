"""Regression tests for narration jargon guard and cache freshness."""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.append(str(pathlib.Path(__file__).resolve().parent))

from services.game_insights_narration import (  # noqa: E402
    NARRATION_SCHEMA_VERSION,
    _build_fallback_narration,
    _compact_events,
    build_turning_point_bullets_from_events,
    enforce_turning_points_grounding,
    is_current_clean_narration_payload,
    narration_has_forbidden_jargon,
)
try:
    from routers.analysis import _is_ai_cached_payload_current_and_clean  # noqa: E402
except Exception:  # pragma: no cover - local test env may not include optional runtime deps
    def _is_ai_cached_payload_current_and_clean(payload: dict | None) -> bool:
        return is_current_clean_narration_payload(payload)


def _base_clean_payload(schema_version: str) -> dict:
    return {
        "narration": {
            "title": "Volatile Game Summary",
            "one_liner": "Own errors drove the result in key moments.",
            "confidence_note": "Confidence is based on deterministic game evidence.",
            "sections": [
                {"heading": "Result summary", "bullets": ["You were punished after key mistakes."]},
                {
                    "heading": "Turning points",
                    "bullets": ["At move 7, your move worsened your position because king safety slipped."],
                },
                {"heading": "What you did well", "bullets": ["You kept practical chances alive."]},
                {"heading": "What to improve", "bullets": ["Scan forcing threats before committing."]},
                {"heading": "Next game focus", "bullets": ["Prioritize king safety in unstable positions."]},
            ],
            "labels": {"decisive_phase": "middlegame", "player_style": "Volatile"},
        },
        "narration_meta": {
            "source": "gemini",
            "schema_version": schema_version,
            "cache_key": "test-key",
        },
    }


class GameInsightsNarrationTest(unittest.TestCase):
    def test_build_turning_point_bullets_are_grounded_and_jargon_free(self) -> None:
        bullets = build_turning_point_bullets_from_events(
            [
                {
                    "ply": 9,
                    "actor": "user",
                    "phase": "opening",
                    "reason_hint": "you lost central control and left your king exposed",
                    "impact_level": "major",
                    "swing_cp": -260,
                    "pre_eval_cp": -10,
                    "post_eval_cp": -270,
                }
            ]
        )
        self.assertEqual(len(bullets), 1)
        self.assertIn("At move 5", bullets[0])
        self.assertIn("because", bullets[0].lower())
        self.assertNotIn("CP", bullets[0].upper())
        self.assertNotIn("centipawn", bullets[0].lower())

    def test_build_turning_point_bullets_are_chronological_and_not_truncated(self) -> None:
        bullets = build_turning_point_bullets_from_events(
            [
                {"ply": 11, "actor": "user", "phase": "middlegame", "reason_hint": "piece activity dropped"},
                {"ply": 3, "actor": "opponent", "phase": "opening", "reason_hint": "your opponent weakened king safety"},
                {"ply": 7, "actor": "user", "phase": "opening", "reason_hint": "you gave up central control"},
                {"ply": 15, "actor": "user", "phase": "middlegame", "reason_hint": "you missed a forcing threat"},
            ]
        )
        self.assertEqual(len(bullets), 4)
        self.assertTrue(bullets[0].startswith("At move 2"))
        self.assertTrue(bullets[1].startswith("At move 4"))
        self.assertTrue(bullets[2].startswith("At move 6"))
        self.assertTrue(bullets[3].startswith("At move 8"))

    def test_enforce_turning_points_grounding_overrides_mismatched_turning_section(self) -> None:
        narration = {
            "title": "Test",
            "one_liner": "Test",
            "confidence_note": "Test",
            "sections": [
                {"heading": "Result summary", "bullets": ["Kept as-is"]},
                {"heading": "Turning points", "bullets": ["At move 99, something happened for unknown reasons."]},
                {"heading": "What you did well", "bullets": ["Good defense"]},
                {"heading": "What to improve", "bullets": ["Check forcing moves"]},
                {"heading": "Next game focus", "bullets": ["Scan threats earlier"]},
            ],
            "labels": {"decisive_phase": "middlegame", "player_style": "Volatile"},
        }
        raw_insights = {
            "turning_points": {
                "events": [
                    {
                        "ply": 7,
                        "actor": "user",
                        "phase": "middlegame",
                        "is_decisive": True,
                        "reason_hint": "king safety broke down around forcing checks",
                    }
                ]
            }
        }

        grounded = enforce_turning_points_grounding(narration, raw_insights)
        turning = next(section for section in grounded["sections"] if section["heading"] == "Turning points")
        self.assertEqual(len(turning["bullets"]), 1)
        self.assertIn("At move 4", turning["bullets"][0])
        self.assertNotIn("move 99", turning["bullets"][0].lower())
        self.assertFalse(narration_has_forbidden_jargon(grounded))

    def test_compact_events_uses_reason_hints_without_cp_fields(self) -> None:
        compact = _compact_events(
            [
                {
                    "ply": 9,
                    "actor": "user",
                    "phase": "middlegame",
                    "label": "Turning Point",
                    "pre_eval_cp": -20,
                    "post_eval_cp": -260,
                    "swing_cp": -240,
                    "severity_score": 88,
                    "is_decisive": True,
                }
            ]
        )
        self.assertEqual(len(compact), 1)
        event = compact[0]
        self.assertEqual(event.get("momentum_direction"), "against_user")
        self.assertEqual(event.get("impact_level"), "decisive")
        self.assertIn("reason_hint", event)
        self.assertNotIn("pre_eval_cp", event)
        self.assertNotIn("post_eval_cp", event)

    def test_fallback_turning_points_are_move_based_and_jargon_free(self) -> None:
        narration = _build_fallback_narration(
            {
                "result_cause": {"primary_label": "Own Errors", "secondary_label": "Time Pressure"},
                "decisive_phase": {"decisive_phase": "middlegame"},
                "turning_points": {
                    "events": [
                        {
                            "ply": 5,
                            "actor": "user",
                            "phase": "opening",
                            "swing_cp": -230,
                            "severity_score": 91,
                            "reason_hint": "you lost central control and left your king exposed",
                            "impact_level": "decisive",
                        }
                    ]
                },
                "phase_ratings": {
                    "opening": {"rating_5": 2, "evaluation_state": "scored", "confidence": 0.8},
                    "middlegame": {"rating_5": 3, "evaluation_state": "scored", "confidence": 0.8},
                    "endgame": {"rating_5": None, "evaluation_state": "not_reached", "confidence": 0.0},
                },
                "time_pressure_collapse": {"status": "not_detected", "data_quality": {"clock_moves": 0, "user_moves": 0}},
                "game_character": {"label": "Volatile"},
                "confidence": 0.72,
            },
            {"result": "loss", "timeout_loss": False, "timeout_signal": None},
        )
        turning = next(section for section in narration["sections"] if section["heading"] == "Turning points")
        text = " ".join(turning["bullets"])
        self.assertIn("At move", text)
        self.assertNotIn("CP", text.upper())
        self.assertFalse(narration_has_forbidden_jargon(narration))

    def test_fallback_phase_text_uses_rating_out_of_five(self) -> None:
        narration = _build_fallback_narration(
            {
                "result_cause": {"primary_label": "Own Errors", "secondary_label": "Unknown"},
                "decisive_phase": {"decisive_phase": "opening"},
                "turning_points": {"events": []},
                "phase_grades": {
                    "opening": {"grade": "E", "evaluation_state": "scored", "confidence": 0.9},
                    "middlegame": {"grade": "C", "evaluation_state": "scored", "confidence": 0.8},
                    "endgame": {"grade": "N/A", "evaluation_state": "not_reached", "confidence": 0.0},
                },
                "time_pressure_collapse": {"status": "unavailable", "data_quality": {}},
                "game_character": {"label": "Stable"},
                "confidence": 0.6,
            },
            {"result": "loss", "timeout_loss": False, "timeout_signal": None},
        )
        well = next(section for section in narration["sections"] if section["heading"] == "What you did well")
        joined = " ".join(well["bullets"])
        self.assertIn("1/5", joined)
        self.assertNotIn("grade", joined.lower())

    def test_current_clean_payload_validation_and_stale_detection(self) -> None:
        clean_payload = _base_clean_payload(NARRATION_SCHEMA_VERSION)
        stale_payload = _base_clean_payload("4" if NARRATION_SCHEMA_VERSION != "4" else "3")
        jargon_payload = _base_clean_payload(NARRATION_SCHEMA_VERSION)
        jargon_payload["narration"]["sections"][1]["bullets"][0] = "At move 7, eval shifted from +0.9 to -2.3 CP."

        self.assertTrue(is_current_clean_narration_payload(clean_payload))
        self.assertFalse(is_current_clean_narration_payload(stale_payload))
        self.assertFalse(is_current_clean_narration_payload(jargon_payload))

        self.assertTrue(_is_ai_cached_payload_current_and_clean(clean_payload))
        self.assertFalse(_is_ai_cached_payload_current_and_clean(stale_payload))
        self.assertFalse(_is_ai_cached_payload_current_and_clean(jargon_payload))


if __name__ == "__main__":
    unittest.main()
