"""
Article body-text extraction — fetch + clean only.

Adapted from your tried_approach_news_extract.py. Deliberate changes:

1. The LLM/instructor summarization step is REMOVED from this module. upsert.py's
   own docstring is explicit that sentiment (and by extension any LLM-derived
   field like a summary) is owned exclusively by a *later*, separate script that
   only ever patches that one field. Baking an LLM call into extraction would
   collapse that boundary, so this module stops at clean body_text and hands
   off via ingestion.upsert.apply_extraction_result(). A future
   extraction/article_summarizer.py can read body_text back out and write
   llm_summary the same way.
2. Returns (status, fetch_method, body_text) instead of printing / building a
   pydantic report, matching what apply_extraction_result() expects.
3. No hardcoded API key, no stray debug file write, no tuple-typo default.
"""
import asyncio
import logging
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException
from trafilatura import extract

from ..config import is_demo
from .body_cleaner import clean_markdown_body, has_article_text

# crawl4ai (and the Playwright browser behind it) is imported only when the browser fallback is
# really used, see _load_crawl4ai(): the online demo never uses it, so its install needs neither.

warnings.filterwarnings("ignore", message="This package.*has been renamed to.*ddgs")

logger = logging.getLogger("article_extractor")


class FetchMethod(str, Enum):
    CURL_CFFI = "curl_cffi"
    CRAWL4AI = "crawl4ai"
    FAILED = "failed"


@dataclass
class ExtractorConfig:
    impersonate: str = "chrome"
    curl_timeout: float = 10.0
    curl_max_retries: int = 2
    curl_retry_backoff: float = 1.5
    min_content_length: int = 500
    block_status_codes: tuple = (403, 429, 503)
    block_markers: tuple = (
        "captcha",
        "cf-browser-verification",
        "just a moment",
        "access denied",
        "attention required",
        "verify you are human",
        "checking your browser",
        "before you continue",
        "enable javascript",
        "please enable js",
        "bot detection",
    )
    playwright_headless: bool = True
    playwright_timeout_ms: int = 30000
    # The browser fallback (crawl4ai) is allowed on your PC (APP_MODE=full) and never in the online demo
    # (APP_MODE=demo): there, a page curl_cffi cannot read is left for the local backend.
    allow_browser_fallback: bool = field(default_factory=lambda: not is_demo())


