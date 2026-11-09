import { createClient } from '@supabase/supabase-js';

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL || '';
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY || '';
export const STREAMLIT_WORKER_URL = (import.meta.env.VITE_STREAMLIT_WORKER_URL || '').replace(/\/$/, '');

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

export interface CompanyInfo {
  ticker: string;
  name: string;
  secForm: string;
  sector: string;
}

export const CONFIGURED_COMPANIES: CompanyInfo[] = [
  { ticker: 'MELI', name: 'MercadoLibre Inc.', secForm: '8-K', sector: 'LATAM E-COMMERCE & FINTECH' },
  { ticker: 'GLBE', name: 'Global-E Online Ltd.', secForm: '6-K', sector: 'CROSS-BORDER E-COMMERCE' },
  { ticker: 'AAPL', name: 'Apple Inc.', secForm: '8-K', sector: 'CONSUMER TECHNOLOGY' },
  { ticker: 'NVDA', name: 'NVIDIA Corporation', secForm: '8-K', sector: 'SEMICONDUCTORS & AI' },
  { ticker: 'KO',   name: 'The Coca-Cola Company', secForm: '8-K', sector: 'CONSUMER STAPLES' },
  { ticker: 'WMT',  name: 'Walmart Inc.', secForm: '8-K', sector: 'HYPERMARKETS & RETAIL' },
  { ticker: 'PFE',  name: 'Pfizer Inc.', secForm: '8-K', sector: 'PHARMACEUTICALS' },
  { ticker: 'T',    name: 'AT&T Inc.', secForm: '8-K', sector: 'TELECOMMUNICATIONS' },
  { ticker: 'DUK',  name: 'Duke Energy Corp.', secForm: '8-K', sector: 'ELECTRIC UTILITIES' },
];

export interface MetricMeta {
  id: string;
  num: number;
  name: string;
  theme: 'GROWTH' | 'PROFITABILITY' | 'CAPITAL EFFICIENCY' | 'REINVESTMENT' | 'LEVERAGE & LIQUIDITY' | 'RISK';
  format: 'pct' | 'mult' | 'curr' | 'dec';
  goodDir: string;
}

