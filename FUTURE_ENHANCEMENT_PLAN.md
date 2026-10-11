# 🚀 Future Enhancement Plan (Breakout Scanner)

## 📌 Guiding Philosophy: The 1% Marginal Gains Principle
Every improvement in this roadmap follows the **1% increment rule**:
- **Zero Disruption**: Never break or destabilize existing working engine logic, state machines, or reporting formats.
- **Measurable Value**: Every change must directly enhance signal accuracy, risk management, execution speed, or user experience.
- **Asset-Class Specific**: Tailor logic appropriately across **US Stocks**, **Crypto**, and **NSE (India)** without hardcoding rigid assumptions.

---

```
                       CURRENT STATE
  (VCP Engine + Multi-Asset 3/3 Bias + Terminal/Telegram Reports)
                             │
                             ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ PHASE 1: Signal Precision (Immediate 1% Increments)        │
  │ • Daily (1D) Macro Confluence Filter                        │
  │ • Sector-Level Relative Strength (Nifty IT, QQQ, etc.)      │
  │ • Retest vs. Raw Breakout State Classifier                  │
  └──────────────────────────────┬──────────────────────────────┘
                                 │
                                 ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ PHASE 2: Automation & Session-Aware Scheduling             │
  │ • Market Session Triggers (NSE IST / US EST / Crypto UTC)   │
  │ • Background Daemon & Deduplication                         │
  └──────────────────────────────┬──────────────────────────────┘
                                 │
                                 ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ PHASE 3: Quantum-Level Signal Precision Scoring Engine     │
  │ • Step 1: VDU & RVOL Multiplier (Volume Footprint)         │
  │ • Step 2: Decaying Contraction Ratio (Wave Physics)        │
  │ • Step 3: RS Momentum Slope (Alpha Divergence)             │
  │ • Step 4: Composite Score Calculator (0-100 & Grade A+/A/B)│
  └──────────────────────────────┬──────────────────────────────┘
                                 │
                                 ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ PHASE 4: Visual Monitoring & UI (Web Dashboard)            │
  │ • Flask Web Dashboard with Live Status Cards                │
  │ • Interactive TradingView Chart Popups                      │
  └──────────────────────────────┬──────────────────────────────┘
                                 │
                                 ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ PHASE 5: Quant Validation & Backtesting                    │
  │ • Portfolio Equity Simulator & Drawdown Analytics          │
  │ • Walk-Forward Walk & Regime Stress Testing                 │
  └──────────────────────────────┬──────────────────────────────┘
                                 │
                                 ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ PHASE 6: Paper Trading & Broker Execution Safeguards       │
  │ • Paper Broker (`paper_broker.py`) with Simulated Fills     │
  │ • Production Safeguards (`safeguards.py` Kill-Switches)     │
  │ • Broker APIs: Dhan/Zerodha (NSE), Alpaca (US), CCXT (Crypto)│
  └─────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Phase-by-Phase Roadmap

### 🔹 Phase 1: Signal Precision (Immediate 1% Increments)
*Focus: Enhancing setup win rate and trade expectancy with zero disruption to core code.*

- [x] **1.1 Higher-Timeframe (Daily / 1D) Macro Confluence**
  - **Concept**: Mark Minervini's cardinal rule: *Never take an hourly breakout against the daily macro trend*.
  - **Enhancement**: Fetch daily candles in the background; verify if the Daily 50 SMA is upward-sloping.
  - **Output**: Tag setups with a `⭐ 1D Confirmed` badge in Section 1 and Section 2.
  - **Status**: ✅ **Implemented and Verified**

- [x] **1.2 Sector-Level Relative Strength Mapping**
  - **Concept**: A stock outperforming its specific sector basket has much stronger institutional tailwinds than one merely outperforming broad indices.
  - **Enhancement**:
    - **NSE**: Auto-map tickers to sub-indices (e.g. `TCS.NS` / `INFY.NS` vs `^CNXIT`, `HDFCBANK.NS` / `SBIN.NS` vs `^NSEBANK`).
    - **US**: Auto-map tickers to sector ETFs (e.g. Tech vs `QQQ`/`XLK`, Financials vs `XLF`, Energy vs `XLE`).
  - **Output**: Append `Sector RS: +X.X%` to the setup diagnostics.
  - **Status**: ✅ **Implemented and Verified**

- [x] **1.3 Retest vs. Raw Breakout Classifier**
  - **Concept**: Some traders prefer buying the initial breakout, while conservative traders prefer the first pullback/retest of the broken pivot level.
  - **Enhancement**: Track if price broke out and is currently retesting the resistance-turned-support level.
  - **Output**: Distinguish between `🚀 BREAKOUT` and `🔄 RETEST_HOLD` in Section 2.
  - **Status**: ✅ **Implemented and Verified**

---

### 🔹 Phase 2: Automation & Session-Aware Scheduling
*Focus: Running the scanner automatically at key market turning points.*

- [ ] **2.1 Market-Session Aligned Schedules**
  - **NSE Market (IST)**:
    - `09:35 AM IST`: Post-opening volatility settlement check.
    - `01:30 PM IST`: European market open / institutional afternoon session.
    - `03:15 PM IST`: EOD swing positioning scan.
  - **US Market (EST)**:
    - `09:45 AM EST`: Opening rush filter.
    - `03:30 PM EST`: Power hour breakouts.
  - **Crypto (24/7 UTC)**:
    - Recurring scan synchronized with 4-hour candle closes (`00:00`, `04:00`, `08:00`, `12:00`, `16:00`, `20:00 UTC`).

- [ ] **2.2 Candle-Close Deduplication & Heartbeat**
  - Assign each signal a unique candle signature (`SYMBOL:TIMEFRAME:TIMESTAMP`) so telegram alerts never duplicate on the same candle.
  - Add a lightweight health check logger (`logs/scanner.log`).

---

### 🔹 Phase 3: Quantum-Level Signal Precision Scoring Engine
*Focus: Upgrading from binary 3/3 checks into a 5-pillar quantitative composite scoring matrix.*

#### 📊 Step-by-Step Implementation Path
Following our **1% marginal gains / zero-disruption rule**:

| Step | Enhancement | Quant Edge |
| :--- | :--- | :--- |
| **Step 1** | **VDU & RVOL Multiplier** | Adds volume exhaustion and breakout expansion scoring. |
| **Step 2** | **Decaying Contraction Ratio** | Scores wave contraction tightness (penalizes wide/loose boxes). |
| **Step 3** | **RS Momentum Slope** | Upgrades binary RS to linear regression slope / divergence. |
| **Step 4** | **Composite Score Calculator** | Aggregates all 5 dimensions into a unified `0–100` score and `Grade A+ / A / B` rating. |

---

- [ ] **3.1 Step 1: Volume Dry-Up (VDU) & Breakout RVOL Multiplier**
  - **Concept**: Institutional accumulation leaves an unmistakable footprint: volume drastically dries up during base consolidation (supply exhaustion) and spikes with high RVOL on breakout.
  - **Quant Logic**:
    - **VDU Ratio**: Measure average volume of the tightest 5 bars against the 20-bar baseline. If $\le 0.60\times$, award full dry-up points.
    - **RVOL Multiplier**: Breakout candle volume must exceed $\ge 1.5\times$ to $2.5\times$ average to validate institutional commitment and filter false wicks.
  - **Output**: Append `VDU: OK` and `RVOL: X.Xx` to setup diagnostics.

- [ ] **3.2 Step 2: Decaying Contraction Ratio & Coil Tightness Scoring**
  - **Concept**: True VCP compression exhibits successive contraction wave decay ($T_1 \rightarrow T_2 \rightarrow T_3$) where volatility systematically collapses.
  - **Quant Logic**:
    - Validate that each contraction cycle has a smaller percentage range than the prior wave.
    - Coil tightness grading:
      - Width $\le 3.5\%$: **Max Score (20/20)** *(Ultra-tight spring coil)*
      - Width $3.6\% - 6.0\%$: **High Score (15/20)**
      - Width $> 8.0\%$: **Penalized Score (5/20)** *(Loose consolidation prone to chop)*

- [ ] **3.3 Step 3: RS Momentum Slope & Alpha Divergence**
  - **Concept**: Outperforming on a green day is ordinary; refusing to drop or printing new highs while the benchmark pulls back is institutional alpha.
  - **Quant Logic**:
    - Compute the 20-bar Linear Regression Slope of the relative strength line: `Stock_Close / Benchmark_Close`.
    - Detect RS divergence: When benchmark prints a lower high but stock prints an equal or higher high, award alpha divergence points.
  - **Output**: Diagnostic flag `[Alpha RS: +X.X% ↗]`.

- [ ] **3.4 Step 4: 5-Pillar Composite Quant Scorer (0–100 & Tier Grading)**
  - **Concept**: Aggregate all quantitative attributes into an objective institutional score:
    1. **Contraction Physics (Coil Decay)**: 20 pts
    2. **Volume Footprint (VDU & RVOL)**: 20 pts
    3. **Multi-Timeframe Trend Template (1D 50 SMA + 1H 20/50 Fan)**: 20 pts
    4. **RS Alpha & Regression Slope**: 20 pts
    5. **Risk-Reward Asymmetry (Stop Tightness & Sky Ratio)**: 20 pts
  - **Output**:
    - `🏆 Grade: A+ [90–100]` *(God-tier setup: tight coil, VDU confirmed, 1D aligned, positive RS slope)*
    - `🏆 Grade: A  [80–89]` *(High conviction breakout)*
    - `🏆 Grade: B  [70–79]` *(Standard playable setup)*
    - `Grade: C [<70]` *(Filtered out from alerts)*

---

### 🔹 Phase 4: Visual Dashboard & Interactive UI
*Focus: Upgrading user experience from pure CLI to an interactive, responsive dashboard.*

- [ ] **4.1 Live Flask Web Dashboard**
  - Upgrade [dashboard.py](file:///Users/naveen/Git/breakout-scanner/dashboard.py) with modern dark-mode aesthetic.
  - Real-time tabular views for **Section 1 (VCP Base Watchlist)** and **Section 2 (Active Trades)** with new Quant Grades.
  - Quick filter buttons by asset class: `All`, `NSE (India)`, `US Catalysts`, `Crypto`.

- [ ] **4.2 Embedded TradingView Widgets**
  - 1-click modal popup displaying the live TradingView chart for any ticker in Section 1 or Section 2.
  - Visual overlay markers for Entry, Stop Loss, and TP1/TP2 levels.

---

### 🔹 Phase 5: Quant Backtesting & Multi-Year Validation
*Focus: Verifying quantitative edge using historical data without look-ahead bias.*

- [ ] **5.1 Portfolio-Level Backtester**
  - Upgrade [run_backtest.py](file:///Users/naveen/Git/breakout-scanner/run_backtest.py) to simulate multi-symbol portfolio equity curves.
  - Model realistic execution:
    - Execution on next candle open after breakout close.
    - Realistic slippage and brokerage fees ($0.05\%$ per leg).
  - Output standard quantitative metrics:
    - Total Trades, Win Rate, Profit Factor, Expectancy ($R$), Max Drawdown %, Sharpe Ratio.

- [ ] **5.2 Walk-Forward Analysis**
  - Split historical datasets into train (in-sample) and test (out-of-sample) windows across multiple market regimes (2022 bear market, 2023 recovery, 2024-2026 momentum).

---

### 🔹 Phase 6: Paper Trading & Live Execution Safeguards
*Focus: Bridging the gap to execution while keeping real capital 100% safe.*

- [ ] **6.1 Paper Trading Engine ([breakout_scanner/paper_broker.py](file:///Users/naveen/Git/breakout-scanner/breakout_scanner/paper_broker.py))**
  - Virtual portfolio maintaining balance, margin, and order states.
  - Automatic order lifecycle tracking: Limit/Stop order placement, fill logging, trailing stop updates on TP1 hit, and exit logging on TP2 or time stop.

- [ ] **6.2 Production Circuit Breakers ([breakout_scanner/safeguards.py](file:///Users/naveen/Git/breakout-scanner/breakout_scanner/safeguards.py))**
  - **Daily Loss Halt**: Halt engine completely if daily floating loss exceeds $2\%$.
  - **Max Concurrent Positions**: Cap active open trades to $3–5$ positions.
  - **Data Latency Guard**: Skip trade generation if data feed latency exceeds 120 seconds.

- [ ] **6.3 Broker Connectors (Future Production Target)**
  - **NSE (India)**: Dhan API or Zerodha KiteConnect.
  - **US Stocks**: Alpaca Trading API.
  - **Crypto**: CCXT library (Binance, Bybit, Coinbase).

---

## 📋 Status & Progress Tracker

| Milestone | Target Market | Status | Primary Files |
|---|---|---|---|
| **VCP Detection & Contraction Engine** | All | ✅ Completed | `engine.py`, `models.py` |
| **Two-Section Reporting (Base & Signals)** | All | ✅ Completed | `scanner.py`, `scan_now.py` |
| **Multi-Market 3/3 Bias Scoring (US/Crypto/NSE)** | US, Crypto, NSE | ✅ Completed | `scanner.py` |
| **Base Width %, Risk %, and P&L % Display** | All | ✅ Completed | `engine.py`, `scanner.py` |
| **Telegram Notifications & Unified Env** | All | ✅ Completed | `daily_report.py`, `live_scanner.py`, `.env` |
| **Higher Timeframe (1D) Confluence** | All | ✅ Completed | `engine.py`, `scanner.py` |
| **Sector-Level Relative Strength** | NSE & US | ✅ Completed | `sectors.py`, `scanner.py` |
| **Retest vs. Raw Breakout Classifier** | All | ✅ Completed | `engine.py`, `models.py`, `scanner.py` |
| **Session-Aware Automated Scheduling** | All | 🟡 Queued | `live_scanner.py` |
| **Quantum Signal Precision: Step 1 (VDU & RVOL)** | All | 🟡 Queued | `engine.py`, `scanner.py` |
| **Quantum Signal Precision: Step 2 (Wave Decay)** | All | ⚪ Planned | `engine.py` |
| **Quantum Signal Precision: Step 3 (RS Slope)** | All | ⚪ Planned | `scanner.py` |
| **Quantum Signal Precision: Step 4 (0–100 Scorer)** | All | ⚪ Planned | `scanner.py`, `models.py` |
| **Flask Dashboard + TradingView UI** | All | ⚪ Planned | `dashboard.py`, `templates/` |
| **Portfolio Backtest & Metrics Dashboard** | All | ⚪ Planned | `run_backtest.py` |
| **Simulated Paper Trading Engine** | All | ⚪ Planned | `paper_broker.py` |
| **Live Broker Execution & Safeguards** | All | ⚪ Future | `safeguards.py` |
