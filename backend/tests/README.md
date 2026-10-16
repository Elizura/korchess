# Backend Tests

This directory contains all test files for the Korchess backend.

## Test Files

| File | Purpose |
|------|---------|
| `test_auth_email_codes.py` | Email sign-up recovery: re-signup, resend, password reset, code attempt limits |
| `test_connection_guardrails.py` | Tests database connection safety and guardrails |
| `test_game_insights_narration.py` | Tests the Gemini narration jargon guard and cache freshness |
| `test_import_nonblocking.py` | A slow upstream import must not stall other requests |
| `test_job_scheduling.py` | Manual refreshes must enqueue their Celery tasks |
| `test_review_tags.py` | Tests move review tag classification |
| `test_single_game_insights.py` | Tests deterministic single-game insights logic |
| `test_tactical_detection.py` | Tests tactical pattern detection |
| `test_performance.py` | Opening-match benchmark (needs a populated database; not a pytest test) |
| `test_quick_scan_performance.py` | Stockfish quick-scan benchmark script (see below) |
| `dump_db.py` | Fills the `raw_games` table used by the quick-scan benchmark |

## Running Tests

The unit tests need no database, Redis or API keys, but the app modules
read `DATABASE_URL`, `REDIS_URL` and `JWT_SECRET` at import time, so set
dummy values. Install `pytest` alongside `requirements.txt`.

### Run all tests
```bash
cd backend
export DATABASE_URL=postgresql://test@localhost:1/test
export REDIS_URL=redis://localhost:1/0
export JWT_SECRET=test
python -m pytest tests/ --ignore=tests/test_performance.py
```

### Run a specific test file
```bash
cd backend
python -m pytest tests/test_tactical_detection.py
```

### Run with verbose output
```bash
cd backend
python -m pytest tests/ -v --ignore=tests/test_performance.py
```

### Run with coverage (needs `pytest-cov`)
```bash
cd backend
python -m pytest tests/ --ignore=tests/test_performance.py --cov=. --cov-report=html
```

## Benchmarks

`dump_db.py` and `test_quick_scan_performance.py` connect to the dev database
at `db:5432` and use Stockfish at `/usr/games/stockfish`, so run them inside
the dev backend container (`make shell-backend`):

```bash
python tests/dump_db.py elizura   # Lichess user whose games to fetch
python tests/test_quick_scan_performance.py
```

The benchmark's username, game count and mode are set in its `__main__` block.

## Notes

- Tests are independent and can be run in any order
- Performance benchmarks may take a long time to run
