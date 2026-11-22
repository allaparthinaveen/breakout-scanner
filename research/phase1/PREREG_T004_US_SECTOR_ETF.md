# Pre-Registration `T004`: US Sector ETF Rotation (12–1m Momentum + Vol-Target & Regime Overlay, 2000–2026)

> **Status:** `PRE-REGISTERED (LOCKED BEFORE DATA DOWNLOAD)`
> **Design Highlights:** Zero survivorship bias (all 9 original SPDR Select Sector ETFs launched simultaneously on `1998-12-22`), 26 full years of multi-regime data (`2000–2026`), true T-bill excess-return Sharpe (`^IRX`), matched overlay controls (`EW_S0..EW_S2`), single-touch 10.8-year HOLDOUT (`2016–2026`, Sharpe SE ~`±0.31`), `$0` data cost.

---

## 1. Metadata
- **Test ID:** `T004`
- **Date Pre-Registered:** `2026-11-23`
- **Stage:** Stage 1 (DEV-Only Tercile Event Study Kill Gate: `2000–2015`) → Stage 2 (Single HOLDOUT Portfolio Backtest: `2016–2026`, *only if Stage 1 passes*)
- **Data Tier:** Free long-history auto-adjusted daily ETF total-return data (`1998–2026`, `$0`) saved to `data/research_etf/`.

## 2. Hypothesis & Economic Rationale
- **Claim:** Rotating monthly into the top tercile (top 3 of 9) US equity sectors ranked by trailing 12–1 month total return (`t-252` to `t-21`), paired with a 15% volatility target and `SPY > SMA200` regime overlay, generates positive risk-adjusted excess return over both `SPY` and a matched equal-weight 9-sector control net of 1× and 2× trading costs.
- **Economic Mechanism:** Industry/sector momentum (Moskowitz & Grinblatt 1999) is one of the most persistent components of equity momentum, driven by macro cycle rotation, earnings revision waves across industry groups, and institutional capital allocation lags — while avoiding single-stock idiosyncratic blowups and survivorship distortion.
- **Known Failure Mode:** Sharp sector leadership reversals at recession troughs (2009) and rapid rate/inflation regime shifts (2020–2022). Vol-targeting + 200-day regime overlay target drawdown compression during systemic bear markets.

## 3. Universe, Data & Matched Controls (Zero Survivorship Bias)
- **Universe (Fixed — All 9 Original SPDR Select Sector ETFs, inception `1998-12-22`):**
  `XLB`, `XLE`, `XLF`, `XLI`, `XLK`, `XLP`, `XLU`, `XLV`, `XLY`.
  *(Late spin-offs `XLRE` [2015] and `XLC` [2018] are excluded to maintain an exact symmetric 9-sector universe across all 26 years.)*
- **Market Benchmark:** `SPY` (inception `1993-01-29`).
- **Risk-Free / Cash Benchmark:** `^IRX` (13-week US T-bill annualized yield index, strictly causal lookup at month-end `t_sig`: monthly risk-free return $r_{f,m} = (1 + y_{\text{IRX}, t_{\text{sig}}} / 100)^{1/12} - 1$, floored at `0.0`).
- **Matched Equal-Weight Controls (Same 9 Sectors + Same Overlay):**
  - `EW_S0` *(matches `S0`)*: Equal-weight all 9 sectors (`1/9` each), 100% invested.
  - `EW_S1` *(matches `S1`)*: `EW_S0` scaled by the exact same 15% vol-target (`w = min(1.0, 0.15 / sigma_60d_EW)`).
  - `EW_S2` *(matches `S2`)*: `EW_S1` with the exact same `SPY Close[t_sig] <= SMA200[t_sig]` → `0%` equity (cash) rule.
- **Disclosure:** Zero survivorship bias in sector ETFs (none delisted; inception predates `DEV` by >1 year). Taxes are not modeled (monthly ETF rotation has low turnover and clean liquidity).

## 4. Exact Rules (Frozen — Zero Tuning)
- **Rebalance Calendar:** Last `SPY` trading day `t_sig` of each calendar month.
- **Signal (Strictly Causal, Data `<= t_sig`):**
  $$\text{Mom}_{12\text{-}1}(t_{\text{sig}}) = \frac{\text{Close}[t_{\text{sig}} - 21]}{\text{Close}[t_{\text{sig}} - 252]} - 1$$
  Requires `>= 253` bars up to `t_sig` (warmup completes Dec 1999; first rebalance `1999-12-31` → `2000-01` holding month).
- **Execution Fill (Zero Lookahead):** Rebalanced at **first `SPY` trading day of month `m+1` Open** (`Open[t_fill]`), held open-to-open until next month's `Open[t_next_fill]`.
- **Stage 1 — DEV-Only Tercile Event Study (`2000-01 .. 2015-12`, 192 months; HOLDOUT SEALED):**
  - Rank all 9 sectors ascending by `(Mom_12_1, symbol)` into 3 terciles: `T1` (bottom 3), `T2` (middle 3), `T3` (top 3), plus `EW_S0` (all 9).
