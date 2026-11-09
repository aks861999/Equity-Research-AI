"""Offline checks for the online demo mode (APP_MODE=demo). Run: python tests/test_demo_mode.py

The demo reads the prepared data (ratios, search news, sentiment scores) from the shared database and
refreshes only SEC EDGAR + Yahoo Finance; article text is read with curl_cffi only.
"""
import asyncio
import os
import subprocess
import sys
import types
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@contextmanager
def env(**values):
    old = {k: os.environ.get(k) for k in values}
    os.environ.update({k: v for k, v in values.items() if v is not None})
    for k, v in values.items():
        if v is None:
            os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_app_mode_defaults_to_full_and_rejects_unknown_values():
    from equity_pipeline.config import app_mode, is_demo
    with env(APP_MODE=None):
        assert app_mode() == "full" and not is_demo()
    with env(APP_MODE="DEMO "):
        assert app_mode() == "demo" and is_demo()
    with env(APP_MODE="something-else"):
        assert app_mode() == "full"


def test_the_demo_import_path_needs_no_torch_no_crawl4ai_no_openbb():
    """What the online app imports must not pull in the heavy libraries the demo install leaves out."""
    code = (
        "import os, sys; os.environ['APP_MODE']='demo'; sys.path.insert(0, 'src');"
        "import equity_pipeline.agent.screening_agent, equity_pipeline.agent.data_access, "
        "equity_pipeline.agent.pipeline_runner, equity_pipeline.agent.evaluation, equity_pipeline.config;"
        "heavy = [m for m in ('torch', 'transformers', 'crawl4ai', 'playwright', 'openbb') if m in sys.modules];"
        "print('HEAVY:' + ','.join(heavy))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-600:]
    assert "HEAVY:" in out.stdout and out.stdout.strip().endswith("HEAVY:"), out.stdout[-300:]


def test_requirements_leave_out_the_heavy_packages_of_the_online_app():
    packages = {line.split("#")[0].strip().lower() for line in (ROOT / "requirements.txt").read_text().splitlines()}
    for heavy in ("torch", "transformers", "crawl4ai", "openbb-sec", "playwright"):
        assert heavy not in packages, heavy
    for needed in ("streamlit", "pydantic-ai", "psycopg2-binary", "curl_cffi", "trafilatura", "yfinance"):
        assert needed in packages, needed


def _blocked_page_extractor(allow_browser):
    """Runs the extractor on a page curl_cffi cannot read; crawl4ai is replaced by a tripwire."""
    from equity_pipeline.extraction import article_extractor as ae

    async def blocked(self, url):
        return ae.FetchResult(url=url, success=False, error="Blocked (status=403)")

    def tripwire():
        raise RuntimeError("crawl4ai was loaded")

    real = (ae.CurlCffiFetcher.fetch, ae._load_crawl4ai, ae._browser_unavailable)
    ae.CurlCffiFetcher.fetch, ae._load_crawl4ai, ae._browser_unavailable = blocked, tripwire, False
    try:
        return asyncio.run(ae.fetch_and_extract_with_date(
            "https://news.example/a", ae.ExtractorConfig(allow_browser_fallback=allow_browser)))
    finally:
        ae.CurlCffiFetcher.fetch, ae._load_crawl4ai, ae._browser_unavailable = real


def test_demo_never_loads_the_browser_and_sets_the_page_aside():
    status, method, body, _date = _blocked_page_extractor(allow_browser=False)
    assert (status, method, body) == ("needs_browser", "browser_not_allowed", None)


def test_full_mode_does_try_the_browser_fallback():
    try:
        _blocked_page_extractor(allow_browser=True)
    except RuntimeError as exc:
        assert "crawl4ai was loaded" in str(exc)
    else:
        raise AssertionError("full mode must attempt the browser fallback")


def test_the_extractor_config_follows_the_mode():
    from equity_pipeline.extraction.article_extractor import ExtractorConfig
    with env(APP_MODE="demo"):
        assert ExtractorConfig().allow_browser_fallback is False
    with env(APP_MODE="full"):
        assert ExtractorConfig().allow_browser_fallback is True


def test_needs_browser_articles_are_retried_by_the_backend_only():
    from equity_pipeline.extraction.run_extraction import find_pending_articles

    class Db:
        def __init__(self):
            self.selectors = []

        def find(self, selector, limit=200):
            self.selectors.append(selector)
            return []

    with env(APP_MODE="demo"):
        d = Db()
        find_pending_articles(d, ticker="MELI", source="yahoo_finance")
        assert [s["extraction.status"] for s in d.selectors] == ["pending"]
        assert d.selectors[0]["ticker"] == "MELI" and d.selectors[0]["source"] == "yahoo_finance"
    with env(APP_MODE="full"):
        d = Db()
        find_pending_articles(d)
        assert [s["extraction.status"] for s in d.selectors] == ["pending", "needs_browser"]


def test_the_extraction_loop_marks_needs_browser_and_does_not_count_it_as_failed():
    from equity_pipeline.extraction import run_extraction as re_

    docs = [{"_id": "a", "url": "https://news.example/a", "title": "t", "ticker": "MELI"}]
    applied = []
    real = (re_.find_pending_articles, re_.fetch_and_extract_with_date, re_.apply_extraction_result)

    async def fake_fetch(url, config):
        return "needs_browser", "browser_not_allowed", None, None

    re_.find_pending_articles = lambda db, limit=200, ticker=None, source=None: docs
    re_.fetch_and_extract_with_date = fake_fetch
    re_.apply_extraction_result = lambda db, doc_id, status, method, body, **kw: applied.append((doc_id, status, method))
    try:
        result = asyncio.run(re_.run_pending_extractions(None, delay_seconds=0))
    finally:
        re_.find_pending_articles, re_.fetch_and_extract_with_date, re_.apply_extraction_result = real
    assert applied == [("a", "needs_browser", "browser_not_allowed")]
    assert result["needs_browser"] == 1 and result["failed"] == 0 and result["succeeded"] == 0


def _patched_pipeline(calls):
    from equity_pipeline.agent import pipeline_runner as pr

    async def fake_extract(db, batch_limit=200, ticker=None, source=None, **kw):
        calls.append(("extraction", ticker, source))
        return {"processed": 0}

    def boom(name):
        def f(*a, **k):
            raise AssertionError(f"{name} must not run in demo mode")
        return f

    patches = {
        "get_company": lambda t: {"ticker": t, "primary_search_name": "Acme", "sec_form_type": "8-K"},
        "_ingest_sec": lambda db, c, check=None: calls.append(("sec", c["ticker"])) or 2,
        "_ingest_yahoo": lambda db, c, check=None: calls.append(("yahoo", c["ticker"])) or 3,
        "run_pending_extractions": fake_extract,
        "find_pending_articles": lambda db, limit=200, ticker=None, source=None, **kw: [],
        "_ingest_searxng_news": boom("searxng"),
        "run_metric_aggregation": boom("aggregation"),
        "run_fundamental_extraction": lambda db, tickers=None: calls.append(("growth", tuple(tickers))) or {},
        "run_risk_fundamental_extraction": lambda db, tickers=None: calls.append(("risk", tuple(tickers))) or {},
    }
    return pr, patches


def test_the_demo_pipeline_runs_only_sec_yahoo_and_curl_extraction_for_the_ticker():
    calls = []
    pr, patches = _patched_pipeline(calls)
    patches["run_metric_aggregation"] = lambda db: (_ for _ in ()).throw(AssertionError("aggregation must not run in demo"))
    patches["run_fundamental_extraction"] = lambda db, tickers=None: (_ for _ in ()).throw(AssertionError("ratios must not run in demo"))
    patches["run_risk_fundamental_extraction"] = lambda db, tickers=None: (_ for _ in ()).throw(AssertionError("ratios must not run in demo"))
    real = {k: getattr(pr, k) for k in patches}
    for k, v in patches.items():
        setattr(pr, k, v)
    try:
        counts = pr.run_live_pipeline(None, "ACME", include_news=True, mode="demo")   # include_news is ignored in demo
    finally:
        for k, v in real.items():
            setattr(pr, k, v)
    assert calls == [("sec", "ACME"), ("yahoo", "ACME"), ("extraction", "ACME", "yahoo_finance")]
    assert counts["mode"] == "demo" and counts["sec_filings_ingested"] == 2 and counts["yahoo_articles_ingested"] == 3
    for step in ("news_articles_ingested", "sentiment", "aggregation", "growth_fundamentals", "risk_fundamentals"):
        assert str(counts[step]).startswith("skipped"), step


def test_the_full_pipeline_is_scoped_to_the_selected_ticker():
    calls = []
    pr, patches = _patched_pipeline(calls)
    patches["run_metric_aggregation"] = lambda db: calls.append(("aggregation",)) or {}
    fake_sentiment = types.ModuleType("equity_pipeline.extraction.run_sentiment")
    fake_sentiment.run_pending_sentiments = lambda db, batch_limit=0, ticker=None: calls.append(("sentiment", ticker)) or {}
    real = {k: getattr(pr, k) for k in patches}
    real_mod = sys.modules.get("equity_pipeline.extraction.run_sentiment")
    for k, v in patches.items():
        setattr(pr, k, v)
    sys.modules["equity_pipeline.extraction.run_sentiment"] = fake_sentiment
    try:
        pr.run_live_pipeline(None, "ACME", include_news=False, mode="full")
    finally:
        for k, v in real.items():
            setattr(pr, k, v)
        if real_mod is None:
            sys.modules.pop("equity_pipeline.extraction.run_sentiment", None)
        else:
            sys.modules["equity_pipeline.extraction.run_sentiment"] = real_mod
    assert ("extraction", "ACME", None) in calls and ("sentiment", "ACME") in calls
    assert ("growth", ("ACME",)) in calls and ("risk", ("ACME",)) in calls


def _levels(notices):
    return [(n["level"], n["text"]) for n in notices]


def test_notice_says_nothing_new_when_everything_fetched_is_already_stored():
    from equity_pipeline.agent.pipeline_runner import live_data_notices
    sec = {"feed_ok": True, "filings_checked": 3, "sections_new": 0, "sections_stored": 5}
    yahoo = {"ok": True, "fetched": 10, "new": 0}
    (sec_level, sec_text), (yahoo_level, yahoo_text) = _levels(live_data_notices(sec, yahoo))
    assert sec_level == yahoo_level == "info"
    assert "already stored" in sec_text and "nothing new" in sec_text and "3 recent filings" in sec_text
    assert "fetched 10 headlines" in yahoo_text and "already stored" in yahoo_text and "nothing new" in yahoo_text


def test_notice_reports_new_items_and_unreachable_sources_differently():
    from equity_pipeline.agent.pipeline_runner import live_data_notices
    sec = {"feed_ok": True, "filings_checked": 4, "sections_new": 2, "sections_stored": 5}
    yahoo = {"ok": True, "fetched": 12, "new": 3}
    assert _levels(live_data_notices(sec, yahoo))[0][0] == "success" and "2 new filing section" in _levels(live_data_notices(sec, yahoo))[0][1]
    assert _levels(live_data_notices(sec, yahoo))[1][0] == "success" and "3 new headline" in _levels(live_data_notices(sec, yahoo))[1][1]
    # unreachable is a WARNING, never "nothing new"
    down = live_data_notices({"feed_ok": False}, {"ok": False, "fetched": 0, "new": 0})
    assert [level for level, _ in _levels(down)] == ["warning", "warning"]
    assert all("could not be reached" in text and "already stored" in text for _, text in _levels(down))
    assert not any("nothing new" in text for _, text in _levels(down))
    # no form type configured, and an empty answer
    assert "skipped" in _levels(live_data_notices({"skipped": "no sec_form_type configured"}, {"ok": True}))[0][1]
    assert "no recent filings" in _levels(live_data_notices({"feed_ok": True, "filings_checked": 0}, {"ok": True}))[0][1]


def test_the_live_run_puts_the_notices_in_its_result():
    calls = []
    pr, patches = _patched_pipeline(calls)

    def sec(db, c, check=None):
        check.update({"feed_ok": True, "filings_checked": 2, "sections_new": 0, "sections_stored": 4})
        return 0

    def yahoo(db, c, check=None):
        check.update({"ok": True, "fetched": 8, "new": 0})
        return 0

    patches["_ingest_sec"], patches["_ingest_yahoo"] = sec, yahoo
    real = {k: getattr(pr, k) for k in patches}
    for k, v in patches.items():
        setattr(pr, k, v)
    try:
        counts = pr.run_live_pipeline(None, "ACME", mode="demo")
    finally:
        for k, v in real.items():
            setattr(pr, k, v)
    assert counts["live_check"]["sec"]["sections_stored"] == 4 and counts["live_check"]["yahoo"]["fetched"] == 8
    assert [n["level"] for n in counts["notices"]] == ["info", "info"]
    assert counts["sec_filings_ingested"] == 0 and counts["yahoo_articles_ingested"] == 0


def test_ingest_helpers_fill_the_check_and_survive_a_failing_source():
    from equity_pipeline.agent import pipeline_runner as pr

    class Db:
        def get(self, doc_id):
            return {"stored": True}                       # every Yahoo article is already stored

    real = (pr.fetch_sec_filings, pr.fetch_yahoo_news)
    try:
        pr.fetch_yahoo_news = lambda ticker, stats=None, **kw: (stats.update({"ok": True, "fetched": 2}) or
                                                                  [{"title": "a", "url": "https://x/1", "published_at": None},
                                                                   {"title": "b", "url": "https://x/2", "published_at": None}])
        check = {}
        assert pr._ingest_yahoo(Db(), {"ticker": "ACME"}, check) == 0
        assert check == {"ok": True, "fetched": 2, "new": 0}

        def boom(*a, **k):
            raise ConnectionError("sec.gov unreachable")
        pr.fetch_sec_filings = boom
        sec_check = {}
        assert pr._ingest_sec(Db(), {"ticker": "ACME", "sec_form_type": "8-K"}, sec_check) == 0
        assert sec_check["feed_ok"] is False and "unreachable" in sec_check["error"]
        assert pr._ingest_sec(Db(), {"ticker": "ACME"}, (skip := {})) == 0 and skip == {"skipped": "no sec_form_type configured"}
    finally:
        pr.fetch_sec_filings, pr.fetch_yahoo_news = real


def test_the_sec_fetcher_counts_stored_and_new_sections_and_reports_a_dead_feed():
    from equity_pipeline.sources import sec_edgar as se

    real = (se.requests.get, se.parse_sec_index_page, se._extract_ex99_text, se.REQUEST_DELAY)

    class Resp:
        def __init__(self, status): self.status_code, self.content = status, b""

    # a dead feed
    se.requests.get = lambda *a, **k: Resp(503)
    try:
        stats = {}
        assert se.fetch_sec_filings("ACME", "8-K", stats=stats) == [] and stats["feed_ok"] is False
    finally:
        se.requests.get = real[0]

    # a feed whose two filings are both already stored, and one that is new
    class Entry:
        def __init__(self, n): self.title, self.link, self.updated = f"8-K filing {n}", f"https://sec/idx/{n}-index.htm", "2026-11-0%dT10:00:00-05:00" % n

    class Feed:
        entries = [Entry(4), Entry(3), Entry(2)]

    class FakeDb:
        def get(self, doc_id):
            return {"body_text": "stored"} if ("0000000004" in doc_id or "0000000003" in doc_id) else None

    real_parse, real_acc = se.feedparser.parse, se._extract_accession   # restored in the finally below
    se.requests.get = lambda *a, **k: Resp(200)
    se.feedparser.parse = lambda content: Feed()
    se.parse_sec_index_page = lambda link: {"ex": {"doc_type": "EX-99.1", "url": "https://sec/ex.htm", "filename": "ex.htm"}}
    se._extract_ex99_text = lambda *a, **k: "Revenue was up. " * 20
    se._extract_accession = lambda link: "000000000" + link.split("/")[-1].split("-")[0]
    se.REQUEST_DELAY = 0
    try:
        stats = {}
        new = se.fetch_sec_filings("ACME", "8-K", db=FakeDb(), lookback_days=3650, stats=stats)
    finally:
        se.requests.get, se.parse_sec_index_page, se._extract_ex99_text, se.REQUEST_DELAY = real
        se.feedparser.parse, se._extract_accession = real_parse, real_acc
    assert len(new) == 1 and stats["feed_ok"] is True
    assert stats["filings_checked"] == 3 and stats["sections_new"] == 1 and stats["sections_stored"] == 2, stats


def test_a_stored_filing_is_skipped_before_its_index_page_is_downloaded():
    from equity_pipeline.sources import sec_edgar as se

    class Entry:
        link, updated = "https://sec/idx/0000000004-index.htm", "2026-11-04T10:00:00-05:00"

    class DbWithCount:
        def count_filing_sections(self, ticker, accession):
            return 2 if accession == "0000000004" else 0

    fetched = []
    real = (se.parse_sec_index_page, se._extract_accession, se.REQUEST_DELAY)
    se.parse_sec_index_page = lambda link: fetched.append(link) or {}
    se._extract_accession = lambda link: "0000000004"
    se.REQUEST_DELAY = 0
    try:
        stats = {}
        out = se._process_one_filing("ACME", Entry(), datetime_min(), "8-K", db=DbWithCount(), stats=stats)
    finally:
        se.parse_sec_index_page, se._extract_accession, se.REQUEST_DELAY = real
    assert out == [] and fetched == [], "the index page of a stored filing must not be requested"
    assert stats == {"sections_stored": 2}


def datetime_min():
    from datetime import datetime
    return datetime(2000, 1, 1)


def test_the_freshness_notice_reads_dates_and_survives_missing_tables():
    from equity_pipeline.agent import data_access as da

    answers = {"searxng_news": "2026-11-08T20:10:25+00:00", "metric_scores": "2026-11-08 21:23:50.6",
               "fundamental_metrics": "2026-11-08 21:00:00"}

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params):
            if "fiscal_year_end" in sql and "max(fiscal_year_end)" in sql:
                self.value = "2025-12-31"
            else:
                self.value = next((v for k, v in answers.items() if k in sql), None)
        def fetchone(self): return (self.value,)

    class Conn:
        def cursor(self): return Cur()

    class Db:
        conn = Conn()

    fresh = da.fetch_data_freshness(Db(), "MELI")
    assert fresh == {"news_last_updated": "2026-11-08", "scores_last_updated": "2026-11-08",
                     "ratios_last_updated": "2026-11-08", "fiscal_year_end": "2025-12-31"}

    class BrokenConn:
        def cursor(self): raise RuntimeError("relation does not exist")

    class BrokenDb:
        conn = BrokenConn()

    assert set(da.fetch_data_freshness(BrokenDb(), "MELI").values()) == {None}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)
