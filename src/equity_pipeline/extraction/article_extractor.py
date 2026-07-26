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
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException
from trafilatura import extract

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

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


@dataclass
class FetchResult:
    url: str
    success: bool
    method: FetchMethod = FetchMethod.FAILED
    html: Optional[str] = None
    error: Optional[str] = None
    elapsed_seconds: float = 0.0


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
        self.browser_config = BrowserConfig(
            headless=config.playwright_headless,
            enable_stealth=True,
            extra_args=["--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox"],
        )

    async def browser_fetch(self, url: str, crawler: AsyncWebCrawler) -> FetchResult:
        start = time.monotonic()
        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            word_count_threshold=150,
            page_timeout=self.config.playwright_timeout_ms,
        )
        try:
            result = await crawler.arun(url=url, config=run_cfg)
            elapsed = time.monotonic() - start
            if result.success and result.markdown:
                logger.info("[crawl4ai] SUCCESS %s (%.2fs, %d chars markdown)", url, elapsed, len(result.markdown))
                return FetchResult(url=url, success=True, method=FetchMethod.CRAWL4AI,
                                    html=result.markdown, elapsed_seconds=elapsed)
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
async def fetch_and_extract(url: str, config: Optional[ExtractorConfig] = None):
    """Returns (status, fetch_method, body_text). status is 'success' or
    'failed'; body_text is None on failure. fetch_method is a plain string
    ('curl_cffi' / 'crawl4ai') for storage in extraction.fetch_method."""
    config = config or ExtractorConfig()
    curl_fetcher = CurlCffiFetcher(config)

    curl_result = await curl_fetcher.fetch(url)
    body_text = None
    method = FetchMethod.CURL_CFFI

    if curl_result.success and curl_result.html:
        body_text = extract_article_text(curl_result.html, url)

    if not body_text:
        reason = curl_result.error if not curl_result.success else "trafilatura extraction empty"
        logger.info("[extractor] Escalating %s to crawl4ai (%s)", url, reason)
        crawl4ai_fetcher = Crawl4AIFetcher(config)
        async with AsyncWebCrawler(config=crawl4ai_fetcher.browser_config) as crawler:
            c4_result = await crawl4ai_fetcher.browser_fetch(url, crawler)
        if c4_result.success and c4_result.html:
            body_text = c4_result.html
            method = FetchMethod.CRAWL4AI
        else:
            return "failed", FetchMethod.FAILED.value, None

    return "success", method.value, body_text
