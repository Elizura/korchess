"""Game analysis endpoints (full analysis and AI insights)."""

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from analytics import hash_username, track_server_event
from db import (
    count_user_ai_gemini_success_utc_day,
    create_analysis_job,
    delete_analysis_job,
    ensure_public_user_for_username,
    get_ai_game_insights,
    get_analysis_job,
    get_connection,
    get_full_analysis,
    get_game_by_id,
    get_public_user_id_for_username,
    log_ai_insights_request,
    save_ai_game_insights,
    save_full_analysis,
    save_full_analysis_insights,
)
from full_analysis import run_full_analysis
from game_insights_narration import ensure_narration, is_current_clean_narration_payload
from single_game_insights import compute_single_game_insights

from schemas import (
    AIInsightsResponse,
    FullAnalysisResponse,
    SingleGameInsightsResponse,
)
from dependencies import get_db, validate_site
from auth import get_optional_user, get_registered_user

router = APIRouter(tags=["analysis"])

MAX_CONCURRENT_ANALYSES = 2
active_analysis_count = 0
active_analysis_lock = asyncio.Lock()
FULL_ANALYSIS_TIME_MS = max(200, int(os.environ.get("FULL_ANALYSIS_TIME_MS", "350")))
AI_INSIGHTS_DAILY_LIMIT = max(0, int(os.environ.get("AI_INSIGHTS_DAILY_LIMIT", "2")))
AI_INSIGHTS_UNLIMITED_EMAILS = {
    entry.strip().lower()
    for env_key in ("AI_INSIGHTS_UNLIMITED_EMAILS", "DEEP_ANALYSIS_UNLIMITED_EMAILS")
    for entry in os.environ.get(env_key, "").split(",")
    if entry.strip()
}


def _current_utc_day_bounds() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return day_start, day_end


def _is_unlimited_ai_email(email: str | None) -> bool:
    if not email:
        return False
    return email.strip().lower() in AI_INSIGHTS_UNLIMITED_EMAILS


def _build_full_analysis_payload(cached: dict) -> dict:
    return {
        "moves": json.loads(cached["moves_json"]),
        "summary": json.loads(cached["summary_json"]),
        "meta": json.loads(cached["meta_json"]),
    }


