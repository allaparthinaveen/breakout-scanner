import pytest
from scripts.eod_summary import generate_eod_summary


def test_late_chase_shadow_table_filters_by_market(tmp_path):
    """
    US EOD report must not include NSE tickers (e.g. TITAGARH.NS) in late-chase shadow table.
    """
    run_dir = tmp_path / "run" / "2026-11-17"
    run_dir.mkdir(parents=True)

    # Empty summaries and traces
    (run_dir / "scan_summary.jsonl").write_text("")
    (run_dir / "symbol_trace.jsonl").write_text("")
    (run_dir / "events.jsonl").write_text("")

    ledger_path = tmp_path / "run" / "forward_test_ledger.jsonl"
    ledger_records = [
        # NSE symbol on same date
        '{"schema_version": "v1.1", "scan_id": "scan_nse_1", "signal_id": "sig_nse_1", "timestamp": "2026-11-17T10:15:00", "breakout_time": "2026-11-17T10:15:00", "profile_name": "NSE Production", "symbol": "TITAGARH.NS", "bucket": "LATE_CHASE_SHADOW", "price": {"pivot": 1000.0, "current": 1020.0}}',
        '{"schema_version": "v1.1", "scan_id": "scan_nse_2", "signal_id": "sig_nse_2", "timestamp": "2026-11-17T10:15:00", "breakout_time": "2026-11-17T10:15:00", "profile_name": "NSE Production", "symbol": "BAJFINANCE.NS", "bucket": "LATE_CHASE_SHADOW", "price": {"pivot": 7000.0, "current": 7100.0}}',
        # US symbol on same date
        '{"schema_version": "v1.1", "scan_id": "scan_us_1", "signal_id": "sig_us_1", "timestamp": "2026-11-17T15:00:00", "breakout_time": "2026-11-17T15:00:00", "profile_name": "US Production", "symbol": "AAPL", "bucket": "LATE_CHASE_SHADOW", "price": {"pivot": 220.0, "current": 222.0}}',
    ]
    ledger_path.write_text("\n".join(ledger_records) + "\n")

    # Generate US EOD report
    summary_us = generate_eod_summary(market="US", date_str="2026-11-17", base_dir=str(tmp_path / "run"), ledger_path=str(ledger_path))

    shadow_symbols = [row["symbol"] for row in summary_us["late_chase_shadow"]["rows"]]
    assert "AAPL" in shadow_symbols
    assert "TITAGARH.NS" not in shadow_symbols
    assert "BAJFINANCE.NS" not in shadow_symbols
    assert len(shadow_symbols) == 1
