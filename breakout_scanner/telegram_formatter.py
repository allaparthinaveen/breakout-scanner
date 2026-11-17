import html
from typing import List, Optional, Any
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import numpy as np
from .models import Setup, State


def format_price(val: Optional[float]) -> str:
    """
    Precision price formatting tailored for stocks and crypto assets.
    """
    if val is None:
        return "N/A"
    if abs(val) >= 100:
        return f"{val:.2f}"
    elif abs(val) >= 1:
        return f"{val:.4f}"
    else:
        return f"{val:.5g}"


def format_setup_telegram_line(s: Setup, is_fresh: bool, risk_blocked: bool = False) -> str:
    """
    Formats a single active setup into a streamlined, high-signal Telegram line:
    • SYMBOL | DIR [ACTION] | CP | Entry | Stop | Risk | [HTF] | Grade | [Status (2B)] | Targets
    """
    sym = html.escape(s.symbol)
    tag = "🔄 RETEST" if s.retest_confirmed else "🚀 BREAKOUT"
    dir_arrow = "▲ BUY" if s.direction == 1 else "▼ SELL"
    dir_str = f"{dir_arrow} [{tag}]"

    cp_val = f"{s.current_price:.5g}" if s.current_price is not None else (f"{s.entry:.5g}" if s.entry is not None else "N/A")
    entry_val = f"{s.entry:.5g}" if s.entry is not None else "N/A"

    # Detect Breakeven / Trailed Stop to avoid confusing inverse stop display
    is_breakeven = False
    if s.stop is not None and s.entry is not None:
        if s.direction == 1 and s.stop >= s.entry:
            is_breakeven = True
        elif s.direction == -1 and s.stop <= s.entry:
            is_breakeven = True

    if is_breakeven:
        stop_val = f"{s.stop:.5g} [🔒 BE]"
        risk_val = "Risk: 0.00% (Protected)"
    else:
        stop_val = f"{s.stop:.5g}" if s.stop is not None else "N/A"
        risk_val = f"Risk: {s.risk_pct:.2f}%" if s.risk_pct is not None else ""

    # Multi-TF HTF Badging
    if s.htf_confirmed and getattr(s, 'htf_4h_confirmed', False):
        htf = "⭐⭐ Multi-TF (1D+4H)"
    elif s.htf_confirmed:
        htf = "⭐ 1D"
    elif getattr(s, 'htf_4h_confirmed', False):
        htf = "⭐ 4H"
    else:
        htf = ""

    grade_badge = f"🏆 {s.composite_grade} [{s.composite_score}]" if s.composite_grade else ""

    targets = f"TP1: {s.tp1:.5g} | TP2: {s.tp2:.5g}" if (s.tp1 is not None and s.tp2 is not None) else ""

    parts = [
        f"• <b>{sym}</b>",
        dir_str,
        f"CP: {cp_val}",
        f"Entry: {entry_val}",
        f"Stop: {stop_val}",
    ]
    if risk_val:
        parts.append(risk_val)
    if htf:
        parts.append(htf)
    if grade_badge:
        parts.append(grade_badge)
    if getattr(s, "earnings_unverified", False):
        parts.append("⚠️ EARNINGS UNVERIFIED — check broker calendar")
    if getattr(s, "provisional_data_incomplete", False):
        parts.append("[⚠️ PROVISIONAL: DATA INCOMPLETE]")

    if is_fresh:
        if getattr(s, "is_next_session_candidate", False):
            parts.append("🌅 [NEXT-SESSION CANDIDATE — MARKET CLOSED]")
        else:
            parts.append("🟢 [ENTER NOW]")

    if not is_fresh:
        pnl_sign = "+" if (s.pnl_pct and s.pnl_pct >= 0) else ""
        pnl_val = f"{pnl_sign}{s.pnl_pct:.2f}%" if s.pnl_pct is not None else ""
        if getattr(s, 'requires_confirmation', False):
            status_badge = "[⏳ AWAITING CONFIRMATION]"
        elif risk_blocked:
            status_badge = "[🛡️ RISK DISQUALIFIED - DO NOT ENTER]"
        elif s.is_tp1_reached:
            # Check if currently at/beyond TP1 or if it was reached earlier and pulled back
            curr = s.current_price if s.current_price is not None else s.entry
            at_tp1 = False
            if curr is not None and s.tp1 is not None:
                at_tp1 = (curr >= s.tp1) if s.direction == 1 else (curr <= s.tp1)
            if at_tp1:
                status_badge = f"[TP1 HIT: {pnl_val} - DO NOT ENTER]" if pnl_val else "[TP1 HIT - DO NOT ENTER]"
            else:
                status_badge = f"[TP1 REACHED (Current: {pnl_val}) - DO NOT ENTER]" if pnl_val else "[TP1 REACHED - DO NOT ENTER]"
        else:
            if s.pnl_pct is not None and s.pnl_pct < 0:
                status_badge = f"[IN PROGRESS: {pnl_val} - DO NOT CHASE]"
            else:
                status_badge = f"[EXTENDED: {pnl_val} - DO NOT CHASE]" if pnl_val else "[EXTENDED - DO NOT CHASE]"
        parts.append(status_badge)

    if targets:
        parts.append(targets)

    return " | ".join(parts)


