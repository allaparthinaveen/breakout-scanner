"""
Sector benchmarks and asset-class mapping for relative strength analysis.
"""

from typing import Tuple

SECTOR_MAP = {
    # ----------------------------------------------------
    # NSE India Sector Mappings
    # ----------------------------------------------------
    # Technology / IT Services
    'TCS.NS': ('IT', '^CNXIT'),
    'INFY.NS': ('IT', '^CNXIT'),
    'HCLTECH.NS': ('IT', '^CNXIT'),
    'WIPRO.NS': ('IT', '^CNXIT'),
    'TECHM.NS': ('IT', '^CNXIT'),
    'PERSISTENT.NS': ('IT', '^CNXIT'),
    'COFORGE.NS': ('IT', '^CNXIT'),
    'MPHASIS.NS': ('IT', '^CNXIT'),
    'TATAELXSI.NS': ('IT', '^CNXIT'),

    # Financials & Banking
    'HDFCBANK.NS': ('Banking', '^NSEBANK'),
    'ICICIBANK.NS': ('Banking', '^NSEBANK'),
    'SBIN.NS': ('Banking', '^NSEBANK'),
    'KOTAKBANK.NS': ('Banking', '^NSEBANK'),
    'AXISBANK.NS': ('Banking', '^NSEBANK'),
    'BAJFINANCE.NS': ('Financials', '^NSEBANK'),
    'BAJAJFINSV.NS': ('Financials', '^NSEBANK'),
    'LICI.NS': ('Financials', '^NSEBANK'),
    'SBILIFE.NS': ('Financials', '^NSEBANK'),
    'SHRIRAMFIN.NS': ('Financials', '^NSEBANK'),

    # Auto & Mobility
    'MARUTI.NS': ('Auto', '^CNXAUTO'),
    'M&M.NS': ('Auto', '^CNXAUTO'),
    'TATAMOTORS.NS': ('Auto', '^CNXAUTO'),
    'EICHERMOT.NS': ('Auto', '^CNXAUTO'),
    'BAJAJ-AUTO.NS': ('Auto', '^CNXAUTO'),

    # Healthcare & Pharma
    'SUNPHARMA.NS': ('Pharma', '^CNXPHARMA'),
    'DIVISLAB.NS': ('Pharma', '^CNXPHARMA'),
    'CIPLA.NS': ('Pharma', '^CNXPHARMA'),
    'DRREDDY.NS': ('Pharma', '^CNXPHARMA'),
    'TORNTPHARM.NS': ('Pharma', '^CNXPHARMA'),
    'ZYDUSLIFE.NS': ('Pharma', '^CNXPHARMA'),
    'APOLLOHOSP.NS': ('Pharma', '^CNXPHARMA'),
    'AUROPHARMA.NS': ('Pharma', '^CNXPHARMA'),
    'MANKIND.NS': ('Pharma', '^CNXPHARMA'),
    'GLENMARK.NS': ('Pharma', '^CNXPHARMA'),

    # Consumer Staples / FMCG
    'HINDUNILVR.NS': ('FMCG', '^CNXFMCG'),
    'ITC.NS': ('FMCG', '^CNXFMCG'),
    'NESTLEIND.NS': ('FMCG', '^CNXFMCG'),
    'BRITANNIA.NS': ('FMCG', '^CNXFMCG'),
    'TATACONSUM.NS': ('FMCG', '^CNXFMCG'),
    'DABUR.NS': ('FMCG', '^CNXFMCG'),
    'GODREJCP.NS': ('FMCG', '^CNXFMCG'),
    'MARICO.NS': ('FMCG', '^CNXFMCG'),
    'COLPAL.NS': ('FMCG', '^CNXFMCG'),
    'EMAMILTD.NS': ('FMCG', '^CNXFMCG'),

    # Energy, Oil & Power
    'RELIANCE.NS': ('Energy', '^CNXENERGY'),
    'ONGC.NS': ('Energy', '^CNXENERGY'),
    'IOC.NS': ('Energy', '^CNXENERGY'),
    'BPCL.NS': ('Energy', '^CNXENERGY'),
    'GAIL.NS': ('Energy', '^CNXENERGY'),
    'NTPC.NS': ('Energy', '^CNXENERGY'),
    'POWERGRID.NS': ('Energy', '^CNXENERGY'),
    'ADANIGREEN.NS': ('Energy', '^CNXENERGY'),
    'TATAPOWER.NS': ('Energy', '^CNXENERGY'),
    'OIL.NS': ('Energy', '^CNXENERGY'),

    # ----------------------------------------------------
    # US Market Sector Mappings
    # ----------------------------------------------------
    # Semiconductors & Tech Hardware
    'NVDA': ('Semis', 'SMH'),
    'TSM': ('Semis', 'SMH'),
    'ASML': ('Semis', 'SMH'),
    'INTC': ('Semis', 'SMH'),

    # Big Tech / Cloud
    'AAPL': ('Tech', 'QQQ'),
    'MSFT': ('Tech', 'QQQ'),
    'ACN': ('Tech Services', 'XLK'),

    # Media & Entertainment
    'NFLX': ('Media', 'XLC'),
    'TTWO': ('Gaming/Media', 'XLC'),

    # Financials
    'GS': ('Banks', 'XLF'),
    'BAC': ('Banks', 'XLF'),

    # Healthcare
    'UNH': ('Healthcare', 'XLV'),
    'GSK': ('Healthcare', 'XLV'),

    # Consumer Staples & Discretionary
    'KO': ('Staples', 'XLP'),
    'TSLA': ('Discretionary', 'XLY'),
    'NKE': ('Discretionary', 'XLY'),
}

def get_sector_benchmark(symbol: str) -> Tuple[str, str]:
    """
    Returns (sector_name, benchmark_symbol) for a given stock symbol.
    Defaults to broad index if specific sector mapping is not configured.
    """
    if symbol in SECTOR_MAP:
        return SECTOR_MAP[symbol]
    
    # Asset class fallbacks
    if symbol.endswith('-USD') or symbol.endswith('-USDT'):
        return ('Crypto', 'BTC-USD')
    elif symbol.endswith('.NS') or symbol.endswith('.BO'):
        return ('NSE Market', '^NSEI')
    else:
        return ('US Market', 'SPY')
