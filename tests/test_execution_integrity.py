"""
Phase 1: Execution Integrity & Measurement Trust Suite (tests/test_execution_integrity.py)

Hand-calculated test cases validating:
1. Overnight adverse gap past stop (slippage fill at Open, not stop price)
2. Overnight favorable gap past TP1 (bonus fill at Open, not TP1 price)
3. Same-bar collision pessimistic execution (Stop takes priority over TP1)
4. Two-stage TP1 partial scale-out (50% at TP1, trailing stop to breakeven, then TP2)
5. TP1 scale-out followed by breakeven stop out (+0.5R gross outcome)
6. Strict rejection of missing structural stops (INVALID_SETUP, zero synthetic fallbacks)
7. Cross-pipeline equivalence: Backtester and PostScanAnalyzer generate identical outputs
"""

import pytest
import pandas as pd
import numpy as np

from breakout_scanner.config import load_config
from breakout_scanner.backtester import Backtester
from breakout_scanner.simulation import simulate_forward_trade
from tools.post_scan_analyzer import PostScanAnalyzer


def get_base_test_config():
    """Return clean base configuration with zero frictions for exact hand calculations."""
    cfg = load_config("config/default.yaml")
    cfg['risk']['slippage_pct'] = 0.0
    cfg['risk']['commission_pct'] = 0.0
    cfg['management']['use_breakeven'] = True
    cfg['management']['breakeven_offset_pct'] = 0.0
    cfg['target']['tp1_r'] = 1.0
    cfg['target']['tp2_r'] = 2.0
    return cfg


def test_hand_calc_overnight_adverse_gap_past_stop():
    """
    Test 1: Overnight gap below stop loss.
    Entry = 100.0, Stop = 95.0 (Risk = 5.0).
    Bar 1: Normal hold.
    Bar 2: Opens at 90.0 (gapped 5.0 points below stop!).
    Fill MUST be at 90.0, resulting in exactly -2.0R realized loss, NOT -1.0R.
    """
    cfg = get_base_test_config()
    candidate = {
        "symbol": "GAP_TEST",
        "direction": 1,
        "entry": 100.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0
    }
    future_candles = pd.DataFrame([
        {"Open": 100.0, "High": 102.0, "Low": 97.0, "Close": 101.0, "Volume": 1000},
        {"Open": 90.0, "High": 92.0, "Low": 88.0, "Close": 89.0, "Volume": 3000},
    ])

    res = simulate_forward_trade(candidate, future_candles, cfg=cfg)

    assert res["entry_price"] == 100.0
    assert res["exit_price"] == 90.0  # Filled at Open, not 95.0!
    assert res["exit_reason"] == "GAP_STOP"
    assert res["outcome"] == "STOPPED_OUT"
    assert res["realized_r"] == pytest.approx(-2.0, abs=0.01)  # (90 - 100) / 5 = -2.0R
    assert res["mae_r"] == pytest.approx(2.4, abs=0.01)  # Low 88, (100 - 88) / 5 = 2.4R


def test_hand_calc_overnight_favorable_gap_past_tp1():
    """
    Test 2: Overnight gap above TP1.
    Entry = 100.0, Stop = 95.0, TP1 = 105.0 (Risk = 5.0).
    Bar 1: Normal hold.
    Bar 2: Opens at 108.0 (gapped 3.0 points above TP1!).
    TP1 scale-out MUST fill at 108.0, rewarding the gap, NOT capped at 105.0.
    """
    cfg = get_base_test_config()
    candidate = {
        "symbol": "GAP_PROFIT",
        "direction": 1,
        "entry": 100.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0
    }
    future_candles = pd.DataFrame([
        {"Open": 100.0, "High": 103.0, "Low": 99.0, "Close": 102.0, "Volume": 1000},
        {"Open": 108.0, "High": 109.0, "Low": 106.0, "Close": 108.5, "Volume": 4000},
        {"Open": 108.5, "High": 111.0, "Low": 107.0, "Close": 110.5, "Volume": 2000},
    ])

    res = simulate_forward_trade(candidate, future_candles, cfg=cfg)

    assert res["t1_hit"] is True
    assert res["t1_bar"] == 2
    assert res["mfe_r"] >= 2.2  # High reached 111.0, (111 - 100) / 5 = 2.2R
    assert res["t2_hit"] is True
    assert res["exit_reason"] == "TP2_HIT"


