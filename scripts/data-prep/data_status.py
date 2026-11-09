"""
One-screen status of the data behind every company, read straight from Postgres, with what is missing
and the command that fixes it. Read-only: no network calls, nothing is written.

    python scripts/data-prep/data_status.py                  # every company in config/companies.yaml
    python scripts/data-prep/data_status.py AAPL T            # only these

Per company it shows:
    filings    SEC filing sections stored and the latest filing date
    ratios     growth (of 25) and risk (of 11) ratios stored, the fiscal year-end and currency they come from
    yahoo      Yahoo articles stored / with usable text
    search     search-news articles stored / with usable text
    texts      rejected by the quality gate, failed, waiting for download (pending), set aside for the browser
    scored     texts scored by FinBERT, and usable texts still waiting for it
    scores     per-metric sentiment scores (of 25 growth metrics)
then a list of problems and notes. For the reason a ratio is missing (what the filing does not report), run
scripts/data-prep/coverage_report.py: this script only says WHICH ones are missing from the database.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from equity_pipeline.config import db_settings, load_companies  # noqa: E402
from equity_pipeline.db.postgres_client import PostgresClient  # noqa: E402
from equity_pipeline.sources.inputs import METRIC_INPUTS  # noqa: E402

GROWTH = [m for m in METRIC_INPUTS if m.startswith("m")]
RISK = [m for m in METRIC_INPUTS if m.startswith("r")]


def _rows(db, sql: str):
    """Rows, or None when the query fails (a table or column that does not exist yet)."""
    try:
        with db.conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    except Exception:
        return None


def collect(db, tickers: list[str]) -> dict[str, dict]:
    stats = {t: {"filings": 0, "filings_latest": None, "ids": set(), "fye": None, "currency": None,
                 "yahoo": 0, "yahoo_ok": 0, "search": 0, "search_ok": 0,
                 "rejected": 0, "failed": 0, "pending": 0, "needs_browser": 0,
                 "scored": 0, "unscored": 0, "mscores": 0, "mscores_at": None} for t in tickers}

    for ticker, n, latest in _rows(db, "SELECT doc->>'ticker', count(*), max(doc->>'filing_date') FROM articles "
                                       "WHERE doc->>'type' = 'sec_filing' GROUP BY 1") or []:
        if ticker in stats:
            stats[ticker].update(filings=n, filings_latest=latest)

    ratio_rows = _rows(db, "SELECT ticker, metric_id, fiscal_year_end, currency FROM fundamental_metrics")
    if ratio_rows is None:  # an older table without the period columns
        ratio_rows = [(t, m, None, None) for t, m in (_rows(db, "SELECT ticker, metric_id FROM fundamental_metrics") or [])]
    for ticker, metric_id, fye, currency in ratio_rows:
        if ticker in stats:
            s = stats[ticker]
            s["ids"].add(metric_id)
            s["fye"] = s["fye"] or fye
            s["currency"] = s["currency"] or currency

    article_rows = _rows(db, "SELECT doc->>'ticker', doc->>'source', coalesce(doc->'extraction'->>'status', 'none'), "
                             "(jsonb_typeof(doc->'sentiment') = 'object'), count(*) FROM articles "
                             "WHERE doc->>'type' = 'raw_article' GROUP BY 1, 2, 3, 4") or []
    for ticker, source, status, scored, n in article_rows:
        if ticker not in stats:
            continue
        s = stats[ticker]
        key = "yahoo" if source == "yahoo_finance" else "search"
        s[key] += n
        if status == "success":
            s[key + "_ok"] += n
            s["scored" if scored else "unscored"] += n
        elif status in ("rejected", "failed", "pending", "needs_browser"):
            s[status] += n

    for ticker, n, at in _rows(db, "SELECT ticker, count(*), max(calculated_at) FROM metric_scores GROUP BY 1") or []:
        if ticker in stats:
            stats[ticker].update(mscores=n, mscores_at=str(at)[:10] if at else None)
    return stats


def problems(ticker: str, s: dict, company: dict) -> list[tuple[str, str]]:
    """[(level, message)]: level '!' is something missing or stale, 'i' is a note."""
    out: list[tuple[str, str]] = []
    if company.get("sec_form_type") and not s["filings"]:
        out.append(("!", f"no SEC filings stored -> python -m equity_pipeline.main --ticker {ticker}"))

    growth_n = sum(1 for m in s["ids"] if m.startswith("m"))
    risk_n = sum(1 for m in s["ids"] if m.startswith("r"))
    if not s["ids"]:
        out.append(("!", "no ratios stored -> python scripts\\data-prep\\run_fundamentals.py"))
    else:
        missing = [m for m in GROWTH + RISK if m not in s["ids"]]
        if missing:
            out.append(("i", f"{len(missing)} ratio(s) not stored: {', '.join(missing)} "
                             f"(the reason is in: python scripts\\data-prep\\coverage_report.py {ticker})"))
        if not s["fye"]:
            out.append(("!", "the ratios carry no fiscal year-end -> re-run python scripts\\data-prep\\run_fundamentals.py"))

    if not s["yahoo"]:
        out.append(("!", f"no Yahoo articles stored -> python -m equity_pipeline.main --ticker {ticker}"))
    if not s["search"]:
        out.append(("!", f"no search news stored -> python scripts\\data-prep\\fetch_news.py {ticker} --extract"))
    if s["pending"]:
        out.append(("!", f"{s['pending']} article(s) waiting for their text -> "
                         f"python scripts\\data-prep\\fetch_news.py {ticker} --extract"))
    if s["needs_browser"]:
        out.append(("i", f"{s['needs_browser']} page(s) set aside for the browser fallback (read by "
                         f"fetch_news.py --extract on your PC, never online)"))
    if s["unscored"]:
        out.append(("!", f"{s['unscored']} usable text(s) waiting for FinBERT -> python -m equity_pipeline.main --run-sentiment"))
    if s["scored"] and not s["mscores"]:
        out.append(("!", "scored articles but no per-metric scores -> python -m equity_pipeline.main --run-aggregation"))
    elif s["mscores"] and s["mscores"] < len(GROWTH):
        out.append(("i", f"only {s['mscores']} of {len(GROWTH)} growth metrics have a sentiment score "
                         f"(no scored article matched the others)"))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Show what data is stored per company and what is missing.")
    parser.add_argument("tickers", nargs="*", help="default: every ticker in config/companies.yaml")
    args = parser.parse_args()

    companies = load_companies()
    wanted = [t.upper() for t in args.tickers]
    companies = [c for c in companies if not wanted or c["ticker"].upper() in wanted]
    if not companies:
        sys.exit("No matching company in config/companies.yaml.")

    db = PostgresClient(**db_settings())  # no ensure_db(): this script never writes, not even DDL
    stats = collect(db, [c["ticker"] for c in companies])

    print(f"\n{'ticker':7} {'filings':>8} {'latest':>10}  {'growth':>6} {'risk':>5} {'FY end':>10} {'cur':>4}  "
          f"{'yahoo':>9} {'search':>9}  {'rej':>4} {'fail':>4} {'pend':>4} {'brwsr':>5}  {'scored':>6} {'wait':>5} {'scores':>6}")
    for c in companies:
        t, s = c["ticker"], stats[c["ticker"]]
        growth_n = sum(1 for m in s["ids"] if m.startswith("m"))
        risk_n = sum(1 for m in s["ids"] if m.startswith("r"))
        print(f"{t:7} {s['filings']:>8} {(s['filings_latest'] or '-'):>10}  "
              f"{f'{growth_n}/{len(GROWTH)}':>6} {f'{risk_n}/{len(RISK)}':>5} {(s['fye'] or '-'):>10} {(s['currency'] or '-'):>4}  "
              f"{f'{s['yahoo_ok']}/{s['yahoo']}':>9} {f'{s['search_ok']}/{s['search']}':>9}  "
              f"{s['rejected']:>4} {s['failed']:>4} {s['pending']:>4} {s['needs_browser']:>5}  "
              f"{s['scored']:>6} {s['unscored']:>5} {f'{s['mscores']}/{len(GROWTH)}':>6}")
    print("\nyahoo / search = usable texts / articles stored.  rej = rejected by the quality gate, fail = could not be "
          "read, pend = waiting to be downloaded,\nbrwsr = set aside for the browser, scored = texts scored by FinBERT, "
          "wait = usable texts not scored yet, scores = per-metric sentiment scores.")

    print("\nPROBLEMS (!) AND NOTES (i)")
    all_good = True
    for c in companies:
        found = problems(c["ticker"], stats[c["ticker"]], c)
        if not found:
            continue
        all_good = False
        print(f"  {c['ticker']}")
        for level, message in found:
            print(f"    {level} {message}")
    if all_good:
        print("  nothing missing.")


if __name__ == "__main__":
    main()
