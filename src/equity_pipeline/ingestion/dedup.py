"""
Stable article identity. The same story reaches you via different Google
redirect wrappers on different days, and via different tracking-param
variants of the same publisher URL — so the _id has to be built from a
*canonicalized* URL, not the raw one, or you'll silently re-store the same
article under multiple _ids.
"""
import hashlib
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

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
