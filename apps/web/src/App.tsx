import React, { useEffect, useState, useRef, useCallback } from 'react';
import {
  supabase,
  STREAMLIT_WORKER_URL,
  CONFIGURED_COMPANIES,
  METRIC_CATALOG,
  formatMetricValue,
} from './lib/supabase';
import './styles/bloomberg.css';

interface FundamentalRow {
  metric_id: string;
  fundamental_value: number | null;
  fundamental_grade: string;
  calculated_at: string;
  fiscal_year_end: string | null;
  currency: string | null;
}

interface ScoreRow {
  metric_id: string;
  weighted_sentiment: number | null;
  dispersion: number | null;
  volume: number | null;
}

interface SourceRow {
  id: string;
  source_label: string;
  title: string;
  link: string;
  published_date: string;
  date_from: string;
  matched_metrics: Array<{ metric_id: string }>;
  sentiment_label: string;
  sentiment_score: number | null;
  text_status: string;
}

interface FreshnessRow {
  news_last_updated: string | null;
  scores_last_updated: string | null;
  ratios_last_updated: string | null;
  fiscal_year_end: string | null;
  currency: string | null;
}

interface ScreeningRunRow {
  ticker: string;
  run_id: string;
  nonce: string | null;
  status: 'idle' | 'running' | 'complete' | 'error';
  mode: string;
  seed: number;
  progress_message: string | null;
  summary: {
    ticker: string;
    growth_relevance_verdict: string;
    growth_relevance_reasoning: string;
    key_positives: string[];
    key_negatives: string[];
    news_narrative_summary: string;
    financial_highlights: string;
    risk_concerns: string;
    risk_flag_count: number;
    disclaimer: string;
  } | null;
  quality: {
    score: number;
    passed_all: boolean;
    checks: Record<string, boolean>;
    details: Record<string, string>;
  } | null;
  monitoring: {
    run_id: string;
    tool_calls_total: number;
    llm_calls_total: number;
    llm_total_tokens: number;
    wall_clock_seconds: number;
  } | null;
  pipeline_counts: {
    notices?: Array<{ level: string; text: string }>;
    sec_filings_ingested?: number;
    yahoo_articles_ingested?: number;
  } | null;
  manifest: {
    prompt_version?: string;
    model_name?: string;
    input_data_hash?: string;
    generated_at?: string;
  } | null;
  error_message: string | null;
  updated_at: string;
  completed_at: string | null;
}

const GITHUB_REPO_URL = 'https://github.com/aks861999/Equity-Research-AI';

// How long the page waits for the Streamlit worker before it gives up (cold start + a run).
const WORKER_TIMEOUT_MS = 4 * 60 * 1000;

