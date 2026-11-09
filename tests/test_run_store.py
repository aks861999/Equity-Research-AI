"""Offline checks for saving screening runs (a fake connection records the SQL; no database needed).
Run: python tests/test_run_store.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from equity_pipeline.agent import run_store  # noqa: E402


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.conn.row


class FakeConn:
    def __init__(self, row=None):
        self.log, self.row = [], row

    def cursor(self):
        return FakeCursor(self)


class FakeDb:
    def __init__(self, row=None):
        self.conn = FakeConn(row)


def test_a_first_run_for_a_ticker_may_start():
    assert run_store.may_start(FakeDb(row=None), "AAPL", "123") == (True, "ok")


def test_the_same_nonce_is_never_run_twice():
    # status, stored nonce, silent for less than the stale limit, finished recently
    assert run_store.may_start(FakeDb(row=("complete", "123", True, False)), "AAPL", "123") == (False, "already_handled")


def test_a_run_in_progress_blocks_a_second_one_unless_it_died():
    assert run_store.may_start(FakeDb(row=("running", "1", True, False)), "AAPL", "2") == (False, "running")
    assert run_store.may_start(FakeDb(row=("running", "1", False, False)), "AAPL", "2") == (True, "ok")   # silent too long


def test_a_fresh_result_is_reused_and_an_old_one_is_not():
    assert run_store.may_start(FakeDb(row=("complete", "1", True, True)), "AAPL", "2") == (False, "recent_result")
    assert run_store.may_start(FakeDb(row=("complete", "1", True, False)), "AAPL", "2") == (True, "ok")
    assert run_store.may_start(FakeDb(row=("error", "1", True, False)), "AAPL", "2") == (True, "ok")


def test_a_reused_result_is_marked_as_the_answer_to_the_new_nonce():
    db = FakeDb()
    run_store.acknowledge(db, "aapl", "999", "RECENT RESULT REUSED")
    sql, params = db.conn.log[-1]
    assert sql.startswith("UPDATE screening_runs SET nonce") and "status = 'complete'" in sql
    assert params == ("999", "RECENT RESULT REUSED", "AAPL")


def test_progress_finish_and_error_write_one_row_per_ticker():
    db = FakeDb()
    run_store.update_progress(db, "aapl", "run1", "step", mode="demo", seed=7, nonce="n")
    run_store.record_error(db, "aapl", "run1", "x" * 5000, nonce="n")
    statements = [s for s, _ in db.conn.log if s.startswith("INSERT INTO screening_runs")]
    assert len(statements) == 2 and all("ON CONFLICT (ticker) DO UPDATE" in s for s in statements)
    assert len(db.conn.log[-1][1][3]) == 2000                  # an error message is cut to 2,000 characters


def test_quality_payload_is_plain_data():
    class Q:
        score, passed_all = 0.91666, False
        checks, details = {"a": True, "b": False}, {"b": "why"}

    assert run_store.quality_payload(Q()) == {"score": 0.9167, "passed_all": False,
                                              "checks": {"a": True, "b": False}, "details": {"b": "why"}}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)