- **Stage 2 — Pre-Registered Portfolio Variants (ONLY These 3; 2 Graduation Candidates: `S1`, `S2`):**
  - **`S0` (Diagnostic Baseline):** Equal-weight Top 3 sectors (`T3`, `1/3` each), 100% invested.
  - **`S1` (Graduation Candidate #1 — +15% Vol-Target):** `T3` scaled by `w = min(1.0, 0.15 / sigma_60d_T3)` (trailing 60 trading days `<= t_sig`, no leverage `> 1.0`).
  - **`S2` (Graduation Candidate #2 — +Regime & 15% Vol-Target):** Same as `S1`, EXCEPT `w = 0.0` (100% cash) if `SPY Close[t_sig] <= SPY SMA200[t_sig]`.

## 5. Cost Model, Cash Yield & Sharpe Convention
- **Turnover Cost Formula (Exact Drifted-Weight Notional):**
  `traded_notional = sum_s |w_new[s] - w_drifted[s]|`, `cost = traded_notional * one_way_cost`.
  - **`1×` Baseline Cost:** `10 bps` round-trip (`5 bps` one-way — conservative for liquid SPDR ETFs with 1–2 bps spreads).
  - **`2×` Stress Cost:** `20 bps` round-trip (`10 bps` one-way).
- **Sharpe & Cash Convention (Identical Across All Series):**
  - **Primary Gate Metric (`Sharpe_tbill`):** Uninvested cash earns the strictly causal `^IRX` T-bill monthly rate $r_{f,m}$ (matching a standard Treasury money-market / `SGOV` sweep), and Sharpe is computed on true excess returns:
    $$\text{Sharpe}_{\text{tbill}} = \frac{\text{mean}(r_{p,m} - r_{f,m})}{\text{std}(r_{p,m} - r_{f,m}, \text{ddof}=1)} \times \sqrt{12}$$
    computed identically for `S0..S2`, `EW_S0..EW_S2`, and `SPY`. *(Under this convention, sitting in cash contributes `0` excess return — never artificially inflating Sharpe in high-rate periods.)*
  - **Secondary Transparency Metric:** `REPORT.md` also prints `CAGR_0cash` and `Sharpe_rf0` (with `0%` cash yield and `rf=0%`) alongside `Sharpe_tbill`.

## 6. Splits & Single-Touch HOLDOUT Discipline
- **DEV:** `2000-01 .. 2015-12` (**192 months = 16 full years** — covers Dot-Com bear, 2008 GFC, 2009 rebound, 2010–2015 expansion). Used for Stage 1 Kill Gate & sanity verification only; zero parameter tuning.
- **HOLDOUT:** `2016-01 .. 2026-10` (**130 complete months = 10.83 years**, including partial 2026; Sharpe SE ~`±0.31`). **NOT touched in Stage 1.** Executed **EXACTLY ONCE** in Stage 2 if Stage 1 passes.

## 7. Pre-Registered Pass / Fail Exit Criteria (Net of Costs)

### Stage 1 Kill Gate (DEV ONLY: `2000-01 .. 2015-12` — Must Pass Before Opening HOLDOUT)
1. `T3` beats `EW_S0` on annualized return net of `1×` costs by **`>= +1.0%/yr`** (`T3_CAGR_1x - EW_S0_CAGR_1x >= +1.0%`).
2. `T3 - T1` average monthly spread is strictly positive (`> 0`, net of `1×` costs).
- **If Stage 1 fails on DEV:** STOP immediately (`FAIL`). HOLDOUT stays sealed.

### Stage 2 Graduation Gate (Evaluated ONCE on HOLDOUT: `2016-01 .. 2026-10`)
Either graduation candidate (`S1` or `S2` — logged as **2 attempts** in `TEST_LOG.md`) must satisfy **ALL 5** criteria simultaneously:
1. **Net Excess Risk-Adjusted Return (1× & 2× Costs):** HOLDOUT `Sharpe_tbill >= 0.50` at `1×` costs **AND** `>= 0.45` at `2×` costs.
2. **Matched-Overlay Selection Edge (DEV & HOLDOUT):** Positive annualized excess CAGR over its **matched overlay control** (`S1` vs `EW_S1`, or `S2` vs `EW_S2`, using identical T-bill cash yield) of **`>= +1.0%/yr`** at `1×` costs **AND** **`> 0%/yr`** at `2×` costs — in **BOTH** DEV (`2000–2015`) AND HOLDOUT (`2016–2026`).
3. **Value-Add vs `SPY`:** On HOLDOUT at `1×` costs, beats `SPY` on `Sharpe_tbill` **OR** cuts Max Drawdown by `>= 5` percentage points vs `SPY` while keeping positive excess CAGR over T-bills (`CAGR > TBill_CAGR`).
4. **Time & Sector Concentration Robustness:** Positive net total return in `>= 60%` of HOLDOUT calendar years (`>= 7/11`, including partial 2026), **AND** HOLDOUT excess CAGR over matched control remains `> 0` after dropping either (a) the single best calendar year or (b) the single highest-contributing sector ETF.
5. **Tail Risk Bound:** HOLDOUT Max Drawdown `<= 25.0%`.

## 8. Decision Rule
- **If FAIL:** Log `FAIL` in `TEST_LOG.md`. Do **not** tweak lookbacks, top-K count, or ETF roster on the same HOLDOUT.
- **If PASS:** Because this dataset already spans **26 years with zero survivorship bias** (no paid data needed for ETFs!), a decisive `PASS` authorizes **Phase 3 (Portfolio & Risk Engineering)** followed by **Phase 4 (4–8 week Execution/Slippage Paper Audit)**.
