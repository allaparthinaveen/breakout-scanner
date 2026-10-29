# 🚀 Institutional Breakout & VCP Scanner

An institutional-grade, deterministic quantitative trading and scanning engine designed to isolate and execute high-probability structural breakouts across global equities and crypto. The system strictly eliminates look-ahead bias, enforces a confirmed-candle policy, and is backed by a 5-pillar composite scoring matrix, an event-driven institutional backtester, a shared-capital portfolio simulator, and production circuit breakers.

> 📖 **Trader's Handbook**: For complete signal interpretation, diagnostic badges, and step-by-step decision rules, see [USAGE.md](USAGE.md).

---

## 🧠 System Architecture

The engine implements a strict state-machine flow inspired by Pine Script mechanics, migrating setups through rigorous validation checks bar by bar:

$$\text{IDLE} \longrightarrow \text{CONSOLIDATION} \longrightarrow \text{ACCEPTANCE\_WATCH} \longrightarrow \text{ACCEPTED\_RETEST\_WATCH} \longrightarrow \text{BUY / SELL}$$

```mermaid
graph TD
    S1(("🔍 IDLE<br/>Seeking Trend")) -->|Trend Found & Range Tight| S2
    
    S2["📦 CONSOLIDATION<br/>Energy Building (VCP)"] -->|Price Crosses Resistance/Support| S3
    S2 -->|Consolidation Timeout / Loose Range| S1
    
    S3["💥 ACCEPTANCE WATCH<br/>Initial Breakout"] -->|Bar Closes & Holds Outside Box| S4
    S3 -->|Fails to Hold (False Wick)| S1
    
    S4["🛡️ RETEST WATCH<br/>Pullback / Confirmation"] -->|Touches Box & Rejects| S5
    S4 -->|Falls Back Inside / Invalidation| S1
    
    S5((("🎯 EXECUTION<br/>Buy or Sell (Next Open)")))
    
    style S1 fill:#1e293b,stroke:#94a3b8,color:#fff
    style S2 fill:#b45309,stroke:#f59e0b,color:#fff
    style S3 fill:#1d4ed8,stroke:#3b82f6,color:#fff
    style S4 fill:#4338ca,stroke:#6366f1,color:#fff
    style S5 fill:#15803d,stroke:#22c55e,color:#fff,stroke-width:4px
```

### Core Components Breakdown

1. **Deterministic Breakout Engine ([`breakout_scanner/engine.py`](breakout_scanner/engine.py))**:
   Evaluates price contraction cycles, ATR, dynamic pivot support/resistance, decaying contraction ratios ($T_1 \to T_2 \to T_3 \le 0.70$), and Volume Dry-Up (VDU).
2. **Multi-Asset Scanner ([`breakout_scanner/scanner.py`](breakout_scanner/scanner.py))**:
   Manages state transitions and persistent memory across multiple symbols simultaneously, tracking relative strength and multi-timeframe daily macro confluence.
3. **5-Pillar Quantum Scorer ([`breakout_scanner/scorer.py`](breakout_scanner/scorer.py))**:
   Quantifies setup conviction into an objective `0–100` score and letter tiers (`A+`, `A`, `B`, `C`).
4. **Data Integrity & Validation Layer ([`breakout_scanner/providers/validation.py`](breakout_scanner/providers/validation.py))**:
   Enforces chronological sorting, duplicate timestamp purging, geometric OHLC consistency, and the anti-wick **Confirmed-Candle Policy**.
5. **Institutional Backtester ([`breakout_scanner/backtester.py`](breakout_scanner/backtester.py))**:
   Chronological event-driven simulation engine with **zero lookahead** (signals locked at Bar $i$ Close $\to$ executed strictly at Bar $i+1$ Open) with full trade-parity for TP1 breakeven trailing, structural stops, and time exits.
6. **Shared-Capital Portfolio Simulator ([`breakout_scanner/portfolio.py`](breakout_scanner/portfolio.py))**:
   Models authentic multi-symbol portfolio equity curves with realistic cash allocation, position limits, slippage, and brokerage fees.
7. **Risk & Sizing Engine ([`breakout_scanner/risk.py`](breakout_scanner/risk.py))**:
   Calculates position sizes based on capital, maximum risk per trade, volatility, and structural stop-loss levels.
