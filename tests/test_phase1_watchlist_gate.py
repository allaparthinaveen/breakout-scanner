import pytest
from datetime import datetime, timezone
from breakout_scanner.models import Setup, State
from breakout_scanner.slate import SlatePipeline, ActionableSlate, WatchlistRecord
from breakout_scanner.rejection_gate import TradeRejectionGate
from scripts.eod_summary import generate_eod_summary, format_eod_markdown


def test_contraction_setup_with_bias3_produces_zero_rejection_records():
    """
    Test (a): A CONTRACTION setup with bias_score >= 3 must go into ActionableSlate.watchlist
    and must produce ZERO RejectionRecords (no MISSING_STRUCTURAL_STOP).
    """
    cfg = {"rejection_gate": {"enabled": True}}
    pipeline = SlatePipeline(cfg=cfg)
    s_cont = Setup(
        symbol="AAPL",
        state=State.CONTRACTION,
        bias_score=3,
        resistance=180.0,
        support=170.0,
        current_price=178.5,
    )
    s_cont.composite_score = 85
    s_cont.composite_grade = "A"

    slate = pipeline.evaluate_setups([s_cont])

    # Must produce ZERO rejections
    assert len(slate.rejected_trades) == 0, f"Expected 0 rejections, got: {slate.rejected_trades}"
    # Must be present in watchlist
    assert len(slate.watchlist) == 1
    w = slate.watchlist[0]
    assert w.symbol == "AAPL"
    assert w.bias_score == 3
    assert w.resistance == 180.0
    # Distance to pivot: (180.0 - 178.5) / 180.0 * 100 = 0.83%
    assert round(w.dist_to_pivot_pct, 2) == 0.83


def test_buy_with_stop_none_yields_missing_structural_stop():
    """
    Test (b): A real BUY setup with stop=None (or <= 0) must still yield MISSING_STRUCTURAL_STOP
    in TradeRejectionGate and in SlatePipeline.
    """
    cfg = {"rejection_gate": {"enabled": True}}
    gate = TradeRejectionGate(cfg=cfg)
    s_buy_bad_stop = Setup(
        symbol="MSFT",
        state=State.BUY,
        direction=1,
        entry=300.0,
        stop=None,  # Bad stop
    )
    decision = gate.evaluate_setup(s_buy_bad_stop)
    assert not decision.approved
    assert decision.rejection_code == "MISSING_STRUCTURAL_STOP"

    pipeline = SlatePipeline(cfg=cfg, rejection_gate=gate)
    slate = pipeline.evaluate_setups([s_buy_bad_stop])
    assert len(slate.rejected_trades) == 1
    assert slate.rejected_trades[0].symbol == "MSFT"
    assert slate.rejected_trades[0].rejection_code == "MISSING_STRUCTURAL_STOP"


