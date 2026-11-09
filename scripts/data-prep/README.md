# data-prep

Commands for the fundamentals data preparation. Each file is only an entry point; the logic
stays in `src/equity_pipeline` so `main.py`, the agent and the tests can import it.

```
 add a company ──► coverage_report.py ──► companies.yaml ──► run_fundamentals.py ──► Postgres
                          │                                          ▲
                   fetch_fundamentals.py (fills data/sec/<TICKER>.json)
                          │
                   check_inputs.py (after changing a runner)
```

| Script | What it does | Uses |
|---|---|---|
| `fetch_fundamentals.py` | Fetch and cache SEC data for tickers | `sources/sec_fundamentals.py` |
| `coverage_report.py` | Show which of the 36 ratios a company would get, and why any are missing | `sources/coverage.py` |
| `run_fundamentals.py` | Calculate and store the ratios in Postgres | `extraction/run_growth_fundamentals.py`, `extraction/run_risk_fundamentals.py` |
| `fetch_news.py` | Fetch the per-metric news through the local SearXNG container, optionally download the texts, and report how many articles have a publish date | `sources/searxng_news.py`, `extraction/run_extraction.py` |
| `quality_gate.py` | Reject junk among the stored article texts: too short, too long, a cookie page, or never mentioning the company. Rejected articles are never scored. Run before sentiment. `--dry-run` writes nothing. New downloads go through the same gate automatically. | `extraction/quality_gate.py` |
| `data_status.py` | One-screen status per company straight from the database (filings, ratios stored vs 36, fiscal year-end, articles by status, FinBERT scored / waiting, metric scores) and what is missing with the command that fixes it. Read-only. | the database only |
| `check_inputs.py` | Verify the declared inputs match the calculators | `sources/inputs.py`, `tests/test_inputs.py` |

Run from the project root. No `PYTHONPATH` needed.

```cmd
python scripts\data-prep\coverage_report.py NEWTICKER
python scripts\data-prep\run_fundamentals.py
```
