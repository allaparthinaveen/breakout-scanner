import json
import pytest
import pandas as pd
import numpy as np
from breakout_scanner.outcome_tracker import evaluate_setup_outcome, update_ledger_outcomes


class MockOutcomeProvider:
    def __init__(self, high_mult=1.05, low_mult=0.98):
        self.high_mult = high_mult
        self.low_mult = low_mult

    def candles(self, symbol, period, interval):
        # 10 bars rallying past TP1
        dates = pd.date_range("2026-11-01", periods=10, freq="1h")
        return pd.DataFrame({
            "Open": [100.0 + i for i in range(10)],
            "High": [101.0 + i * 1.5 for i in range(10)],
            "Low": [99.5 + i for i in range(10)],
            "Close": [100.8 + i * 1.2 for i in range(10)],
            "Volume": [1000] * 10,
        }, index=dates)


def test_evaluate_setup_outcome_tp1_and_mfe():
    provider = MockOutcomeProvider()
    record = {
        "symbol": "TEST",
        "direction": "BUY",
        "timestamp": "2026-11-01T00:00:00Z",
        "price": {
            "entry": 100.0,
            "stop": 98.0,      # 2.0 initial risk
            "tp1": 102.0,      # 1.0R
            "tp2": 104.0,      # 2.0R
        },
    }

    outcome = evaluate_setup_outcome(record, provider=provider, eval_bars=10)
    assert outcome["evaluated"] is True
    assert outcome["hit_tp1"] is True
    assert outcome["max_favorable_excursion_r"] > 1.0
    assert outcome["hypothetical_r"] > 0.0


def test_update_ledger_outcomes_batch(tmp_path):
    ledger_path = str(tmp_path / "test_ledger.jsonl")
    provider = MockOutcomeProvider()

    rec1 = {
        "symbol": "SYM1",
        "status": "APPROVED",
        "direction": "BUY",
        "timestamp": "2026-11-01T00:00:00Z",
        "price": {"entry": 100.0, "stop": 98.0, "tp1": 102.0, "tp2": 104.0},
        "what_if_outcome": {"evaluated": False},
    }
    rec2 = {
        "symbol": "SYM2",
        "status": "SUPPRESSED",
        "direction": "BUY",
        "timestamp": "2026-11-01T00:00:00Z",
        "price": {"entry": 50.0, "stop": 49.0, "tp1": 51.0, "tp2": 52.0},
        "what_if_outcome": {"evaluated": False},
    }

    with open(ledger_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec1) + "\n")
        f.write(json.dumps(rec2) + "\n")

    res = update_ledger_outcomes(ledger_path=ledger_path, provider=provider)
    assert res["status"] == "SUCCESS"
    assert res["newly_evaluated"] == 2
    assert "approved_avg_r" in res["metrics"]
    assert "suppressed_avg_r" in res["metrics"]
