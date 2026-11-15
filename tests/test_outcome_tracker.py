import json
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from breakout_scanner.outcome_tracker import evaluate_setup_outcome_from_df, update_ledger_outcomes


class MockMultiBarProvider:
    def __init__(self, bars_dict=None):
        self.bars_dict = bars_dict or {}
        self.calls = []

    def candles(self, symbol, period, interval):
        self.calls.append((symbol, period, interval))
        return self.bars_dict.get(symbol, pd.DataFrame())


def test_next_open_execution_and_continuous_in_progress():
    """
    Verifies:
    1. Fill occurs at Bar 1 Open + 5 bps friction (not Bar 0 Close).
    2. With only 3 bars and no stop/TP2 hit, trade status is IN_PROGRESS (evaluated=False).
    3. Revisiting with 40 bars where TP2 is reached finalizes as EVALUATED (evaluated=True, hypothetical_r=1.50).
    """
    sig_time = "2026-11-01T10:00:00Z"
    record = {
        "signal_id": "TEST_SIG_1",
        "symbol": "TEST",
        "direction": "BUY",
        "timestamp": sig_time,
        "price": {
            "entry": 100.0,
            "stop": 98.0,      # Initial risk based on fill
            "tp1": 102.0,      # 1.0R
            "tp2": 104.0,      # 2.0R
        },
    }

    # Night 1: Only 3 bars exist post-signal (Bar 0 signal close, Bar 1 Open fill at 100.0, Bar 2 rally to 101.0)
    dates_3 = pd.date_range("2026-11-01 10:00:00", periods=3, freq="1h", tz="UTC")
    df_night_1 = pd.DataFrame({
        "Open": [99.5, 100.0, 100.5],
        "High": [100.0, 100.8, 101.0],
        "Low": [99.0, 99.8, 100.2],
        "Close": [100.0, 100.6, 100.9],
        "Volume": [1000] * 3,
    }, index=dates_3)

    out_night_1 = evaluate_setup_outcome_from_df(record, df_night_1)
    # Realistic Next-Open Fill: 100.0 * 1.0005 = 100.05
    assert out_night_1["actual_fill"] == 100.05
    # MUST NOT be locked out as terminal evaluated!
    assert out_night_1["status"] == "IN_PROGRESS"
    assert out_night_1["evaluated"] is False
    assert out_night_1["bars_elapsed"] == 2

    # Night 2: 40 bars have elapsed. Price rallied past TP1 (102.0) and touched TP2 (104.1)
    dates_40 = pd.date_range("2026-11-01 10:00:00", periods=40, freq="1h", tz="UTC")
    highs = np.linspace(100.0, 105.0, 40)
    df_night_2 = pd.DataFrame({
        "Open": np.linspace(99.5, 104.0, 40),
        "High": highs,
        "Low": np.linspace(99.0, 103.5, 40),
        "Close": np.linspace(100.0, 104.5, 40),
        "Volume": [1000] * 40,
    }, index=dates_40)

    out_night_2 = evaluate_setup_outcome_from_df(record, df_night_2)
    # Terminal TP2 hit: 50% at TP1 (1.0R) + 50% at TP2 (2.0R) = 1.50R
    assert out_night_2["status"] == "EVALUATED"
    assert out_night_2["evaluated"] is True
    assert out_night_2["hit_tp1"] is True
    assert out_night_2["hit_tp2"] is True
    assert out_night_2["hypothetical_r"] == 1.50