8. **Production Safeguards & Circuit Breakers ([`breakout_scanner/safeguards.py`](breakout_scanner/safeguards.py))**:
   Master gatekeeper implementing Global Kill-Switches, Daily Loss Halts (e.g., $-2\%$), Stale Data Guards ($>120\text{s}$ delay), and Bid-Ask Spread Filters.
9. **Automated Scheduler & Background Daemon ([`breakout_scanner/scheduler.py`](breakout_scanner/scheduler.py), [`live_scanner.py`](live_scanner.py))**:
   Triggers scans right at candle closes (`:01` past the hour), deduplicating alerts (`data/alert_dedup.json`) and pushing formatted alerts to Telegram.
10. **Signal Quality & Noise Reduction Engine ([`breakout_scanner/signal_quality.py`](breakout_scanner/signal_quality.py))**:
    Evaluates breakout candle upper-close location (wick rejection filter), intra-base volume dry-up slope (last 1/3 vs first 1/3 decay), continuous distance-to-trigger tightness, and base-piercing memory.
11. **Interactive Web Dashboard ([`dashboard.py`](dashboard.py))**:
    Flask-based UI featuring dark mode, setup diagnostic cards, and TradingView chart overlays.

---

## 🛡️ Data Integrity & Anti-Lookahead Foundation

The platform adheres to strict institutional data integrity rules:

* **Confirmed-Candle Policy (Anti-Wick Guard)**: Real-time queries drop the active, unclosed bar. Signals are generated exclusively on fully confirmed, closed candles to eliminate false breakout wicks.
* **Elimination of Lookahead Leaks**: Relative strength calculation strictly uses forward-fill (`.ffill()`). All backward-filling (`.bfill()`) is prohibited to prevent future price leakage.
* **Geometric OHLC Sanity**:
  $$\text{High} \ge \text{Low}, \quad \text{High} \ge \max(\text{Open}, \text{Close}), \quad \text{Low} \le \min(\text{Open}, \text{Close}), \quad \text{Volume} \ge 0$$
* **Next-Bar Execution**: Backtested trades are executed on the open of the following candle ($i+1$), factoring in execution friction ($0.05\%$ slippage/fees per leg).

---

## 📊 The 5-Pillar Quantitative Scoring Matrix (0–100 Points)

Every breakout and base consolidation is scored objectively across five dimensions in [`breakout_scanner/scorer.py`](breakout_scanner/scorer.py):

| Pillar | Dimension | Max Points | Institutional Criteria |
| :--- | :--- | :---: | :--- |
| **1. Contraction Physics** | Coil Decay & Tightness | **20 pts** | • Base width $\le 4\%$ (10 pts), $\le 6.5\%$ (8 pts), $> 9\%$ (2 pts)<br>• Contraction ratio decay $\le 0.40\times$ (10 pts), $\le 0.70\times$ (8 pts) |
| **2. Volume Footprint** | Supply Exhaustion / RVOL | **20 pts** | • **Bases**: Volume Dry-Up (VDU) $\le 0.60\times$ (20 pts), $\le 0.75\times$ (16 pts)<br>• **Breakouts**: RVOL $\ge 2.0\times$ (20 pts), $\ge 1.5\times$ (17 pts), $\ge 1.2\times$ (14 pts) |
| **3. Trend Template** | 1D Macro Confluence | **20 pts** | • 1D 50 SMA upward slope ⭐ 1D (12 pts)<br>• 1H trend alignment with price above key EMAs (8 pts) |
| **4. Relative Strength Alpha**| Benchmark & Sector Lead | **20 pts** | • Sector RS Delta $\ge +3.0\%$ (10 pts), $> 0.0\%$ (8 pts)<br>• Linear regression slope ↗ accelerating (10 pts), → neutral (6 pts) |
| **5. Risk-Reward Asymmetry**| Stop Tightness & Structure | **20 pts** | • Risk width $\le 1.2\%$ (12 pts), $\le 2.0\%$ (9 pts)<br>• Confirmed structural retest / pattern geometry (8 pts) |

### Tier Grading System

| Grade | Score Range | Classification | Actionable Trading Rule |
| :---: | :---: | :--- | :--- |
| **A+** | **90 – 100** | **Institutional "God-Tier"** | Ultra-tight coil, supply dry-up, 1D macro aligned, accelerating sector alpha. Maximum conviction / full size. |
| **A** | **80 – 89** | **High Conviction Breakout** | Robust structure with strong institutional tailwinds. Standard full size. |
| **B** | **70 – 79** | **Playable Standard Setup** | Valid base, but either slightly wider width or neutral relative strength. Half size or tighter stop. |
| **C** | **< 70** | **Filtered / Sub-optimal** | Poor volume, loose coil, or counter-trend drag. Skipped by default. |

