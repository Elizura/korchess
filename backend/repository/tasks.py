"""Celery task definitions for game processing pipeline."""

import json
import logging
from typing import Any

from repository.celery_app import app
from repository.db import (
    bulk_upsert_games,
    get_featured_game_ids,
    get_games_for_insights,
    get_insight_game_features,
    get_quick_scan_results,
    get_scanned_game_ids,
    update_insight_job,
    update_scan_job,
    upsert_game_quick_scan,
    upsert_insight_game_feature,
    upsert_player_insights,
)
from repository.db_connection import get_connection
from services.insights import (
    _build_aggregate_features,
    _build_fallback_narrative,
    build_narrative,
    extract_light_game_features,
)
from utils.insights_constants import FEATURE_VERSION, MAX_GAMES_WINDOW, NARRATIVE_VERSION
from utils.insights_utils import utc_now_iso
from services.quick_scan import run_quick_scan_single
from utils.quick_scan_constants import QUICK_SCAN_MAX_GAMES
from repository.redis_client import redis_client as _redis

logger = logging.getLogger(__name__)


def _import_key(username: str, site: str, field: str) -> str:
    return f"import:{username.strip().lower()}:{site}:{field}"


@app.task(name="tasks.process_game", bind=True, max_retries=3, default_retry_delay=10)
def process_game(self, game_data: dict, username: str, site: str) -> dict:
    """Process a single game: store, extract features, quick scan.

    After finishing, increments the Redis done counter. When done == total,
    runs coaching-summary aggregation and marks the import as complete.
    """
    canonical = username.strip().lower()
    site_game_id = game_data.get("site_game_id", "unknown")

    try:
        with get_connection() as conn:
            inserted, skipped = bulk_upsert_games(conn, [game_data])
            conn.commit()
    except Exception as exc:
        raise self.retry(exc=exc)

    try:
        light_features = extract_light_game_features(game_data)
        with get_connection() as conn:
            upsert_insight_game_feature(
                conn,
                username=canonical,
                site=site,
                site_game_id=site_game_id,
                feature_version=FEATURE_VERSION,
                light=light_features,
                deep=None,
            )
            conn.commit()
    except Exception:
        logger.exception("Light feature extraction failed for %s/%s", site, site_game_id)

    try:
        scan_result = run_quick_scan_single(game_data, canonical)
        problems_str = json.dumps(scan_result)
        summary_str = json.dumps(scan_result.get("summary", {}))

        with get_connection() as conn:
            upsert_game_quick_scan(
                conn,
                username=canonical,
                site=site,
                site_game_id=site_game_id,
                problems_json=problems_str,
                summary_json=summary_str,
            )
            conn.commit()
    except Exception:
        logger.exception("Quick scan failed for %s/%s", site, site_game_id)

    done_key = _import_key(canonical, site, "done")
    total_key = _import_key(canonical, site, "total")
    meta_key = _import_key(canonical, site, "meta")
    status_key = _import_key(canonical, site, "status")

    done = _redis.incr(done_key)
    total_raw = _redis.get(total_key)
    total = int(total_raw) if total_raw is not None else None

    if total is not None and done == total:
        _redis.set(status_key, "complete", ex=3600)
        _redis.delete(done_key, total_key, meta_key)
        try:
            _run_aggregation(canonical, site)
        except Exception:
            logger.exception("Post-import aggregation failed for %s", canonical)

    return {
        "site_game_id": site_game_id,
        "inserted": inserted,
        "skipped": skipped,
    }


def _run_aggregation(canonical: str, site: str) -> None:
    """Build aggregate coaching insights from light features and quick-scan data.

    Called automatically when all games in an import batch finish processing.
    Also used by the ``run_insights`` Celery task for manual refreshes.
    """
    with get_connection() as conn:
        stored_features = get_insight_game_features(
            conn,
            username=canonical,
            site="all",
            feature_version=FEATURE_VERSION,
        )
        scan_rows = get_quick_scan_results(conn, canonical, "all")

    if not stored_features:
        return

    features, coverage, fact_map = _build_aggregate_features(stored_features, scan_rows)
    narrative = build_narrative(features, fact_map)

    status = "complete" if coverage.get("has_enough_games") else "not_enough_data"

    with get_connection() as conn:
        upsert_player_insights(
            conn,
            username=canonical,
            site="all",
            status=status,
            feature_version=FEATURE_VERSION,
            narrative_version=NARRATIVE_VERSION,
            coverage=coverage,
            features=features,
            fact_map=fact_map,
            narrative=narrative,
            source_job_id=None,
        )
        conn.commit()


