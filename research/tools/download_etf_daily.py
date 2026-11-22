"""
Download full-history (1998-2026) auto-adjusted daily OHLCV bars for the 9 original
SPDR Select Sector ETFs + SPY benchmark + ^IRX 13-week US T-bill yield index.

Outputs:
  - data/research_etf/{SYM}_1d.pkl
  - research_out/phase1/T004/manifest.csv
"""

import argparse
import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data" / "research_etf"
OUT_DIR = ROOT / "research" / "phase1" / "T004"
MANIFEST_PATH = OUT_DIR / "manifest.csv"

SECTOR_ETFS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
BENCHMARKS = ["SPY", "^IRX"]
ALL_SYMBOLS = BENCHMARKS + SECTOR_ETFS
REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]
MAX_RETRIES = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("download_etf_daily")


def clean_etf_df(raw: Optional[pd.DataFrame], sym: str) -> Optional[pd.DataFrame]:
    if raw is None or raw.empty:
        return None
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = set(df.columns.get_level_values(0))
        if set(REQUIRED_COLS).issubset(lvl0):
            df.columns = df.columns.get_level_values(0)
        else:
            df.columns = df.columns.get_level_values(1)
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated(keep="first")]
    if not set(REQUIRED_COLS).issubset(df.columns):
        return None
    df = df[REQUIRED_COLS].copy()
    for col in REQUIRED_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df.index.name = "Date"
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]

    if sym == "^IRX":
        # 13-week T-bill yield in percent; allow zero volume & zero/near-zero yields
        df = df.dropna(subset=["Close"])
        df["Close"] = df["Close"].clip(lower=0.0)
        df = df.loc[df.index >= pd.Timestamp("1998-01-01")].copy()
    else:
        df = df.dropna(subset=REQUIRED_COLS)
        ok = (df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0) & (df["Volume"] > 0)
        df = df.loc[ok].copy()
    return df


def _fetch_one(sym: str) -> Optional[pd.DataFrame]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = yf.download(sym, period="max", interval="1d", auto_adjust=True, progress=False)
            if raw is not None and not raw.empty:
                return raw
        except Exception as exc:
            logger.warning("Retry %d/%d failed for %s: %s", attempt, MAX_RETRIES, sym, exc)
        if attempt < MAX_RETRIES:
            time.sleep(2 ** (attempt - 1))
    return None


def run_download(force: bool = False) -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tz_dir = Path(tempfile.gettempdir()) / "breakout_scanner_yf_etf_tz_cache"
    tz_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(tz_dir.resolve()))

    rows: List[Dict] = []
    for sym in ALL_SYMBOLS:
        fp = DATA_DIR / f"{sym}_1d.pkl"
        df = None
        if not force and fp.exists():
            df = pd.read_pickle(fp)
        else:
            raw = _fetch_one(sym)
            df = clean_etf_df(raw, sym)
            if df is not None and not df.empty:
                df.to_pickle(fp)
            time.sleep(0.3)

        if df is None or df.empty:
            rows.append({"symbol": sym, "bars": 0, "first_date": "", "last_date": "", "status": "FAILED"})
        else:
            rows.append(
                {
                    "symbol": sym,
                    "bars": int(len(df)),
                    "first_date": str(df.index.min().date()),
                    "last_date": str(df.index.max().date()),
                    "status": "OK",
                }
            )

    manifest = pd.DataFrame(rows, columns=["symbol", "bars", "first_date", "last_date", "status"])
    manifest.to_csv(MANIFEST_PATH, index=False)
    print(manifest.to_string(index=False))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Download 1998-2026 adjusted daily ETF + ^IRX history.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    m = run_download(force=args.force)
    return 0 if (m["status"] == "OK").all() else 1


if __name__ == "__main__":
    sys.exit(main())
