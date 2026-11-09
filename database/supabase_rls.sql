-- ============================================================================
-- SUPABASE ONLY: who may read what from the browser. Run after schema.sql, once
-- (python scripts/online/apply_schema.py --target-url "<url>" --rls).
--
-- The web front end (apps/web) runs in the visitor's browser with the public "anon" key, so anyone can
-- see that key. It must therefore be able to READ the prepared results and NOTHING else:
--
--   readable by anon:   fundamental_metrics, metric_scores, screening_runs, v_reference_sources, v_data_freshness
--   not readable:       articles  (full article and filing text; only the two views above expose its links)
--   writable by anon:   nothing   (all writes come from the Python backend over a direct database connection)
--
-- Views run with the privileges of their owner, so v_reference_sources and v_data_freshness can read
-- `articles` for the visitor without the visitor being able to read the table itself.
-- ============================================================================

ALTER TABLE fundamental_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE metric_scores       ENABLE ROW LEVEL SECURITY;
ALTER TABLE screening_runs      ENABLE ROW LEVEL SECURITY;
ALTER TABLE articles            ENABLE ROW LEVEL SECURITY;      -- enabled, and no policy for anon: closed

DROP POLICY IF EXISTS "Public read fundamental_metrics" ON fundamental_metrics;
CREATE POLICY "Public read fundamental_metrics" ON fundamental_metrics FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "Public read metric_scores" ON metric_scores;
CREATE POLICY "Public read metric_scores" ON metric_scores FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "Public read screening_runs" ON screening_runs;
CREATE POLICY "Public read screening_runs" ON screening_runs FOR SELECT TO anon, authenticated USING (true);

-- An earlier draft opened `articles` to everyone. Remove that policy if it was ever created.
DROP POLICY IF EXISTS "Public read articles" ON articles;

REVOKE ALL ON articles FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE ON fundamental_metrics, metric_scores, screening_runs FROM anon, authenticated;

GRANT SELECT ON fundamental_metrics, metric_scores, screening_runs, v_reference_sources, v_data_freshness
    TO anon, authenticated;
