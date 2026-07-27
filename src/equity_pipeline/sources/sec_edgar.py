"""
SEC EDGAR fetcher — general/company-level scope (never per-metric).

The parsing/cleaning logic here is your original 3_sec_edgar_only_for_MELI_GLBE.py
almost unchanged — it was solid. The only real change is the outer function:
instead of process_filing() calling _save_extraction() to write content.md +
metadata.json to disk, fetch_sec_filings() now returns a list of dicts that
ingestion/upsert.upsert_filing() writes straight into CouchDB.
"""
import re
import time
import urllib.parse
from datetime import datetime, timedelta

import requests
import feedparser
from bs4 import BeautifulSoup, NavigableString

HEADERS = {"User-Agent": "SEC_USER_AGENT_FROM_ENV"}  # SEC strictly requires a real UA
REQUEST_DELAY = 0.5


# --------------------------------------------------------------------------
# Small helpers (unchanged from your original)
# --------------------------------------------------------------------------
def _extract_accession(index_url: str) -> str:
    m = re.search(r"/(\d{10}-\d{2}-\d{6})-index\.htm", index_url)
    if m:
        return m.group(1)
    return urllib.parse.urlparse(index_url).path.rsplit("/", 1)[-1].replace("-index.htm", "")


def _parse_rss_date(date_str: str):
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            return None


def _unwrap_xbrl_viewer(response_text: str, viewer_url: str) -> str:
    lower_text = response_text.lower()
    if "xbrl viewer" not in lower_text and "inline xbrl" not in lower_text:
        return viewer_url

    soup = BeautifulSoup(response_text, "html.parser")
    iframe = soup.find("iframe", {"id": "ixvFrame"})
    if iframe and iframe.get("src"):
        src = iframe["src"]
        return urllib.parse.urljoin("https://www.sec.gov", src) if src.startswith("/") else src

    parsed = urllib.parse.urlparse(viewer_url)
    if parsed.path in ("/ix", "/ixviewer/ix.html", "/ixviewer"):
        qs = urllib.parse.parse_qs(parsed.query)
        if "doc" in qs:
            doc_path = qs["doc"][0]
            if doc_path.startswith("/"):
                return urllib.parse.urljoin("https://www.sec.gov", doc_path)

    return viewer_url


def parse_sec_index_page(index_url: str) -> dict:
    response = requests.get(index_url, headers=HEADERS)
    if response.status_code != 200:
        return {}

    soup = BeautifulSoup(response.content, "html.parser")
    document_links = {}
    for table in soup.find_all("table"):
        if "Document" in table.text and "Type" in table.text:
            for row in table.find_all("tr")[1:]:
                cols = row.find_all("td")
                if len(cols) >= 4:
                    link_tag = cols[2].find("a")
                    doc_type = cols[3].text.strip()
                    if link_tag and "href" in link_tag.attrs:
                        full_url = urllib.parse.urljoin("https://www.sec.gov", link_tag["href"])
                        filename = link_tag.get_text(strip=True)
                        key = doc_type if doc_type not in document_links else f"{doc_type}__{filename}"
                        document_links[key] = {"url": full_url, "filename": filename, "doc_type": doc_type}
            break
    return document_links


def _is_tabular_chunk(text, digit_ratio_thresh=0.20, adjacency_thresh=0.08, min_tokens=4):
    tokens = text.split()
    if len(tokens) < min_tokens:
        return False
    numeric_flags = [bool(re.search(r"\d", t)) for t in tokens]
    digit_ratio = sum(numeric_flags) / len(tokens)
    adjacent_pairs = sum(1 for i in range(len(tokens) - 1) if numeric_flags[i] and numeric_flags[i + 1])
    return digit_ratio >= digit_ratio_thresh or (adjacent_pairs / len(tokens)) >= adjacency_thresh


def _clean_soup(soup):
    for tag in soup.find_all(re.compile(r"^ix:", re.I)):
        tag.decompose()
    head = soup.find("head")
    if head:
        head.decompose()
    for tag in soup.find_all(["meta", "link", "noscript"]):
        tag.decompose()
    for tag in soup.find_all("input", {"type": "hidden"}):
        tag.decompose()
    for element in soup(["table", "img", "svg", "figure", "script", "style", "hr"]):
        element.decompose()

    for block in soup.find_all("font", attrs={"style": re.compile(r"font-size:1pt", re.I)}):
        block_text = " ".join(block.get_text(separator=" ").split())
        kept = [c for c in re.split(r"(?<=[.?!])\s+", block_text) if c and not _is_tabular_chunk(c)]
        block.replace_with(NavigableString(" ".join(kept)))

    return soup


