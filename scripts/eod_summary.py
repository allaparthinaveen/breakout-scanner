#!/usr/bin/env python3
"""
End-of-Day (EOD) Session Summary Generator (§F.1).
==================================================
Aggregates a day's scan summaries, symbol decision traces, and event journals
into structured JSON and readable Markdown diagnostics.

Usage:
  python3 scripts/eod_summary.py --market nse --date YYYY-MM-DD
"""

import os
import sys

# Ensure project root is in sys.path when executed directly as a script
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
import gzip
import argparse
from datetime import datetime, timezone
from collections import Counter
from typing import Dict, Any, List, Optional
from breakout_scanner.run_logger import get_exchange_date_str, redact_secrets, truncate_val


def read_jsonl_file(filepath: str) -> List[dict]:
    """Reads lines from a .jsonl or .jsonl.gz file."""
    rows = []
    actual_path = filepath
    if not os.path.exists(actual_path) and os.path.exists(filepath + ".gz"):
        actual_path = filepath + ".gz"

    if not os.path.exists(actual_path):
        return rows

    try:
        if actual_path.endswith(".gz"):
            with gzip.open(actual_path, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        else:
            with open(actual_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
    except Exception as e:
        sys.stderr.write(f"Warning reading {actual_path}: {e}\n")
    return rows


def generate_eod_summary(
    market: str,
    date_str: str,
    base_dir: str = "logs/run",
    ledger_path: str = "logs/forward_test_ledger.jsonl",
) -> Dict[str, Any]:
    """
    Computes comprehensive EOD analytics across all scans, symbol traces, and events.
    """
    session_dir = os.path.join(base_dir, date_str)
    all_scan_summaries = read_jsonl_file(os.path.join(session_dir, "scan_summary.jsonl"))
    all_symbol_traces = read_jsonl_file(os.path.join(session_dir, "symbol_trace.jsonl"))
    all_events = read_jsonl_file(os.path.join(session_dir, "events.jsonl"))
    ledger_rows = read_jsonl_file(ledger_path)

    market_upper = market.upper()
    scan_summaries = [s for s in all_scan_summaries if s.get("market", "").upper() == market_upper]
    symbol_traces = [
        t for t in all_symbol_traces
        if t.get("market", "").upper() == market_upper
        or (not t.get("market") and ((market_upper == "NSE" and str(t.get("symbol", "")).endswith(".NS")) or (market_upper == "US" and not str(t.get("symbol", "")).endswith(".NS") and "-" not in str(t.get("symbol", "")))))
    ]
    events = [
        e for e in all_events
        if e.get("market", "").upper() in (market_upper, "ALL", "")
        or market_upper in str(e.get("market", "")).upper()
        or (not e.get("market"))
    ]

    # Filter ledger rows for this market and date if possible
    market_ledger = []
    for row in ledger_rows:
        row_time = str(row.get("breakout_time") or row.get("recorded_at") or "")
        if date_str in row_time or market.lower() in str(row.get("symbol", "")).lower():
            market_ledger.append(row)

    outcomes_path = os.path.join(os.path.dirname(ledger_path), "forward_test_outcomes.jsonl")
    outcomes_map = {o.get("signal_id"): o for o in read_jsonl_file(outcomes_path) if o.get("signal_id")}

    shadow_rows = [
        row for row in market_ledger
        if row.get("bucket") == "LATE_CHASE_SHADOW"
    ]
    late_chase_shadow_entries = []
    for sr in shadow_rows:
        sig_id = sr.get("signal_id")
        out = outcomes_map.get(sig_id, {})
        entry_item = {
            "symbol": sr.get("symbol"),
            "signal_id": sig_id,
            "timestamp": sr.get("timestamp") or sr.get("breakout_time"),
            "pivot": sr.get("pivot") or sr.get("price", {}).get("pivot"),
            "signal_price": sr.get("signal_price") or sr.get("price", {}).get("signal_price") or sr.get("price", {}).get("current"),
            "extension_pct": sr.get("extension_pct") or sr.get("price", {}).get("extension_pct"),
            "tolerance_pct": sr.get("tolerance_pct") or sr.get("price", {}).get("tolerance_pct"),
            "atr": sr.get("atr") or sr.get("price", {}).get("atr"),
            "score": sr.get("score") or sr.get("quant_score"),
            "grade": sr.get("grade") or sr.get("quant_grade"),
            "stop": sr.get("stop") or sr.get("price", {}).get("stop"),
            "tp1": sr.get("tp1") or sr.get("price", {}).get("tp1"),
            "tp2": sr.get("tp2") or sr.get("price", {}).get("tp2"),
            "status": out.get("status", "PENDING"),
            "hypothetical_r": out.get("hypothetical_r"),
            "pullback_filled": out.get("pullback_filled"),
            "pullback_r": out.get("pullback_to_pivot_fill", {}).get("hypothetical_r"),
        }
        late_chase_shadow_entries.append(entry_item)

    # 1. Scans scheduled vs executed vs completed
    job_fires = [e for e in events if e.get("event") == "job_fire" and (market_upper in str(e.get("market", "")).upper() or e.get("market") in ("ALL", ""))]
    job_skips = [e for e in events if e.get("event") == "job_skipped" and (market_upper in str(e.get("market", "")).upper() or e.get("market") in ("ALL", ""))]
    completed_scans = len(scan_summaries)
    executed_scans = len(job_fires) if job_fires else completed_scans
    scheduled_scans = executed_scans + len(job_skips)

    # 2. Avg/max start delay
    delays = [s.get("start_delay_seconds", 0.0) for s in scan_summaries]
    avg_delay = round(sum(delays) / len(delays), 2) if delays else 0.0
    max_delay = max(delays) if delays else 0.0

    # 3. Completeness stats per scan
    completeness_stats = []
    for s in scan_summaries:
        completeness_stats.append({
            "scan_id": s.get("scan_id"),
            "completeness_pct": s.get("completeness_pct", 0.0),
            "with_data": s.get("symbols_with_data", 0),
            "total": s.get("symbols_total", 0),
            "aborted": s.get("aborted", False),
        })

    # 4. Error type totals and 429 count
    error_totals: Dict[str, int] = {}
    for s in scan_summaries:
        for err_k, err_v in s.get("error_type_counts", {}).items():
            error_totals[err_k] = error_totals.get(err_k, 0) + err_v

    count_429 = sum(
        1 for e in events
        if e.get("event") == "yf_call" and ("429" in str(e.get("error_class", "")) or "429" in str(e.get("error_message", "")))
    )
    if "429" in error_totals:
        count_429 = max(count_429, error_totals["429"])

    # 5. Symbols that failed most (top 15)
    fail_counter = Counter()
    for tr in symbol_traces:
        if tr.get("fetch", {}).get("status") in ("EMPTY", "ERROR", "SKIPPED"):
            fail_counter[tr.get("symbol")] += 1
    top_failed_symbols = [{"symbol": sym, "failures": cnt} for sym, cnt in fail_counter.most_common(15)]

    # 6. Full funnel totals for the day
    funnel_totals = {
        "with_data": sum(s.get("funnel_counts", {}).get("with_data", 0) for s in scan_summaries),
        "trend_ok": sum(s.get("funnel_counts", {}).get("trend_ok", 0) for s in scan_summaries),
        "base_ok": sum(s.get("funnel_counts", {}).get("base_ok", 0) for s in scan_summaries),
        "contraction": sum(s.get("funnel_counts", {}).get("contraction", 0) for s in scan_summaries),
        "watchlist_count": sum(s.get("funnel_counts", {}).get("watchlist_count", 0) for s in scan_summaries),
        "breakout": sum(s.get("funnel_counts", {}).get("breakout", 0) for s in scan_summaries),
        "passed_quality": sum(s.get("funnel_counts", {}).get("passed_quality", 0) for s in scan_summaries),
        "passed_friction": sum(s.get("funnel_counts", {}).get("passed_friction", 0) for s in scan_summaries),
        "scored_gte_min_auto": sum(s.get("funnel_counts", {}).get("scored_gte_min_auto", 0) for s in scan_summaries),
        "approved": sum(s.get("funnel_counts", {}).get("approved", 0) for s in scan_summaries),
    }

    # 7. Every signal (Approved / Provisional)
    signals = []
    seen_sigs = set()
    for tr in symbol_traces:
        st = tr.get("gates/slate", {}).get("status")
        sym = tr.get("symbol")
        if st in ("APPROVED", "PROVISIONAL"):
            key = f"{sym}_{tr.get('ts_utc')}"
            if key not in seen_sigs:
                seen_sigs.add(key)
                signals.append({
                    "symbol": sym,
                    "time": tr.get("ts_local") or tr.get("ts_utc"),
                    "grade": tr.get("score", {}).get("grade"),
                    "score": tr.get("score", {}).get("composite_score"),
                    "status": st,
                    "code": tr.get("gates/slate", {}).get("code"),
                    "friction_r": tr.get("gates/slate", {}).get("friction_r"),
                    "risk_pct": tr.get("gates/slate", {}).get("risk_pct"),
                    "provisional": tr.get("enrich", {}).get("provisional", False),
                    "earnings_unverified": tr.get("enrich", {}).get("earnings_unverified", False),
                })

    # 8. Every suppressed / rejected candidate with reason & watchlist symbols
    suppressed_candidates = []
    rejected_candidates = []
    seen_suppressed = set()
    seen_rejected = set()
    watchlist_symbols: Dict[str, Dict[str, Any]] = {}

    for tr in symbol_traces:
        st = tr.get("gates/slate", {}).get("status")
        sym = tr.get("symbol")
        code = tr.get("gates/slate", {}).get("code")
        score = tr.get("score", {}).get("composite_score", 0) or 0
        if st == "SUPPRESSED":
            key = (sym, code)
            if key not in seen_suppressed:
                seen_suppressed.add(key)
                suppressed_candidates.append({
                    "symbol": sym,
                    "scan_id": tr.get("scan_id"),
                    "code": code,
                    "reason": tr.get("gates/slate", {}).get("reason"),
                    "score": score,
                })
        elif st == "REJECTED":
            key = (sym, code)
            if key not in seen_rejected:
                seen_rejected.add(key)
                rejected_candidates.append({
                    "symbol": sym,
                    "scan_id": tr.get("scan_id"),
                    "code": code,
                    "reason": tr.get("gates/slate", {}).get("reason"),
                    "score": score,
                })
        elif st == "WATCHLIST":
            trend_ok = bool(tr.get("engine", {}).get("all_gates", {}).get("trend_sma50", False))
            if sym not in watchlist_symbols or trend_ok:
                watchlist_symbols[sym] = {"trend_ok": trend_ok}

    # Sort real rejections by composite score descending
    rejected_candidates.sort(key=lambda x: x.get("score", 0) or 0, reverse=True)
    suppressed_candidates.sort(key=lambda x: x.get("score", 0) or 0, reverse=True)

    # 9. Telegram send success rate
    tg_events = [
        e for e in events
        if e.get("event") == "telegram_send"
        and (market_upper in str(e.get("market", "")).upper() or e.get("market") in ("ALL", "", None))
    ]
    tg_success = sum(1 for e in tg_events if e.get("level") == "INFO" or (e.get("http_status") and 200 <= e.get("http_status") < 300))
    tg_total = len(tg_events)
    tg_rate = round(tg_success / tg_total * 100.0, 1) if tg_total > 0 else 100.0

    # 10. Daemon gaps
    daemon_gaps = [
        {"time": e.get("ts_local") or e.get("ts_utc"), "gap_seconds": e.get("gap_seconds")}
        for e in events if e.get("event") == "daemon_gap"
    ]

    # 11. Exceptions list (count + first 5 tracebacks)
    exc_events = [
        e for e in events
        if e.get("level") in ("ERROR", "CRITICAL") or e.get("error_trace")
    ]
    first_5_exceptions = [
        {
            "event": e.get("event"),
            "time": e.get("ts_local") or e.get("ts_utc"),
            "message": e.get("message"),
            "error_trace": e.get("error_trace") or e.get("error_text"),
        }
        for e in exc_events[:5]
    ]

    # 12. Slowest 10 symbols by fetch latency
    symbol_latencies = []
    for tr in symbol_traces:
        lat = tr.get("fetch", {}).get("latency_ms")
        if lat is not None:
            symbol_latencies.append({
                "symbol": tr.get("symbol"),
                "latency_ms": lat,
                "scan_id": tr.get("scan_id"),
            })
    symbol_latencies.sort(key=lambda x: x["latency_ms"], reverse=True)
    slowest_10 = symbol_latencies[:10]

    return {
        "market": market.upper(),
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scans": {
            "scheduled": scheduled_scans,
            "executed": executed_scans,
            "completed": completed_scans,
            "avg_start_delay_seconds": avg_delay,
            "max_start_delay_seconds": max_delay,
            "completeness_per_scan": completeness_stats,
        },
        "errors": {
            "error_type_totals": error_totals,
            "rate_limit_429_count": count_429,
            "top_failed_symbols": top_failed_symbols,
        },
        "funnel_totals": funnel_totals,
        "signals": signals,
        "suppressed_candidates": suppressed_candidates,
        "rejected_candidates": rejected_candidates,
        "watchlist": {
            "count": len(watchlist_symbols),
            "trend_ok_count": sum(1 for v in watchlist_symbols.values() if v.get("trend_ok")),
            "symbols": list(watchlist_symbols.keys()),
        },
        "telegram_delivery": {
            "total_attempts": tg_total,
            "successful": tg_success,
            "success_rate_pct": tg_rate,
        },
        "daemon_health": {
            "gaps_count": len(daemon_gaps),
            "gaps": daemon_gaps,
            "exceptions_count": len(exc_events),
            "first_5_exceptions": first_5_exceptions,
        },
        "performance": {
            "slowest_10_fetch_latency": slowest_10,
        },
        "late_chase_shadow": {
            "count": len(late_chase_shadow_entries),
            "rows": late_chase_shadow_entries,
        },
    }


def format_eod_markdown(summary: Dict[str, Any]) -> str:
    """Formats the summary dictionary into GitHub-flavored Markdown."""
    m = summary.get("market")
    d = summary.get("date")
    sc = summary.get("scans", {})
    err = summary.get("errors", {})
    fn = summary.get("funnel_totals", {})
    tg = summary.get("telegram_delivery", {})
    dh = summary.get("daemon_health", {})

    lines = [
        f"# 📊 End-of-Day Market Session Summary: {m} ({d})",
        f"*Generated: {summary.get('generated_at')}*",
        "",
        "## 1. Scan Execution & Completeness",
        f"- **Scans:** Scheduled: `{sc.get('scheduled')}` | Executed: `{sc.get('executed')}` | Completed: `{sc.get('completed')}`",
        f"- **Start Delay:** Avg: `{sc.get('avg_start_delay_seconds')}s` | Max: `{sc.get('max_start_delay_seconds')}s`",
        "",
        "| Scan ID | Completeness | With Data / Total | Aborted |",
        "| :--- | :---: | :---: | :---: |",
    ]
    for c in sc.get("completeness_per_scan", []):
        lines.append(f"| `{c.get('scan_id')}` | `{c.get('completeness_pct')}%` | `{c.get('with_data')}/{c.get('total')}` | `{c.get('aborted')}` |")

    lines.extend([
        "",
        "## 2. Data Health & Network Errors",
        f"- **Rate Limit 429 Errors:** `{err.get('rate_limit_429_count')}`",
        f"- **Error Types:** `{json.dumps(err.get('error_type_totals', {}))}`",
        "",
        "**Top Failed Symbols:**",
    ])
    for item in err.get("top_failed_symbols", []):
        lines.append(f"- **{item['symbol']}**: `{item['failures']}` failures")

    lines.extend([
        "",
        "## 3. Candidate Funnel Totals",
        f"- **With Data:** `{fn.get('with_data')}`",
        f"- **Trend OK:** `{fn.get('trend_ok')}`",
        f"- **Base OK:** `{fn.get('base_ok')}`",
        f"- **Contraction Phase:** `{fn.get('contraction')}`",
        f"- **Breakout Triggered:** `{fn.get('breakout')}`",
        f"- **Passed Quality (Score >= Min):** `{fn.get('passed_quality')}`",
        f"- **Passed Friction Gate:** `{fn.get('passed_friction')}`",
        f"- **Approved by Slate:** `{fn.get('approved')}`",
        "",
        "## 4. Playable Signals Produced",
    ])
    signals = summary.get("signals", [])
    if signals:
        lines.append("| Symbol | Time | Status | Grade | Score | Friction R | Risk % | Flags |")
        lines.append("| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |")
        for s in signals:
            flags = []
            if s.get("provisional"):
                flags.append("PROVISIONAL")
            if s.get("earnings_unverified"):
                flags.append("EARNINGS_UNVERIFIED")
            flag_str = ", ".join(flags) if flags else "CLEAN"
            lines.append(f"| **{s.get('symbol')}** | `{s.get('time')}` | `{s.get('status')}` | `{s.get('grade')}` | `{s.get('score')}` | `{s.get('friction_r')}R` | `{s.get('risk_pct')}%` | `{flag_str}` |")
    else:
        lines.append("*No playable signals generated during this session.*")

    wl = summary.get("watchlist", {})
    wl_count = wl.get("count", 0)
    wl_trend = wl.get("trend_ok_count", 0)

    lines.extend([
        "",
        "## 5. Suppressed & Rejected Candidates",
        f"- **Watchlist:** `{wl_count} names ({wl_trend} trend-OK)`",
        f"- **Suppressed Count:** `{len(summary.get('suppressed_candidates', []))}`",
        f"- **Rejected Count:** `{len(summary.get('rejected_candidates', []))}`",
    ])
    for supp in summary.get("suppressed_candidates", []):
        lines.append(f"- [SUPPRESSED] **{supp['symbol']}**: `{supp['code']}` - {supp['reason']}")
    for rej in summary.get("rejected_candidates", []):
        lines.append(f"- [REJECTED] **{rej['symbol']}**: `{rej['code']}` - {rej['reason']}")

    shadow_data = summary.get("late_chase_shadow", {})
    shadow_rows = shadow_data.get("rows", [])
    shadow_count = shadow_data.get("count", len(shadow_rows))
    lines.extend([
        "",
        f"## Late-chase shadow: {shadow_count} rows, outcomes so far",
    ])
    if shadow_rows:
        lines.append("| Symbol | Signal Time | Pivot | Signal Price | Ext % | Tol % | ATR | Score | Grade | Stop | TP1 | Outcome R | Pullback Filled |")
        lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
        for r in shadow_rows:
            pb_str = "YES" if r.get("pullback_filled") else ("NO" if r.get("pullback_filled") is False else "PENDING")
            r_str = f"{r.get('hypothetical_r'):+.2f}R" if r.get("hypothetical_r") is not None else "PENDING"
            lines.append(f"| **{r.get('symbol')}** | `{r.get('timestamp')}` | `{r.get('pivot')}` | `{r.get('signal_price')}` | `{r.get('extension_pct')}%` | `{r.get('tolerance_pct')}%` | `{r.get('atr')}` | `{r.get('score')}` | `{r.get('grade')}` | `{r.get('stop')}` | `{r.get('tp1')}` | `{r_str}` | `{pb_str}` |")
    else:
        lines.append("*No late-chase shadow candidates recorded.*")

    lines.extend([
        "",
        "## 6. Delivery & Daemon Reliability",
        f"- **Telegram Delivery Success Rate:** `{tg.get('success_rate_pct')}%` ({tg.get('successful')}/{tg.get('total_attempts')})",
        f"- **Daemon Stalls / Gaps (> 120s):** `{dh.get('gaps_count')}`",
        f"- **Daemon Exceptions Logged:** `{dh.get('exceptions_count')}`",
    ])
    for exc in dh.get("first_5_exceptions", []):
        lines.append(f"> ⚠️ **[{exc.get('time')}] {exc.get('event')}**: {exc.get('message')}")
        if exc.get("error_trace"):
            lines.append(f"> ```\n> {exc.get('error_trace')}\n> ```")

    lines.extend([
        "",
        "## 7. Performance Diagnostics (Slowest 10 Fetches)",
    ])
    for p in summary.get("performance", {}).get("slowest_10_fetch_latency", []):
        lines.append(f"- **{p['symbol']}**: `{p['latency_ms']:.1f}ms` (Scan: `{p['scan_id']}`)")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate End-of-Day (EOD) Session Summary")
    parser.add_argument("--market", type=str, default="nse", help="Market identifier (e.g. nse, us, crypto)")
    parser.add_argument("--date", type=str, default=None, help="Calendar date YYYY-MM-DD (defaults to today in market tz)")
    parser.add_argument("--logs-dir", type=str, default="logs/run", help="Base run logs directory")
    args = parser.parse_args()

    date_str = args.date or get_exchange_date_str(args.market)
    summary_data = generate_eod_summary(market=args.market, date_str=date_str, base_dir=args.logs_dir)

    out_dir = os.path.join(args.logs_dir, date_str)
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "eod_summary.json")
    md_path = os.path.join(out_dir, "eod_summary.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, default=str)

    md_report = format_eod_markdown(summary_data)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_report)

    print(f"\n✅ EOD Summary generated successfully for {args.market.upper()} ({date_str}):")
    print(f"  • JSON: {json_path}")
    print(f"  • Markdown: {md_path}\n")
    print(md_report)


if __name__ == "__main__":
    main()