def _load_cached_insights(cached: dict) -> dict | None:
    raw = cached.get("insights_json")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _load_ai_cached_insights(cached: dict) -> dict | None:
    raw = cached.get("insights_json")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_ai_cached_payload_current_and_clean(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    return is_current_clean_narration_payload(payload)


def _resolve_public_user_id(conn: psycopg.Connection, username: str) -> str:
    return get_public_user_id_for_username(conn, username)


def _analysis_owner_candidates(
    conn: psycopg.Connection,
    current_user: dict | None,
    username: str,
) -> list[str]:
    public_user_id = _resolve_public_user_id(conn, username)
    candidates = [public_user_id]
    signed_user_id = current_user["id"] if current_user else None
    if signed_user_id and signed_user_id != public_user_id:
        candidates.append(signed_user_id)
    return candidates


def _get_full_analysis_with_fallback(
    conn: psycopg.Connection,
    current_user: dict | None,
    username: str,
    game_id: str,
    depth: int,
    multipv: int,
    site: str,
) -> tuple[dict | None, str]:
    candidates = _analysis_owner_candidates(conn, current_user, username)
    for owner_id in candidates:
        cached = get_full_analysis(conn, owner_id, username, game_id, depth, multipv, site)
        if cached:
            return cached, owner_id
    return None, candidates[0]


def _get_analysis_job_with_fallback(
    conn: psycopg.Connection,
    current_user: dict | None,
    username: str,
    game_id: str,
    depth: int,
    multipv: int,
    site: str,
) -> tuple[dict | None, str]:
    candidates = _analysis_owner_candidates(conn, current_user, username)
    for owner_id in candidates:
        job = get_analysis_job(conn, owner_id, username, game_id, depth, multipv, site)
        if job:
            return job, owner_id
    return None, candidates[0]


def _get_game_for_deep_analysis(
    conn: psycopg.Connection,
    signed_user_id: str | None,
    username: str,
    game_id: str,
    site: str,
) -> tuple[dict | None, str]:
    public_user_id = _resolve_public_user_id(conn, username)
    public_game = get_game_by_id(conn, public_user_id, username, game_id, site)
    if public_game:
        return public_game, public_user_id

    if signed_user_id:
        private_game = get_game_by_id(conn, signed_user_id, username, game_id, site)
        if private_game:
            return private_game, signed_user_id
    return None, public_user_id


async def run_analysis_background(
    job_id: str,
    user_id: str,
    username: str,
    game_id: str,
    pgn: str,
    opening_ply_count: int | None,
    depth: int,
    multipv: int,
    site: str,
    analytics_user_id: str | None = None,
    analytics_anonymous_id: str | None = None,
    analytics_session_id: str | None = None,
    username_hash: str | None = None,
):
    """Background task to run Stockfish analysis."""
    global active_analysis_count

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: run_full_analysis(
                pgn,
                depth,
                multipv,
                FULL_ANALYSIS_TIME_MS,
                opening_ply_count=opening_ply_count,
            )
        )
        full_analysis = {
            "moves": result["moves"],
            "summary": result["summary"],
            "meta": result["meta"],
        }

        conn = get_connection()
        try:
            save_full_analysis(
                conn, user_id, username, game_id,
                depth=depth,
                multipv=multipv,
                moves_json=json.dumps(full_analysis["moves"]),
                summary_json=json.dumps(full_analysis["summary"]),
                meta_json=json.dumps(full_analysis["meta"]),
                insights_json=None,
                site=site
            )
            await track_server_event(
                conn,
                event_name="analysis.deep.completed",
                user_id=analytics_user_id,
                request=None,
                anonymous_id=analytics_anonymous_id,
                session_id=analytics_session_id,
                properties={
                    "job_id": job_id,
                    "site": site,
                    "depth": depth,
                    "multipv": multipv,
                    "username": username_hash,
                    "total_time_ms": full_analysis["meta"].get("total_time_ms"),
                    "positions_analyzed": full_analysis["meta"].get("positions_analyzed"),
                    "time_per_position_ms": full_analysis["meta"].get("time_per_position_ms"),
                },
            )
            delete_analysis_job(conn, job_id)
            conn.commit()
            print(f"[Analysis] Completed for game {game_id} on {site}")
        finally:
            conn.close()

    except Exception as e:
        print(f"[Analysis] Failed for game {game_id} on {site}: {e}")
        conn = get_connection()
        try:
            await track_server_event(
                conn,
                event_name="analysis.deep.failed",
                user_id=analytics_user_id,
                request=None,
                anonymous_id=analytics_anonymous_id,
                session_id=analytics_session_id,
                properties={
                    "job_id": job_id,
                    "site": site,
                    "depth": depth,
                    "multipv": multipv,
                    "username": username_hash,
                    "reason": str(e),
                },
            )
            delete_analysis_job(conn, job_id)
            conn.commit()
        finally:
            conn.close()

    finally:
        async with active_analysis_lock:
            active_analysis_count -= 1
            print(f"[Analysis] Active count now: {active_analysis_count}")