def _extract_text_from_soup(soup):
    lines = [line.strip() for line in soup.get_text(separator="\n").splitlines() if line.strip()]
    return "\n".join(lines)


def _extract_ex99_text(document_url: str, index_url: str = None, filename: str = None) -> str:
    response = requests.get(document_url, headers=HEADERS)
    if response.status_code != 200:
        return ""

    actual_url = _unwrap_xbrl_viewer(response.text, document_url)
    if actual_url != document_url:
        time.sleep(REQUEST_DELAY)
        response = requests.get(actual_url, headers=HEADERS)
        if response.status_code != 200:
            return ""
        if "xbrl viewer" in response.text.lower():
            if index_url and filename:
                fallback_url = urllib.parse.urljoin(index_url.rsplit("/", 1)[0] + "/", filename)
                if fallback_url not in (document_url, actual_url):
                    time.sleep(REQUEST_DELAY)
                    response = requests.get(fallback_url, headers=HEADERS)
                    if response.status_code != 200:
                        return ""
                else:
                    return ""
            else:
                return ""

    soup = _clean_soup(BeautifulSoup(response.content, "html.parser"))
    return _extract_text_from_soup(soup)


# --------------------------------------------------------------------------
# Orchestration — returns structured dicts instead of writing to disk
# --------------------------------------------------------------------------
# Update the function signature to accept db
def _process_one_filing(ticker: str, entry, cutoff_date: datetime, form_type: str, db=None) -> list:
    index_url = entry.link
    filing_date_str = getattr(entry, "updated", "")
    filing_date = _parse_rss_date(filing_date_str)
    if filing_date and filing_date < cutoff_date:
        return []

    accession = _extract_accession(index_url)
    date_display = filing_date_str[:10] if filing_date_str else None

    time.sleep(REQUEST_DELAY)
    docs = parse_sec_index_page(index_url)
    if not docs:
        return []

    ex99_keys = sorted(k for k, v in docs.items() if v["doc_type"].upper().startswith("EX-99"))
    if ex99_keys:
        targets = [(k, docs[k]) for k in ex99_keys]
    else:
        primary = next((v for v in docs.values() if v["doc_type"].upper() == form_type.upper()), None)
        targets = [(form_type, primary)] if primary else []

    results = []
    for section, doc_info in targets:
        # ---> ADD THIS DB CHECK <---
        safe_section = section.replace(".", "_").replace(" ", "_")
        doc_id = f"filing:{ticker}:{accession}:{safe_section}"
        
        if db:
            existing_doc = db.get(doc_id)
            if existing_doc and existing_doc.get("body_text"):
                # Filing is already in the DB, skip downloading and parsing!
                continue
                
        time.sleep(REQUEST_DELAY)
        text = _extract_ex99_text(doc_info["url"], index_url, doc_info.get("filename"))
        if not text:
            continue
        results.append({
            "form_type": form_type,
            "accession": accession,
            "section": section,
            "filing_date": date_display,
            "url": doc_info["url"],
            "source_filename": doc_info.get("filename"),
            "body_text": text,
        })
    return results
# Update the function signature to accept db
def fetch_sec_filings(ticker: str, form_type: str, max_results: int = 100, lookback_days: int = 365, db=None):
    """Returns a list of filing dicts..."""
    cutoff = datetime.now() - timedelta(days=lookback_days)
    rss_url = (
        f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}"
        f"&type={form_type}&owner=exclude&count=40&output=atom"
    )
    resp = requests.get(rss_url, headers=HEADERS)
    if resp.status_code != 200:
        return []

    feed = feedparser.parse(resp.content)
    all_results = []
    processed = 0
    for entry in feed.entries:
        if processed >= max_results:
            break
        if form_type not in getattr(entry, "title", ""):
            continue
        filing_date = _parse_rss_date(getattr(entry, "updated", ""))
        if filing_date and filing_date < cutoff:
            continue

        # Pass db to the inner function
        all_results.extend(_process_one_filing(ticker, entry, cutoff, form_type, db=db))
        processed += 1

    time.sleep(REQUEST_DELAY)
    return all_results