import numpy as np, pandas as pd
from unittest.mock import MagicMock
from breakout_scanner.config import load_config
from breakout_scanner.engine import BreakoutEngine
from breakout_scanner.models import Setup, State
from breakout_scanner.scanner import MultiSymbolScanner

def test_smoke():
    n=120;c=np.linspace(100,105,n);df=pd.DataFrame({'Open':c-.1,'High':c+.2,'Low':c-.2,'Close':c,'Volume':np.ones(n)*100})
    s=BreakoutEngine(load_config(),'TEST').run(df);assert s.symbol=='TEST'

def test_scanner_report_hierarchy(capsys):
    scanner = MultiSymbolScanner(provider=MagicMock(), cfg={})
    s_fresh = Setup(symbol="FRESH", state=State.BUY, direction=1, entry=100.0, current_price=100.2, stop=98.0, tp1=102.0, tp2=104.0, bars_since_breakout=0, composite_grade="A", composite_score=85)
    s_progress = Setup(symbol="PROG", state=State.BUY, direction=1, entry=100.0, current_price=105.0, stop=98.0, tp1=102.0, tp2=104.0, bars_since_breakout=5, composite_grade="B", composite_score=75)
    s_failed = Setup(symbol="FAIL", state=State.FAILED, direction=1, current_price=97.0, failure_reason="Stopped out")
    s_base = Setup(symbol="BASE", state=State.CONTRACTION, bias_score=3, resistance=110.0, support=100.0, base_width_pct=5.0)

    scanner.report([s_fresh, s_progress, s_failed, s_base])
    out = capsys.readouterr().out

    idx_2a = out.find("SECTION 2A: 🟢 FRESH ACTIONABLE ENTRIES")
    idx_2b = out.find("SECTION 2B: 🔒 TRADES IN PROGRESS")
    idx_2c = out.find("SECTION 2C: ⚠️ FAILED BREAKOUT EXITS")
    idx_1 = out.find("SECTION 1: ◉ VCP BASE")

    assert idx_2a != -1, "SECTION 2A must be present"
    assert idx_2b != -1, "SECTION 2B must be present"
    assert idx_2c != -1, "SECTION 2C must be present"
    assert idx_1 != -1, "SECTION 1 must be present"

    assert idx_2a < idx_2b < idx_2c < idx_1, "Hierarchy must be 2A -> 2B -> 2C -> 1"

