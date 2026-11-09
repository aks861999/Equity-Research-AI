"""Offline checks for the agent-side changes: period/currency, the quality evaluator, the prompt.
Run: python tests/test_agent_checks.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from equity_pipeline.agent.evaluation import (  # noqa: E402
    QualityReport, _groundedness_check, _metric_bound_checks, _no_decision_language_check,
)
from equity_pipeline.agent.reproducibility import PROMPT_VERSION  # noqa: E402
from equity_pipeline.agent.schemas import (  # noqa: E402
    FundamentalsPeriod, FundamentalsToolOutput, GeneralSentimentSignal, GrowthMetric, RiskMetric, ScreeningSummary,
)
from equity_pipeline.agent.screening_agent import SYSTEM_PROMPT  # noqa: E402
from equity_pipeline.extraction.fundamentals_store import PERIOD_TYPE, upsert_metric  # noqa: E402


def _summary(**over):
    base = dict(
        ticker="ACME", growth_relevance_verdict="Potentially relevant",
        growth_relevance_reasoning="Growth is strong.", key_positives=["a"], key_negatives=["b"],
        news_narrative_summary="Mixed narrative.", financial_highlights="Revenue growth of 38.3% (Green).",
        risk_concerns="Debt ratio 0.84.", risk_flag_count=1,
    )
    base.update(over)
    return ScreeningSummary(**base)


def _fundamentals(evidence=()):
    gm = GrowthMetric(metric_id="m01", name="Revenue growth", description="d", good_direction="higher",
                      fundamental_value=0.383, fundamental_grade="Green")
    sig = GeneralSentimentSignal(source="sec_edgar_filings", label="Neutral", summary="8-K excerpts",
                                 article_or_filing_count=1, evidence=list(evidence))
    return FundamentalsToolOutput(ticker="ACME", data_as_of="x", growth_metrics=[gm], risk_metrics=[],
                                  general_sentiment=[sig])


def _decision_check(text):
    report = QualityReport()
    _no_decision_language_check(_summary(growth_relevance_reasoning=text), report)
    return report.checks["no_investment_decision_language"]


def test_sell_side_and_reported_headlines_are_not_decision_language():
    # found in the first real MELI brief: "sell-side" was flagged as advice
    assert _decision_check("Coverage splits between caution and constructive sell-side views.")
    assert _decision_check("The stock sold off after earnings, and buy-side interest faded.")
    assert _decision_check("Headline: 'Is Mercado Libre Finally a Buy?' scored negative.")
    assert _decision_check('Headline: "MercadoLibre Stock Crashed. Is It a Buy Now?" scored negative.')
    assert _decision_check("The Motley Fool's coverage was negative.")            # apostrophe is not a quote span


def test_real_decision_language_is_still_caught():
    assert not _decision_check("We recommend you buy the stock.")
    assert not _decision_check("This is a sell.")
    assert not _decision_check("A price target of $2,000 seems fair.")
    assert not _decision_check("The stock looks overweight in growth portfolios.")


def test_groundedness_accepts_figures_quoted_from_the_evidence_and_ignores_years_and_counts():
    ev = ["[SEC 8-K, 2026-11-04] Net revenues and financial income of $10,750m, up 45% YoY; TPV of $109bn."]
    text = ("Revenue growth of 38.3% (Green). The Q3 2026 8-K shows $10,750m of revenue, up 45%, "
            "with TPV of $109bn and 2 red flags.")
    report = QualityReport()
    _groundedness_check(_summary(financial_highlights=text), _fundamentals(ev), report)
    assert report.checks["groundedness_numbers_traceable"], report.details
    # the same text without the evidence has untraceable figures
    report2 = QualityReport()
    _groundedness_check(_summary(financial_highlights=text), _fundamentals(), report2)
    assert not report2.checks["groundedness_numbers_traceable"]


def test_groundedness_still_flags_invented_numbers():
    report = QualityReport()
    _groundedness_check(_summary(financial_highlights="Margin is 71.9% and EBIT is 5,432.1 and ROE is 88.8%."),
                        _fundamentals(), report)
    assert not report.checks["groundedness_numbers_traceable"]


def _bound_fundamentals():
    def g(mid, name, value, grade, sentiment=None, dispersion=None, volume=None):
        return GrowthMetric(metric_id=mid, name=name, description="d", good_direction="higher",
                            fundamental_value=value, fundamental_grade=grade,
                            weighted_sentiment=sentiment, sentiment_dispersion=dispersion, sentiment_volume=volume)

    growth = [
        g("m01_revenue_growth", "Revenue growth", 0.107, "Yellow", -0.27, 0.67, 20),
        g("m05_gross_margin", "Gross margin", 0.488, "Green", -0.57, 0.75, 8),
        g("m06_operating_margin", "Operating margin", 0.33, "Green"),
        g("m13_roic", "ROIC", 1.044, "Green"),
        g("m16_reinvestment_rate", "Reinvestment rate", 0.031, "Green"),
        g("m25_interest_coverage", "Interest coverage", None, "N/A"),
    ]
    risk = [RiskMetric(metric_id="r03_debt_ratio", name="Debt ratio", description="d", good_direction="lower",
                       fundamental_value=0.72, fundamental_grade="Red")]
    return FundamentalsToolOutput(ticker="ACME", data_as_of="x", growth_metrics=growth, risk_metrics=risk,
                                  general_sentiment=[])


def _bound(**fields):
    report = QualityReport()
    _metric_bound_checks(_summary(**fields), _bound_fundamentals(), report)
    return report


def test_a_number_must_belong_to_the_metric_it_is_written_next_to():
    good = ("Revenue growth 10.7% (Yellow), gross margin -0.57 (dispersion 0.75, volume 8), operating margin 33.0%, "
            "ROIC 104% and capex only 0.85% of sales with a 3.1% reinvestment rate. Free cash flow is about $141.5B. "
            "Guidance is 45-46% for the quarter.")
    report = _bound(financial_highlights=good, risk_concerns="Debt ratio (total liabilities/assets) of 0.72 is Red.")
    assert report.checks["numbers_match_their_metric"], report.details
    # the ROIC figure written as the operating margin (a swap), and a wrong sign
    swapped = _bound(financial_highlights="Operating margin 104.4% and gross margin +0.57.")
    assert not swapped.checks["numbers_match_their_metric"]
    detail = swapped.details["numbers_match_their_metric"].lower()
    assert "operating margin" in detail and "gross margin" in detail


def test_a_metric_without_a_value_must_not_get_a_number():
    ok = _bound(financial_highlights="Interest coverage is not supplied (N/A) in the data.")
    assert ok.checks["no_numbers_for_missing_metrics"]
    guessed = _bound(financial_highlights="Interest coverage is a comfortable 29.3x.")
    assert not guessed.checks["no_numbers_for_missing_metrics"]
    # unless the figure is quoted from the evidence (a filing excerpt)
    report = QualityReport()
    fundamentals = _bound_fundamentals()
    fundamentals.general_sentiment = [GeneralSentimentSignal(
        source="sec_edgar_filings", label="Neutral", summary="s", article_or_filing_count=1,
        evidence=["The 10-K reports interest coverage of 29.3 times."])]
    _metric_bound_checks(_summary(financial_highlights="Interest coverage is 29.3x."), fundamentals, report)
    assert report.checks["no_numbers_for_missing_metrics"], report.details


def test_grade_wording_must_not_contradict_the_grade():
    assert not _bound(risk_concerns="Debt ratio of 0.72 is strong and healthy.").checks["grade_wording_matches_grade"]
    assert not _bound(financial_highlights="Gross margin 48.8% is weak.").checks["grade_wording_matches_grade"]
    fine = _bound(risk_concerns="Debt ratio of 0.72 is Red despite strong cash generation.",
                  financial_highlights="Gross margin 48.8% is strong, ROIC 104% is excellent.")
    assert fine.checks["grade_wording_matches_grade"], fine.details


def test_risk_flag_count_is_counted_from_the_data_not_from_the_model():
    from equity_pipeline.agent.evaluation import _risk_flag_check
    from equity_pipeline.agent.screening_agent import red_risk_count

    payload = {"risk_metrics": [{"fundamental_grade": "Red"}, {"fundamental_grade": "Green"},
                                {"fundamental_grade": "red"}, {"fundamental_grade": "N/A"}]}
    assert red_risk_count(payload) == 2 and red_risk_count({}) == 0
    fundamentals = _bound_fundamentals()                      # one risk metric, graded Red
    ok, wrong = QualityReport(), QualityReport()
    _risk_flag_check(_summary(risk_flag_count=1), fundamentals, ok)
    _risk_flag_check(_summary(risk_flag_count=3), fundamentals, wrong)
    assert ok.checks["risk_flag_count_matches_grades"]
    assert not wrong.checks["risk_flag_count_matches_grades"] and "3" in wrong.details["risk_flag_count_matches_grades"]


def test_trend_and_explanation_words_need_a_source_in_the_supplied_data():
    from equity_pipeline.agent.evaluation import _unsupported_explanation_check

    text = "Growth is solid but decelerating, and ROE is flattered by buybacks."
    bad = QualityReport()
    _unsupported_explanation_check(_summary(financial_highlights=text), _fundamentals(), bad)
    assert not bad.checks["no_unsupported_explanations"]
    for word in ("decelerating", "flattered", "buybacks"):
        assert word in bad.details["no_unsupported_explanations"]
    # the same words are fine when the news or filing text the model got uses them
    news = {"recent_articles": [{"snippet": "Analysts see growth decelerating; ROE flattered by buybacks."}]}
    good = QualityReport()
    _unsupported_explanation_check(_summary(financial_highlights=text), _fundamentals(), good, news=news)
    assert good.checks["no_unsupported_explanations"], good.details
    # a plain brief with none of those words passes
    plain = QualityReport()
    _unsupported_explanation_check(_summary(), _fundamentals(), plain)
    assert plain.checks["no_unsupported_explanations"]


def test_manifests_of_every_version_load_with_the_same_keys():
    import json
    import tempfile

    from equity_pipeline.agent.reproducibility import MANIFEST_VERSION, build_run_manifest, load_run_manifest

    new = build_run_manifest("AAPL_x", "AAPL", "model", 42, {"a": 1}, {"b": 2}, {"llm_calls_total": 2}, {"ok": True},
                             risk_flag_correction={"model_said": 3, "actual": 1})
    assert new["manifest_version"] == MANIFEST_VERSION == 2
    assert new["risk_flag_count_corrected"] == {"model_said": 3, "actual": 1}
    assert build_run_manifest("AAPL_y", "AAPL", "m", 42, {}, {}, {}, {})["risk_flag_count_corrected"] is None

    old = {"run_id": "AAPL_old", "ticker": "AAPL", "prompt_version": "screening-agent-v8", "final_output": {}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(old, f)
    loaded = load_run_manifest(f.name)
    assert loaded["manifest_version"] == 1 and loaded["risk_flag_count_corrected"] is None
    assert loaded["pipeline_counts"] is None and loaded["prompt_version"] == "screening-agent-v8"


def test_period_note_and_defaults():
    p = FundamentalsPeriod(fiscal_year_end="2025-12-31", currency="USD")
    assert p.period_type == "annual" and "ANNUAL" in p.note and "different period" in p.note
    assert _fundamentals().period is None                      # old rows: honest None, not a made-up period
    assert FundamentalsToolOutput.model_fields["period"].default is None


def test_the_prompt_tells_the_agent_not_to_guess_about_periods_and_limits_length():
    for needle in ("period_type", "fiscal_year_end", "do NOT explain the difference with guesses",
                   "at most 120 words", "period is missing"):
        assert needle in SYSTEM_PROMPT, needle
    assert PROMPT_VERSION == "screening-agent-v9" and "about_company" in SYSTEM_PROMPT
    # the model may say what a ratio measures, but not why a figure is what it is, nor a trend
    assert "commonly known facts" not in SYSTEM_PROMPT
    assert "Do NOT explain WHY" in SYSTEM_PROMPT and "do NOT describe a trend" in SYSTEM_PROMPT
    assert "fetched live from the internet" not in SYSTEM_PROMPT and "as stored in" in SYSTEM_PROMPT
    assert "has_article_text" in SYSTEM_PROMPT and "ONLY here" in SYSTEM_PROMPT


def test_sec_excerpts_reach_the_model_once_only_through_the_news_tool():
    import inspect
    from equity_pipeline.agent import data_access as da

    filings = [{"form_type": "8-K", "filing_date": "2026-11-04"}, {"form_type": "8-K", "filing_date": "2026-09-14"}]
    signal = da._sec_filing_general_signal(filings)
    assert signal.evidence == [], "the fundamentals tool must carry no filing excerpts"
    assert signal.article_or_filing_count == 2 and "2026-11-04" in signal.summary and "news_tool" in signal.summary
    assert da._sec_filing_general_signal([]).label == "Unknown"
    # and building the fundamentals bundle must not download filings a second time
    assert "fetch_sec_filings" not in inspect.getsource(da.load_fundamentals_bundle)
    # the news tool reads the database too: SEC EDGAR and Yahoo are fetched once per run, by the refresh step
    news_source = inspect.getsource(da.load_news_bundle)
    assert "fetch_sec_filings" not in news_source and "fetch_yahoo_news" not in news_source


def test_about_company_means_named_in_the_title_or_named_repeatedly():
    from equity_pipeline.extraction.quality_gate import is_about_company

    filler = "Revenue rose while margins fell as spending on logistics grew across the region this quarter. "
    uber_like = ("Dara Khosrowshahi said he does not want Uber dependent on an outside gatekeeper again, and Acme and "
                 "JPMorgan are keeping outside agents away from their inventory too. ") + filler * 62     # 1 mention, ~1000 words
    burry_like = filler * 60 + "A paywalled post whose subtitle names Acme (NASDAQ:ACME) and semiconductors."   # 2 mentions, late
    about = ("Acme cut its price target after earnings. " + filler * 3) * 10                                # named every few lines
    # the real hedge-fund profile: 2 name mentions + 3 in a related-tickers block = ~7 per 1,000 words
    burry_with_tickers = (filler * 60 + "Its subtitle names Acme (NASDAQ:ACMX | ACMX Price Prediction) and chips. "
                          "Related: ACMX. More on Acme at ACMX.")
    assert not is_about_company("Uber CEO says no more gatekeepers", uber_like, "Acme", "ACME")
    assert not is_about_company("Fund manager sold chip puts", burry_like, "Acme", "ACME")
    assert not is_about_company("Fund manager sold chip puts", burry_with_tickers, "Acme", "ACMX")
    assert is_about_company("Quarterly results", about, "Acme", "ACME")
    assert is_about_company("Acme shares fall", None, "Acme", "ACME")             # title alone is enough, no text needed
    assert not is_about_company("Quarterly results", None, "Acme", "ACME")        # no title mention and no text: not shown to be about it
    assert is_about_company("anything", "x", None, None)                           # nothing to check against
    # three mentions in a long text are still just an aside; three in a short note are not
    assert not is_about_company("t", ("Acme is named. " * 3) + filler * 80, "Acme", "ACME")
    assert is_about_company("t", ("Acme is named. " * 3) + filler * 5, "Acme", "ACME")


def test_news_tool_reads_the_database_and_sends_text_only_for_items_about_the_company():
    from equity_pipeline.agent import data_access as da

    # rows as the news tool's Yahoo query returns them: (title, published_at, extraction status, body text)
    yahoo_rows = [
        ("Quarterly results", "2026-11-20", "success", "Acme reported strong quarterly results this week. " * 40),  # about it, not in the title
        ("Another headline", "2026-11-19", "rejected", None),                    # the quality gate rejected the text
        ("Acme shares rise", "2026-11-18", "pending", None),                     # title names it, text not read yet
        ("Uber CEO won't depend on Google", "2026-11-17", "success",
         "Uber management said so. " * 40 + "Acme and others keep agents away. " + "Uber said more. " * 40),   # Acme once, in passing
        ("Fund manager sold chip puts", "2026-11-16", "success", "The investor sold part of his chip puts. " * 40),
        ("Acme guidance", "2026-11-15", "success", "Acme raised guidance for the year. " * 40),
        ("Acme margins", "2026-11-14", "success", "Acme margins fell this quarter. " * 40),
        ("Acme debt", "2026-11-13", "success", "Acme debt rose. " * 40),
        ("Acme news", "2026-11-12", "success", "Acme more news. " * 40),
    ]
    filing_rows = [("8-K", "2026-11-04", "EX-99.1", "Revenue was $10,750m. " * 30),
                   ("8-K", "2026-09-14", "8-K", "Notes offering of $1,000m.")]
    queries = []

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params):
            queries.append((sql, params))
            self.rows = yahoo_rows if "yahoo_finance" in sql else filing_rows if "sec_filing" in sql else []
        def fetchall(self): return self.rows

    class Conn:
        def cursor(self): return Cur()

    class FakeDb:
        conn = Conn()

    real = da.get_company
    da.get_company = lambda t: {"sec_form_type": "8-K", "primary_search_name": "Acme"}
    try:
        out = da.load_news_bundle("ACME", FakeDb(), text_chars=300)
    finally:
        da.get_company = real

    # no network: the module no longer even imports the fetchers
    assert not hasattr(da, "fetch_yahoo_news") and not hasattr(da, "fetch_sec_filings")
    assert len(queries) == 2 and queries[0][1][0] == "ACME" and queries[1][1][0] == "ACME"   # one Yahoo read, one filings read

    arts = out.recent_articles
    assert [a.about_company for a in arts] == [True, False, True, False, False, True, True, True, True]
    # text goes to the first five items about the company that have stored text: 0, 5, 6, 7, 8
    assert [a.has_article_text for a in arts] == [True, False, False, False, False, True, True, True, True]
    assert arts[0].snippet.startswith("Acme reported strong") and len(arts[0].snippet) <= 303
    assert arts[3].snippet == "Uber CEO won't depend on Google", "not about the company: title only, never its text"
    assert arts[4].snippet == "Fund manager sold chip puts"
    assert arts[2].snippet == "Acme shares rise" and not arts[2].has_article_text   # named in the title, no text stored
    assert all(a.source == "yahoo_finance" for a in arts)

    assert [(f.form_type, f.filing_date, f.section) for f in out.recent_sec_filings] == \
        [("8-K", "2026-11-04", "EX-99.1"), ("8-K", "2026-09-14", "8-K")]
    assert out.recent_sec_filings[0].snippet.startswith("Revenue was $10,750m.")


def test_yahoo_sentiment_average_leaves_out_items_that_do_not_name_the_company():
    from equity_pipeline.agent import data_access as da

    filler = "Management discussed spending on logistics and the outlook for the next quarter in some detail. " * 50
    rows = [  # (title, published_at, score, label, stored text)
        ("Acme profits fall again", "2026-11-07", "-0.60", "negative", "Acme ..."),
        ("Uber CEO won't depend on Google", "2026-11-06", "-0.90", "negative",
         "Uber management said so, and Acme keeps agents away too. " + filler),     # Acme named once, in passing
        ("Quarterly results", "2026-11-06", "0.20", "positive", "Acme reported results. " * 30),  # about it, not in the title
        ("Fund manager sold chip puts", "2026-11-06", "-0.71", "negative", filler),
    ]

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params): self.params = params
        def fetchall(self): return rows

    class Conn:
        def cursor(self): return Cur()

    class FakeDb:
        conn = Conn()

    real = da.get_company
    da.get_company = lambda t: {"primary_search_name": "Acme"}
    try:
        signal = da._fetch_general_article_sentiment(FakeDb(), "ACME")
    finally:
        da.get_company = real

    assert signal.article_or_filing_count == 2                       # the Uber and fund-manager items are out
    assert "-0.20" in signal.summary, signal.summary                  # average of -0.60 and +0.20, not of all four
    assert "2 further scored item(s) were left out" in signal.summary and "in passing" in signal.summary
    assert len(signal.evidence) == 2 and not any("Uber" in e or "Fund manager" in e for e in signal.evidence)


def test_groundedness_counts_figures_quoted_from_the_news_payload():
    news = {"recent_sec_filings": [{"snippet": "Net revenues were $10,750m, up 45% year over year."}],
            "recent_articles": [{"snippet": "Total payment volume reached $109bn."}]}
    text = "Q3 2026 revenue of $10,750m was up 45%, with payment volume of $109bn."
    report = QualityReport()
    _groundedness_check(_summary(financial_highlights=text), _fundamentals(), report, news=news)
    assert report.checks["groundedness_numbers_traceable"], report.details
    report2 = QualityReport()
    _groundedness_check(_summary(financial_highlights=text), _fundamentals(), report2)       # without the news
    assert not report2.checks["groundedness_numbers_traceable"]


def test_stored_ratios_carry_the_period_and_currency():
    calls = []

    class Cur:
        def execute(self, sql, params):
            calls.append(params)

    class Stm:
        period_ending = "2025-12-31"
        currency = "USD"

    upsert_metric(Cur(), "MELI", "m01_revenue_growth", 0.38301, "Green", Stm())
    params = calls[0]
    assert params[0] == "MELI:m01_revenue_growth" and params[3] == 0.383
    assert params[6:] == ("2025-12-31", PERIOD_TYPE, "USD")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)