### Phase 2 Additive Signal Quality Modifiers (Feature-Flagged)

To reduce false breakouts without hard-killing valid setups, Phase 2 introduces optional score modifiers evaluated via [`breakout_scanner/signal_quality.py`](breakout_scanner/signal_quality.py) and applied in [`breakout_scanner/scorer.py`](breakout_scanner/scorer.py) (default weights = `0.0`):

* **Breakout Candle Upper Close (`close_location_weight`)**: $+5$ pts if candle closes in top 30% of range ($\ge 0.70$), $-5$ pts for heavy upper-wick rejection ($< 0.40$).
* **Volume Dry-Up Slope (`vdu_slope_weight`)**: $+5$ pts if volume in the final 1/3 of the base contracts to $\le 0.70\times$ of the first 1/3.
* **Continuous Trigger Tightness (`trigger_tightness_weight`)**: $+5$ pts if base is ultra-tight ($\le 4.0\%$) and price is coiling within $0.75\%$ of pivot.
* **Base Piercing Memory (`failed_breakout_penalty_weight`)**: $-5$ pts per prior failed breakout attempt in the base (max $-10$ pts) to penalize churned resistance.

### Phase 3 Confluence & Regime Context Layer (Feature-Flagged)

Phase 3 introduces macro and structural alignment evaluated via [`breakout_scanner/confluence.py`](breakout_scanner/confluence.py) and applied in [`breakout_scanner/scorer.py`](breakout_scanner/scorer.py) (default weights = `0.0` / disabled):

* **Multi-Timeframe 4H Confluence (`four_hour_bonus_weight`)**: $+5$ pts when 4H resampled structure exhibits bullish alignment (4H EMA20 > EMA50 and positive slope). Badged with `⭐⭐ Multi-TF (1D+4H)`.
* **Market Regime Volatility Attenuator (`regime_penalty_weight`)**: $-8$ pts when benchmark (SPY/QQQ) suffers high-volatility distribution (benchmark below 50 SMA with 20-period ATR expanded beyond $+2\sigma$).
* **Earnings Proximity Warning & Penalty (`earnings_penalty_weight`)**: $-5$ pts when earnings occur within $0–3$ trading days to shield capital against binary overnight risk. Setups are badged with `⚠️ Earnings in N days`.

### Phase 4 Risk & Portfolio Guard Layer (Feature-Flagged)

Phase 4 bridges technical breakout signals to live institutional capital allocation via [`breakout_scanner/portfolio_guard.py`](breakout_scanner/portfolio_guard.py) (default: `enabled: false` or accessible via CLI `--slate`):

* **Position Sizing Engine**: Converts account equity risk (default $1.0\% \to \$1,000$ on $\$100\text{k}$) and ATR structural stop distance into exact share counts, constrained by max position capital (default $20\% \to \$20,000$).
* **Correlation Stacking Guard**: Detects simultaneous alerts in the same sector/industry basket (e.g., multiple semiconductor setups) and selects only the single highest-conviction setup (`Grade A+`), suppressing correlated redundancy.
* **Daily Risk Budget Engine**: Enforces max daily risk budget (default $2.0\% \to \$2,000$), max concurrent active positions (default $4$ trades), and sector exposure caps (default $25\%$).
* **Actionable Trade Slate**: Generates live execution trade cards with exact share sizes, dollar risk, position value, TP1/TP2 targets, and rejection reasons for suppressed candidates.

### Phase 5 Validation & Hardening (A+ Proof)

Phase 5 proves the system's institutional grade and bulletproofs operations:

