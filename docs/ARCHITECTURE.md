# En Passant - Technical Architecture

This document explains the complete flow from user import through analysis, insights generation, and frontend display.

---

## Table of Contents

1. [High-Level Architecture](#high-level-architecture)
2. [Import Flow](#import-flow)
3. [Analysis Pipeline](#analysis-pipeline)
4. [Insights Generation](#insights-generation)
5. [Background Job System](#background-job-system)
6. [Database Schema](#database-schema)
7. [Frontend Data Flow](#frontend-data-flow)
8. [Configuration Reference](#configuration-reference)

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                   FRONTEND                                       │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐       │
│  │  Dashboard  │    │  Game View  │    │  Problems   │    │  Openings   │       │
│  │   Page      │    │    Page     │    │    Page     │    │    Page     │       │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘    └──────┬──────┘       │
└─────────┼──────────────────┼──────────────────┼──────────────────┼──────────────┘
          │                  │                  │                  │
          ▼                  ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              FASTAPI BACKEND                                     │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐       │
│  │   Import    │    │  Analysis   │    │  Insights   │    │  Openings   │       │
│  │   Router    │    │   Router    │    │   Router    │    │   Router    │       │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘    └──────┬──────┘       │
│         │                  │                  │                  │               │
│         ▼                  ▼                  ▼                  ▼               │
│  ┌──────────────────────────────────────────────────────────────────────┐       │
│  │                        BACKGROUND JOBS (asyncio)                      │       │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐               │       │
│  │  │   Quick     │    │   Full      │    │  Insights   │               │       │
│  │  │   Scan      │    │  Analysis   │    │  Pipeline   │               │       │
│  │  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘               │       │
│  └─────────┼──────────────────┼──────────────────┼──────────────────────┘       │
│            │                  │                  │                               │
│            ▼                  ▼                  ▼                               │
│  ┌──────────────────────────────────────────────────────────────────────┐       │
│  │                          STOCKFISH ENGINE                             │       │
│  │                      /usr/games/stockfish                             │       │
│  └──────────────────────────────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              POSTGRESQL DATABASE                                 │
│  ┌─────────┐  ┌──────────────┐  ┌───────────────┐  ┌─────────────────┐          │
│  │  games  │  │   analysis   │  │ full_analysis │  │ player_insights │          │
│  └─────────┘  └──────────────┘  └───────────────┘  └─────────────────┘          │
│  ┌─────────────────┐  ┌─────────────┐  ┌───────────────────┐                    │
│  │ game_quick_scans│  │  scan_jobs  │  │   insight_jobs    │                    │
│  └─────────────────┘  └─────────────┘  └───────────────────┘                    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Import Flow

When a user clicks "Import" on the dashboard, the following sequence occurs:

```
┌──────────┐     ┌─────────────┐     ┌─────────────────┐     ┌──────────────┐
│ Frontend │     │   FastAPI   │     │  Lichess/Chess  │     │   PostgreSQL │
│          │     │   Backend   │     │    .com API     │     │              │
└────┬─────┘     └──────┬──────┘     └────────┬────────┘     └──────┬───────┘
     │                  │                     │                     │
     │  POST /import    │                     │                     │
     │  /lichess        │                     │                     │
     │ ─────────────────>                     │                     │
     │                  │                     │                     │
     │                  │  GET /api/games/    │                     │
     │                  │  user/{username}    │                     │
     │                  │ ────────────────────>                     │
     │                  │                     │                     │
     │                  │    PGN response     │                     │
     │                  │ <────────────────────                     │
     │                  │                     │                     │
     │                  │                     │   upsert_game()     │
     │                  │                     │   for each game     │
     │                  │ ─────────────────────────────────────────>
     │                  │                     │                     │
     │                  │                     │   record_import_    │
     │                  │                     │   status()          │
     │                  │ ─────────────────────────────────────────>
     │                  │                     │                     │
     │                  │  ┌────────────────────────────────────┐   │
     │                  │  │  asyncio.create_task():            │   │
     │                  │  │  - schedule_insights_refresh()     │   │
     │                  │  │  - schedule_quick_scan()           │   │
     │                  │  └────────────────────────────────────┘   │
     │                  │                     │                     │
     │  ImportResponse  │                     │                     │
     │ <─────────────────                     │                     │
     │  {imported: N,   │                     │                     │
     │   is_sync: bool} │                     │                     │
```

### Import vs Sync

| Scenario | Condition | Behavior |
|----------|-----------|----------|
| **Initial Import** | No prior games for this user/site | Fetch up to `max_games` (default: **500**) from the beginning |
| **Sync** | Prior games exist AND `last_synced_at` is set | Only fetch games played after `last_synced_at` |

**Key files:**
- `backend/routers/import_.py` - Import endpoints
- `backend/lichess.py` - Lichess API client
- `backend/chesscom.py` - Chess.com API client

---

## Analysis Pipeline

There are **two types of analysis**, each with different purposes and engine settings:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            ANALYSIS TYPES COMPARISON                             │
├──────────────────┬───────────────────────────────┬──────────────────────────────┤
│                  │   QUICK SCAN                  │   FULL ANALYSIS              │
├──────────────────┼───────────────────────────────┼──────────────────────────────┤
│ Trigger          │ Auto after import             │ On-demand (API)              │
│ Scope            │ User moves only               │ Every ply                    │
│ Depth            │ 8                             │ 18 (configurable)            │
│ Time/position    │ 60ms                          │ 350ms+ (configurable)        │
│ Purpose          │ Find blunders                 │ Full review                  │
│ Output           │ Problems list                 │ Move-by-move evals           │
│ Storage          │ game_quick_scans              │ full_analysis                │
└──────────────────┴───────────────────────────────┴──────────────────────────────┘
```

### Quick Scan Flow (Automatic)

Triggered automatically after every import:

```
┌───────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────────┐
│   Import      │     │  Quick Scan  │     │  Stockfish  │     │   Database   │
│   Completes   │     │    Worker    │     │   Engine    │     │              │
└───────┬───────┘     └──────┬───────┘     └──────┬──────┘     └──────┬───────┘
        │                    │                    │                    │
        │  schedule_quick_   │                    │                    │
        │  scan()            │                    │                    │
        │───────────────────>│                    │                    │
        │                    │                    │                    │
        │                    │  create_scan_job() │                    │
        │                    │────────────────────────────────────────>│
        │                    │                    │                    │
        │                    │  For each game (up to 500):             │
        │                    │  ┌─────────────────────────────────────┐│
        │                    │  │  For each USER move:                ││
        │                    │  │  - Analyze position (depth 8)       ││
        │                    │  │  - Detect if cp_loss > 100          ││
        │                    │  │  - Tag tactical themes              ││
        │                    │  └─────────────────────────────────────┘│
        │                    │                    │                    │
        │                    │  upsert_game_      │                    │
        │                    │  quick_scan()      │                    │
        │                    │────────────────────────────────────────>│
        │                    │                    │                    │
        │                    │  _merge_and_       │                    │
        │                    │  update_insights() │                    │
        │                    │────────────────────────────────────────>│
```

**Quick Scan Settings:**
- `QUICK_SCAN_DEPTH`: 8 (engine search depth)
- `QUICK_SCAN_TIME_MS`: 60ms per position
- `QUICK_SCAN_CP_THRESHOLD`: 100 centipawns (moves losing more are flagged)
- `QUICK_SCAN_CONCURRENCY`: 2 games analyzed in parallel

### Full Analysis Flow (On-Demand)

Triggered when a user requests deep analysis on the game page:

```
┌──────────┐     ┌─────────────┐     ┌───────────────┐     ┌─────────────┐
│ Frontend │     │   FastAPI   │     │  Background   │     │  Stockfish  │
│          │     │   Backend   │     │    Task       │     │   Engine    │
└────┬─────┘     └──────┬──────┘     └───────┬───────┘     └──────┬──────┘
     │                  │                    │                    │
     │  POST /analysis  │                    │                    │
     │  /.../full       │                    │                    │
     │ ─────────────────>                    │                    │
     │                  │                    │                    │
     │                  │  create_analysis_  │                    │
     │                  │  job()             │                    │
     │                  │                    │                    │
     │  202 Accepted    │                    │                    │
     │  {job_id: ...}   │                    │                    │
     │ <─────────────────                    │                    │
     │                  │                    │                    │
     │                  │  asyncio.create_   │                    │
     │                  │  task(run_full_    │                    │
     │                  │  analysis)         │                    │
     │                  │ ──────────────────>│                    │
     │                  │                    │                    │
     │                  │                    │  For EVERY ply:    │
     │                  │                    │  - eval_before     │
     │                  │                    │  - best_move       │
     │                  │                    │  - eval_after      │
     │                  │                    │  - multi_pv (opt)  │
     │                  │                    │ ──────────────────>│
     │                  │                    │                    │
     │                  │                    │  save_full_        │
     │                  │                    │  analysis()        │
     │                  │                    │                    │
     │  Poll GET        │                    │                    │
     │  /analysis/...   │                    │                    │
     │ ─────────────────>                    │                    │
     │                  │                    │                    │
     │  {status:        │                    │                    │
     │   "completed",   │                    │                    │
     │   analysis: ...} │                    │                    │
     │ <─────────────────                    │                    │
```

**Full Analysis Output:**
- `moves[]`: Per-move evaluations with `eval_before`, `eval_after`, `cp_loss`, `best_move`, `pv`
- `summary`: Accuracy scores, opening name, player info
- `meta`: Engine settings, timing, position counts
- `review_tags`: Classifications like "brilliant", "blunder", "book", etc.

---

## Insights Generation

### Profile Insights Pipeline

Generates aggregated player insights across all games:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          INSIGHTS PIPELINE STAGES                                │
└─────────────────────────────────────────────────────────────────────────────────┘

   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
   │   LOAD      │     │   EXTRACT   │     │  AGGREGATE  │     │  NARRATIVE  │
   │   GAMES     │────>│   FEATURES  │────>│   STATS     │────>│  GENERATION │
   └─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
         │                   │                   │                   │
         ▼                   ▼                   ▼                   ▼
   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
   │ Up to 500   │     │ Per-game:   │     │ Across all: │     │ LLM or      │
   │ recent      │     │ - Phases    │     │ - Style     │     │ fallback    │
   │ games       │     │ - Clocks    │     │ - Openings  │     │ template    │
   │             │     │ - Style     │     │ - Strengths │     │             │
   │             │     │   signals   │     │ - Weaknesses│     │             │
   └─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

**Feature Extraction (Light):**
- `phase_profile`: Opening end ply, endgame start, total plies
- `style_signals`: Early capture/check rates, aggression metrics
- `time_pressure`: Clock samples, low-time game detection

**Aggregated Features:**
- Style label (e.g., "Solid Tactical", "Aggressive Positional")
- Opening rankings (best/worst by win rate)
- Performance by time control and color
- Strengths and weaknesses
- Recurring themes/mistakes

### Single-Game AI Insights

For individual game analysis with Gemini narration:

```
┌──────────┐     ┌─────────────┐     ┌───────────────────┐     ┌─────────────┐
│ Frontend │     │  Analysis   │     │  Single Game      │     │   Gemini    │
│          │     │   Router    │     │  Insights Logic   │     │     API     │
└────┬─────┘     └──────┬──────┘     └────────┬──────────┘     └──────┬──────┘
     │                  │                     │                       │
     │  POST            │                     │                       │
     │  /ai-insights    │                     │                       │
     │ ─────────────────>                     │                       │
     │                  │                     │                       │
     │                  │  compute_single_    │                       │
     │                  │  game_insights()    │                       │
     │                  │ ───────────────────>│                       │
     │                  │                     │                       │
     │                  │  Deterministic:     │                       │
     │                  │  - result_cause     │                       │
     │                  │  - turning_points   │                       │
     │                  │  - phase_grades     │                       │
     │                  │  - game_character   │                       │
     │                  │ <───────────────────│                       │
     │                  │                     │                       │
     │                  │  ensure_narration() │                       │
     │                  │ ───────────────────────────────────────────>│
     │                  │                     │                       │
     │                  │  Natural language   │                       │
     │                  │  summary            │                       │
     │                  │ <───────────────────────────────────────────│
     │                  │                     │                       │
     │  AI Insights     │                     │                       │
     │  Response        │                     │                       │
     │ <─────────────────                     │                       │
```

---

## Background Job System

Jobs run via **asyncio tasks** on the FastAPI process (no separate worker):

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              JOB MANAGEMENT                                      │
└─────────────────────────────────────────────────────────────────────────────────┘

                    ┌──────────────────────────────────────┐
                    │         FastAPI Process              │
                    │                                      │
                    │  ┌────────────────────────────────┐  │
                    │  │      asyncio Event Loop        │  │
                    │  │                                │  │
                    │  │  ┌─────────────────────────┐   │  │
                    │  │  │  _INSIGHTS_SEMAPHORE    │   │  │
                    │  │  │  (max: 1 concurrent)    │   │  │
                    │  │  └─────────────────────────┘   │  │
                    │  │                                │  │
                    │  │  ┌─────────────────────────┐   │  │
                    │  │  │  Quick Scan Semaphore   │   │  │
                    │  │  │  (2 games in parallel)  │   │  │
                    │  │  └─────────────────────────┘   │  │
                    │  │                                │  │
                    │  └────────────────────────────────┘  │
                    │                                      │
                    └──────────────────────────────────────┘
```

### Concurrency Limits

| Job Type | Concurrency | Environment Variable |
|----------|-------------|---------------------|
| Insights Pipeline | 1 per process | `MAX_CONCURRENT_INSIGHTS` |
| Quick Scan (batch) | 1 per user | One active scan per user |
| Quick Scan (games) | 2 parallel | `QUICK_SCAN_CONCURRENCY` |
| Full Analysis | 1 per game | One job per game at a time |

### Job Lifecycle

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌───────────┐
│  queued  │────>│  running │────>│completed │     │  failed   │
└──────────┘     └──────────┘     └──────────┘     └───────────┘
                       │                                 ▲
                       └─────────────────────────────────┘
                              (on error)
```

---

## Database Schema

### Table Reference

#### Core User & Game Data

| Table | Purpose | When Used |
|-------|---------|-----------|
| **`users`** | Stores user accounts (both registered and public/anonymous users) | Created on first import or OAuth sign-in. Public users are auto-created with `pub_` prefix for anonymous imports. |
| **`games`** | Stores all imported chess games with PGN and metadata | Populated during import from Lichess/Chess.com. Queried when viewing games, openings, or generating insights. |
| **`imports`** | Tracks import history and sync status per user/site | Updated after each import. Used to determine if next import is "sync" (incremental) vs full import. |

#### Analysis Storage

| Table | Purpose | When Used |
|-------|---------|-----------|
| **`full_analysis`** | Caches complete move-by-move Stockfish analysis results | Written when full analysis completes. Read when user views a previously analyzed game. |
| **`analysis_jobs`** | Tracks in-flight full analysis jobs | Created when analysis starts, deleted when complete. Used for polling status and preventing duplicate jobs. |
| **`full_analysis_requests`** | Audit log of analysis requests (for quotas) | Written on each analysis request. Can be used to implement rate limiting. |

#### Quick Scan (Problem Spotter)

| Table | Purpose | When Used |
|-------|---------|-----------|
| **`game_quick_scans`** | Per-game tactical problem detection results | Written by quick scan batch job. Stores blunders/mistakes with tactical themes per game. |
| **`scan_jobs`** | Tracks batch quick scan job progress | Created after import, updated during scan, used for progress bar in UI. |

#### Player Insights

| Table | Purpose | When Used |
|-------|---------|-----------|
| **`player_insights`** | Aggregated player profile: style, strengths, weaknesses, openings | Updated after insights pipeline runs. Main source for dashboard coaching summary. |
| **`insight_jobs`** | Tracks insights generation job status | Created after import, tracks stages (loading, extracting, aggregating, narrative). |
| **`insight_game_features`** | Per-game extracted features for aggregation | Written during insights pipeline. Stores light features (phases, style signals, clocks) per game. |

#### AI Insights (Gemini)

| Table | Purpose | When Used |
|-------|---------|-----------|
| **`ai_game_insights`** | Caches Gemini-generated single-game narratives | Written when AI insights successfully generated. Read when user views game with AI summary. |
| **`ai_insights_requests`** | Audit log of AI insight requests with status | Written on each request. Used to count daily Gemini usage for quotas. |

#### Opening Encyclopedia

| Table | Purpose | When Used |
|-------|---------|-----------|
| **`openings`** | Master list of chess openings (ECO codes, names, PGN) | Loaded at startup. Used to match games to openings during import. |
| **`opening_moves`** | Move sequences for each opening (ply-by-ply UCI moves) | Used by opening matcher to find best matching opening for each game. |

#### User Consent

| Table | Purpose | When Used |
|-------|---------|-----------|
| **`lesson_consent_events`** | Records user consent decisions for email lessons | Written when user opts in/out. Tracks consent history with source context. |

### Schema Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                               DATABASE SCHEMA                                    │
└─────────────────────────────────────────────────────────────────────────────────┘

 USERS & GAMES                              IMPORT TRACKING
┌─────────────────────┐                    ┌─────────────────────┐
│       users         │                    │      imports        │
├─────────────────────┤                    ├─────────────────────┤
│ id (PK)             │◄───────────────┐   │ user_id (FK)        │
│ email               │                │   │ username            │
│ name                │                │   │ site                │
│ username            │                │   │ imported            │
│ created_at          │                │   │ skipped             │
│ updated_at          │                │   │ max_games           │
└─────────────────────┘                │   │ imported_at         │
         │                             │   │ last_synced_at      │
         │ 1:N                         │   └─────────────────────┘
         ▼                             │
┌─────────────────────┐                │    FULL ANALYSIS
│       games         │                │   ┌─────────────────────┐
├─────────────────────┤                │   │   full_analysis     │
│ id (PK)             │                │   ├─────────────────────┤
│ user_id (FK)        │────────────────┤   │ id (PK)             │
│ site                │                │   │ user_id (FK)        │
│ site_game_id        │                │   │ site                │
│ username            │                │   │ site_game_id        │
│ played_at           │                │   │ depth               │
│ time_class          │                │   │ multipv             │
│ color               │                │   │ moves_json          │
│ result              │                │   │ summary_json        │
│ eco                 │                │   │ meta_json           │
│ opening_name        │                │   │ insights_json       │
│ opening_id (FK)     │                │   │ created_at          │
│ opponent            │                │   └─────────────────────┘
│ white_elo           │                │
│ black_elo           │                │   ┌─────────────────────┐
│ pgn                 │                │   │   analysis_jobs     │
└─────────────────────┘                │   ├─────────────────────┤
                                       │   │ id (PK)             │
 QUICK SCAN                            │   │ user_id (FK)        │
┌─────────────────────┐                │   │ site                │
│  game_quick_scans   │                │   │ site_game_id        │
├─────────────────────┤                │   │ depth, multipv      │
│ id (PK)             │                │   │ created_at          │
│ user_id (FK)        │────────────────┤   └─────────────────────┘
│ site                │                │
│ site_game_id        │                │    PLAYER INSIGHTS
│ username            │                │   ┌─────────────────────┐
│ problems_json       │                │   │   player_insights   │
│ summary_json        │                │   ├─────────────────────┤
│ scanned_at          │                │   │ id (PK)             │
└─────────────────────┘                │   │ user_id (FK)        │
                                       │   │ username            │
┌─────────────────────┐                │   │ site                │
│     scan_jobs       │                │   │ status              │
├─────────────────────┤                │   │ feature_version     │
│ id (PK)             │                │   │ features_json       │
│ user_id (FK)        │────────────────┤   │ narrative_json      │
│ username            │                │   │ updated_at          │
│ site                │                │   └─────────────────────┘
│ status              │                │
│ total_games         │                │   ┌─────────────────────┐
│ games_done          │                │   │    insight_jobs     │
│ created_at          │                │   ├─────────────────────┤
│ updated_at          │                │   │ id (PK)             │
└─────────────────────┘                │   │ user_id (FK)        │
                                       │   │ username            │
 AI INSIGHTS                           │   │ site                │
┌─────────────────────┐                │   │ status              │
│  ai_game_insights   │                │   │ stage               │
├─────────────────────┤                │   │ reason              │
│ id (PK)             │                │   │ created_at          │
│ user_id (FK)        │────────────────┤   │ updated_at          │
│ site                │                │   └─────────────────────┘
│ site_game_id        │                │
│ depth, multipv      │                │    OPENINGS ENCYCLOPEDIA
│ insights_json       │                │   ┌─────────────────────┐
│ source              │                │   │      openings       │
│ created_at          │                │   ├─────────────────────┤
└─────────────────────┘                │   │ id (PK)             │
                                       │   │ eco                 │
┌─────────────────────┐                │   │ name                │
│ ai_insights_requests│                │   │ pgn                 │
├─────────────────────┤                │   │ ply_count           │
│ id (PK)             │                │   │ opening_key         │
│ user_id (FK)        │────────────────┘   │ opening_label       │
│ site                │                    │ variation_key       │
│ site_game_id        │                    │ variation_label     │
│ status              │                    └──────────┬──────────┘
│ requested_at        │                               │ 1:N
└─────────────────────┘                               ▼
                                       ┌─────────────────────┐
                                       │   opening_moves     │
                                       ├─────────────────────┤
                                       │ opening_id (FK)     │
                                       │ ply_index           │
                                       │ uci                 │
                                       └─────────────────────┘
```

### JSON Column Contents

#### `games` - No JSON columns
Standard relational columns for game metadata.

#### `full_analysis.moves_json`
```json
[
  {
    "ply": 1,
    "san": "e4",
    "uci": "e2e4",
    "fen_before": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "fen_after": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "eval_before": {"cp": 20},
    "eval_after": {"cp": 15},
    "best_move_uci": "e2e4",
    "best_move_san": "e4",
    "cp_loss": 0,
    "classification": "best",
    "review_tag": "book",
    "tactical": {"type": "fork", "square": "c7"}
  }
]
```

#### `full_analysis.summary_json`
```json
{
  "accuracy_white": 87,
  "accuracy_black": 72,
  "opening_name": "Sicilian Defense",
  "opening_eval": {"cp": 25},
  "total_moves": 45
}
```

#### `game_quick_scans.problems_json`
```json
[
  {
    "ply": 24,
    "san": "Nxd4",
    "classification": "blunder",
    "cp_loss": 320,
    "phase": "middlegame",
    "tactic_types": ["hanging_piece", "fork"]
  }
]
```

#### `player_insights.features_json`
```json
{
  "style": {
    "label": "Solid Tactical",
    "scores": {"tactical": 0.7, "positional": 0.5, "aggressive": 0.4, "solid": 0.8}
  },
  "openings": {
    "best": [{"name": "Italian Game", "score_pct": 68, "games": 45}],
    "weak": [{"name": "Sicilian Defense", "score_pct": 38, "games": 32}]
  },
  "strengths": ["Endgame technique", "Time management"],
  "weaknesses": ["Tactical oversights in middlegame"],
  "time_pressure": {
    "games_under_pressure": 12,
    "score_under_pressure": 42
  }
}
```

---

## Frontend Data Flow

### Dashboard Page

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           DASHBOARD DATA SOURCES                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

  ┌──────────────────┐
  │   Dashboard UI   │
  │                  │
  │ ┌──────────────┐ │     GET /api/v1/insights/profile
  │ │  Coaching    │ │◄────────────────────────────────────────────────────────┐
  │ │  Summary     │ │                                                         │
  │ └──────────────┘ │     Response:                                           │
  │                  │     {                                                    │
  │ ┌──────────────┐ │       features: {                                       │
  │ │  Problem     │ │         style: { label, scores },                       │
  │ │  Spotter     │ │         openings: { best, weak },                       │
  │ └──────────────┘ │         strengths: [...],                               │
  │                  │         weaknesses: [...],                              │
  │ ┌──────────────┐ │         time_pressure: {...}                            │
  │ │  Opening     │ │       },                                                │
  │ │  Stats       │ │       problem_spotter: {                                │
  │ └──────────────┘ │         total_problems,                                 │
  │                  │         by_theme: [...]                                 │
  └──────────────────┘       },                                                │
                             scan_progress: { status, done, total }            │
                           }                                                   │
                                                                               │
                                                                               │
  ┌──────────────────┐                                                         │
  │   Polling Logic  │                                                         │
  │                  │                                                         │
  │  if lifecycle_   │     Re-fetch every 3s while:                            │
  │  status in       │     - "queued"                                          │
  │  ACTIVE_STATUSES │     - "baseline_ready"                                  │
  │  OR scan is      │     - scan status "queued" or "running"                 │
  │  running         │                                                         │
  └──────────────────┘─────────────────────────────────────────────────────────┘
```

### Game Page

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                             GAME PAGE DATA SOURCES                               │
└─────────────────────────────────────────────────────────────────────────────────┘

  ┌──────────────────┐
  │    Game View     │
  │                  │
  │ ┌──────────────┐ │     GET /api/v1/games/{site}/{username}/{gameId}
  │ │  PGN Viewer  │ │◄───────────────────────────────────────────────────────┐
  │ │              │ │     { pgn, played_at, result, opponent, ... }          │
  │ └──────────────┘ │                                                        │
  │                  │                                                        │
  │ ┌──────────────┐ │     GET /api/v1/analysis/{site}/{username}/{gameId}/full
  │ │  Eval Graph  │ │◄───────────────────────────────────────────────────────┐
  │ │  Move List   │ │     { moves: [...], summary, meta }                    │
  │ └──────────────┘ │                                                        │
  │                  │                                                        │
  │ ┌──────────────┐ │     GET /api/v1/analysis/{...}/ai-insights
  │ │  AI Summary  │ │◄───────────────────────────────────────────────────────┐
  │ │  (Gemini)    │ │     { narration: { ... }, cards: { ... } }             │
  │ └──────────────┘ │                                                        │
  └──────────────────┘                                                        │
```

---

## Configuration Reference

### Import Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `max_games` (schema) | 500 | Games fetched per import |
| Max allowed | 10,000 | Hard limit in schema |

### Quick Scan Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `QUICK_SCAN_DEPTH` | 8 | Stockfish search depth |
| `QUICK_SCAN_TIME_MS` | 60 | Time per position (ms) |
| `QUICK_SCAN_CONCURRENCY` | 2 | Parallel games per batch |
| `QUICK_SCAN_CP_THRESHOLD` | 100 | CP loss to flag as problem |
| `QUICK_SCAN_MAX_GAMES` | 500 | Max games per scan batch |
| `MAX_CONCURRENT_SCANS` | 1 | Max concurrent scan batches |

### Full Analysis Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_DEPTH` | 18 | Stockfish search depth |
| `FULL_ANALYSIS_TIME_MS` | 350 | Time per position (ms) |
| `FULL_ANALYSIS_POSITION_WORKERS` | 1 | Parallel position workers |
| `FULL_ANALYSIS_ENGINE_THREADS` | 2 | Stockfish threads |
| `FULL_ANALYSIS_ENGINE_HASH_MB` | 128 | Stockfish hash table |

### Insights Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_GAMES_WINDOW` | 500 | Games for profile analysis |
| `MAX_CONCURRENT_INSIGHTS` | 1 | Concurrent insight pipelines |
| `MIN_BASELINE_GAMES` | 12 | Min games for insights |
| `LOW_TIME_RATIO` | 0.1 | Ratio for time pressure |
| `NARRATIVE_PROVIDER` | none | "none", "openai", etc. |

### Stockfish Engine

| Setting | Value |
|---------|-------|
| Path | `/usr/games/stockfish` |
| Move classifications | Based on CP loss: best (0), excellent (<10), good (<30), inaccuracy (<100), mistake (<300), blunder (≥300) |

---

## API Endpoints Reference

### Import

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/import/lichess` | Import from Lichess |
| POST | `/api/v1/import/chesscom` | Import from Chess.com |
| GET | `/api/v1/import/status` | Get import status |

### Analysis

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/analysis/{site}/{username}/{gameId}/full` | Start full analysis |
| GET | `/api/v1/analysis/{site}/{username}/{gameId}/full` | Get full analysis |
| POST | `/api/v1/analysis/{site}/{username}/{gameId}/ai-insights` | Request AI insights |
| GET | `/api/v1/analysis/{site}/{username}/{gameId}/ai-insights` | Get AI insights |

### Insights

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/insights/profile` | Get player profile insights |
| POST | `/api/v1/insights/profile` | Refresh player insights |
| GET | `/api/v1/insights/problems-by-theme` | Get problems by tactical theme |

### Games

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/games/{site}/{username}/{gameId}` | Get game details |
| GET | `/api/v1/openings/{site}/{username}` | Get opening statistics |