@app.task(name="tasks.run_insights")
def run_insights(
    job_id: str,
    username: str,
    site: str = "all",
    trigger_quick_scan: bool = False,
) -> dict:
    """Run the full insights pipeline as a Celery task.

    Synchronous equivalent of run_insights_pipeline from insights.py.
    """
    canonical = username.strip().lower()
    started_at = utc_now_iso()

    with get_connection() as conn:
        update_insight_job(
            conn, job_id,
            status="running",
            stage="light",
            error="",
            started_at=started_at,
        )
        conn.commit()

    try:
        with get_connection() as conn:
            games = get_games_for_insights(
                conn, username=canonical, site=site, limit=MAX_GAMES_WINDOW,
            )
            already_featured = get_featured_game_ids(
                conn, username=canonical, site=site, feature_version=FEATURE_VERSION,
            )

        if not games:
            empty_features = {
                "version": FEATURE_VERSION,
                "computed_at": utc_now_iso(),
                "style": {"label": "Insufficient Data", "scores": {}},
                "performance": {"overall": {"games": 0, "score_pct": 0.0}},
                "time_pressure": {},
                "recurring_themes": [],
                "strengths": [],
                "weaknesses": [],
                "coaching_focus": [],
                "confidence": {"value": 0.0},
            }
            coverage: dict[str, Any] = {
                "games_total": 0,
                "games_light": 0,
                "games_scanned": 0,
                "scan_coverage": 0.0,
                "games_with_clock": 0,
                "clock_coverage": 0.0,
                "has_enough_games": False,
            }
            fact_map: dict[str, Any] = {}
            narrative = _build_fallback_narrative(empty_features)
            _save_snapshot_sync(
                canonical, site, "not_enough_data",
                coverage, empty_features, fact_map, narrative, job_id,
            )
            with get_connection() as conn:
                update_insight_job(
                    conn, job_id,
                    status="completed", stage="complete",
                    finished_at=utc_now_iso(),
                )
                conn.commit()
            return {"job_id": job_id, "status": "completed", "games": 0}

        new_games = [
            g for g in games
            if (g["site"], g["site_game_id"]) not in already_featured
        ]

        for game in new_games:
            light_feature = extract_light_game_features(game)
            with get_connection() as conn:
                upsert_insight_game_feature(
                    conn,
                    username=canonical,
                    site=game["site"],
                    site_game_id=game["site_game_id"],
                    feature_version=FEATURE_VERSION,
                    light=light_feature,
                    deep=None,
                )
                conn.commit()

        with get_connection() as conn:
            stored_features = get_insight_game_features(
                conn, username=canonical, site=site,
                feature_version=FEATURE_VERSION,
            )
            scan_rows = get_quick_scan_results(conn, canonical, site)

        features, coverage, fact_map = _build_aggregate_features(stored_features, scan_rows)
        narrative = build_narrative(features, fact_map)

        initial_status = "baseline_ready" if coverage.get("has_enough_games") else "not_enough_data"
        _save_snapshot_sync(
            canonical, site, initial_status,
            coverage, features, fact_map, narrative, job_id,
        )

        if not coverage.get("has_enough_games"):
            with get_connection() as conn:
                update_insight_job(
                    conn, job_id,
                    status="completed", stage="complete",
                    finished_at=utc_now_iso(),
                )
                conn.commit()
            return {"job_id": job_id, "status": "completed", "games": len(games)}

        _save_snapshot_sync(
            canonical, site, "complete",
            coverage, features, fact_map, narrative, job_id,
        )

        with get_connection() as conn:
            update_insight_job(
                conn, job_id,
                status="completed", stage="complete",
                finished_at=utc_now_iso(),
            )
            conn.commit()

        if trigger_quick_scan:
            from quick_scan import schedule_quick_scan
            try:
                schedule_quick_scan(canonical, site=site)
            except Exception:
                pass

        return {"job_id": job_id, "status": "completed", "games": len(games)}

    except Exception as exc:
        logger.exception("Insights pipeline failed for %s", canonical)
        with get_connection() as conn:
            update_insight_job(
                conn, job_id,
                status="failed", stage="failed",
                error=str(exc), finished_at=utc_now_iso(),
            )
            conn.commit()
        return {"job_id": job_id, "status": "failed", "error": str(exc)}


