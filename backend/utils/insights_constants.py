"""Configuration constants for chess insights processing."""

import os


FEATURE_VERSION = os.environ.get("INSIGHTS_FEATURE_VERSION", "1")
NARRATIVE_VERSION = os.environ.get("INSIGHTS_NARRATIVE_VERSION", "1")
MAX_GAMES_WINDOW = max(50, int(os.environ.get("INSIGHTS_MAX_GAMES", "150")))
MAX_CONCURRENT_INSIGHTS = max(1, int(os.environ.get("MAX_CONCURRENT_INSIGHTS", "1")))
LOW_TIME_RATIO = float(os.environ.get("INSIGHTS_LOW_TIME_RATIO", "0.1"))
LOW_TIME_FLOOR_SECONDS = max(10, int(os.environ.get("INSIGHTS_LOW_TIME_FLOOR_SECONDS", "30")))
MIN_BASELINE_GAMES = max(5, int(os.environ.get("INSIGHTS_MIN_GAMES", "12")))

NARRATIVE_PROVIDER = os.environ.get("INSIGHTS_NARRATIVE_PROVIDER", "none").lower()
NARRATIVE_API_URL = os.environ.get("INSIGHTS_NARRATIVE_API_URL", "").strip()
NARRATIVE_API_KEY = os.environ.get("INSIGHTS_NARRATIVE_API_KEY", "").strip()
NARRATIVE_MODEL = os.environ.get("INSIGHTS_NARRATIVE_MODEL", "").strip()
NARRATIVE_TIMEOUT_S = max(5, int(os.environ.get("INSIGHTS_NARRATIVE_TIMEOUT_S", "30")))
