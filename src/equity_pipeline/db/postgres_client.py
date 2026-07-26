import psycopg2
from psycopg2.extras import Json, RealDictCursor

class PostgresClient:
    def __init__(self, host: str, port: str, db_name: str, user: str, password: str):
        self.conn = psycopg2.connect(
            host=host, port=port, dbname=db_name, user=user, password=password
        )
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
        """Creates expression indexes on JSONB fields for faster queries."""
        # Convert ["type", "ticker"] into ((doc->>'type'), (doc->>'ticker'))
        expressions = [f"(doc->>'{f}')" for f in fields]
        sql = f"CREATE INDEX IF NOT EXISTS {name} ON articles ({', '.join(expressions)})"
        
        with self.conn.cursor() as cur:
            cur.execute(sql)

    def get(self, doc_id: str):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT doc FROM articles WHERE id = %s", (doc_id,))
            row = cur.fetchone()
            return dict(row["doc"]) if row else None

    def upsert(self, doc_id: str, update_fn, max_retries: int = 5):
        """Same logic as CouchDB: get -> apply update_fn -> save. Postgres 
        handles this much faster and safer with ON CONFLICT."""
        existing = self.get(doc_id)
        new_doc = update_fn(existing)
        new_doc["_id"] = doc_id

        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO articles (id, doc) 
                VALUES (%s, %s)
                ON CONFLICT (id) DO UPDATE 
                SET doc = EXCLUDED.doc;
            """, (doc_id, Json(new_doc)))
            
        return {"ok": True}

    def find(self, selector: dict, limit: int = 200, fields: list = None):
        """Translates your dict selectors into Postgres JSONB queries."""
        conditions = []
        params = []
        
        for k, v in selector.items():
            # Handle array element matching (e.g., matched_metrics)
            if k == "matched_metrics" and isinstance(v, dict) and "$elemMatch" in v:
                elem = v["$elemMatch"]
                conditions.append("doc @> %s")
                params.append(Json({"matched_metrics": [elem]}))
            # Handle nested dictionary keys like "extraction.status"
            elif "." in k:
                parts = k.split(".")
                # Build nested dict: {"extraction": {"status": v}}
                nested = v
                for p in reversed(parts):
                    nested = {p: nested}
                conditions.append("doc @> %s")
                params.append(Json(nested))
            else:
                conditions.append("doc @> %s")
                params.append(Json({k: v}))
                
        query = f"SELECT doc FROM articles WHERE {' AND '.join(conditions)} LIMIT %s"
        params.append(limit)
        
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            return [row[0] for row in cur.fetchall()]