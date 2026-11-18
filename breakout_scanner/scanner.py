import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import pandas as pd
import numpy as np
from .engine import BreakoutEngine
from .prediction_chat import explain
from .models import Setup, State
from .sectors import get_sector_benchmark
from .scorer import calculate_composite_score
from .run_logger import (
    generate_scan_id,
    get_iso_timestamps,
    get_exchange_date_str,
    log_symbol_trace_batch,
    log_scan_summary,
    _STATE,
)

class MultiSymbolScanner:
    def __init__(self, provider, cfg):
        self.provider = provider
        self.cfg = cfg
        self.engines: Dict[str, BreakoutEngine] = {}
        self.consecutive_aux_failures = 0
        self.last_scan_stats = None

    def get_engine(self, symbol: str) -> BreakoutEngine:
        if symbol not in self.engines:
            self.engines[symbol] = BreakoutEngine(self.cfg, symbol)
        return self.engines[symbol]

    def scan(
        self,
        symbols: List[str],
        period: str = '60d',
        interval: str = '1h',
        scan_id: Optional[str] = None,
        market: Optional[str] = None,
        scheduled_time: Optional[datetime] = None,
        auto_flush: bool = True,
    ) -> List[Setup]:
        """
        Poll-based scan that maintains state. Records per-symbol decision traces
        and scan cycle metrics (§B, §C).
        """
        out = []
        self.consecutive_aux_failures = 0
        if hasattr(self.provider, "reset_circuit_breaker"):
            self.provider.reset_circuit_breaker()
        elif hasattr(self.provider, "consecutive_fetch_failures"):
            self.provider.consecutive_fetch_failures = 0

        total_symbols = len(symbols)
        with_data = 0
        failed_symbols = []
        aborted = False

        data_health_cfg = self.cfg.get("data_health", {}) if self.cfg else {}
        abort_k = int(data_health_cfg.get("abort_after_consecutive_empty", 8))
        min_comp_pct = float(data_health_cfg.get("min_scan_completeness_pct", 90))

        consecutive_initial_failures = 0
        success_seen = False

        market_id = (market or "US").upper()
        scan_id = scan_id or generate_scan_id(market_id)
        self.last_scan_id = scan_id
        if hasattr(self.provider, "current_scan_id"):
            self.provider.current_scan_id = scan_id

        actual_start_dt = datetime.now(timezone.utc)
        start_mono = time.time()
        self.last_symbol_traces: List[Dict[str, Any]] = []
        observed_bar_starts: List[str] = []
        error_type_counts: Dict[str, int] = {}
        stage_durations = {"fetch": 0.0, "engine": 0.0, "enrich": 0.0, "score": 0.0, "slate": 0.0}

        for idx, symbol in enumerate(symbols):
            if not success_seen and consecutive_initial_failures >= abort_k:
                aborted = True
                remaining = symbols[idx:]
                failed_symbols.extend(remaining)
                for rem_sym in remaining:
                    ts_u, ts_l = get_iso_timestamps(actual_start_dt, market=market_id)
                    self.last_symbol_traces.append({
                        "scan_id": scan_id,
                        "symbol": rem_sym,
                        "ts_utc": ts_u,
                        "ts_local": ts_l,
                        "market": market_id,
                        "fetch": {
                            "status": "SKIPPED",
                            "error_type": "aborted",
                            "error_message": "Scan aborted after consecutive initial empty frames",
                            "attempts": 0,
                            "latency_ms": 0.0,
                            "bars_returned": 0,
                            "first_bar_start": None,
                            "last_bar_start": None,
                            "last_bar_close_time": None,
                            "last_bar_is_closed": False,
                            "data_age_minutes": None,
                            "duplicate_ts_count": 0,
                            "nan_count": 0,
                        },
                        "engine": {
                            "state_before": "IDLE",
                            "state_after": "IDLE",
                            "first_failed_gate": "skipped",
                            "gate_details": None,
                            "all_gates": {},
                        },
                        "enrich": {
                            "htf_status": "NONE",
                            "sector_status": "NONE",
                            "regime_status": "NONE",
                            "earnings_status": "NONE",
                            "earnings_unverified": False,
                            "provisional": False,
                            "aux_fetch_failures_so_far": self.consecutive_aux_failures,
                            "htf_trend_value": None,
                            "sector_rs_value": None,
                            "regime_bullish": None,
                            "bias_score": 0,
                            "components": {},
                        },
                        "score": {
                            "composite_score": None,
                            "grade": None,
                            "pillar_scores": {},
                            "modifiers": [],
                        },
                        "gates/slate": {
                            "friction_r": None,
                            "max_friction_r": float(self.cfg.get('risk', {}).get('max_friction_r', 0.15)) if self.cfg else 0.15,
                            "friction_pass": None,
                            "risk_pct": None,
                            "rr": None,
                            "status": "NONE",
                            "code": None,
                            "reason": None,
                            "haircut_applied": False,
                            "shares": 0,
                            "dollar_risk": 0.0,
                        },
                        "timing_ms": {"fetch": 0.0, "engine": 0.0, "enrich": 0.0, "score": 0.0, "slate": 0.0},
                    })
                break

            t_f0 = time.time()
            fetch_err_cls = None
            fetch_err_msg = None
            df = None

            try:
                df = self.provider.candles(symbol, period, interval)
            except Exception as e:
                fetch_err_cls = type(e).__name__
                fetch_err_msg = str(e)
                import logging
                logging.getLogger("breakout_scanner.scanner").warning(f"Error scanning {symbol}: {e}")

            fetch_ms = (time.time() - t_f0) * 1000.0
            stage_durations["fetch"] += fetch_ms

            ts_u, ts_l = get_iso_timestamps(datetime.now(timezone.utc), market=market_id)

            if df is not None and not df.empty:
                success_seen = True
                consecutive_initial_failures = 0
                with_data += 1

                first_bar_str = str(df.index[0])
                last_bar_str = str(df.index[-1])
                observed_bar_starts.append(last_bar_str)

                try:
                    last_bar_dt = pd.to_datetime(last_bar_str)
                    inv = (interval or "1h").lower()
                    if inv.endswith("h"):
                        inv_delta = pd.Timedelta(hours=float(inv[:-1]))
                    elif inv.endswith("m"):
                        inv_delta = pd.Timedelta(minutes=float(inv[:-1]))
                    elif inv.endswith("d"):
                        inv_delta = pd.Timedelta(days=float(inv[:-1]))
                    else:
                        inv_delta = pd.Timedelta(hours=1)

                    theoretical_close = last_bar_dt + inv_delta
                    actual_close = theoretical_close

                    try:
                        from breakout_scanner.market_session import MarketSessionManager
                        msm = MarketSessionManager.from_market_id(market_id)
                        if msm:
                            _, sess_close = msm.get_session_bounds(last_bar_dt)
                            if sess_close is not None:
                                if last_bar_dt.tzinfo is not None and sess_close.tzinfo is not None:
                                    sess_close_conv = sess_close.tz_convert(last_bar_dt.tzinfo)
                                elif last_bar_dt.tzinfo is None and sess_close.tzinfo is not None:
                                    sess_close_conv = sess_close.tz_convert(msm.tz).tz_localize(None)
                                else:
                                    sess_close_conv = sess_close
                                actual_close = min(theoretical_close, sess_close_conv)
                    except Exception:
                        pass

                    last_bar_close_str = actual_close.isoformat()
                    now_utc = datetime.now(timezone.utc)
                    close_utc = actual_close.tz_localize(timezone.utc) if actual_close.tzinfo is None else actual_close.astimezone(timezone.utc)
                    diff_s = (now_utc - close_utc).total_seconds()
                    data_age_min = round(max(0.0, diff_s) / 60.0, 1)
                except Exception:
                    last_bar_close_str = None
                    data_age_min = None

                dup_count = int(df.index.duplicated().sum()) if hasattr(df.index, "duplicated") else 0
                nan_count = int(df.isna().sum().sum()) if hasattr(df, "isna") else 0

                fetch_block = {
                    "status": "OK",
                    "error_type": None,
                    "error_message": None,
                    "attempts": 1,
                    "latency_ms": round(fetch_ms, 2),
                    "bars_returned": len(df),
                    "first_bar_start": first_bar_str,
                    "last_bar_start": last_bar_str,
                    "last_bar_close_time": last_bar_close_str,
                    "last_bar_is_closed": True,
                    "data_age_minutes": data_age_min,
                    "duplicate_ts_count": dup_count,
                    "nan_count": nan_count,
                }

                t_e0 = time.time()
                engine = self.get_engine(symbol)
                setup = engine.run(df)
                engine_ms = (time.time() - t_e0) * 1000.0
                stage_durations["engine"] += engine_ms

                t_en0 = time.time()
                if setup.state in (State.CONTRACTION, State.BUY, State.SELL, State.FAILED):
                    self.enrich_bias(setup, df, period, interval)
                enrich_ms = (time.time() - t_en0) * 1000.0
                stage_durations["enrich"] += enrich_ms

                out.append(setup)

                engine_block = getattr(setup, "engine_trace", None) or {
                    "state_before": "IDLE",
                    "state_after": setup.state.name if hasattr(setup.state, "name") else str(setup.state),
                    "first_failed_gate": "none",
                    "gate_details": None,
                    "all_gates": {},
                }

                enrich_block = {
                    "htf_status": setup.htf_data_status,
                    "sector_status": setup.sector_data_status,
                    "regime_status": setup.regime_data_status,
                    "earnings_status": setup.earnings_data_status,
                    "earnings_unverified": getattr(setup, "earnings_unverified", False),
                    "provisional": getattr(setup, "provisional_data_incomplete", False),
                    "aux_fetch_failures_so_far": self.consecutive_aux_failures,
                    "htf_trend_value": getattr(setup, "htf_trend", None),
                    "sector_rs_value": getattr(setup, "sector_rs_pct", None),
                    "regime_bullish": getattr(setup, "market_regime_favorable", True),
                    "bias_score": getattr(setup, "bias_score", 0),
                    "components": getattr(setup, "bias_components", {}),
                }

                score_block = {
                    "composite_score": getattr(setup, "composite_score", None),
                    "grade": getattr(setup, "composite_grade", None),
                    "pillar_scores": getattr(setup, "pillar_scores", {}),
                    "modifiers": getattr(setup, "score_modifiers", []),
                }

                max_fric = float(self.cfg.get('risk', {}).get('max_friction_r', 0.15)) if self.cfg else 0.15
                fric_r = getattr(setup, "friction_r", None)
                fric_pass = (fric_r <= max_fric) if fric_r is not None else None
                risk_p = getattr(setup, "risk_pct", None)
                rr_val = round((setup.tp2 - setup.entry) / abs(setup.entry - setup.stop), 2) if (setup.entry and setup.stop and setup.tp2 and setup.entry != setup.stop) else None

                slate_block = {
                    "friction_r": fric_r,
                    "max_friction_r": max_fric,
                    "friction_pass": fric_pass,
                    "risk_pct": risk_p,
                    "rr": rr_val,
                    "status": "APPROVED" if (setup.state in (State.BUY, State.SELL) and not setup.provisional_data_incomplete) else ("PROVISIONAL" if (setup.state in (State.BUY, State.SELL) and setup.provisional_data_incomplete) else "NONE"),
                    "code": None,
                    "reason": None,
                    "haircut_applied": False,
                    "shares": 0,
                    "dollar_risk": 0.0,
                }

                self.last_symbol_traces.append({
                    "scan_id": scan_id,
                    "symbol": symbol,
                    "ts_utc": ts_u,
                    "ts_local": ts_l,
                    "market": market_id,
                    "fetch": fetch_block,
                    "engine": engine_block,
                    "enrich": enrich_block,
                    "score": score_block,
                    "gates/slate": slate_block,
                    "timing_ms": {
                        "fetch": round(fetch_ms, 2),
                        "engine": round(engine_ms, 2),
                        "enrich": round(enrich_ms, 2),
                        "score": 0.0,
                        "slate": 0.0,
                    },
                })

            else:
                failed_symbols.append(symbol)
                err_type = "empty_frame"
                if fetch_err_cls:
                    err_type = "429" if ("429" in fetch_err_cls or "429" in str(fetch_err_msg)) else fetch_err_cls
                error_type_counts[err_type] = error_type_counts.get(err_type, 0) + 1

                fetch_block = {
                    "status": "ERROR" if fetch_err_cls else "EMPTY",
                    "error_type": err_type,
                    "error_message": fetch_err_msg[:200] if fetch_err_msg else "DataFrame empty",
                    "attempts": 1,
                    "latency_ms": round(fetch_ms, 2),
                    "bars_returned": 0,
                    "first_bar_start": None,
                    "last_bar_start": None,
                    "last_bar_close_time": None,
                    "last_bar_is_closed": False,
                    "data_age_minutes": None,
                    "duplicate_ts_count": 0,
                    "nan_count": 0,
                }

                self.last_symbol_traces.append({
                    "scan_id": scan_id,
                    "symbol": symbol,
                    "ts_utc": ts_u,
                    "ts_local": ts_l,
                    "market": market_id,
                    "fetch": fetch_block,
                    "engine": {
                        "state_before": "IDLE",
                        "state_after": "IDLE",
                        "first_failed_gate": "empty_frame" if not fetch_err_cls else "fetch_error",
                        "gate_details": None,
                        "all_gates": {},
                    },
                    "enrich": {
                        "htf_status": "NONE",
                        "sector_status": "NONE",
                        "regime_status": "NONE",
                        "earnings_status": "NONE",
                        "earnings_unverified": False,
                        "provisional": False,
                        "aux_fetch_failures_so_far": self.consecutive_aux_failures,
                        "htf_trend_value": None,
                        "sector_rs_value": None,
                        "regime_bullish": None,
                        "bias_score": 0,
                        "components": {},
                    },
                    "score": {
                        "composite_score": None,
                        "grade": None,
                        "pillar_scores": {},
                        "modifiers": [],
                    },
                    "gates/slate": {
                        "friction_r": None,
                        "max_friction_r": float(self.cfg.get('risk', {}).get('max_friction_r', 0.15)) if self.cfg else 0.15,
                        "friction_pass": None,
                        "risk_pct": None,
                        "rr": None,
                        "status": "NONE",
                        "code": None,
                        "reason": None,
                        "haircut_applied": False,
                        "shares": 0,
                        "dollar_risk": 0.0,
                    },
                    "timing_ms": {"fetch": round(fetch_ms, 2), "engine": 0.0, "enrich": 0.0, "score": 0.0, "slate": 0.0},
                })

                if not success_seen:
                    consecutive_initial_failures += 1
                    if consecutive_initial_failures >= abort_k:
                        aborted = True
                        remaining = symbols[idx + 1:]
                        failed_symbols.extend(remaining)
                        for rem_sym in remaining:
                            ts_u, ts_l = get_iso_timestamps(actual_start_dt, market=market_id)
                            self.last_symbol_traces.append({
                                "scan_id": scan_id,
                                "symbol": rem_sym,
                                "ts_utc": ts_u,
                                "ts_local": ts_l,
                                "market": market_id,
                                "fetch": {
                                    "status": "SKIPPED",
                                    "error_type": "aborted",
                                    "error_message": "Scan aborted after consecutive initial empty frames",
                                    "attempts": 0,
                                    "latency_ms": 0.0,
                                    "bars_returned": 0,
                                    "first_bar_start": None,
                                    "last_bar_start": None,
                                    "last_bar_close_time": None,
                                    "last_bar_is_closed": False,
                                    "data_age_minutes": None,
                                    "duplicate_ts_count": 0,
                                    "nan_count": 0,
                                },
                                "engine": {
                                    "state_before": "IDLE",
                                    "state_after": "IDLE",
                                    "first_failed_gate": "skipped",
                                    "gate_details": None,
                                    "all_gates": {},
                                },
                                "enrich": {
                                    "htf_status": "NONE",
                                    "sector_status": "NONE",
                                    "regime_status": "NONE",
                                    "earnings_status": "NONE",
                                    "earnings_unverified": False,
                                    "provisional": False,
                                    "aux_fetch_failures_so_far": self.consecutive_aux_failures,
                                    "htf_trend_value": None,
                                    "sector_rs_value": None,
                                    "regime_bullish": None,
                                    "bias_score": 0,
                                    "components": {},
                                },
                                "score": {
                                    "composite_score": None,
                                    "grade": None,
                                    "pillar_scores": {},
                                    "modifiers": [],
                                },
                                "gates/slate": {
                                    "friction_r": None,
                                    "max_friction_r": float(self.cfg.get('risk', {}).get('max_friction_r', 0.15)) if self.cfg else 0.15,
                                    "friction_pass": None,
                                    "risk_pct": None,
                                    "rr": None,
                                    "status": "NONE",
                                    "code": None,
                                    "reason": None,
                                    "haircut_applied": False,
                                    "shares": 0,
                                    "dollar_risk": 0.0,
                                },
                                "timing_ms": {"fetch": 0.0, "engine": 0.0, "enrich": 0.0, "score": 0.0, "slate": 0.0},
                            })
                        break

        self.last_scan_stats = {
            "total": total_symbols,
            "with_data": with_data,
            "failed_symbols": failed_symbols,
            "aborted": aborted,
            "min_scan_completeness_pct": min_comp_pct,
            "scan_id": scan_id,
            "market": market_id,
            "actual_start_dt": actual_start_dt,
            "scheduled_time": scheduled_time,
            "duration_s": round(time.time() - start_mono, 2),
            "observed_bar_starts": observed_bar_starts,
            "error_type_counts": error_type_counts,
            "stage_durations": stage_durations,
        }

        self._scan_flushed = False

        if auto_flush:
            self.flush_symbol_traces(
                slate=None,
                market=market_id,
                scan_id=scan_id,
                scheduled_time=scheduled_time,
            )

        return out

    def flush_symbol_traces(
        self,
        slate=None,
        market: Optional[str] = None,
        scan_id: Optional[str] = None,
        scheduled_time: Optional[datetime] = None,
        telegram_sent: bool = False,
    ):
        """
        Finalizes per-symbol traces with slate allocations and writes them
        in one buffered flush to symbol_trace.jsonl and scan_summary.jsonl (§B, §C).
        """
        if getattr(self, "_scan_flushed", False):
            return

        market_id = (market or getattr(self, "last_scan_stats", {}).get("market") or "US").upper()
        sid = scan_id or getattr(self, "last_scan_id", None) or "scan"
        stats = getattr(self, "last_scan_stats", {})
        traces = getattr(self, "last_symbol_traces", [])

        # Integrate slate results if provided
        if slate is not None:
            approved_map = {}
            for t in getattr(slate, "selected_trades", []):
                sym = t.setup.symbol if getattr(t, "setup", None) else None
                if sym:
                    approved_map[sym] = t

            suppressed_map = {}
            for s_rec in getattr(slate, "suppressed_trades", []):
                suppressed_map[s_rec.symbol] = s_rec

            rejected_map = {}
            for r_rec in getattr(slate, "rejected_trades", []):
                rejected_map[r_rec.symbol] = r_rec

            watchlist_map = {}
            for w_rec in getattr(slate, "watchlist", []):
                w_sym = getattr(w_rec, "symbol", "") or (w_rec.get("symbol") if isinstance(w_rec, dict) else "")
                if w_sym:
                    watchlist_map[w_sym] = w_rec

            for tr in traces:
                sym = tr.get("symbol")
                st_after = tr.get("engine", {}).get("state_after")
                if sym in approved_map:
                    t = approved_map[sym]
                    tr["gates/slate"]["status"] = "PROVISIONAL" if getattr(t.setup, "provisional_data_incomplete", False) else "APPROVED"
                    tr["gates/slate"]["code"] = "APPROVED_CANDIDATE"
                    tr["gates/slate"]["reason"] = None
                    tr["gates/slate"]["haircut_applied"] = getattr(t, "haircut_applied", False)
                    tr["gates/slate"]["shares"] = getattr(t, "shares", 0)
                    tr["gates/slate"]["dollar_risk"] = getattr(t, "dollar_risk", 0.0)
                elif sym in suppressed_map:
                    s_rec = suppressed_map[sym]
                    tr["gates/slate"]["status"] = "SUPPRESSED"
                    tr["gates/slate"]["code"] = getattr(s_rec, "suppression_code", "SUPPRESSED")
                    tr["gates/slate"]["reason"] = getattr(s_rec, "reason", None)
                    if getattr(s_rec, "allocation", None):
                        tr["gates/slate"]["shares"] = s_rec.allocation.shares
                        tr["gates/slate"]["dollar_risk"] = s_rec.allocation.dollar_risk
                elif sym in rejected_map and st_after in ("BUY", "SELL"):
                    r_rec = rejected_map[sym]
                    tr["gates/slate"]["status"] = "REJECTED"
                    tr["gates/slate"]["code"] = getattr(r_rec, "rejection_code", "REJECTED")
                    tr["gates/slate"]["reason"] = getattr(r_rec, "reason", None)
                elif sym in watchlist_map:
                    w_rec = watchlist_map[sym]
                    b_score = getattr(w_rec, "bias_score", None) or tr.get("enrich", {}).get("bias_score", 0)
                    tr["gates/slate"]["status"] = "WATCHLIST"
                    tr["gates/slate"]["code"] = "WATCHLIST"
                    tr["gates/slate"]["reason"] = f"CONTRACTION setup (bias {b_score})"
                elif st_after == "CONTRACTION":
                    b_score = tr.get("enrich", {}).get("bias_score", 0)
                    tr["gates/slate"]["status"] = "NONE"
                    tr["gates/slate"]["code"] = "NONE"
                    tr["gates/slate"]["reason"] = "CONTRACTION (bias<3)"

        # Flush symbol trace batch to logs/run/<date>/symbol_trace.jsonl
        log_symbol_trace_batch(traces, market=market_id, scan_id=sid)

        # Build scan summary (§C)
        actual_start_dt = stats.get("actual_start_dt", datetime.now(timezone.utc))
        actual_end_dt = datetime.now(timezone.utc)
        sched_dt = scheduled_time or stats.get("scheduled_time") or actual_start_dt
        if sched_dt.tzinfo is None:
            sched_dt = sched_dt.replace(tzinfo=timezone.utc)

        start_delay = max(0.0, round((actual_start_dt - sched_dt).total_seconds(), 2))
        duration = stats.get("duration_s", round((actual_end_dt - actual_start_dt).total_seconds(), 2))

        # Expected vs observed last bar start
        observed_bar_starts = stats.get("observed_bar_starts", [])
        most_common_bar = None
        if observed_bar_starts:
            from collections import Counter
            most_common_bar = Counter(observed_bar_starts).most_common(1)[0][0]

        expected_bar = None
        stale_flag = "OK"
        try:
            from breakout_scanner.market_session import STANDARD_MARKET_CONFIGS, MarketSessionConfig
            if market_id in STANDARD_MARKET_CONFIGS:
                cfg_session = STANDARD_MARKET_CONFIGS[market_id]
                from breakout_scanner.market_session import MarketSession
                session = MarketSession(cfg_session)
                last_closed = session.get_last_closed_candle(actual_start_dt, timeframe="1h")
                if last_closed is not None:
                    expected_bar = last_closed.isoformat()
                    if most_common_bar and expected_bar and most_common_bar != expected_bar:
                        stale_flag = "STALE_DATA"
        except Exception:
            pass

        # Funnel Counts
        total_syms = stats.get("total", len(traces))
        with_data_cnt = stats.get("with_data", 0)
        failed_syms = stats.get("failed_symbols", [])
        completeness = round(with_data_cnt / total_syms * 100.0, 1) if total_syms > 0 else 0.0

        cnt_trend_ok = 0
        cnt_base_ok = 0
        cnt_contraction = 0
        cnt_watchlist = 0
        cnt_breakout = 0
        cnt_quality = 0
        cnt_friction = 0
        cnt_scored_gte_min = 0
        cnt_approved = 0
        suppressed_by_code: Dict[str, int] = {}
        rejected_by_code: Dict[str, int] = {}
        fresh_signals = 0
        inprogress_signals = 0
        provisional_cnt = 0
        aux_htf_fails = 0
        aux_sec_fails = 0
        aux_reg_fails = 0

        min_auto_score = float(self.cfg.get('confirmation_policy', {}).get('min_auto_entry_score', self.cfg.get('signal_quality', {}).get('min_auto_entry_score', 70))) if self.cfg else 70.0

        for tr in traces:
            eng = tr.get("engine", {})
            gates = eng.get("all_gates", {})
            st_after = eng.get("state_after")
            enrich = tr.get("enrich", {})
            score_d = tr.get("score", {})
            slate_d = tr.get("gates/slate", {})

            if tr.get("fetch", {}).get("status") == "OK":
                if gates.get("trend_sma50"):
                    cnt_trend_ok += 1
                if gates.get("base_range"):
                    cnt_base_ok += 1
                if st_after in ("CONTRACTION", "BUY", "SELL"):
                    cnt_contraction += 1
                if st_after in ("BUY", "SELL"):
                    cnt_breakout += 1
                    is_fresh_val = enrich.get("is_fresh", True)
                    if is_fresh_val:
                        fresh_signals += 1
                    else:
                        inprogress_signals += 1

                    c_score = score_d.get("composite_score")
                    if c_score is not None and c_score >= min_auto_score:
                        cnt_scored_gte_min += 1
                        cnt_quality += 1

                    if slate_d.get("friction_pass"):
                        cnt_friction += 1

                if enrich.get("provisional"):
                    provisional_cnt += 1
                if enrich.get("htf_status") in ("FAILED", "UNKNOWN"):
                    aux_htf_fails += 1
                if enrich.get("sector_status") in ("FAILED", "UNKNOWN"):
                    aux_sec_fails += 1
                if enrich.get("regime_status") in ("FAILED", "UNKNOWN"):
                    aux_reg_fails += 1

                st_slate = slate_d.get("status")
                if st_slate in ("APPROVED", "PROVISIONAL"):
                    cnt_approved += 1
                elif st_slate == "WATCHLIST":
                    cnt_watchlist += 1
                elif st_slate == "SUPPRESSED":
                    code = slate_d.get("code") or "SUPPRESSED"
                    suppressed_by_code[code] = suppressed_by_code.get(code, 0) + 1
                elif st_slate == "REJECTED" and st_after in ("BUY", "SELL"):
                    code = slate_d.get("code") or "REJECTED"
                    rejected_by_code[code] = rejected_by_code.get(code, 0) + 1

        date_str = get_exchange_date_str(market=market_id, dt_utc=actual_start_dt)
        scan_429, daily_429, total_attempts, total_retry_sleep_s = _STATE.get_scan_telemetry(sid, date_str)

        rss_mb = 0.0
        try:
            import resource
            max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss_mb = round(max_rss / (1024 * 1024), 2) if sys.platform == "darwin" else round(max_rss / 1024, 2)
        except Exception:
            pass

        summary_row = {
            "scan_id": sid,
            "market": market_id,
            "scheduled_time_utc": sched_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "actual_start_utc": actual_start_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "actual_end_utc": actual_end_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "start_delay_seconds": start_delay,
            "duration_s": duration,
            "expected_last_closed_bar_start": expected_bar,
            "observed_last_bar_start": most_common_bar,
            "stale_data_flag": stale_flag,
            "symbols_total": total_syms,
            "symbols_with_data": with_data_cnt,
            "symbols_failed": len(failed_syms),
            "failed_symbols_top10": failed_syms[:10],
            "completeness_pct": completeness,
            "aborted": stats.get("aborted", False),
            "error_type_counts": stats.get("error_type_counts", {}),
            "total_attempts": total_attempts,
            "total_retry_sleep_s": total_retry_sleep_s,
            "aux_failures": {
                "htf": aux_htf_fails,
                "sector": aux_sec_fails,
                "regime": aux_reg_fails,
            },
            "provisional_count": provisional_cnt,
            "funnel_counts": {
                "with_data": with_data_cnt,
                "trend_ok": cnt_trend_ok,
                "base_ok": cnt_base_ok,
                "contraction": cnt_contraction,
                "watchlist_count": cnt_watchlist,
                "breakout": cnt_breakout,
                "passed_quality": cnt_quality,
                "passed_friction": cnt_friction,
                "scored_gte_min_auto": cnt_scored_gte_min,
                "approved": cnt_approved,
                "suppressed_by_code": suppressed_by_code,
                "rejected_by_code": rejected_by_code,
            },
            "signals_fresh": fresh_signals,
            "signals_in_progress": inprogress_signals,
            "telegram_sent": telegram_sent,
            "per_stage_durations_ms": stats.get("stage_durations", {}),
            "process_rss_mb": rss_mb,
        }

        log_scan_summary(summary_row, market=market_id, dt=actual_start_dt)
        self._scan_flushed = True
        self.last_scan_summary = summary_row

    def enrich_bias(self, setup: Setup, df, period, interval):
        import logging
        logger = logging.getLogger("breakout_scanner.scanner")
        
        score = 0
        target_dir = setup.direction if setup.direction in (1, -1) else (1 if setup.trend >= 0 else -1)
        
        # Initialize data provenance statuses
        setup.htf_data_status = "PENDING"
        setup.sector_data_status = "PENDING"
        setup.regime_data_status = "PENDING"
        setup.provisional_data_incomplete = False
        proxy_df = pd.DataFrame()

        # Pillar 1: 1H Trend Alignment
        if target_dir == 1 and setup.trend == 1:
            score += 1
        elif target_dir == -1 and setup.trend == -1:
            score += 1
            
        try:
            import yfinance as yf
            import datetime
            
            # ----------------------------------------------------
            # 1. Higher-Timeframe (1D) Confluence Check & Cache TTL
            # ----------------------------------------------------
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            if not hasattr(self.provider, 'daily_cache'):
                self.provider.daily_cache = {}
            if not hasattr(self.provider, 'daily_cache_ts'):
                self.provider.daily_cache_ts = {}

            def get_cached_daily(sym: str) -> pd.DataFrame:
                cached_df = self.provider.daily_cache.get(sym)
                cached_ts = self.provider.daily_cache_ts.get(sym)
                is_stale = False
                if cached_df is None or cached_ts is None:
                    is_stale = True
                else:
                    elapsed = (now_utc - cached_ts).total_seconds()
                    # Invalidate if older than 4 hours or if calendar day/session has advanced
                    if elapsed > 4 * 3600 or now_utc.date() != cached_ts.date():
                        is_stale = True
                if is_stale:
                    cb_val = getattr(self, "consecutive_aux_failures", 0)
                    if isinstance(cb_val, (int, float)) and cb_val >= 3:
                        logger.warning(
                            f"⚠️ [CircuitBreaker] Auxiliary benchmark fetches throttled/failed >= 3 times. "
                            f"Bypassing network download for {sym}; marking provisional."
                        )
                        if cached_df is not None and not cached_df.empty:
                            setup.htf_data_status = "STALE"
                            return cached_df
                        setup.htf_data_status = "FAILED"
                        return pd.DataFrame()
                    try:
                        fresh_df = self.provider.candles(sym, period='1y', interval='1d')
                        if fresh_df is not None and not fresh_df.empty:
                            self.consecutive_aux_failures = 0
                            self.provider.daily_cache[sym] = fresh_df
                            self.provider.daily_cache_ts[sym] = now_utc
                            return fresh_df
                        else:
                            self.consecutive_aux_failures = getattr(self, "consecutive_aux_failures", 0) + 1
                            if cached_df is not None and not cached_df.empty:
                                setup.htf_data_status = "STALE"
                                return cached_df
                            return pd.DataFrame()
                    except Exception as e:
                        self.consecutive_aux_failures = getattr(self, "consecutive_aux_failures", 0) + 1
                        logger.warning(f"⚠️ [DataHealth] HTF 1D candles fetch failed for {sym}: {e}")
                        if cached_df is not None and not cached_df.empty:
                            setup.htf_data_status = "STALE"
                            return cached_df
                        return pd.DataFrame()
                return cached_df
                
            daily_df = get_cached_daily(setup.symbol)
            if daily_df is not None and not daily_df.empty and len(daily_df) >= 55:
                if setup.htf_data_status != "STALE":
                    setup.htf_data_status = "OK"
                daily_sma = daily_df['Close'].rolling(50).mean()
                sma50_curr = daily_sma.iloc[-1]
                sma50_prev5 = daily_sma.iloc[-5]
                daily_close = daily_df['Close'].iloc[-1]
                
                # Shadow Feature: Daily SMA50 alignment
                setup.is_above_daily_sma50 = bool(daily_close > sma50_curr)
                
                # SMA50 slope check: SMA50[-1] > SMA50[-5]
                slope_up = sma50_curr > sma50_prev5
                slope_down = sma50_curr < sma50_prev5
                
                if daily_close > sma50_curr and slope_up:
                    setup.htf_trend = 1
                elif daily_close < sma50_curr and slope_down:
                    setup.htf_trend = -1
                else:
                    setup.htf_trend = 0
                
                # Check HTF alignment with current setup direction
                if target_dir == 1 and setup.htf_trend == 1:
                    setup.htf_confirmed = True
                elif target_dir == -1 and setup.htf_trend == -1:
                    setup.htf_confirmed = True
                else:
                    setup.htf_confirmed = False
            else:
                setup.htf_data_status = "FAILED"
                setup.provisional_data_incomplete = True
                logger.warning(f"⚠️ [DataHealth] HTF 1D data missing or insufficient for {setup.symbol}")

            # ----------------------------------------------------
            # 2. Sector-Level Relative Strength (Forward-Only Alignment)
            # ----------------------------------------------------
            sector_name, benchmark_symbol = get_sector_benchmark(setup.symbol)
            setup.sector_name = sector_name
            proxy = benchmark_symbol
                
            cache_key = f"{proxy}_{period}_{interval}"
            if not hasattr(self.provider, 'market_cache'):
                self.provider.market_cache = {}
            if not hasattr(self.provider, 'market_cache_ts'):
                self.provider.market_cache_ts = {}

            def get_cached_proxy(key: str, p_sym: str, p_per: str, p_int: str) -> pd.DataFrame:
                cached_df = self.provider.market_cache.get(key)
                cached_ts = self.provider.market_cache_ts.get(key)
                is_stale = False
                if cached_df is None or cached_ts is None:
                    is_stale = True
                else:
                    elapsed = (now_utc - cached_ts).total_seconds()
                    if elapsed > 4 * 3600 or now_utc.date() != cached_ts.date():
                        is_stale = True
                if is_stale:
                    cb_val = getattr(self, "consecutive_aux_failures", 0)
                    if isinstance(cb_val, (int, float)) and cb_val >= 3:
                        logger.warning(
                            f"⚠️ [CircuitBreaker] Auxiliary benchmark fetches throttled/failed >= 3 times. "
                            f"Bypassing network download for {p_sym}; marking provisional."
                        )
                        if cached_df is not None and not cached_df.empty:
                            setup.sector_data_status = "STALE"
                            return cached_df
                        setup.sector_data_status = "FAILED"
                        return pd.DataFrame()
                    try:
                        fresh_df = self.provider.candles(p_sym, period=p_per, interval=p_int)
                        if fresh_df is not None and not fresh_df.empty:
                            self.consecutive_aux_failures = 0
                            self.provider.market_cache[key] = fresh_df
                            self.provider.market_cache_ts[key] = now_utc
                            return fresh_df
                        else:
                            self.consecutive_aux_failures = getattr(self, "consecutive_aux_failures", 0) + 1
                            if cached_df is not None and not cached_df.empty:
                                setup.sector_data_status = "STALE"
                                return cached_df
                            return pd.DataFrame()
                    except Exception as e:
                        self.consecutive_aux_failures = getattr(self, "consecutive_aux_failures", 0) + 1
                        logger.warning(f"⚠️ [DataHealth] Sector proxy fetch failed for {p_sym}: {e}")
                        if cached_df is not None and not cached_df.empty:
                            setup.sector_data_status = "STALE"
                            return cached_df
                        return pd.DataFrame()
                return cached_df
            
            proxy_df = get_cached_proxy(cache_key, proxy, period, interval)
            
            # Relative Strength without backward-filling future data
            if proxy_df is not None and not proxy_df.empty and len(df) > 20 and len(proxy_df) > 20:
                if setup.sector_data_status != "STALE":
                    setup.sector_data_status = "OK"
                stock_tail = df['Close'].iloc[-20:]
                proxy_aligned = proxy_df['Close'].reindex(stock_tail.index).ffill()
                
                # Drop leading NaNs if proxy history started after stock_tail (NO bfill!)
                valid_idx = proxy_aligned.dropna().index
                if len(valid_idx) >= 10:
                    proxy_aligned = proxy_aligned.loc[valid_idx]
                    stock_aligned = stock_tail.loc[valid_idx]
                    
                    if proxy_aligned.iloc[0] > 0 and stock_aligned.iloc[0] > 0:
                        stock_returns = stock_aligned / stock_aligned.iloc[0]
                        proxy_returns = proxy_aligned / proxy_aligned.iloc[0]
                        
                        rs_delta = (stock_returns.iloc[-1] - proxy_returns.iloc[-1]) * 100.0
                        setup.sector_rs_pct = round(rs_delta, 2)
                        
                        # Symmetric RS evaluation
                        if target_dir == 1 and stock_returns.iloc[-1] > proxy_returns.iloc[-1]:
                            score += 1
                        elif target_dir == -1 and stock_returns.iloc[-1] < proxy_returns.iloc[-1]:
                            score += 1

                        # Step 3: RS Momentum Slope (Linear regression slope over trailing bars)
                        rs_ratio = (stock_returns / proxy_returns.replace(0, np.nan)).dropna()
                        if len(rs_ratio) >= 3:
                            tail = rs_ratio.iloc[-min(len(rs_ratio), 5):]
                            x = np.arange(len(tail))
                            slope, _ = np.polyfit(x, tail.values, 1)
                            setup.rs_slope = round(float(slope * 100.0), 2)
                            setup.rs_trend = "↗" if setup.rs_slope > 0.05 else ("↘" if setup.rs_slope < -0.05 else "→")
            else:
                setup.sector_data_status = "FAILED"
                setup.provisional_data_incomplete = True
                logger.warning(f"⚠️ [DataHealth] Sector proxy data missing for {setup.symbol}")

            # ----------------------------------------------------
            # 3. Institutional Consensus / Macro Regime
            # ----------------------------------------------------
            from .confluence import is_non_equity_symbol
            is_crypto = setup.symbol.endswith('-USD') or setup.symbol.endswith('-USDT')
            is_nse = setup.symbol.endswith('.NS') or setup.symbol.endswith('.BO')
            is_macro = is_non_equity_symbol(setup.symbol)

            if is_crypto:
                # Crypto: Bitcoin Macro Regime (Daily BTC-USD trading above upward-sloping 50-day SMA)
                btc_daily = get_cached_daily('BTC-USD')
                btc_bullish = False
                if btc_daily is not None and not btc_daily.empty and len(btc_daily) >= 55:
                    btc_sma = btc_daily['Close'].rolling(50).mean()
                    btc_bullish = (btc_daily['Close'].iloc[-1] > btc_sma.iloc[-1]) and (btc_sma.iloc[-1] > btc_sma.iloc[-5])
                elif not proxy_df.empty and len(proxy_df) >= 55:
                    btc_sma = proxy_df['Close'].rolling(50).mean()
                    btc_bullish = (proxy_df['Close'].iloc[-1] > btc_sma.iloc[-1]) and (btc_sma.iloc[-1] > btc_sma.iloc[-5])
                
                setup.regime_data_status = "OK"
                if target_dir == 1 and btc_bullish:
                    score += 1
                elif target_dir == -1 and not btc_bullish:
                    score += 1

            elif is_nse:
                # NSE: Nifty 50 Trend Regime
                nifty_bullish = False
                nifty_daily = get_cached_daily('^NSEI')
                if nifty_daily is not None and not nifty_daily.empty and len(nifty_daily) >= 55:
                    nifty_sma = nifty_daily['Close'].rolling(50).mean()
                    nifty_bullish = (nifty_daily['Close'].iloc[-1] > nifty_sma.iloc[-1]) and (nifty_sma.iloc[-1] > nifty_sma.iloc[-5])
                elif not proxy_df.empty and len(proxy_df) >= 55:
                    nifty_sma = proxy_df['Close'].rolling(50).mean()
                    nifty_bullish = (proxy_df['Close'].iloc[-1] > nifty_sma.iloc[-1]) and (nifty_sma.iloc[-1] > nifty_sma.iloc[-5])
                
                setup.regime_data_status = "OK"
                if target_dir == 1 and nifty_bullish:
                    score += 1
                elif target_dir == -1 and not nifty_bullish:
                    score += 1

            elif is_macro:
                # Macro & Derivative Instruments (Commodities, Futures, Forex, Global Indices)
                if not proxy_df.empty and len(proxy_df) >= 55:
                    macro_sma = proxy_df['Close'].rolling(50).mean()
                    macro_bullish = (proxy_df['Close'].iloc[-1] > macro_sma.iloc[-1]) and (macro_sma.iloc[-1] > macro_sma.iloc[-5])
                    setup.regime_data_status = "OK"
                    if target_dir == 1 and macro_bullish:
                        score += 1
                    elif target_dir == -1 and not macro_bullish:
                        score += 1

            else:
                # US Equities: Market Regime via Benchmark (SPY / QQQ / Sector Proxy)
                spy_daily = get_cached_daily('SPY')
                if spy_daily is None or spy_daily.empty:
                    spy_daily = get_cached_daily('QQQ')

                regime_bullish = False
                if spy_daily is not None and not spy_daily.empty and len(spy_daily) >= 55:
                    spy_sma = spy_daily['Close'].rolling(50).mean()
                    regime_bullish = (spy_daily['Close'].iloc[-1] > spy_sma.iloc[-1]) and (spy_sma.iloc[-1] > spy_sma.iloc[-5])
                    setup.regime_data_status = "OK"
                elif proxy_df is not None and not proxy_df.empty and len(proxy_df) >= 55:
                    spy_sma = proxy_df['Close'].rolling(50).mean()
                    regime_bullish = (proxy_df['Close'].iloc[-1] > spy_sma.iloc[-1]) and (spy_sma.iloc[-1] > spy_sma.iloc[-5])
                    setup.regime_data_status = "OK"
                else:
                    setup.regime_data_status = "FAILED"
                    setup.provisional_data_incomplete = True

                if setup.regime_data_status == "OK":
                    if target_dir == 1 and regime_bullish:
                        score += 1
                    elif target_dir == -1 and not regime_bullish:
                        score += 1

        except Exception as e:
            logger.error(f"❌ [DataHealth] Unexpected error in enrich_bias for {setup.symbol}: {e}", exc_info=True)
            setup.provisional_data_incomplete = True
            
        setup.bias_score = score

        # Phase 3: Confluence & Context Layer
        try:
            from .confluence import enrich_confluence_and_context
            enrich_confluence_and_context(setup, df, proxy_df=proxy_df, cfg=self.cfg)
        except Exception as e:
            logger.warning(f"⚠️ [DataHealth] Confluence enrichment failed for {setup.symbol}: {e}")

        quality_cfg = self.cfg.get('signal_quality', {}) if self.cfg else None
        context_cfg = self.cfg.get('confluence_context', {}) if self.cfg else None
        c_score, c_grade = calculate_composite_score(setup, quality_cfg=quality_cfg, context_cfg=context_cfg)
        setup.composite_score = c_score
        setup.composite_grade = c_grade

        # Phase 2/3: Tiered Follow-Through Confirmation Policy (P0-2)
        confirmation_cfg = self.cfg.get('confirmation_policy', {}) if self.cfg else {}
        min_auto_entry_score = int(confirmation_cfg.get('min_auto_entry_score', 70))
        
        if setup.retest_confirmed:
            setup.requires_confirmation = False
            setup.confirmation_status = "CONFIRMED"
        elif setup.provisional_data_incomplete:
            # Exemption: Degraded external data does not suppress a structurally sound technical breakout!
            setup.requires_confirmation = False
            setup.confirmation_status = "PROVISIONAL - DATA INCOMPLETE"
        elif c_score >= min_auto_entry_score:
            setup.requires_confirmation = False
            setup.confirmation_status = "CONFIRMED"
        else:
            # Score < 85: requires follow-through confirmation
            # Check if breakout already occurred in prior closed bars and breached pivot
            has_followed_through = False
            if (
                setup.state in (State.BUY, State.SELL)
                and setup.bars_since_breakout is not None
                and setup.bars_since_breakout >= 1
                and setup.breakout_index is not None
                and not df.empty
            ):
                subsequent_df = df.iloc[setup.breakout_index + 1:]
                if not subsequent_df.empty:
                    entry_lvl = setup.entry or setup.breakout_level
                    if setup.direction == 1 and entry_lvl is not None:
                        has_followed_through = bool(float(subsequent_df['High'].max()) > (entry_lvl * 1.0001))
                    elif setup.direction == -1 and entry_lvl is not None:
                        has_followed_through = bool(float(subsequent_df['Low'].min()) < (entry_lvl * 0.9999))

            if has_followed_through:
                setup.requires_confirmation = False
                setup.confirmation_status = "CONFIRMED"
            else:
                setup.requires_confirmation = True
                setup.confirmation_status = "AWAITING_FOLLOW_THROUGH"

        # Phase 1: Options Policy Tag
        setup.underlying_validated = False
        setup.options_eligible = False
        setup.options_status = "UNDERLYING_UNVALIDATED"

    def report(self, setups: List[Setup]):
        stats = getattr(self, "last_scan_stats", None)
        if stats:
            total_sym = stats.get("total", 0)
            with_data = stats.get("with_data", 0)
            failed_syms = stats.get("failed_symbols", [])
            min_pct = stats.get("min_scan_completeness_pct", 90)
            aborted = stats.get("aborted", False)
            pct = (with_data / total_sym * 100) if total_sym > 0 else 0.0
            if total_sym > 0 and (pct < min_pct or aborted):
                failed_str = ", ".join(failed_syms[:10]) + ("..." if len(failed_syms) > 10 else "")
                print(f"\n\033[91m⚠️  SCAN INCOMPLETE: {with_data}/{total_sym} symbols had data (failed: {failed_str}). Do NOT read 'no signals' as a quiet market.\033[0m")

        active_setups = [s for s in setups if s.state in (State.BUY, State.SELL)]
        fresh_setups = [s for s in active_setups if s.is_fresh_entry]
        progress_setups = [s for s in active_setups if not s.is_fresh_entry]

        prov_count = sum(1 for s in setups if getattr(s, 'provisional_data_incomplete', False))
        if prov_count > 0:
            print(f"\n\033[93m⚠️  DATA DEGRADED: {prov_count}/{len(setups)} symbol(s) evaluated on provisional technicals (external data incomplete).\033[0m")

        def safe_sort_active(s: Setup):
            score = s.composite_score if (s.composite_score is not None and np.isfinite(s.composite_score)) else 0
            if (s.entry is not None and s.current_price is not None and 
                np.isfinite(s.entry) and np.isfinite(s.current_price) and s.entry > 0):
                prox = -abs(s.entry - s.current_price) / s.entry
                if not np.isfinite(prox):
                    prox = -999.0
            else:
                prox = -999.0
            return (score, prox)

        fresh_setups.sort(key=safe_sort_active, reverse=True)
        progress_setups.sort(key=safe_sort_active, reverse=True)

        print("\n=======================================================")
        print(" SECTION 2A: 🟢 FRESH ACTIONABLE ENTRIES (Playable Now)")
        print("=======================================================")
        if not fresh_setups:
            print("  (None)")
        else:
            for s in fresh_setups:
                print(self.format_active_setup(s, is_fresh=True))

        print("\n=======================================================")
        print(" SECTION 2B: 🔒 TRADES IN PROGRESS (Holders Only — Do Not Chase)")
        print("=======================================================")
        if not progress_setups:
            print("  (None)")
        else:
            for s in progress_setups:
                print(self.format_active_setup(s, is_fresh=False))

        failed_setups = [s for s in setups if s.state == State.FAILED]
        if failed_setups:
            print("\n=======================================================")
            print(" SECTION 2C: ⚠️ FAILED BREAKOUT EXITS (Exit Signal for Holders)")
            print("=======================================================")
            for s in failed_setups:
                d_str = "BUY" if s.direction == 1 else "SELL"
                pnl_str = f"{s.pnl_pct:+.1f}%" if s.pnl_pct is not None else "N/A"
                print(f"  ❌ \033[91m{s.symbol:<8}\033[0m | Was {d_str} | PnL: {pnl_str} | Alert: {s.failure_reason or 'Fell back inside base'}")

        print("\n=======================================================")
        print(" SECTION 1: ◉ VCP BASE (Actively Ready To Provide Signal)")
        print("=======================================================")
        
        base_setups = [s for s in setups if s.state == State.CONTRACTION and s.bias_score >= 3]
        base_setups.sort(key=lambda s: (s.composite_score if (s.composite_score is not None and np.isfinite(s.composite_score)) else 0, s.bias_score if (s.bias_score is not None and np.isfinite(s.bias_score)) else 0), reverse=True)
        if not base_setups:
            print("  (None)")
        else:
            for s in base_setups:
                res = f"\033[91mResistance: {s.resistance:.5g}\033[0m"
                sup = f"\033[92mSupport: {s.support:.5g}\033[0m"
                width = f"Width: {s.base_width_pct:.1f}%" if s.base_width_pct is not None else ""
                coil = f"\033[94m🗜️ Coil: {s.decay_ratio:.1f}x\033[0m" if (s.decay_ratio is not None and s.tightening_confirmed) else ""
                vdu = f"\033[96m💧 VDU: {s.vdu_ratio:.1f}x\033[0m" if s.vdu_confirmed else ""
                trend_sym = f" {s.rs_trend}" if s.rs_trend else ""
                sector = f"[{s.sector_name} RS: {s.sector_rs_pct:+.1f}%{trend_sym}]" if s.sector_name and s.sector_rs_pct is not None else (f"[{s.sector_name}]" if s.sector_name else "")
                
                # Multi-TF HTF Badging
                if s.htf_confirmed and getattr(s, 'htf_4h_confirmed', False):
                    htf = "\033[93m⭐⭐ Multi-TF (1D+4H)\033[0m"
                elif s.htf_confirmed:
                    htf = "\033[93m⭐ 1D Confirmed\033[0m"
                elif getattr(s, 'htf_4h_confirmed', False):
                    htf = "\033[93m⭐ 4H Confirmed\033[0m"
                else:
                    htf = ""

                grade_badge = f"\033[93m🏆 Grade: {s.composite_grade} [{s.composite_score}]\033[0m" if s.composite_grade else ""
                
                target_dir = s.direction if s.direction in (1, -1) else (1 if s.trend >= 0 else -1)
                bias_label = "Upside" if target_dir == 1 else "Downside"
                if s.bias_score >= 2:
                    bias_text = f"Strong {bias_label} Bias ({s.bias_score}/3)"
                    bias_color = "\033[92m" if target_dir == 1 else "\033[91m"
                elif s.bias_score == 1:
                    bias_text = f"Weak {bias_label} Bias ({s.bias_score}/3)"
                    bias_color = "\033[93m"
                else:
                    bias_text = f"Neutral/Counter Bias ({s.bias_score}/3)"
                    bias_color = "\033[90m"
                    
                if s.earnings_warning:
                    bias_text += " ⚠️ EARNINGS RISK"
                elif getattr(s, "earnings_unverified", False):
                    bias_text += " ⚠️ EARNINGS UNVERIFIED — check broker calendar"
                    
                bias_str = f"{bias_color}Bias: {bias_text}\033[0m"
                parts = [f"  • {s.symbol:9}", res, sup]
                if width:
                    parts.append(width)
                if coil:
                    parts.append(coil)
                if vdu:
                    parts.append(vdu)
                if sector:
                    parts.append(sector)
                if htf:
                    parts.append(htf)
                if grade_badge:
                    parts.append(grade_badge)
                parts.append(bias_str)
                print(" | ".join(parts))

        # Phase 4: Portfolio Guard Actionable Trade Slate
        if self.cfg and self.cfg.get('portfolio_guard', {}).get('enabled', False):
            try:
                from .portfolio_guard import generate_actionable_slate, format_trade_slate_cli
                slate = generate_actionable_slate(setups, config=self.cfg)
                print(format_trade_slate_cli(slate))
            except Exception:
                pass

        print("\n")

    def format_active_setup(self, s: Setup, is_fresh: bool) -> str:
        tag = "🔄 RETEST" if s.retest_confirmed else "🚀 BREAKOUT"
        dir_color = "\033[92m" if s.direction == 1 else "\033[91m"
        dir_arrow = "▲ BUY" if s.direction == 1 else "▼ SELL"
        dir_str = f"{dir_color}{dir_arrow} [{tag}]\033[0m"
        
        cp_str = f"CP: {s.current_price:.5g}" if s.current_price is not None else "CP: N/A"
        entry = f"Entry: {s.entry:.5g}" if s.entry is not None else "Entry: N/A"
        stop = f"Stop: {s.stop:.5g}" if s.stop is not None else "Stop: N/A"
        targets = f"TP1: {s.tp1:.5g} | TP2: {s.tp2:.5g}" if (s.tp1 is not None and s.tp2 is not None) else ""
        risk_str = f"Risk: {s.risk_pct:.2f}%" if s.risk_pct is not None else ""
        frict_str = f"[Cost: {s.friction_r:.2f}R]" if s.friction_r is not None else ""
        rvol_str = f"[Vol: {s.rvol:.1f}x]" if s.rvol is not None else ""
        
        pnl_str = ""
        pnl_sign = ""
        if s.pnl_pct is not None:
            pnl_color = "\033[92m" if s.pnl_pct >= 0 else "\033[91m"
            pnl_sign = "+" if s.pnl_pct >= 0 else ""
            pnl_str = f"{pnl_color}P&L: {pnl_sign}{s.pnl_pct:.2f}%\033[0m"
            
        trend_sym = f" {s.rs_trend}" if s.rs_trend else ""
        sector = f"[{s.sector_name}{trend_sym}]" if s.sector_name else ""
        htf = "\033[93m⭐ 1D\033[0m" if s.htf_confirmed else ""
        grade_badge = f"\033[93m🏆 {s.composite_grade} [{s.composite_score}]\033[0m" if s.composite_grade else ""
        
        parts = [f"  • {s.symbol:9}", dir_str, cp_str, entry, stop]
        if getattr(s, "earnings_unverified", False):
            parts.append("\033[93m⚠️ EARNINGS UNVERIFIED — check broker calendar\033[0m")
        if getattr(s, "provisional_data_incomplete", False):
            parts.append("\033[93m[PROVISIONAL]\033[0m")
        if risk_str:
            parts.append(risk_str)
        if frict_str:
            parts.append(frict_str)
        if pnl_str:
            parts.append(pnl_str)
        if rvol_str:
            parts.append(rvol_str)
        if sector:
            parts.append(sector)
        if htf:
            parts.append(htf)
        if grade_badge:
            parts.append(grade_badge)
            
        if not is_fresh:
            pnl_val = f"{pnl_sign}{s.pnl_pct:.2f}%" if s.pnl_pct is not None else ""
            if s.is_tp1_reached:
                curr = s.current_price if s.current_price is not None else s.entry
                at_tp1 = False
                if curr is not None and s.tp1 is not None:
                    at_tp1 = (curr >= s.tp1) if s.direction == 1 else (curr <= s.tp1)
                if at_tp1:
                    parts.append(f"\033[91m[TP1 HIT: {pnl_val} - DO NOT ENTER]\033[0m")
                else:
                    parts.append(f"\033[91m[TP1 REACHED (Current: {pnl_val}) - DO NOT ENTER]\033[0m")
            else:
                if s.pnl_pct is not None and s.pnl_pct < 0:
                    parts.append(f"\033[93m[IN PROGRESS: {pnl_val} - DO NOT CHASE]\033[0m")
                else:
                    parts.append(f"\033[93m[EXTENDED: {pnl_val} - DO NOT CHASE]\033[0m")
        else:
            parts.append("\033[92m[ENTER NOW]\033[0m")
            
        if targets:
            parts.append(targets)
        return " | ".join(parts)