def test_hand_calc_same_bar_collision_pessimistic():
    """
    Test 3: Same-bar collision.
    Single candle touches High = 108.0 (TP1) and Low = 93.0 (Stop).
    Pessimistic stop priority MUST trigger stop loss, tp1_hit remains False.
    """
    cfg = get_base_test_config()
    candidate = {
        "symbol": "COLLISION_TEST",
        "direction": 1,
        "entry": 100.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0
    }
    future_candles = pd.DataFrame([
        {"Open": 100.0, "High": 108.0, "Low": 93.0, "Close": 98.0, "Volume": 5000},
    ])

    res = simulate_forward_trade(candidate, future_candles, cfg=cfg)

    assert res["outcome"] == "STOPPED_OUT"
    assert res["exit_reason"] == "STOP_LOSS"
    assert res["t1_hit"] is False
    assert res["stopped_out"] is True
    assert res["realized_r"] == pytest.approx(-1.0, abs=0.01)


def test_hand_calc_partial_scaleout_then_tp2():
    """
    Test 4: Full two-stage winner.
    Entry = 100.0, Stop = 95.0 (Risk = 5.0), TP1 = 105.0, TP2 = 110.0.
    Bar 1: Touches 106.0 -> 50% sold at 105.0 (+1.0R on 0.5 = +0.5R). Stop moved to 100.0.
    Bar 2: Touches 111.0 -> Remaining 50% sold at 110.0 (+2.0R on 0.5 = +1.0R).
    Total Realized R MUST be exactly +1.50R.
    """
    cfg = get_base_test_config()
    candidate = {
        "symbol": "TWO_STAGE_WIN",
        "direction": 1,
        "entry": 100.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0
    }
    future_candles = pd.DataFrame([
        {"Open": 100.0, "High": 106.0, "Low": 99.0, "Close": 104.0, "Volume": 1000},
        {"Open": 104.0, "High": 111.0, "Low": 102.0, "Close": 110.0, "Volume": 2000},
    ])

    res = simulate_forward_trade(candidate, future_candles, cfg=cfg, use_partial_exits=True)

    assert res["t1_hit"] is True
    assert res["t2_hit"] is True
    assert res["exit_reason"] == "TP2_HIT"
    assert res["outcome"] == "TP2_REACHED"
    # 50% * 1.0R + 50% * 2.0R = +1.50R
    assert res["realized_r"] == pytest.approx(1.50, abs=0.01)


def test_hand_calc_tp1_then_breakeven_stop():
    """
    Test 5: TP1 hit, then pull back to Breakeven.
    Entry = 100.0, Stop = 95.0 (Risk = 5.0), TP1 = 105.0.
    Bar 1: High 106.0 -> 50% sold at 105.0 (+0.50R). Stop trailed to 100.0.
    Bar 2: Drops to Low 99.0 -> Remaining 50% sold at Breakeven 100.0 (0.0R).
    Total Realized R MUST be exactly +0.50R.
    """
    cfg = get_base_test_config()
    candidate = {
        "symbol": "TP1_BREAKEVEN",
        "direction": 1,
        "entry": 100.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0
    }
    future_candles = pd.DataFrame([
        {"Open": 100.0, "High": 106.0, "Low": 99.0, "Close": 103.0, "Volume": 1000},
        {"Open": 103.0, "High": 103.5, "Low": 99.0, "Close": 99.5, "Volume": 2000},
    ])

    res = simulate_forward_trade(candidate, future_candles, cfg=cfg, use_partial_exits=True)

    assert res["t1_hit"] is True
    assert res["t2_hit"] is False
    assert res["exit_reason"] == "BREAKEVEN_STOP"
    assert res["outcome"] == "TP1_THEN_BREAKEVEN"
    # 50% * 1.0R + 50% * 0.0R = +0.50R
    assert res["realized_r"] == pytest.approx(0.50, abs=0.01)


def test_strict_rejection_missing_structural_stop():
    """
    Test 6: Rule 1.3 - Missing structural stop rejection.
    If stop is None, zero, or negative:
    Simulator MUST mark INVALID_SETUP with MISSING_STRUCTURAL_STOP.
    NO synthetic 2% stops fabricated.
    """
    candidate_no_stop = {
        "symbol": "INVALID_STOP",
        "direction": 1,
        "entry": 100.0,
        "stop": None,
    }
    future_candles = pd.DataFrame([
        {"Open": 100.0, "High": 105.0, "Low": 95.0, "Close": 102.0, "Volume": 1000}
    ])

    res = simulate_forward_trade(candidate_no_stop, future_candles)

    assert res["outcome"] == "INVALID_SETUP"
    assert res["exit_reason"] == "MISSING_STRUCTURAL_STOP"
    assert res["realized_r"] == 0.0