def test_accurate_tp1_banked_and_mtm_maths():
    """
    Verifies that after TP1 is banked, if trade closes at MTM on remaining half,
    it correctly calculates 0.50 * 1.0 + 0.50 * rem_r (e.g. 0.75R).
    """
    sig_time = "2026-11-01 10:00:00"
    record = {
        "signal_id": "TEST_SIG_MTM",
        "symbol": "TEST",
        "direction": "BUY",
        "timestamp": sig_time,
        "price": {
            "entry": 100.0,
            "stop": 98.0,      # ~2.0 risk
            "tp1": 102.05,     # ~1.0R above 100.05 fill
            "tp2": 106.0,
        },
    }

    # Generate 70 bars (max hold window)
    dates_70 = pd.date_range("2026-11-01 10:00:00", periods=71, freq="1h", tz="UTC")
    # Bar 1 fills at 100.5 Open (+5 bps -> ~100.55). Risk is ~2.55.
    # Bar 5 touches 103.5 (TP1 hit!)
    # Remaining bars fluctuate and Bar 70 ends with remaining half at +0.50R excursion (~101.83)
    closes = [100.0] + [100.5]*3 + [103.5]*10 + [101.825]*57
    highs = [100.5] + [100.8]*3 + [103.6]*10 + [102.5]*57
    lows = [100.0] + [100.6]*70  # Low stays strictly above breakeven stop (~100.55)

    df_70 = pd.DataFrame({
        "Open": closes,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": [1000] * 71,
    }, index=dates_70)

    out = evaluate_setup_outcome_from_df(record, df_70, max_hold_bars=70)
    assert out["status"] == "EVALUATED"
    assert out["evaluated"] is True
    assert out["hit_tp1"] is True
    assert out["exit_reason"] == "TIME_STOP_10D"
    # Banked half is 0.5 * 1.0 = 0.50R. Remaining half at ~0.50R is 0.5 * 0.50 = 0.25R. Total = 0.75R!
    assert out["hypothetical_r"] == pytest.approx(0.75, abs=0.03)


def test_update_ledger_outcomes_quarantines_provisional_and_updates_in_progress(tmp_path):
    ledger_path = str(tmp_path / "forward_test_ledger.jsonl")
    outcomes_path = str(tmp_path / "forward_test_outcomes.jsonl")

    now_utc = datetime.now(timezone.utc)
    fresh_ts = (now_utc - timedelta(days=1)).isoformat()

    rec_approved = {
        "signal_id": "APP_1",
        "symbol": "APP",
        "status": "APPROVED",
        "direction": "BUY",
        "timestamp": fresh_ts,
        "price": {"entry": 100.0, "stop": 98.0, "tp1": 102.0, "tp2": 104.0},
    }
    rec_provisional = {
        "signal_id": "PROV_1",
        "symbol": "PROV",
        "status": "PROVISIONAL",
        "direction": "BUY",
        "timestamp": fresh_ts,
        "price": {"entry": 50.0, "stop": 49.0, "tp1": 51.0, "tp2": 52.0},
    }

    with open(ledger_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec_approved) + "\n")
        f.write(json.dumps(rec_provisional) + "\n")

    # Mock provider returning 5 bars
    dates = pd.date_range(now_utc - timedelta(days=1), periods=5, freq="1h", tz="UTC")
    df_app = pd.DataFrame({
        "Open": [100.0, 100.0, 101.0, 102.5, 104.5],  # hits TP2
        "High": [100.5, 100.5, 101.5, 103.0, 105.0],
        "Low": [99.5, 99.8, 100.5, 102.0, 104.0],
        "Close": [100.0, 100.5, 101.2, 102.8, 104.8],
        "Volume": [1000] * 5,
    }, index=dates)

    df_prov = pd.DataFrame({
        "Open": [50.0, 50.0, 48.5, 48.0, 47.5],  # hits stop loss (-1.0R)
        "High": [50.2, 50.2, 49.5, 48.5, 48.0],
        "Low": [49.8, 48.5, 48.0, 47.5, 47.0],
        "Close": [50.0, 49.0, 48.2, 47.8, 47.2],
        "Volume": [1000] * 5,
    }, index=dates)

    provider = MockMultiBarProvider(bars_dict={"APP": df_app, "PROV": df_prov})

    res = update_ledger_outcomes(
        ledger_path=ledger_path,
        outcomes_path=outcomes_path,
        provider=provider,
    )

    assert res["status"] == "SUCCESS"
    metrics = res["metrics"]
    # APP made +1.50R
    assert metrics["approved_avg_r"] == 1.50
    assert metrics["approved_count"] == 1
    # PROV lost -1.00R, but ISOLATED in provisional bucket!
    assert metrics["provisional_avg_r"] == -1.00
    assert metrics["provisional_count"] == 1
    # Approved average was NOT contaminated by provisional loss!
    assert metrics["approved_avg_r"] == 1.50
