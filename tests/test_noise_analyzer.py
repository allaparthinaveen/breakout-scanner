"""
Phase 1 Observability & Noise Map Test Suite.
Validates:
1. PostScanAnalyzer forward 5-10 bar outcome tracking (TP1 hit vs Stopped Out, MFE, MAE).
2. NoiseReportGenerator aggregation across score buckets, base width, and RVOL.
3. Telemetry structured reject and candidate logging persistence.
"""

import os
import json
import pytest
import pandas as pd
import numpy as np

from tools.post_scan_analyzer import PostScanAnalyzer
from tools.generate_noise_report import NoiseReportGenerator
from breakout_scanner.telemetry import ScanMetricsLogger
from breakout_scanner.models import Setup, State


def test_post_scan_analyzer_tp1_hit():
    """Verify that a candidate reaching TP1 before stop is classified as TP1_REACHED with correct R."""
    analyzer = PostScanAnalyzer(forward_bars=5)

    candidate = {
        "symbol": "TEST_WIN",
        "timestamp": "2026-10-24T10:00:00Z",
        "direction": 1,
        "entry": 100.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0,
        "risk": 5.0,
        "composite_score": 85,
        "composite_grade": "A"
    }

    # Future candles: Bar 1 moves to 102, Bar 2 hits 106 (TP1), never touches stop
    future_candles = pd.DataFrame([
        {"Open": 100.0, "High": 102.0, "Low": 99.0, "Close": 101.5, "Volume": 1000},
        {"Open": 101.5, "High": 106.0, "Low": 100.0, "Close": 105.5, "Volume": 2500},
        {"Open": 105.5, "High": 108.0, "Low": 104.0, "Close": 107.0, "Volume": 1500},
    ])

    res = analyzer.evaluate_candidate_forward(candidate, future_candles)

    assert res["outcome"] == "TP1_REACHED"
    assert res["t1_hit"] is True
    assert res["stopped_out"] is False
    assert res["t1_bar"] == 2
    assert res["realized_r"] == 1.0
    assert res["mfe_r"] >= 1.2  # High reached 106, (106-100)/5 = 1.2R
    assert res["mae_r"] <= 0.2  # Low was 99, (100-99)/5 = 0.2R


def test_post_scan_analyzer_stopped_out():
    """Verify that a candidate hitting stop is classified as STOPPED_OUT (false breakout)."""
    analyzer = PostScanAnalyzer(forward_bars=5)

    candidate = {
        "symbol": "TEST_LOSS",
        "timestamp": "2026-10-24T10:00:00Z",
        "direction": 1,
        "entry": 100.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0,
        "risk": 5.0,
        "composite_score": 65,
        "composite_grade": "C"
    }

    # Future candles: Bar 1 drops to 94 (hits stop)
    future_candles = pd.DataFrame([
        {"Open": 100.0, "High": 100.5, "Low": 94.0, "Close": 94.5, "Volume": 2000},
        {"Open": 94.5, "High": 96.0, "Low": 93.0, "Close": 95.0, "Volume": 1000},
    ])

    res = analyzer.evaluate_candidate_forward(candidate, future_candles)

    assert res["outcome"] == "STOPPED_OUT"
    assert res["t1_hit"] is False
    assert res["stopped_out"] is True
    assert res["realized_r"] == -1.0
    assert res["mae_r"] >= 1.2  # Low reached 94, (100-94)/5 = 1.2R


