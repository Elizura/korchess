# Backend Tests

This directory contains all test files for the En Passant backend.

## Test Files

| File | Purpose |
|------|---------|
| `test_connection_guardrails.py` | Tests database connection safety and guardrails |
| `test_game_insights_narration.py` | Tests Gemini AI narrative generation for game insights |
| `test_performance.py` | Performance benchmarks |
| `test_review_tags.py` | Tests move review tag classification |
| `test_single_game_insights.py` | Tests deterministic single-game insights logic |
| `test_tactical_detection.py` | Tests tactical pattern detection |

## Running Tests

### Run all tests
```bash
cd backend
python -m pytest tests/
```

### Run a specific test file
```bash
cd backend
python -m pytest tests/test_tactical_detection.py
```

### Run with verbose output
```bash
cd backend
python -m pytest tests/ -v
```

### Run with coverage
```bash
cd backend
python -m pytest tests/ --cov=. --cov-report=html
```

## Test Dependencies

Tests may require:
- `pytest` - Test framework
- Access to Stockfish engine at `/usr/games/stockfish`
- Database connection (for connection guardrail tests)
- API keys for Gemini (for narration tests)

## Notes

- Tests are independent and can be run in any order
- Some tests may be skipped if external dependencies (like Gemini API) are not configured
- Performance tests may take longer to run
