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
    'INDUSINDBK.NS': ('Banking', '^NSEBANK'),
    
    # Non-Bank Financial Services & Insurance
    'BAJFINANCE.NS': ('Fin Services', 'NIFTY_FIN_SERVICE.NS'),
    'BAJAJFINSV.NS': ('Fin Services', 'NIFTY_FIN_SERVICE.NS'),
    'LICI.NS': ('Fin Services', 'NIFTY_FIN_SERVICE.NS'),
    'SBILIFE.NS': ('Fin Services', 'NIFTY_FIN_SERVICE.NS'),
    'HDFCLIFE.NS': ('Fin Services', 'NIFTY_FIN_SERVICE.NS'),
    'SHRIRAMFIN.NS': ('Fin Services', 'NIFTY_FIN_SERVICE.NS'),

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

    # Energy & Oil
    'RELIANCE.NS': ('Energy', '^CNXENERGY'),
    'ONGC.NS': ('Energy', '^CNXENERGY'),
    'IOC.NS': ('Energy', '^CNXENERGY'),
    'BPCL.NS': ('Energy', '^CNXENERGY'),
    'GAIL.NS': ('Energy', '^CNXENERGY'),
    'OIL.NS': ('Energy', '^CNXENERGY'),

    # Utilities, Power & Infrastructure
    'NTPC.NS': ('Utilities', '^CNXINFRA'),
    'POWERGRID.NS': ('Utilities', '^CNXINFRA'),
    'TATAPOWER.NS': ('Utilities', '^CNXINFRA'),
    'ADANIGREEN.NS': ('Utilities', '^CNXINFRA'),

    # ----------------------------------------------------
    # US Market Sector Mappings
    # ----------------------------------------------------
    # Semiconductors & Tech Hardware
    'NVDA': ('Semis', 'SMH'),
    'TSM': ('Semis', 'SMH'),
    'ASML': ('Semis', 'SMH'),
    'INTC': ('Semis', 'SMH'),
    'AMD': ('Semis', 'SMH'),
    'QCOM': ('Semis', 'SMH'),
    'AVGO': ('Semis', 'SMH'),

    # Big Tech, Software & Cloud
    'AAPL': ('Tech', 'QQQ'),
    'MSFT': ('Tech', 'QQQ'),
    'ACN': ('Tech Services', 'XLK'),
    'PLTR': ('Enterprise Software', 'XLK'),
    'ORCL': ('Enterprise Software', 'XLK'),
    'CRM': ('Cloud/SaaS', 'XLK'),
    'NOW': ('Cloud/SaaS', 'XLK'),
    'ADBE': ('Software', 'XLK'),
    'CSCO': ('Networking', 'XLK'),
    'PANW': ('Cybersecurity', 'XLK'),
    'ANET': ('Cloud Networking', 'XLK'),

    # Media, Entertainment & Communication
    'NFLX': ('Media', 'XLC'),
    'TTWO': ('Gaming/Media', 'XLC'),
    'EA': ('Gaming', 'XLC'),
    'GOOGL': ('Media/Search', 'XLC'),
    'META': ('Social Media', 'XLC'),
    'DIS': ('Entertainment', 'XLC'),
    'CMCSA': ('Telecom/Media', 'XLC'),
    'TMUS': ('Telecom', 'XLC'),
    'VZ': ('Telecom', 'XLC'),
    'T': ('Telecom', 'XLC'),

    # Financials & Banking
    'GS': ('Banks', 'XLF'),
    'BAC': ('Banks', 'XLF'),
    'JPM': ('Banks', 'XLF'),
    'MS': ('Investment Banks', 'XLF'),
    'C': ('Banks', 'XLF'),
    'BX': ('Private Equity', 'XLF'),
    'APO': ('Private Equity', 'XLF'),
    'KKR': ('Private Equity', 'XLF'),
    'BLK': ('Asset Mgmt', 'XLF'),
    'SCHW': ('Brokerage', 'XLF'),
    'HOOD': ('Fintech', 'XLF'),
    'AXP': ('Financials', 'XLF'),
    'V': ('Payments', 'XLK'),
    'MA': ('Payments', 'XLK'),

    # Healthcare & Biotech
    'UNH': ('Healthcare', 'XLV'),
    'GSK': ('Pharma', 'XLV'),
    'LLY': ('Pharma', 'XLV'),
    'JNJ': ('Healthcare', 'XLV'),
    'GILD': ('Biotech', 'XLV'),
    'ABT': ('Healthcare', 'XLV'),
    'MDT': ('MedTech', 'XLV'),
    'ISRG': ('MedTech', 'XLV'),
    'SYK': ('MedTech', 'XLV'),
    'BSX': ('MedTech', 'XLV'),
    'REGN': ('Biotech', 'XLV'),
    'VRTX': ('Biotech', 'XLV'),

    # Consumer Staples & Defensive
    'KO': ('Staples', 'XLP'),
    'PEP': ('Staples', 'XLP'),
    'PG': ('Staples', 'XLP'),
    'COST': ('Staples/Retail', 'XLP'),
    'WMT': ('Staples/Retail', 'XLP'),
    'PM': ('Staples', 'XLP'),
    'MO': ('Staples', 'XLP'),
    'MDLZ': ('Staples', 'XLP'),
    'CL': ('Staples', 'XLP'),

    # Consumer Discretionary & Retail
    'TSLA': ('Discretionary', 'XLY'),
    'NKE': ('Discretionary', 'XLY'),
    'AMZN': ('E-Commerce', 'XLY'),
    'MCD': ('Restaurants', 'XLY'),
    'SBUX': ('Restaurants', 'XLY'),
    'CMG': ('Restaurants', 'XLY'),
    'HD': ('Home Improvement', 'XLY'),
    'LOW': ('Home Improvement', 'XLY'),
    'BKNG': ('Travel', 'XLY'),
    'ABNB': ('Travel', 'XLY'),

    # Energy, Materials & Utilities
    'XOM': ('Energy', 'XLE'),
    'CVX': ('Energy', 'XLE'),
    'FCX': ('Materials', 'XLB'),
    'LIN': ('Chemicals', 'XLB'),
    'NEE': ('Utilities', 'XLU'),
    'SO': ('Utilities', 'XLU'),
    'DUK': ('Utilities', 'XLU'),

    # ----------------------------------------------------
    # Hong Kong Sector Mappings
    # ----------------------------------------------------
    # Technology / Cloud / Semis
    '0700.HK': ('Tech/Gaming', '^HSI'),
    '9988.HK': ('Tech/E-Commerce', '^HSI'),
    '3690.HK': ('Tech/Delivery', '^HSI'),
    '9888.HK': ('Tech/AI', '^HSI'),
    '0981.HK': ('Semis', '^HSI'),
    '1810.HK': ('Hardware/EV', '^HSI'),
    # Financials & Insurance
    '0005.HK': ('Banking', '^HSI'),
    '1299.HK': ('Insurance', '^HSI'),
    '0388.HK': ('Exchange', '^HSI'),
    '2318.HK': ('Insurance', '^HSI'),
    '0939.HK': ('Banking', '^HSI'),
    '1398.HK': ('Banking', '^HSI'),
    # Auto & Consumer
    '1211.HK': ('Auto/EV', '^HSI'),
    '2015.HK': ('Auto/EV', '^HSI'),
    '9999.HK': ('Gaming', '^HSI'),
    '9618.HK': ('E-Commerce', '^HSI'),
    # Healthcare
    '2269.HK': ('Biotech/CDMO', '^HSI'),
    '2359.HK': ('Biotech/CXO', '^HSI'),
    '6160.HK': ('Biotech/Oncology', '^HSI'),
    # Energy
    '0883.HK': ('Energy', '^HSI'),
    '0857.HK': ('Energy', '^HSI'),

    # ----------------------------------------------------
    # Japan Sector Mappings
    # ----------------------------------------------------
    # Semiconductors & Electronics
    '8035.T': ('Semis', '^N225'),
    '6857.T': ('Semis', '^N225'),
    '6758.T': ('Consumer Electronics/Gaming', '^N225'),
    '6861.T': ('Automation/Sensors', '^N225'),
    '6920.T': ('Semis/EUV', '^N225'),
    # Automotive
    '7203.T': ('Auto', '^N225'),
    '7267.T': ('Auto', '^N225'),
    # Gaming & Entertainment
    '7974.T': ('Gaming', '^N225'),
    # Financials & Banking
    '8306.T': ('Banking', '^N225'),
    '8316.T': ('Banking', '^N225'),
    '8766.T': ('Insurance', '^N225'),
    # Tech / Telecom / Investment
    '9984.T': ('Tech Conglomerate', '^N225'),
    '9432.T': ('Telecom', '^N225'),
    '9434.T': ('Telecom', '^N225'),
    # Retail
    '9983.T': ('Apparel/Retail', '^N225'),
    # Pharma
    '4502.T': ('Pharma', '^N225'),
    '4568.T': ('Pharma/Oncology', '^N225'),
    # Trading Houses & Industrials
    '8058.T': ('Trading House', '^N225'),
    '8001.T': ('Trading House', '^N225'),
    '4063.T': ('Chemicals/Semis', '^N225'),
    '6501.T': ('Industrials/Energy', '^N225'),
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
    elif symbol.endswith('.HK'):
        return ('HK Market', '^HSI')
    elif symbol.endswith('.T'):
        return ('Japan Market', '^N225')
    else:
        return ('US Market', 'SPY')