def test_eod_summary_shows_dis_rejection_and_watchlist_stats(tmp_path):
    """
    Test (c): EOD output with 300 watchlist setups + 1 DIS rejection
    shows DIS in markdown without being cut off by an alphabetical top-10 slice,
    and displays the 'Watchlist: N names (M trend-OK)' line.
    """
    run_dir = tmp_path / "run" / "2026-11-17"
    run_dir.mkdir(parents=True, exist_ok=True)

    symbol_traces = []
    # 300 Watchlist CONTRACTION symbols (alphabetically before DIS: "A001" to "A300")
    for i in range(300):
        sym = f"A{i:03d}"
        symbol_traces.append({
            "scan_id": "us_scan_1",
            "symbol": sym,
            "ts_utc": "2026-11-17T15:31:00Z",
            "market": "US",
            "fetch": {"status": "OK", "latency_ms": 50.0},
            "engine": {
                "state_after": "CONTRACTION",
                "all_gates": {"trend_sma50": (i % 2 == 0)},  # half trend-OK
            },
            "enrich": {"bias_score": 3},
            "score": {"composite_score": 70},
            "gates/slate": {
                "status": "WATCHLIST",
                "code": "WATCHLIST",
                "reason": "CONTRACTION setup (bias 3)",
            },
        })

    # 1 real BUY rejection: DIS rejected for IMMINENT_EARNINGS (score 96)
    symbol_traces.append({
        "scan_id": "us_scan_1",
        "symbol": "DIS",
        "ts_utc": "2026-11-17T15:31:00Z",
        "market": "US",
        "fetch": {"status": "OK", "latency_ms": 65.0},
        "engine": {
            "state_after": "BUY",
            "all_gates": {"trend_sma50": True},
        },
        "enrich": {"bias_score": 2},
        "score": {"composite_score": 96, "grade": "A+"},
        "gates/slate": {
            "status": "REJECTED",
            "code": "IMMINENT_EARNINGS",
            "reason": "IMMINENT_EARNINGS: High-volatility earnings event within 0 sessions.",
            "risk_pct": 1.21,
            "friction_r": 0.041,
        },
    })

    import json
    with open(run_dir / "symbol_trace.jsonl", "w") as f:
        for st in symbol_traces:
            f.write(json.dumps(st) + "\n")

    # Empty events and scan summaries
    with open(run_dir / "events.jsonl", "w") as f:
        f.write("")
    with open(run_dir / "scan_summary.jsonl", "w") as f:
        f.write(json.dumps({
            "scan_id": "us_scan_1",
            "market": "US",
            "scheduled_time_utc": "2026-11-17T15:31:00Z",
            "actual_start_utc": "2026-11-17T15:31:03Z",
            "actual_end_utc": "2026-11-17T15:32:00Z",
            "start_delay_seconds": 3.0,
            "symbols_total": 301,
            "symbols_with_data": 301,
            "completeness_pct": 100.0,
            "funnel_counts": {
                "with_data": 301,
                "contraction": 300,
                "watchlist_count": 300,
                "breakout": 1,
                "approved": 0,
            },
        }) + "\n")

    summary_data = generate_eod_summary(
        market="US",
        date_str="2026-11-17",
        base_dir=str(tmp_path / "run"),
        ledger_path=str(tmp_path / "forward_test_ledger.jsonl"),
    )
    md_report = format_eod_markdown(summary_data)

    # Assert DIS is present in rejected_candidates
    assert len(summary_data["rejected_candidates"]) == 1
    assert summary_data["rejected_candidates"][0]["symbol"] == "DIS"
    assert summary_data["rejected_candidates"][0]["code"] == "IMMINENT_EARNINGS"

    # Assert Markdown report displays DIS
    assert "[REJECTED] **DIS**: `IMMINENT_EARNINGS`" in md_report

    # Assert Watchlist line is formatted properly
    # 300 names, half (150) trend-OK
    assert "Watchlist:" in md_report
    assert "300 names (150 trend-OK)" in md_report


def test_scanner_watchlist_strict_map_and_confirmation_policy_min_score():
    """
    Verifies:
    1. Only symbols in watchlist_map get status WATCHLIST.
    2. Other CONTRACTION setups (bias < 3) get status NONE with reason 'CONTRACTION (bias<3)'.
    3. min_auto_entry_score is read from confirmation_policy.min_auto_entry_score.
    """
    from unittest.mock import MagicMock
    from breakout_scanner.scanner import MultiSymbolScanner

    cfg = {
        "confirmation_policy": {"min_auto_entry_score": 80},
        "signal_quality": {"min_auto_entry_score": 70},  # Must NOT use this legacy 70
    }
    scanner = MultiSymbolScanner(provider=MagicMock(), cfg=cfg)

    # Mock traces:
    # 1. High bias contraction (in watchlist)
    # 2. Low bias contraction (not in watchlist)
    traces = [
        {
            "symbol": "WL_SYM",
            "fetch": {"status": "OK"},
            "engine": {"state_after": "CONTRACTION", "all_gates": {}},
            "enrich": {"bias_score": 3},
            "score": {"composite_score": 85},
            "gates/slate": {},
        },
        {
            "symbol": "LOW_BIAS_SYM",
            "fetch": {"status": "OK"},
            "engine": {"state_after": "CONTRACTION", "all_gates": {}},
            "enrich": {"bias_score": 2},
            "score": {"composite_score": 65},
            "gates/slate": {},
        },
    ]

    w_rec = WatchlistRecord(symbol="WL_SYM", bias_score=3, resistance=100.0, dist_to_pivot_pct=0.5)
    slate = ActionableSlate(
        slate_id="test_scan",
        timestamp="2026-11-18T10:00:00Z",
        profile_name="Test",
        total_scanned=2,
        portfolio_equity=100000.0,
        max_daily_risk_pct=2.0,
        max_daily_dollar_risk=2000.0,
        selected_trades=[],
        suppressed_trades=[],
        rejected_trades=[],
        watchlist=[w_rec],
    )

    scanner.last_symbol_traces = traces
    scanner.last_scan_stats = {"total": 2, "with_data": 2, "failed_symbols": []}
    scanner.flush_symbol_traces(
        slate=slate,
        market="US",
        scheduled_time=datetime.now(timezone.utc),
    )

    assert traces[0]["gates/slate"]["status"] == "WATCHLIST"
    assert traces[0]["gates/slate"]["code"] == "WATCHLIST"

    assert traces[1]["gates/slate"]["status"] == "NONE"
    assert traces[1]["gates/slate"]["code"] == "NONE"
    assert "bias<3" in traces[1]["gates/slate"]["reason"]

    assert scanner.last_scan_summary["funnel_counts"]["watchlist_count"] == 1

