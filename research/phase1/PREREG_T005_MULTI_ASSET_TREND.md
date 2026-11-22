# Pre-Registration `T005`: Multi-Asset Time-Series Trend / Dual Momentum (`2007–2026`, Zero Survivorship Bias — FINAL Free-Data Research Gate)

> **Status:** `PRE-REGISTERED (LOCKED BEFORE DATA DOWNLOAD)`
> **Hard Stop Rule:** This is our **FINAL** `$0` free-data research test (`N_hyp = 5`). If `T005` fails, we **STOP** alpha research cleanly and freeze the scanner as a manual alert tool.

---

## 1. Metadata
- **Test ID:** `T005`
- **Date Pre-Registered:** `2026-11-23`
- **Stage:** Stage 1 (DEV-Only Trend Event Study Kill Gate: `2007-03..2015-12`, 106 mos) → Stage 2 (Single HOLDOUT Evaluation: `2016-01..2026-10`, 130 mos, *only if Stage 1 passes*)
- **Data Tier:** Free long-history auto-adjusted daily ETF total-return data (`data/research_etf/`, `$0`), zero survivorship bias.

## 2. Hypothesis & Economic Rationale
- **Claim:** Applying 12-month excess-over-T-bill time-series trend following across 5 uncorrelated macro asset classes (US equities, international equities, long Treasuries, gold, commodities) generates superior true excess-return Sharpe (`^IRX`) and dramatically lower crisis drawdowns than static multi-asset (`EW_5`, `RP_5`), `60/40`, and `SPY`, net of 1× and 2× trading costs.
- **Economic Mechanism:** Time-Series Momentum across asset classes (Moskowitz, Ooi & Pedersen 2012; Hurst, Ooi & Pedersen 2017) captures persistent macro policy cycles (central-bank rate regimes, inflation waves, growth slowdowns) and institutional slow rebalancing across genuinely low/negatively correlated asset classes (unlike 9 highly correlated US equity sectors in `T004`).
- **Known Failure Mode:** Rapid whipsaw reversals across all asset classes simultaneously (e.g., sharp V-shaped policy pivots) and low-volatility range-bound markets.

## 3. Universe, Data & Controls (Zero Survivorship Bias)
- **5 Asset-Class ETFs (All Liquid Institutional Macro Proxies):**
  - `SPY` — US Equities (inception `1993-01-29`)
  - `EFA` — Developed International Equities (inception `2001-08-17`)
  - `TLT` — 20+ Year US Treasuries (inception `2002-07-26`)
  - `GLD` — Gold Bullion (inception `2004-11-18`)
  - `DBC` — Broad Commodities (inception `2006-02-06`)
- **Risk-Free / Cash Proxy:** `^IRX` (13-week US T-bill yield, strictly causal lookup at `t_sig`).
- **Static Benchmarks & Matched Controls:**
  - `SPY`: US Equity buy-and-hold.
  - `B60_40`: `60% SPY / 40% TLT` monthly rebalanced.
  - `EW_5`: Equal-weight all 5 ETFs (`20%` each, monthly rebalanced, no trend filter).
  - `RP_5`: Inverse 60-day volatility weighted all 5 ETFs (100% invested, monthly rebalanced, no trend filter — exact matched vol-weighting control for `A1`).

## 4. Exact Rules (Frozen — Zero Tuning)
- **Rebalance Calendar & Fills (Zero Lookahead):**
  - Signal computed on last `SPY` trading day `t_sig` of month `m` (strictly `<= t_sig`).
  - Executed at **first `SPY` trading day of month `m+1` Open** (`Open[t_fill]`), held open-to-open until next month's `Open[t_next_fill]`.
- **Time-Series Trend Signal (Strictly Causal, 252 Trading Days):**
  $$r_{12m, i}(t_{\text{sig}}) = \frac{\text{Close}_i[t_{\text{sig}}]}{\text{Close}_i[t_{\text{sig}} - 252]} - 1, \qquad r_{f, 12m}(t_{\text{sig}}) = \frac{\text{IRX}[t_{\text{sig}}]}{100}$$
  $$\text{TrendOn}_i(t_{\text{sig}}) = \mathbf{1}\!\left(r_{12m, i}(t_{\text{sig}}) > r_{f, 12m}(t_{\text{sig}})\right)$$
