# Korchess

Chess game analysis for Lichess and Chess.com players, live at
[korchess.com](https://korchess.com).

## Features

- **Game import** from Lichess and Chess.com, with saved chess profiles that
  can be re-synced later.
- **Opening stats**: score by opening and variation, filterable by color and
  time control (blitz, rapid, classical).
- **Quick scan**: every imported game gets a fast Stockfish pass that finds
  blunders and mistakes, collected as tactical problems to review by theme.
- **Deep analysis**: on-demand, move-by-move Stockfish analysis of a single
  game.
- **AI game insights**: Gemini-written commentary on a deep-analyzed game
  (limited per account per day, `AI_INSIGHTS_DAILY_LIMIT`, default 2).
- **Accounts**: email/password sign-up with an emailed verification code,
  password reset by email, and Google sign-in.

## Architecture

```
browser ──> nginx (80/443, Let's Encrypt certificate renewed by certbot)
              ├── /api/v1/*        ──> backend (FastAPI)
              └── everything else  ──> frontend (Next.js 14)

backend ───────> Postgres, Redis, Stockfish, LMDB eval cache (optional)
backend ──Celery tasks via Redis──> celery-worker ──> Postgres, Stockfish
```

| Service | What it does |
|---------|--------------|
| `frontend` | Next.js 14 (App Router, TypeScript, Tailwind). Port 3000 in prod, 3005 in dev. |
| `backend` | FastAPI app (`backend/main.py`), port 8000. Creates the database schema on startup. |
| `celery-worker` | Same image as the backend. Processes imported games, quick scans and insight jobs from Redis. |
| Postgres | Users, games, analyses, insights. |
| Redis | Celery broker and import progress counters. |
| Stockfish | Installed in the backend image at `/usr/games/stockfish`. |
| `nginx` | TLS termination, `www` to apex redirect, per-IP rate limits, routing (`deploy/nginx/`). Prod only. |
| `certbot` | Renews the Let's Encrypt certificate. Prod only. |
| LMDB eval cache | Optional local copy of the Lichess evaluation database; the quick scan skips Stockfish for positions found in it. |

External services: Lichess and Chess.com APIs (game import), Gemini (AI
insights), Resend (verification and reset emails), Google OAuth, and PostHog
(optional analytics).

Compose files: `docker-compose.yml` is the base; `docker-compose.dev.yml` adds
Postgres, Redis, Flower and live reload; `docker-compose.prod.yml` adds nginx
and certbot. The Makefile wraps both combinations.

## Configuration

Copy `.env.example` to `.env` and fill it in; every variable is described
there. Required:

| Variable | Notes |
|----------|-------|
| `DATABASE_URL` | Postgres connection string. |
| `REDIS_URL` | Redis URL for Celery and import progress. |
| `JWT_SECRET` | Long random string used to sign access tokens. |
| `NEXT_PUBLIC_API_BASE_URL` | Public URL of the site, for example `https://korchess.com`. Baked into the frontend at build time. |

Needed for the matching features: `GOOGLE_CLIENT_ID` and
`NEXT_PUBLIC_GOOGLE_CLIENT_ID` (Google sign-in), `RESEND_API_KEY` and
`FROM_EMAIL` (email sign-up and password reset), `GEMINI_API_KEY` (AI
insights), `LICHESS_EVAL_DB_HOST_PATH` (LMDB eval cache).

## Local development

```bash
cp .env.example .env
# In .env set:
#   DATABASE_URL=postgresql://postgres:postgres@db:5432/korchess
#   REDIS_URL=redis://redis:6379/0
#   JWT_SECRET=<anything>
make up
```

Open http://localhost:3005. The dev stack runs Postgres (host port 5432),
Redis (host port 6390), the backend with `--reload` (http://localhost:8000),
the Celery worker, Flower (http://localhost:5555) and the Next.js dev server
with hot reload. Code under `backend/` and `frontend/` is mounted, so most edits
need no rebuild.

Seed the opening names once per database (required for opening stats):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python repository/import_openings.py
```

Useful targets:

| Command | Does |
|---------|------|
| `make up` / `make down` | Build and start, or stop and remove, the dev stack |
| `make logs`, `make logs-backend`, `make logs-celery` | Follow logs (also `-frontend`, `-db`, `-redis`) |
| `make restart-backend` | Restart one service (also `-frontend`, `-celery`, `-db`, `-redis`) |
| `make up-backend`, `make up-frontend` | Rebuild and restart one service |
| `make shell-backend`, `make shell-frontend` | Shell inside a container |
| `make ps` | Container status |
| `make db-clean` | Drop all dev tables except the opening data (destructive) |
| `make clean` | Stop and delete the dev volumes, including the database (destructive) |

## Production deploy

The prod stack (`make up-prod`) runs backend, celery-worker, frontend, nginx
and certbot, all with `restart: unless-stopped`. It does **not** run Postgres
or Redis: `DATABASE_URL` and `REDIS_URL` in `.env` must point to instances the
containers can reach (the `redis` and `db` hostnames from the dev setup do not
exist in prod). The compose file sets `ENV=production` for backend and
celery-worker, which makes the refresh cookie `Secure` and limits CORS to the
production origins.

1. Point DNS for `korchess.com` and `www.korchess.com` at the server. The
   domain is hard-coded in `deploy/nginx/conf.d/korchess.conf`.
2. Create `.env` with the required variables above and
   `NEXT_PUBLIC_API_BASE_URL=https://korchess.com`.
3. Issue the first certificate (only once, while nothing listens on port 80):

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm -p 80:80 \
     --entrypoint certbot certbot certonly --standalone \
     -d korchess.com -d www.korchess.com \
     --email you@example.com --agree-tos --no-eff-email
   ```

   Certificates are stored in `deploy/certbot/conf/`.
4. Start the stack: `make up-prod`. Seed the opening names once with
   `docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend python repository/import_openings.py`.

Renewal is automatic: the `certbot` service runs
`certbot renew --webroot -w /var/www/certbot` every 12 hours (nginx serves the
challenge from `/.well-known/acme-challenge/`), and nginx reloads itself every
6 hours to pick up the new files. Check it with
`docker compose -f docker-compose.yml -f docker-compose.prod.yml logs certbot`.

Redeploy after pulling changes with `make up-prod` (it rebuilds the images).
`NEXT_PUBLIC_*` values are compiled into the frontend, so changing them also
needs `make up-prod`. Other targets: `make logs-prod`, `make down-prod`,
`make build-prod`.

nginx limits each client IP to 10 requests/minute (burst 20) on
`/api/v1/auth/` and 10 requests/second (burst 50) on the rest of `/api/v1/`,
returning 429 beyond that.

## Tests

Backend tests use pytest and do not need a running database or Redis, but the
app modules read `DATABASE_URL`, `REDIS_URL` and `JWT_SECRET` at import time,
so set dummy values:

```bash
cd backend
pip install -r requirements.txt pytest   # in a virtualenv
DATABASE_URL=postgresql://test@localhost:1/test \
REDIS_URL=redis://localhost:1/0 \
JWT_SECRET=test \
python -m pytest tests/ --ignore=tests/test_performance.py
```

`test_performance.py` and `test_quick_scan_performance.py` are benchmarks that
need a populated database (and Stockfish); see `backend/tests/README.md`.

## More documentation

- `docs/ARCHITECTURE.md`: import, analysis and insights pipelines in detail
- `docs/EVENT_TRACKING.md`: analytics events
- `FULL_ANALYSIS_EXPLAINED.md`: how deep analysis works

## License

MIT
