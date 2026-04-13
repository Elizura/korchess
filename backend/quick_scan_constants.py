"""Configuration constants for lightweight quick-scan analysis."""

import os


QUICK_SCAN_DEPTH = max(4, int(os.environ.get("QUICK_SCAN_DEPTH", "8")))
QUICK_SCAN_TIME_MS = max(30, int(os.environ.get("QUICK_SCAN_TIME_MS", "60")))
QUICK_SCAN_CONCURRENCY = max(1, int(os.environ.get("QUICK_SCAN_CONCURRENCY", "2")))
QUICK_SCAN_CP_THRESHOLD = max(50, int(os.environ.get("QUICK_SCAN_CP_THRESHOLD", "100")))
QUICK_SCAN_MAX_GAMES = max(10, int(os.environ.get("QUICK_SCAN_MAX_GAMES", "500")))
MAX_CONCURRENT_SCANS = max(1, int(os.environ.get("MAX_CONCURRENT_SCANS", "1")))
