# Pre-Registration `T003`: 12–1 Month Cross-Sectional Momentum + Vol-Target / Regime Overlay (Free-Data Asymmetric Kill Test)

> **Status:** `PRE-REGISTERED — REV 2 (NOT YET RUN)` — Incorporates reviewer audit fixes; ready for final sign-off before executing Stage 1 DEV Event Study.
> **Defaults Applied:** US primary, monthly rebalance, **$0 free-data screen first**; paid survivorship-free data ($30–60/mo) **only** on a decisive pass.

---

## 1. Metadata
- **Test ID:** `T003`
- **Date Pre-Registered:** `2026-11-23` (Rev 2)
- **Stage:** Stage 1 (DEV-Only Quintile Event Study Kill Gate) → Stage 2 (Single HOLDOUT Portfolio Backtest, *only if Stage 1 passes*)
- **Data Tier:** Free 12-year auto-adjusted daily dataset already on disk (`data/research_daily/`, `$0`).
- **Statistical Power & Asymmetric Screen Caveat:**
  - Today's watchlist contains names that survived and grew large (including formerly small stocks that surged), which inflates momentum persistence and most likely flatters results. However, with ~42 stocks per quintile and a mega-cap-heavy list, a **FAIL** here is **strong evidence** against the strategy on this universe, not absolute mathematical proof.
  - With ~58 monthly HOLDOUT observations (~4.83 years), the standard error of an annualized Sharpe estimate is roughly **±0.45 to ±0.50** — meaning a Sharpe of `0.50` **cannot** be statistically distinguished from `0` on five years of monthly data alone.
  - Therefore, **`T003` is strictly a cheap screen, NOT statistical proof of alpha.** A `PASS` here is **permission to purchase clean paid data for `T004`, nothing more.**

## 2. Hypothesis & Economic Rationale
- **Claim:** Equities in the top quintile of trailing 12-to-1 month total return (`t-252` to `t-21`) outperform a matched equal-weight universe control and the bottom quintile over the next month, net of realistic transaction costs, when paired with a volatility-targeting and market-regime crash overlay.
- **Economic Mechanism:** Documented cross-sectional momentum premium (Jegadeesh & Titman 1993; Carhart 1997; Barroso & Santa-Clara 2015) driven by institutional slow-information diffusion, disposition effect, and gradual fund flows; skipping the last 21 trading days avoids short-term microstructure reversal.
- **Known Failure Mode:** Momentum crashes during sharp bear-market rebounds (e.g., 2009 / 2020 / 2022 whipsaws). Volatility targeting and a 200-day trend regime filter directly target crash risk.

## 3. Universe, Data & Matched Controls
- **Primary Market (Decision Gate):** US Equities — `config/us_watchlist.txt` (`211` symbols with `>= 500` clean auto-adjusted bars in `data/research_daily/`). Market benchmark: `SPY`.
- **Secondary Diagnostic (Reported Separately, Not Pooled for Pass):** NSE Equities — `config/nse_watchlist.txt` (`187` symbols). Market benchmark: `^NSEI`. *(Reported for transparency; NSE production path remains index/sector/liquid-factor rotation due to single-stock survivorship data constraints.)*
- **Matched Anti-Survivorship Controls (Same Universe + Same Overlay):**
  Because `M1` and `M2` hold cash during high-volatility or bear-regime periods, judging them only against a fully invested equal-weight universe mixes stock selection with cash timing. Each variant is therefore benchmarked against its **exact matched overlay control**:
  - `EW_M0` *(matches `M0`)*: Monthly-rebalanced equal-weight portfolio of all eligible stocks with a valid `Mom_12_1` score, 100% invested.
  - `EW_M1` *(matches `M1`)*: `EW_M0` scaled by the exact same 15% annualized portfolio vol-target (`w = min(1.0, 0.15 / sigma_60d_EW)`), uninvested cash at `0%`.
  - `EW_M2` *(matches `M2`)*: `EW_M1` with the exact same `Close[t] <= SMA200[t]` benchmark regime → `0%` cash rule.
