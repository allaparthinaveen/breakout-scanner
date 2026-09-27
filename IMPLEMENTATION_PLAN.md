# Pending implementation plan

The order I recommend is:

```text
                    ┌────────────────────┐
                    │   MARKET DATA      │
                    └─────────┬──────────┘
                              ↓
                    ┌────────────────────┐
                    │ STRUCTURE ENGINE   │
                    └─────────┬──────────┘
                              ↓
                    ┌────────────────────┐
                    │ CONSOLIDATION      │
                    └─────────┬──────────┘
                              ↓
                    ┌────────────────────┐
                    │ BREAKOUT SCORE     │
                    │       ≥ 4/5        │
                    └─────────┬──────────┘
                              ↓
                    ┌────────────────────┐
                    │ ACCEPTANCE ENGINE  │
                    └─────────┬──────────┘
                              ↓
                    ┌────────────────────┐
                    │ RETEST ENGINE      │
                    └─────────┬──────────┘
                              ↓
                    ┌────────────────────┐
                    │ ENTRY ENGINE       │
                    └─────────┬──────────┘
                              ↓
               ┌──────────────┴──────────────┐
               ↓                             ↓
          RISK ENGINE                   SIGNAL BUS
               ↓                             ↓
        SL / TP / SIZE              Dashboard / Alerts
                                             ↓
                                      Prediction Chat
```

## What is already implemented in the Python foundation

**🟢 Core logic**
- Structure detection
- Trend classification
- Tight consolidation
- Frozen support/resistance
- Breakout scoring
- 4/5 breakout threshold
- Acceptance watch
- Acceptance failure
- Retest watch
- Retest hold
- BUY / SELL classification
- Basic entry
- Basic stop
- Basic 2R target
- Multi-symbol scanning
- Rule-based explanation/"prediction chat"

The scanner can therefore eventually show:
```
AAPL   CONSOLIDATION
MSFT   BREAKOUT_READY
NVDA   ACCEPTANCE_WATCH
AMZN   ACCEPTED_RETEST_WATCH
META   BUY
TSLA   SELL
...
```
That's the structure we want.

## The important unfinished pieces

**🟡 Entry generation**
We're going to freeze:
BREAKOUT ≥ 4/5
       ↓
ACCEPTED
       ↓
RETEST touches level
       ↓
close holds breakout side
       ↓
ENTRY

No acceptance = no entry.
No retest = no entry.
That separation is important.

**🔴 Stop-loss**
We'll test three approaches:
1. Box invalidation
2. Retest swing invalidation
3. ATR-based stop

Then evaluate them out of sample, rather than choosing whichever looks best historically.

**🔴 Take profit**
Baseline:
2R

Then evaluate:
1R
1.5R
2R
3R
Trailing

But we should not optimize this to force a 70% win rate.
The real objective is positive expectancy with controlled drawdown.

**🔴 Position sizing**
Eventually:
Account = $10,000
Risk = 0.5%
Risk capital = $50
Entry - Stop = $1
Position = 50 shares

Then portfolio-level limits:
- Max positions
- Max exposure
- Max daily loss
- Max symbol exposure
- Minimum liquidity

**🔴 Multi-symbol scanner**
This is where Python becomes much more useful than Pine.
We'll maintain:
Symbol 1 → independent state machine
Symbol 2 → independent state machine
...
Symbol 20 → independent state machine

rather than recalculating everything as one giant script.

**🔴 Real-time engine**
This is important for the eventual live system.
We should make decisions on confirmed candle close, not every tick.
And give every candle an ID:
BTCUSD | 1H | 2026-09-28 01:00

so the same candle cannot generate duplicate signals.

**🔴 Alerts**
Eventually:
BREAKOUT
ACCEPTANCE WATCH
ACCEPTED
RETEST WATCH
BUY
SELL
FAILED

can feed:
Dashboard
Telegram
Discord
Email
Webhook

**🔴 Backtesting**
This is where I want us to be strict.
The backtester must handle:
- no look-ahead
- candle-close signal timing
- next-bar execution assumptions
- fees
- slippage
- gaps
- stop/target sequencing
- multiple simultaneous positions
- portfolio equity
- drawdown

Then we'll get metrics like:
Trades
Win rate
Profit factor
Expectancy
Average R
Net return
Max drawdown
Exposure

**🔴 Walk-forward validation**
This comes after the backtester.
Something like:
Historical data
      ↓
Train
      ↓
Validation
      ↓
Test
      ↓
Move window forward
      ↓
Repeat

And we'll test different regimes, symbols and timeframes.
That is how we reduce the chance that we're just fitting BTC 1H.

## One correction to our previous goal

I understand what you meant by:
"100 trades → at least 70 wins."

We'll use 70/30 as an aspirational benchmark, not as something we force the system to achieve.
If we tune the rules until the backtest says exactly 70%, there's a serious risk of overfitting.

I'd rather eventually have something like:
120 trades
78 winners
42 losers
65% win rate
+
good average R
+
positive expectancy
+
controlled drawdown

than a fake 75% system that collapses out of sample.

## Where I would take the project next

The next development sequence should be:

**Phase 1 — Python parity**
Make Python produce the same setup states as Pine.

**Phase 2 — Historical backtester**
Prove the engine behaves correctly without look-ahead.

**Phase 3 — Risk engine**
SL + TP + position sizing.

**Phase 4 — 20-symbol scanner**
Persistent state for every symbol.

**Phase 5 — Real-time feed**
Confirmed candle events + websocket/reconnect logic.

**Phase 6 — Dashboard + prediction chat**
"Why is NVDA in ACCEPTANCE_WATCH?" becomes directly explainable.

**Phase 7 — Walk-forward validation**
Separate development data from unseen data.

**Phase 8 — Paper trading**
Live market, zero real money.

**Phase 9 — Production safeguards**
Kill switch, exposure limits, stale-data detection, order reconciliation, etc.

**Phase 10 — Only then live execution.**
