# Breakout Scanner Architecture

An institutional-grade, deterministic trading engine designed to isolate and execute high-probability structural breakouts across equities and crypto. The system strictly eliminates look-ahead bias and is backed by a robust Risk Engine, Paper Trading simulation, and Production Circuit Breakers.

## 🧠 System Architecture

The engine implements a strict state-machine flow inspired by Pine Script mechanics, migrating setups through rigorous validation checks.

`IDLE` → `CONSOLIDATION` → `ACCEPTANCE_WATCH` → `ACCEPTED_RETEST_WATCH` → `BUY/SELL`

### Implementation Phases (Roadmap)
```mermaid
graph TD
    P1[Phase 1: Python Parity<br/><i>Core logic migration</i>] --> P2
    P2[Phase 2: Historical Backtester<br/><i>O(N²) Zero Look-Ahead Engine</i>] --> P3
    P3[Phase 3: Risk Engine<br/><i>SL, TP, and Sizing</i>] --> P4
    P4[Phase 4: Multi-Symbol Scanner<br/><i>Persistent state memory</i>] --> P5
    P5[Phase 5: Real-Time Feed<br/><i>Websocket adapters</i>] --> P6
    P6[Phase 6: Dashboard & AI Chat<br/><i>UI & Explainability</i>] --> P7
    P7[Phase 7: Walk-Forward Validation<br/><i>OOS cross-sector grid search</i>] --> P8
    P8[Phase 8: Paper Trading<br/><i>Simulated mock execution</i>] --> P9
    P9[Phase 9: Production Safeguards<br/><i>Kill switches & circuit breakers</i>] --> P10
    P10(((Phase 10: Live Execution<br/><i>Broker API integration</i>)))
    
    style P1 fill:#0f172a,stroke:#38bdf8,color:#fff
    style P2 fill:#0f172a,stroke:#38bdf8,color:#fff
    style P3 fill:#0f172a,stroke:#38bdf8,color:#fff
    style P4 fill:#0f172a,stroke:#38bdf8,color:#fff
    style P5 fill:#0f172a,stroke:#38bdf8,color:#fff
    style P6 fill:#0f172a,stroke:#38bdf8,color:#fff
    style P7 fill:#0f172a,stroke:#38bdf8,color:#fff
    style P8 fill:#0f172a,stroke:#38bdf8,color:#fff
    style P9 fill:#0f172a,stroke:#38bdf8,color:#fff
    style P10 fill:#38bdf8,stroke:#0ea5e9,color:#fff,stroke-width:4px
```

### Core Components
1. **The Breakout Engine (`engine.py`)**: The deterministic core. It computes ATR, Momentum, and Volume profiles bar-by-bar to evaluate structures.
2. **The Scanner (`scanner.py`)**: Manages the persistent memory state for N-symbols simultaneously.
3. **The Backtester (`backtester.py`)**: An event-driven historical simulation engine that loops bar-by-bar (O(N²)) to prevent any potential look-ahead bias.
4. **Risk Engine (`risk.py`)**: Calculates position sizing based on available account balance, risk per trade limits, and structural stop-losses.
5. **Paper Broker (`paper_broker.py`)**: A simulated execution environment that manages virtual balances, enforces maximum exposure constraints, and fills limit/stop orders.
6. **Production Safeguards (`safeguards.py`)**: The ultimate gatekeeper implementing Global Kill Switches, Daily Loss Limits, Liquidity checks, and Stale Data detection.
7. **The Dashboard (`dashboard.py`)**: A Flask-based Glassmorphism UI that visualizes real-time states and utilizes a "Prediction Chat" to explain exactly *why* the engine made a specific decision.

---

## ⚙️ Configuration (`config/default.yaml`)
All hyper-parameters are decoupled from the code and live in `default.yaml`. 

* **Structure**: Pivot lengths and trend validation.
* **Consolidation**: Maximum range percentages and time constraints.
* **Breakout**: Minimum push strength and volume anomalies required to trigger.
* **Risk**: Maximum daily drawdown, account exposure, and R:R ratios.

---

## 🚀 Execution Guide

### 1. Installation
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the Local Dashboard
```bash
python3 dashboard.py
```
*Navigate to `http://127.0.0.1:5000` to view the UI.*

### 3. Run a Backtest
```bash
python3 run_backtest.py
```
*Results will automatically populate in the `/backtest` directory, generating equity curves, complete trade logs, and high-level `metrics.json`.*

### 4. Command Line Scanner
To run a one-off historical scan:
```bash
python3 -m breakout_scanner.cli scan --watchlist config/watchlist.txt --period 60d --interval 1h
```

---

## 🛡️ Safeguards & Phase 10 (Live Execution)
Before attaching live brokerage API keys (e.g., Alpaca, CCXT) for Phase 10, ensure that the `ProductionSafeguards` are correctly calibrated to your timezone and liquidity preferences. The engine is programmed to halt completely if daily loss limits are breached or data latency exceeds 120 seconds.
