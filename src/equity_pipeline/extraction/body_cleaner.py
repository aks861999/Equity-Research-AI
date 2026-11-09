"""
Strips page navigation from a whole-page markdown text (the crawl4ai browser fallback).

The normal download path (curl_cffi + trafilatura) returns only the article text. The browser path
returns the whole page: menus, sidebars, link lists. Generic rules, nothing site-specific:

  * keep a line only if it reads like prose: at least MIN_WORDS words, not mostly link text
  * drop images, bare URLs and table rows; links keep their anchor text
  * drop short boilerplate lines ("subscribe", "read more", "all rights reserved", ...) and any
    line that mentions cookies
  * keep repeated lines once; cap the result at MAX_CHARS

Whether the result is a usable article at all (too short, a cookie page, not about the company)
is decided by extraction/quality_gate.py, not here.
"""
from __future__ import annotations

import re

MIN_WORDS = 12
MAX_LINK_RATIO = 0.5
BOILERPLATE_MAX_WORDS = 40
MIN_BODY_CHARS = 400   # same minimum as the quality gate
MAX_CHARS = 30_000

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BARE_URL = re.compile(r"https?://\S+")
_LEADING_MARKERS = re.compile(r"^[#>*\-\s•|]+")
_EMPHASIS = re.compile(r"[*_`]{1,3}")
_COOKIE = re.compile(r"cookie", re.IGNORECASE)
_BOILERPLATE = re.compile(
    r"subscribe|sign up|newsletter|all rights reserved|follow us|advertisement|read more|copyright|©"
    r"|click here|log in|sign in|share this|related (articles|stories|news)|you may also like"
    r"|recommended for you|skip to|back to top|to continue reading|full access|unlimited access"
    r"|already a (subscriber|member)|members only|terms of (use|service)",
    re.IGNORECASE,
)


def clean_markdown_body(markdown: str | None) -> str:
    """The article prose of a markdown page, paragraphs separated by a blank line ('' if none)."""
    if not markdown:
        return ""
    kept, seen = [], set()
    for raw in _IMAGE.sub("", markdown).splitlines():
        line = raw.strip()
        if not line:
            continue
        anchor_chars = sum(len(m.group(1)) for m in _LINK.finditer(line))
        plain = _BARE_URL.sub("", _LINK.sub(r"\1", line))
        plain = _EMPHASIS.sub("", _LEADING_MARKERS.sub("", plain))
        plain = re.sub(r"\s+", " ", plain).strip()
        words = plain.split()
        if len(words) < MIN_WORDS or anchor_chars / max(len(plain), 1) > MAX_LINK_RATIO:
            continue
        if plain.count("|") >= 3 or _COOKIE.search(plain):
            continue
        if _BOILERPLATE.search(plain) and len(words) < BOILERPLATE_MAX_WORDS:
            continue
        if plain.lower() in seen:
            continue
        seen.add(plain.lower())
        kept.append(plain)
    return "\n\n".join(kept)[:MAX_CHARS]


def has_article_text(cleaned: str) -> bool:
    return len(cleaned) >= MIN_BODY_CHARS
