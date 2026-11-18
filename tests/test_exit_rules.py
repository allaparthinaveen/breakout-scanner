import pytest
from breakout_scanner.exit_rules import evaluate_position_bar


def test_fill_on_first_bar():
    pos = {
        "symbol": "TEST",
        "direction": 1,
        "status": "AWAITING_FILL",
        "stop_loss": 95.0,
        "pivot": 100.0,
        "take_profit_1": 110.0,
        "take_profit_2": 120.0,
        "shares": 100,
        "events": [],
    }
    bar = {"Open": 100.0, "High": 102.0, "Low": 99.0, "Close": 101.0, "bar_ts": "2026-11-18T10:00:00Z"}
    cfg = {"execution": {"friction_bps": 5.0}, "management": {"failed_breakout_max_bars": 3}}

    events = evaluate_position_bar(pos, bar, cfg)
    assert pos["status"] == "OPEN"
    assert pos["modeled_fill"] == pytest.approx(100.05, rel=1e-4)
    assert pos["bars_held"] == 1
    assert any(e["event_type"] == "FILL" for e in events)


def test_pvrinox_failed_breakout_wins_over_tp1():
    """
    PVRINOX regression:
    High reaches TP1, but Close slips back below pivot on bar 2.
    Failed breakout MUST win and position must be CLOSED with FAILED_BREAKOUT.
    No TP1 event may be emitted.
    """
    pos = {
        "symbol": "PVRINOX.NS",
        "direction": 1,
        "status": "OPEN",
        "modeled_fill": 100.0,
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "initial_risk": 5.0,
        "pivot": 100.0,
        "take_profit_1": 105.0,  # High touches TP1
        "take_profit_2": 110.0,
        "shares": 100,
        "bars_held": 1,
        "events": [],
    }
    # Bar 2: High reaches 106.0 (>= TP1), but Close falls to 98.0 (< pivot 100.0)
    bar = {"Open": 101.0, "High": 106.0, "Low": 97.0, "Close": 98.0, "bar_ts": "2026-11-18T11:00:00Z"}
    cfg = {"management": {"use_failed_breakout_exit": True, "failed_breakout_max_bars": 3}}

    events = evaluate_position_bar(pos, bar, cfg)
    assert pos["status"] == "CLOSED"
    assert pos["exit_reason"] == "FAILED_BREAKOUT"
    assert pos["exit_price"] == 98.0
    assert pos["realized_r"] == -0.40  # (98 - 100) / 5.0
    assert not pos.get("tp1_hit", False)
    assert any(e["event_type"] == "FAILED_BREAKOUT" for e in events)
    assert not any(e["event_type"] == "TP1_SCALE" for e in events)


def test_tp1_scale_and_subsequent_breakeven_stop():
    pos = {
        "symbol": "WINNER",
        "direction": 1,
        "status": "OPEN",
        "modeled_fill": 100.0,
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "initial_risk": 5.0,
        "pivot": 99.0,
        "take_profit_1": 105.0,
        "take_profit_2": 110.0,
        "shares": 100,
        "bars_held": 1,
        "events": [],
    }
    cfg = {"management": {"use_failed_breakout_exit": True, "failed_breakout_max_bars": 3, "breakeven_offset_pct": 0.10}}

    # Bar 1: Reaches TP1, holds above pivot
    bar1 = {"Open": 101.0, "High": 106.0, "Low": 100.5, "Close": 104.0, "bar_ts": "2026-11-18T11:00:00Z"}
    ev1 = evaluate_position_bar(pos, bar1, cfg)
    assert pos["status"] == "TP1_HIT"
    assert pos["tp1_hit"] is True
    assert pos["stop_loss"] == pytest.approx(100.10, rel=1e-3)
    assert pos["shares"] == 50
    assert any(e["event_type"] == "TP1_SCALE" for e in ev1)

    # Bar 2: Drops back to hit the trailed breakeven stop
    bar2 = {"Open": 103.0, "High": 103.5, "Low": 100.0, "Close": 100.5, "bar_ts": "2026-11-18T12:00:00Z"}
    ev2 = evaluate_position_bar(pos, bar2, cfg)
    assert pos["status"] == "CLOSED"
    assert pos["exit_reason"] == "BREAKEVEN_STOP"
    assert pos["realized_r"] == 0.50
    assert any(e["event_type"] == "BREAKEVEN_STOP" for e in ev2)


def test_reject_second_terminal_state():
    pos = {
        "symbol": "CLOSED_SYM",
        "direction": 1,
        "status": "CLOSED",
        "exit_reason": "STOP_LOSS",
        "modeled_fill": 100.0,
        "events": [],
    }
    bar = {"Open": 105.0, "High": 120.0, "Low": 104.0, "Close": 119.0, "bar_ts": "2026-11-18T13:00:00Z"}
    events = evaluate_position_bar(pos, bar)
    assert events == []
    assert pos["status"] == "CLOSED"
    assert pos["exit_reason"] == "STOP_LOSS"