def format_scan_report_html(
    setups: List[Setup],
    market: Optional[str] = None,
    timestamp: Optional[datetime] = None,
    timeframe: str = "1H",
    slate: Optional[Any] = None,
    exit_events: Optional[List[Any]] = None,
    scan_stats: Optional[Any] = None,
) -> str:
    """
    Full multi-symbol market scan report formatted for Telegram.
    Strictly includes only actionable trades (Section 2A and Section 2B).
    Section 2A ONLY admits setups that are risk-approved and confirmed (P0-1 & P0-2).
    Section 2C reports explicit trade exits and management events (P1-3).
    Excludes Section 1 (VCP Bases) to eliminate non-trade noise.
    Enriched with timezone-aware scan timestamp (Market Local + IST/UTC).
    """
    market_upper = market.upper() if market and market.upper() not in ("DEFAULT", "NONE") else None
    title = f"📡 <b>{market_upper} MARKET SIGNALS</b>" if market_upper else "📡 <b>MARKET SCANNER SIGNALS</b>"

    ts_utc = timestamp or datetime.now(timezone.utc)
    if ts_utc.tzinfo is None:
        ts_utc = ts_utc.replace(tzinfo=timezone.utc)

    dt_ist = ts_utc.astimezone(ZoneInfo("Asia/Kolkata"))
    dt_sgt = ts_utc.astimezone(ZoneInfo("Asia/Singapore"))
    ist_str = dt_ist.strftime("%d/%m/%Y %I:%M %p IST")
    sgt_str = dt_sgt.strftime("%I:%M %p SGT")

    if market_upper == "US":
        dt_mkt = ts_utc.astimezone(ZoneInfo("America/New_York"))
        mkt_str = dt_mkt.strftime("%d/%m/%Y %I:%M %p %Z")
        time_line = f"📅 <i>{mkt_str} | {sgt_str} | {dt_ist.strftime('%I:%M %p IST')} ({timeframe} Close)</i>"
    elif market_upper == "NSE":
        time_line = f"📅 <i>{ist_str} | {sgt_str} ({timeframe} Close)</i>"
    elif market_upper == "CRYPTO":
        utc_str = ts_utc.strftime("%d/%m/%Y %H:%M UTC")
        time_line = f"📅 <i>{utc_str} | {sgt_str} | {dt_ist.strftime('%I:%M %p IST')} ({timeframe} Close)</i>"
    elif market_upper in ("TSE", "TOKYO"):
        dt_mkt = ts_utc.astimezone(ZoneInfo("Asia/Tokyo"))
        mkt_str = dt_mkt.strftime("%d/%m/%Y %I:%M %p %Z")
        time_line = f"📅 <i>{mkt_str} | {sgt_str} | {dt_ist.strftime('%I:%M %p IST')} ({timeframe} Close)</i>"
    elif market_upper in ("HKEX", "HK"):
        dt_mkt = ts_utc.astimezone(ZoneInfo("Asia/Hong_Kong"))
        mkt_str = dt_mkt.strftime("%d/%m/%Y %I:%M %p %Z")
        time_line = f"📅 <i>{mkt_str} | {sgt_str} | {dt_ist.strftime('%I:%M %p IST')} ({timeframe} Close)</i>"
    else:
        utc_str = ts_utc.strftime("%d/%m/%Y %H:%M UTC")
        time_line = f"📅 <i>{ist_str} | {sgt_str} | {utc_str} ({timeframe} Close)</i>"

    incomplete_banner = None
    if scan_stats:
        tot = scan_stats.get("total", 0)
        wd = scan_stats.get("with_data", 0)
        failed_list = scan_stats.get("failed_symbols", [])
        min_pct = scan_stats.get("min_scan_completeness_pct", 90.0)
        aborted = scan_stats.get("aborted", False)
        pct = (wd / tot * 100) if tot > 0 else 0.0
        if tot > 0 and (pct < min_pct or aborted):
            failed_str = ", ".join(failed_list[:10]) + ("..." if len(failed_list) > 10 else "")
            incomplete_banner = (
                f"⚠️ <b>SCAN INCOMPLETE:</b> {wd}/{tot} symbols had data (failed: {failed_str}). "
                "Do NOT read 'no signals' as a quiet market.\n━━━━━━━━━━━━━━━━━━━━"
            )

    lines = []
    if incomplete_banner:
        lines.append(incomplete_banner)

    lines.extend([
        title,
        time_line,
        "━━━━━━━━━━━━━━━━━━━━",
    ])

    prov_count = sum(1 for s in setups if getattr(s, "provisional_data_incomplete", False))
    if prov_count > 0:
        lines.append(f"⚠️ <b>DATA DEGRADED:</b> {prov_count}/{len(setups)} symbol(s) evaluated on provisional technicals.")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

    active_setups = [s for s in setups if s.state in (State.BUY, State.SELL)]

    # Approved and rejected symbols from institutional ActionableSlate / TradeSlate
    approved_symbols = set()
    rejected_map: Dict[str, str] = {}
    if slate is not None:
        if hasattr(slate, "selected_trades"):
            approved_symbols = set(t.symbol for t in (slate.selected_trades or []))
        elif hasattr(slate, "actionable_trades"):
            approved_symbols = set(t.symbol for t in (slate.actionable_trades or []))

        if hasattr(slate, "rejected_trades"):
            for r in (slate.rejected_trades or []):
                r_sym = getattr(r, "symbol", "")
                r_reason = getattr(r, "reason", None) or getattr(r, "rejection_code", "REJECTED")
                if r_sym:
                    rejected_map[r_sym] = str(r_reason)
        if hasattr(slate, "suppressed_trades"):
            for sp in (slate.suppressed_trades or []):
                sp_sym = getattr(sp, "symbol", "")
                sp_reason = getattr(sp, "reason", None) or getattr(sp, "suppression_code", "SUPPRESSED")
                if sp_sym and sp_sym not in rejected_map:
                    rejected_map[sp_sym] = str(sp_reason)

    fresh_setups: List[Setup] = []
    fresh_rejected_setups: List[Tuple[Setup, str]] = []
    progress_setups: List[Tuple[Setup, bool]] = []  # (Setup, is_risk_blocked)

    for s in active_setups:
        # P0-1 Risk Filtering Gate:
        # If slate is provided, setup must be in selected_trades (approved by rejection gate & portfolio guard)
        # If slate not provided, evaluate basic rejection gate rules (legacy callers)
        if slate is not None:
            is_risk_approved = (s.symbol in approved_symbols)
        else:
            is_risk_approved = (
                not getattr(s, "earnings_warning", False)
                and (s.risk_pct is None or s.risk_pct <= 5.0)
                and (getattr(s, "friction_r", None) is None or s.friction_r <= 0.15)
            )

        if getattr(s, "safeguard_rejected", False) or s.symbol in rejected_map:
            is_risk_approved = False

        is_confirmed = not getattr(s, "requires_confirmation", False)

        # Section 2A Criteria: Fresh + Confirmed + Risk Approved
        if s.is_fresh_entry and is_confirmed and is_risk_approved:
            fresh_setups.append(s)
        else:
            is_fresh_candle = (s.bars_since_breakout is None or s.bars_since_breakout <= 1)
            if (s.is_fresh_entry or is_fresh_candle) and is_confirmed and not is_risk_approved:
                if s.symbol in rejected_map or getattr(s, "safeguard_rejected", False):
                    rej_msg = (
                        rejected_map.get(s.symbol)
                        or getattr(s, "safeguard_rejection_reason", None)
                        or getattr(s, "failure_reason", None)
                        or "REJECTED_BY_SAFEGUARDS"
                    )
                    fresh_rejected_setups.append((s, rej_msg))
            # Everything else belongs to Section 2B (Trades In Progress / Awaiting / Blocked)
            progress_setups.append((s, not is_risk_approved and s.is_fresh_entry))

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
    progress_setups.sort(key=lambda item: safe_sort_active(item[0]), reverse=True)

    # 1. SECTION 2A: FRESH ACTIONABLE ENTRIES
    is_next_session = any(getattr(s, "is_next_session_candidate", False) for s in fresh_setups)
    if is_next_session:
        lines.append("🌅 <b>SECTION 2A: NEXT-SESSION CANDIDATES (Market Closed — Execute Next Open)</b>")
    else:
        lines.append("🟢 <b>SECTION 2A: FRESH ACTIONABLE ENTRIES (Playable Now)</b>")
    lines.append("────────────────────")
    if not fresh_setups:
        lines.append("<i>(None)</i>")
        if fresh_rejected_setups:
            lines.append("")
            lines.append("⚠️ <b>Fresh but rejected:</b>")
            for s, r_reason in fresh_rejected_setups:
                lines.append(f"  • <b>{html.escape(s.symbol)}</b>: {html.escape(str(r_reason))}")
    else:
        for s in fresh_setups:
            lines.append(format_setup_telegram_line(s, is_fresh=True))

    lines.append("")

    # 2. SECTION 2B: TRADES IN PROGRESS
    lines.append("🔒 <b>SECTION 2B: TRADES IN PROGRESS (Holders Only — Do Not Chase)</b>")
    lines.append("────────────────────")
    if not progress_setups:
        lines.append("<i>(None)</i>")
    else:
        for s, risk_blocked in progress_setups:
            lines.append(format_setup_telegram_line(s, is_fresh=False, risk_blocked=risk_blocked))

    # 3. SECTION 2C: TRADE EXITS & MANAGEMENT EVENTS (P1-3)
    if exit_events:
        lines.append("")
        lines.append("🔔 <b>SECTION 2C: TRADE EXITS & MANAGEMENT EVENTS</b>")
        lines.append("────────────────────")
        for ev in exit_events:
            sym = html.escape(str(getattr(ev, "symbol", "")))
            ev_type = str(getattr(ev, "event_type", "EXIT"))
            exit_px = format_price(getattr(ev, "exit_price", 0.0))
            entry_px = format_price(getattr(ev, "entry_price", 0.0))
            pnl_pct = getattr(ev, "pnl_pct", 0.0)
            realized = getattr(ev, "realized_pnl", 0.0)
            sign = "+" if pnl_pct >= 0 else ""
            dollar_sign = "+" if realized >= 0 else ""

            if ev_type == "STOP_LOSS":
                badge = "🔴 <b>[STOP LOSS HIT]</b>"
            elif ev_type == "TP2_EXIT":
                badge = "🎯 <b>[TP2 HIT — FULL EXIT]</b>"
            elif ev_type == "TP1_SCALE":
                badge = "🔄 <b>[TP1 HIT — SCALED 50% & TRAILED BE]</b>"
            elif ev_type == "TIME_EXIT":
                badge = "⏱️ <b>[TIME EXIT — MAX HOLD]</b>"
            elif ev_type == "BREAKEVEN_STOP":
                badge = "🛡️ <b>[BREAKEVEN STOP HIT]</b>"
            else:
                badge = f"⚠️ <b>[{ev_type}]</b>"

            lines.append(
                f"• <b>{sym}</b> | {badge} | Exit: <code>{exit_px}</code> | Entry: <code>{entry_px}</code> | "
                f"P&L: <code>{sign}{pnl_pct:.2f}% ({dollar_sign}${realized:,.2f})</code>"
            )

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)