def test_noise_report_generator_aggregation(tmp_path):
    """Test report compilation across score tiers, box quality, and RVOL levels."""
    outcomes = [
        # Grade A+ setup that hits TP1, tight box
        {
            "symbol": "TIGHT_A", "composite_score": 92, "composite_grade": "A+",
            "base_width_pct": 3.2, "rvol": 2.2, "htf_confirmed": True,
            "t1_hit": True, "stopped_out": False, "realized_r": 1.0, "mfe_r": 1.8, "mae_r": 0.2
        },
        # Grade B setup that stops out, medium box
        {
            "symbol": "MED_B", "composite_score": 75, "composite_grade": "B",
            "base_width_pct": 5.5, "rvol": 1.4, "htf_confirmed": True,
            "t1_hit": False, "stopped_out": True, "realized_r": -1.0, "mfe_r": 0.3, "mae_r": 1.1
        },
        # Grade C setup that stops out, loose box
        {
            "symbol": "LOOSE_C", "composite_score": 55, "composite_grade": "C",
            "base_width_pct": 12.0, "rvol": 0.9, "htf_confirmed": False,
            "t1_hit": False, "stopped_out": True, "realized_r": -1.0, "mfe_r": 0.1, "mae_r": 1.5
        }
    ]

    rejects = [
        {"symbol": "REJ1", "reasons": ["LOOSE_RANGE_GT_10PCT", "SUBOPTIMAL_GRADE_C"]},
        {"symbol": "REJ2", "reasons": ["NO_VOLUME_DRYUP", "UNCOILED_DECAY_GT_0_70"]},
        {"symbol": "REJ3", "reasons": ["LOOSE_RANGE_GT_10PCT"]}
    ]

    generator = NoiseReportGenerator()
    data = generator.generate_report_data(outcomes=outcomes, rejects=rejects)

    assert data["total_candidates_analyzed"] == 3
    assert data["overall_metrics"]["t1_hit_rate_pct"] == pytest.approx(33.33, abs=0.1)
    assert data["overall_metrics"]["stopped_out_rate_pct"] == pytest.approx(66.67, abs=0.1)

    # Box width check
    tight_metrics = data["by_base_width"]["Ultra-Tight (<= 4.0%)"]
    assert tight_metrics["t1_hit_rate"] == 100.0
    loose_metrics = data["by_base_width"]["Loose Box (> 7.0%)"]
    assert loose_metrics["t1_hit_rate"] == 0.0

    # Reject histogram check
    assert data["top_reject_reasons"]["LOOSE_RANGE_GT_10PCT"] == 2

    # Render Markdown report
    md_report = generator.render_markdown_report(data)
    assert "Breakout Scanner: Weekly Noise & Follow-Through Report" in md_report
    assert "Ultra-Tight (<= 4.0%)" in md_report

    # Save to disk
    md_file = tmp_path / "test_report.md"
    json_file = tmp_path / "test_report.json"
    generator.write_reports(data, md_path=str(md_file), json_path=str(json_file))

    assert os.path.exists(md_file)
    assert os.path.exists(json_file)


def test_structured_reject_and_candidate_logging(tmp_path):
    """Test that ScanMetricsLogger writes structured reject and candidate event logs."""
    logger = ScanMetricsLogger(output_dir=str(tmp_path))

    # Create dummy setups
    s1 = Setup("AAPL")
    s1.state = State.CONTRACTION
    s1.entry = 150.0
    s1.stop = 145.0
    s1.tp1 = 155.0
    s1.base_width_pct = 3.5
    s1.decay_ratio = 0.50
    s1.tightening_confirmed = True
    s1.vdu_confirmed = True
    s1.vdu_ratio = 0.65
    s1.composite_score = 88
    s1.composite_grade = "A"

    s2 = Setup("AMD")
    s2.state = State.IDLE
    s2.base_width_pct = 14.2
    s2.vdu_confirmed = False
    s2.composite_score = 45
    s2.composite_grade = "C"

    # Record scan
    record = logger.record_scan([s1, s2], market="us", timeframe="1h")

    assert os.path.exists(logger.reject_path)
    assert os.path.exists(logger.candidate_path)

    rejects = logger.get_reject_history()
    candidates = logger.get_candidate_history()

    # s2 must be in rejects with LOOSE_RANGE and SUBOPTIMAL_GRADE
    assert any(r["symbol"] == "AMD" for r in rejects)
    amd_rej = next(r for r in rejects if r["symbol"] == "AMD")
    assert "LOOSE_RANGE_GT_10PCT" in amd_rej["reasons"]

    # s1 must be in candidates
    assert any(c["symbol"] == "AAPL" for c in candidates)
    aapl_cand = next(c for c in candidates if c["symbol"] == "AAPL")
    assert aapl_cand["entry"] == 150.0
    assert aapl_cand["risk"] == 5.0
    assert aapl_cand["composite_grade"] == "A"
