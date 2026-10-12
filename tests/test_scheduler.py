import os
import pytest
from unittest.mock import MagicMock
from breakout_scanner.models import Setup, State
from breakout_scanner.scheduler import (
    CandleAlertDeduplicator,
    format_instant_alert,
    format_price,
    SessionScheduler
)

def test_format_price():
    assert format_price(None) == "N/A"
    assert format_price(541.73) == "541.73"
    assert format_price(1.2815) == "1.2815"
    assert format_price(1.2650) == "1.2650"
    assert format_price(0.0993) == "0.0993"


def test_deduplicator_lifecycle(tmp_path):
    cache_file = str(tmp_path / "test_dedup.json")
    dedup = CandleAlertDeduplicator(cache_file)
    
    sig = dedup.build_signature("DOT-USD", "BREAKOUT", "2026-10-12 14:00:00")
    assert sig == "DOT-USD:BREAKOUT:2026-10-12_14:00:00"
    assert dedup.is_duplicate(sig) is False
    
    # Record alert
    dedup.record_alert(sig, {"symbol": "DOT-USD", "entry": 1.2815})
    assert dedup.is_duplicate(sig) is True
    
    # Reload from disk in a fresh instance
    dedup2 = CandleAlertDeduplicator(cache_file)
    assert dedup2.is_duplicate(sig) is True
    
    # Clear
    dedup2.clear()
    assert dedup2.is_duplicate(sig) is False


def test_format_instant_alert_buy():
    setup = Setup(
        symbol="DOT-USD",
        state=State.BUY,
        direction=1,
        entry=1.2815,
        stop=1.2650,
        current_price=1.2818,
        risk_pct=1.2,
        composite_grade="B",
        composite_score=79,
        tp1=1.3012,
        tp2=1.3209,
        sector_name="Crypto",
        sector_rs_pct=1.4,
        rs_trend="↗",
        rvol=2.1,
        htf_confirmed=True,
        breakout_type="BREAKOUT"
    )

    alert_text = format_instant_alert(setup, timeframe="1H")
    assert "🚨 <b>FRESH BREAKOUT DETECTED: DOT-USD</b>" in alert_text
    assert "Entry:" in alert_text and "1.2815" in alert_text
    assert "Stop:" in alert_text and "1.2650" in alert_text
    assert "Risk:" in alert_text and "1.2%" in alert_text
    assert "Grade:" in alert_text and "B [79]" in alert_text
    assert "Triggered:" in alert_text and "(1H candle close)" in alert_text
    assert "▲ BUY [BREAKOUT]" in alert_text
    assert "Targets:" in alert_text and "TP1:" in alert_text and "1.3012" in alert_text
    assert "Crypto RS: +1.4% ↗" in alert_text


def test_format_instant_alert_sell():
    setup = Setup(
        symbol="INTC",
        state=State.SELL,
        direction=-1,
        entry=105.11,
        stop=107.25,
        current_price=104.90,
        risk_pct=2.0,
        composite_grade="C",
        composite_score=55,
        tp1=102.84,
        tp2=100.57,
        sector_name="Semis",
        sector_rs_pct=-2.5,
        rs_trend="↘",
        rvol=1.8,
        htf_confirmed=True,
        breakout_type="BREAKOUT"
    )

    alert_text = format_instant_alert(setup, timeframe="1H")
    assert "🚨 <b>FRESH BREAKDOWN DETECTED: INTC</b>" in alert_text
    assert "Entry:" in alert_text and "105.11" in alert_text
    assert "Stop:" in alert_text and "107.25" in alert_text
    assert "▼ SELL [BREAKDOWN]" in alert_text


def test_format_instant_alert_retest():
    setup = Setup(
        symbol="TSM",
        state=State.BUY,
        direction=1,
        entry=451.42,
        stop=445.00,
        current_price=452.10,
        risk_pct=1.4,
        composite_grade="A",
        composite_score=84,
        tp1=458.00,
        tp2=465.00,
        retest_confirmed=True,
        breakout_type="RETEST_HOLD"
    )

    alert_text = format_instant_alert(setup, timeframe="1H")
    assert "🔄 <b>FRESH RETEST HOLD DETECTED: TSM</b>" in alert_text
    assert "▲ BUY [RETEST HOLD]" in alert_text


