# Openingscope

Analyze your chess opening performance from Lichess games.

## Features

- Import your recent rated games from Lichess
- View opening statistics by ECO code
- Filter by color (White/Black) and time control (Blitz/Rapid/Classical)
- Automatic score calculation (wins + 0.5*draws) / games * 100

## Quick Start with Docker

### Run Both Frontend and Backend

```bash
docker compose up --build
```

Then open http://localhost:3000 in your browser.

### Run Backend Only

```bash
# Build the backend image
docker build -t openingscope-backend ./backend

# Run with data persistence
docker run -p 8000:8000 -v $(pwd)/data:/data openingscope-backend
```

Backend will be available at http://localhost:8000

### Run Frontend Only

```bash
# Build the frontend image (specify backend URL)
docker build -t openingscope-frontend \
  --build-arg NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 \
  ./frontend

# Run the frontend
docker run -p 3000:3000 openingscope-frontend
```

If backend is running in Docker and you're accessing frontend from host:
```bash
docker run -p 3000:3000 \
  -e NEXT_PUBLIC_API_BASE_URL=http://host.docker.internal:8000 \
  openingscope-frontend
```

Frontend will be available at http://localhost:3000

## API Endpoints

### Import Games

```bash
curl -X POST http://localhost:8000/api/import/lichess \
  -H "Content-Type: application/json" \
  -d '{"username": "DrNykterstein", "max_games": 100}'
```

Response:
```json
{
  "username": "DrNykterstein",
  "imported": 95,
  "skipped": 5
}
```

### Get Opening Statistics

```bash
# All games
curl "http://localhost:8000/api/openings/lichess/DrNykterstein"

# Filter by color and time control
curl "http://localhost:8000/api/openings/lichess/DrNykterstein?color=white&time_class=blitz"
```

Response:
```json
[
  {
    "eco": "B90",
    "opening_name": "Sicilian Defense: Najdorf Variation",
    "games": 42,
    "wins": 25,
    "draws": 8,
    "losses": 9,
    "score_pct": 69.0
  }
]
```

### Query Parameters

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| color | all, white, black | all | Filter by player color |
| time_class | all, blitz, rapid, classical | all | Filter by time control |

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Frontend  │────▶│   Backend   │────▶│  Lichess    │
│  (Next.js)  │     │  (FastAPI)  │     │    API      │
└─────────────┘     └──────┬──────┘     └─────────────┘
                          │
                    ┌─────▼─────┐
                    │  SQLite   │
                    │ Database  │
                    └───────────┘
```

## Tech Stack

**Backend:**
- Python 3.11
- FastAPI
- httpx (HTTP client)
- python-chess (PGN parsing)
- SQLite (data storage)

**Frontend:**
- Next.js 14 (App Router)
- TypeScript
- Tailwind CSS

## Data Persistence

When running with Docker Compose, game data is persisted in a Docker volume (`openingscope-data`).

When running backend standalone, mount a local directory:
```bash
docker run -p 8000:8000 -v $(pwd)/data:/data openingscope-backend
```

## Rate Limiting

The backend handles Lichess rate limiting automatically:
- If rate limited (HTTP 429), waits for the specified retry period
- Retries once after waiting
- Returns a clean error if still rate limited

## License

MIT
