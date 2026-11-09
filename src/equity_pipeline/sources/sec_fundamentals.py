"""
Fundamentals from SEC filings, through OpenBB's `sec` provider (free, no API key).

OpenBB returns standardised annual income statements and balance sheets in full
currency units, with the reporting currency on every row. The calculators read
those field names directly (`total_revenue`, `total_assets`, ...).

Adding a company needs no code. Every input the calculators read goes through the
same chain, so a gap is closed once for all companies:

    1. the OpenBB field
    2. ordered raw XBRL tags from the SEC companyfacts API   (FALLBACK_TAGS)
    3. a derivation from other fields                        (e.g. liabilities = L&E - equity)
    4. otherwise left empty: "not found", never guessed as 0

Every value that did not come straight from OpenBB is recorded in the row's
`_sources`, and basic sanity checks (balance sheet identity, gross profit <= revenue,
...) are recorded in `_warnings`. Run the coverage report to see both:

    python scripts/data-prep/coverage_report.py NEWTICKER

Rules that were checked against raw XBRL tags (AAPL, JPM, XOM, WMT, NVDA, T, F,
DUK, PFE, KO, MELI, GLBE and more):

* `depreciation_and_amortization` is used as OpenBB gives it. Do not add
  `amortization_expense`: for AAPL, NVDA and JPM it is already inside that total.
  Known limit: GLBE tags only depreciation under that name, so its D&A is low.
* `long_term_debt` from OpenBB is the non-current part for most filers (AAPL, WMT,
  KO...) but the total including the current part for others (T, MELI). The current
  part is removed only when OpenBB's figure equals the filer's total-debt tag.
* Debt is left empty (unknown) when neither OpenBB nor any debt tag has it AND the
  filer reports interest expense. If there are no debt facts and no interest expense
  at all, the company is treated as debt-free (recorded as `assumed_zero`).
* Interest expense falls back to the filer's cash interest paid as a labelled proxy.

Results are cached per company in data/sec/<TICKER>.json (git-ignored).

    python scripts/data-prep/fetch_fundamentals.py MELI AAPL --refresh
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date
from pathlib import Path

import numpy as np
import requests

from ..config import ROOT_DIR, load_companies, sec_user_agent
from .inputs import FALLBACK_TAGS, INPUTS

log = logging.getLogger(__name__)

CACHE_DIR = ROOT_DIR / "data" / "sec"
CACHE_MAX_AGE_DAYS = 7
SCHEMA_VERSION = 3  # bump when the fallback rules change: older cache files are refetched
YEARS_FETCHED = 4  # growth metrics need the latest fiscal year and two before it
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F"}

# Interest expense, best first. The proxy is cash interest paid: close to expense for most
# filers, but not the same thing, so it is only used when no expense tag exists.
INTEREST_TAGS = ["InterestExpenseDebt", "InterestAndDebtExpense", "InterestExpense",
                 "InterestExpenseNonoperating", "InterestExpenseAndOtherFinancialLosses"]
INTEREST_PROXY_TAGS = ["InterestPaidNet", "InterestPaid"]

# Raw-tag fallbacks per input live in inputs.py (the single declared list of what we need).

_cik_cache: dict[str, str] = {}
_facts_cache: dict[str, dict] = {}


# ---------------------------------------------------------------- raw XBRL facts
def _sec_get(url: str) -> dict:
    ua = sec_user_agent()
    if not ua:
        raise RuntimeError('Set SEC_USER_AGENT in .env ("Your Name your.email@example.com"); SEC requires it.')
    r = requests.get(url, headers={"User-Agent": ua}, timeout=30)
    r.raise_for_status()
    return r.json()


def _cik(symbol: str) -> str:
    if not _cik_cache:
        for row in _sec_get("https://www.sec.gov/files/company_tickers.json").values():
            _cik_cache[row["ticker"].upper()] = f"{int(row['cik_str']):010d}"
    return _cik_cache[symbol.upper()]  # KeyError: not an SEC filer


def _fact(symbol: str, tag: str, period_end: str, currency: str) -> float | None:
    """Annual value of a raw tag for the fiscal year ending `period_end` (latest filing wins)."""
    if symbol not in _facts_cache:
        _facts_cache[symbol] = _sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{_cik(symbol)}.json")
    facts = _facts_cache[symbol].get("facts", {})
    for taxonomy in ("us-gaap", "ifrs-full"):
        best = None
        for e in facts.get(taxonomy, {}).get(tag, {}).get("units", {}).get(currency, []):
            if e.get("end") != period_end or e.get("form") not in ANNUAL_FORMS:
                continue
            if "start" in e:  # flow item: must cover a full year
                days = (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days
                if not 300 <= days <= 400:
                    continue
            if best is None or e.get("filed", "") > best.get("filed", ""):
                best = e
        if best is not None:
            return float(best["val"])
    return None


def _first_fact(symbol: str, tags: list[str], end: str, cur: str) -> tuple[float | None, str | None]:
    for tag in tags:
        value = _fact(symbol, tag, end, cur)
        if value is not None:
            return value, tag
    return None, None


# --------------------------------------------------------------------- OpenBB
def _openbb_rows(symbol: str, statement: str, years: int) -> dict[int, dict]:
    from openbb import obb  # heavy import, keep it lazy

    res = getattr(obb.sec, statement)(symbol=symbol, period="annual", limit=years, provider="sec")
    return {r.fiscal_year: r.model_dump() for r in res.results}


def _sum(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


# ------------------------------------------------------------- fallback chains
def _fill_missing(symbol: str, row: dict, end: str, cur: str, sources: dict) -> None:
    """Steps 2 and 3 of the chain for the plain inputs."""
    for field, tags in FALLBACK_TAGS.items():
        if row.get(field) is None:
            value, tag = _first_fact(symbol, tags, end, cur)
            if value is not None:
                row[field] = value
                sources[field] = f"xbrl:{tag}"

    if row.get("total_liabilities") is None:
        liab_and_eq = row.get("total_liabilities_and_equity")
        equity = row.get("total_equity_and_noncontrolling_interests")
        if liab_and_eq is not None and equity is not None:
            row["total_liabilities"] = liab_and_eq - equity
            sources["total_liabilities"] = "derived: liabilities+equity minus equity"

    if row.get("total_operating_income") is None:
        revenue = row.get("total_revenue")
        costs = row.get("costs_and_expenses")
        if costs is None:
            costs, _ = _first_fact(symbol, ["CostsAndExpenses"], end, cur)
        if revenue is not None and costs is not None:
            row["total_operating_income"] = revenue - costs
            sources["total_operating_income"] = "derived: revenue minus costs and expenses"


def _xbrl_debt(symbol: str, end: str, cur: str) -> tuple[float | None, float | None, str]:
    """(short-term debt incl. current portion, non-current debt, description) from raw tags.
    (None, None, '') when the filer has no debt facts."""
    def f(*tags):
        return _first_fact(symbol, list(tags), end, cur)

    # Filers tag the current part differently (MELI: LoansPayableCurrent, KO: ...ObligationsCurrent),
    # so take the larger of the filer's own total and the sum of its separate lines.
    total_current, _ = f("DebtCurrent")
    borrowings, _ = f("ShortTermBorrowings", "CommercialPaper")
    current_long, _ = f("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent",
                        "LoansPayableCurrent", "NotesPayableCurrent")
    parts = _sum(borrowings, current_long)
    current = max((x for x in (total_current, parts) if x is not None), default=None)

    noncurrent, tag = f("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermNotesPayable")
    if noncurrent is None:
        total, tag = f("LongTermDebt", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities")
        if total is not None:
            noncurrent = max(total - (current_long or 0.0), 0.0)
            tag = f"{tag} minus LongTermDebtCurrent"
    if noncurrent is None:
        return None, None, ""
    return (current if current is not None else 0.0), noncurrent, f"xbrl:{tag}"


# --------------------------------------------------------------- sanity checks
def _sanity(row: dict) -> list[str]:
    warnings = []

    def rel(a, b):
        return abs(a - b) / abs(a) if a else 0.0

    assets, rev = row.get("total_assets"), row.get("total_revenue")
    le, tl, eq = (row.get("total_liabilities_and_equity"), row.get("total_liabilities"),
                  row.get("total_equity_and_noncontrolling_interests"))
    if assets is not None and le is not None and rel(assets, le) > 0.01:
        warnings.append("total assets differ from liabilities+equity by more than 1%")
    elif assets is not None and tl is not None and eq is not None and rel(assets, tl + eq) > 0.02:
        warnings.append("total assets differ from liabilities + equity by more than 2%")
    ca = row.get("total_current_assets")
    if assets is not None and ca is not None and ca > assets * 1.001:
        warnings.append("current assets exceed total assets")
    gp = row.get("total_gross_profit")
    if rev is not None and gp is not None and gp > rev * 1.001:
        warnings.append("gross profit exceeds revenue")
    if rev is not None and rev < 0:
        warnings.append("negative revenue")
    ebit = row.get("ebit")
    if rev and ebit is not None and abs(ebit / rev) > 1.0:
        warnings.append("EBIT is larger than revenue: check the income statement mapping")
    for field in ("interest_expense", "short_term_debt_total", "long_term_debt_noncurrent"):
        if (row.get(field) or 0) < 0:
            warnings.append(f"{field} is negative")
    return warnings


def _derive(symbol: str, row: dict) -> dict:
    """Closes gaps (see the module docstring) and adds the lines the ratios need."""
    end, cur = str(row["period_ending"]), row.get("reported_currency") or "USD"
    sources: dict[str, str] = {}
    _fill_missing(symbol, row, end, cur, sources)

    interest = row.get("total_interest_expense")
    if interest is None:
        interest, tag = _first_fact(symbol, INTEREST_TAGS, end, cur)
        if tag:
            sources["interest_expense"] = f"xbrl:{tag}"
    if interest is None:
        interest, tag = _first_fact(symbol, INTEREST_PROXY_TAGS, end, cur)
        if tag:
            sources["interest_expense"] = f"proxy: cash {tag}"
    row["interest_expense"] = interest
    pretax = row.get("total_pretax_income")
    row["ebit"] = None if pretax is None else pretax + (interest or 0.0)
    if interest is None and pretax is not None:
        sources["ebit"] = "pretax income only (no interest expense found)"

    current_part = row.get("current_portion_of_long_term_debt")
    row["short_term_debt_total"] = _sum(row.get("short_term_debt"), current_part)

    ltd = row.get("long_term_debt")
    if ltd is not None and current_part:
        total_tag = _fact(symbol, "LongTermDebt", end, cur)
        if total_tag is not None and abs(total_tag - ltd) < 1:
            ltd = max(ltd - current_part, 0.0)
    row["long_term_debt_noncurrent"] = ltd
    if row["short_term_debt_total"] is None and ltd is not None:
        row["short_term_debt_total"] = 0.0  # long-term debt is reported, so no short-term line means none due

    if row["short_term_debt_total"] is None and ltd is None:
        std, noncurrent, how = _xbrl_debt(symbol, end, cur)
        if noncurrent is not None:
            row["short_term_debt_total"], row["long_term_debt_noncurrent"] = std, noncurrent
            sources["debt"] = how
        elif interest is None and row.get("total_liabilities") is not None:
            # no debt line anywhere and no interest expense: a debt-free filer
            row["short_term_debt_total"] = row["long_term_debt_noncurrent"] = 0.0
            sources["debt"] = "assumed_zero: no debt facts and no interest expense found"
        else:
            sources["debt"] = "not found (interest is reported, so the company has debt we could not read)"

    row["cash_and_short_term_investments"] = _sum(row.get("cash_and_equivalents"), row.get("short_term_investments"))
    row["intangibles_total"] = _sum(row.get("goodwill"), row.get("intangible_assets")) or 0.0  # none reported = none held
    row["inventory"] = row.get("net_inventory") or 0.0  # service companies carry none
    row["extraordinary_credit"] = row.get("extraordinary_income") or 0.0
    row["extraordinary_charge"] = _sum(row.get("impairment_expense"), row.get("restructuring_charge"),
                                       row.get("other_special_charges")) or 0.0

    row["_sources"] = sources
    row["_warnings"] = _sanity(row)
    row["_schema"] = SCHEMA_VERSION
    for w in row["_warnings"]:
        log.warning("%s FY%s: %s", symbol, row["fiscal_year"], w)
    return row


def fetch_company(symbol: str, years: int = YEARS_FETCHED) -> dict[int, dict]:
    """Merged income + balance sheet rows per fiscal year, newest first, plus derived lines."""
    _cik(symbol)  # KeyError for a ticker that is not an SEC filer, before OpenBB is asked
    inc = _openbb_rows(symbol, "income_statement", years)
    bal = _openbb_rows(symbol, "balance_sheet", years)
    merged = {}
    for year in sorted(set(inc) & set(bal), reverse=True):
        merged[year] = _derive(symbol, {**bal[year], **inc[year]})
    return merged


# ------------------------------------------------------------------ public API
class CompanyStatements:
    """Latest fiscal year is offset 0, the one before it offset 1, and so on."""

    def __init__(self, ticker: str, rows: dict[int, dict]):
        self.ticker = ticker
        self.years = sorted(rows, reverse=True)
        self._rows = rows

    @property
    def currency(self) -> str:
        return self._rows[self.years[0]].get("reported_currency") or "USD"

    @property
    def period_ending(self) -> str | None:
        """End date of the latest fiscal year, e.g. '2025-12-31'."""
        value = self._rows[self.years[0]].get("period_ending")
        return str(value)[:10] if value else None

    @property
    def is_financial(self) -> bool:
        """Banks and insurers: interest is their revenue/cost of funds, they have no current
        assets or gross profit, so the industrial ratios (EBIT margin, liquidity, leverage)
        would be wrong rather than merely empty."""
        latest = self._rows[self.years[0]]
        return any(latest.get(f) is not None for f in ("net_interest_income", "premiums_earned"))

    def get(self, offset: int, field: str) -> float:
        if field not in INPUTS:
            raise KeyError(f"'{field}' is not a declared input: add it to sources/inputs.py first")
        if offset >= len(self.years):
            return np.nan
        value = self._rows[self.years[offset]].get(field)
        return float(value) if isinstance(value, (int, float)) else np.nan

    def sources(self, offset: int = 0) -> dict[str, str]:
        """Inputs that did not come straight from OpenBB, and where they came from."""
        return self._rows[self.years[offset]].get("_sources", {})

    def warnings(self, offset: int = 0) -> list[str]:
        return self._rows[self.years[offset]].get("_warnings", [])


def _cache_is_current(rows: dict) -> bool:
    return bool(rows) and all(r.get("_schema") == SCHEMA_VERSION for r in rows.values())


def load_company_statements(ticker: str, refresh: bool = False) -> CompanyStatements | None:
    """Cached for a week. None if the ticker is not an SEC filer or has no statements."""
    cache = CACHE_DIR / f"{ticker.upper()}.json"
    if not refresh and cache.exists() and time.time() - cache.stat().st_mtime < CACHE_MAX_AGE_DAYS * 86400:
        rows = {int(y): r for y, r in json.loads(cache.read_text()).items()}
        if _cache_is_current(rows):
            return CompanyStatements(ticker, rows)
        log.info("%s: cached data is from older fallback rules, refetching", ticker)
    try:
        rows = fetch_company(ticker)
    except KeyError:
        log.warning("%s is not an SEC filer, skipped", ticker)
        return None
    except Exception as exc:  # network, SEC or OpenBB failure: skip this company, keep the run alive
        log.warning("%s: could not fetch SEC statements (%s)", ticker, exc)
        return None
    if not rows:
        log.warning("%s: SEC returned no annual statements", ticker)
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows, default=str))
    return CompanyStatements(ticker, rows)


def configured_tickers() -> list[str]:
    return [c["ticker"] for c in load_companies()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and cache SEC fundamentals via OpenBB.")
    parser.add_argument("symbols", nargs="+", help="US tickers, e.g. MELI AAPL")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache")
    args = parser.parse_args()
    for symbol in args.symbols:
        stm = load_company_statements(symbol, refresh=args.refresh)
        if stm is None:
            print(f"{symbol}: no data")
        else:
            print(f"{symbol}: FY{stm.years} in {stm.currency}, revenue {stm.get(0, 'total_revenue'):,.0f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
