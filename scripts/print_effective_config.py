#!/usr/bin/env python3
"""
Print Effective Config Snapshot.
Outputs the active parameters and deterministic config_hash for audit review.
"""

import os
import sys

# Ensure project root is in sys.path when executed directly as a script
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
import argparse
from breakout_scanner.config import load_config
from breakout_scanner.audit_ledger import compute_config_hash


def get_effective_config(config_path: str = "config/default.yaml") -> dict:
    cfg = load_config(config_path) if os.path.exists(config_path) else {}
    c_hash = compute_config_hash(cfg)

    c_timeframe = cfg.get("timeframe", {})
    c_break = cfg.get("breakout", {})
    c_risk = cfg.get("risk", {})
    c_qual = cfg.get("signal_quality", {})
    c_earn = cfg.get("earnings", {})
    c_health = cfg.get("data_health", {})
    c_conf = cfg.get("confirmation_policy", {})
    c_safe = cfg.get("safeguards", {})
    c_rej = cfg.get("rejection_gate", {})

    min_auto_score = int(c_qual.get("min_auto_entry_score", c_conf.get("min_auto_entry_score", 70)))
    stale_cap = int(c_safe.get("max_stale_data_seconds", c_risk.get("max_stale_data_seconds", 600)))
    conf_enabled = bool(c_conf.get("enabled", c_break.get("use_open_confirmation", False)))
    shorts_enabled = bool(c_break.get("allow_shorts", True))

    return {
        "config_file_path": config_path,
        "config_hash": c_hash,
        "effective_config": {
            "timeframe": c_timeframe.get("interval", "1h"),
            "allow_shorts": shorts_enabled,
            "shorts_enabled": shorts_enabled,
            "slippage_pct": c_risk.get("slippage_pct", 0.05),
            "commission_pct": c_risk.get("commission_pct", 0.05),
            "max_friction_r": c_risk.get("max_friction_r", 0.15),
            "cost_model": {
                "slippage_pct": float(c_risk.get("slippage_pct", 0.05)),
                "commission_pct": float(c_risk.get("commission_pct", 0.05)),
                "max_friction_r": float(c_risk.get("max_friction_r", 0.15)),
            },
            "min_auto_entry_score": min_auto_score,
            "confirmation_policy": {
                "enabled": conf_enabled,
                "min_auto_entry_score": int(c_conf.get("min_auto_entry_score", min_auto_score)),
            },
            "max_stale_data_seconds": stale_cap,
            "extension_tolerance": {
                "enforce_anti_chase": bool(c_rej.get("enforce_anti_chase", True)),
                "max_extension_pct": float(c_rej.get("max_extension_pct", 1.0)),
            },
            "earnings_unverified_policy": c_earn.get("unverified_policy", "warn"),
            "data_health": {
                "min_scan_completeness_pct": c_health.get("min_scan_completeness_pct", 90),
                "abort_after_consecutive_empty": c_health.get("abort_after_consecutive_empty", 8),
            },
            "risk_settings": {
                "max_stop_pct": c_risk.get("max_stop_pct", 5.0),
                "min_stop_pct": c_risk.get("min_stop_pct", 0.5),
                "atr_multiplier": c_risk.get("atr_multiplier", 1.5),
            },
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Print effective strategy config snapshot")
    parser.add_argument("--config", type=str, default="config/default.yaml", help="Path to config yaml")
    args = parser.parse_args()

    snapshot = get_effective_config(args.config)
    print(json.dumps(snapshot, indent=2))


if __name__ == "__main__":
    main()
