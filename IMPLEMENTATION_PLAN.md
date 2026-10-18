# Institutional Remediation & Implementation Plan

> **Scope:** Remediation of critical and high-priority findings from the quantitative review on commit `1d273fd`.  
> **Target Branch:** `main`  
> **Core Objective:** Eliminate lookahead bias, enforce data integrity, unify backtester with the live execution engine, and establish trustworthy NSE performance metrics.

---

## 🗺️ Roadmap & Phase Overview

```mermaid
flowchart TD
    subgraph Phase1 ["Phase 1: Data Integrity & Confirmed Candle Policy"]
        A1["Robust Data Validation Layer<br>(Sort, Dedup, OHLC Sanity)"]
        A2["Confirmed Candle Policy<br>(Discard Incomplete/Open Live Bar)"]
        A3["Eliminate .bfill() Lookahead Leak<br>(Strict Forward-Only Alignment)"]
    end

    subgraph Phase2 ["Phase 2: Bidirectional Scoring & Benchmark Precision"]
        B1["Bidirectional Bias Scorer<br>(Symmetric Long vs. Short Evaluation)"]
        B2["Meaningful VCP Setup Gating<br>(Tightening & Volume Exhaustion Gates)"]
        B3["Accurate NSE Sector Mappings<br>(Nifty Financial Services & Utilities)"]
    end

    subgraph Phase3 ["Phase 3: Unified Institutional Backtest Engine"]
        C1["Chronological Bar-by-Bar Simulator<br>(Entry at Next-Bar Open)"]
        C2["Full Live Engine Trade Parity<br>(TP1 Breakeven Trailing & Time Exits)"]
        C3["Friction Modeling & End-of-Test Settlement<br>(Slippage, STT/Brokerage, Mark-to-Market)"]
    end

    subgraph Phase4 ["Phase 4: NSE Equity Backtesting Suite & Metrics"]
        D1["Multi-Market Backtest CLI (run_backtest.py)<br>(NSE Top 30, US Tech, Crypto)"]
        D2["Out-of-Sample Validation & Equity Curves<br>(Expectancy, Drawdown, Profit Factor)"]
    end

    Phase1 --> Phase2 --> Phase3 --> Phase4
```

---

## 📌 Phase 1: Data Integrity, Confirmed Candle Policy & Lookahead Elimination (Priority 1 & 2 Fixes)

