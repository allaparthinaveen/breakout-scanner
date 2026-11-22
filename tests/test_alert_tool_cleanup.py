"""
Regression tests for alert-tool-cleanup:
  (a) Telegram wording: 'breakout candidate (manual review)', no score-as-quality/edge claims
  (b) Suppress 'SELL, Entry N/A' for long-only profiles and IDLE setups matching open positions
  (c) Mark legacy positions lacking pivot/signal_bar_ts as legacy=True and exclude them
      from failed-breakout exits and R statistics
"""

import json
from datetime import datetime, timedelta, timezone
import pandas as pd
import pytest

from breakout_scanner.models import Setup, State
from breakout_scanner.telegram_formatter import (
    format_instant_alert_html,
    format_scan_report_html,
    format_setup_telegram_line,
)
from breakout_scanner.portfolio_state import PortfolioStateManager
from breakout_scanner.portfolio_guard import PositionAllocation
from breakout_scanner.outcome_tracker import evaluate_setup_outcome_from_df, update_ledger_outcomes


def test_telegram_wording_manual_review_and_no_quality_or_edge_claims():
    s = Setup(
        symbol="AAPL",
        state=State.BUY,
        direction=1,
        entry=200.0,
        current_price=200.5,
        stop=195.0,
        tp1=205.0,
        tp2=210.0,
        risk_pct=2.5,
        bars_since_breakout=0,
        composite_score=92,
        composite_grade="A+",
        rvol=2.1,
    )
    report = format_scan_report_html([s], market="US", allow_shorts=False)
    assert "US BREAKOUT CANDIDATES (MANUAL REVIEW)" in report
    assert "MARKET SIGNALS" not in report
    assert "BREAKOUT CANDIDATE — MANUAL REVIEW" in report
    assert "ENTER NOW" not in report
    assert "🏆" not in report

    alert = format_instant_alert_html(s, timeframe="1H")
    assert "Breakout candidate (manual review)" in alert
    assert "Checklist Ref (non-predictive):" in alert
    assert "<b>Edge:</b>" not in alert
    assert "ENTER NOW" not in alert


def test_suppress_sell_entry_na_for_long_only_and_idle_open_position():
    # 1. Long-only profile suppresses SELL setups and Entry=None setups
    s_buy = Setup(symbol="MSFT", state=State.BUY, direction=1, entry=400.0, current_price=401.0, stop=392.0, risk_pct=2.0, bars_since_breakout=0)
    s_sell = Setup(symbol="SHRT", state=State.SELL, direction=-1, entry=None, current_price=100.0, stop=105.0, risk_pct=5.0, bars_since_breakout=0)

    html_long_only = format_scan_report_html([s_buy, s_sell], market="NSE", allow_shorts=False)
    assert "MSFT" in html_long_only
    assert "SHRT" not in html_long_only
    assert "▼ SELL" not in html_long_only
    assert "Entry: N/A" not in html_long_only

    # 2. Root-cause regression: persisted BUY position whose live scan setup reverted to IDLE (direction=0, entry=None)
    s_idle = Setup(symbol="INFY.NS", state=State.IDLE, direction=0, entry=None, current_price=1820.0)
    open_positions = [
        {
            "symbol": "INFY.NS",
            "direction": 1,
            "status": "OPEN",
            "entry_price": 1800.0,
            "modeled_fill": 1800.9,
            "stop_loss": 1760.0,
            "take_profit_1": 1840.0,
            "take_profit_2": 1880.0,
            "current_price": 1820.0,
            "pnl_pct": 1.06,
        }
    ]
    html_2b = format_scan_report_html([s_idle], market="NSE", open_positions=open_positions, allow_shorts=False)
    assert "INFY.NS" in html_2b
    assert "▲ BUY" in html_2b
    assert "▼ SELL" not in html_2b
    assert "Entry: N/A" not in html_2b

    # 3. Neutral direction=0 line never formats as SELL
    line_neutral = format_setup_telegram_line(s_idle, is_fresh=False)
    assert "▼ SELL" not in line_neutral


