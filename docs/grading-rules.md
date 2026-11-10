# Grading rules: every Red / Yellow / Green boundary, and why it is where it is

This page is the reference for the grades stored next to each of the 36 ratios (`fundamental_grade`).
The code is in [`src/equity_pipeline/extraction/grading.py`](../src/equity_pipeline/extraction/grading.py) (the 25 growth ratios) and [`run_risk_fundamentals.py`](../src/equity_pipeline/extraction/run_risk_fundamentals.py) (the 11 risk ratios).

| Grade | Meaning |
|---|---|
| 🟢 **Green** | strong, passes the screen |
| 🟡 **Yellow** | acceptable, monitor |
| 🔴 **Red** | concern, fails the screen. In the risk set Red means **high risk**. |
| **Ungraded** | the ratio has a value, but an input its rule needs is missing (for example last year's gross margin) |
| **N/A** | the ratio itself could not be computed |

A grade is a **first-pass screen**, not a verdict. The language model receives the grade together with its one-line rule (`GRADE_RULES`) and the screening brief explains it.

---

## 1. Where the boundaries come from, honestly

| Set | Source | Status |
|---|---|---|
| **25 growth ratios** | the project's design document, "Part 10: Decision-Boundary Table (PM Screening Thresholds)". Its authors say they researched it from current industry practice: CFI, Wall Street Prep, Damodaran (NYU Stern sector data), the S&P and Moody's leverage-tier frameworks, McKinsey SG&A benchmarking, and standard growth-investing screens (GARP, CAN SLIM, Peter Lynch's framework). | covers all 25, but is **not always a number**: nine of its rules are comparisons (with revenue growth, with last year, with peers) and four places are ambiguous or silent (section 5) |
| **11 risk ratios** | **no source document in the project.** The cut-offs are in the code. Four of them are shared with a growth ratio (so they inherit its source); `r01` follows the published Altman Z'' model; the other six (`r03`, `r05`, `r09`, `r10`, `r11`, and the lower bounds of `r06`) are conventional rules of thumb. | **provisional**: a documented source is still missing (section 4) |

Two principles from the design document apply to every grade:

1. **Most thresholds are sector-adjusted, not universal.** A good gross margin for software (65 to 85%) is a poor one for a grocer. Where a hard cross-industry number exists (ROIC against the cost of capital, net debt to EBITDA credit tiers) it is used as is. Where a metric is inherently sector-relative (margins, asset turnover) the bands are general reference points. **The project does not adjust for sector yet**, so a utility will grade Red on leverage that is normal in its sector.
2. **Trend beats level.** A PM cares more about the three-year direction than where one year falls inside a band. The project grades the **latest year** and, for nine ratios, its change on the year before.

---

## 2. Growth ratios

Format: **document** = what the design document says, **why** = the reasoning behind that boundary, **code** = what is implemented and what is an assumption.

### 2.1 Growth

| ID | 🔴 Red | 🟡 Yellow | 🟢 Green |
|---|---|---|---|
| **m01 revenue growth** (2-year CAGR) | below 10% or negative | 10 to 15% | 15% or more |

- **Document:** Green is 15 to 20%+ (25 to 30%+ for aggressive CAN SLIM style screens). Sustained growth above 30% is often unsustainable (Lynch): treat an extreme high as "verify", not an automatic Green.
- **Why:** this is a growth-strategy screen. At 15% a year sales double in about five years (72 divided by 15); at 10% it takes about seven.
- **Code:** exactly the document's bands. No upper cap is applied.

| ID | 🔴 Red | 🟡 Yellow | 🟢 Green |
|---|---|---|---|
| **m02 operating profit growth** | below revenue growth, or negative | about equal to revenue growth | above revenue growth |
| **m03 net income growth** | negative | positive but below revenue growth | at or above revenue growth |
| **m04 asset growth** | assets grow clearly faster than revenue | about equal | revenue grows faster than assets |

- **Document:** these are relative tests, not fixed numbers. m02: the pass condition is operating profit outpacing revenue (operating leverage). m03: the same logic one step lower in the income statement. m04: inventory growing faster than revenue flags demand softness or channel stuffing; revenue outgrowing assets means capital-light scaling.
- **Why:** growth that is not matched by profit is bought growth. Profit growing faster than sales shows the business becoming more efficient.
- **Code (assumption):** "about equal" means **within 2 percentage points** (`TOL_GROWTH`). m03 compares with revenue growth only (the document says "revenue/EBIT growth"). m04 compares one-year asset growth with the 2-year revenue CAGR, and covers total assets (the ratio is named "asset / equity / inventory" but only assets are computed).

### 2.2 Profitability and margins

| ID | 🔴 Red | 🟡 Yellow | 🟢 Green |
|---|---|---|---|
| **m05 gross margin** | down more than 1 point on last year | within 1 point | up more than 1 point |
| **m06 operating margin** | below 10% | 10 to 20% | above 20% |
| **m07 EBIT margin** | below 10% | 10 to 20% | above 20% |
| **m08 EBITDA margin** | below 8% | 8 to 15% | above 15% |
| **m09 net profit margin** | below 5% | 5 to 10% | above 10% |
| **m10 SG&A to revenue** | above 30%, or up more than 1 point | 20 to 30% and stable | below 20%, or down more than 1 point |

- **m05, document:** the *level* is sector-specific (software about 65 to 85%, luxury 60 to 70%, retail and distribution often 15 to 30%), so judge the trend and the level against peers, not the number alone. **Why:** a compressing margin means pricing power or cost control is slipping, whatever the level. **Code:** a **trend rule**, so a high margin that fell is Red. The level is not graded. "Flat" is 1 point (`TOL_MARGIN_TREND`), because the document's Yellow "flat" and Green "stable or expanding" overlap; stable is read as flat.
- **m06 and m07, document:** below 10% (or negative), 10 to 20%, above 20%; the two share bands because they are close proxies. Sector-adjusted: asset-heavy manufacturers run structurally lower than software or platform models. **Why:** 20% or more operating margin means the core business is very profitable.
- **m08, document:** below 8% is under the typical distribution and retail band; 8 to 15%; above 15%. Consumer discretionary reference points: retail about 4 to 10%, distribution 3 to 8%, manufacturing 8 to 18%.
- **m09, document:** below 5%, 5 to 10%, above 10% (20% or more is excellent). A general cross-industry rule of thumb, not sector-specific.
- **m10, document:** above 30% or a rising trend is Red; 20 to 30% stable is Yellow; a declining trend and/or below 20% is Green. Sector references: manufacturing 10 to 20%, retail trade 20 to 28% (median), services and healthcare up to 40 to 50%. **Direction matters more than the level.** **Why:** a rising SG&A ratio means overhead grows faster than sales. **Code:** level and trend together; "rising" and "falling" are 1 point of sales (`TOL_MARGIN_TREND`).

### 2.3 Capital efficiency and returns

| ID | 🔴 Red | 🟡 Yellow | 🟢 Green |
|---|---|---|---|
| **m11 ROE** | below 10% | 10 to 15% | 15% or more |
| **m12 ROA** | below 5% | 5 to 10% | 10% or more |
| **m13 ROIC** | below 8% | 8 to 15% | 15% or more |
| **m14 asset turnover** | below 1.0 | 1.0 to 1.5 | 1.5 or more |

- **m11, document:** Green is 15 to 20% (above 20% exceptional). Cross-check against the cost of equity (about 8 to 12% is typical): ROE must clear that hurdle, not just an absolute number. **Why:** ROE below the cost of equity destroys value for owners.
- **m12, document:** below 5%, 5 to 10%, 10 to 20% (above 20% excellent). Below 5% is normal, not necessarily bad, for asset-heavy manufacturing and utilities, so read it next to asset turnover.
- **m13, document:** the single most-cited threshold: it is the **spread over the cost of capital (WACC)**, not the absolute ROIC, that decides value creation. Red is below WACC (roughly under 8 to 10% for most industrials); Yellow is near WACC or 8 to 15%; Green is above 15% and/or a spread of 2 to 3 points or more (above 20% signals a durable moat). **Code:** the project has no WACC, so the absolute bands 8% and 15% stand in for it. ROIC can be extreme for buyback-heavy or net-cash companies (Apple about 104%) and still grades Green.
- **m14, document:** below 1.0, 1.0 to 1.5, above 1.5. Heavily sector-dependent: retail and e-commerce commonly run above 2.0; utilities and telecoms normally sit below 1.0 and that is not a red flag there.

### 2.4 Growth funding and reinvestment

| ID | 🔴 Red | 🟡 Yellow | 🟢 Green |
|---|---|---|---|
| **m15 sustainable growth rate** | revenue growth exceeds it by more than 5 points | exceeds it by up to 5 points | revenue growth does not exceed it |
| **m16 reinvestment rate** | 50% or more reinvested and ROIC below 8% | any other case | 50% or more reinvested and ROIC at least 15% |
| **m17 free cash flow** | zero or negative, or below last year | positive and not falling, but under 70% of net income | positive, not falling, at least 70% of net income |
| **m18 capex to sales** | above 7.4% of sales, or up more than 1 point | above 3.7% | at or below 3.7% and not rising |

- **m15, document:** no fixed number: a comparison of the sustainable growth rate (ROE times retention) with the company's *actual* growth. Actual above it means growth relies on outside financing (debt or new shares). **Code (assumption):** actual growth is the 2-year revenue CAGR; "modestly" is up to 5 points (`SGR_MODEST_EXCESS`).
- **m16, document:** a consistency check, not a band. The worrying pattern is capital reinvested at low returns, not a high reinvestment rate by itself. **Code (assumption):** "high" reinvestment is 50% of NOPAT or more (`REINVEST_HIGH`); a high rate at ROIC under 8% is Red and at 15% or more is Green; everything else is Yellow ("acceptable, monitor").
- **m17, document:** the 70% free-cash-flow-to-net-income conversion is a commonly cited growth-screen rule of thumb; treat it cautiously here because this free cash flow is an approximation (it uses the change in gross PP&E as capex). **Code:** "declining" means below last year's approximate figure.
- **m18, document:** Red is the top ~80th percentile against **sector peers** or rising abnormally fast; Yellow is in line with the peer median; Green is stable or declining. The cross-industry median is about 3.7% of sales, but capital-intensive sectors run higher, so compare within the peer set. **Code (assumption, a deviation):** the project has no sector peer groups, so the document's own 3.7% cross-industry median stands in (`PEER_CAPEX_MEDIAN`), Red above twice that (7.4%), and "rising" is more than 1 point (`CAPEX_RISE_TOL`). This is the least faithful rule: add peer groups before relying on it.

### 2.5 Leverage, liquidity and coverage

| ID | 🔴 Red | 🟡 Yellow | 🟢 Green |
|---|---|---|---|
| **m19 debt to equity** | above 2.0 | 1.0 to 2.0 | 1.0 or less |
| **m20 debt to assets** | above 0.5 | 0.3 to 0.5 | 0.3 or less |
| **m21 net debt to EBITDA** | above 4.0 | 2.0 to 4.0 | 2.0 or less |
| **m22 current ratio** | below 1.0 | 1.0 to 1.5, or above 2.0 | 1.5 to 2.0 |
| **m23 quick ratio** | below 0.5 | 0.5 to 1.0 | 1.0 or more |
| **m24 cash ratio** | below 0.2 | 0.2 to 0.5 | 0.5 or more |
| **m25 interest coverage** | below 1.5x | 1.5 to 3.0x | above 3.0x |

- **m19, document:** sector-adjusted: utilities, banks and real estate routinely and safely run above 2.0; for most industrial and consumer names below 1.0 is the conservative GARP-style cap.
- **m20, document:** "under 0.5 is typically safer" is the standard rule of thumb; the direction of travel matters as much as the level. **Code:** the document's Green bound is written "below 0.3 to 0.5"; the stricter 0.3 is used so that the ratio matches the risk-set debt bands.
- **m21, document:** above 4.0x is Red (5x or more is high leverage or distress risk), 2.0 to 4.0 Yellow, below 2.0 Green. S&P-style tiers: below 1.5x minimal, 1.5 to 2.0 modest, 2.0 to 3.0 intermediate, 3.0 to 4.0 significant, 4.0 to 5.0 aggressive, above 5.0 highly leveraged. The investment-grade ceiling is roughly 3.5x. **Why:** it is roughly the number of years of operating earnings needed to repay the debt.
- **m22, document:** below 1.0 is Red; 1.0 to 1.5 **or above 2.5** is Yellow; 1.5 to 2.0 is Green. Above 2.5 is not automatically great: it can mean idle cash or inventory not deployed. Retailers and grocers structurally run lower without distress. **Code (assumption):** the document is silent between 2.0 and 2.5; that range is Yellow, so only a shortage of liquidity is Red.
- **m23, document:** below 0.5, 0.5 to 1.0, 1.0 or more. Retail and consumer discretionary names commonly sit in the 0.5 to 1.0 Yellow band because of inventory-heavy models: do not over-penalise that sector.
- **m24, document:** below 0.2, 0.2 to 0.5, 0.5 or more (written "0.5 to 1.0"). Sector split: tech and pharma typically 0.5 to 1.5; manufacturing and autos 0.2 to 0.6. A 0.3 can be healthy for a manufacturer and thin for a software company.
- **m25, document:** below 1.5x is Red (below 1.0x means operating income does not cover interest: an acute red flag); 1.5 to 3.0 Yellow; above 3.0 Green. Above 3.0x is the standard investment-grade convention; the corporate average is about 3.7x. **Code:** a company with no interest expense gets no value (N/A), not a grade.

---

## 3. Risk ratios (higher grade colour = lower risk; Red = high risk)

| ID | 🔴 Red | 🟡 Yellow | 🟢 Green | Basis |
|---|---|---|---|---|
| **r01 Altman Z''** | below 1.1 | 1.1 to 2.6 | 2.6 or more | the published Z'' model (below) |
| **r02 debt to equity** | above 2.0 | 1.0 to 2.0 | 1.0 or less | same as m19 |
| **r03 debt ratio** (total liabilities / assets) | above 0.5 | 0.3 to 0.5 | 0.3 or less | rule of thumb |
| **r04 net debt to EBITDA** | above 4.0 | 2.0 to 4.0 | 2.0 or less | same as m21 |
| **r05 short-term debt share** | above 50% | 30 to 50% | 30% or less | rule of thumb |
| **r06 current ratio** | below 1.0 | 1.0 to 1.5 | 1.5 or more | m22's lower bounds, no upper band |
| **r07 quick ratio** | below 0.5 | 0.5 to 1.0 | 1.0 or more | same as m23 |
| **r08 interest coverage** | below 1.5x | 1.5 to 3.0x | 3.0x or more | same as m25 |
| **r09 retained earnings / assets** | below 0 | 0 to 0.2 | 0.2 or more | rule of thumb |
| **r10 one-off items / pretax income** | above 0.05 | above 0 up to 0.05 | exactly 0 | rule of thumb |
| **r11 goodwill and intangibles / assets** | above 0.4 | 0.2 to 0.4 | 0.2 or less | rule of thumb |

**Why each boundary (our reasoning, not from a source document):**

- **r01:** Altman's Z'' is the non-manufacturer, book-equity version of the distress model: `6.56·WC/TA + 3.26·RE/TA + 6.72·EBIT/TA + 1.05·BookEquity/Liabilities`. It needs no share price. The zones **below 1.1 distress, 1.1 to 2.6 grey, above 2.6 safe** belong to this Z'' formula. (A research note in the project's archive quotes 1.23 and 2.90; those cut-offs belong to the older private-firm Z' model, which has different coefficients. They must not be paired with the Z'' formula used here.)
- **r02, r04, r07, r08:** identical to their growth twins, so the two sets never disagree on the same number.
- **r03:** at 0.5 more than half of the assets are covered by someone else's claims; below 0.3 the company is mostly financed by its owners. Same cut-offs as debt to assets, on a broader numerator (all liabilities, not only borrowings), which is why Apple (many non-debt liabilities) is Red here while its borrowings are low.
- **r05:** when more than half of the debt falls due within a year, the company must refinance on the market's terms; below 30% the maturities are spread out.
- **r06:** a risk screen has no upper limit: too much liquidity is not a risk, so there is no "too high" band, unlike m22.
- **r09:** a negative value is an accumulated deficit (a history of losses eroding equity); below 0.2 the company has kept little of what it earned. It is also the second term of the Altman model.
- **r10:** earnings quality. Any one-off item makes the profit less repeatable, so any amount above zero is at least Yellow, and above 5% of pretax income is Red. A value of 0 can also mean the filer does not break one-offs out.
- **r11:** goodwill and acquired intangibles can be written down; above 40% of assets an impairment would hit equity hard.

---

## 4. What is not yet backed by a document

| Item | What is missing |
|---|---|
| r03, r05, r09, r10, r11 and r06's lower bounds | a source for the cut-offs. They are conventional, not documented here. If you have a reference, add it to this page and adjust the code constants. |
| Sector adjustment | no company is compared with its sector. Utilities, banks, real estate and software will grade unfairly on some ratios. |
| WACC | m13 and m11 use absolute bands in place of the cost of capital |
| Peer groups | m18 uses a cross-industry median in place of sector peers |

---

## 5. Where the design document is ambiguous or silent, and what was chosen

| Place | The document | Choice |
|---|---|---|
| m05 Yellow "flat" and Green "stable or expanding" overlap | both | stable is read as flat: within 1 point is Yellow, above is Green |
| m20 Green bound "below 0.3 to 0.5" | a range | 0.3 |
| m22 between 2.0 and 2.5 | not stated | Yellow |
| m24 Green "0.5 to 1.0" | a range | 0.5 or more |
| "roughly equal", "modestly", "high", "rising abnormally fast" | no numbers | 2 points, 5 points, 50%, 1 point |
| m02, m03, m04 comparison basis | "revenue growth", "revenue/EBIT growth" | revenue growth |

An earlier version of the code differed from the document in two places and was corrected: m05 was graded on the level (20% and 40% cut-offs that are not in the document), and m22 made anything above 2.5 Red.

---

## 6. How to change a boundary

1. Edit the number in `grading.py` (the `_BANDS` table, a constant at its top, or `_current_ratio`) or in `run_risk_fundamentals.py` for the risk set.
2. Update the one-line rule in `GRADE_RULES` (the language model and the guides read it) and this page.
3. Update `tests/test_grading.py`, then run it.
4. Recompute and publish: `python scripts/data-prep/run_fundamentals.py`, then `python scripts/online/publish_to_supabase.py`. Stored grades change only when the ratios are recomputed.

---

## 7. Known limits of grading

- A grade is a snapshot of one year (plus last year for nine ratios); it does not see a three-year trend.
- Fixed numbers treat all sectors alike.
- The risk set has no documented sources for six cut-offs (section 4).
- Grades do not decide the screening verdict; they are evidence the model reasons over.
