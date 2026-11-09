"""Offline checks for the Postgres client (a fake connection records the SQL; no database needed).
Run: python tests/test_postgres_client.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from equity_pipeline.db.postgres_client import PostgresClient, _path_expression  # noqa: E402
from equity_pipeline.ingestion.queries import create_indexes  # noqa: E402


class FakeCursor:
    def __init__(self, log, row=None):
        self.log, self.row = log, row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self, row=None):
        self.log, self.row = [], row

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.log, self.row)


def _client(row=None):
    client = object.__new__(PostgresClient)      # skip __init__: no connection is opened
    client.conn = FakeConn(row)
    return client


def test_index_expressions_match_the_queries():
    assert _path_expression("ticker") == "(doc->>'ticker')"
    assert _path_expression("extraction.status") == "(doc->'extraction'->>'status')"


def test_create_indexes_drops_the_useless_ones_and_makes_the_two_useful_ones():
    client = _client()
    create_indexes(client)
    sql = [s for s, _ in client.conn.log]
    for old in ("extraction_status_idx", "metric_match_idx", "google_link_idx"):
        assert f"DROP INDEX IF EXISTS {old}" in sql
    assert any("type_ticker_idx" in s and "(doc->>'type'), (doc->>'ticker')" in s for s in sql)
    assert any("type_extraction_status_idx" in s and "(doc->'extraction'->>'status')" in s for s in sql)


def test_find_with_fields_returns_only_those_fields():
    client = _client()
    client.find({"type": "raw_article", "ticker": "AAPL"}, limit=5, fields=["date_source", "extraction"])
    sql, params = client.conn.log[-1]
    assert sql.startswith("SELECT jsonb_build_object('_id', doc->'_id', %s, doc->%s, %s, doc->%s) FROM articles")
    assert params[:4] == ["date_source", "date_source", "extraction", "extraction"] and params[-1] == 5
    client.find({"type": "raw_article"}, limit=3)
    assert client.conn.log[-1][0].startswith("SELECT doc FROM articles WHERE doc @> %s")


def test_upsert_runs_in_one_locked_transaction_and_commits():
    client = _client(row=({"n": 1},))
    client.upsert("article:X:1", lambda existing: {**existing, "n": existing["n"] + 1})
    sql = [s for s, _ in client.conn.log]
    assert sql[0] == "BEGIN" and "pg_advisory_xact_lock" in sql[1] and sql[-1] == "COMMIT"
    assert any(s.startswith("INSERT INTO articles") for s in sql)


def test_upsert_rolls_back_and_raises_when_the_update_function_fails():
    client = _client(row=None)

    def broken(existing):
        raise RuntimeError("deleted before it could be applied")

    try:
        client.upsert("article:X:2", broken)
    except RuntimeError:
        pass
    else:
        raise AssertionError("the error must reach the caller")
    sql = [s for s, _ in client.conn.log]
    assert sql[0] == "BEGIN" and sql[-1] == "ROLLBACK" and not any(s.startswith("INSERT") for s in sql)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)
