import json
import pytest
from breakout_scanner.models import Setup, State
from breakout_scanner.audit_ledger import compute_config_hash, record_forward_test_ledger
from breakout_scanner.slate import ActionableSlate, SuppressedRecord, RejectionRecord


def test_compute_config_hash_consistency():
    cfg1 = {"timeframe": {"interval": "1h"}, "risk": {"max_stop_pct": 5.0}}
    cfg2 = {"risk": {"max_stop_pct": 5.0}, "timeframe": {"interval": "1h"}}
    # Dict key order should not change hash
    assert compute_config_hash(cfg1) == compute_config_hash(cfg2)
    assert len(compute_config_hash(cfg1)) == 12


def test_record_forward_test_ledger_schema(tmp_path):
    ledger_file = str(tmp_path / "test_ledger.jsonl")
    cfg = {"profile_name": "TestProfile", "confirmation_policy": {"min_auto_entry_score": 70}}

    s1 = Setup(
        symbol="AAPL",
        state=State.BUY,
        direction=1,
        entry=150.0,
        stop=147.0,
        risk_pct=2.0,
        tp1=153.0,
        tp2=156.0,
        composite_score=85,
        composite_grade="A",
    )
    s1.htf_data_status = "OK"
    s1.sector_data_status = "OK"
    s1.is_above_daily_sma50 = True

    s2 = Setup(
        symbol="MSFT",
        state=State.BUY,
        direction=1,
        entry=300.0,
        stop=290.0,
        risk_pct=3.33,
        tp1=310.0,
        tp2=320.0,
        composite_score=60,
        composite_grade="C",
        requires_confirmation=True,
    )
    s2.htf_data_status = "FAILED"
    s2.provisional_data_incomplete = False

    slate = ActionableSlate(
        slate_id="test1234",
        timestamp="2026-11-14T00:00:00Z",
        profile_name="TestProfile",
        total_scanned=2,
        portfolio_equity=100000.0,
        max_daily_risk_pct=2.0,
        max_daily_dollar_risk=2000.0,
        suppressed_trades=[
            SuppressedRecord(
                symbol="MSFT",
                suppression_code="AWAITING_CONFIRMATION",
                reason="Score 60 < 70 requires follow-through confirmation",
                setup=s2,
            )
        ],
    )

    count = record_forward_test_ledger([s1, s2], cfg, slate=slate, ledger_path=ledger_file)
    assert count == 2

    with open(ledger_file, "r") as f:
        lines = [json.loads(line) for line in f]

    assert len(lines) == 2

    # Check AAPL record
    aapl_rec = [r for r in lines if r["symbol"] == "AAPL"][0]
    assert aapl_rec["status"] == "APPROVED"
    assert aapl_rec["config_hash"] == compute_config_hash(cfg)
    assert aapl_rec["data_health"]["htf_status"] == "OK"
    assert aapl_rec["shadow_features"]["is_above_daily_sma50"] is True
    assert "execution" in aapl_rec
    assert "what_if_outcome" in aapl_rec

    # Check MSFT record
    msft_rec = [r for r in lines if r["symbol"] == "MSFT"][0]
    assert msft_rec["status"] == "SUPPRESSED"
    assert msft_rec["code"] == "AWAITING_CONFIRMATION"
    assert "Score 60 < 70" in msft_rec["reason"]
    assert msft_rec["data_health"]["htf_status"] == "FAILED"
