"""
Automated tests for the redesigned Telegram MARKET SCAN REPORT (`format_scan_report_html`
and `split_scan_report_html`), covering all 15 required scenarios:
  1. Report containing all three sections
  2. BUY and SELL signals
  3. Multiple fresh actionable entries
  4. Multiple active trades
  5. Multiple exit event types (STOP_LOSS, TP1_SCALE, TP2_EXIT, TIME_EXIT, TIME_STOP_10D, BREAKEVEN_STOP, FAILED_BREAKOUT)
  6. Empty Section 2A
  7. Empty Section 2B
  8. Empty Section 2C
  9. All three sections empty
  10. Missing optional fields
  11. Special characters and HTML escaping
  12. Long report and logical card-boundary message splitting
  13. Unicode symbols and emojis
  14. Preservation of signal and event ordering
  15. Existing formatter signature compatibility & HTML tag balance
"""

from datetime import datetime, timezone
from html.parser import HTMLParser
import pytest

from breakout_scanner.models import Setup, State, TradeExitEvent
from breakout_scanner.slate import ActionableSlate, RejectionRecord
from breakout_scanner.telegram_formatter import (
    format_scan_report_html,
    split_scan_report_html,
)


TS = datetime(2026, 11, 24, 15, 30, tzinfo=timezone.utc)
ALLOWED_TG_TAGS = {"b", "i", "code", "pre", "a", "u", "s"}


