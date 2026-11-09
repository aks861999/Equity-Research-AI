"""
Create the tables and views of database/schema.sql (and, for Supabase, the read-only rules of
database/supabase_rls.sql) in a database.

    python scripts/online/apply_schema.py                          # the database set in .env (POSTGRES_*)
    python scripts/online/apply_schema.py --target-url "<url>"     # another one; default: TARGET_DATABASE_URL
    python scripts/online/apply_schema.py --target-url "<url>" --rls    # Supabase: also the row-level security

Safe to run again. --rls only works on Supabase (it uses the roles "anon" and "authenticated").
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import psycopg2  # noqa: E402

from equity_pipeline.config import db_settings, target_database_url  # noqa: E402


def connect(url: str | None):
    if url:
        return psycopg2.connect(url)
    s = db_settings()
    options = dict(host=s["host"], port=s["port"], dbname=s["db_name"], user=s["user"], password=s["password"])
    if s.get("sslmode"):
        options["sslmode"] = s["sslmode"]
    return psycopg2.connect(**options)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply the database schema.")
    parser.add_argument("--target-url", help="connection URL; default TARGET_DATABASE_URL, else the .env database")
    parser.add_argument("--rls", action="store_true", help="also apply database/supabase_rls.sql (Supabase only)")
    args = parser.parse_args()

    url = args.target_url or target_database_url() or None
    files = [ROOT / "database" / "schema.sql"] + ([ROOT / "database" / "supabase_rls.sql"] if args.rls else [])
    conn = connect(url)
    conn.autocommit = True
    with conn.cursor() as cur:
        for path in files:
            cur.execute(path.read_text(encoding="utf-8"))
            print(f"applied {path.relative_to(ROOT)}")
    conn.close()
    print("Done. Where: " + ("the target database" if url else "the database set in .env"))


if __name__ == "__main__":
    main()