def test_legacy_position_flagged_and_excluded_from_failed_breakout_and_r_stats(tmp_path):
    state_file = tmp_path / "portfolio_state.json"
    today_str = datetime.now().date().isoformat()
    # Pre-seed a legacy position missing pivot and signal_bar_ts, plus a normal closed position
    seed_state = {
        "date": today_str,
        "active_positions": {
            "LEGACY_NO_PIVOT": {
                "position_id": "pos_legacy_1",
                "symbol": "LEGACY_NO_PIVOT",
                "direction": 1,
                "status": "OPEN",
                "entry_price": 100.0,
                "modeled_fill": 100.0,
                "stop_loss": 95.0,
                "initial_stop": 95.0,
                "initial_risk": 5.0,
                "take_profit_1": 105.0,
                "take_profit_2": 110.0,
                "shares": 100,
                "bars_held": 1,
                "events": [],
                # Missing 'pivot' and 'signal_bar_ts'
            }
        },
        "closed_positions_today": [
            {
                "symbol": "OLD_CLOSED",
                "direction": 1,
                "status": "CLOSED",
                "realized_r": -1.0,
                # Missing 'pivot' and 'signal_bar_ts' -> legacy=True
            },
            {
                "symbol": "CLEAN_CLOSED",
                "direction": 1,
                "status": "CLOSED",
                "pivot": 100.0,
                "signal_bar_ts": "2026-11-22T10:00:00Z",
                "realized_r": 1.5,
            },
        ],
        "cumulative_daily_risk": 500.0,
        "realized_daily_pnl": 0.0,
        "unrealized_daily_pnl": 0.0,
    }
    state_file.write_text(json.dumps(seed_state), encoding="utf-8")

    mgr = PortfolioStateManager(state_file=str(state_file))
    # Legacy flags assigned on load
    assert mgr.active_positions["LEGACY_NO_PIVOT"]["legacy"] is True
    assert mgr.closed_positions_today[0]["legacy"] is True
    assert mgr.closed_positions_today[1]["legacy"] is False

    # R stats exclude legacy closed position (-1.0R) and keep only CLEAN_CLOSED (+1.5R)
    r_stats = mgr.get_r_stats()
    assert r_stats["count"] == 1
    assert r_stats["avg_r"] == pytest.approx(1.5)
    assert r_stats["legacy_excluded_count"] == 1

    # On a bar that closes below entry/pivot (98 < 100) within 3 bars,
    # legacy position MUST NOT trigger FAILED_BREAKOUT (excluded from failed-breakout exit)
    dates = pd.date_range("2026-11-22 11:00:00", periods=1, freq="1h", tz="UTC")
    df_dip = pd.DataFrame(
        {"Open": [100.5], "High": [101.0], "Low": [97.5], "Close": [98.0], "Volume": [1000]},
        index=dates,
    )
    events = mgr.update_market_data(
        setups=[],
        symbol_dfs={"LEGACY_NO_PIVOT": df_dip},
        cfg={"management": {"use_failed_breakout_exit": True, "failed_breakout_max_bars": 3}},
    )
    assert not any(e.event_type == "FAILED_BREAKOUT" for e in events)
    assert "LEGACY_NO_PIVOT" in mgr.active_positions
    assert mgr.active_positions["LEGACY_NO_PIVOT"]["status"] == "OPEN"

    # Newly recorded positions get signal_bar_ts and legacy=False
    s_new = Setup(symbol="FRESH_CO", state=State.BUY, direction=1, entry=50.0, stop=48.0, breakout_time="2026-11-22T12:00:00Z")
    alloc = PositionAllocation(
        symbol="FRESH_CO", direction=1, entry_price=50.0, stop_loss=48.0,
        take_profit_1=52.0, take_profit_2=54.0, shares=100, dollar_risk=200.0,
        position_value=5000.0, risk_pct_of_equity=0.2, score=85, grade="A", setup=s_new,
    )
    mgr.record_entry(alloc, signal_id="SIG_FRESH", pivot=50.0)
    assert mgr.active_positions["FRESH_CO"]["legacy"] is False
    assert mgr.active_positions["FRESH_CO"]["signal_bar_ts"] == "2026-11-22T12:00:00Z"


def test_outcome_tracker_excludes_legacy_from_failed_breakout_and_r_stats(tmp_path):
    now_utc = datetime.now(timezone.utc)
    dates = pd.date_range(now_utc - timedelta(days=1), periods=4, freq="1h", tz="UTC")
    # Bar 1 fills at ~100.05; Bar 1 closes at 98 (< pivot 100) then Bar 2 hits TP2 (105)
    df = pd.DataFrame(
        {
            "Open": [100.0, 100.0, 101.0, 104.5],
            "High": [100.5, 100.5, 103.0, 105.5],
            "Low": [99.5, 97.5, 100.0, 104.0],
            "Close": [100.0, 98.0, 102.5, 105.0],
            "Volume": [1000] * 4,
        },
        index=dates,
    )
    legacy_rec = {
        "signal_id": "LEG_1",
        "symbol": "LEG",
        "status": "APPROVED",
        "direction": "BUY",
        "legacy": True,
        "timestamp": str(dates[0]),
        "breakout_time": str(dates[0]),
        "price": {"entry": 100.0, "stop": 95.0, "tp1": 102.5, "tp2": 105.0, "pivot": 100.0},
    }
    out = evaluate_setup_outcome_from_df(legacy_rec, df, max_hold_bars=10)
    assert out["legacy"] is True
    assert out["exit_reason"] != "FAILED_BREAKOUT"

    # Ledger R-stats exclude legacy=True records
    ledger_path = str(tmp_path / "ledger.jsonl")
    outcomes_path = str(tmp_path / "outcomes.jsonl")
    clean_rec = {
        "signal_id": "CLN_1",
        "symbol": "CLN",
        "status": "APPROVED",
        "direction": "BUY",
        "timestamp": str(dates[0]),
        "breakout_time": str(dates[0]),
        "price": {"entry": 100.0, "stop": 95.0, "tp1": 102.5, "tp2": 105.0, "pivot": 100.0},
    }
    with open(ledger_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(legacy_rec) + "\n")
        f.write(json.dumps(clean_rec) + "\n")

    class _Provider:
        def candles(self, sym, period="60d", interval="1h"):
            return df

    res = update_ledger_outcomes(ledger_path=ledger_path, outcomes_path=outcomes_path, provider=_Provider())
    assert res["metrics"]["legacy_excluded_count"] == 1
    assert res["metrics"]["approved_count"] == 1
