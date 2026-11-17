import json
import pytest
import pandas as pd
import numpy as np
from breakout_scanner.models import Setup, State
from breakout_scanner.audit_ledger import record_forward_test_ledger
from breakout_scanner.slate import ActionableSlate, RejectionRecord
from breakout_scanner.rejection_gate import TradeRejectionGate
from breakout_scanner.outcome_tracker import evaluate_setup_outcome_from_df, update_ledger_outcomes


def test_late_chase_only_rejection_writes_shadow_row(tmp_path):
    """
    ITEM 3 Regression Test 1:
    When a setup is rejected ONLY for LATE_CHASE (all other gates pass),
    it writes a forward_test_ledger row with bucket='LATE_CHASE_SHADOW',
    populating pivot, signal_price, extension_pct, tolerance_pct, atr, score, grade, stop, tp1, tp2.
    """
    ledger_file = str(tmp_path / "test_ledger.jsonl")
    cfg = {
        "profile_name": "US_Production",
        "rejection_gate": {
            "enabled": True,
            "max_stop_pct": 5.0,
            "max_friction_r": 0.15,
            "min_rr_ratio": 1.0,
            "enforce_anti_chase": True,
            "max_extension_pct": 1.0,
        },
        "risk": {
            "max_stop_pct": 5.0,
            "slippage_pct": 0.01,
            "commission_pct": 0.01,
            "max_friction_r": 0.15,
        },
    }

    # Setup pristine on all gates except extended 1.5% past pivot 100.0 (tolerance 1.0%)
    setup = Setup(
        symbol="NVDA",
        state=State.BUY,
        direction=1,
        entry=100.0,
        current_price=101.5,
        stop=98.0,
        risk_pct=2.0,
        tp1=104.0,
        tp2=108.0,
        atr=2.0,
        resistance=100.0,
        breakout_level=100.0,
        composite_score=88,
        composite_grade="A",
        breakout_time="2026-11-16T15:00:00Z",
    )
    setup.friction_r = 0.04

    gate = TradeRejectionGate(cfg)
    decision = gate.evaluate_setup(setup)
    assert not decision.approved
    assert decision.rejection_code == "LATE_CHASE"

    slate = ActionableSlate(
        slate_id="slate_test_1",
        timestamp="2026-11-16T15:05:00Z",
        profile_name="US_Production",
        total_scanned=1,
        portfolio_equity=100000.0,
        max_daily_risk_pct=2.0,
        max_daily_dollar_risk=2000.0,
        rejected_trades=[
            RejectionRecord(
                symbol="NVDA",
                rejection_code="LATE_CHASE",
                reason=decision.reason,
                setup=setup,
                details=decision.details,
            )
        ],
    )

    count = record_forward_test_ledger([setup], cfg, slate=slate, ledger_path=ledger_file)
    assert count == 1

    with open(ledger_file, "r") as f:
        lines = [json.loads(line) for line in f]

    assert len(lines) == 1
    row = lines[0]

    assert row["bucket"] == "LATE_CHASE_SHADOW"
    assert row["symbol"] == "NVDA"
    assert row["code"] == "LATE_CHASE"
    assert row["status"] in ("REJECTED", "LATE_CHASE_SHADOW")

    # Required measurement fields
    assert row.get("pivot") == 100.0 or row.get("price", {}).get("pivot") == 100.0
    assert row.get("signal_price") == 101.5 or row.get("price", {}).get("signal_price") == 101.5 or row.get("price", {}).get("current") == 101.5
    assert row.get("extension_pct") == 1.5 or row.get("price", {}).get("extension_pct") == 1.5
    assert row.get("tolerance_pct") == 1.0 or row.get("price", {}).get("tolerance_pct") == 1.0
    assert row.get("atr") == 2.0 or row.get("price", {}).get("atr") == 2.0
    assert row.get("score") == 88 or row.get("quant_score") == 88
    assert row.get("grade") == "A" or row.get("quant_grade") == "A"
    assert row.get("stop") == 98.0 or row.get("price", {}).get("stop") == 98.0
    assert row.get("tp1") == 104.0 or row.get("price", {}).get("tp1") == 104.0
    assert row.get("tp2") == 108.0 or row.get("price", {}).get("tp2") == 108.0


def test_late_chase_with_other_rejection_no_shadow_row(tmp_path):
    """
    ITEM 3 Regression Test 2:
    When a setup fails LATE_CHASE AND another gate (e.g. friction excessive or structural risk exceeded),
    it MUST NOT be written to bucket='LATE_CHASE_SHADOW'.
    """
    ledger_file = str(tmp_path / "test_ledger.jsonl")
    cfg = {
        "profile_name": "US_Production",
        "rejection_gate": {
            "enabled": True,
            "max_stop_pct": 5.0,
            "max_friction_r": 0.15,
            "min_rr_ratio": 1.0,
            "enforce_anti_chase": True,
            "max_extension_pct": 1.0,
        },
        "risk": {
            "max_stop_pct": 5.0,
            "slippage_pct": 0.05,
            "commission_pct": 0.05,
            "max_friction_r": 0.15,
        },
    }

    # Setup extended (101.5 vs pivot 100.0) BUT ALSO friction is excessive (0.25R > 0.15R)
    setup = Setup(
        symbol="BAD_LATE",
        state=State.BUY,
        direction=1,
        entry=100.0,
        current_price=101.5,
        stop=98.0,
        risk_pct=2.0,
        tp1=104.0,
        tp2=108.0,
        atr=2.0,
        resistance=100.0,
        breakout_level=100.0,
        composite_score=80,
        composite_grade="B",
        breakout_time="2026-11-16T15:00:00Z",
    )
    setup.friction_r = 0.25  # Fails Gate 5 friction!

    slate = ActionableSlate(
        slate_id="slate_test_2",
        timestamp="2026-11-16T15:05:00Z",
        profile_name="US_Production",
        total_scanned=1,
        portfolio_equity=100000.0,
        max_daily_risk_pct=2.0,
        max_daily_dollar_risk=2000.0,
        rejected_trades=[
            RejectionRecord(
                symbol="BAD_LATE",
                rejection_code="FRICTION_EXCESSIVE",
                reason="Excessive friction",
                setup=setup,
            )
        ],
    )

    record_forward_test_ledger([setup], cfg, slate=slate, ledger_path=ledger_file)

    with open(ledger_file, "r") as f:
        lines = [json.loads(line) for line in f]

    assert len(lines) == 1
    assert lines[0].get("bucket") != "LATE_CHASE_SHADOW"


