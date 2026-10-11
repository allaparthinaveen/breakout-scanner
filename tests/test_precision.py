import numpy as np
import pandas as pd
from breakout_scanner.config import load_config
from breakout_scanner.engine import BreakoutEngine
from breakout_scanner.models import Setup, State
from breakout_scanner.sectors import get_sector_benchmark

def test_sector_benchmark():
    # Test NSE mapping
    sec, bench = get_sector_benchmark('TCS.NS')
    assert sec == 'IT'
    assert bench == '^CNXIT'
    
    sec_bank, bench_bank = get_sector_benchmark('HDFCBANK.NS')
    assert sec_bank == 'Banking'
    assert bench_bank == '^NSEBANK'
    
    # Test US mapping
    sec_nvda, bench_nvda = get_sector_benchmark('NVDA')
    assert sec_nvda == 'Semis'
    assert bench_nvda == 'SMH'
    
    # Test Crypto mapping
    sec_crypto, bench_crypto = get_sector_benchmark('SOL-USD')
    assert sec_crypto == 'Crypto'
    assert bench_crypto == 'BTC-USD'
    
    # Test generic fallback
    sec_fb, bench_fb = get_sector_benchmark('UNKNOWN.NS')
    assert sec_fb == 'NSE Market'
    assert bench_fb == '^NSEI'

def test_setup_precision_defaults():
    s = Setup('AAPL')
    assert s.htf_trend == 0
    assert s.htf_confirmed is False
    assert s.sector_name is None
    assert s.sector_rs_pct is None
    assert s.breakout_type == "BREAKOUT"
    assert s.retest_confirmed is False