* **Walk-Forward OOS Evaluator ([`tools/walk_forward_oos.py`](tools/walk_forward_oos.py))**: Rolling temporal train/test splits comparing baseline vs. Phase 2 improved scoring across all golden fixture symbols. Reports TP1 hit rate, Win Rate, Expectancy $E(R)$, Average R, and Profit Factor with a side-by-side improvement verdict.
* **Chaos Resilience Suite ([`tests/test_chaos_resilience.py`](tests/test_chaos_resilience.py))**: 13 adversarial tests — empty DataFrames, zero-volume holidays, NaN floods, extreme ATR spikes, negative prices, flat-line data, missing OHLCV columns, and combined chaos. Discovered and fixed a `KeyError` in `BreakoutEngine.run()` which now gracefully returns `State.IDLE` on corrupted data.
* **Heartbeat Monitor ([`tools/heartbeat_monitor.py`](tools/heartbeat_monitor.py))**: Inspects telemetry for $N$ consecutive zero-candidate sessions and fires a Telegram alert. Cron-schedulable, exits with code 1 on drought to integrate with CI/monitoring pipelines.
* **Operational Runbook ([`RUNBOOK.md`](RUNBOOK.md))**: Complete operator guide — pre-market startup checklist, daily scan procedure, output reading guide, trade slate workflow, heartbeat monitoring, emergency kill switch procedures, weekly maintenance, log locations, and a troubleshooting table.

---

## ⚙️ Modular Strategy Configurations

Fine-tuned configurations in [`config/`](config/) match specific horizons and asset classes:

| Configuration | Trading Style | Horizon | Key Rules & Characteristics |
| :--- | :--- | :---: | :--- |
| [`config/intraday_1h.yaml`](config/intraday_1h.yaml) | **1–2 Days Optimal Long Model** | 1H Candles | • Long-only (`allow_shorts: false` cuts short drag)<br>• Max $0.15\text{R}$ friction gate<br>• Hourly volume profile normalization<br>• Next-bar open confirmation |
| [`config/options_trading.yaml`](config/options_trading.yaml) | **Directional Options (Calls)** | 1H Candles | • Low IV contraction base ($\le 10\%$ width)<br>• Rising Daily SMA50 slope required<br>• High volume surge ($\ge 1.50\times$) for explosive delta<br>• Fast 3-day hold to mitigate theta decay |
| [`config/swing_1_2d_bidirectional.yaml`](config/swing_1_2d_bidirectional.yaml) | **Active Bi-Directional Swing** | 1H Candles | • Both Long and Short signals enabled<br>• Next-bar confirmation guards against short squeezes<br>• Friction threshold $\le 0.12\text{R}$ |
| [`config/daily_swing.yaml`](config/daily_swing.yaml) | **Minervini Position Swing** | Daily (1D) | • Multi-day position trades on daily candles<br>• Successive contraction waves ($T_1 \to T_2 \to T_3$)<br>• 10-day holding horizon |
| [`config/default.yaml`](config/default.yaml) | **Universal Baseline** | Configurable | Universal baseline with conservative defaults |

---

## 🌐 Supported Markets & Watchlists

The scanner includes pre-built institutional watchlists in [`config/`](config/):

| Market | Watchlist File | Asset Count | Benchmark Index | Sector Relative Strength |
| :--- | :--- | :---: | :--- | :--- |
| **US S&P 500** | [`config/sp_50_watchlist.txt`](config/sp_50_watchlist.txt) | 50 | `SPY` | `XLK`, `XLF`, `XLE`, `XLV`, `XLI`, `XLY`, `XLU` |
| **US Nasdaq 100** | [`config/nasdaq_50_watchlist.txt`](config/nasdaq_50_watchlist.txt) | 50 | `QQQ` | `SMH`, `XLK`, `XLC` |
| **US Tech & Growth** | [`config/top30_watchlist.txt`](config/top30_watchlist.txt) | 30 | `SPY` / `QQQ` | Granular ETF mapping |
| **India NSE Top 50** | [`config/nifty_50_watchlist.txt`](config/nifty_50_watchlist.txt) | 50 | `^NSEI` | `^CNXIT`, `^NSEBANK`, `NIFTY_FIN_SERVICE.NS`, `^CNXAUTO`, etc. |
| **India NSE Top 10** | [`config/nsetop10_watchlist.txt`](config/nsetop10_watchlist.txt) | 10 | `^NSEI` | Sub-sector mapping |
| **Crypto Assets** | [`config/crypto_watchlist.txt`](config/crypto_watchlist.txt) | 20+ | `BTC-USD` | Layer-1, DeFi, High Beta |
| **Europe Top 30** | [`config/europe_top30_watchlist.txt`](config/europe_top30_watchlist.txt) | 30 | `^STOXX50E` | European blue-chips |
| **Japan Top 30** | [`config/japan_top30_watchlist.txt`](config/japan_top30_watchlist.txt) | 30 | `^N225` | Nikkei large-caps |
| **Hong Kong Top 30**| [`config/hk_top30_watchlist.txt`](config/hk_top30_watchlist.txt) | 30 | `^HSI` | Hang Seng constituents |