def test_format_scan_report_html():
    from breakout_scanner.telegram_formatter import format_scan_report_html
    s_fresh = Setup(symbol="MSFT", state=State.BUY, direction=1, entry=541.73, current_price=540.68, stop=536.49, tp1=546.97, tp2=552.21, bars_since_breakout=0, composite_grade="A+", composite_score=90, risk_pct=0.97)
    s_progress = Setup(symbol="NVDA", state=State.SELL, direction=-1, entry=229.32, current_price=226.49, stop=229.09, tp1=227.02, tp2=224.72, bars_since_breakout=5, composite_grade="B", composite_score=77, risk_pct=1.0)
    s_base = Setup(symbol="AAPL", state=State.CONTRACTION, bias_score=3, resistance=341.57, support=330.70, base_width_pct=3.2, decay_ratio=0.6, tightening_confirmed=True, composite_grade="A", composite_score=81)

    html = format_scan_report_html([s_fresh, s_progress, s_base])
    assert "SECTION 2A: FRESH ENTRIES" in html
    assert "MSFT" in html
    assert "[ENTER NOW — FRESH]" in html
    assert "SECTION 2B: IN PROGRESS" in html
    assert "NVDA" in html
    assert "[TP1 HIT:" in html
    assert "SECTION 1: VCP BASES" in html
    assert "AAPL" in html


def test_scheduler_scan_and_deduplication(tmp_path):
    dedup_path = str(tmp_path / "dedup.json")
    log_path = str(tmp_path / "test.log")

    scheduler = SessionScheduler(
        cfg_path="config/default.yaml",
        watchlist_files=[],
        dedup_file=dedup_path,
        log_file=log_path
    )

    # Mock watchlist symbols
    scheduler.load_symbols = MagicMock(return_value=["DOT-USD", "NVDA", "STALE-COIN"])

    # Mock Telegram dispatch
    scheduler.send_telegram_alert = MagicMock(return_value=True)

    # Setup 1: Fresh Breakout on latest candle (bars_since_breakout=0, fresh entry)
    s1 = Setup(
        symbol="DOT-USD",
        state=State.BUY,
        direction=1,
        entry=1.2815,
        stop=1.2650,
        current_price=1.2820,
        risk_pct=1.2,
        composite_grade="B",
        composite_score=79,
        tp1=1.3012,
        tp2=1.3209,
        bars_since_breakout=0,
        breakout_time="2026-10-12 14:00:00"
    )

    # Setup 2: In-Progress / TP1 already hit (is_fresh_entry=False)
    s2 = Setup(
        symbol="NVDA",
        state=State.SELL,
        direction=-1,
        entry=229.32,
        stop=229.09,
        current_price=226.49,
        risk_pct=1.0,
        composite_grade="B",
        composite_score=77,
        tp1=227.02,
        tp2=224.72,
        bars_since_breakout=0,
        breakout_time="2026-10-12 14:00:00"
    )

    # Setup 3: Stale Breakout from 10 candles ago (bars_since_breakout=10)
    s3 = Setup(
        symbol="STALE-COIN",
        state=State.BUY,
        direction=1,
        entry=10.0,
        stop=9.0,
        current_price=10.05,
        risk_pct=10.0,
        composite_grade="C",
        composite_score=50,
        tp1=12.0,
        tp2=14.0,
        bars_since_breakout=10,
        breakout_time="2026-10-10 10:00:00"
    )

    scheduler.scanner.scan = MagicMock(return_value=[s1, s2, s3])

    # Run 1: Should alert DOT-USD only (1 alert sent)
    res1 = scheduler.scan_and_alert()
    assert res1["alerts_sent"] == 1
    assert scheduler.send_telegram_alert.call_count == 1

    # Run 2 on same candle: Deduplication should prevent duplicate alert
    res2 = scheduler.scan_and_alert()
    assert res2["alerts_sent"] == 0
    assert res2["duplicates_skipped"] == 1
    # Total Telegram calls remains 1!
    assert scheduler.send_telegram_alert.call_count == 1
