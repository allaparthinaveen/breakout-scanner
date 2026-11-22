# Pre-Registration Template (Alpha Research Gate — Phase 1)

> **Rule:** Complete and commit this 1-page spec **BEFORE** touching data or running code. Do **not** add variants, change lookbacks, or relax thresholds after viewing HOLDOUT results. Every run increments `TEST_LOG.md`.

---

## 1. Metadata
- **Test ID:** `T0XX` *(matches `TEST_LOG.md`)*
- **Date Pre-Registered:** `YYYY-MM-DD`
- **Stage:** `Stage 1: Event Study (Kill Test)` | `Stage 2: Portfolio Backtest` | `Stage 3: Survivorship-Free Paid Data Verification`
- **Data Tier:** `Free (Survivorship-Flattered — Asymmetric Kill Test Only)` | `Paid Point-in-Time (Delisted Included)`

## 2. Hypothesis & Economic Rationale
- **Claim:** *(1–2 sentences: what anomaly is being captured?)*
- **Why it should exist (economic/behavioral mechanism, NOT a chart pattern):** *(e.g., institutional under-reaction, forced rebalancing, risk premium, liquidity provision)*
- **Known failure mode / crash risk:** *(when does this strategy blow up, and what overlay addresses it?)*

## 3. Universe, Data & Bias Disclosure
- **Primary Market & Universe:** *(exact file / index / point-in-time constituent rule)*
- **Benchmarks:** *(market index + equal-weight universe control)*
- **Price Basis:** Auto-adjusted OHLCV (`auto_adjust=True`), strictly causal (`<= signal_date`).
- **Known Biases in This Run:** *(e.g., survivorship bias, missing corporate actions/earnings timestamps, liquidity assumptions)*

## 4. Exact Rules (Frozen — Zero Tuning)
- **Signal Formula (exact math, bar lag, warmup):**
- **Variants Allowed (max 3–5, explicitly enumerated):**
  - `V0`: Baseline
  - `V1`: ...
- **Execution / Fill Model:** *(e.g., signal at close `t`, fill at open `t+1`, one-way/round-trip cost)*
- **Rebalance / Exit / Overlay Rules:** *(fixed thresholds only)*

## 5. Cost Model & Stress Test
- **1× Baseline Costs:** *(commission + spread + slippage + STT/taxes)*
- **2× Stress Costs:** *(mandatory survival gate)*

## 6. Splits & Discipline
- **DEV Split:** *(start .. cutoff — used ONLY for code verification / sanity checks, never parameter grids)*
- **HOLDOUT Split:** *(cutoff+1 .. end — executed **EXACTLY ONCE** at the end; results written immediately)*
- **Multiple-Testing Count (`N_tests`):** *(cumulative tests in `TEST_LOG.md`; higher `N_tests` raises required t-stat/Sharpe)*

## 7. Pre-Registered Pass / Fail Exit Criteria (Evaluated on HOLDOUT, Net of Costs)
All gates must pass simultaneously — otherwise verdict is **FAIL**:
1. **Expectancy & 2× Cost Survival:** *(exact min Sharpe / avg R at 1× and 2× costs)*
2. **Benchmark / Control Outperformance:** *(must beat market & equal-weight control)*
3. **Cross-Split Consistency:** *(holds in both DEV and HOLDOUT)*
4. **Time & Concentration Stability:** *(positive in `>= 60%` of years; does not depend on 1 year, 1 sector, or 1 stock)*
5. **Drawdown / Tail Bound:** *(max drawdown ceiling)*

## 8. Decision Rule
- **If FAIL:** Stop immediately. Log `FAIL` in `TEST_LOG.md`. Do **not** tweak and re-run on the same HOLDOUT.
- **If PASS on Free Data:** Treat marginal pass as `FAIL`. On decisive pass, graduate to **Paid Point-in-Time Data Verification** before Phase 3.
- **If PASS on Paid Point-in-Time Data:** Graduate to Phase 3 (Portfolio Engineering) → Phase 4 (4–8 week Execution/Slippage Paper Audit).