---

## 🚀 Execution & Command Reference

### 1. Installation & Environment Setup
```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 3. Verify complete test suite (105 tests)
.venv/bin/pytest

# 4. Verify Phase 0 Golden Regression Shield (Zero behavioral drift against BASELINE_V1)
.venv/bin/pytest tests/test_golden_regression.py

# 5. Verify Phase 1 Observability & Noise Map Suite
.venv/bin/pytest tests/test_noise_analyzer.py

# 6. Verify Phase 2 Signal Quality & Noise Reduction Suite
.venv/bin/pytest tests/test_phase2_signal_quality.py

# 7. Verify Phase 3 Confluence & Context Suite
.venv/bin/pytest tests/test_phase3_confluence.py

# 8. Verify Phase 4 Portfolio Guard & Actionable Trade Slate Suite
.venv/bin/pytest tests/test_portfolio_guard.py

# 9. Verify Phase 5 Chaos Resilience Suite
.venv/bin/pytest tests/test_chaos_resilience.py
```

### 1b. Phase 5 Validation Tools
```bash
# Walk-forward OOS comparison (baseline vs. improved scoring on fixtures)
python3 tools/walk_forward_oos.py

# Heartbeat monitor (check for candidate drought)
python3 tools/heartbeat_monitor.py --sessions 10 --threshold 3

# Weekly noise report
python3 tools/generate_noise_report.py

### 2. Run Instant Scans (`scan_now.py`)
Run real-time scans on any watchlist by combining `--config` with `--watchlist`:

```bash
# 1. US Nasdaq 50 - Intraday 1H Swing Model
python3 scan_now.py --config config/intraday_1h.yaml --watchlist config/nasdaq_50_watchlist.txt

# 2. US S&P 50 - Directional Options Trading
python3 scan_now.py --config config/options_trading.yaml --watchlist config/sp_50_watchlist.txt

# 3. India Nifty 50 - Daily Swing Strategy
python3 scan_now.py --config config/daily_swing.yaml --watchlist config/nifty_50_watchlist.txt

# 4. Crypto Watchlist - Bi-directional Hourly Scan
python3 scan_now.py --config config/swing_1_2d_bidirectional.yaml --watchlist config/crypto_watchlist.txt

# Tip: To run without sending Telegram alerts, append --no-tg
python3 scan_now.py --config config/intraday_1h.yaml --watchlist config/top30_watchlist.txt --no-tg
```

### 3. Automated Hourly Daemon & Alerts (`live_scanner.py`)
Eliminate execution latency by running the session-aware background daemon:

```bash
# Start background daemon running at :01 past every hour (candle close)
python3 live_scanner.py --config config/intraday_1h.yaml --watchlists config/nasdaq_50_watchlist.txt --daemon

# Start background daemon with session milestones (NSE 09:35 / US 09:45)
python3 live_scanner.py --config config/intraday_1h.yaml --daemon --sessions

# Run an immediate one-off live scan
python3 live_scanner.py --config config/options_trading.yaml --watchlists config/sp_50_watchlist.txt --now

# Send a test Telegram alert to verify credentials
python3 live_scanner.py --test-alert

# Clear deduplication cache
python3 live_scanner.py --clear-cache

# Monitor live daemon execution logs
tail -f logs/scanner.log
```

#### Push Alert Format Example
```text
🚨 FRESH BREAKOUT DETECTED: NVDA
Entry: 142.50 | Stop: 139.20 | Risk: 2.3% | Grade: A+ [94]
Triggered: Just now (1H candle close)
Direction: ▲ BUY [BREAKOUT] | Current Price: 142.65
Targets: TP1: 145.80 | TP2: 149.10
[Semis RS: +3.8% ↗] | Vol: 2.4x | ⭐ 1D Confirmed
```

### 4. Run Backtests & Experiments (`run_backtest.py`)
Simulate historical multi-symbol performance with zero lookahead:

```bash
# Run 1H backtest over 2 years on US stocks
python3 run_backtest.py --market us --watchlist config/top30_watchlist.txt --interval 1h --period 730d

# Run Daily backtest over 3 years on NSE
python3 run_backtest.py --market nse --watchlist config/nifty_50_watchlist.txt --interval 1d --period 3y