export const METRIC_CATALOG: Record<string, MetricMeta> = {
  m01_revenue_growth:               { id: 'm01_revenue_growth', num: 1, name: 'Revenue Growth (YoY/CAGR)', theme: 'GROWTH', format: 'pct', goodDir: 'HIGHER' },
  m02_operating_profit_growth:      { id: 'm02_operating_profit_growth', num: 2, name: 'Operating Profit Growth (EBIT)', theme: 'GROWTH', format: 'pct', goodDir: 'HIGHER' },
  m03_net_income_growth:            { id: 'm03_net_income_growth', num: 3, name: 'Net Income Growth', theme: 'GROWTH', format: 'pct', goodDir: 'HIGHER' },
  m04_asset_equity_inventory_growth:{ id: 'm04_asset_equity_inventory_growth', num: 4, name: 'Asset / Balance Sheet Growth', theme: 'GROWTH', format: 'pct', goodDir: 'HIGHER' },
  m05_gross_margin:                 { id: 'm05_gross_margin', num: 5, name: 'Gross Margin', theme: 'PROFITABILITY', format: 'pct', goodDir: 'HIGHER' },
  m06_operating_margin:             { id: 'm06_operating_margin', num: 6, name: 'Operating Margin', theme: 'PROFITABILITY', format: 'pct', goodDir: 'HIGHER' },
  m07_ebit_margin:                  { id: 'm07_ebit_margin', num: 7, name: 'EBIT Margin', theme: 'PROFITABILITY', format: 'pct', goodDir: 'HIGHER' },
  m08_ebitda_margin:                { id: 'm08_ebitda_margin', num: 8, name: 'EBITDA Margin', theme: 'PROFITABILITY', format: 'pct', goodDir: 'HIGHER' },
  m09_net_profit_margin:            { id: 'm09_net_profit_margin', num: 9, name: 'Net Profit Margin', theme: 'PROFITABILITY', format: 'pct', goodDir: 'HIGHER' },
  m10_sga_to_revenue:               { id: 'm10_sga_to_revenue', num: 10, name: 'SG&A-to-Revenue', theme: 'PROFITABILITY', format: 'pct', goodDir: 'LOWER' },
  m11_roe:                          { id: 'm11_roe', num: 11, name: 'Return on Equity (ROE)', theme: 'CAPITAL EFFICIENCY', format: 'pct', goodDir: 'HIGHER' },
  m12_roa:                          { id: 'm12_roa', num: 12, name: 'Return on Assets (ROA)', theme: 'CAPITAL EFFICIENCY', format: 'pct', goodDir: 'HIGHER' },
  m13_roic:                         { id: 'm13_roic', num: 13, name: 'Return on Invested Capital (ROIC)', theme: 'CAPITAL EFFICIENCY', format: 'pct', goodDir: 'HIGHER' },
  m14_asset_turnover:               { id: 'm14_asset_turnover', num: 14, name: 'Asset Turnover', theme: 'CAPITAL EFFICIENCY', format: 'mult', goodDir: 'HIGHER' },
  m15_sustainable_growth_rate:      { id: 'm15_sustainable_growth_rate', num: 15, name: 'Sustainable Growth Rate', theme: 'REINVESTMENT', format: 'pct', goodDir: 'HIGHER' },
  m16_reinvestment_rate:            { id: 'm16_reinvestment_rate', num: 16, name: 'Reinvestment Rate', theme: 'REINVESTMENT', format: 'pct', goodDir: 'CTX' },
  m17_free_cash_flow:               { id: 'm17_free_cash_flow', num: 17, name: 'Free Cash Flow (Approx.)', theme: 'REINVESTMENT', format: 'curr', goodDir: 'HIGHER' },
  m18_capex_to_sales:               { id: 'm18_capex_to_sales', num: 18, name: 'Capex-to-Sales', theme: 'REINVESTMENT', format: 'pct', goodDir: 'CTX' },
  m19_debt_to_equity:               { id: 'm19_debt_to_equity', num: 19, name: 'Debt-to-Equity', theme: 'LEVERAGE & LIQUIDITY', format: 'mult', goodDir: 'LOWER' },
  m20_debt_to_assets:               { id: 'm20_debt_to_assets', num: 20, name: 'Debt-to-Assets', theme: 'LEVERAGE & LIQUIDITY', format: 'pct', goodDir: 'LOWER' },
  m21_net_debt_to_ebitda:           { id: 'm21_net_debt_to_ebitda', num: 21, name: 'Net Debt-to-EBITDA', theme: 'LEVERAGE & LIQUIDITY', format: 'mult', goodDir: 'LOWER' },
  m22_current_ratio:                { id: 'm22_current_ratio', num: 22, name: 'Current Ratio', theme: 'LEVERAGE & LIQUIDITY', format: 'mult', goodDir: '1.5-2.0x' },
  m23_quick_ratio:                  { id: 'm23_quick_ratio', num: 23, name: 'Quick Ratio', theme: 'LEVERAGE & LIQUIDITY', format: 'mult', goodDir: 'HIGHER' },
  m24_cash_ratio:                   { id: 'm24_cash_ratio', num: 24, name: 'Cash Ratio', theme: 'LEVERAGE & LIQUIDITY', format: 'mult', goodDir: 'HIGHER' },
  m25_interest_coverage:            { id: 'm25_interest_coverage', num: 25, name: 'Interest Coverage (EBIT/Int)', theme: 'LEVERAGE & LIQUIDITY', format: 'mult', goodDir: 'HIGHER' },
  r01_altman_z_score:               { id: 'r01_altman_z_score', num: 26, name: "Altman Z''-Score (Distress)", theme: 'RISK', format: 'dec', goodDir: '> 2.60' },
  r02_debt_to_equity:               { id: 'r02_debt_to_equity', num: 27, name: 'Debt-to-Equity Ratio', theme: 'RISK', format: 'mult', goodDir: '< 1.0x' },
  r03_debt_ratio:                   { id: 'r03_debt_ratio', num: 28, name: 'Debt Ratio (Total Liab/Assets)', theme: 'RISK', format: 'pct', goodDir: '< 30%' },
  r04_net_debt_to_ebitda:           { id: 'r04_net_debt_to_ebitda', num: 29, name: 'Net Debt-to-EBITDA', theme: 'RISK', format: 'mult', goodDir: '< 2.0x' },
  r05_short_term_debt_ratio:        { id: 'r05_short_term_debt_ratio', num: 30, name: 'Short-Term Debt / Total Debt', theme: 'RISK', format: 'pct', goodDir: '< 30%' },
  r06_current_ratio:                { id: 'r06_current_ratio', num: 31, name: 'Current Ratio (Liquidity)', theme: 'RISK', format: 'mult', goodDir: '> 1.5x' },
  r07_quick_ratio:                  { id: 'r07_quick_ratio', num: 32, name: 'Quick Ratio (Acid Test)', theme: 'RISK', format: 'mult', goodDir: '> 1.0x' },
  r08_interest_coverage:            { id: 'r08_interest_coverage', num: 33, name: 'Interest Coverage Ratio', theme: 'RISK', format: 'mult', goodDir: '> 3.0x' },
  r09_retained_earnings_deficit:    { id: 'r09_retained_earnings_deficit', num: 34, name: 'Retained Earnings / Assets', theme: 'RISK', format: 'pct', goodDir: '> 20%' },
  r10_non_recurring_items:          { id: 'r10_non_recurring_items', num: 35, name: 'Non-Recurring Items / Pretax', theme: 'RISK', format: 'pct', goodDir: '0.0%' },
  r11_goodwill_intangibles:         { id: 'r11_goodwill_intangibles', num: 36, name: 'Goodwill & Intangibles / Assets', theme: 'RISK', format: 'pct', goodDir: '< 20%' },
};

export function formatMetricValue(metricId: string, val: number | null | undefined, currency = 'USD'): string {
  if (val === null || val === undefined || Number.isNaN(val)) return 'N/A';
  const meta = METRIC_CATALOG[metricId];
  if (!meta) return val.toFixed(4);
  if (meta.format === 'pct') return `${(val * 100).toFixed(1)}%`;
  if (meta.format === 'mult') return `${val.toFixed(2)}x`;
  if (meta.format === 'curr') {
    const abs = Math.abs(val);
    if (abs >= 1e9) return `${currency} ${(val / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${currency} ${(val / 1e6).toFixed(1)}M`;
    return `${currency} ${val.toLocaleString()}`;
  }
  return val.toFixed(2);
}