export default function App() {
  const [ticker, setTicker] = useState<string>('MELI');
  const [seed, setSeed] = useState<number>(42);
  const [refreshLive, setRefreshLive] = useState<boolean>(true);
  const [activeBottomTab, setActiveBottomTab] = useState<'SOURCES' | 'RISK' | 'AUDIT'>('SOURCES');
  const [scoredOnly, setScoredOnly] = useState<boolean>(false);

  // Database state
  const [fundamentals, setFundamentals] = useState<Record<string, FundamentalRow>>({});
  const [scores, setScores] = useState<Record<string, ScoreRow>>({});
  const [sources, setSources] = useState<SourceRow[]>([]);
  const [freshness, setFreshness] = useState<FreshnessRow | null>(null);
  const [screeningRun, setScreeningRun] = useState<ScreeningRunRow | null>(null);
  const [loadingDb, setLoadingDb] = useState<boolean>(true);

  // Live Worker Polling State
  const [isPolling, setIsPolling] = useState<boolean>(false);
  const [workerIframeUrl, setWorkerIframeUrl] = useState<string | null>(null);
  const [statusLog, setStatusLog] = useState<string>('READY // DIRECT SUPABASE SDK CONNECTED');
  const [utcClock, setUtcClock] = useState<string>(new Date().toISOString().slice(11, 19));

  const pollTimerRef = useRef<number | null>(null);

  // Live UTC Terminal Clock
  useEffect(() => {
    const timer = window.setInterval(() => {
      setUtcClock(new Date().toISOString().slice(11, 19));
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  // Fetch all prepared & latest screening data from Supabase
  const fetchTickerData = useCallback(async (targetTicker: string) => {
    setLoadingDb(true);
    // Never leave the previous company's numbers on screen while the new ones load (or if they fail to load).
    setFundamentals({});
    setScores({});
    setSources([]);
    setFreshness(null);
    setScreeningRun(null);
    try {
      const [fundRes, scoreRes, srcRes, freshRes, runRes] = await Promise.all([
        supabase.from('fundamental_metrics').select('*').eq('ticker', targetTicker),
        supabase.from('metric_scores').select('*').eq('ticker', targetTicker),
        supabase.from('v_reference_sources').select('*').eq('ticker', targetTicker).limit(250),
        supabase.from('v_data_freshness').select('*').eq('ticker', targetTicker).maybeSingle(),
        supabase.from('screening_runs').select('*').eq('ticker', targetTicker).maybeSingle(),
      ]);

      const fundMap: Record<string, FundamentalRow> = {};
      (fundRes.data || []).forEach((r: FundamentalRow) => {
        fundMap[r.metric_id] = r;
      });
      setFundamentals(fundMap);

      const scoreMap: Record<string, ScoreRow> = {};
      (scoreRes.data || []).forEach((r: ScoreRow) => {
        scoreMap[r.metric_id] = r;
      });
      setScores(scoreMap);

      // Sort sources: scored news first, then by published_date DESC
      const sortedSources = ((srcRes.data || []) as SourceRow[]).sort((a, b) => {
        const aScored = a.sentiment_label ? 1 : 0;
        const bScored = b.sentiment_label ? 1 : 0;
        if (bScored !== aScored) return bScored - aScored;
        return (b.published_date || '').localeCompare(a.published_date || '');
      });
      setSources(sortedSources);

      setFreshness((freshRes.data as FreshnessRow) || null);
      setScreeningRun((runRes.data as ScreeningRunRow) || null);
      setStatusLog(`LOADED ${targetTicker} // ${Object.keys(fundMap).length} RATIOS, ${sortedSources.length} SOURCES`);
    } catch (err) {
      setStatusLog(`SUPABASE READ ERROR: ${String(err)}`);
    } finally {
      setLoadingDb(false);
    }
  }, []);

  useEffect(() => {
    fetchTickerData(ticker);
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, [ticker, fetchTickerData]);

  // Asynchronous Trigger + Supabase Polling Loop
  const triggerLiveScreening = (targetTicker = ticker) => {
    if (!STREAMLIT_WORKER_URL) {
      setStatusLog('DEMO MODE (SUPABASE DIRECT): Set VITE_STREAMLIT_WORKER_URL in Vercel to enable live worker runs.');
      fetchTickerData(targetTicker);
      return;
    }

    const nonce = `${Date.now()}`;
    const url = `${STREAMLIT_WORKER_URL}/?embed=true&autorun=1&ticker=${targetTicker}&seed=${seed}&refresh=${
      refreshLive ? '1' : '0'
    }&nonce=${nonce}`;

    setWorkerIframeUrl(url);
    setIsPolling(true);
    setStatusLog(`[SYS] TRIGGERING LIVE DEMO WORKER FOR ${targetTicker} (NONCE ${nonce.slice(-6)})...`);

    if (pollTimerRef.current) clearInterval(pollTimerRef.current);

    const startedAt = Date.now();
    pollTimerRef.current = window.setInterval(async () => {
      // A sleeping worker needs about 30-45 s to wake and a run takes up to a few minutes. If nothing
      // finished by then, stop waiting and show the prepared data instead of spinning forever.
      if (Date.now() - startedAt > WORKER_TIMEOUT_MS) {
        if (pollTimerRef.current) clearInterval(pollTimerRef.current);
        setIsPolling(false);
        setWorkerIframeUrl(null);
        setStatusLog('[TIMEOUT] The live worker did not answer in time (it may still be waking up). Showing the prepared data; try again in a minute.');
        return;
      }
      const { data } = await supabase
        .from('screening_runs')
        .select('*')
        .eq('ticker', targetTicker)
        .maybeSingle();

      if (!data) return;
      const runRow = data as ScreeningRunRow;
      setScreeningRun(runRow);

      if (runRow.nonce === nonce && runRow.status === 'running') {
        setStatusLog(runRow.progress_message || `[RUNNING] Processing ${targetTicker}...`);
      } else if (runRow.nonce !== nonce && runRow.status === 'running') {
        setStatusLog(`[QUEUE] Another screening of ${targetTicker} is in progress; waiting for the worker...`);
      } else if (runRow.nonce === nonce && runRow.status === 'complete') {
        if (pollTimerRef.current) clearInterval(pollTimerRef.current);
        setIsPolling(false);
        setWorkerIframeUrl(null);
        await fetchTickerData(targetTicker);
        setStatusLog(`[COMPLETE] ${targetTicker} LIVE DEMO SCREENING UPDATED`);
      } else if (runRow.nonce === nonce && runRow.status === 'error') {
        if (pollTimerRef.current) clearInterval(pollTimerRef.current);
        setIsPolling(false);
        setWorkerIframeUrl(null);
        setStatusLog(`[ERROR] ${runRow.error_message || 'Worker failed'}`);
      }
    }, 2500);
  };

  const company = CONFIGURED_COMPANIES.find((c) => c.ticker === ticker) || CONFIGURED_COMPANIES[0];
  const currency = freshness?.currency || fundamentals['m01_revenue_growth']?.currency || 'USD';
  const fyEnd = freshness?.fiscal_year_end || fundamentals['m01_revenue_growth']?.fiscal_year_end || 'N/A';

  const growthMetricIds = Object.keys(METRIC_CATALOG).filter((id) => id.startsWith('m'));
  const riskMetricIds = Object.keys(METRIC_CATALOG).filter((id) => id.startsWith('r'));
  const redRiskCount = riskMetricIds.filter((id) => fundamentals[id]?.fundamental_grade === 'Red').length;

  const displayedSources = scoredOnly ? sources.filter((s) => s.sentiment_label !== '') : sources;
  const summary = screeningRun?.summary;

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* Hidden 0x0 iframe to wake & execute Streamlit Cloud Worker via WebSocket */}
      {workerIframeUrl && (
        <iframe
          src={workerIframeUrl}
          title="streamlit-worker"
          style={{ width: 0, height: 0, border: 0, position: 'absolute', visibility: 'hidden' }}
        />
      )}

      {/* 1. TOP BLOOMBERG COMMAND BAR */}
      <header className="bb-topbar">
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ background: 'var(--bb-amber)', color: '#000', fontWeight: 700, padding: '2px 6px' }}>
            EQUITY RESEARCH AI
          </span>
          <span style={{ color: 'var(--bb-cyan)', fontWeight: 700 }}>EQUITY SCREENING TERMINAL</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '11px' }}>
          <a
            className="bb-btn bb-link-btn"
            href={GITHUB_REPO_URL}
            target="_blank"
            rel="noreferrer"
            title="Open the source code on GitHub"
          >
            <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" style={{ marginRight: 5 }}>
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
            </svg>
            GITHUB
          </a>
          <span style={{ color: 'var(--bb-green)' }}>● DEMO MODE (ONLINE)</span>
          <span style={{ color: 'var(--bb-amber)' }}>{utcClock} UTC</span>
        </div>
      </header>

      {/* 2. TICKER SELECTOR & ACTION RIBBON */}
      <div
        className="bb-ribbon"
        style={{
          background: '#0b0f16',
          borderBottom: '1px solid var(--bb-border)',
          padding: '5px 10px',
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '8px',
        }}
      >
        <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ color: 'var(--bb-muted)', marginRight: '4px' }}>SECURITIES:</span>
          {CONFIGURED_COMPANIES.map((c) => (
            <button
              key={c.ticker}
              className={`bb-btn ${ticker === c.ticker ? 'bb-btn-active' : ''}`}
              onClick={() => setTicker(c.ticker)}
            >
              {c.ticker}
            </button>
          ))}
        </div>

        <div className="bb-ribbon-actions" style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '4px', color: 'var(--bb-muted)', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={refreshLive}
              onChange={(e) => setRefreshLive(e.target.checked)}
            />
            LIVE SEC/YAHOO REFRESH
          </label>
          <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
            <span style={{ color: 'var(--bb-muted)' }}>SEED:</span>
            <input
              type="number"
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value) || 42)}
              style={{
                width: '54px',
                background: '#000',
                color: 'var(--bb-amber)',
                border: '1px solid var(--bb-border)',
                padding: '2px 4px',
                fontFamily: 'inherit',
              }}
            />
          </div>
          <button
            className="bb-btn bb-btn-go"
            disabled={isPolling}
            onClick={() => triggerLiveScreening(ticker)}
          >
            {isPolling ? '⟳ EXECUTING PIPELINE...' : 'RUN SCREENING <GO>'}
          </button>
        </div>
      </div>

      {/* 3. SECURITY HEADER & DATA FRESHNESS BAR */}
      <div
        style={{
          background: '#06080c',
          borderBottom: '1px solid var(--bb-border)',
          padding: '4px 10px',
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'space-between',
          gap: '12px',
          fontSize: '11px',
        }}
      >
        <div>
          <strong style={{ color: 'var(--bb-amber)', fontSize: '13px' }}>
            {company.ticker} US Equity
          </strong>{' '}
          <span style={{ color: '#fff', fontWeight: 600 }}>{company.name}</span>{' '}
          <span style={{ color: 'var(--bb-cyan)' }}>[{company.sector}]</span>{' '}
          <span style={{ color: 'var(--bb-muted)' }}>// SEC FORM: {company.secForm}</span>
        </div>
        <div style={{ display: 'flex', gap: '4px 14px', flexWrap: 'wrap', color: 'var(--bb-muted)' }}>
          <span>
            ANNUAL FY END: <strong style={{ color: 'var(--bb-text)' }}>{fyEnd} ({currency})</strong>
          </span>
          <span>
            RATIOS CALC: <strong style={{ color: 'var(--bb-text)' }}>{freshness?.ratios_last_updated || 'N/A'}</strong>
          </span>
          <span>
            FINBERT SCORES: <strong style={{ color: 'var(--bb-text)' }}>{freshness?.scores_last_updated || 'N/A'}</strong>
          </span>
          <span>
            SEARCH NEWS: <strong style={{ color: 'var(--bb-text)' }}>{freshness?.news_last_updated || 'N/A'}</strong>
          </span>
        </div>
      </div>

      {/* 4. MAIN 2-COLUMN TERMINAL WORKSPACE */}
      <main className="bb-workspace" style={{ flex: 1 }}>
        {/* LEFT COLUMN: AI PM SCREENING BRIEF + BOTTOM TABS */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <section className="bb-panel" style={{ flex: 1 }}>
            <div className="bb-panel-header">
              <span>[1] PM SCREENING SUMMARY // PYDANTIC-AI AGENT BRIEF</span>
              {summary && (
                <span
                  className={`bb-badge ${
                    summary.growth_relevance_verdict === 'Potentially relevant'
                      ? 'bb-badge-green'
                      : summary.growth_relevance_verdict === 'Not clearly relevant'
                      ? 'bb-badge-yellow'
                      : 'bb-grade-NA'
                  }`}
                >
                  VERDICT: {summary.growth_relevance_verdict}
                </span>
              )}
            </div>

            <div className="bb-panel-body" style={{ display: 'flex', flexDirection: 'column', gap: '26px', padding: '14px' }}>
              {isPolling && (
                <div
                  style={{
                    background: '#141100',
                    border: '1px solid var(--bb-amber)',
                    padding: '8px',
                    color: 'var(--bb-amber)',
                  }}
                >
                  <strong>⚡ LIVE DEMO WORKER ACTIVE:</strong> {statusLog}
                </div>
              )}

              {screeningRun?.pipeline_counts?.notices?.map((n, idx) => (
                <div
                  key={idx}
                  style={{
                    background: '#0d1520',
                    borderLeft: '3px solid var(--bb-cyan)',
                    padding: '4px 8px',
                    fontSize: '11px',
                    color: 'var(--bb-cyan)',
                  }}
                >
                  [LIVE FEED] {n.text}
                </div>
              ))}

              {summary && !isPolling && (
                <div
                  style={{
                    background: '#0d1520',
                    borderLeft: '3px solid var(--bb-muted)',
                    padding: '6px 10px',
                    fontSize: '11px',
                    color: 'var(--bb-muted)',
                    lineHeight: 1.6,
                  }}
                >
                  SAVED RESULT of the last screening of {ticker}
                  {screeningRun?.completed_at
                    ? `, finished ${new Date(screeningRun.completed_at).toISOString().replace('T', ' ').slice(0, 16)} UTC`
                    : ''}
                  {screeningRun?.manifest?.model_name ? ` // ${screeningRun.manifest.model_name}` : ''}
                  {screeningRun?.manifest?.prompt_version ? ` // ${screeningRun.manifest.prompt_version}` : ''}.
                  It is read from the database, not produced now. Press RUN SCREENING for a fresh one.
                </div>
              )}

              {!summary ? (
                <div style={{ padding: '20px', color: 'var(--bb-muted)', textAlign: 'center' }}>
                  No cached AI screening brief stored in <code>screening_runs</code> for {ticker} yet.
                  <br />
                  Click <strong>RUN SCREENING &lt;GO&gt;</strong> above to execute the agent and store the brief.
                </div>
              ) : (
                <>
                  {/* Q1 */}
                  <div>
                    <div style={{ color: 'var(--bb-amber)', fontWeight: 700, marginBottom: '3px' }}>
                      Q1 // GROWTH-STRATEGY RELEVANCE
                    </div>
                    <p style={{ color: 'var(--bb-text)' }}>{summary.growth_relevance_reasoning}</p>
                  </div>

                  {/* Q2 */}
                  <div>
                    <div style={{ color: 'var(--bb-amber)', fontWeight: 700, marginBottom: '4px' }}>
                      Q2 // KEY DRIVERS FROM RECENT NEWS &amp; SEC FILINGS
                    </div>
                    <div className="bb-two-col">
                      <div style={{ background: '#07130d', border: '1px solid #133926', padding: '12px' }}>
                        <div style={{ color: 'var(--bb-green)', fontWeight: 700, marginBottom: '10px' }}>
                          [+] KEY POSITIVES
                        </div>
                        {summary.key_positives.map((p, i) => (
                          <div key={i} style={{ marginBottom: '10px', fontSize: '11px', lineHeight: 1.6 }}>
                            <span style={{ color: 'var(--bb-green)' }}>+</span> {p}
                          </div>
                        ))}
                      </div>
                      <div style={{ background: '#180909', border: '1px solid #3d1717', padding: '12px' }}>
                        <div style={{ color: 'var(--bb-red)', fontWeight: 700, marginBottom: '10px' }}>
                          [-] KEY NEGATIVES / RISKS
                        </div>
                        {summary.key_negatives.map((n, i) => (
                          <div key={i} style={{ marginBottom: '10px', fontSize: '11px', lineHeight: 1.6 }}>
                            <span style={{ color: 'var(--bb-red)' }}>-</span> {n}
                          </div>
                        ))}
                      </div>
                    </div>
                    <div style={{ color: 'var(--bb-muted)', fontSize: '11px', marginTop: '14px', lineHeight: 1.6 }}>
                      <strong>NARRATIVE:</strong> {summary.news_narrative_summary}
                    </div>
                  </div>

                  {/* Q3 */}
                  <div>
                    <div style={{ color: 'var(--bb-amber)', fontWeight: 700, marginBottom: '3px' }}>
                      Q3 // FINANCIAL HIGHLIGHTS (FY END {fyEnd})
                    </div>
                    <p>{summary.financial_highlights}</p>
                  </div>

                  {/* Q4 */}
                  <div>
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        marginBottom: '3px',
                      }}
                    >
                      <span style={{ color: 'var(--bb-amber)', fontWeight: 700 }}>
                        Q4 // RISK CONCERNS &amp; DISCLOSURE SIGNALS
                      </span>
                      <span className={`bb-badge ${summary.risk_flag_count > 0 ? 'bb-badge-red' : 'bb-badge-green'}`}>
                        RED RISK FLAGS: {summary.risk_flag_count} / 11
                      </span>
                    </div>
                    <p>{summary.risk_concerns}</p>
                  </div>

                  <div
                    style={{
                      borderTop: '1px solid var(--bb-border)',
                      paddingTop: '6px',
                      fontSize: '10px',
                      color: 'var(--bb-muted)',
                    }}
                  >
                    {summary.disclaimer}
                  </div>
                </>
              )}
            </div>
          </section>

          {/* BOTTOM LEFT PANEL: TABBED RISK / SOURCES / AUDIT */}
          <section className="bb-panel bb-bottom-panel">
            <div className="bb-panel-header">
              <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                <button
                  className={`bb-btn ${activeBottomTab === 'SOURCES' ? 'bb-btn-active' : ''}`}
                  onClick={() => setActiveBottomTab('SOURCES')}
                >
                  [F5] WIRE &amp; SEC SOURCES ({sources.length})
                </button>
                <button
                  className={`bb-btn ${activeBottomTab === 'RISK' ? 'bb-btn-active' : ''}`}
                  onClick={() => setActiveBottomTab('RISK')}
                >
                  [F4] 11 RISK RATIOS ({redRiskCount} RED)
                </button>
                <button
                  className={`bb-btn ${activeBottomTab === 'AUDIT' ? 'bb-btn-active' : ''}`}
                  onClick={() => setActiveBottomTab('AUDIT')}
                >
                  [F6] QUALITY &amp; MANIFEST
                </button>
              </div>
              <a
                href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${ticker}&type=10-K&owner=include&count=40`}
                target="_blank"
                rel="noreferrer"
                style={{ color: 'var(--bb-cyan)', textDecoration: 'none', fontSize: '10px' }}
              >
                SEC EDGAR 10-K FEED ↗
              </a>
            </div>

            <div className="bb-panel-body" style={{ flex: 1 }}>
              {activeBottomTab === 'SOURCES' && (
                <>
                  <div style={{ marginBottom: '6px', display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: '4px 12px' }}>
                    <label style={{ color: 'var(--bb-muted)', cursor: 'pointer', fontSize: '11px' }}>
                      <input
                        type="checkbox"
                        checked={scoredOnly}
                        onChange={(e) => setScoredOnly(e.target.checked)}
                      />{' '}
                      ONLY FINBERT-SCORED ARTICLES
                    </label>
                    <span style={{ color: 'var(--bb-muted)', fontSize: '11px' }}>
                      SHOWING {displayedSources.length} OF {sources.length} STORED ITEMS
                    </span>
                  </div>
                  <table className="bb-table">
                    <thead>
                      <tr>
                        <th>DATE</th>
                        <th>TYPE</th>
                        <th>HEADLINE / FILING SECTION</th>
                        <th>METRICS</th>
                        <th>FINBERT</th>
                      </tr>
                    </thead>
                    <tbody>
                      {displayedSources.map((src) => {
                        const mTags = Array.from(
                          new Set((src.matched_metrics || []).map((m) => m.metric_id?.split('_')[0]))
                        ).join(', ');
                        return (
                          <tr key={src.id}>
                            <td style={{ whiteSpace: 'nowrap', color: 'var(--bb-muted)' }}>
                              {src.published_date || 'UNDATED'}
                            </td>
                            <td style={{ whiteSpace: 'nowrap', color: 'var(--bb-cyan)' }}>{src.source_label}</td>
                            <td>
                              {src.link ? (
                                <a
                                  href={src.link}
                                  target="_blank"
                                  rel="noreferrer"
                                  style={{ color: 'var(--bb-text)', textDecoration: 'none' }}
                                >
                                  {src.title} <span style={{ color: 'var(--bb-amber)' }}>↗</span>
                                </a>
                              ) : (
                                src.title
                              )}
                            </td>
                            <td style={{ color: 'var(--bb-amber)' }}>{mTags}</td>
                            <td style={{ whiteSpace: 'nowrap' }}>
                              {src.sentiment_score !== null ? (
                                <span
                                  style={{
                                    color:
                                      src.sentiment_score > 0.15
                                        ? 'var(--bb-green)'
                                        : src.sentiment_score < -0.15
                                        ? 'var(--bb-red)'
                                        : 'var(--bb-yellow)',
                                  }}
                                >
                                  {src.sentiment_score > 0 ? '+' : ''}
                                  {Number(src.sentiment_score).toFixed(2)} ({src.sentiment_label})
                                </span>
                              ) : (
                                <span style={{ color: 'var(--bb-muted)' }}>{src.text_status}</span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </>
              )}

              {activeBottomTab === 'RISK' && (
                <table className="bb-table">
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>RISK METRIC (11 SEC RATIOS)</th>
                      <th>VALUE</th>
                      <th>SAFE BENCH</th>
                      <th>GRADE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {riskMetricIds.map((id) => {
                      const meta = METRIC_CATALOG[id];
                      const row = fundamentals[id];
                      const grade = row?.fundamental_grade || 'N/A';
                      return (
                        <tr key={id}>
                          <td style={{ color: 'var(--bb-muted)' }}>{id.slice(0, 3).toUpperCase()}</td>
                          <td style={{ fontWeight: 600 }}>{meta.name}</td>
                          <td style={{ fontWeight: 700 }}>
                            {formatMetricValue(id, row?.fundamental_value, currency)}
                          </td>
                          <td style={{ color: 'var(--bb-muted)' }}>{meta.goodDir}</td>
                          <td>
                            <span className={`bb-badge bb-grade-${grade.replace('/', '')}`}>{grade}</span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}

              {activeBottomTab === 'AUDIT' && (
                <div className="bb-two-col bb-two-col-tight">
                  <div>
                    <div style={{ color: 'var(--bb-amber)', fontWeight: 700, marginBottom: '4px' }}>
                      QUALITY EVALUATION ({screeningRun?.quality ? `${Math.round(screeningRun.quality.score * 100)}%` : 'N/A'})
                    </div>
                    {screeningRun?.quality ? (
                      Object.entries(screeningRun.quality.checks).map(([k, passed]) => (
                        <div key={k} style={{ marginBottom: '3px', fontSize: '11px' }}>
                          <span style={{ color: passed ? 'var(--bb-green)' : 'var(--bb-red)', fontWeight: 700 }}>
                            [{passed ? 'PASS' : 'FAIL'}]
                          </span>{' '}
                          {k}
                          {screeningRun.quality?.details?.[k] && (
                            <div style={{ color: 'var(--bb-muted)', paddingLeft: '14px' }}>
                              ↳ {screeningRun.quality.details[k]}
                            </div>
                          )}
                        </div>
                      ))
                    ) : (
                      <div style={{ color: 'var(--bb-muted)' }}>No quality report stored yet.</div>
                    )}
                  </div>
                  <div>
                    <div style={{ color: 'var(--bb-amber)', fontWeight: 700, marginBottom: '4px' }}>
                      MONITORING &amp; REPRODUCIBILITY MANIFEST
                    </div>
                    <div style={{ color: 'var(--bb-muted)', fontSize: '11px', lineHeight: 1.6 }}>
                      <div>RUN ID: <span style={{ color: 'var(--bb-text)' }}>{screeningRun?.run_id || 'N/A'}</span></div>
                      <div>MODEL: <span style={{ color: 'var(--bb-text)' }}>{screeningRun?.manifest?.model_name || 'N/A'}</span></div>
                      <div>PROMPT VER: <span style={{ color: 'var(--bb-text)' }}>{screeningRun?.manifest?.prompt_version || 'N/A'}</span></div>
                      <div>INPUT SHA-256: <span style={{ color: 'var(--bb-cyan)' }}>{screeningRun?.manifest?.input_data_hash || 'N/A'}</span></div>
                      <div>TOOL CALLS: <span style={{ color: 'var(--bb-text)' }}>{screeningRun?.monitoring?.tool_calls_total ?? 'N/A'}</span></div>
                      <div>LLM CALLS: <span style={{ color: 'var(--bb-text)' }}>{screeningRun?.monitoring?.llm_calls_total ?? 'N/A'}</span></div>
                      <div>TOTAL TOKENS: <span style={{ color: 'var(--bb-text)' }}>{screeningRun?.monitoring?.llm_total_tokens ?? 'N/A'}</span></div>
                      <div>WALL CLOCK: <span style={{ color: 'var(--bb-text)' }}>{screeningRun?.monitoring?.wall_clock_seconds ?? 'N/A'}s</span></div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </section>
        </div>

        {/* RIGHT COLUMN: 25 GROWTH FUNDAMENTALS + FINBERT SENTIMENT MATRIX */}
        <section className="bb-panel">
          <div className="bb-panel-header">
            <span>[2] 25 GROWTH FUNDAMENTALS &amp; FINBERT SENTIMENT MATRIX ({ticker})</span>
            <span style={{ color: 'var(--bb-cyan)' }}>
              {loadingDb ? 'SYNCING...' : `FY ${fyEnd} // ${currency}`}
            </span>
          </div>
          <div className="bb-panel-body" style={{ padding: 0 }}>
            <table className="bb-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>METRIC NAME</th>
                  <th style={{ textAlign: 'right' }}>ANNUAL VALUE</th>
                  <th>GRADE</th>
                  <th>FINBERT SENTIMENT</th>
                  <th style={{ textAlign: 'right' }}>DISP (σ)</th>
                  <th style={{ textAlign: 'right' }}>VOL</th>
                </tr>
              </thead>
              <tbody>
                {!loadingDb && Object.keys(fundamentals).length === 0 && (
                  <tr>
                    <td colSpan={7} style={{ padding: '16px', color: 'var(--bb-yellow)' }}>
                      NO PREPARED DATA PUBLISHED FOR {ticker}. Run the backend, then publish_to_supabase.py.
                    </td>
                  </tr>
                )}
                {(['GROWTH', 'PROFITABILITY', 'CAPITAL EFFICIENCY', 'REINVESTMENT', 'LEVERAGE & LIQUIDITY'] as const).map(
                  (theme) => {
                    const themeIds = growthMetricIds.filter((id) => METRIC_CATALOG[id].theme === theme);
                    return (
                      <React.Fragment key={theme}>
                        <tr className="bb-theme-row">
                          <td colSpan={7}>// {theme}</td>
                        </tr>
                        {themeIds.map((id) => {
                          const meta = METRIC_CATALOG[id];
                          const fRow = fundamentals[id];
                          const sRow = scores[id];
                          const grade = fRow?.fundamental_grade || 'N/A';
                          const ws = sRow?.weighted_sentiment;
                          const pctBar = ws !== null && ws !== undefined ? Math.min(Math.max((ws + 1) * 50, 0), 100) : 50;

                          return (
                            <tr key={id}>
                              <td style={{ color: 'var(--bb-muted)' }}>M{String(meta.num).padStart(2, '0')}</td>
                              <td style={{ fontWeight: 500 }}>{meta.name}</td>
                              <td style={{ textAlign: 'right', fontWeight: 700, color: '#fff' }}>
                                {formatMetricValue(id, fRow?.fundamental_value, currency)}
                              </td>
                              <td>
                                <span
                                  className={`bb-badge bb-grade-${grade.replace('/', '')}`}
                                  title={
                                    grade === 'Ungraded'
                                      ? 'Not graded: an input the grading rule needs is missing for this company.'
                                      : undefined
                                  }
                                >
                                  {grade === 'Ungraded' ? 'NO RULE' : grade}
                                </span>
                              </td>
                              <td>
                                {ws !== null && ws !== undefined ? (
                                  <div className="bb-sent-bar-wrap">
                                    <div className="bb-sent-track">
                                      <div
                                        style={{
                                          width: `${pctBar}%`,
                                          height: '100%',
                                          background:
                                            ws > 0.15
                                              ? 'var(--bb-green)'
                                              : ws < -0.15
                                              ? 'var(--bb-red)'
                                              : 'var(--bb-yellow)',
                                        }}
                                      />
                                    </div>
                                    <span
                                      style={{
                                        color:
                                          ws > 0.15
                                            ? 'var(--bb-green)'
                                            : ws < -0.15
                                            ? 'var(--bb-red)'
                                            : 'var(--bb-yellow)',
                                        fontWeight: 600,
                                      }}
                                    >
                                      {ws > 0 ? '+' : ''}
                                      {ws.toFixed(2)}
                                    </span>
                                  </div>
                                ) : (
                                  <span style={{ color: 'var(--bb-muted)' }}>—</span>
                                )}
                              </td>
                              <td style={{ textAlign: 'right', color: 'var(--bb-muted)' }}>
                                {sRow?.dispersion !== null && sRow?.dispersion !== undefined
                                  ? sRow.dispersion.toFixed(2)
                                  : '—'}
                              </td>
                              <td style={{ textAlign: 'right', color: 'var(--bb-cyan)' }}>
                                {sRow?.volume ?? '—'}
                              </td>
                            </tr>
                          );
                        })}
                      </React.Fragment>
                    );
                  }
                )}
              </tbody>
            </table>
            <div style={{ padding: '10px 12px', fontSize: '10px', color: 'var(--bb-muted)', lineHeight: 1.6 }}>
              <div>
                PREPARED DATA: the annual ratios and the FinBERT sentiment are computed on the backend and change only when
                it publishes (ratios {freshness?.ratios_last_updated || 'N/A'}, scores {freshness?.scores_last_updated || 'N/A'}).
                RUN SCREENING refreshes SEC filings and Yahoo headlines and rewrites the brief, not this table.
              </div>
              <div style={{ marginTop: '6px' }}>
                GRADES follow the design document: level bands, plus trend and relative rules (for example profit growth
                against revenue growth). NO RULE = an input the rule needs is missing for this company.
              </div>
            </div>
          </div>
        </section>
      </main>

      {/* 5. TERMINAL FOOTER STATUS BAR */}
      <footer className="bb-statusbar">
        <div>{statusLog}</div>
        <div>
          SUPABASE RLS: <span style={{ color: 'var(--bb-green)' }}>READ-ONLY ANON</span> | WORKER:{' '}
          <span style={{ color: STREAMLIT_WORKER_URL ? 'var(--bb-green)' : 'var(--bb-yellow)' }}>
            {STREAMLIT_WORKER_URL ? 'STREAMLIT CLOUD LINKED' : 'STATIC DB MODE'}
          </span>
        </div>
      </footer>
    </div>
  );
}
