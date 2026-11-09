"""Offline checks for the SearXNG news fetcher and the date handling. No network, no database.

Run from the project root:   python tests/test_news.py      (or: python -m pytest tests/test_news.py)
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from equity_pipeline.extraction.article_extractor import find_publish_date  # noqa: E402
from equity_pipeline.extraction.run_aggregation import UNKNOWN_DATE_AGE_DAYS, article_age_days  # noqa: E402
from equity_pipeline.sources import searxng_news as sn  # noqa: E402

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def test_parse_published():
    assert sn.parse_published("2026-11-06T14:22:02", NOW) == datetime(2026, 11, 6, 14, 22, 2, tzinfo=timezone.utc)
    assert sn.parse_published("2026-11-06T14:22:02Z", NOW).tzinfo is not None
    assert sn.parse_published(None, NOW) is None
    assert sn.parse_published("", NOW) is None
    assert sn.parse_published("yesterday", NOW) is None
    assert sn.parse_published("2027-01-01T00:00:00", NOW) is None  # future dates are not trusted


def test_select_results_filters_old_empty_and_duplicate():
    raw = [
        {"url": "https://a.com/x?utm_source=feed", "title": "A", "publishedDate": "2026-11-05T10:00:00", "engines": ["duckduckgo news"]},
        {"url": "https://a.com/x", "title": "A again", "publishedDate": None, "engines": ["bing news"]},      # same story
        {"url": "https://b.com/old", "title": "Old", "publishedDate": "2024-01-01T00:00:00", "engines": []},  # > 1 year
        {"url": "https://c.com/undated", "title": "Undated", "publishedDate": None, "engines": ["brave.news"]},
        {"url": "", "title": "No url", "publishedDate": None, "engines": []},
        {"url": "https://d.com/notitle", "title": " ", "publishedDate": None, "engines": []},
    ]
    chosen = sn.select_results(raw, max_results=10, now=NOW)
    assert [c["title"] for c in chosen] == ["A", "Undated"]
    assert chosen[0]["published_at"] is not None and chosen[1]["published_at"] is None


def test_select_results_caps_at_max():
    raw = [{"url": f"https://x.com/{i}", "title": f"T{i}", "publishedDate": None, "engines": []} for i in range(10)]
    assert len(sn.select_results(raw, max_results=3, now=NOW)) == 3


def test_collect_merges_metrics_for_the_same_url_and_uses_the_cache_flag():
    metrics = [
        {"metric_id": "m01", "search_template": '{COMPANY} ("revenue")'},
        {"metric_id": "m02", "search_template": '{COMPANY} ("profit")'},
    ]
    answers = {
        'Acme revenue': [{"url": "https://n.com/1", "title": "Acme revenue and profit both up",
                          "publishedDate": None, "engines": ["bing news"]}],
        'Acme profit': [{"url": "https://n.com/1?utm_medium=x", "title": "Acme revenue and profit both up",
                         "publishedDate": "2026-11-07T08:00:00", "engines": ["duckduckgo news"]}],
    }
    slept = []

    def fake_search(query, base_url=None, engines=sn.ENGINES):
        return answers[query], False

    articles = sn.collect_all_metric_articles("Acme", metrics, max_per_metric=5, search_fn=fake_search,
                                              sleep_fn=slept.append)
    assert len(articles) == 1
    a = articles[0]
    assert {m["metric_id"] for m in a["matched_metrics"]} == {"m01", "m02"}
    assert a["published_at"] is not None, "a later dated hit must fill the missing date"
    assert a["engines"] == ["bing news", "duckduckgo news"]
    assert len(slept) == 2, "queries that really hit SearXNG are spaced out"

    slept.clear()
    sn.collect_all_metric_articles("Acme", metrics, search_fn=lambda q, base_url=None, engines=sn.ENGINES: (answers[q], True),
                                   sleep_fn=slept.append)
    assert slept == [], "queries answered from the cache are not delayed"


def test_short_queries_use_the_key_phrases_without_or_chains():
    template = '{COMPANY} ("return on assets" OR "asset efficiency" OR "asset utilization")'
    assert sn.short_queries("MercadoLibre", template) == ["MercadoLibre return on assets", "MercadoLibre asset efficiency"]
    assert sn.short_queries("MercadoLibre", template, limit=1) == ["MercadoLibre return on assets"]
    assert sn.short_queries("Acme", "{COMPANY} earnings") == ["Acme earnings"]  # no quoted phrase: used as written


def test_second_phrase_only_when_the_first_finds_too_few():
    metric = [{"metric_id": "m12", "search_template": '{COMPANY} ("return on assets" OR "asset efficiency")'}]
    asked = []

    def few(query, base_url=None, engines=sn.ENGINES):
        asked.append(query)
        return [{"url": f"https://n.com/{query}", "title": query, "publishedDate": None, "engines": []}], False

    articles = sn.collect_all_metric_articles("Acme", metric, max_per_metric=3, search_fn=few, sleep_fn=lambda s: None)
    assert asked == ["Acme return on assets", "Acme asset efficiency"] and len(articles) == 2

    asked.clear()

    def many(query, base_url=None, engines=sn.ENGINES):
        asked.append(query)
        return [{"url": f"https://n.com/{i}", "title": f"Acme return on assets story {i}", "publishedDate": None,
                 "engines": []} for i in range(5)], False

    sn.collect_all_metric_articles("Acme", metric, max_per_metric=3, search_fn=many, sleep_fn=lambda s: None)
    assert asked == ["Acme return on assets"], "enough articles from the first phrase: no second query"


def test_title_key_merges_syndicated_copies_but_not_different_stories():
    from equity_pipeline.ingestion.dedup import title_key
    # real titles from the first MELI run
    assert title_key("MercadoLibre Plunged After Earnings. Here's Why I'm Not Selling. | The Motley Fool") == \
        title_key("MercadoLibre Plunged After Earnings. Here's Why I'm Not Selling.")
    assert title_key("MercadoLibre earnings analysis: questions answered and next catalysts By Investing.com") == \
        title_key("MercadoLibre earnings analysis: questions answered and next catalysts")
    assert title_key("MercadoLibre Won't Give Its Inventory to Outside AI Agents - FourWeekMBA") == \
        title_key("MercadoLibre Won't Give Its Inventory to Outside AI Agents")
    # a short headline keeps its tail, so two different stories with the same prefix stay apart
    assert title_key("MercadoLibre Q3 - Revenue Jumps") != title_key("MercadoLibre Q3 - Margin Slips")
    assert title_key(None) == ""


def test_phrase_matching_uses_whole_words():
    from equity_pipeline.extraction.metric_relevance import mentions_any
    assert mentions_any("MercadoLibre ROE hits 64%", ["ROE"])
    assert not mentions_any("MercadoLibre heroes of e-commerce", ["ROE"])
    assert mentions_any("Q3: working capital improved", ["liquidity", "working capital"])
    assert mentions_any("SG&A fell", ["SG&A"])
    assert not mentions_any("anything", None) and not mentions_any(None, ["x"])


def test_metric_relevance_is_judged_on_the_body_not_the_headline():
    from equity_pipeline.extraction import metric_relevance as mr
    phrases = ["liquidity", "working capital"]
    filler = "MercadoLibre reported third-quarter results. " * 40          # > 1000 characters
    assert mr.metric_relevance("MercadoLibre liquidity holds", filler, phrases) == mr.WEIGHT_TITLE_OR_LEDE
    assert mr.metric_relevance("Q3 results", "Working capital rose. " + filler, phrases) == mr.WEIGHT_TITLE_OR_LEDE
    # headline says nothing, the body does: still counts (the user's point)
    assert mr.metric_relevance("Q3 results", filler + " Its liquidity is strong.", phrases) == mr.WEIGHT_IN_BODY
    assert mr.metric_relevance("MercadoLibre Scraper", filler, phrases) == mr.WEIGHT_ABSENT
    assert mr.metric_relevance("x", None, phrases) == mr.WEIGHT_NO_BODY
    assert mr.metric_relevance("x", filler, []) == 1.0, "no phrases: no judgement"


def test_phrases_by_metric_reads_all_phrases():
    from equity_pipeline.extraction.metric_relevance import phrases_by_metric
    m = [{"metric_id": "m22", "search_template": '{COMPANY} ("liquidity" OR "working capital" OR "cash crunch")'}]
    assert phrases_by_metric(m) == {"m22": ["liquidity", "working capital", "cash crunch"]}


def test_nothing_is_dropped_for_keywords_but_mentions_rank_first():
    # the 3 off-topic results come FIRST in SearXNG's order
    raw = [
        {"url": "https://scrapingbee.com/x", "title": "MercadoLibre Scraper", "content": "", "publishedDate": None},
        {"url": "https://fool.com/a", "title": "Advisor exits MercadoLibre position", "content": "", "publishedDate": None},
        {"url": "https://fool.com/b", "title": "MercadoLibre stock buy?", "content": "", "publishedDate": None},
        {"url": "https://n.com/1", "title": "MercadoLibre liquidity strong", "content": "", "publishedDate": None},
        {"url": "https://n.com/2", "title": "MercadoLibre Q3", "content": "working capital up", "publishedDate": None},
        {"url": "https://n.com/3", "title": "MercadoLibre cash crunch fears", "content": "", "publishedDate": None},
    ]
    phrases = ["liquidity", "working capital", "cash crunch"]
    chosen = sn.select_results(raw, 3, now=NOW, phrases=phrases)
    assert [c["url"] for c in chosen] == ["https://n.com/1", "https://n.com/2", "https://n.com/3"]
    everything = sn.select_results(raw, 10, now=NOW, phrases=phrases)
    assert len(everything) == 6, "results that do not name the metric are kept (ranked last), not dropped"
    assert [c["mentions_metric"] for c in everything] == [True, True, True, False, False, False]


def test_dated_results_first_newest_first():
    raw = [
        {"url": "https://a.com", "title": "MercadoLibre liquidity A", "publishedDate": None},
        {"url": "https://b.com", "title": "MercadoLibre liquidity B", "publishedDate": "2026-10-01T00:00:00"},
        {"url": "https://c.com", "title": "MercadoLibre liquidity C", "publishedDate": "2026-11-07T00:00:00"},
    ]
    assert [c["url"] for c in sn.select_results(raw, 3, now=NOW, phrases=["liquidity"])] == \
        ["https://c.com", "https://b.com", "https://a.com"]


def test_same_story_on_two_sites_is_one_article_with_both_metrics():
    metrics = [
        {"metric_id": "m02", "search_template": '{COMPANY} ("operating profit")'},
        {"metric_id": "m05", "search_template": '{COMPANY} ("gross margin")'},
    ]
    story = "MercadoLibre's Profits Fall Again on operating profit and gross margin squeeze"
    answers = {
        "Acme operating profit": [{"url": "https://fool.com/s", "title": story, "publishedDate": None, "engines": []}],
        "Acme gross margin": [{"url": "https://nasdaq.com/s", "title": story + " | Nasdaq", "publishedDate": None, "engines": []}],
    }
    articles = sn.collect_all_metric_articles("Acme", metrics, search_fn=lambda q, base_url=None, engines=sn.ENGINES: (answers[q], True),
                                              sleep_fn=lambda s: None)
    assert len(articles) == 1
    assert [m["metric_id"] for m in articles[0]["matched_metrics"]] == ["m02", "m05"]


def test_risk_metrics_are_skipped_by_default():
    metrics = [{"metric_id": "m01", "theme": "growth"}, {"metric_id": "r02", "theme": "risk"}]
    assert [m["metric_id"] for m in sn.news_metrics(metrics)] == ["m01"]
    assert len(sn.news_metrics(metrics, include_risk=True)) == 2


def test_search_never_sends_time_range():
    # time_range makes SearXNG skip DuckDuckGo and Brave (checked against the user's container).
    import tempfile

    sent = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"url": "https://n.com/1", "title": "One", "engine": "bing news"}]}

    def fake_get(url, params=None, timeout=None):
        sent.update(url=url, params=params)
        return FakeResponse()

    real_get, real_cache = sn.requests.get, sn.CACHE_DIR
    with tempfile.TemporaryDirectory() as tmp:
        sn.requests.get, sn.CACHE_DIR = fake_get, Path(tmp)
        try:
            results, from_cache = sn.search("Acme earnings", base_url="http://fake:1")
            again, cached_again = sn.search("Acme earnings", base_url="http://fake:1")
        finally:
            sn.requests.get, sn.CACHE_DIR = real_get, real_cache
    assert "time_range" not in sent["params"] and sent["params"]["format"] == "json"
    assert sent["params"]["engines"] == sn.ENGINES
    assert (from_cache, cached_again) == (False, True) and again == results


def test_degraded_answers_are_not_cached():
    # A query an engine failed on (rate limit, parsing error) must be asked again, not served all day.
    import tempfile

    class Resp:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    ok = {"results": [{"url": "https://n.com/1", "title": "One", "engine": "bing news"}]}
    degraded = {"results": ok["results"], "unresponsive_engines": [["brave.news", "too many requests"]]}
    empty = {"results": []}
    real_get, real_cache = sn.requests.get, sn.CACHE_DIR
    with tempfile.TemporaryDirectory() as tmp:
        sn.CACHE_DIR = Path(tmp)
        try:
            for payload, label in ((degraded, "degraded"), (empty, "empty")):
                sn.requests.get = lambda url, params=None, timeout=None, p=payload: Resp(p)
                sn.search(f"q {label}", base_url="http://fake:1")
                assert sn.search(f"q {label}", base_url="http://fake:1")[1] is False, f"{label} answer was cached"
            sn.requests.get = lambda url, params=None, timeout=None: Resp(ok)
            sn.search("q ok", base_url="http://fake:1")
            assert sn.search("q ok", base_url="http://fake:1")[1] is True
        finally:
            sn.requests.get, sn.CACHE_DIR = real_get, real_cache


def test_unknown_article_age_is_not_zero():
    age, known = article_age_days(None, NOW)
    assert (age, known) == (UNKNOWN_DATE_AGE_DAYS, False)
    assert article_age_days("garbage", NOW) == (UNKNOWN_DATE_AGE_DAYS, False)
    age, known = article_age_days((NOW - timedelta(days=10)).isoformat(), NOW)
    assert known and abs(age - 10) < 1e-6
    assert article_age_days((NOW + timedelta(days=3)).isoformat(), NOW) == (0.0, True)  # clock skew never goes negative


def test_find_publish_date_from_page_metadata():
    html = """<html><head><title>Q3 results</title>
    <meta property="article:published_time" content="2026-11-05T09:30:00+00:00"/>
    <script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-11-05T09:30:00Z"}</script>
    </head><body><article><p>Acme reported results.</p></article></body></html>"""
    found = find_publish_date(html, "https://example.com/news/acme-q3")
    assert found == datetime(2026, 11, 5, tzinfo=timezone.utc)
    assert find_publish_date("<html><body><p>no date here</p></body></html>", "https://example.com/x") is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)
