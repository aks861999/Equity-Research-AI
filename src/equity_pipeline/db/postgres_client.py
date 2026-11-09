import psycopg2
from psycopg2.extras import Json, RealDictCursor


def _path_expression(field: str) -> str:
    """'ticker' -> (doc->>'ticker');  'extraction.status' -> (doc->'extraction'->>'status').
    The expression must be written exactly like the queries write it, or the index is never used."""
    parts = field.split(".")
    inner = "".join(f"->'{p}'" for p in parts[:-1])
    return f"(doc{inner}->>'{parts[-1]}')"


class PostgresClient:
    def __init__(self, host: str, port: str, db_name: str, user: str, password: str, sslmode: str | None = None):
        """`sslmode` ('require', 'verify-full', ...) is needed for a hosted database such as Supabase;
        leave it empty for the local Docker container."""
        options = dict(host=host, port=port, dbname=db_name, user=user, password=password)
        if sslmode:
            options["sslmode"] = sslmode
        self.conn = psycopg2.connect(**options)
        self.conn.autocommit = True

    def ensure_db(self):
        """Create the table if it doesn't exist."""
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS articles (
                    id TEXT PRIMARY KEY,
                    doc JSONB
                );
            """)
            # Create an index on the JSONB document for faster querying
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_articles_doc ON articles USING GIN (doc);
            """)

    def create_index(self, fields: list, name: str):
        """Creates an expression index on JSONB fields. A field may be a dotted path:
        ["type", "extraction.status"] -> ((doc->>'type'), (doc->'extraction'->>'status'))."""
        sql = f"CREATE INDEX IF NOT EXISTS {name} ON articles ({', '.join(_path_expression(f) for f in fields)})"
        with self.conn.cursor() as cur:
            cur.execute(sql)

    def drop_index(self, name: str):
        with self.conn.cursor() as cur:
            cur.execute(f"DROP INDEX IF EXISTS {name}")

    def count_filing_sections(self, ticker: str, accession: str) -> int:
        """Stored SEC filing sections (with their text) of one filing, by the id prefix
        'filing:<ticker>:<accession>:'. Lets the SEC fetcher skip a stored filing without any request."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM articles WHERE starts_with(id, %s) "
                "AND coalesce(doc->>'body_text', '') <> ''",
                (f"filing:{ticker}:{accession}:",),
            )
            return cur.fetchone()[0]

    def get(self, doc_id: str):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT doc FROM articles WHERE id = %s", (doc_id,))
            row = cur.fetchone()
            return dict(row["doc"]) if row else None

    def upsert(self, doc_id: str, update_fn):
        """Read the document, let `update_fn(existing_or_None)` return the new one, write it back.

        The three steps run in ONE transaction that first takes a lock on this document id, so two
        programs updating the same document queue up instead of overwriting each other's change
        (the second one reads what the first one wrote). Documents with different ids do not wait for
        each other. If `update_fn` raises, nothing is written."""
        with self.conn.cursor() as cur:
            cur.execute("BEGIN")
            try:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (doc_id,))
                cur.execute("SELECT doc FROM articles WHERE id = %s", (doc_id,))
                row = cur.fetchone()
                new_doc = update_fn(dict(row[0]) if row else None)
                new_doc["_id"] = doc_id
                cur.execute("""
                    INSERT INTO articles (id, doc)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO UPDATE
                    SET doc = EXCLUDED.doc;
                """, (doc_id, Json(new_doc)))
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
        return {"ok": True}

    def find(self, selector: dict, limit: int = 200, fields: list = None):
        """Translates your dict selectors into Postgres JSONB queries.

        `fields`: only these top-level fields (plus `_id`) are returned, so a report that needs one
        field does not load every document's full text. Without it the whole document comes back."""
        conditions = []
        where_params = []

        for k, v in selector.items():
            # Handle array element matching (e.g., matched_metrics)
            if k == "matched_metrics" and isinstance(v, dict) and "$elemMatch" in v:
                elem = v["$elemMatch"]
                conditions.append("doc @> %s")
                where_params.append(Json({"matched_metrics": [elem]}))
            # Handle nested dictionary keys like "extraction.status"
            elif "." in k:
                parts = k.split(".")
                # Build nested dict: {"extraction": {"status": v}}
                nested = v
                for p in reversed(parts):
                    nested = {p: nested}
                conditions.append("doc @> %s")
                where_params.append(Json(nested))
            else:
                conditions.append("doc @> %s")
                where_params.append(Json({k: v}))

        if fields:
            select = "jsonb_build_object('_id', doc->'_id'" + "".join(", %s, doc->%s" for _ in fields) + ")"
            select_params = [name for f in fields for name in (f, f)]
        else:
            select, select_params = "doc", []

        where = " AND ".join(conditions) or "TRUE"
        query = f"SELECT {select} FROM articles WHERE {where} LIMIT %s"
        params = select_params + where_params + [limit]

        with self.conn.cursor() as cur:
            cur.execute(query, params)
            return [row[0] for row in cur.fetchall()]
