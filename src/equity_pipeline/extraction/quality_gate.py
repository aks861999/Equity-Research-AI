"""
Quality gate: decides whether a downloaded text is a usable article BEFORE it is stored for scoring.

Three plain rules, no website-specific logic (thresholds checked on 142 real texts of one company:
all 18 junk texts were caught, none of the 124 good ones; re-check the rejected counts per run):

  too_short             fewer than MIN_CHARS characters. Menu-only pages and paywall teasers.
                        (junk was <= 336 chars, the shortest good text 501)
  too_long              more than MAX_CHARS characters. A whole error page with its navigation.
                        (junk was ~341,000 chars, the longest good text 56,289)
  cookie_wall           under WALL_MAX_CHARS characters and at least WALL_MIN_TERMS DIFFERENT consent
                        words (cookie, consent, privacy, preferences, accept, personal data). A consent
                        page instead of the article. Checked on real data: the cookie pages contained
                        all six words, while 73 of the 74 other short texts contained none. (Counting
                        repeats of "cookie" alone does not work: the pages use it once.)
  company_not_mentioned the company name (as whole words) or ticker is in neither title nor text, so the
                        article is not about the company (e.g. a search for "<company> inventory
                        levels" returning a video-game maker's inventory story).

`assess` returns None for a usable text, else the reason. A rejected article keeps a small marker
row with status "rejected" and the reason (so it is not downloaded again), but has no stored text
and is never scored.
"""
from __future__ import annotations

import re

MIN_CHARS = 400
MAX_CHARS = 100_000
WALL_MAX_CHARS = 4_000
WALL_MIN_TERMS = 2
MIN_TICKER_LEN = 3  # "T" or "KO" would match ordinary words; short tickers are not used

_CONSENT_WORDS = [re.compile(p, re.IGNORECASE) for p in (
    r"cookie", r"consent", r"privacy", r"preferences?", r"\baccept\b", r"personal (data|information)",
)]


def name_pattern(company_name: str) -> re.Pattern:
    """Whole-word, case-insensitive pattern for a company name.

    The name is split into words at spaces, punctuation and camelCase ('MercadoLibre' ->
    Mercado, Libre; 'Global-E Online' -> Global, E, Online; 'AT&T' -> AT, T). The words may be
    joined by spaces or punctuation or nothing, so 'Mercado Libre' and 'Coca Cola' match, but
    the match must start and end at a word boundary, so 'AT&T' does not match 'attention' and
    'Apple' does not match 'pineapple'. A trailing possessive or plural ('Apple's', 'Apples') is fine."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", company_name)
    words = [w for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]
    body = r"[\W_]*".join(re.escape(w) for w in words)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?:['’]?s)?(?![A-Za-z0-9])", re.IGNORECASE)


def mentions_company(title: str | None, body: str | None, company_name: str | None, ticker: str | None) -> bool:
    if not company_name and not ticker:
        return True  # nothing to check against
    haystack = f"{title or ''} {body or ''}"
    if company_name and name_pattern(company_name).search(haystack):
        return True
    if ticker and len(ticker) >= MIN_TICKER_LEN and re.search(rf"\b{re.escape(ticker)}\b", haystack):
        return True
    return False


ABOUT_MIN_MENTIONS = 3
# Measured on the real MELI Yahoo items: articles about the company named it 15-22 times per 1,000
# words; the pieces that only mention it in passing 3-7 (a CEO story 3, a hedge-fund profile 6.9 once the
# ticker in its related-tickers block is counted). 10 sits between them. Tuned on a handful of articles
# of ONE company: re-check on other companies.
ABOUT_MIN_PER_1000_WORDS = 10.0


def count_mentions(text: str | None, company_name: str | None, ticker: str | None) -> int:
    """How often the company is named in `text` (whole-word name matches plus whole-word ticker)."""
    if not text:
        return 0
    n = len(name_pattern(company_name).findall(text)) if company_name else 0
    if ticker and len(ticker) >= MIN_TICKER_LEN:
        n += len(re.findall(rf"\b{re.escape(ticker)}\b", text))
    return n


def is_about_company(title: str | None, body: str | None, company_name: str | None,
                     ticker: str | None) -> bool:
    """Is the article ABOUT the company, not just mentioning it? True when the title names it, or the
    text names it at least ABOUT_MIN_MENTIONS times and at least ABOUT_MIN_PER_1000_WORDS per 1,000
    words. A single mention is not enough: on real data, a story about another firm's CEO named the
    company once in passing inside its first 1,000 characters, and a hedge-fund profile named it a few
    times (name plus ticker); the articles really about the company named it 15-22 times per 1,000 words.

    Stricter than the gate's `mentions_company` (which only has to find the name anywhere); used where
    off-topic text would mislead the agent: the Yahoo items sent to the model and the Yahoo sentiment
    average."""
    if not company_name and not ticker:
        return True
    if mentions_company(title, None, company_name, ticker):
        return True
    n = count_mentions(body, company_name, ticker)
    words = len((body or "").split())
    return n >= ABOUT_MIN_MENTIONS and n / max(words, 1) * 1000 >= ABOUT_MIN_PER_1000_WORDS


def assess(title: str | None, body: str | None, company_name: str | None = None,
           ticker: str | None = None) -> str | None:
    """None if the text is usable, else one of: too_short, too_long, cookie_wall, company_not_mentioned."""
    n = len(body or "")
    if n < MIN_CHARS:
        return "too_short"
    if n > MAX_CHARS:
        return "too_long"
    if n < WALL_MAX_CHARS and sum(1 for w in _CONSENT_WORDS if w.search(body)) >= WALL_MIN_TERMS:
        return "cookie_wall"
    if not mentions_company(title, body, company_name, ticker):
        return "company_not_mentioned"
    return None
