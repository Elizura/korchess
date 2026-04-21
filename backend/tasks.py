"""Celery task definitions for game processing pipeline."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import redis as redis_lib

from celery_app import app
from db import (
    bulk_upsert_games,
    get_connection,
    get_featured_game_ids,
    get_games_for_insights,
    get_insight_game_features,
    get_player_insights,
    get_quick_scan_results,
    get_scanned_game_ids,
    update_insight_job,
    update_scan_job,
    upsert_game_quick_scan,
    upsert_import_status,
    upsert_insight_game_feature,
    upsert_player_insights,
)
from insights import (
    _build_aggregate_features,
    _build_fallback_narrative,
    build_narrative,
    extract_light_game_features,
)
from insights_aggregate import aggregate_scan_features
from insights_constants import FEATURE_VERSION, MAX_GAMES_WINDOW, NARRATIVE_VERSION
from insights_utils import utc_now_iso
from quick_scan import run_quick_scan_single
from quick_scan_constants import QUICK_SCAN_MAX_GAMES

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_redis = redis_lib.from_url(REDIS_URL, decode_responses=True)

# Flip to True when you want coaching summary / aggregate insights on import.
RUN_AGGREGATION_ON_IMPORT = False


def _import_key(username: str, site: str, field: str) -> str:
    return f"import:{username.strip().lower()}:{site}:{field}"


@app.task(name="tasks.process_game", bind=True, max_retries=3, default_retry_delay=10)
def process_game(self, game_data: dict, username: str, site: str) -> dict:
    """Process a single game: store, extract features, quick scan.

    After finishing, increments the Redis done counter. When done == total,
    auto-triggers finalize_import.
    """
    canonical = username.strip().lower()
    site_game_id = game_data.get("site_game_id", "unknown")

    conn = get_connection()
    try:
        inserted, skipped = bulk_upsert_games(conn, [game_data])
        conn.commit()
    except Exception as exc:
        conn.close()
        raise self.retry(exc=exc)
    finally:
        if not conn.closed:
            conn.close()

    try:
        light_features = extract_light_game_features(game_data)
        conn = get_connection()
        try:
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
        finally:
            conn.close()
    except Exception:
        logger.exception("Light feature extraction failed for %s/%s", site, site_game_id)

    try:
        scan_result = run_quick_scan_single(game_data, canonical)
        problems_str = json.dumps(scan_result)
        summary_str = json.dumps(scan_result.get("summary", {}))

        conn = get_connection()
        try:
            upsert_game_quick_scan(
                conn,
                username=canonical,
                site=site,
                site_game_id=site_game_id,
                problems_json=problems_str,
                summary_json=summary_str,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("Quick scan failed for %s/%s", site, site_game_id)

    done_key = _import_key(canonical, site, "done")
    total_key = _import_key(canonical, site, "total")
    meta_key = _import_key(canonical, site, "meta")

    done = _redis.incr(done_key)
    total_raw = _redis.get(total_key)
    total = int(total_raw) if total_raw is not None else None

    if total is not None and done >= total:
        meta_raw = _redis.get(meta_key)
        import_meta = json.loads(meta_raw) if meta_raw else {}
        finalize_import.delay(canonical, site, import_meta)

    return {
        "site_game_id": site_game_id,
        "inserted": inserted,
        "skipped": skipped,
    }


@app.task(name="tasks.finalize_import")
def finalize_import(
    username: str,
    site: str,
    import_meta: dict,
) -> dict:
    """Run after all game tasks complete: record import status.

    Aggregation (coaching summary, narrative, scan merge) is gated behind
    RUN_AGGREGATION_ON_IMPORT. Flip it to True to re-enable.
    """
    canonical = username.strip().lower()

    now = datetime.now(timezone.utc)
    imported_at = now.isoformat()
    synced_at_value = now.isoformat()

    done_key = _import_key(canonical, site, "done")
    total_key = _import_key(canonical, site, "total")
    meta_key = _import_key(canonical, site, "meta")
    status_key = _import_key(canonical, site, "status")

    done_raw = _redis.get(done_key)
    total_inserted = int(done_raw) if done_raw else 0
    total_skipped = import_meta.get("parse_skipped", 0)

    conn = get_connection()
    try:
        upsert_import_status(
            conn,
            username=canonical,
            site=site,
            imported=total_inserted,
            skipped=total_skipped,
            max_games=import_meta.get("max_games", 0),
            imported_at=imported_at,
            last_synced_at=synced_at_value,
        )
        conn.commit()
    finally:
        conn.close()

    if RUN_AGGREGATION_ON_IMPORT:
        _run_aggregation(canonical, site)

    _redis.set(status_key, "complete", ex=3600)
    _redis.delete(done_key, total_key, meta_key)

    return {
        "username": canonical,
        "site": site,
        "imported": total_inserted,
        "skipped": total_skipped,
    }


def _run_aggregation(canonical: str, site: str) -> None:
    """Build aggregate insights and merge scan data.

    Kept as a separate function so the logic is preserved intact and can be
    re-enabled by flipping RUN_AGGREGATION_ON_IMPORT.
    """
    try:
        conn = get_connection()
        try:
            stored_features = get_insight_game_features(
                conn,
                username=canonical,
                site="all",
                feature_version=FEATURE_VERSION,
            )
        finally:
            conn.close()

        if stored_features:
            features, coverage, fact_map = _build_aggregate_features(stored_features)
            narrative = build_narrative(features, fact_map)

            status = "complete" if coverage.get("has_enough_games") else "not_enough_data"

            conn = get_connection()
            try:
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
            finally:
                conn.close()
    except Exception:
        logger.exception("Aggregate insights failed for %s", canonical)

    try:
        conn = get_connection()
        try:
            scan_rows = get_quick_scan_results(conn, canonical, "all")
            existing = get_player_insights(conn, canonical, "all")
        finally:
            conn.close()

        if scan_rows and existing:
            scan_agg = aggregate_scan_features(scan_rows)

            features_dict: dict[str, Any] = existing.get("features") or {}
            features_dict["performance"] = features_dict.get("performance", {})
            features_dict["performance"]["phase"] = scan_agg.get("phase_performance", {})
            features_dict["recurring_themes"] = scan_agg.get("theme_items", [])
            features_dict["time_pressure"] = features_dict.get("time_pressure", {})
            features_dict["time_pressure"]["blunders_total"] = scan_agg.get("total_blunders", 0)
            features_dict["time_pressure"]["blunders_from_scan"] = True
            features_dict["scan_aggregate"] = scan_agg

            conn = get_connection()
            try:
                upsert_player_insights(
                    conn,
                    username=canonical,
                    site="all",
                    status="complete",
                    feature_version=existing.get("feature_version", ""),
                    narrative_version=existing.get("narrative_version", ""),
                    coverage=existing.get("coverage") or {},
                    features=features_dict,
                    fact_map=existing.get("fact_map") or {},
                    narrative=existing.get("narrative") or {},
                    source_job_id=None,
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        logger.exception("Scan aggregate merge failed for %s", canonical)


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

    conn = get_connection()
    try:
        update_insight_job(
            conn, job_id,
            status="running",
            stage="light",
            error="",
            started_at=started_at,
        )
        conn.commit()
    finally:
        conn.close()

    try:
        conn = get_connection()
        try:
            games = get_games_for_insights(
                conn, username=canonical, site=site, limit=MAX_GAMES_WINDOW,
            )
            already_featured = get_featured_game_ids(
                conn, username=canonical, site=site, feature_version=FEATURE_VERSION,
            )
        finally:
            conn.close()

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
                "games_deep": 0,
                "deep_coverage": 0.0,
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
            conn = get_connection()
            try:
                update_insight_job(
                    conn, job_id,
                    status="completed", stage="complete",
                    finished_at=utc_now_iso(),
                )
                conn.commit()
            finally:
                conn.close()
            return {"job_id": job_id, "status": "completed", "games": 0}

        new_games = [
            g for g in games
            if (g["site"], g["site_game_id"]) not in already_featured
        ]

        for game in new_games:
            light_feature = extract_light_game_features(game)
            conn = get_connection()
            try:
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
            finally:
                conn.close()

        conn = get_connection()
        try:
            stored_features = get_insight_game_features(
                conn, username=canonical, site=site,
                feature_version=FEATURE_VERSION,
            )
        finally:
            conn.close()

        features, coverage, fact_map = _build_aggregate_features(stored_features)
        narrative = build_narrative(features, fact_map)

        initial_status = "baseline_ready" if coverage.get("has_enough_games") else "not_enough_data"
        _save_snapshot_sync(
            canonical, site, initial_status,
            coverage, features, fact_map, narrative, job_id,
        )

        if not coverage.get("has_enough_games"):
            conn = get_connection()
            try:
                update_insight_job(
                    conn, job_id,
                    status="completed", stage="complete",
                    finished_at=utc_now_iso(),
                )
                conn.commit()
            finally:
                conn.close()
            return {"job_id": job_id, "status": "completed", "games": len(games)}

        _save_snapshot_sync(
            canonical, site, "complete",
            coverage, features, fact_map, narrative, job_id,
        )

        conn = get_connection()
        try:
            update_insight_job(
                conn, job_id,
                status="completed", stage="complete",
                finished_at=utc_now_iso(),
            )
            conn.commit()
        finally:
            conn.close()

        if trigger_quick_scan:
            from quick_scan import schedule_quick_scan
            try:
                schedule_quick_scan(canonical, site=site)
            except Exception:
                pass

        return {"job_id": job_id, "status": "completed", "games": len(games)}

    except Exception as exc:
        logger.exception("Insights pipeline failed for %s", canonical)
        conn = get_connection()
        try:
            update_insight_job(
                conn, job_id,
                status="failed", stage="failed",
                error=str(exc), finished_at=utc_now_iso(),
            )
            conn.commit()
        finally:
            conn.close()
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
    conn = get_connection()
    try:
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
    finally:
        conn.close()


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

    conn = get_connection()
    try:
        update_scan_job(conn, job_id, status="running", started_at=started_at)
        conn.commit()
    finally:
        conn.close()

    try:
        conn = get_connection()
        try:
            games = get_games_for_insights(
                conn, username=canonical, site=site, limit=QUICK_SCAN_MAX_GAMES,
            )
            already_scanned = get_scanned_game_ids(conn, canonical, site)
        finally:
            conn.close()

        to_scan = [
            g for g in games
            if (g["site"], g["site_game_id"]) not in already_scanned
        ]

        if not to_scan:
            conn = get_connection()
            try:
                update_scan_job(
                    conn, job_id,
                    status="completed", games_done=0,
                    finished_at=utc_now_iso(),
                )
                conn.commit()
            finally:
                conn.close()
            _merge_scan_into_insights(canonical, site, job_id)
            return {"job_id": job_id, "status": "completed", "scanned": 0}

        conn = get_connection()
        try:
            update_scan_job(conn, job_id, games_done=0)
            conn.commit()
        finally:
            conn.close()

        done_count = 0
        for game in to_scan:
            try:
                result = run_quick_scan_single(game, canonical)
                problems_str = json.dumps(result)
                summary_str = json.dumps(result.get("summary", {}))

                conn = get_connection()
                try:
                    upsert_game_quick_scan(
                        conn,
                        username=canonical,
                        site=game["site"],
                        site_game_id=game["site_game_id"],
                        problems_json=problems_str,
                        summary_json=summary_str,
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                logger.exception(
                    "Quick scan failed for game %s/%s",
                    game.get("site"), game.get("site_game_id"),
                )

            done_count += 1
            if done_count % 5 == 0 or done_count == len(to_scan):
                conn = get_connection()
                try:
                    update_scan_job(conn, job_id, games_done=done_count)
                    conn.commit()
                finally:
                    conn.close()

        conn = get_connection()
        try:
            update_scan_job(
                conn, job_id,
                status="completed", games_done=done_count,
                finished_at=utc_now_iso(),
            )
            conn.commit()
        finally:
            conn.close()

        _merge_scan_into_insights(canonical, site, job_id)
        return {"job_id": job_id, "status": "completed", "scanned": done_count}

    except Exception as exc:
        logger.exception("Quick scan batch failed: %s", exc)
        conn = get_connection()
        try:
            update_scan_job(
                conn, job_id,
                status="failed", error=str(exc),
                finished_at=utc_now_iso(),
            )
            conn.commit()
        finally:
            conn.close()
        return {"job_id": job_id, "status": "failed", "error": str(exc)}


def _merge_scan_into_insights(
    username: str,
    site: str,
    job_id: str,
) -> None:
    """Synchronous equivalent of _merge_and_update_insights from quick_scan.py."""
    conn = get_connection()
    try:
        scan_rows = get_quick_scan_results(conn, username, site)
        existing = get_player_insights(conn, username, site)
    finally:
        conn.close()

    if not scan_rows:
        return

    scan_agg = aggregate_scan_features(scan_rows)

    features: dict[str, Any] = (existing.get("features") or {}) if existing else {}
    features["performance"] = features.get("performance", {})
    features["performance"]["phase"] = scan_agg.get("phase_performance", {})
    features["recurring_themes"] = scan_agg.get("theme_items", [])
    features["time_pressure"] = features.get("time_pressure", {})
    features["time_pressure"]["blunders_total"] = scan_agg.get("total_blunders", 0)
    features["time_pressure"]["blunders_from_scan"] = True
    features["scan_aggregate"] = scan_agg

    conn = get_connection()
    try:
        if existing:
            upsert_player_insights(
                conn,
                username=username,
                site=site,
                status="complete",
                feature_version=existing.get("feature_version", ""),
                narrative_version=existing.get("narrative_version", ""),
                coverage=existing.get("coverage") or {},
                features=features,
                fact_map=existing.get("fact_map") or {},
                narrative=existing.get("narrative") or {},
                source_job_id=job_id,
            )
        conn.commit()
    finally:
        conn.close()
