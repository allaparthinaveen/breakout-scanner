import html
from typing import List, Optional, Any
from datetime import datetime
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


def format_setup_telegram_line(s: Setup, is_fresh: bool) -> str:
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

    if not is_fresh:
        pnl_sign = "+" if (s.pnl_pct and s.pnl_pct >= 0) else ""
        pnl_val = f"{pnl_sign}{s.pnl_pct:.2f}%" if s.pnl_pct is not None else ""
        if s.is_tp1_reached:
            status_badge = f"[TP1 HIT: {pnl_val} - DO NOT ENTER]" if pnl_val else "[TP1 HIT - DO NOT ENTER]"
        else:
            status_badge = f"[EXTENDED: {pnl_val} - DO NOT CHASE]" if pnl_val else "[EXTENDED - DO NOT CHASE]"
        parts.append(status_badge)

    if targets:
        parts.append(targets)

    return " | ".join(parts)


def format_scan_report_html(setups: List[Setup]) -> str:
    """
    Full multi-symbol market scan report formatted for Telegram.
    Strictly includes only actionable trades (Section 2A and Section 2B).
    Excludes Section 1 (VCP Bases) to eliminate non-trade noise.
    """
    lines = [
        "📡 <b>MARKET SCANNER SIGNALS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    active_setups = [s for s in setups if s.state in (State.BUY, State.SELL)]
    fresh_setups = [s for s in active_setups if s.is_fresh_entry]
    progress_setups = [s for s in active_setups if not s.is_fresh_entry]

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

    # 1. SECTION 2A: FRESH ACTIONABLE ENTRIES
    lines.append("🟢 <b>SECTION 2A: FRESH ACTIONABLE ENTRIES (Playable Now)</b>")
    lines.append("────────────────────")
    if not fresh_setups:
        lines.append("<i>(None)</i>")
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
        for s in progress_setups:
            lines.append(format_setup_telegram_line(s, is_fresh=False))

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

