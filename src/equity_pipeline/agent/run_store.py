"""
Saving screening runs to the database, for the web front end (table `screening_runs`).

The web front end (apps/web) cannot run Python. It starts a run by opening the Streamlit worker
(apps/dashboard) in a hidden frame and then POLLS this table. The worker writes into it:

    start      → status "running", a progress line per pipeline step
    finish     → status "complete" with the brief, the quality checks, the monitoring and the manifest
    failure    → status "error" with the message

One row per ticker: the latest run. A `nonce` (a number the front end makes up per click) is stored with
the run, so the front end can tell "my run" from an older result of the same ticker.

Guards (a public demo must not let anyone run the LLM in a loop):
    a run that already carries this nonce is not started again (a page reload must not rerun it)
    a run still in progress for the ticker blocks a second one, unless it has been silent too long
    a result younger than COOLDOWN_SECONDS is reused instead of running again

The same DDL is in database/schema.sql (used when the database is set up by hand).
"""
from __future__ import annotations

import json
import logging

from psycopg2.extras import Json

logger = logging.getLogger("agent.run_store")

COOLDOWN_SECONDS = 300        # a finished result younger than this is reused
STALE_RUN_SECONDS = 600       # a "running" row not updated for this long is a run that died (tab closed, crash)

_DDL = """
    CREATE TABLE IF NOT EXISTS screening_runs (
        ticker TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        nonce TEXT,
        status TEXT NOT NULL DEFAULT 'idle',
        mode TEXT NOT NULL DEFAULT 'demo',
        seed INT DEFAULT 42,
        progress_message TEXT,
        summary JSONB,
        quality JSONB,
        monitoring JSONB,
        pipeline_counts JSONB,
        manifest JSONB,
        error_message TEXT,
        started_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        completed_at TIMESTAMPTZ
    );
"""


def _json(value) -> Json:
    return Json(value, dumps=lambda o: json.dumps(o, default=str))


def ensure_table(db) -> None:
    with db.conn.cursor() as cur:
        cur.execute(_DDL)


def may_start(db, ticker: str, nonce: str | None, cooldown_seconds: int = COOLDOWN_SECONDS,
              stale_seconds: int = STALE_RUN_SECONDS) -> tuple[bool, str]:
    """(allowed, reason). reason is "ok", "already_handled", "running" or "recent_result"."""
    ensure_table(db)
    with db.conn.cursor() as cur:
        cur.execute(
            "SELECT status, nonce, updated_at > NOW() - make_interval(secs => %s), "
            "       completed_at IS NOT NULL AND completed_at > NOW() - make_interval(secs => %s) "
            "FROM screening_runs WHERE ticker = %s",
            (stale_seconds, cooldown_seconds, ticker.upper()))
        row = cur.fetchone()
    if not row:
        return True, "ok"
    status, stored_nonce, fresh, recent = row
    if nonce and stored_nonce == nonce:
        return False, "already_handled"
    if status == "running" and fresh:
        return False, "running"
    if status == "complete" and recent:
        return False, "recent_result"
    return True, "ok"


def acknowledge(db, ticker: str, nonce: str | None, message: str) -> None:
    """The result already stored is good enough: mark it as the answer to this nonce, so the front end
    that is polling for it shows that result instead of waiting for a run that will not happen."""
    ensure_table(db)
    with db.conn.cursor() as cur:
        cur.execute(
            "UPDATE screening_runs SET nonce = COALESCE(%s, nonce), progress_message = %s, updated_at = NOW() "
            "WHERE ticker = %s AND status = 'complete'",
            (nonce, message, ticker.upper()))


def update_progress(db, ticker: str, run_id: str, message: str, mode: str = "demo", seed: int = 42,
                    nonce: str | None = None) -> None:
    ensure_table(db)
    with db.conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO screening_runs (ticker, run_id, nonce, status, mode, seed, progress_message, started_at, updated_at)
            VALUES (%s, %s, %s, 'running', %s, %s, %s, NOW(), NOW())
            ON CONFLICT (ticker) DO UPDATE
            SET run_id = EXCLUDED.run_id,
                nonce = COALESCE(EXCLUDED.nonce, screening_runs.nonce),
                status = 'running',
                mode = EXCLUDED.mode,
                seed = EXCLUDED.seed,
                progress_message = EXCLUDED.progress_message,
                error_message = NULL,
                updated_at = NOW();
            """,
            (ticker.upper(), run_id, nonce, mode, seed, message))


def save_completed(db, ticker: str, run_id: str, mode: str, seed: int, summary: dict, quality: dict,
                   monitoring: dict, pipeline_counts: dict | None, manifest: dict, nonce: str | None = None) -> None:
    ensure_table(db)
    with db.conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO screening_runs (
                ticker, run_id, nonce, status, mode, seed, progress_message,
                summary, quality, monitoring, pipeline_counts, manifest, started_at, updated_at, completed_at)
            VALUES (%s, %s, %s, 'complete', %s, %s, 'SCREENING COMPLETE', %s, %s, %s, %s, %s, NOW(), NOW(), NOW())
            ON CONFLICT (ticker) DO UPDATE
            SET run_id = EXCLUDED.run_id,
                nonce = COALESCE(EXCLUDED.nonce, screening_runs.nonce),
                status = 'complete',
                mode = EXCLUDED.mode,
                seed = EXCLUDED.seed,
                progress_message = 'SCREENING COMPLETE',
                summary = EXCLUDED.summary,
                quality = EXCLUDED.quality,
                monitoring = EXCLUDED.monitoring,
                pipeline_counts = EXCLUDED.pipeline_counts,
                manifest = EXCLUDED.manifest,
                error_message = NULL,
                updated_at = NOW(),
                completed_at = NOW();
            """,
            (ticker.upper(), run_id, nonce, mode, seed, _json(summary), _json(quality), _json(monitoring),
             _json(pipeline_counts or {}), _json(manifest)))


def record_error(db, ticker: str, run_id: str, error_message: str, nonce: str | None = None) -> None:
    ensure_table(db)
    with db.conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO screening_runs (ticker, run_id, nonce, status, progress_message, error_message, updated_at)
            VALUES (%s, %s, %s, 'error', 'ERROR DURING SCREENING RUN', %s, NOW())
            ON CONFLICT (ticker) DO UPDATE
            SET status = 'error',
                progress_message = 'ERROR DURING SCREENING RUN',
                error_message = EXCLUDED.error_message,
                nonce = COALESCE(EXCLUDED.nonce, screening_runs.nonce),
                updated_at = NOW();
            """,
            (ticker.upper(), run_id, nonce, error_message[:2000]))


def quality_payload(quality) -> dict:
    """A QualityReport as the plain dict stored in the `quality` column."""
    return {"score": round(quality.score, 4), "passed_all": quality.passed_all,
            "checks": quality.checks, "details": quality.details}