# Run single-variable parameter optimization sweeps across rules
python3 run_phase2_experiments.py --market us --max-symbols 15

# Run out-of-sample temporal walk-forward validation (Phase B)
python3 run_phase_b_validation.py
```
*Outputs are saved to `backtest/` including equity curves (`.csv`), trade logs (`all_trades.csv`), and summary metrics (`metrics.json`, `portfolio_summary.json`).*

### 5. Observability & Noise Map Analysis (`tools/generate_noise_report.py`)
Track forward candidate outcomes (TP1 vs Stop, MFE, MAE) and generate empirical noise attribution reports:

```bash
# Generate weekly noise attribution report across score tiers, box widths, and RVOL
python3 tools/generate_noise_report.py
```
*Outputs are saved to [`reports/noise_report.md`](reports/noise_report.md) and [`reports/noise_report.json`](reports/noise_report.json).*

### 6. Interactive Web Dashboard (`dashboard.py`)
```bash
python3 dashboard.py
```
*Navigate to `http://127.0.0.1:5000` to review real-time setup cards, quant grades, and TradingView charts.*

---

## 📋 Roadmap & Milestone Status Tracker

| Phase & Milestone | Focus Area | Status | Key Deliverables & Files |
| :--- | :--- | :---: | :--- |
| **Phase 1: Data Integrity & Anti-Lookahead** | OHLC Sanity, Confirmed Candle, No-Bfill | ✅ Completed | [`validation.py`](breakout_scanner/providers/validation.py), [`scanner.py`](breakout_scanner/scanner.py) |
| **Phase 2: Signal Precision & Macro Confluence** | 1D SMA50 Slope, Sector RS, Retest Engine | ✅ Completed | [`engine.py`](breakout_scanner/engine.py), [`sectors.py`](breakout_scanner/sectors.py) |
| **Phase 3: 5-Pillar Quantum Scorer** | VDU, RVOL Multiplier, Wave Decay, 0–100 Matrix | ✅ Completed | [`scorer.py`](breakout_scanner/scorer.py), [`models.py`](breakout_scanner/models.py) |
| **Phase 4: Automation & Scheduling Daemon** | Hourly Candle Close (:01), Dedup, Telegram | ✅ Completed | [`scheduler.py`](breakout_scanner/scheduler.py), [`live_scanner.py`](live_scanner.py) |
| **Phase 5: Institutional Backtester** | Bar $i+1$ Open Entry, Parity, Slippage/Fees | ✅ Completed | [`backtester.py`](breakout_scanner/backtester.py), [`run_backtest.py`](run_backtest.py) |
| **Phase 6: Shared Portfolio Simulation** | Cash Allocation, Max Exposure, Net Equity Curves | ✅ Completed | [`portfolio.py`](breakout_scanner/portfolio.py), [`run_phase_b_validation.py`](run_phase_b_validation.py) |
| **Phase 7: Multi-Market Global Expansion** | NSE, US (S&P/Nasdaq), Crypto, Europe, Japan, HK | ✅ Completed | [`config/*_watchlist.txt`](config/) |
| **Phase 8: Consolidation Quality Gate & NaN Guards** | Volatility Bounds, yfinance Retry Backoff | ✅ Completed | [`engine.py`](breakout_scanner/engine.py), [`yfinance_provider.py`](breakout_scanner/providers/yfinance_provider.py) |
| **Phase 9: Interactive UI / TradingView Modals** | Flask Dark UI, TradingView Overlay Popups | ⚪ Planned | [`dashboard.py`](dashboard.py), `templates/` |
| **Phase 10: Broker Connectors & Live Execution** | Live Paper Trading, Alpaca / Dhan / CCXT | ⚪ Planned | [`safeguards.py`](breakout_scanner/safeguards.py), [`paper_broker.py`](breakout_scanner/paper_broker.py) |

---

## 🛡️ Live Execution Safeguards (`safeguards.py`)

Before activating live trading, the engine's built-in circuit breakers enforce non-negotiable risk boundaries:

* **Daily Loss Limit**: Automatically trips the global kill-switch if portfolio floating loss exceeds defined threshold (default: $2.0\%$).
* **Stale Data Guard**: Rejects trade execution if market feed latency exceeds 120 seconds.
* **Liquidity & Spread Check**: Verifies bid-ask spread ($\le 0.5\%$) and minimum volume before routing orders.
* **Global Kill Switch**: Halts all outbound orders immediately upon risk trigger trip.