class _TelegramHTMLValidator(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TG_TAGS:
            self.errors.append(f"Unsupported Telegram HTML tag: <{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"Mismatched closing tag </{tag}>; stack={self.stack}")
        else:
            self.stack.pop()

    def validate(self, html_str: str):
        self.feed(html_str)
        self.close()
        if self.stack:
            self.errors.append(f"Unclosed tags: {self.stack}")
        assert not self.errors, f"Invalid Telegram HTML: {self.errors}"


def _assert_valid_tg_html(text: str):
    _TelegramHTMLValidator().validate(text)


def _fresh_setup(sym: str, direction: int = 1, score: int = 90, grade: str = "A+", entry: float = 120.0, **kw) -> Setup:
    stop = entry - 3.0 if direction == 1 else entry + 3.0
    tp1 = entry + 3.0 if direction == 1 else entry - 3.0
    tp2 = entry + 6.0 if direction == 1 else entry - 6.0
    s = Setup(
        symbol=sym,
        state=State.BUY if direction == 1 else State.SELL,
        direction=direction,
        entry=entry,
        current_price=entry + (0.2 if direction == 1 else -0.2),
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        risk_pct=2.5,
        bars_since_breakout=0,
        composite_score=score,
        composite_grade=grade,
        **kw,
    )
    return s


def test_1_2_all_three_sections_and_buy_sell_distinguishable():
    s_buy = _fresh_setup("ACME", direction=1, score=92, grade="A+", rvol=2.1, htf_confirmed=True, sector_name="Tech", sector_rs_pct=3.5, rs_trend="▲")
    s_sell = _fresh_setup("BETA", direction=-1, score=85, grade="A", retest_confirmed=True)
    s_active = Setup(
        symbol="GAMMA", state=State.BUY, direction=1,
        entry=200.0, current_price=204.0, stop=200.2, tp1=205.0, tp2=210.0,
        pnl_pct=2.0, bars_since_breakout=4, tp1_hit=True,
    )
    exits = [
        TradeExitEvent(symbol="DELTA", event_type="TP1_SCALE", direction=1, entry_price=100.0, exit_price=104.0, pnl_pct=4.0, realized_pnl=200.0, shares=50, reason="TP1"),
    ]
    stats = {"total": 215, "with_data": 215, "failed_symbols": [], "min_scan_completeness_pct": 90.0}

    html = format_scan_report_html([s_buy, s_sell, s_active], market="US", timestamp=TS, timeframe="1H", exit_events=exits, scan_stats=stats)
    _assert_valid_tg_html(html)

    assert "📊 <b>MARKET SCAN REPORT</b>" in html
    assert "Scanned <code>215</code> · Fresh <code>2</code> · Active <code>1</code> · Exits <code>1</code>" in html
    assert "🟢 <b>FRESH ACTIONABLE ENTRIES</b>" in html
    assert "🟢 <b>ACME</b>  |  <b>BUY</b> · Breakout" in html
    assert "🔴 <b>BETA</b>  |  <b>SELL</b> · Retest" in html
    assert "📌 <b>TRADES IN PROGRESS</b>" in html
    assert "📌 <b>GAMMA</b>  |  <b>BUY</b> · Breakout" in html
    assert "📤 <b>EXITS &amp; MANAGEMENT EVENTS</b>" in html
    assert "💰 <b>DELTA</b>  |  <b>PARTIAL PROFIT</b>" in html
    assert "📊 <b>SCAN COMPLETED</b>" in html


def test_3_4_multiple_fresh_entries_and_multiple_active_trades():
    fresh = [_fresh_setup(f"FR{i}", score=95 - i) for i in range(4)]
    active = [
        Setup(symbol=f"ACT{i}", state=State.BUY, direction=1, entry=100.0 + i, current_price=102.0 + i, stop=97.0 + i, tp1=103.0 + i, tp2=106.0 + i, pnl_pct=2.0, bars_since_breakout=5, composite_score=80 - i)
        for i in range(3)
    ]
    html = format_scan_report_html(fresh + active, market="US", timestamp=TS)
    _assert_valid_tg_html(html)

    sec_2a = html.split("FRESH ACTIONABLE ENTRIES</b>")[1].split("TRADES IN PROGRESS")[0]
    sec_2b = html.split("TRADES IN PROGRESS</b>")[1].split("SCAN COMPLETED")[0]
    for i in range(4):
        assert f"FR{i}" in sec_2a
    for i in range(3):
        assert f"ACT{i}" in sec_2b


def test_5_all_exit_event_types_and_visual_indicators():
    events = [
        TradeExitEvent(symbol="E_TP1", event_type="TP1_SCALE", direction=1, entry_price=100.0, exit_price=104.0, pnl_pct=4.0, realized_pnl=200.0, shares=50),
        TradeExitEvent(symbol="E_TP2", event_type="TP2_EXIT", direction=1, entry_price=100.0, exit_price=108.0, pnl_pct=8.0, realized_pnl=400.0, shares=50),
        TradeExitEvent(symbol="E_SL", event_type="STOP_LOSS", direction=1, entry_price=100.0, exit_price=96.0, pnl_pct=-4.0, realized_pnl=-400.0, shares=100),
        TradeExitEvent(symbol="E_BE", event_type="BREAKEVEN_STOP", direction=1, entry_price=100.0, exit_price=100.1, pnl_pct=0.1, realized_pnl=5.0, shares=50),
        TradeExitEvent(symbol="E_TIME1", event_type="TIME_EXIT", direction=1, entry_price=100.0, exit_price=101.0, pnl_pct=1.0, realized_pnl=50.0, shares=50),
        TradeExitEvent(symbol="E_TIME2", event_type="TIME_STOP_10D", direction=1, entry_price=100.0, exit_price=99.0, pnl_pct=-1.0, realized_pnl=-50.0, shares=50),
        TradeExitEvent(symbol="E_FB", event_type="FAILED_BREAKOUT", direction=1, entry_price=100.0, exit_price=98.5, pnl_pct=-1.5, realized_pnl=-150.0, shares=100),
    ]
    html = format_scan_report_html([], market="US", timestamp=TS, exit_events=events)
    _assert_valid_tg_html(html)

    assert "💰 <b>E_TP1</b>  |  <b>PARTIAL PROFIT</b>" in html
    assert "🏆 <b>E_TP2</b>  |  <b>FULL EXIT</b>" in html
    assert "🛑 <b>E_SL</b>  |  <b>STOP LOSS</b>" in html
    assert "-$400.00" in html  # Leading sign before currency
    assert "⚖️ <b>E_BE</b>  |  <b>BREAKEVEN</b>" in html
    assert "⏱️ <b>E_TIME1</b>  |  <b>TIME EXIT</b>" in html
    assert "⏱️ <b>E_TIME2</b>  |  <b>TIME EXIT</b>" in html
    assert "⚠️ <b>E_FB</b>  |  <b>FAILED BREAKOUT</b>" in html


def test_6_7_8_9_empty_section_combinations():
    s_fresh = _fresh_setup("ONLY2A")
    s_active = Setup(symbol="ONLY2B", state=State.BUY, direction=1, entry=100.0, current_price=102.0, stop=97.0, bars_since_breakout=5)
    ev = TradeExitEvent(symbol="ONLY2C", event_type="STOP_LOSS", direction=1, entry_price=50.0, exit_price=48.0, pnl_pct=-4.0, realized_pnl=-200.0, shares=100)

    # 6. Empty 2A
    h_no_2a = format_scan_report_html([s_active], market="US", timestamp=TS, exit_events=[ev])
    _assert_valid_tg_html(h_no_2a)
    assert "<i>(None)</i>" in h_no_2a.split("FRESH ACTIONABLE ENTRIES</b>")[1].split("TRADES IN PROGRESS")[0]
    assert "ONLY2B" in h_no_2a and "ONLY2C" in h_no_2a

    # 7. Empty 2B
    h_no_2b = format_scan_report_html([s_fresh], market="US", timestamp=TS, exit_events=[ev])
    _assert_valid_tg_html(h_no_2b)
    assert "<i>(None)</i>" in h_no_2b.split("TRADES IN PROGRESS</b>")[1].split("EXITS &amp; MANAGEMENT EVENTS")[0]
    assert "ONLY2A" in h_no_2b and "ONLY2C" in h_no_2b

    # 8. Empty 2C
    h_no_2c = format_scan_report_html([s_fresh, s_active], market="US", timestamp=TS, exit_events=[])
    _assert_valid_tg_html(h_no_2c)
    assert "EXITS &amp; MANAGEMENT EVENTS" not in h_no_2c
    assert "Exits <code>0</code>" in h_no_2c

    # 9. All three sections empty
    h_empty = format_scan_report_html([], market="US", timestamp=TS)
    _assert_valid_tg_html(h_empty)
    assert "Fresh <code>0</code> · Active <code>0</code> · Exits <code>0</code>" in h_empty
    assert h_empty.count("<i>(None)</i>") == 2
    assert "📊 <b>SCAN COMPLETED</b>" in h_empty


def test_10_missing_optional_fields_never_invented():
    s_min = Setup(symbol="MINIMAL", state=State.BUY, direction=1, entry=50.0, stop=48.0, bars_since_breakout=0)
    s_act_min = Setup(symbol="ACTMIN", state=State.BUY, direction=1, entry=50.0, stop=48.0, bars_since_breakout=6)

    class _MinExit:
        symbol = "EXMIN"
        event_type = "STOP_LOSS"
        entry_price = 50.0
        exit_price = 48.0
        pnl_pct = None
        realized_pnl = None

    html = format_scan_report_html([s_min, s_act_min], market="US", timestamp=TS, exit_events=[_MinExit()])
    _assert_valid_tg_html(html)

    min_card = html.split("🟢 <b>MINIMAL</b>")[1].split("━━")[0]
    assert "Grade:" not in min_card
    assert "TARGETS:" not in min_card
    assert "Context:" not in min_card
    assert "Current:" not in min_card

    act_card = html.split("📌 <b>ACTMIN</b>")[1].split("━━")[0]
    assert "Current:" not in act_card
    assert "Targets:" not in act_card

    ex_card = html.split("🛑 <b>EXMIN</b>")[1].split("━━")[0]
    assert "P&amp;L:" not in ex_card  # Never invented when missing


def test_11_13_html_escaping_special_chars_and_unicode():
    s_esc = _fresh_setup("M&M<TEST>.NS", direction=1, sector_name="Auto & <EV> 🚀", rs_trend="▲")
    ev_esc = TradeExitEvent(symbol="A&B<1>", event_type="CUSTOM_<TAG>", direction=1, entry_price=100.0, exit_price=101.0, pnl_pct=1.0, realized_pnl=10.0, shares=10)
    html = format_scan_report_html([s_esc], market="NSE", timestamp=TS, exit_events=[ev_esc])
    _assert_valid_tg_html(html)

    assert "M&amp;M&lt;TEST&gt;.NS" in html
    assert "Auto &amp; &lt;EV&gt; 🚀" in html
    assert "A&amp;B&lt;1&gt;" in html
    assert "CUSTOM_&lt;TAG&gt;" in html
    assert "₹" in html


def test_12_long_report_splits_on_card_boundaries_preserving_order():
    many_fresh = [_fresh_setup(f"SYM{i:02d}", score=100 - i, entry=100.0 + i) for i in range(25)]
    html = format_scan_report_html(many_fresh, market="US", timestamp=TS)
    assert len(html) > 4000

    chunks = split_scan_report_html(html, max_length=4000)
    assert len(chunks) >= 2
    for ch in chunks:
        assert len(ch) <= 4000
        _assert_valid_tg_html(ch)

    # Every individual card stays intact (ENTRY + STOP LOSS + Status together in same chunk)
    for i in range(25):
        sym_tag = f"🟢 <b>SYM{i:02d}</b>"
        holding = [c for c in chunks if sym_tag in c]
        assert len(holding) == 1
        card_slice = holding[0].split(sym_tag)[1].split("━━━━━━━━━━━━━━━━━━")[0]
        assert "<b>ENTRY:</b>" in card_slice
        assert "<b>STOP LOSS:</b>" in card_slice
        assert "<i>Status:</i>" in card_slice

    # Exact ordering preserved across chunks
    rejoined = "\n\n".join(chunks)
    positions = [rejoined.index(f"SYM{i:02d}") for i in range(25)]
    assert positions == sorted(positions)


def test_14_15_ordering_preservation_and_signature_compatibility():
    # Unordered scores in input -> sorted descending by score (existing safe_sort_active preserved)
    s_low = _fresh_setup("LOW_S", score=72, grade="B")
    s_high = _fresh_setup("HIGH_S", score=96, grade="A+")
    s_mid = _fresh_setup("MID_S", score=84, grade="A")

    ev1 = TradeExitEvent(symbol="FIRST_EV", event_type="TP1_SCALE", direction=1, entry_price=10.0, exit_price=11.0, pnl_pct=10.0, realized_pnl=100.0, shares=10)
    ev2 = TradeExitEvent(symbol="SECOND_EV", event_type="STOP_LOSS", direction=1, entry_price=20.0, exit_price=19.0, pnl_pct=-5.0, realized_pnl=-100.0, shares=10)

    # Call with minimal positional arg only (legacy caller compatibility like daily_scan.py)
    html_min = format_scan_report_html([s_low, s_high, s_mid])
    _assert_valid_tg_html(html_min)
    assert html_min.index("HIGH_S") < html_min.index("MID_S") < html_min.index("LOW_S")

    # Full keyword signature compatibility (like scheduler.py)
    html_full = format_scan_report_html(
        [s_low, s_high, s_mid],
        market="US",
        timestamp=TS,
        timeframe="1H",
        slate=None,
        exit_events=[ev1, ev2],
        scan_stats={"total": 3, "with_data": 3, "failed_symbols": []},
        open_positions=[],
        allow_shorts=False,
    )
    _assert_valid_tg_html(html_full)
    assert html_full.index("FIRST_EV") < html_full.index("SECOND_EV")


def test_16_fresh_rejected_renders_full_setup_card_details_and_escapes_html():
    s_amgn = Setup(
        symbol="AMGN",
        state=State.BUY,
        direction=1,
        entry=142.50,
        current_price=146.50,
        stop=139.00,
        tp1=146.00,
        tp2=149.50,
        risk_pct=2.46,
        bars_since_breakout=0,
        composite_grade="A+",
        composite_score=91,
        htf_confirmed=True,
        htf_4h_confirmed=True,
        rvol=2.1,
        sector_name="Healthcare",
        sector_rs_pct=3.4,
        rs_trend="▲",
    )
    s_esc = Setup(
        symbol="M&M<R>",
        state=State.SELL,
        direction=-1,
        entry=50.0,
        stop=52.0,
        bars_since_breakout=0,
    )

    slate = ActionableSlate(
        slate_id="slate_rej_cards",
        timestamp="2026-11-24T21:00:00Z",
        profile_name="us_production",
        total_scanned=215,
        portfolio_equity=100000.0,
        max_daily_risk_pct=2.0,
        max_daily_dollar_risk=2000.0,
        selected_trades=[],
        rejected_trades=[
            RejectionRecord(
                symbol="AMGN",
                rejection_code="LATE_CHASE",
                reason="LATE_CHASE: Price extension 2.81% exceeds tolerance 0.46%.",
                setup=s_amgn,
            ),
            RejectionRecord(
                symbol="M&M<R>",
                rejection_code="LATE_CHASE",
                reason="LATE_CHASE: ext <1.5%> & tol >0.4%",
                setup=s_esc,
            ),
        ],
    )

    html = format_scan_report_html([s_amgn, s_esc], market="US", timestamp=TS, slate=slate)
    _assert_valid_tg_html(html)

    assert "<i>(None)</i>" in html.split("FRESH ACTIONABLE ENTRIES</b>")[1].split("TRADES IN PROGRESS")[0]
    assert "⚠️ <b>Fresh but rejected:</b>" in html
    assert "🟢 <b>AMGN</b>" not in html
    assert "🔴 <b>M&amp;M&lt;R&gt;</b>" not in html

    amgn_card = html.split("⚠️ <b>AMGN</b>")[1].split("━━━━━━━━━━━━━━━━━━")[0]
    assert "<b>BUY</b> · Breakout <i>(Rejected)</i>" in amgn_card
    assert "<i>Reason:</i> LATE_CHASE: Price extension 2.81% exceeds tolerance 0.46%." in amgn_card
    assert "<i>Grade:</i> <code>A+ [91]</code>" in amgn_card
    assert "<i>Risk:</i> <code>2.46%</code>" in amgn_card
    assert "<b>ENTRY:</b> <code>$142.50</code>" in amgn_card
    assert "<i>Current:</i> <code>$146.50</code>" in amgn_card
    assert "<b>STOP LOSS:</b> <code>$139.00</code>" in amgn_card
    assert "<b>TARGETS:</b> TP1 <code>$146.00</code>  ·  TP2 <code>$149.50</code>" in amgn_card
    assert "<i>Context:</i> ⭐⭐ Multi-TF (1D+4H) · RVOL <code>2.1x</code> · Healthcare (+3.4% ▲)" in amgn_card
    assert "<i>Status:</i> ⚠️ Rejected — manual review only (do not chase)" in amgn_card

    esc_card = html.split("⚠️ <b>M&amp;M&lt;R&gt;</b>")[1].split("━━━━━━━━━━━━━━━━━━")[0]
    assert "<b>SELL</b> · Breakout <i>(Rejected)</i>" in esc_card
    assert "LATE_CHASE: ext &lt;1.5%&gt; &amp; tol &gt;0.4%" in esc_card
    assert "Grade:" not in esc_card
    assert "TARGETS:" not in esc_card
    assert "Context:" not in esc_card
    assert "Current:" not in esc_card


def test_17_many_fresh_rejected_cards_split_atomically_preserving_order():
    setups = [_fresh_setup(f"REJ{i:02d}", score=95 - i, entry=100.0 + i) for i in range(20)]
    rej_records = [
        RejectionRecord(
            symbol=s.symbol,
            rejection_code="LATE_CHASE",
            reason=f"LATE_CHASE: Price extension {1.0 + i * 0.1:.2f}% exceeds tolerance 0.50%.",
            setup=s,
        )
        for i, s in enumerate(setups)
    ]
    slate = ActionableSlate(
        slate_id="slate_many_rej",
        timestamp="2026-11-24T21:00:00Z",
        profile_name="us_production",
        total_scanned=215,
        portfolio_equity=100000.0,
        max_daily_risk_pct=2.0,
        max_daily_dollar_risk=2000.0,
        selected_trades=[],
        rejected_trades=rej_records,
    )

    html = format_scan_report_html(setups, market="US", timestamp=TS, slate=slate)
    assert len(html) > 4000

    chunks = split_scan_report_html(html, max_length=4000)
    assert len(chunks) >= 2
    for ch in chunks:
        assert len(ch) <= 4000
        _assert_valid_tg_html(ch)

    for i in range(20):
        sym_tag = f"⚠️ <b>REJ{i:02d}</b>"
        holding = [c for c in chunks if sym_tag in c]
        assert len(holding) == 1
        card_slice = holding[0].split(sym_tag)[1].split("━━━━━━━━━━━━━━━━━━")[0]
        assert "<i>Reason:</i>" in card_slice
        assert "<b>ENTRY:</b>" in card_slice
        assert "<b>STOP LOSS:</b>" in card_slice
        assert "<i>Status:</i>" in card_slice

    rejoined = "\n\n".join(chunks)
    positions = [rejoined.index(f"REJ{i:02d}") for i in range(20)]
    assert positions == sorted(positions)
