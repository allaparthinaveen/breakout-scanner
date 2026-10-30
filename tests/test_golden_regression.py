"""
Phase 0 Regression Shield & Golden Snapshot Verification Suite.
Asserts that future modifications to indicators, scoring, or rules do not cause
behavioral drift or regression against the frozen BASELINE_V1 outputs.
"""

import os
import json
import pytest
import pandas as pd
import numpy as np

from breakout_scanner.config import load_config
from breakout_scanner.scanner import MultiSymbolScanner
from breakout_scanner.providers.base import MarketDataProvider
from breakout_scanner.telemetry import ScanMetricsLogger, log_scan_metrics
from breakout_scanner.version import __version__, VERSION
from unittest.mock import MagicMock
import yfinance as yf


@pytest.fixture(autouse=True)
def mock_golden_yfinance(monkeypatch):
    """Ensure golden regression test runs 100% offline without live Yahoo Finance queries."""
    def mock_ticker(sym):
        m = MagicMock()
        if sym in ("AAPL", "MSFT", "NVDA", "CSCO", "ORCL", "AMD"):
            m.info = {"recommendationKey": "buy"}
        else:
            m.info = {"recommendationKey": "hold"}
        m.calendar = None
        return m

    monkeypatch.setattr(yf, "Ticker", mock_ticker)


class OfflineGoldenProvider(MarketDataProvider):
    """
    Offline data provider that serves strictly from the frozen golden fixture directory.
    Guarantees 100% deterministic, zero-network execution for regression testing.
    """
    def __init__(self, fixture_dir: str = "tests/fixtures/golden_dataset"):
        self.fixture_dir = fixture_dir
        self.daily_cache = {}
        self.daily_cache_ts = {}
        self.market_cache = {}
        self.market_cache_ts = {}

    def candles(self, symbol: str, period: str = "60d", interval: str = "1h", **kwargs) -> pd.DataFrame:
        clean_name = symbol.replace("^", "").replace(":", "_").replace("/", "_")
        suffix = "1d" if interval in ("1d", "1D") or period in ("1y", "2y", "3y") and interval not in ("1h", "1m", "5m", "15m") else "1h"
        if interval == "1d":
            suffix = "1d"
            
        path = os.path.join(self.fixture_dir, f"{clean_name}_{suffix}.pkl")
        if not os.path.exists(path):
            path = os.path.join(self.fixture_dir, f"QQQ_{suffix}.pkl")
            
        if os.path.exists(path):
            return pd.read_pickle(path)
        return pd.DataFrame()


def test_baseline_v1_version_tagging():
    """Verify that current code is formally tagged as BASELINE_V1."""
    assert __version__ == "BASELINE_V1"
    assert VERSION == "BASELINE_V1"
    
    version_file = "config/version.json"
    assert os.path.exists(version_file), "config/version.json missing"
    with open(version_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("version") == "BASELINE_V1"


def test_golden_regression_outputs_match_snapshot():
    """
    Re-runs the scanner in offline mode on the golden fixture dataset and asserts
    identical candidate lists, state classifications, pivot levels, and quant scores.
    """
    cfg = load_config("config/default.yaml")
    provider = OfflineGoldenProvider()
    scanner = MultiSymbolScanner(provider, cfg)
    
    symbols = ["AAPL", "MSFT", "NVDA", "CSCO", "ORCL", "AMD", "QCOM"]
    setups = scanner.scan(symbols)
    
    # Load frozen golden snapshot
    snapshot_path = "tests/fixtures/golden_snapshot.json"
    assert os.path.exists(snapshot_path), "Golden snapshot fixture missing"
    with open(snapshot_path, "r", encoding="utf-8") as f:
        golden_snapshot = json.load(f)
        
    assert len(setups) == len(symbols)
    
    for s in setups:
        sym = s.symbol
        assert sym in golden_snapshot, f"Unexpected symbol {sym} in output"
        expected = golden_snapshot[sym]
        
        # 1. State and Direction Match
        assert s.state.name == expected["state"], f"State mismatch for {sym}"
        assert s.direction == expected["direction"], f"Direction mismatch for {sym}"
        
        # 2. Structural Pivot Consistency (within 0.05 tolerance)
        assert s.base_width_pct == pytest.approx(expected["base_width_pct"], abs=0.05), f"Base width mismatch for {sym}"
        assert s.resistance == pytest.approx(expected["resistance"], abs=0.05), f"Resistance mismatch for {sym}"
        assert s.support == pytest.approx(expected["support"], abs=0.05), f"Support mismatch for {sym}"
        
        # 3. Wave Decay & VDU Metrics
        assert s.vdu_ratio == pytest.approx(expected["vdu_ratio"], abs=0.05), f"VDU ratio mismatch for {sym}"
        assert s.vdu_confirmed == expected["vdu_confirmed"], f"VDU confirmed mismatch for {sym}"
        assert s.decay_ratio == pytest.approx(expected["decay_ratio"], abs=0.05), f"Decay ratio mismatch for {sym}"
        assert s.tightening_confirmed == expected["tightening_confirmed"], f"Tightening confirmed mismatch for {sym}"
        
        # 4. Quant Composite Score and Tier Grade
        assert s.composite_score == expected["composite_score"], f"Composite score mismatch for {sym}"
        assert s.composite_grade == expected["composite_grade"], f"Composite grade mismatch for {sym}"
        assert s.bias_score == expected["bias_score"], f"Bias score mismatch for {sym}"


def test_scan_metrics_telemetry_logger(tmp_path):
    """
    Validates that ScanMetricsLogger records scan-level metrics, candidate counts,
    and reject reason histograms accurately without modifying setup objects.
    """
    logger = ScanMetricsLogger(output_dir=str(tmp_path))
    
    cfg = load_config("config/default.yaml")
    provider = OfflineGoldenProvider()
    scanner = MultiSymbolScanner(provider, cfg)
    
    symbols = ["AAPL", "MSFT", "NVDA", "CSCO", "ORCL", "AMD", "QCOM"]
    setups = scanner.scan(symbols)
    
    # Record metrics
    record = logger.record_scan(setups, market="us_tech", timeframe="1h", config_name="default.yaml")
    
    assert record["total_scanned"] == 7
    assert record["total_candidates"] == 7
    assert record["ready_count"] == 7
    assert record["breakout_count"] == 0
    assert record["avg_candidate_score"] > 50.0
    
    # Grade distribution contains A, B, C
    assert record["grade_distribution"]["A"] >= 1
    assert record["grade_distribution"]["B"] >= 3
    assert record["grade_distribution"]["C"] >= 3
    
    # Verify reject reason histogram contains counts
    assert "LOOSE_RANGE_GT_10PCT" in record["reject_histogram"]
    assert record["reject_histogram"]["LOOSE_RANGE_GT_10PCT"] >= 2  # ORCL and AMD > 10%
    
    # Verify file persistence
    assert os.path.exists(logger.jsonl_path)
    assert os.path.exists(logger.csv_path)
    
    # Verify readback
    history = logger.get_history()
    assert len(history) == 1
    assert history[0]["total_scanned"] == 7