def _save_snapshot_sync(
    username: str,
    site: str,
    status: str,
    coverage: dict[str, Any],
    features: dict[str, Any],
    fact_map: dict[str, Any],
    narrative: dict[str, Any],
    source_job_id: str | None,
) -> None:
    with get_connection() as conn:
        upsert_player_insights(
            conn,
            username=username,
            site=site,
            status=status,
            feature_version=FEATURE_VERSION,
            narrative_version=NARRATIVE_VERSION,
            coverage=coverage,
            features=features,
            fact_map=fact_map,
            narrative=narrative,
            source_job_id=source_job_id,
        )
        conn.commit()


@app.task(name="tasks.run_scan")
def run_scan(
    job_id: str,
    username: str,
    site: str = "all",
) -> dict:
    """Run the quick-scan batch as a Celery task.

    Synchronous equivalent of run_quick_scan_batch from quick_scan.py.
    """
    canonical = username.strip().lower()
    started_at = utc_now_iso()

    with get_connection() as conn:
        update_scan_job(conn, job_id, status="running", started_at=started_at)
        conn.commit()

    try:
        with get_connection() as conn:
            games = get_games_for_insights(
                conn, username=canonical, site=site, limit=QUICK_SCAN_MAX_GAMES,
            )
            already_scanned = get_scanned_game_ids(conn, canonical, site)

        to_scan = [
            g for g in games
            if (g["site"], g["site_game_id"]) not in already_scanned
        ]

        if not to_scan:
            with get_connection() as conn:
                update_scan_job(
                    conn, job_id,
                    status="completed", games_done=0,
                    finished_at=utc_now_iso(),
                )
                conn.commit()
            _merge_scan_into_insights(canonical, site, job_id)
            return {"job_id": job_id, "status": "completed", "scanned": 0}

        with get_connection() as conn:
            update_scan_job(conn, job_id, games_done=0)
            conn.commit()

        done_count = 0
        for game in to_scan:
            try:
                result = run_quick_scan_single(game, canonical)
                problems_str = json.dumps(result)
                summary_str = json.dumps(result.get("summary", {}))

                with get_connection() as conn:
                    upsert_game_quick_scan(
                        conn,
                        username=canonical,
                        site=game["site"],
                        site_game_id=game["site_game_id"],
                        problems_json=problems_str,
                        summary_json=summary_str,
                    )
                    conn.commit()
            except Exception:
                logger.exception(
                    "Quick scan failed for game %s/%s",
                    game.get("site"), game.get("site_game_id"),
                )

            done_count += 1
            if done_count % 5 == 0 or done_count == len(to_scan):
                with get_connection() as conn:
                    update_scan_job(conn, job_id, games_done=done_count)
                    conn.commit()

        with get_connection() as conn:
            update_scan_job(
                conn, job_id,
                status="completed", games_done=done_count,
                finished_at=utc_now_iso(),
            )
            conn.commit()

        _merge_scan_into_insights(canonical, site, job_id)
        return {"job_id": job_id, "status": "completed", "scanned": done_count}

    except Exception as exc:
        logger.exception("Quick scan batch failed: %s", exc)
        with get_connection() as conn:
            update_scan_job(
                conn, job_id,
                status="failed", error=str(exc),
                finished_at=utc_now_iso(),
            )
            conn.commit()
        return {"job_id": job_id, "status": "failed", "error": str(exc)}


def _merge_scan_into_insights(
    username: str,
    site: str,
    job_id: str,
) -> None:
    """Re-aggregate insights after a scan batch completes."""
    try:
        _run_aggregation(username, site)
    except Exception:
        logger.exception("Post-scan aggregation failed for %s", username)
