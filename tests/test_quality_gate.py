"""Offline checks for the quality gate and the navigation stripper. Run: python tests/test_quality_gate.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from equity_pipeline.extraction.body_cleaner import clean_markdown_body, has_article_text  # noqa: E402
from equity_pipeline.extraction.quality_gate import (  # noqa: E402
    MAX_CHARS, MIN_CHARS, assess, mentions_company,
)

SENTENCE = "Acme Corp reported quarterly revenue growth and said operating margin fell as it invested in logistics. "
GOOD = SENTENCE * 8            # ~800 chars, names the company


def test_a_normal_article_passes():
    assert assess("Acme Q3 results", GOOD, "Acme Corp", "ACME") is None


def test_length_limits_use_the_gaps_seen_in_real_data():
    assert assess("Acme", "Acme " * 40, "Acme", None) == "too_short"            # ~200 chars
    assert assess("Acme", "x" * (MIN_CHARS - 1), "Acme", None) == "too_short"
    assert assess("Acme", ("Acme news " * 12_000)[: MAX_CHARS + 1], "Acme", None) == "too_long"
    assert assess("Acme", (SENTENCE * 400)[:60_000], "Acme Corp", None) is None  # a long real article (60k) is fine


def test_a_short_cookie_page_is_rejected_but_an_article_mentioning_cookies_once_is_not():
    # "cookie" appears only ONCE here, as in the real pages: it is the different consent words that count
    wall = ("Our partners use cookies to store information on your device. You can accept, or manage your preferences "
            "and privacy choices at any time. Acme " * 3)
    assert len(wall) > MIN_CHARS and assess("Acme news", wall, "Acme", None) == "cookie_wall"
    other_wording = ("We value your privacy. By clicking accept you give consent to personal data processing. " * 6) + "Acme"
    assert assess("Acme news", other_wording, "Acme", None) == "cookie_wall"
    assert assess("Acme", GOOD + " The shop also uses a cookie banner.", "Acme Corp", None) is None
    # a long article that happens to use two such words is not a wall (walls are short)
    assert assess("Acme", GOOD * 8 + " Privacy groups said they accept the ruling.", "Acme Corp", None) is None


def test_company_must_be_in_the_title_or_the_text():
    off_topic = "Another firm's inventory levels hit a record high this quarter as costs rose across the industry. " * 8
    assert assess("Inventory levels at a record", off_topic, "Acme Corp", "ACME") == "company_not_mentioned"
    assert assess("Acme Corp inventory levels", off_topic, "Acme Corp", "ACME") is None   # title counts
    assert assess("Inventory", off_topic + " Shares of ACME fell.", "Acme Corp", "ACME") is None  # ticker counts


def test_company_matching_ignores_case_spaces_and_hyphens():
    assert mentions_company("t", "mercado libre grew", "MercadoLibre", "MELI")
    assert mentions_company("t", "Global E Online said", "Global-E Online", "GLBE")


def test_the_name_must_match_as_whole_words_not_inside_other_words():
    # found on review: letters-only substring matching accepted these unrelated texts
    assert not mentions_company("News", "Attorneys said attention to detail matters in the attempt to attain results.", "AT&T", "T")
    assert not mentions_company("News", "Farmers sold pineapple and applesauce at the market.", "Apple", "AAPL")
    assert not mentions_company("News", "Nvidiaphobia is not a word.", "Nvidia", "NVDA")
    assert mentions_company("News", "Apple's iPhone sales rose.", "Apple", "AAPL")      # possessive
    assert mentions_company("News", "Walmarts and other chains", "Walmart", "WMT")      # plural
    assert mentions_company("News", "AT&T raised guidance; AT & T shares rose", "AT&T", "T")
    assert mentions_company("News", "Mercado Libre and MercadoLibre", "MercadoLibre", "MELI")  # camelCase split
    assert mentions_company("News", "Coca-Cola, Coca Cola and CocaCola", "Coca-Cola", "KO")
    assert mentions_company("News", "Global E Online said", "Global-E Online", "GLBE")
    assert mentions_company("News", "Duke Energy's grid", "Duke Energy", "DUK")


def test_short_tickers_are_not_used_for_matching():
    # "T" (AT&T) or "KO" would match ordinary text
    assert not mentions_company("A T bar", "a T shirt, KO punch", "AT&T", "T")
    assert mentions_company("t", "at&t raised guidance", "AT&T", "T")


def test_no_company_given_means_no_company_check():
    assert assess("x", GOOD, None, None) is None


PAGE = """\
[Skip to main content](#main)
* [Home](https://example.com/)
* [Markets](https://example.com/markets)  [Stocks](https://example.com/stocks)  [Crypto](https://example.com/crypto)
# Acme Q3 results
![chart](https://cdn.example.com/chart.png)
""" + ("\n\n".join([SENTENCE.strip()] * 1)) + """

Operating margin fell to 11.1% from 12.2%, which the company attributed to heavier spending on free shipping and credit provisions in its largest markets this quarter.

We use cookies to improve your experience and show personalised ads across our network of websites.
Subscribe to our newsletter for daily market updates
Related articles
* [Rival Corp vs Acme: what revenue trends tell investors about these e-commerce companies](https://example.com/a)
"""


def test_navigation_is_stripped_from_a_browser_page():
    cleaned = clean_markdown_body(PAGE)
    assert "quarterly revenue growth" in cleaned and "Operating margin fell to 11.1%" in cleaned
    for junk in ("Skip to main content", "Markets", "cookies", "newsletter", "Related articles", "Rival Corp", "![", "](http"):
        assert junk not in cleaned, junk
    assert clean_markdown_body(None) == "" and not has_article_text(clean_markdown_body("[Home](https://x.com)"))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)
