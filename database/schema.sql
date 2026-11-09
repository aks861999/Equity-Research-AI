-- ============================================================================
-- Database schema of the project (PostgreSQL). Safe to run again: everything is IF NOT EXISTS / OR REPLACE.
--
--   python scripts/online/apply_schema.py                         applies it to the database set in .env
--   python scripts/online/apply_schema.py --target-url "<url>"    ... or to another one (for example Supabase)
--
-- The Python code creates the same tables when it first needs them; this file is for setting up a
-- database by hand and for the views the web front end reads.
-- ============================================================================

-- 1. Documents: news articles, Yahoo headlines and SEC filing sections (one JSON document per row)
CREATE TABLE IF NOT EXISTS articles (
    id  TEXT PRIMARY KEY,
    doc JSONB
);
CREATE INDEX IF NOT EXISTS idx_articles_doc ON articles USING GIN (doc);
CREATE INDEX IF NOT EXISTS type_ticker_idx ON articles ((doc->>'type'), (doc->>'ticker'));
CREATE INDEX IF NOT EXISTS type_extraction_status_idx ON articles ((doc->>'type'), (doc->'extraction'->>'status'));

-- 2. The 36 financial ratios per company
CREATE TABLE IF NOT EXISTS fundamental_metrics (
    id                TEXT PRIMARY KEY,
    ticker            TEXT,
    metric_id         TEXT,
    fundamental_value FLOAT,
    fundamental_grade TEXT,
    calculated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    fiscal_year_end   TEXT,
    period_type       TEXT,
    currency          TEXT
);

-- 3. News sentiment per company and metric
CREATE TABLE IF NOT EXISTS metric_scores (
    id                 TEXT PRIMARY KEY,
    ticker             TEXT,
    metric_id          TEXT,
    weighted_sentiment FLOAT,
    dispersion         FLOAT,
    volume             INT,
    calculated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. The latest screening run per ticker: what the web front end polls (see agent/run_store.py)
CREATE TABLE IF NOT EXISTS screening_runs (
    ticker           TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    nonce            TEXT,
    status           TEXT NOT NULL DEFAULT 'idle',     -- 'running' | 'complete' | 'error'
    mode             TEXT NOT NULL DEFAULT 'demo',
    seed             INT DEFAULT 42,
    progress_message TEXT,
    summary          JSONB,
    quality          JSONB,
    monitoring       JSONB,
    pipeline_counts  JSONB,
    manifest         JSONB,
    error_message    TEXT,
    started_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    completed_at     TIMESTAMPTZ
);

-- 5. View for the "reference sources" table: links and labels only, never the article text
CREATE OR REPLACE VIEW v_reference_sources AS
SELECT
    id,
    doc->>'ticker' AS ticker,
    doc->>'type'   AS doc_type,
    doc->>'source' AS source,
    CASE
        WHEN doc->>'type' = 'sec_filing'        THEN trim('SEC filing ' || coalesce(doc->>'form_type', ''))
        WHEN doc->>'source' = 'searxng_news'    THEN 'News (search)'
        WHEN doc->>'source' = 'yahoo_finance'   THEN 'News (Yahoo Finance)'
        WHEN doc->>'source' = 'google_news'     THEN 'News (Google, old)'
        ELSE coalesce(doc->>'source', 'News')
    END AS source_label,
    CASE
        WHEN doc->>'type' = 'sec_filing'
            THEN trim(both ' -' from coalesce(doc->>'form_type', 'Filing') || ' - ' || coalesce(doc->>'section', ''))
        ELSE coalesce(doc->>'title', '(Untitled)')
    END AS title,
    CASE WHEN doc->>'type' = 'sec_filing' THEN doc->>'source_url' ELSE doc->>'url' END AS link,
    CASE
        WHEN doc->>'type' = 'sec_filing' THEN left(coalesce(doc->>'filing_date', ''), 10)
        ELSE left(coalesce(doc->>'published_at', ''), 10)
    END AS published_date,
    CASE WHEN doc->>'type' = 'sec_filing' THEN 'filing' ELSE coalesce(doc->>'date_source', 'unknown') END AS date_from,
    coalesce(doc->'matched_metrics', '[]'::jsonb) AS matched_metrics,
    coalesce(doc->'sentiment'->>'label', '') AS sentiment_label,
    CASE
        WHEN (doc->'sentiment'->>'score') IS NOT NULL AND (doc->'sentiment'->>'score') <> ''
            THEN round((doc->'sentiment'->>'score')::numeric, 3)
        ELSE NULL
    END AS sentiment_score,
    CASE
        WHEN doc->>'type' = 'sec_filing' THEN 'stored'
        WHEN doc->'extraction'->>'status' = 'rejected'
            THEN 'rejected: ' || coalesce(doc->'extraction'->>'reject_reason', '')
        ELSE coalesce(doc->'extraction'->>'status', '')
    END AS text_status
FROM articles
WHERE doc->>'type' IN ('raw_article', 'sec_filing');

-- 6. View: when was each part of a company's prepared data last refreshed
CREATE OR REPLACE VIEW v_data_freshness AS
WITH tickers AS (
    SELECT DISTINCT ticker FROM fundamental_metrics
    UNION
    SELECT DISTINCT doc->>'ticker' FROM articles WHERE doc->>'ticker' IS NOT NULL
)
SELECT
    t.ticker,
    (SELECT left(max(doc->>'last_seen_at'), 10) FROM articles a
        WHERE a.doc->>'type' = 'raw_article' AND a.doc->>'source' = 'searxng_news'
          AND a.doc->>'ticker' = t.ticker)                                                    AS news_last_updated,
    (SELECT left(max(calculated_at)::text, 10) FROM metric_scores ms WHERE ms.ticker = t.ticker)       AS scores_last_updated,
    (SELECT left(max(calculated_at)::text, 10) FROM fundamental_metrics fm WHERE fm.ticker = t.ticker) AS ratios_last_updated,
    (SELECT max(fiscal_year_end) FROM fundamental_metrics fm WHERE fm.ticker = t.ticker)               AS fiscal_year_end,
    (SELECT max(currency) FROM fundamental_metrics fm WHERE fm.ticker = t.ticker)                      AS currency
FROM tickers t;
