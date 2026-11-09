"""
Stable article identity. The same story reaches you via different Google
redirect wrappers on different days, and via different tracking-param
variants of the same publisher URL — so the _id has to be built from a
*canonicalized* URL, not the raw one, or you'll silently re-store the same
article under multiple _ids.
"""
import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_TITLE_SEPARATOR = re.compile(r"\s+[|\-–—]\s+")
_BY_SITE = re.compile(r"\s+by\s+\S+\.\S+$", re.IGNORECASE)   # "... By Investing.com"


def title_key(title: str | None) -> str:
    """Normalised title used to spot the same story syndicated on several sites
    (fool.com / nasdaq.com / aol.com, finance.yahoo.com / uk.finance.yahoo.com).

    A trailing site name is dropped ("... | The Motley Fool", "... - FourWeekMBA",
    "... By Investing.com") only when it is short (at most 4 words) and the rest is a real
    headline (at least 5 words), so two different stories that share a prefix do not collide.
    Then lower case, punctuation removed."""
    text = (title or "").strip()
    text = _BY_SITE.sub("", text)
    parts = _TITLE_SEPARATOR.split(text)
    if len(parts) > 1:
        head, tail = " ".join(parts[:-1]), parts[-1]
        if len(tail.split()) <= 4 and len(head.split()) >= 5:
            text = head
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "ocid", "cmpid", "fbclid", "gclid",
}


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    query_pairs = sorted(
        (k, v) for k, v in parse_qsl(parts.query) if k.lower() not in TRACKING_PARAMS
    )
    clean = parts._replace(
        scheme=parts.scheme.lower(),
        netloc=parts.netloc.lower(),
        query=urlencode(query_pairs),
        fragment="",
    )
    return urlunsplit(clean)


def url_hash(url: str) -> str:
    return hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()


def article_doc_id(ticker: str, url: str) -> str:
    return f"article:{ticker}:{url_hash(url)}"


def filing_doc_id(ticker: str, accession: str, section: str) -> str:
    safe_section = section.replace(".", "_").replace(" ", "_")
    return f"filing:{ticker}:{accession}:{safe_section}"
