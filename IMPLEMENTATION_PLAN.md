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

## 📌 Phase 3: Unified Institutional Backtest Engine (Priority 1 Critical Fix)

### 3.1 Chronological Bar-by-Bar Simulator (`backtester.py`)
* **Problem**: [backtester.py](file:///Users/naveen/Git/breakout-scanner/breakout_scanner/backtester.py) checks the signal candle's High/Low on the same bar of entry, creating unrealistic same-bar stop/target fills.
* **Solution**:
  - **Realistic Entry Timing**: When a breakout triggers on the close of Bar $i$, the trade enters at the **Open of Bar $i+1$** ($Open_{i+1}$).
  - Trade execution checks begin strictly on Bar $i+1$, preventing same-bar lookahead.

### 3.2 100% Live Engine Trade Parity
* Replicate all live trade management mechanics from [engine.py](file:///Users/naveen/Git/breakout-scanner/breakout_scanner/engine.py):
  1. **TP1 (1.0R)**: Scales out partial profit and trails stop loss to Breakeven ($+0.1\%$).
  2. **TP2 (2.0R)**: Fully closes remaining position.
  3. **Time-Based Exit**: Closes position if held longer than `max_hold_days`.

### 3.3 Friction & Edge Accounting
* **Slippage**: Model 0.05% slippage on entry and exit.
* **Transaction Costs**: Apply brokerage, STT, and exchange fees (0.05% - 0.10%).
* **Gap-Through-Stop**: If Bar $i+1$ opens below the stop price for a long, exit at $Open_{i+1}$, not the theoretical stop.
* **End-of-Test Settlement**: Mark-to-market all remaining open positions at test end.

---

## 📌 Phase 4: NSE Backtesting Suite & Out-of-Sample Verification

### 4.1 Multi-Market Backtest CLI (`run_backtest.py`)
* Add command-line arguments:
  ```bash
  python3 run_backtest.py --market nse --watchlist config/nsetop10_watchlist.txt --period 3y
  python3 run_backtest.py --market us --watchlist config/top30_watchlist.txt --period 3y
  python3 run_backtest.py --market crypto --watchlist config/crypto_watchlist.txt --period 3y
  ```
* Compute and persist:
  - Win Rate (%)
  - Profit Factor
  - Average R-Multiple
  - Max Drawdown (%)
  - Total Net P&L (after slippage & commissions)
  - Full trade execution logs (`backtest/all_trades.csv`)
