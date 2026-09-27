# Breakout Scanner — Pine reference + Python multi-symbol engine

A deterministic migration of the agreed Structure → Tight Consolidation → Breakout → Acceptance → Retest → Entry architecture.

**Important:** the exact V2 Pine source from earlier turns is not attached in this turn, so `pine/final_breakout_reference.pine` is a clean consolidated reference build from the rules we explicitly established, not a byte-for-byte reconstruction of an unseen V2 file.

## State machine

IDLE → CONSOLIDATION → ACCEPTANCE_WATCH → ACCEPTED_RETEST_WATCH → BUY/SELL

Failures reset the setup. One setup is kept per symbol/timeframe.

## Run

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python -m breakout_scanner.cli scan --watchlist config/watchlist.txt --period 60d --interval 1h
```

The included Yahoo provider is for development/backtesting. For production real-time scanning, replace it with a broker/exchange websocket adapter.

## Output

The scanner classifies each symbol as `CONSOLIDATION`, `ACCEPTANCE_WATCH`, `ACCEPTED_RETEST_WATCH`, `BUY`, `SELL`, `FAILED`, or `IDLE`, and generates a human-readable rule explanation.

The explanation layer is not an ML guarantee/prediction; it describes what the deterministic engine currently sees and what condition comes next.