- **Explicit Biases:** Survivorship bias (today's watchlist constituents only), no historical liquidity/market-cap floor, no tax modeling (monthly turnover keeps tax drag far below weekly strategies).

## 4. Exact Rules (Frozen — Zero Tuning)
- **Rebalance Calendar:** Last trading day `t` of each calendar month (computed strictly from benchmark trading dates).
- **Signal (Strictly Causal, Data `<= t`):**
  $$\text{Mom}_{12\text{-}1}(t) = \frac{\text{Close}[t - 21]}{\text{Close}[t - 252]} - 1$$
  Requires `>= 253` clean trading bars up to `t` (warmup completes ~Nov 2015; first rebalance `2015-12-31` for `2016-01` return).
- **Execution Fill (Zero Lookahead):** Signal computed at month-end close `t`; portfolio rebalanced at **first trading day of month `t+1` Open** (`Open[t+1]`). Holding-period return is `Open[next_rebal_fill] / Open[curr_rebal_fill] - 1`.
- **Stage 1 — DEV-Only Event Study (Run First; HOLDOUT NOT TOUCHED):**
  - Evaluated strictly on **DEV (`2016-01 .. 2021-12`, 72 months)**.
  - Sort eligible stocks each month-end into 5 equal-sized quintiles (`Q1` lowest momentum → `Q5` highest momentum).
  - Measure open-to-open monthly returns for `Q1..Q5`, `EW_M0`, `Q5 - EW_M0`, and `Q5 - Q1`, plus turnover and `1×`/`2×` net returns.
- **Stage 2 — Pre-Registered Portfolio Variants (ONLY These 3; 2 Graduation Candidates: `M1`, `M2`):**
  - **`M0` (Diagnostic Baseline — Raw Q5):** Top 20% (`Q5`), equal-weighted, 100% invested.
  - **`M1` (Graduation Candidate #1 — +15% Vol-Target):** Same `Q5` weights, scaled by `w = min(1.0, 0.15 / sigma_60d)`, where `sigma_60d` is annualized realized volatility of the current `Q5` basket over trailing 60 trading days `<= t` (uninvested weight earns `0%` cash; no leverage `> 1.0`).
  - **`M2` (Graduation Candidate #2 — +Regime & 15% Vol-Target):** Same as `M1`, EXCEPT if benchmark (`SPY` / `^NSEI`) `Close[t] <= SMA200[t]` on month-end `t`, gross exposure is `0%` (cash) for the upcoming month.

## 5. Cost Model, 2× Stress Test & Sharpe Convention
- **Turnover Cost Formula:** Charged on one-way portfolio turnover at every monthly rebalance (`turnover = 0.5 * sum(|w_new - w_drifted|)`):
  - **US:** `1× = 20 bps` round-trip (`10 bps` per one-way turnover); **`2× = 40 bps`** round-trip (`20 bps` one-way).
  - **NSE (Diagnostic):** `1× = 35 bps` round-trip (`17.5 bps` one-way, reflecting 0.10% delivery STT each side + brokerage + slippage); **`2× = 70 bps`** round-trip (`35 bps` one-way).
- **Sharpe Convention (Explicit & Identical Across All Series):**
  - **Gate Metric (`Sharpe_rf0`):** $\text{Sharpe}_{rf=0} = \frac{\text{mean}(r_{\text{monthly}})}{\text{std}(r_{\text{monthly}}, \text{ddof}=1)} \times \sqrt{12}$, computed with **`rf = 0%` and `0%` cash yield** identically for `M0..M2`, `EW_M0..EW_M2`, and `SPY` (and `^NSEI`).
  - **Disclosure:** During 2022–2026 T-bills paid ~4–5%, so `rf = 0%` overstates excess-return Sharpe relative to cash (while `0%` cash credit penalizes `M1`/`M2` during cash months relative to real T-bill interest). For transparency, `REPORT.md` will also print an informational `Sharpe_rf4` (assuming 4%/yr T-bill rate on both excess return and cash weight), while the pre-registered pass gate uses `Sharpe_rf0` consistently across strategy and `SPY`.

## 6. Splits & Single-Touch HOLDOUT Discipline
- **DEV:** `2016-01 .. 2021-12` (72 months) — used for Stage 1 Event Study Kill Gate and Stage 2 sanity verification. **Zero parameter tuning.**
- **HOLDOUT:** `2022-01 .. 2026-11` (~58 months, **including partial 2026**) — **NOT touched in Stage 1.** Executed **EXACTLY ONCE** in Stage 2 (if Stage 1 passes), with results written immediately to `REPORT.md` + `summary.json`.

## 7. Pre-Registered Pass / Fail Exit Criteria (US Primary, Net of Costs)

### Stage 1 Kill Gate (DEV ONLY: `2016-01 .. 2021-12` — Must Pass Before Opening HOLDOUT)
1. `Q5` beats `EW_M0` on annualized return net of `1×` costs by **`>= +1.0%/yr`** (`Q5_CAGR_1x - EW_M0_CAGR_1x >= +1.0%`).
2. `Q5 - Q1` average monthly spread is strictly positive (`> 0`, net of `1×` costs).
- **If Stage 1 fails on DEV:** STOP immediately (`FAIL`). HOLDOUT stays sealed. Do not build Stage 2.

### Stage 2 Graduation Gate (Evaluated ONCE on US HOLDOUT: `2022-01 .. 2026-11`)
Either graduation candidate (`M1` or `M2` — logged as **2 attempts** in `TEST_LOG.md`) must satisfy **ALL 5** criteria simultaneously (marginal pass on survivorship-flattered data = **FAIL**):
1. **Net Risk-Adjusted Return (1× & 2× Costs):** HOLDOUT `Sharpe_rf0 >= 0.50` at `1×` costs **AND** `>= 0.40` at `2×` costs.
2. **Matched-Overlay Selection Edge (Anti-Survivorship Gate):** Positive annualized excess CAGR over its **matched overlay control** (`M1` vs `EW_M1`, or `M2` vs `EW_M2`) of **`>= +1.5%/yr`** at `1×` costs **AND** **`> 0%/yr`** at `2×` costs — in **BOTH** DEV and HOLDOUT.
3. **Value-Add vs `SPY` (Same `Sharpe_rf0` Convention):** On HOLDOUT at `1×` costs, beats `SPY` on `Sharpe_rf0` **OR** reduces Max Drawdown by `>= 5` percentage points vs `SPY` while maintaining positive net CAGR.
4. **Time & Concentration Robustness:** Positive net return in `>= 60%` of HOLDOUT calendar years (`>= 3/5`, **including partial 2026**), **AND** HOLDOUT excess return over matched control (`EW_M1`/`EW_M2`) remains `> 0` after dropping either (a) the single best calendar year or (b) the single highest-contributing stock.
5. **Tail Risk Bound:** HOLDOUT Max Drawdown `<= 25.0%`.

## 8. Decision Rule & Pre-Declared Definition of `T004`
- **If `T003` FAILS:** Log `FAIL` in `TEST_LOG.md`, keep `$0` spent on paid data, do not iterate on `T003`.
- **If `T003` PASSES:** A `PASS` here is **permission to buy clean survivorship-free US data — nothing more** (NOT proof of alpha, NOT entry to Phase 3).
- **Pre-Declared Definition of `T004` (Independent Long-History Verification):**
  - Purchase point-in-time, survivorship-free US equity data (including delisted stocks and historical index/liquidity constituents, e.g., Norgate Data or EODHD) extending back to the **late 1990s (target `1998–2026`, providing 15+ independent years `1998–2015` that strictly predate `T003`'s `2016` start)**.
  - Run the **exact same frozen `T003` rules** (`M0`, `M1`, `M2` + matched `EW` controls + `1×`/`2×` costs) with **zero re-optimization** across the pre-2016 independent out-of-sample history (covering 2000–2002 dot-com bear, 2008 GFC, 2009 momentum crash, 2010–2015 expansion) AND the clean survivorship-free 2016–2026 period. Only a `PASS` on `T004` unlocks Phase 3.
