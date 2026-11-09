"""
Check the article texts already stored in Postgres with the quality gate and reject the junk.

    python scripts/data-prep/quality_gate.py --dry-run     # show what would be rejected, write nothing
    python scripts/data-prep/quality_gate.py               # apply it to every ticker
    python scripts/data-prep/quality_gate.py MELI          # only this ticker

The three rules (extraction/quality_gate.py): too short or too long, a short cookie page, an
article that never mentions the company. A rejected article keeps a small marker row (status
"rejected" + reason), its text is removed and any sentiment is cleared, so it is never scored.
Texts that came through the browser fallback and pass the gate also have their page navigation
stripped (extraction/body_cleaner.py). New downloads go through the same gate automatically
during extraction; this script is for what was stored before the gate existed. Safe to run again.

Run it BEFORE `python -m equity_pipeline.main --run-sentiment`. Needs the Postgres container only.
"""
import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from equity_pipeline.config import get_company  # noqa: E402
from equity_pipeline.extraction.body_cleaner import clean_markdown_body, has_article_text  # noqa: E402
from equity_pipeline.extraction.quality_gate import assess  # noqa: E402
from equity_pipeline.main import get_db  # noqa: E402

logger = logging.getLogger("data-prep")

SELECT = """
    SELECT id, doc->>'ticker', doc->>'title', doc->>'body_text', doc->'extraction'->>'fetch_method'
    FROM articles
    WHERE doc->>'type' = 'raw_article'
      AND doc->'extraction'->>'status' = 'success'
      {ticker_filter}
    ORDER BY doc->>'ticker', id
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply the quality gate to the stored article texts.")
    parser.add_argument("tickers", nargs="*", help="default: all tickers")
    parser.add_argument("--dry-run", action="store_true", help="show the effect, write nothing")
    parser.add_argument("--list", type=int, default=40, help="how many rejected titles to print")
    args = parser.parse_args()

    db = get_db()
    tickers = [t.upper() for t in args.tickers]

    with db.conn.cursor() as cur:
        cur.execute(SELECT.format(ticker_filter="AND doc->>'ticker' = ANY(%s)" if tickers else ""),
                    (tickers,) if tickers else None)
        rows = cur.fetchall()
    if not rows:
        print("No stored article texts to check.")
        return

    names = {}
    reasons = Counter()
    kept = cleaned = 0
    shown = 0
    for doc_id, ticker, title, body, method in rows:
        if ticker not in names:
            try:
                names[ticker] = get_company(ticker)["primary_search_name"]
            except (ValueError, KeyError):
                names[ticker] = None
        reason = assess(title, body, names[ticker], ticker)

        if reason:
            reasons[reason] += 1
            if shown < args.list:
                print(f"  REJECT {reason:22} {ticker:6} {len(body or ''):>8,} chars  {(title or '')[:62]}")
                shown += 1
            if not args.dry_run:
                def reject(existing, reason=reason):
                    existing["extraction"] = {**(existing.get("extraction") or {}), "status": "rejected",
                                              "reject_reason": reason, "body_char_count": 0}
                    existing["body_text"] = None
                    existing["sentiment"] = None
                    return existing
                db.upsert(doc_id, reject)
            continue

        kept += 1
        if method == "crawl4ai":  # browser pages are whole-page markdown; a cleaned text is left unchanged
            text = clean_markdown_body(body)
            if has_article_text(text) and text != body:
                cleaned += 1
                if not args.dry_run:
                    def strip_navigation(existing, text=text):
                        existing["body_text"] = text
                        existing["extraction"]["body_char_count"] = len(text)
                        existing["sentiment"] = None  # the text changed: any earlier score is stale
                        return existing
                    db.upsert(doc_id, strip_navigation)

    rejected = sum(reasons.values())
    print(f"\n{len(rows)} texts checked: {kept} kept ({cleaned} of them browser pages with navigation stripped), "
          f"{rejected} rejected")
    for reason, n in reasons.most_common():
        print(f"   {n:4}  {reason}")
    print("DRY RUN: nothing was written." if args.dry_run else "Done. Next: python -m equity_pipeline.main --run-sentiment")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