@router.get(
    "/{site}/{username}/{game_id}/single-insights",
    response_model=SingleGameInsightsResponse,
)
async def get_single_game_insights_endpoint(
    site: str,
    username: str,
    game_id: str,
    depth: int = Query(default=18, ge=1, le=30),
    multipv: int = Query(default=1, ge=1, le=5),
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
    """Get deterministic single-game rule insights derived from cached full analysis."""
    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    cached, cached_owner_id = _get_full_analysis_with_fallback(
        conn, current_user, username, game_id, depth, multipv, site
    )
    if not cached:
        job, _owner_id = _get_analysis_job_with_fallback(
            conn, current_user, username, game_id, depth, multipv, site
        )
        if job:
            return SingleGameInsightsResponse(
                status="analysis_processing",
                version="single_game_rules_v2",
            )
        return SingleGameInsightsResponse(
            status="analysis_missing",
            version="single_game_rules_v2",
        )

    cached_insights = _load_cached_insights(cached)
    if cached_insights:
        return SingleGameInsightsResponse(**cached_insights)

    game, _source_user_id = _get_game_for_deep_analysis(
        conn, current_user["id"], username, game_id, site
    )
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    full_analysis = _build_full_analysis_payload(cached)

    insights_payload = compute_single_game_insights(
        site=site,
        game_id=game_id,
        username=username,
        depth=depth,
        multipv=multipv,
        full_analysis=full_analysis,
        game_meta=game,
    )

    try:
        save_full_analysis_insights(
            conn,
            cached_owner_id,
            username,
            game_id,
            depth,
            multipv,
            site,
            json.dumps(insights_payload),
        )
        conn.commit()
    except Exception as persist_err:
        print(f"[Analysis] Failed to persist computed insights for game {game_id} on {site}: {persist_err}")

    return SingleGameInsightsResponse(**insights_payload)


@router.get("/{site}/{username}/{game_id}/full", response_model=FullAnalysisResponse)
async def get_full_analysis_endpoint(
    site: str,
    username: str,
    game_id: str,
    depth: int = Query(default=18, ge=1, le=30),
    multipv: int = Query(default=1, ge=1, le=5),
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict | None = Depends(get_optional_user),
):
    """Get full analysis status for a game."""
    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    cached, _owner_id = _get_full_analysis_with_fallback(
        conn, current_user, username, game_id, depth, multipv, site
    )
    if cached:
        full_analysis = _build_full_analysis_payload(cached)
        return FullAnalysisResponse(
            status="completed",
            analysis=full_analysis,
            insights=None,
            created_at=cached["created_at"]
        )

    job, _owner_id = _get_analysis_job_with_fallback(
        conn, current_user, username, game_id, depth, multipv, site
    )
    if job:
        return FullAnalysisResponse(status="processing")

    return FullAnalysisResponse(status="missing")


@router.post("/{site}/{username}/{game_id}/full", response_model=FullAnalysisResponse)
async def run_full_analysis_endpoint(
    site: str,
    username: str,
    game_id: str,
    request: Request,
    depth: int = Query(default=18, ge=1, le=30),
    multipv: int = Query(default=1, ge=1, le=5),
    force: bool = Query(default=False),
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict | None = Depends(get_optional_user),
):
    """Start full move-by-move analysis on a game (async with background task)."""
    global active_analysis_count

    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    actor_user_id = current_user["id"] if current_user else None
    owner_user_id = ensure_public_user_for_username(conn, username)
    username_hash = hash_username(username)
    await track_server_event(
        conn,
        event_name="analysis.deep.requested",
        user_id=actor_user_id,
        request=request,
        properties={
            "site": site,
            "depth": depth,
            "multipv": multipv,
            "force": force,
            "username": username_hash,
        },
    )

    if not force:
        cached, _cached_owner_id = _get_full_analysis_with_fallback(
            conn, current_user, username, game_id, depth, multipv, site
        )
        if cached:
            full_analysis = _build_full_analysis_payload(cached)
            await track_server_event(
                conn,
                event_name="analysis.deep.completed",
                user_id=actor_user_id,
                request=request,
                properties={
                    "site": site,
                    "depth": depth,
                    "multipv": multipv,
                    "force": force,
                    "username": username_hash,
                    "cached": True,
                },
            )
            conn.commit()
            return FullAnalysisResponse(
                status="completed",
                analysis=full_analysis,
                insights=None,
                created_at=cached["created_at"]
            )

    existing_job, _owner_id = _get_analysis_job_with_fallback(
        conn, current_user, username, game_id, depth, multipv, site
    )
    if existing_job:
        conn.commit()
        return FullAnalysisResponse(status="processing")

    async with active_analysis_lock:
        if active_analysis_count >= MAX_CONCURRENT_ANALYSES:
            await track_server_event(
                conn,
                event_name="analysis.deep.failed",
                user_id=actor_user_id,
                request=request,
                properties={
                    "site": site,
                    "depth": depth,
                    "multipv": multipv,
                    "username": username_hash,
                    "reason": "Server busy",
                },
            )
            conn.commit()
            raise HTTPException(
                status_code=429,
                detail="Server busy. Max 2 analyses can run at once. Try again shortly."
            )
        active_analysis_count += 1
        print(f"[Analysis] Starting new analysis. Active count: {active_analysis_count}")

    game, _source_user_id = _get_game_for_deep_analysis(
        conn, actor_user_id, username, game_id, site
    )
    if not game:
        async with active_analysis_lock:
            active_analysis_count -= 1
        await track_server_event(
            conn,
            event_name="analysis.deep.failed",
            user_id=actor_user_id,
            request=request,
            properties={
                "site": site,
                "depth": depth,
                "multipv": multipv,
                "username": username_hash,
                "reason": "Game not found",
            },
        )
        conn.commit()
        raise HTTPException(status_code=404, detail="Game not found")

    if not game.get("pgn"):
        async with active_analysis_lock:
            active_analysis_count -= 1
        await track_server_event(
            conn,
            event_name="analysis.deep.failed",
            user_id=actor_user_id,
            request=request,
            properties={
                "site": site,
                "depth": depth,
                "multipv": multipv,
                "username": username_hash,
                "reason": "Game has no PGN",
            },
        )
        conn.commit()
        raise HTTPException(status_code=400, detail="Game has no PGN")

    job_id = str(uuid.uuid4())
    create_analysis_job(conn, job_id, owner_user_id, username, game_id, depth, multipv, site)
    await track_server_event(
        conn,
        event_name="analysis.deep.started",
        user_id=actor_user_id,
        request=request,
        properties={
            "job_id": job_id,
            "site": site,
            "depth": depth,
            "multipv": multipv,
            "force": force,
            "username": username_hash,
        },
    )
    conn.commit()

    asyncio.create_task(
        run_analysis_background(
            job_id,
            owner_user_id,
            username,
            game_id,
            game["pgn"],
            game.get("opening_ply_count"),
            depth,
            multipv,
            site,
            analytics_user_id=actor_user_id,
            analytics_anonymous_id=request.headers.get("x-anonymous-id") if request else None,
            analytics_session_id=request.headers.get("x-session-id") if request else None,
            username_hash=username_hash,
        )
    )

    return FullAnalysisResponse(status="processing")


@router.get(
    "/{site}/{username}/{game_id}/ai-insights",
    response_model=AIInsightsResponse,
)
async def get_ai_insights_endpoint(
    site: str,
    username: str,
    game_id: str,
    depth: int = Query(default=18, ge=1, le=30),
    multipv: int = Query(default=1, ge=1, le=5),
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
    """Get account-scoped cached AI insights for a deep-analyzed game."""
    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    cached_ai = get_ai_game_insights(
        conn,
        current_user["id"],
        username,
        game_id,
        depth,
        multipv,
        site,
    )
    if cached_ai:
        payload = _load_ai_cached_insights(cached_ai)
        if _is_ai_cached_payload_current_and_clean(payload):
            created_at = cached_ai.get("updated_at") or cached_ai.get("created_at")
            return AIInsightsResponse(
                status="ready",
                insights=payload,
                created_at=str(created_at) if created_at is not None else None,
            )

    deep_cached, _owner_id = _get_full_analysis_with_fallback(
        conn, current_user, username, game_id, depth, multipv, site
    )
    if deep_cached:
        return AIInsightsResponse(
            status="analysis_missing",
            detail="Request AI insights to generate your AI summary.",
        )

    job, _owner_id = _get_analysis_job_with_fallback(
        conn, current_user, username, game_id, depth, multipv, site
    )
    if job:
        return AIInsightsResponse(
            status="analysis_missing",
            detail="In-depth analysis is still processing for this game.",
        )

    return AIInsightsResponse(
        status="analysis_missing",
        detail="Run in-depth analysis before requesting AI insights.",
    )


@router.post(
    "/{site}/{username}/{game_id}/ai-insights",
    response_model=AIInsightsResponse,
)
async def request_ai_insights_endpoint(
    site: str,
    username: str,
    game_id: str,
    request: Request,
    depth: int = Query(default=18, ge=1, le=30),
    multipv: int = Query(default=1, ge=1, le=5),
    force: bool = Query(default=False),
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
    """Generate or return cached account-scoped AI insights for a game."""
    site = validate_site(site)
    username = username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    username_hash = hash_username(username)
    unlimited_ai_quota = _is_unlimited_ai_email(current_user.get("email"))
    await track_server_event(
        conn,
        event_name="analysis.ai.requested",
        user_id=current_user["id"],
        request=request,
        properties={
            "site": site,
            "depth": depth,
            "multipv": multipv,
            "force": force,
            "quota_unlimited_email": unlimited_ai_quota,
            "username": username_hash,
        },
    )

    cached_ai = get_ai_game_insights(
        conn,
        current_user["id"],
        username,
        game_id,
        depth,
        multipv,
        site,
    )
    stale_cache_refresh = False
    if cached_ai and not force:
        payload = _load_ai_cached_insights(cached_ai)
        if _is_ai_cached_payload_current_and_clean(payload):
            conn.commit()
            created_at = cached_ai.get("updated_at") or cached_ai.get("created_at")
            return AIInsightsResponse(
                status="ready",
                insights=payload,
                created_at=str(created_at) if created_at is not None else None,
            )
        if isinstance(payload, dict):
            stale_cache_refresh = True

    deep_cached, _owner_id = _get_full_analysis_with_fallback(
        conn, current_user, username, game_id, depth, multipv, site
    )
    if not deep_cached:
        await track_server_event(
            conn,
            event_name="analysis.ai.failed",
            user_id=current_user["id"],
            request=request,
            properties={
                "site": site,
                "depth": depth,
                "multipv": multipv,
                "username": username_hash,
                "reason": "Deep analysis missing",
            },
        )
        conn.commit()
        return AIInsightsResponse(
            status="analysis_missing",
            detail="Run in-depth analysis before requesting AI insights.",
        )

    if AI_INSIGHTS_DAILY_LIMIT > 0 and not unlimited_ai_quota and not stale_cache_refresh:
        day_start_utc, day_end_utc = _current_utc_day_bounds()
        successful_today = count_user_ai_gemini_success_utc_day(
            conn,
            current_user["id"],
            day_start_utc,
            day_end_utc,
        )
        if successful_today >= AI_INSIGHTS_DAILY_LIMIT:
            await track_server_event(
                conn,
                event_name="analysis.ai.failed",
                user_id=current_user["id"],
                request=request,
                properties={
                    "site": site,
                    "depth": depth,
                    "multipv": multipv,
                    "username": username_hash,
                    "reason": "Daily quota exceeded",
                },
            )
            conn.commit()
            return AIInsightsResponse(
                status="quota_exceeded",
                detail=(
                    f"You can only request {AI_INSIGHTS_DAILY_LIMIT} AI insights per day. "
                    "Please try again after 00:00 UTC."
                ),
            )

    game_meta, _source_owner_id = _get_game_for_deep_analysis(
        conn, current_user["id"], username, game_id, site
    )
    if not game_meta:
        await track_server_event(
            conn,
            event_name="analysis.ai.failed",
            user_id=current_user["id"],
            request=request,
            properties={
                "site": site,
                "depth": depth,
                "multipv": multipv,
                "username": username_hash,
                "reason": "Game not found",
            },
        )
        conn.commit()
        return AIInsightsResponse(
            status="generation_failed",
            detail="AI insights could not be generated for this game.",
        )

    full_analysis = _build_full_analysis_payload(deep_cached)
    raw_insights = compute_single_game_insights(
        site=site,
        game_id=game_id,
        username=username,
        depth=depth,
        multipv=multipv,
        full_analysis=full_analysis,
        game_meta=game_meta,
    )

    try:
        narrated_payload = await asyncio.to_thread(
            ensure_narration,
            raw_insights,
            game_id,
            game_meta,
            True,
        )
    except Exception as err:
        log_ai_insights_request(
            conn,
            current_user["id"],
            username,
            game_id,
            depth,
            multipv,
            site,
            status="failed",
        )
        await track_server_event(
            conn,
            event_name="analysis.ai.failed",
            user_id=current_user["id"],
            request=request,
            properties={
                "site": site,
                "depth": depth,
                "multipv": multipv,
                "username": username_hash,
                "reason": f"Gemini error: {err}",
            },
        )
        conn.commit()
        return AIInsightsResponse(
            status="generation_failed",
            detail="AI insights are unavailable right now. Please try again.",
        )

    narration_meta = narrated_payload.get("narration_meta") if isinstance(narrated_payload, dict) else None
    narration_source = (
        str(narration_meta.get("source")).strip().lower()
        if isinstance(narration_meta, dict)
        else ""
    )
    if narration_source != "gemini":
        log_ai_insights_request(
            conn,
            current_user["id"],
            username,
            game_id,
            depth,
            multipv,
            site,
            status="failed",
        )
        await track_server_event(
            conn,
            event_name="analysis.ai.failed",
            user_id=current_user["id"],
            request=request,
            properties={
                "site": site,
                "depth": depth,
                "multipv": multipv,
                "username": username_hash,
                "reason": "Gemini unavailable",
            },
        )
        conn.commit()
        return AIInsightsResponse(
            status="generation_failed",
            detail="AI insights are unavailable right now. Please try again.",
        )

    save_ai_game_insights(
        conn,
        current_user["id"],
        username,
        game_id,
        depth,
        multipv,
        site,
        json.dumps(narrated_payload),
        source="gemini",
    )
    log_ai_insights_request(
        conn,
        current_user["id"],
        username,
        game_id,
        depth,
        multipv,
        site,
        status="gemini_refresh_success" if stale_cache_refresh else "gemini_success",
    )
    await track_server_event(
        conn,
        event_name="analysis.ai.completed",
        user_id=current_user["id"],
        request=request,
        properties={
            "site": site,
            "depth": depth,
            "multipv": multipv,
            "username": username_hash,
        },
    )
    conn.commit()

    return AIInsightsResponse(
        status="ready",
        insights=narrated_payload,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
