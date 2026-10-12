from typing import List, Optional
from datetime import datetime
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


def format_instant_alert_html(setup: Setup, timeframe: str = "1H") -> str:
    """
    High-priority instant push alert sent within 60s of candle close.
    Designed with a mobile-first card layout and clean HTML badges.
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


def format_scan_report_html(setups: List[Setup]) -> str:
    """
    Full multi-symbol market scan report formatted as mobile-friendly cards.
    Eliminates ugly wrapped monospace tables on phones.
    """
    lines = [
        "📡 <b>MARKET SCANNER REPORT</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    # Partition active setups
    active_setups = [
        s for s in setups
        if s.state in (State.BUY, State.SELL)
        and (s.bars_since_breakout is not None and s.bars_since_breakout <= 24)
    ]
    fresh_setups = [s for s in active_setups if s.is_fresh_entry]
    progress_setups = [s for s in active_setups if not s.is_fresh_entry]

    fresh_setups.sort(
        key=lambda s: (s.composite_score, -abs(s.entry - s.current_price) / s.entry if s.entry and s.current_price else float('inf')),
        reverse=True
    )
    progress_setups.sort(
        key=lambda s: (s.composite_score, -abs(s.entry - s.current_price) / s.entry if s.entry and s.current_price else float('inf')),
        reverse=True
    )

    # 1. SECTION 2A: FRESH ACTIONABLE ENTRIES
    lines.append("🟢 <b>SECTION 2A: FRESH ENTRIES (Playable Now)</b>")
    lines.append("────────────────────")
    if not fresh_setups:
        lines.append("<i>(None currently within &lt;1.0% of entry)</i>")
    else:
        for s in fresh_setups:
            dir_emoji = "🟢" if s.direction == 1 else "🔴"
            dir_str = "BUY" if s.direction == 1 else "SELL"
            tag = "RETEST" if s.retest_confirmed else "BREAKOUT"
            grade = f"🏆 <b>{s.composite_grade} [{s.composite_score}]</b>" if s.composite_grade else ""
            htf = "⭐ 1D" if s.htf_confirmed else ""
            header_right = "  •  ".join(filter(None, [grade, htf]))
            header_right_str = f"  •  {header_right}" if header_right else ""

            pnl_str = f" | PnL: <code>{s.pnl_pct:+.1f}%</code>" if s.pnl_pct is not None else ""
            vol_str = f" | Vol: <code>{s.rvol:.1f}x</code>" if s.rvol is not None else ""
            trend_str = f" {s.rs_trend}" if s.rs_trend else ""
            sec_str = f" | <code>{s.sector_name}{trend_str}</code>" if s.sector_name else ""

            targets_str = ""
            if s.tp1 is not None and s.tp2 is not None:
                targets_str = f"\n• <b>Targets:</b> TP1: <code>{format_price(s.tp1)}</code> | TP2: <code>{format_price(s.tp2)}</code>"

            card = (
                f"🎯 <b>{s.symbol}</b>  {dir_emoji} <b>{dir_str} [{tag}]</b>{header_right_str}\n"
                f"• <b>Price:</b> <code>{format_price(s.current_price)}</code> | <b>In:</b> <code>{format_price(s.entry)}</code> | <b>SL:</b> <code>{format_price(s.stop)}</code>\n"
                f"• <b>Risk:</b> <code>{s.risk_pct:.2f}%</code>{vol_str}{sec_str}{pnl_str}{targets_str}\n"
                f"• <b>Status:</b> 🟢 <b>[ENTER NOW — FRESH]</b>\n"
            )
            lines.append(card)

    lines.append("")

    # 2. SECTION 2B: TRADES IN PROGRESS
    lines.append("🔒 <b>SECTION 2B: IN PROGRESS (Holders Only — Do Not Chase)</b>")
    lines.append("────────────────────")
    if not progress_setups:
        lines.append("<i>(None)</i>")
    else:
        for s in progress_setups:
            dir_emoji = "🟢" if s.direction == 1 else "🔴"
            dir_str = "BUY" if s.direction == 1 else "SELL"
            tag = "RETEST" if s.retest_confirmed else "BREAKOUT"
            grade = f"🏆 <b>{s.composite_grade} [{s.composite_score}]</b>" if s.composite_grade else ""
            pnl_sign = "+" if (s.pnl_pct and s.pnl_pct >= 0) else ""
            pnl_val = f"{pnl_sign}{s.pnl_pct:.1f}%" if s.pnl_pct is not None else ""
            
            if s.is_tp1_reached:
                badge = f"🛑 <b>[TP1 HIT: {pnl_val} — DO NOT ENTER]</b>"
            else:
                badge = f"⚠️ <b>[EXTENDED: {pnl_val} — DO NOT CHASE]</b>"

            vol_str = f" | Vol: <code>{s.rvol:.1f}x</code>" if s.rvol is not None else ""
            trend_str = f" {s.rs_trend}" if s.rs_trend else ""
            sec_str = f" | <code>{s.sector_name}{trend_str}</code>" if s.sector_name else ""

            card = (
                f"⏳ <b>{s.symbol}</b>  {dir_emoji} <b>{dir_str} [{tag}]</b>  •  {grade}\n"
                f"• <b>Price:</b> <code>{format_price(s.current_price)}</code> | <b>In:</b> <code>{format_price(s.entry)}</code> | <b>SL:</b> <code>{format_price(s.stop)}</code>{vol_str}{sec_str}\n"
                f"• <b>Status:</b> {badge}\n"
            )
            lines.append(card)

    lines.append("")

    # 3. SECTION 1: VCP BASES
    lines.append("◉ <b>SECTION 1: VCP BASES (Preparing Signal)</b>")
    lines.append("────────────────────")
    base_setups = [s for s in setups if s.state == State.CONTRACTION and s.bias_score >= 3]
    base_setups.sort(key=lambda s: (s.composite_score, s.bias_score), reverse=True)

    if not base_setups:
        lines.append("<i>(None currently forming bases)</i>")
    else:
        for s in base_setups:
            width_str = f"W: <code>{s.base_width_pct:.1f}%</code>" if s.base_width_pct is not None else ""
            coil_str = f" | 🗜️ <code>{s.decay_ratio:.1f}x</code>" if (s.decay_ratio is not None and s.tightening_confirmed) else ""
            vdu_str = f" | 💧 <code>{s.vdu_ratio:.1f}x</code>" if s.vdu_confirmed else ""
            trend_str = f" {s.rs_trend}" if s.rs_trend else ""
            sec_str = f" | <code>{s.sector_name}{trend_str}</code>" if s.sector_name else ""
            htf_str = " | ⭐ 1D" if s.htf_confirmed else ""
            grade_str = f" | 🏆 <b>{s.composite_grade} [{s.composite_score}]</b>" if s.composite_grade else ""
            res_str = format_price(s.resistance)
            sup_str = format_price(s.support)

            lines.append(
                f"• <b>{s.symbol:8}</b> | {width_str}{coil_str}{vdu_str}{sec_str}{htf_str}{grade_str}\n"
                f"  <i>Range:</i> <code>{sup_str} – {res_str}</code>"
            )

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