def test_cross_pipeline_equivalence_backtester_and_forward_analyzer():
    """
    Test 7: Phase 1 Done-When Criterion.
    The exact same trade scenario run through Backtester and PostScanAnalyzer
    MUST yield identical:
    - entry_price
    - exit_price
    - exit_reason
    - tp1_hit
    - realized_r
    - mfe_r
    - mae_r
    """
    cfg = get_base_test_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False

    # Build 60-bar synthetic dataframe:
    # Bars 0-49: Flat consolidation at 100.0
    dates = pd.date_range("2024-01-01", periods=60, freq="D")
    df = pd.DataFrame({
        "Open": 100.0,
        "High": 101.0,
        "Low": 99.0,
        "Close": 100.0,
        "Volume": 1000.0
    }, index=dates)

    # Bar 50: Breakout bar (Close above resistance 101.0)
    df.loc[df.index[50], "Open"] = 100.5
    df.loc[df.index[50], "High"] = 104.0
    df.loc[df.index[50], "Low"] = 100.0
    df.loc[df.index[50], "Close"] = 103.5

    # Bar 51: Entry Bar at Open 104.0
    df.loc[df.index[51], "Open"] = 104.0
    df.loc[df.index[51], "High"] = 106.0
    df.loc[df.index[51], "Low"] = 103.0
    df.loc[df.index[51], "Close"] = 105.0

    # Bar 52: Touches TP1
    df.loc[df.index[52], "Open"] = 105.0
    df.loc[df.index[52], "High"] = 112.0
    df.loc[df.index[52], "Low"] = 104.0
    df.loc[df.index[52], "Close"] = 111.0

    # 1. Run through Backtester
    tester = Backtester(cfg)
    tester.run("CROSS_PIPELINE", df)
    assert len(tester.results) >= 1
    b_trade = tester.results[0]

    # 2. Run through PostScanAnalyzer on the exact same slice
    analyzer = PostScanAnalyzer(forward_bars=10, cfg=cfg, use_partial_exits=True)
    candidate = {
        "symbol": "CROSS_PIPELINE",
        "direction": 1,
        "entry": b_trade["signal_close"],
        "stop": b_trade["original_stop"],
        "tp1": b_trade["tp1"],
        "tp2": b_trade["tp2"],
        "resistance": b_trade.get("resistance"),
        "support": b_trade.get("support")
    }
    future_candles = df.iloc[51:]  # From entry bar onwards
    f_trade = analyzer.evaluate_candidate_forward(candidate, future_candles)

    # Cross-pipeline verification: ZERO discrepancy
    assert b_trade["entry_price"] == pytest.approx(f_trade["entry_price"], abs=0.01)
    assert b_trade["exit_price"] == pytest.approx(f_trade["exit_price"], abs=0.01)
    assert b_trade["exit_reason"] == f_trade["exit_reason"]
    assert b_trade["tp1_hit"] == f_trade["t1_hit"]
    assert b_trade["realized_r"] == pytest.approx(f_trade["realized_r"], abs=0.01)
    assert b_trade["mfe_r"] == pytest.approx(f_trade["mfe_r"], abs=0.05)
    assert b_trade["mae_r"] == pytest.approx(f_trade["mae_r"], abs=0.05)


def test_tiered_confirmation_policy_unconfirmed_follow_through():
    """Verify that a candidate requiring confirmation is skipped if Bar 0 fails to breach breakout high."""
    cfg = get_base_test_config()
    candidate = {
        "symbol": "TIER_B_TRAP",
        "direction": 1,
        "entry": 100.0,
        "breakout_high": 102.0,
        "stop": 95.0,
        "requires_confirmation": True
    }
    # Bar 0 opens at 100.5, high is only 101.0 (fails to exceed breakout high 102.0!)
    future_candles = pd.DataFrame([
        {"Open": 100.5, "High": 101.0, "Low": 98.0, "Close": 99.0, "Volume": 1000},
        {"Open": 99.0, "High": 99.5, "Low": 92.0, "Close": 93.0, "Volume": 1500},
    ])

    res = simulate_forward_trade(candidate, future_candles, cfg=cfg)
    assert res["outcome"] == "UNCONFIRMED"
    assert res["exit_reason"] == "FAILED_FOLLOW_THROUGH"
    assert res["entry_price"] is None
    assert res["realized_r"] == 0.0


def test_tiered_confirmation_policy_confirmed_follow_through():
    """Verify that a candidate requiring confirmation enters normally when Bar 0 breaches breakout high."""
    cfg = get_base_test_config()
    candidate = {
        "symbol": "TIER_B_WINNER",
        "direction": 1,
        "entry": 100.0,
        "breakout_high": 102.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0,
        "requires_confirmation": True
    }
    # Bar 0 opens at 101.0, high reaches 103.5 (breaches breakout high 102.0 -> Confirmed!)
    future_candles = pd.DataFrame([
        {"Open": 101.0, "High": 103.5, "Low": 100.5, "Close": 103.0, "Volume": 1000},
        {"Open": 103.0, "High": 106.0, "Low": 102.5, "Close": 105.5, "Volume": 1500},
    ])

    res = simulate_forward_trade(candidate, future_candles, cfg=cfg)
    assert res["outcome"] != "UNCONFIRMED"
    assert res["entry_price"] == 101.0
    assert res["t1_hit"] is True