### 1.1 Robust Market Data Validation Layer (`breakout_scanner/providers/validation.py`)
* **Problem**: [yfinance_provider.py](file:///Users/naveen/Git/breakout-scanner/breakout_scanner/providers/yfinance_provider.py) only drops null rows without verifying data integrity.
* **Solution**: Implement strict data validation:
  1. **Chronological Sorting & Deduplication**: Sort index chronologically and remove duplicate timestamps (`~df.index.duplicated(keep='last')`).
  2. **Geometric OHLC Consistency**:
     $$\text{High} \ge \text{Low}, \quad \text{High} \ge \max(\text{Open}, \text{Close}), \quad \text{Low} \le \min(\text{Open}, \text{Close})$$
     $$\text{Open} > 0, \quad \text{High} > 0, \quad \text{Low} > 0, \quad \text{Close} > 0, \quad \text{Volume} \ge 0$$
  3. **Minimum History Warmup**: Require at least 50 valid bars for indicator stability (50 SMA, 20 Base, 14 ATR). Reject or flag datasets failing this threshold.

### 1.2 Confirmed-Candle Policy (Anti-Wick Protection)
* **Problem**: When querying intraday candles (e.g. 1H) during active trading hours, `yfinance` returns the *currently forming, unclosed candle* as the latest row. An unfinished 15-minute wick can trigger an alert that collapses before the hour ends.
* **Solution**:
  - Check the timestamp of the latest candle against current UTC/local market time.
  - If the latest bar has not closed, truncate it from analysis so that signals are generated **strictly on closed, locked candles**.

### 1.3 Elimination of Lookahead Leaks in Relative Strength (`scanner.py`)
* **Problem**: [scanner.py:93](file:///Users/naveen/Git/breakout-scanner/breakout_scanner/scanner.py#L93) uses:
  ```python
  proxy_returns = proxy_df['Close'].reindex(stock_returns.index).ffill().bfill()
  ```
  `.bfill()` fills earlier missing benchmark values using **future** prices, creating lookahead distortion.
* **Solution**: Remove `.bfill()` entirely. Use strict forward-fill (`.ffill()`) and drop any leading unaligned data points.

---

## 📌 Phase 2: Bidirectional Scoring, Setup Gating & NSE Benchmark Precision (Priority 3 & 4 Fixes)

### 2.1 Bidirectional Bias Scorer (`scanner.py`, `scorer.py`)
* **Problem**: Bias scoring is strictly upside-oriented (`score += 1` for bullish trend, outperformance, and Nifty > 50 SMA). For a `SELL` breakdown, a high score is misleadingly labeled as `"Strong Upside Bias (3/3)"`.
* **Solution**:
  - Implement symmetric scoring based on setup direction:
    - **BUY / Long**: Reward $\text{Price} > \text{SMA50}$, stock outperformance ($\text{RS} > 0$), and macro regime above 50 SMA.
    - **SELL / Short**: Reward $\text{Price} < \text{SMA50}$, stock underperformance ($\text{RS} < 0$), and macro regime below 50 SMA.
    - **CONTRACTION**: Assess compression above vs. below 50 SMA to report accurate directional accumulation bias.

### 2.2 Meaningful VCP Setup Gating (`config/default.yaml`, `engine.py`)
* **Problem**: `require_tightening: false` and `use_volume_contraction: false` allow loose boxes to qualify as VCP setups.
* **Solution**: Enable measurable contraction gates with sensible defaults:
  - Tightening ratio decay $\le 0.70$
  - Volume contraction $\le 0.85$ (dry-up confirmation)

### 2.3 Refined NSE Sector Mappings (`sectors.py`)
* **Problem**: Non-bank financial services (`BAJFINANCE.NS`, `LICI.NS`, etc.) are mapped to `^NSEBANK` (commercial banks only), and utilities (`NTPC.NS`, `POWERGRID.NS`) to `^CNXENERGY`.
* **Solution**:
  - Map NBFCs and insurance names to Nifty Financial Services (`NIFTY_FIN_SERVICE.NS` or `^CNXFIN`).
  - Distinguish Power Utilities from Oil & Gas.

---

---

## 📌 Phase 3: Unified Institutional Backtest Engine (Priority 1 Critical Fix) ✅ COMPLETED

### 3.1 Chronological Bar-by-Bar Simulator (`backtester.py`) ✅
* **Zero Lookahead**: Signals confirmed at Bar $i$ Close $\rightarrow$ Execution strictly at Bar $i+1$ Open.
* Trade execution checks begin strictly on Bar $i+1$ and beyond.

### 3.2 100% Live Engine Trade Parity ✅
* Replicated all live trade management mechanics from [engine.py](file:///Users/naveen/Git/breakout-scanner/breakout_scanner/engine.py):
  1. **TP1 (1.0R)**: Scales out 50% shares and trails stop loss to Breakeven ($+0.10\%$).
  2. **TP2 (2.0R)**: Fully closes remaining position.
  3. **Time-Based Exit**: Closes position if held longer than `max_hold_days`.

### 3.3 Friction & Edge Accounting ✅
* **Slippage**: 0.05% slippage on entry and exit legs.
* **Transaction Costs**: 0.05% brokerage and exchange fees on every leg.
* **Gap-Through-Stop**: Filled at Open price if candle opens past the stop level.
* **End-of-Test Settlement**: Mark-to-market all remaining open positions at test end with `END_OF_DATA`.

---

## 📌 Phase 4: NSE Backtesting Suite & Out-of-Sample Verification ✅ COMPLETED

### 4.1 Multi-Market Backtest CLI (`run_backtest.py`) ✅
* Added CLI arguments:
  ```bash
  python3 run_backtest.py --market nse --watchlist config/nsetop10_watchlist.txt --period 3y
  python3 run_backtest.py --market us --watchlist config/top30_watchlist.txt --period 3y
  python3 run_backtest.py --market crypto --watchlist config/crypto_watchlist.txt --period 3y
  ```
* **Verified Backtest Results (`config/watchlist.txt` — 16 Stocks, 3-Year Daily with Structural Stop & 5% Rejection Gate)**:
  | Metric | Value |
  | :--- | :--- |
  | **Total Completed Trades** | **272** |
  | **Win / Loss Distribution** | 123 Wins / 149 Losses |
  | **Realized Win Rate** | **45.22%** |
  | **Profit Factor** | **1.47** |
  | **Average R-Multiple** | **+0.14R** |
  | **TP1 (1.0R) Scale-Out Hit Rate** | **40.44%** |
  | **Total Gross P&L** | **+$33,210.04** |
  | **Total Frictions (Slippage & Fees)** | **$5,620.75** |
  | **Net Realized P&L** | **+$27,589.29** |
  | **Portfolio Net Return** | **+27.59%** |
  | **Portfolio Max Drawdown** | **4.40%** (Significantly reduced from 7.62%) |
  | **Trade Execution Log** | [`backtest/all_trades.csv`](file:///Users/naveen/Git/breakout-scanner/backtest/all_trades.csv) (272 trades) |
  | **Portfolio Summary JSON** | [`backtest/portfolio_summary.json`](file:///Users/naveen/Git/breakout-scanner/backtest/portfolio_summary.json) |

* **Per-Symbol Highlights**:
  - `NVDA`: 4 trades, **75.0%** win rate, Profit Factor **8.48**, Net Return **+4.06%**
  - `ACN`: 20 trades, **60.0%** win rate, Profit Factor **3.72**, Net Return **+7.21%**
  - `GS`: 21 trades, **52.4%** win rate, Profit Factor **2.09**, Net Return **+4.30%**
  - `BAC`: 27 trades, **55.6%** win rate, Profit Factor **2.05**, Net Return **+5.14%**
  - `AAPL`: 21 trades, **42.9%** win rate, Profit Factor **1.73**, Net Return **+2.97%**
  - `NFLX`: 19 trades, **42.1%** win rate, Profit Factor **1.72**, Net Return **+2.41%**
  - `MSFT`: 25 trades, **44.0%** win rate, Profit Factor **1.46**, Net Return **+2.39%**
  - `TSLA`: 0 trades (Wide/loose swings $> 5\%$ risk properly rejected by the structural risk gate)

---

## 📌 Phase 5: Post-Audit Refinements (P0 & P1 Action Items)

### 5.1 Fix 1 (P0): Stop Placement & Risk Separation ✅ COMPLETED
* **Structural Stop Anchoring**: Stop loss is anchored strictly to base support (or ATR), without clamping into market noise.
* **Risk Rejection Gate**: If structural risk $> \text{max\_stop\_pct}$ (5.0%), the setup is **rejected/invalidated** rather than shortening the stop.
* **Dynamic Position Sizing**: Every trade dynamically scales share count so total dollar risk is mathematically locked to exactly **1.0% of capital**.
* **Variable Shadowing Fix**: Eliminated inner `risk_pct` variable collision that caused sizing distortions.

### 5.2 Fix 2 (P0): Time-Exit Calendar/Timeframe Synchronization ✅ COMPLETED
* **Elapsed Days via Timestamps**: Synchronized holding periods in both `engine.py` and `backtester.py` to calculate elapsed calendar days using timestamps (`(current_dt - entry_dt).total_seconds() / 86400.0`).
* **Timeframe Scaling Fallback**: For integer-indexed datasets, automatically scales bars by timeframe (e.g. 7 bars/day for NSE 1H, 24 bars/day for crypto 1H, 25 bars/day for 15M).
* **Prevention of Premature 1H Exits**: Trades on 1-hour or 15-minute timeframes now hold for the intended swing horizon (e.g. 10 days / 70+ hourly bars) instead of prematurely exiting after 10 hours.
* **Verification**: Verified with `test_hourly_timeframe_hold_days_synchronization` in `tests/test_backtester.py`.

### 5.3 Fix 3 (P1): Scorer Integrity & Missing Evidence Handling ✅ COMPLETED
* **Zero Phantom Points**: Completely eliminated fake fallback points (+5, +4, +10, +6) across all 5 quantitative pillars in `scorer.py`.
* **Zero-Point Pillar Rule**: Missing evidence strictly yields $0$ points for that pillar, preventing unearned grade inflation.
* **Unconfirmed Evidence Flags**: Automatically flags missing pillars on `setup.evidence_status` (`Unconfirmed HTF`, `Unconfirmed Sector`, `Unconfirmed Volume`).
* **Verification**: Verified with `test_missing_evidence_zero_points_and_unconfirmed_flags` in `tests/test_precision.py`.

### 5.4 Fix 4 (P1): Provider Closed-Candle Default & Cache Invalidation ✅ COMPLETED
* **Closed Candle Default**: `YFinanceProvider` and `validate_and_clean_candles` now default to `enforce_closed_candle = True` to prevent unclosed bar lookahead in live and test environments.
* **Cache TTL & Session Invalidation**: In-memory `daily_cache` and `market_cache` now track timestamps (`daily_cache_ts`) and automatically invalidate entries after 4 hours or whenever the calendar date/session advances.
* **SMA50 Slope Verification**: Higher-timeframe 1D confirmation now requires positive slope (`SMA50[-1] > SMA50[-5]`) for Bullish setups and negative slope (`SMA50[-1] < SMA50[-5]`) for Bearish setups.
* **Verification**: Verified with `test_provider_enforce_closed_candle_default` and `test_sma50_slope_check_and_cache_ttl` in `tests/test_validation.py`.