def format_instant_alert_html(setup: Setup, timeframe: str = "1H", allocation: Optional[Any] = None) -> str:
    """
    High-priority instant push alert sent within 60s of candle close.
    Designed with a mobile-first card layout and clean HTML badges.
    Optionally enriched with institutional position sizing allocation.
    """
    is_buy = setup.direction == 1
    dir_badge = "▲ BUY" if is_buy else "▼ SELL"
    dir_emoji = "🟢" if is_buy else "🔴"
    action_type = "RETEST HOLD" if setup.retest_confirmed else ("BREAKOUT" if is_buy else "BREAKDOWN")
    
    headline_icon = "🚨" if not setup.retest_confirmed else "🔄"
    headline = f"{headline_icon} <b>FRESH {action_type} DETECTED: {setup.symbol}</b>"
    
    grade_str = f"{setup.composite_grade} [{setup.composite_score}]" if setup.composite_grade else "N/A"
    entry_str = format_price(setup.entry)
    stop_str = format_price(setup.stop)
    cp_str = format_price(setup.current_price)
    risk_str = f"{setup.risk_pct:.1f}%" if setup.risk_pct is not None else "N/A"

    lines = [
        headline,
        "━━━━━━━━━━━━━━━━━━━━",
        f"💎 <b>{setup.symbol}</b>  •  {dir_emoji} <b>{dir_badge} [{action_type}]</b>",
        f"• <b>Entry:</b> <code>{entry_str}</code> | <b>Stop:</b> <code>{stop_str}</code> | <b>Risk:</b> <code>{risk_str}</code>",
        f"• <b>Grade:</b> <code>{grade_str}</code>" + ("  •  ⭐ <b>1D Macro</b>" if setup.htf_confirmed else ""),
        f"• <b>Triggered:</b> <i>Just now ({timeframe} candle close)</i>",
        f"• <b>Current Price:</b> <code>{cp_str}</code>",
    ]

    if allocation and getattr(allocation, 'shares', 0) > 0:
        lines.append(
            f"• <b>Size:</b> <code>{allocation.shares:,} shares (${allocation.position_value:,.0f}) | Risk: ${allocation.dollar_risk:,.0f} ({allocation.risk_pct_of_equity:.1f}%)</code>"
        )

    if setup.tp1 is not None and setup.tp2 is not None:
        lines.append(f"• <b>Targets:</b> TP1: <code>{format_price(setup.tp1)}</code> | TP2: <code>{format_price(setup.tp2)}</code>")

    # Quant indicators
    edge_bullets = []
    if setup.rvol is not None:
        edge_bullets.append(f"<code>Vol: {setup.rvol:.1f}x RVOL</code> 🔥")
    if setup.sector_name:
        trend = f" {setup.rs_trend}" if setup.rs_trend else ""
        rs_pct = f" RS: {setup.sector_rs_pct:+.1f}%" if setup.sector_rs_pct is not None else ""
        edge_bullets.append(f"<code>{setup.sector_name}{rs_pct}{trend}</code>")

    if edge_bullets:
        lines.append("• <b>Edge:</b> " + " | ".join(edge_bullets))

    if getattr(setup, "is_next_session_candidate", False):
        lines.append("• <b>Status:</b> 🌅 <b>[NEXT-SESSION CANDIDATE — EXECUTE AT NEXT OPEN]</b>")
    else:
        lines.append("• <b>Status:</b> 🟢 <b>[ENTER NOW — FRESH]</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)





def format_daily_briefing_html(
    contraction: List[Setup],
    breaking_out: List[Setup],
    retesting: List[Setup],
    idle: List[Setup]
) -> str:
    """
    Formats the pre-market daily briefing as a mobile card layout.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "🌅 <b>PRE-MARKET VCP RADAR</b>",
        f"📅 <i>Scan Time: {now_str}</i>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    # Active Breakouts
    lines.append("🚀 <b>ACTIVELY BREAKING OUT (MOMENTUM)</b>")
    lines.append("────────────────────")
    if breaking_out:
        for s in breaking_out:
            dir_emoji = "🟢" if s.direction == 1 else "🔴"
            dir_str = "BUY" if s.direction == 1 else "SELL"
            pnl_sign = "+" if (s.pnl_pct and s.pnl_pct >= 0) else ""
            pnl_val = f"{pnl_sign}{s.pnl_pct:.1f}%" if s.pnl_pct is not None else ""
            status = "🟢 <b>[ENTER NOW]</b>" if s.is_fresh_entry else (
                f"🛑 <b>[TP1 HIT: {pnl_val}]</b>" if s.is_tp1_reached else f"⚠️ <b>[EXTENDED: {pnl_val}]</b>"
            )
            vol_str = f" | Vol: <code>{s.rvol:.1f}x</code>" if s.rvol is not None else ""
            grade = f" | 🏆 <b>{s.composite_grade} [{s.composite_score}]</b>" if s.composite_grade else ""
            card = (
                f"🎯 <b>{s.symbol}</b>  {dir_emoji} <b>{dir_str}</b>{grade}\n"
                f"• <b>Price:</b> <code>{format_price(s.current_price)}</code> | <b>In:</b> <code>{format_price(s.entry)}</code> | <b>SL:</b> <code>{format_price(s.stop)}</code>{vol_str}\n"
                f"• <b>Status:</b> {status}\n"
            )
            lines.append(card)
    else:
        lines.append("<i>None currently breaking out.</i>\n")

    # Retests
    lines.append("🔄 <b>CONFIRMED PIVOT RETESTS</b>")
    lines.append("────────────────────")
    if retesting:
        for s in retesting:
            dir_emoji = "🟢" if s.direction == 1 else "🔴"
            dir_str = "BUY" if s.direction == 1 else "SELL"
            pnl_sign = "+" if (s.pnl_pct and s.pnl_pct >= 0) else ""
            pnl_val = f"{pnl_sign}{s.pnl_pct:.1f}%" if s.pnl_pct is not None else ""
            status = "🟢 <b>[ENTER NOW]</b>" if s.is_fresh_entry else (
                f"🛑 <b>[TP1 HIT: {pnl_val}]</b>" if s.is_tp1_reached else f"⚠️ <b>[EXTENDED: {pnl_val}]</b>"
            )
            grade = f" | 🏆 <b>{s.composite_grade} [{s.composite_score}]</b>" if s.composite_grade else ""
            card = (
                f"🔄 <b>{s.symbol}</b>  {dir_emoji} <b>{dir_str} [RETEST]</b>{grade}\n"
                f"• <b>Price:</b> <code>{format_price(s.current_price)}</code> | <b>In:</b> <code>{format_price(s.entry)}</code> | <b>SL:</b> <code>{format_price(s.stop)}</code>\n"
                f"• <b>Status:</b> {status}\n"
            )
            lines.append(card)
    else:
        lines.append("<i>None currently in pivot retest.</i>\n")

    # VCP Bases
    lines.append("📦 <b>TIGHT BASES / VCP COILING</b>")
    lines.append("────────────────────")
    if contraction:
        for s in contraction:
            w_str = f"W: <code>{s.base_width_pct:.1f}%</code>" if s.base_width_pct is not None else ""
            coil_str = f" | 🗜️ <code>{s.decay_ratio:.1f}x</code>" if (s.decay_ratio is not None and s.tightening_confirmed) else ""
            vdu_str = f" | 💧 <code>{s.vdu_ratio:.1f}x</code>" if s.vdu_confirmed else ""
            htf_str = " | ⭐ 1D" if s.htf_confirmed else ""
            grade_str = f" | 🏆 <b>{s.composite_grade} [{s.composite_score}]</b>" if s.composite_grade else ""
            res_str = format_price(s.resistance)
            sup_str = format_price(s.support)
            lines.append(
                f"• <b>{s.symbol:8}</b> | {w_str}{coil_str}{vdu_str}{htf_str}{grade_str}\n"
                f"  <i>Range:</i> <code>{sup_str} – {res_str}</code>"
            )
    else:
        lines.append("<i>None currently forming a box.</i>\n")

    if idle:
        lines.append("")
        lines.append("💤 <b>IDLE / FAILED</b>")
        lines.append("<code>" + ", ".join([s.symbol for s in idle]) + "</code>")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_actionable_slate_html(slate: Any) -> str:
    """
    Formats the authoritative institutional ActionableSlate for Telegram.
    Presents Top-N approved trades with exact position sizes and risk limits,
    while explicitly logging suppressed and rejected candidates.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    profile = getattr(slate, "profile_name", "Production")
    equity = getattr(slate, "portfolio_equity", 100000.0)
    risk_cap = getattr(slate, "max_daily_dollar_risk", 2000.0)
    risk_pct = getattr(slate, "max_daily_risk_pct", 2.0)
    allocated_risk = getattr(slate, "total_daily_risk_allocated", 0.0)
    open_slots = getattr(slate, "remaining_trade_slots", 4)
    max_slots = getattr(slate, "max_active_trades", 4)
    is_illustrative = getattr(slate, "is_illustrative_sizing", False)

    lines = [
        "🏛️ <b>INSTITUTIONAL ACTIONABLE SLATE</b>",
        f"📋 <i>Profile: {profile} | {now_str}</i>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💰 <b>Equity:</b> <code>${equity:,.0f}</code> | <b>Risk Cap:</b> <code>${risk_cap:,.0f} ({risk_pct:.1f}%)</code>",
        f"📊 <b>Allocated Risk:</b> <code>${allocated_risk:,.2f}</code> | <b>Slots:</b> <code>{open_slots}/{max_slots}</code>",
    ]

    if is_illustrative:
        lines.append("⚠️ <i>Note: Portfolio Guard is disabled; sizing is illustrative.</i>")

    lines.append("────────────────────")

    selected = getattr(slate, "selected_trades", [])
    if not selected:
        lines.append("🟢 <b>TOP-N APPROVED TRADES (PLAYABLE NOW)</b>")
        lines.append("<i>(No trades approved for capital allocation in this scan)</i>")
    else:
        lines.append(f"🟢 <b>TOP-N APPROVED TRADES ({len(selected)} PLAYABLE NOW)</b>")
        for t in selected:
            sym = html.escape(t.symbol)
            dir_arrow = "▲ BUY" if t.direction == 1 else "▼ SELL"
            shares_str = f"{t.shares:,} shares" if t.shares > 0 else "N/A"
            pos_val = f"${t.position_value:,.0f}" if t.position_value > 0 else "N/A"
            risk_val = f"${t.dollar_risk:,.0f}" if t.dollar_risk > 0 else "N/A"
            grade_str = f"🏆 {t.grade} [{t.score}]" if t.grade else f"[{t.score}]"

            tp_str = ""
            if t.take_profit_1 > 0 and t.take_profit_2 > 0:
                tp_str = f" | TP1: <code>{format_price(t.take_profit_1)}</code> | TP2: <code>{format_price(t.take_profit_2)}</code>"

            lines.append(
                f"• <b>{sym}</b> {dir_arrow} | <b>In:</b> <code>{format_price(t.entry_price)}</code> | <b>SL:</b> <code>{format_price(t.stop_loss)}</code>\n"
                f"  <i>Size:</i> <code>{shares_str} ({pos_val})</code> | <i>Risk:</i> <code>{risk_val}</code> | {grade_str}{tp_str}"
            )

    suppressed = getattr(slate, "suppressed_trades", [])
    if suppressed:
        lines.append("")
        lines.append(f"🔒 <b>SUPPRESSED ({len(suppressed)} BLOCKED BY PORTFOLIO GUARD)</b>")
        lines.append("────────────────────")
        for s in suppressed:
            sym = html.escape(s.symbol)
            lines.append(f"• <b>{sym}</b>: <i>{s.reason}</i>")

    rejected = getattr(slate, "rejected_trades", [])
    if rejected:
        lines.append("")
        lines.append(f"🛡️ <b>DISQUALIFIED ({len(rejected)} BLOCKED BY RISK GATE)</b>")
        lines.append("────────────────────")
        for r in rejected:
            sym = html.escape(r.symbol)
            lines.append(f"• <b>{sym}</b> [<code>{r.rejection_code}</code>]: <i>{r.reason}</i>")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