- **Pre-Registered Variants (ONLY These 3; 2 Graduation Candidates `A1`, `A2`):**
  - **`A0` (Stage 1 Diagnostic — Equal-Weight Trend):** Each of the 5 ETFs gets `20%` if `TrendOn_i == True`, else `0%` (sits in T-bill cash).
  - **`A1` (Graduation Candidate #1 — Inverse-Vol Trend):** Base weights `w_base_i` = inverse trailing 60-day realized vol normalized to sum to `1.0` (identical to `RP_5`). Keep `w_i = w_base_i` if `TrendOn_i == True`, else `0%` (sits in T-bill cash).
  - **`A2` (Graduation Candidate #2 — Dual Momentum Top-2 + 10% Vol-Target):** Rank the 5 ETFs by `r_12m - r_f_12m`; take the **Top 2** ETFs that ALSO satisfy `TrendOn_i == True` (`50%` base weight per slot; unfilled slots sit in cash), then scale equity exposure by `min(1.0, 0.10 / sigma_60d_basket)` (no leverage `> 1.0`).

## 5. Cost Model, Cash Yield & Sharpe Convention
- **Turnover Cost Formula (Exact Drifted-Weight Notional):**
  - **`1×` Baseline Cost:** `10 bps` round-trip (`5 bps` one-way).
  - **`2×` Stress Cost:** `20 bps` round-trip (`10 bps` one-way).
- **Sharpe & Cash Convention (Identical Across All Series):**
  - Uninvested cash earns strictly causal `^IRX` T-bill monthly rate $r_{f,m}$.
  - Primary metric: **True Excess-Return Sharpe (`Sharpe_tbill`)** on `r_p - r_f` identically across `A0..A2`, `EW_5`, `RP_5`, `B60_40`, `SPY`. Also report `0%`-cash CAGR and `Sharpe_rf0` for transparency.

## 6. Splits & Single-Touch HOLDOUT Discipline
- **DEV:** `2007-03 .. 2015-12` (**106 months = 8.83 years** — covers 2008 GFC, 2009 rebound, 2011 Euro crisis, 2013 taper tantrum, 2014–2015 commodity bust). Used for Stage 1 Kill Gate only; zero parameter tuning.
- **HOLDOUT:** `2016-01 .. 2026-10` (**130 complete months = 10.83 years**, including partial 2026; Sharpe SE ~`±0.31`). **SEALED during Stage 1.** Executed **EXACTLY ONCE** in Stage 2 if Stage 1 passes.

## 7. Pre-Registered Pass / Fail Exit Criteria (Net of Costs)

### Stage 1 Kill Gate (DEV ONLY: `2007-03 .. 2015-12` — Must Pass Before Opening HOLDOUT)
1. `A0` beats static `EW_5` on `Sharpe_tbill` at `1×` costs by **`>= +0.15`** (`A0_Sharpe_1x - EW_5_Sharpe_1x >= +0.15`).
2. `A0` cuts Max Drawdown vs `EW_5` by **`>= 5.0` percentage points** (`EW_5_MaxDD - A0_MaxDD >= 0.05`).
- **If Stage 1 fails on DEV:** STOP immediately (`FAIL`). HOLDOUT stays sealed.

### Stage 2 Graduation Gate (Evaluated ONCE on HOLDOUT: `2016-01 .. 2026-10`)
Either graduation candidate (`A1` or `A2` — logged as **2 attempts** in `TEST_LOG.md`) must satisfy **ALL 5** criteria simultaneously:
1. **True Excess Sharpe (1× & 2× Costs):** HOLDOUT `Sharpe_tbill >= 0.55` at `1×` costs **AND** `>= 0.50` at `2×` costs.
2. **Beats Static Multi-Asset Controls (DEV & HOLDOUT):** Beats BOTH `EW_5` and `RP_5` on `Sharpe_tbill` at `1×` **AND** `2×` costs in **BOTH** DEV (`2007–2015`) AND HOLDOUT (`2016–2026`).
3. **Beats `60/40` & Protects vs `SPY`:** On HOLDOUT at `1×` costs, beats `B60_40` on `Sharpe_tbill` **AND** reduces Max Drawdown by `>= 5` percentage points vs `SPY` (with positive excess CAGR over T-bills).
4. **Time & Asset Concentration Robustness:** Positive net total return in `>= 70%` of HOLDOUT calendar years (`>= 8/11`, including partial 2026), **AND** HOLDOUT `Sharpe_tbill` remains `> 0.25` after dropping either (a) the single best calendar year or (b) the single highest-contributing ETF (replacing its weight with T-bill cash).
5. **Crisis Tail Bound:** HOLDOUT Max Drawdown `<= 15.0%`.

## 8. Hard Stop Decision Rule
- **If FAIL:** Log `FAIL` in `TEST_LOG.md`. **STOP alpha research** (`5/5` honest negatives, `$0` lost) and proceed to freezing the scanner as a clean alert tool.
- **If PASS:** Zero survivorship bias across 20 years of liquid ETFs → graduate directly to **Phase 3 (Portfolio Engineering)** → **Phase 4 (4–8 week Execution/Slippage Paper Audit)**.