def test_shadow_rows_excluded_from_approved_stats(tmp_path):
    """
    ITEM 3 Regression Test 3:
    Shadow rows must never be counted toward approved-trade stats or main P&L.
    """
    ledger_file = str(tmp_path / "forward_test_ledger.jsonl")
    outcomes_file = str(tmp_path / "forward_test_outcomes.jsonl")

    # Write one APPROVED trade (+1.0R) and one LATE_CHASE_SHADOW row (+2.0R)
    row_approved = {
        "signal_id": "APPROVED_SIG_1",
        "symbol": "APP1",
        "direction": "BUY",
        "status": "APPROVED",
        "bucket": "MAIN",
        "timestamp": "2026-11-10T10:00:00Z",
        "price": {"entry": 100.0, "stop": 98.0, "tp1": 102.0, "tp2": 104.0},
    }
    row_shadow = {
        "signal_id": "SHADOW_SIG_1",
        "symbol": "SHAD1",
        "direction": "BUY",
        "status": "REJECTED",
        "code": "LATE_CHASE",
        "bucket": "LATE_CHASE_SHADOW",
        "timestamp": "2026-11-10T10:00:00Z",
        "price": {"entry": 100.0, "stop": 98.0, "tp1": 102.0, "tp2": 104.0, "pivot": 100.0},
    }

    with open(ledger_file, "w") as f:
        f.write(json.dumps(row_approved) + "\n")
        f.write(json.dumps(row_shadow) + "\n")

    # Mock outcomes directly
    out_approved = {
        "signal_id": "APPROVED_SIG_1",
        "symbol": "APP1",
        "status": "EVALUATED",
        "evaluated": True,
        "hypothetical_r": 1.0,
    }
    out_shadow = {
        "signal_id": "SHADOW_SIG_1",
        "symbol": "SHAD1",
        "status": "EVALUATED",
        "evaluated": True,
        "hypothetical_r": 2.0,
    }
    with open(outcomes_file, "w") as f:
        f.write(json.dumps(out_approved) + "\n")
        f.write(json.dumps(out_shadow) + "\n")

    res = update_ledger_outcomes(ledger_path=ledger_file, outcomes_path=outcomes_file)
    metrics = res.get("metrics", {})

    # Approved average must be 1.0, NOT polluted by shadow's 2.0 (which would give 1.5)
    assert metrics.get("approved_avg_r") == 1.0
    assert metrics.get("approved_count") == 1


def test_hypothetical_pullback_to_pivot_fill():
    """
    ITEM 3 Regression Test 4:
    Hypothetical pullback-to-pivot fill:
    - Filled if a later bar touches pivot before stop.
    - Not filled if price moves away to TP or fails without touching pivot.
    """
    record = {
        "signal_id": "TEST_PULLBACK_1",
        "symbol": "TEST",
        "direction": "BUY",
        "timestamp": "2026-11-01T10:00:00Z",
        "pivot": 100.0,
        "price": {
            "current": 101.5,
            "entry": 101.5,
            "pivot": 100.0,
            "stop": 98.0,
            "tp1": 104.0,
            "tp2": 108.0,
        },
    }

    # Case A: Price pulls back to 99.8 on Bar 2 (touches pivot 100.0) then rallies to 104.5 (TP1 hit)
    dates_a = pd.date_range("2026-11-01 10:00:00", periods=4, freq="1h", tz="UTC")
    df_a = pd.DataFrame({
        "Open": [101.5, 101.2, 100.5, 102.0],
        "High": [101.8, 101.5, 101.0, 104.5],
        "Low": [101.0, 100.8, 99.8, 101.5],   # Bar 2 touches pivot (99.8 <= 100.0)
        "Close": [101.5, 101.0, 100.8, 104.2],
        "Volume": [1000] * 4,
    }, index=dates_a)

    out_a = evaluate_setup_outcome_from_df(record, df_a)
    assert out_a.get("pullback_filled") is True or out_a.get("pullback_to_pivot_fill", {}).get("filled") is True

    # Case B: Price rallies straight to TP1 without ever pulling back to pivot 100.0
    dates_b = pd.date_range("2026-11-01 10:00:00", periods=4, freq="1h", tz="UTC")
    df_b = pd.DataFrame({
        "Open": [101.5, 102.0, 103.0, 103.5],
        "High": [102.0, 103.0, 104.2, 104.8],  # Reaches TP1
        "Low": [101.2, 101.8, 102.5, 103.0],   # Never drops below 101.2 (never touches 100.0)
        "Close": [102.0, 103.0, 103.8, 104.5],
        "Volume": [1000] * 4,
    }, index=dates_b)

    out_b = evaluate_setup_outcome_from_df(record, df_b)
    assert (out_b.get("pullback_filled") is False) or (out_b.get("pullback_to_pivot_fill", {}).get("filled") is False)
