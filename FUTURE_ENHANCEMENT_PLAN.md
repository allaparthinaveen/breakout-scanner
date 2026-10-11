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
  │ PHASE 3: Visual Monitoring & UI (Web Dashboard)            │
  │ • Flask Web Dashboard with Live Status Cards                │
  │ • Interactive TradingView Chart Popups                      │
  └──────────────────────────────┬──────────────────────────────┘
                                 │
                                 ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ PHASE 4: Quant Validation & Backtesting                    │
  │ • Portfolio Equity Simulator & Drawdown Analytics          │
  │ • Walk-Forward Walk & Regime Stress Testing                 │
  └──────────────────────────────┬──────────────────────────────┘
                                 │
                                 ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ PHASE 5: Paper Trading & Broker Execution Safeguards       │
  │ • Paper Broker (`paper_broker.py`) with Simulated Fills     │
  │ • Production Safeguards (`safeguards.py` Kill-Switches)     │
  │ • Broker APIs: Dhan/Zerodha (NSE), Alpaca (US), CCXT (Crypto)
  └─────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Phase-by-Phase Roadmap

### 🔹 Phase 1: Signal Precision (Immediate 1% Increments)
*Focus: Enhancing setup win rate and trade expectancy with zero disruption to core code.*

- [ ] **1.1 Higher-Timeframe (Daily / 1D) Macro Confluence**
  - **Concept**: Mark Minervini's cardinal rule: *Never take an hourly breakout against the daily macro trend*.
  - **Enhancement**: Fetch daily candles in the background; verify if the Daily 50 SMA is upward-sloping.
  - **Output**: Tag setups with a `⭐ 1D Confirmed` badge in Section 1 and Section 2.
  - **Expected Impact**: Filters out 20–30% of low-conviction counter-trend fakeouts.

- [ ] **1.2 Sector-Level Relative Strength Mapping**
  - **Concept**: A stock outperforming its specific sector basket has much stronger institutional tailwinds than one merely outperforming broad indices.
  - **Enhancement**:
    - **NSE**: Auto-map tickers to sub-indices (e.g. `TCS.NS` / `INFY.NS` vs `^CNXIT`, `HDFCBANK.NS` / `SBIN.NS` vs `^NSEBANK`).
    - **US**: Auto-map tickers to sector ETFs (e.g. Tech vs `QQQ`/`XLK`, Financials vs `XLF`, Energy vs `XLE`).
  - **Output**: Append `Sector RS: +X.X%` to the setup diagnostics.

- [ ] **1.3 Retest vs. Raw Breakout Classifier**
  - **Concept**: Some traders prefer buying the initial breakout, while conservative traders prefer the first pullback/retest of the broken pivot level.
  - **Enhancement**: Track if price broke out and is currently retesting the resistance-turned-support level.
  - **Output**: Distinguish between `🚀 BREAKOUT` and `🔄 RETEST_HOLD` in Section 2.

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

### 🔹 Phase 3: Visual Dashboard & Interactive UI
*Focus: Upgrading user experience from pure CLI to an interactive, responsive dashboard.*

- [ ] **3.1 Live Flask Web Dashboard**
  - Upgrade [dashboard.py](file:///Users/naveen/Git/breakout-scanner/dashboard.py) with modern dark-mode aesthetic.
  - Real-time tabular views for **Section 1 (VCP Base Watchlist)** and **Section 2 (Active Trades)**.
  - Quick filter buttons by asset class: `All`, `NSE (India)`, `US Catalysts`, `Crypto`.

- [ ] **3.2 Embedded TradingView Widgets**
  - 1-click modal popup displaying the live TradingView chart for any ticker in Section 1 or Section 2.
  - Visual overlay markers for Entry, Stop Loss, and TP1/TP2 levels.

---

### 🔹 Phase 4: Quant Backtesting & Multi-Year Validation
*Focus: Verifying quantitative edge using historical data without look-ahead bias.*

- [ ] **4.1 Portfolio-Level Backtester**
  - Upgrade [run_backtest.py](file:///Users/naveen/Git/breakout-scanner/run_backtest.py) to simulate multi-symbol portfolio equity curves.
  - Model realistic execution:
    - Execution on next candle open after breakout close.
    - Realistic slippage and brokerage fees ($0.05\%$ per leg).
  - Output standard quantitative metrics:
    - Total Trades, Win Rate, Profit Factor, Expectancy ($R$), Max Drawdown %, Sharpe Ratio.

- [ ] **4.2 Walk-Forward Analysis**
  - Split historical datasets into train (in-sample) and test (out-of-sample) windows across multiple market regimes (2022 bear market, 2023 recovery, 2024-2026 momentum).

---

### 🔹 Phase 5: Paper Trading & Live Execution Safeguards
*Focus: Bridging the gap to execution while keeping real capital 100% safe.*

- [ ] **5.1 Paper Trading Engine ([breakout_scanner/paper_broker.py](file:///Users/naveen/Git/breakout-scanner/breakout_scanner/paper_broker.py))**
  - Virtual portfolio maintaining balance, margin, and order states.
  - Automatic order lifecycle tracking: Limit/Stop order placement, fill logging, trailing stop updates on TP1 hit, and exit logging on TP2 or time stop.

- [ ] **5.2 Production Circuit Breakers ([breakout_scanner/safeguards.py](file:///Users/naveen/Git/breakout-scanner/breakout_scanner/safeguards.py))**
  - **Daily Loss Halt**: Halt engine completely if daily floating loss exceeds $2\%$.
  - **Max Concurrent Positions**: Cap active open trades to $3–5$ positions.
  - **Data Latency Guard**: Skip trade generation if data feed latency exceeds 120 seconds.

- [ ] **5.3 Broker Connectors (Phase 10 Target)**
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
| **Higher Timeframe (1D) Confluence** | All | 🟡 Next Up | `engine.py`, `scanner.py` |
| **Sector-Level Relative Strength** | NSE & US | ⚪ Planned | `scanner.py` |
| **Session-Aware Automated Scheduling** | All | ⚪ Planned | `live_scanner.py` |
| **Flask Dashboard + TradingView UI** | All | ⚪ Planned | `dashboard.py`, `templates/` |
| **Portfolio Backtest & Metrics Dashboard** | All | ⚪ Planned | `run_backtest.py` |
| **Simulated Paper Trading Engine** | All | ⚪ Planned | `paper_broker.py` |
| **Live Broker Execution & Safeguards** | All | ⚪ Future | `safeguards.py` |
