# 🛡️ Breakout Scanner — Operational Runbook

> **Version**: BASELINE_V1 + Phases 0–5  
> **Audience**: Operator / Trader running live scans  
> **Golden Rule**: Whatever is working today must continue to work tomorrow. Never break the engine.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Pre-Market Startup Checklist](#2-pre-market-startup-checklist)
3. [Daily Scan Procedure](#3-daily-scan-procedure)
4. [Reading the Scan Output](#4-reading-the-scan-output)
5. [Portfolio Guard Workflow (Phase 4)](#5-portfolio-guard-workflow-phase-4)
6. [Monitoring & Heartbeat Alerts (Phase 5)](#6-monitoring--heartbeat-alerts-phase-5)
7. [Emergency Procedures & Kill Switch](#7-emergency-procedures--kill-switch)
8. [Weekly Maintenance](#8-weekly-maintenance)
9. [Telemetry & Log Locations](#9-telemetry--log-locations)
10. [Troubleshooting Guide](#10-troubleshooting-guide)
11. [Configuration Reference](#11-configuration-reference)
12. [Test Suite Reference](#12-test-suite-reference)

---

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                   BREAKOUT SCANNER STACK                        │
├─────────────────┬───────────────────────────┬───────────────────┤
│  Data Layer     │  Detection Layer          │  Output Layer     │
│  yfinance       │  VCP Engine               │  CLI Report       │
│  Golden Fixture │  Signal Quality (Ph2)     │  Telegram Alerts  │
│  Telemetry      │  Confluence (Ph3)         │  Trade Slate (Ph4)│
└─────────────────┴───────────────────────────┴───────────────────┘
```

**Execution flow per scan**:
1. `scan_now.py` loads config + watchlist
2. `MultiSymbolScanner.scan()` fetches OHLCV via yfinance for each symbol
3. `BreakoutEngine.run()` classifies each symbol into `IDLE / CONTRACTION / BUY / SELL / FAILED`
4. `calculate_composite_score()` scores each setup (0–100 pts, Grade A+ to C)
5. `enrich_signal_quality()` and `enrich_confluence_and_context()` add Phase 2/3 data (if enabled)
6. Scanner outputs Section 1 (bases), Section 2A (fresh entries), Section 2B (in progress)
7. Phase 4 Trade Slate renders if `--slate` flag or `portfolio_guard.enabled: true`
8. Telegram message dispatched (unless `--no-tg`)
9. Telemetry logged to `data/telemetry/`

---

## 2. Pre-Market Startup Checklist

Run these checks **before the market opens** each trading day:

```bash
# 1. Verify the environment
source .venv/bin/activate
python3 -c "import breakout_scanner; print('✅ Module OK')"

# 2. Verify .env credentials
cat .env | grep -E "TG_BOT|TG_CHAT"
# Expected: TG_BOT_TOKEN=... TG_CHAT_ID=...

# 3. Run full regression shield (must pass 100%)
.venv/bin/pytest tests/test_golden_regression.py -q
# Expected: 3 passed in < 15s

# 4. Quick smoke test (all 100+ tests)
.venv/bin/pytest -q --tb=no
# Expected: All passed

# 5. Check internet & yfinance connectivity
python3 -c "import yfinance as yf; d=yf.download('AAPL',period='2d',interval='1h',progress=False); print('✅ yfinance OK', len(d), 'bars')"
```

---

## 3. Daily Scan Procedure

### Standard Intraday (US Markets — 1H)

```bash
# US Nasdaq 50 — Primary scan
python3 scan_now.py \
  --config config/intraday_1h.yaml \
  --watchlist config/nasdaq_50_watchlist.txt

# US S&P 50 — Options plays
python3 scan_now.py \
  --config config/options_trading.yaml \
  --watchlist config/sp_50_watchlist.txt

# With trade slate (Phase 4 position sizing)
python3 scan_now.py \
  --config config/intraday_1h.yaml \
  --watchlist config/nasdaq_50_watchlist.txt \
  --slate --no-tg
```

### India NSE (Pre-Market 8:45 AM IST)

```bash
python3 scan_now.py \
  --config config/daily_swing.yaml \
  --watchlist config/nifty_50_watchlist.txt
```

### Automated Scheduler (keep running)

```bash
# Start scheduled daemon (runs every hour during market hours)
python3 -m breakout_scanner.scheduler &

# Check scheduler logs
tail -f data/telemetry/metrics_history.jsonl | python3 -m json.tool
```

---

## 4. Reading the Scan Output

### Section 1 — VCP Bases (Coiling, Not Yet Breaking Out)

```
• NVDA      Resistance: 140.2 | Support: 132.1 | Width: 6.2% | 🗜️ Coil: 0.6x | 💧 VDU: 0.5x
            [Semis RS: +5.2% ↗] ⭐⭐ Multi-TF (1D+4H) | 🏆 Grade: A [85] | Bias: Strong Long (3/3)
```

| Field | Meaning |
|---|---|
| `Width: X.X%` | Base width. **< 4%** = ultra-tight, **4–7%** = standard, **> 9%** = loose |
| `Coil: X.Xx` | Decay ratio. **< 0.50** = institutional spring, **0.50–0.70** = good, **> 0.70** = weak |
| `VDU: X.Xx` | Volume dry-up ratio vs. base average. **< 0.60** = excellent supply exhaustion |
| `RS: +X.X%` | Sector relative strength delta vs. benchmark. **> +3%** = leading sector |
| `⭐ 1D` / `⭐⭐ Multi-TF` | Daily / 4H HTF trend confirmation |
| `Grade: A+ [95]` | Composite score (0–100) and tier |
| `⚠️ EARNINGS RISK` | Earnings within 3 trading days — reduce size or avoid |

### Section 2A — Fresh Actionable Entries

```
• AAPL     ▲ BUY [🚀 BREAKOUT] | CP: 185.4 | Entry: 183.2 | Stop: 179.1 | TP1: 187.3 | TP2: 191.4
            Risk: 2.2% | [Tech] ⭐ 1D | 🏆 A [88] | Bias: Strong Long (3/3)
```

**Trade checklist before entering**:
- [ ] Grade ≥ A (score ≥ 80)
- [ ] Sector RS positive
- [ ] Risk ≤ 3.0% (structural stop)
- [ ] Not within 3 days of earnings
- [ ] RVOL ≥ 1.5× on breakout candle
- [ ] Market regime not in `HIGH_VOLATILITY_SELLOFF`

### Section 3 — Trade Slate (Portfolio Guard)

```
• BUY 200 shares NVDA @ $140.20 (Value: $28,040 / 28.0% equity) [Semis]
  Stop: $135.00 | TP1: $145.40 (+1.0R) | TP2: $150.60 (+2.0R)
  Risk: $1,040.00 (1.04% equity) | Grade: A+ [94]

⚠️ SUPPRESSED: AMD — CORRELATION_STACKING (Semis capped at 1 trade; winner: NVDA [94])
```

---

## 5. Portfolio Guard Workflow (Phase 4)

### Activation

Enable in config for automatic Trade Slate rendering:

```yaml
# config/default.yaml (or your custom config)
portfolio_guard:
  enabled: true              # Renders Section 3 after every scan
  account_equity: 100000.0
  risk_per_trade_pct: 1.0
  max_daily_risk_pct: 2.0
  max_active_trades: 4
  max_sector_allocation_pct: 25.0
  max_trades_per_sector: 1
```

Or use the `--slate` flag for on-demand sizing without enabling globally:

```bash
python3 scan_now.py --config config/intraday_1h.yaml \
    --watchlist config/nasdaq_50_watchlist.txt --slate --no-tg
```

### Daily Risk Tracking

Each scan appends to `data/telemetry/metrics_history.jsonl`. Track today's allocated risk:

```bash
# Show today's scan metrics
tail -5 data/telemetry/metrics_history.jsonl | python3 -m json.tool

# Show candidate events
tail -20 data/telemetry/candidate_events.jsonl | python3 -m json.tool
```

---

## 6. Monitoring & Heartbeat Alerts (Phase 5)

### Manual Heartbeat Check

```bash
# Check last 10 sessions for candidate drought
python3 tools/heartbeat_monitor.py --sessions 10 --threshold 3

# Check and fire Telegram alert if drought detected
python3 tools/heartbeat_monitor.py --sessions 10 --threshold 3 --notify

# Machine-readable JSON output
python3 tools/heartbeat_monitor.py --json
```

**Exit codes**: `0` = healthy, `1` = drought alert triggered.

### Scheduled Heartbeat (Cron)

Add to crontab to check every 4 hours on trading days:

```cron
# Monitor for candidate drought (Mon–Fri, every 4 hours during trading day)
0 10,14,18,22 * * 1-5 cd /path/to/breakout-scanner && .venv/bin/python3 tools/heartbeat_monitor.py --notify 2>&1 >> data/logs/heartbeat.log
```

### Walk-Forward OOS Validation

Run weekly to verify scoring improvements hold out-of-sample:

```bash
# Compare baseline vs. Phase 2 improved scoring on all fixtures
python3 tools/walk_forward_oos.py \
  --train-bars 300 --test-bars 100 --step-bars 80 \
  --output reports/walkforward_results.json
```

### Noise Report

Run weekly to identify top reject causes:

```bash
python3 tools/generate_noise_report.py
# Output: reports/noise_report.md + reports/noise_report.json
```

---

## 7. Emergency Procedures & Kill Switch

### Market Circuit Breaker (Rapid Losses)

If the portfolio exceeds $2,000 daily risk threshold or a significant loss occurs:

```bash
# 1. Kill scheduled scans immediately
pkill -f "breakout_scanner.scheduler"
pkill -f "scan_now.py"

# 2. Verify no running processes
ps aux | grep "scan_now\|scheduler"
```

The `ProductionSafeguards.engage_kill_switch()` is automatically tripped when `max_daily_loss_pct` is breached if safeguards are integrated.

### Data Staleness

If market data appears stale (more than 2 minutes old during market hours):

```bash
# Force fresh download (bypass any caching)
python3 scan_now.py --config config/intraday_1h.yaml --period 2d --no-tg
```

### Telegram Bot Not Responding

```bash
# Test Telegram connectivity directly
python3 -c "
import os, requests
from dotenv import load_dotenv
load_dotenv()
token = os.getenv('TG_BOT_TOKEN')
chat_id = os.getenv('TG_CHAT_ID')
r = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
    json={'chat_id': chat_id, 'text': '🧪 Connectivity test OK'})
print(r.status_code, r.text[:100])
"
```

---

## 8. Weekly Maintenance

Every **Sunday** before the new trading week:

```bash
# 1. Full regression suite
.venv/bin/pytest -q --tb=short
# Expected: 100+ tests passing

# 2. Golden Regression Shield
.venv/bin/pytest tests/test_golden_regression.py -v

# 3. Walk-forward OOS evaluation
python3 tools/walk_forward_oos.py \
  --output reports/walkforward_$(date +%Y%m%d).json

# 4. Noise report
python3 tools/generate_noise_report.py

# 5. Review and rotate telemetry (optional — files grow over time)
wc -l data/telemetry/metrics_history.jsonl data/telemetry/reject_events.jsonl

# 6. Git checkpoint
git add -A && git commit -m "chore: weekly checkpoint $(date +%Y-%m-%d)"
```

---

## 9. Telemetry & Log Locations

| File | Contents | Updated |
|---|---|---|
| `data/telemetry/metrics_history.jsonl` | Per-scan: timestamp, symbol counts, grade distribution, avg score | Every scan |
| `data/telemetry/metrics_daily.csv` | Daily aggregate CSV | Every scan |
| `data/telemetry/reject_events.jsonl` | Per-symbol reject reasons with score/state | Every scan |
| `data/telemetry/candidate_events.jsonl` | Per-candidate setup snapshots (grade, score, RS) | Every scan |
| `reports/noise_report.md` | Noise attribution report (win rates by bucket) | Weekly |
| `reports/noise_report.json` | Machine-readable noise report | Weekly |
| `reports/walkforward_*.json` | OOS evaluation results by date | Weekly |

---

## 10. Troubleshooting Guide

| Symptom | Likely Cause | Fix |
|---|---|---|
| `0 candidates` every scan | Overly strict config or data gap | Run `--no-tg` to inspect output; check `reject_events.jsonl` |
| `AttributeError: 'NoneType'` in scanner | Symbol returned empty DataFrame from yfinance | Usually transient; retry after 30s |
| `ModuleNotFoundError: breakout_scanner` | Running outside venv or from wrong dir | `source .venv/bin/activate` from repo root |
| Telegram: `401 Unauthorized` | Invalid `TG_BOT_TOKEN` in `.env` | Re-enter bot token from BotFather |
| Golden regression tests fail | Code change broke baseline behavior | Revert last change, run `pytest tests/test_golden_regression.py` |
| Heartbeat alert: candidate drought | Scanner failed silently or market closed | Check `metrics_history.jsonl`; verify market hours |
| Chaos tests fail | Engine raises on adversarial data | Check `tests/test_chaos_resilience.py` failure; fix in engine |
| Fixture files not found | Phase 0 fixtures not initialized | Run `python3 -c "from tests.test_golden_regression import *"` |

---

## 11. Configuration Reference

### Feature Flag Summary

| Feature | Config Key | Default | Phase |
|---|---|---|---|
| Trend filter (SMA50) | `trend.use_trend_filter` | `true` | Baseline |
| Volume breakout gate | `volume.use_breakout_volume` | `true` | Baseline |
| Next-bar confirmation | `breakout.use_open_confirmation` | `false` | Baseline |
| Signal quality modifiers | `signal_quality.enabled` | `false` | Phase 2 |
| 4H confluence badge | `confluence_context.enabled` | `false` | Phase 3 |
| Market regime filter | `confluence_context.market_regime_weight` | `0.0` | Phase 3 |
| Earnings penalty | `confluence_context.earnings_penalty_weight` | `0.0` | Phase 3 |
| Portfolio guard | `portfolio_guard.enabled` | `false` | Phase 4 |
| Correlation stacking guard | `portfolio_guard.max_trades_per_sector` | `1` | Phase 4 |

### Activating Phase 2 Signal Quality (When Ready)

After noise report confirms ≥ 15% false-breakout reduction:

```yaml
signal_quality:
  enabled: true
  close_location_weight: 1.0       # +5/-5 pts candle close location
  vdu_slope_weight: 1.0            # +5 pts volume dry-up slope
  trigger_tightness_weight: 1.0    # +5 pts ultra-tight coil
  failed_breakout_penalty_weight: 1.0  # -5/-10 pts base piercing
```

### Activating Phase 3 Confluence (When Ready)

```yaml
confluence_context:
  enabled: true
  four_hour_bonus_weight: 1.0      # +5 pts 4H alignment
  regime_penalty_weight: 1.0       # -8 pts high-vol selloff
  earnings_penalty_weight: 1.0     # -5 pts earnings risk
```

---

## 12. Test Suite Reference

```bash
# Run all tests (must always be 100% green)
.venv/bin/pytest

# By phase
.venv/bin/pytest tests/test_golden_regression.py       # Phase 0 shield (3 tests)
.venv/bin/pytest tests/test_noise_analyzer.py          # Phase 1 observability (4 tests)
.venv/bin/pytest tests/test_phase2_signal_quality.py   # Phase 2 quality (8 tests)
.venv/bin/pytest tests/test_phase3_confluence.py       # Phase 3 confluence (5 tests)
.venv/bin/pytest tests/test_portfolio_guard.py         # Phase 4 guard (8 tests)
.venv/bin/pytest tests/test_chaos_resilience.py        # Phase 5 chaos (13 tests)

# Quick health check (no output, exit code only)
.venv/bin/pytest -q --tb=no && echo "✅ ALL PASSING"
```

### Minimum Acceptance Criteria (Before Any Production Scan)

1. `tests/test_golden_regression.py` — **3/3 passing** (non-negotiable)
2. Full test suite — **100% passing**
3. `python3 tools/heartbeat_monitor.py` — **status: HEALTHY**
4. `.env` — `TG_BOT_TOKEN` and `TG_CHAT_ID` present

---

*Built to institutional standards. Treat it like production capital.*  
*The scanner is a signal generator — never blindly trade every alert.*