def _load_crawl4ai():
    """crawl4ai's classes, imported on first use (heavy: it pulls in Playwright)."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    return AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig


@dataclass
class FetchResult:
    url: str
    success: bool
    method: FetchMethod = FetchMethod.FAILED
    html: Optional[str] = None       # curl_cffi: the page HTML; crawl4ai: the page as markdown
    error: Optional[str] = None
    elapsed_seconds: float = 0.0
    raw_html: Optional[str] = None   # crawl4ai only: the rendered page HTML (for trafilatura and htmldate)


# --------------------------------------------------------------------------
# Tier 1 — curl_cffi
# --------------------------------------------------------------------------
class CurlCffiFetcher:
    def __init__(self, config: ExtractorConfig):
        self.config = config

    def _looks_blocked(self, status_code: int, html: str) -> bool:
        if status_code in self.config.block_status_codes:
            return True
        if len(html) < self.config.min_content_length:
            return True
        lowered = html[:8000].lower()
        return any(marker in lowered for marker in self.config.block_markers)

    async def fetch(self, url: str) -> FetchResult:
        start = time.monotonic()
        last_error: Optional[str] = None

        for attempt in range(1, self.config.curl_max_retries + 1):
            try:
                async with AsyncSession() as session:
                    resp = await session.get(
                        url,
                        impersonate=self.config.impersonate,
                        timeout=self.config.curl_timeout,
                        allow_redirects=True,
                    )
                    html = resp.text
                    elapsed = time.monotonic() - start

                    if self._looks_blocked(resp.status_code, html):
                        last_error = f"Blocked (status={resp.status_code})"
                        logger.warning("[curl_cffi] %s attempt %d: %s", url, attempt, last_error)
                    else:
                        logger.info("[curl_cffi] SUCCESS %s (status=%d, %.2fs)", url, resp.status_code, elapsed)
                        return FetchResult(url=url, success=True, method=FetchMethod.CURL_CFFI,
                                            html=html, elapsed_seconds=elapsed)

            except RequestException as e:
                last_error = str(e)
                logger.warning("[curl_cffi] %s attempt %d: %s", url, attempt, e)
            except Exception as e:
                last_error = f"Unexpected: {e}"
                logger.error("[curl_cffi] %s attempt %d: %s", url, attempt, e)

            if attempt < self.config.curl_max_retries:
                await asyncio.sleep(self.config.curl_retry_backoff * attempt)

        elapsed = time.monotonic() - start
        return FetchResult(url=url, success=False, method=FetchMethod.FAILED,
                            error=last_error, elapsed_seconds=elapsed)


def extract_article_text(html: str, url: str) -> Optional[str]:
    text = extract(
        html, url=url, include_comments=False, include_tables=False,
        deduplicate=True, target_language="en",
    )
    if not text or len(text.strip()) < 200:
        logger.warning("[trafilatura] No meaningful text from %s", url)
        return None
    return text.strip()


# --------------------------------------------------------------------------
# Tier 2 — crawl4ai browser fallback
# --------------------------------------------------------------------------
class Crawl4AIFetcher:
    def __init__(self, config: ExtractorConfig):
        self.config = config
        self.AsyncWebCrawler, BrowserConfig, self._CacheMode, self._CrawlerRunConfig = _load_crawl4ai()
        self.browser_config = BrowserConfig(
            headless=config.playwright_headless,
            enable_stealth=True,
            extra_args=["--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox"],
        )

    async def browser_fetch(self, url: str, crawler) -> FetchResult:
        start = time.monotonic()
        run_cfg = self._CrawlerRunConfig(
            cache_mode=self._CacheMode.BYPASS,
            word_count_threshold=150,
            page_timeout=self.config.playwright_timeout_ms,
        )
        try:
            result = await crawler.arun(url=url, config=run_cfg)
            elapsed = time.monotonic() - start
            if result.success and result.markdown:
                logger.info("[crawl4ai] SUCCESS %s (%.2fs, %d chars markdown)", url, elapsed, len(result.markdown))
                return FetchResult(url=url, success=True, method=FetchMethod.CRAWL4AI,
                                    html=str(result.markdown), raw_html=getattr(result, "html", None),
                                    elapsed_seconds=elapsed)
            logger.error("[crawl4ai] FAILED %s: %s", url, result.error_message)
            return FetchResult(url=url, success=False, method=FetchMethod.FAILED,
                                error=result.error_message, elapsed_seconds=elapsed)
        except Exception as e:
            elapsed = time.monotonic() - start
            logger.error("[crawl4ai] Exception on %s: %s", url, e)
            return FetchResult(url=url, success=False, method=FetchMethod.FAILED,
                                error=str(e), elapsed_seconds=elapsed)


# --------------------------------------------------------------------------
# Orchestrator for one URL — matches apply_extraction_result()'s signature
# --------------------------------------------------------------------------
_browser_unavailable = False  # set once crawl4ai's browser fails to start, so it is not retried per article


def find_publish_date(html: str, url: str) -> Optional[datetime]:
    """Original publication date of an article page, or None.

    Deterministic for the same HTML: htmldate checks JSON-LD, meta tags (article:published_time,
    datePublished, ...), <time> elements and the URL, in that fixed order. The slower text
    heuristics are off (extensive_search=False) because a wrong date is worse than no date.
    Dates before 2000 or in the future are rejected."""
    try:
        from htmldate import find_date
        found = find_date(
            html, url=url, original_date=True, extensive_search=False, outputformat="%Y-%m-%d",
            min_date="2000-01-01", max_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
    except Exception as exc:  # malformed HTML must never break text extraction
        logger.debug("[htmldate] failed on %s: %s", url, exc)
        return None
    if not found:
        return None
    return datetime.strptime(found, "%Y-%m-%d").replace(tzinfo=timezone.utc)


async def fetch_and_extract(url: str, config: Optional[ExtractorConfig] = None):
    """Returns (status, fetch_method, body_text). status is 'success' or
    'failed'; body_text is None on failure. fetch_method is a plain string
    ('curl_cffi' / 'crawl4ai') for storage in extraction.fetch_method."""
    status, method, body_text, _ = await fetch_and_extract_with_date(url, config)
    return status, method, body_text


async def fetch_and_extract_with_date(url: str, config: Optional[ExtractorConfig] = None):
    """Like fetch_and_extract, plus the publish date read from the page (aware datetime or
    None; only available when the page came through curl_cffi, the browser fallback returns
    markdown without the page metadata).

    status is 'success', 'failed', 'skipped' (the page needs the browser fallback and the browser
    cannot start: the caller leaves the article pending) or 'needs_browser' (the browser fallback
    is not allowed in this mode: the caller marks the article needs_browser, to be read later by
    the backend with the browser; it is neither retried online nor counted as failed)."""
    config = config or ExtractorConfig()
    curl_fetcher = CurlCffiFetcher(config)

    curl_result = await curl_fetcher.fetch(url)
    body_text = None
    page_date = None
    method = FetchMethod.CURL_CFFI

    if curl_result.success and curl_result.html:
        body_text = extract_article_text(curl_result.html, url)
        page_date = find_publish_date(curl_result.html, url)

    if not body_text:
        reason = curl_result.error if not curl_result.success else "trafilatura extraction empty"
        global _browser_unavailable
        if not config.allow_browser_fallback:
            # Online demo: curl_cffi only. Not marked failed: the local backend reads it with the browser.
            logger.info("[extractor] %s needs the browser (%s); not allowed here, left for the backend", url, reason)
            return "needs_browser", "browser_not_allowed", None, page_date
        if _browser_unavailable:
            return "skipped", "browser_unavailable", None, page_date
        logger.info("[extractor] Escalating %s to crawl4ai (%s)", url, reason)
        crawl4ai_fetcher = Crawl4AIFetcher(config)
        try:
            async with crawl4ai_fetcher.AsyncWebCrawler(config=crawl4ai_fetcher.browser_config) as crawler:
                c4_result = await crawl4ai_fetcher.browser_fetch(url, crawler)
        except Exception as exc:
            # Typically "Executable doesn't exist": the Playwright browser is not installed.
            # The article is NOT marked failed: it stays pending and is retried on the next run,
            # once the browser is installed. The browser is not tried again during this run.
            _browser_unavailable = True
            logger.error("[crawl4ai] cannot start the browser (%s). Fix: run `playwright install chromium`. "
                         "Articles that need the browser stay pending.", str(exc).splitlines()[0])
            return "skipped", "browser_unavailable", None, page_date
        if c4_result.success and c4_result.html:
            method = FetchMethod.CRAWL4AI
            # The browser's markdown is the WHOLE page (menus, sidebars, related links). Read the
            # rendered HTML with trafilatura like any other page; only if that finds no article,
            # fall back to the markdown with the navigation stripped (extraction/body_cleaner.py).
            if c4_result.raw_html:
                body_text = extract_article_text(c4_result.raw_html, url)
                page_date = page_date or find_publish_date(c4_result.raw_html, url)
            if not body_text:
                cleaned = clean_markdown_body(c4_result.html)
                body_text = cleaned if has_article_text(cleaned) else None
        if not body_text:
            return "failed", FetchMethod.FAILED.value, None, page_date

    return "success", method.value, body_text, page_date
