# Breakout Scanner (BS) — Project Context & Roadmap

> **Purpose of this file:** paste/attach this together with the code at the start of any new chat so the assistant does not need the history re-explained.
> **Snapshot date:** 2026-11-15 (Sun). **Owner:** Naveen. **Repo:** private GitHub `allaparthinaveen/breakout-scanner`, deployed on EC2 (systemd service `breakout-scanner`, deploy = `cd ~/breakout-scanner && git pull origin main && sudo systemctl restart breakout-scanner`).
> **Honesty note:** everything marked **[VERIFIED]** I checked by reading code or running it. **[CLAIMED]** = stated by the owner, not independently checked. **[UNKNOWN]** = needs checking next session.

---

## 0. How the assistant should work with Naveen (standing preferences)

- Tone: casual "tech buddy" (English with light Telugu slang — bro, arre, ra), light humour, occasional emoji.
- Be **direct and objective, not sycophantic**: challenge weak logic and shortcuts, never rubber-stamp ("all done, zero loose ends" claims get verified).
- **Analysis first**, then solutions: scope the domain, edge cases and constraints before coding.
- Break complex work into **phases / sequential steps**.
- **Ask clarifying questions** when requirements are ambiguous.
- Prefer evidence (backtests, probes, reproduced defects) over opinion. Say clearly what is unverified.
- Not financial advice; the assistant is not a licensed advisor.

---

## 1. The ultimate goal

Turn a VCP breakout *scanner* into an **alpha-grade, end-to-end trading system** that:

1. Finds a **statistically demonstrable edge** (not just pretty signals),
2. Executes it with realistic costs and strict risk control,
3. Learns from its own results (an internal "BS brain"),
4. Eventually trades **US and NSE** equities, then **options**, through a live broker.

Target outcome: **positive expectancy per trade and controlled drawdown over months**.
It is **not** "profitable every single end of day". Even a real edge loses on many days and weeks (see §4.4). A system that promises daily profit is a red flag, not a goal.

---

## 2. Where we are today (state of the project)

### 2.1 Operating modes
| Mode | How | Notes |
|---|---|---|
| Quick scan | `python3 scan_now.py --watchlist <file> --config <profile.yaml> [--no-tg] [--slate]` | One-shot scan, 1h bars, 60d history |
| Daily pipeline | `daily_scan.py` | Builds dynamic Top-30 liquid universe, scans, slate, Telegram |
| Daemon | `production_daemon.py` (systemd) | Scheduled scans per market, Telegram heartbeat every 40 min, nightly outcome tracker 00:15 UTC |
| Backtester | `breakout_scanner/backtester.py` | Used for validation; cached 1h data in `backtest/cache/*_730d_1h.pkl` |

Timeframe in production profiles: **1h candles, 60d period**, signals form when an hourly candle **closes** (scanner only sees closed candles). A single pre-market scan only shows the previous session's last candle; there was no reliable time-of-day edge in backtests.

### 2.2 Production profiles
- `config/profiles/us_production.yaml` — Nasdaq-50 watchlist (`config/nasdaq_50_watchlist.txt`). **[VERIFIED backtested]**
- `config/profiles/nse_production.yaml` — Nifty-50 (`--market nse`). **Cost model from arithmetic only, never backtested.**
- `crypto_production.yaml` — not reviewed.

Key production settings (US / NSE):
| Setting | US | NSE | Why |
|---|---|---|---|
| `timeframe` | 1h / 60d | 1h / 60d | explicit, no hidden defaults |
| `breakout.allow_shorts` | false | false | **missing key = shorts ON.** US 1h shorts backtested ≈ −0.19R |
| `risk.slippage_pct` | 0.02 | 0.03 | per fill |
| `risk.commission_pct` | 0.005 | 0.10 | NSE includes delivery STT, brokerage, GST, stamp duty |
| `max_friction_r` | 0.15 | 0.20 | friction gate |
| `confirmation_policy.min_auto_entry_score` | 70 | 70 | single block (old files had a **duplicate YAML key**; last silently won) |

### 2.3 Milestones shipped (owner-reported commits)
dd05ddf data-health & provisional exemption · 970d023 audit ledger · 1bfe0c9/eb921c1 nightly what-if tracker · then a further hardening round (review2.patch) · multi-market daemon sessions (NSE/US/crypto), multi-bot Telegram **[CLAIMED, not in the reviewed diffs]** · 244 tests passing **[CLAIMED; I ran only 8 of the new ones — they passed]**.

### 2.4 Latest independent review (review2.patch) — status
**Fixed [VERIFIED]:** ledger dedup via stable `signal_id` (uses `breakout_time` = signal bar timestamp), CONTRACTION/zero-risk rows excluded, ledger errors logged, `.info` removed from US path, SPY regime wired as 3rd bias point, provisional → 50% risk haircut + score<50 suppressed, "DATA DEGRADED" banner, outcome tracker no longer freezes early, one fetch per symbol, atomic output, background thread.

**Still open [VERIFIED by probe — fix before trusting forward data]:**
1. **Circuit breaker silently drops symbols.** After 3 consecutive empty fetches, `provider.candles()` returns empty for all remaining symbols; scanner skips them; banner counts only existing setups → report looks like "no signals". The log claim "marked provisional" is false. A run of 3 bad tickers can kill a healthy scan.
2. **Earnings cache never expires** (`_EARNINGS_CACHE`, process-lifetime). `days_until` freezes; earnings guard never flips to imminent in a long-running daemon; a throttled lookup caches FAILED forever (fail-open).
3. **Outcome tracker fills at the wrong bar.** Scan at 10:31 ET should fill at the 10:30 bar open; tracker fills two bars later (12:30) and measures R vs the original stop → `hypothetical_r` distorted, skipped bars can't stop you out. **Don't trust what-if R until fixed.**
4. **Failed-breakout exit is dead code in the tracker** (reads `price.resistance/support` which the ledger never writes).

Smaller: `schedule.at("00:15","UTC")` needs `pytz` (not in requirements.txt — pandas 2.x brings it, pandas 3 may not); heartbeat missing `\n` after PID/Symbols; 14-calendar-day expiry can clip a 70-bar trade; no daily-SMA50 hard gate (recorded as shadow feature only); no automatic drawdown circuit breaker (manual −6R stop rule); `.calendar` earnings call is per-symbol network I/O with no disk cache.
**[UNKNOWN]:** the multi-market session scheduling (NSE first scan 10:15 IST … ; US 10:31 ET …) was not in the reviewed diffs; verify with `journalctl` and by reading `production_daemon.py` + `market_session.py`.

---

## 3. What is VCP and why do we trade it

### 3.1 The concept
**VCP = Volatility Contraction Pattern** (Mark Minervini's popularisation of a classic base-breakout idea). A stock in an **established uptrend** pauses and builds a **base**. During the base:
- Price swings get **smaller each time** (each pullback is shallower than the last) → volatility contracts.
- **Volume dries up** at the lows (sellers exhausted, no one is distributing).
- Price coils under a **pivot / resistance** level.
- When price closes above the pivot on **expanded volume**, supply has been absorbed and trend continuation is statistically more likely. The stop is placed just below the last (tightest) contraction, so risk is **small and defined**.

The edge claim has two parts: (1) **the entry has positive drift** (momentum continuation after supply is absorbed) and (2) **the stop is structurally tight**, so a modest hit-rate produces positive R.

### 3.2 Worked example (illustrative numbers, not a real trade)
Stock `XYZ`, uptrend, trading around $100 on 1h bars, 1h SMA50 rising.

| Wave | High → Low | Depth | Volume vs 20-bar avg |
|---|---|---|---|
| 1 | 104.0 → 91.5 | 12.0% | 1.0x |
| 2 | 102.5 → 95.5 | 6.8% | 0.8x |
| 3 | 101.0 → 97.5 | 3.5% | 0.6x ← volume dry-up |

- **Decay ratio** ≈ 3.5/12 ≈ 0.3 (each wave ~½ of the previous or tighter) → "tight coil".
- **Pivot:** 101.0 (top of wave 3). **Trigger:** 1h candle **closes** above ~101.1 with volume ≥ ~1.5–2x average and a strong close (high CLV).
- **Entry** ≈ 101.2 (next-bar open in the backtester). **Stop** ≈ 99.0 (below wave-3 low, ATR-aware) → risk ≈ 2.2%.
- **TP1 = +1R ≈ 103.4** → sell 50%, move stop to breakeven. **TP2 = +2R ≈ 105.6.** Time stop ≈ 10 sessions.
- **Sizing:** account $100k, 1% risk = $1,000 → ~454 shares by risk, then capped by `max_position_size_pct` (so the cap often binds).
- **Outcomes in R:** stop = −1.0R; TP1 then breakeven = +0.5R; TP1 then TP2 = +1.5R. A hypothetical 40% win-rate system with these payoffs is roughly breakeven-to-positive; the edge is in the **shape** of the payoffs, which is why costs (friction) matter so much.

### 3.3 Why it *could* give an edge (hypotheses, not proven facts)
1. **Momentum / continuation:** stocks in uptrends tend to continue (well documented in academic literature, but weakened in efficient mega-caps).
2. **Asymmetric payoff:** tight structural stop → small, defined losses; winners can run past 2R.
3. **Selection by volatility regime:** contraction → expansion is a real, repeatable volatility phenomenon.
4. **Discipline edge:** rules, friction gate, earnings filter, slate/risk caps remove emotional errors.
5. **Edge decays if everyone does it** → edge is likely in *filters* (regime, RS, liquidity, event risk), not the raw pattern.

---

## 4. Evidence so far (honest)

### 4.1 Backtest facts **[VERIFIED, in-sample]**
Data: ~730 sessions of 1h bars (≈ Nov 2023 – Oct 2026), large-cap Nasdaq + financials. Engine: next-bar-open entry + slippage, stop-first, gap stops, 50% off at TP1 + breakeven.

| Finding | Result |
|---|---|
| Old costs (min stop 1.33%) | 204 trades, profit factor ≈ 0.92 (negative) |
| New costs (min stop floor 0.75%) | 383 trades, PF ≈ 1.11 |
| Average edge | roughly **+0.05 to +0.1R per trade** (thin) |
| Last ~12 months | statistically flat |
| AAPL / MSFT / NVDA-type mega-caps | lost money |
| Tightest stops (<1%) | best bucket (~+0.22R) |
| Shorts | ≈ −0.19R |
| Cost sensitivity | edge ≈ 0 near **0.08% slippage per fill** |

### 4.2 Why this is not proof
- **In-sample:** settings were adjusted after seeing this data.
- Small sample, confidence interval on mean R includes zero.
- Regime-dependent (flat last 12 months).
- Per-symbol independent backtest ignores portfolio-level correlation and caps.
- NSE and crypto not validated.

### 4.3 Statistical toolbox already used
Bootstrap / date-clustered CIs, random-entry null with identical exits, forward-return null, cost stress, parameter neighbourhood, Monte Carlo drawdown, portfolio-cap simulation, drawdown-breaker replay. Scripts lived in a session scratchpad (`lib.py`, `s1–s6.py`) — **not in the repo yet; should be committed under `research/`.**

### 4.4 Realistic expectations
With ~0.05–0.1R/trade, a handful of signals per day at best, and 1% risk, expect **long flat/losing stretches and multi-R drawdowns even if the edge is real**. Judge over ≥100 closed trades / 3 months, never a day or a week.

---

## 5. How the system works (my understanding of the code)

### 5.1 Pipeline
```
watchlist / dynamic universe (ADV_20 liquidity, stability buffer)
   → YFinanceProvider.candles()  (retry/backoff, closed-candle enforcement, validate_and_clean, 3-strike breaker)
   → BreakoutEngine.run(df)      (per-symbol state machine)
   → MultiSymbolScanner.enrich_bias()   (HTF/daily, sector RS, regime → bias_score, data-health statuses)
   → confluence.enrich_confluence_and_context()  (market regime, earnings proximity, quality modifiers)
   → scorer (composite 0–100, 5 pillars × 20 + modifiers)
   → portfolio_guard / SlatePipeline  (friction gate, rejection gate, sizing, caps, suppression, "AWAITING_CONFIRMATION")
   → report (CLI + Telegram HTML)  → audit ledger (JSONL)  → nightly outcome tracker
```

### 5.2 The engine (state machine)
`IDLE → CONTRACTION → BUY/SELL → FAILED`. Gates: 1h trend (SMA50 ≈ 7 sessions), tight/base range, volume contraction, breakout volume multiplier, CLV, open-confirmation, anti-chase (extended > ~1% or > 0.5 ATR past pivot), base-trap (>2 failed wicks), min/max stop (0.75% / 5%), RR ≥ 1.0, earnings buffer (3 days).

### 5.3 Scoring & grades
Five pillars × 20 pts (contraction physics, volume, trend/RS, HTF, context) + modifiers. Grades: A+ 90–100, A 80–89, B 70–79, C <70. **Missing evidence = 0 points** (a healthy 96 became 64 in a throttled test). `min_auto_entry_score` (production 70) decides whether a setup is auto-enter or `AWAITING_CONFIRMATION` (needs a later bar above signal level).

### 5.4 Bias score (0–3)
1h trend + sector relative strength + (US: SPY/QQQ regime — analyst rating removed; NSE/crypto: index regime). Section 1 (VCP bases) and slate candidates need bias ≥ 3. A failed regime fetch marks the setup provisional.

### 5.5 Friction gate
`round_trip = 2×(slippage+commission)`; `friction_r = round_trip / risk_pct`; reject if > `max_friction_r`. Must use realistic costs: too high silently rejects the best tight setups; too low → over-trading.

### 5.6 Data health
Statuses per setup: `htf_data_status`, `sector_data_status`, `regime_data_status`, `earnings_data_status` (OK/STALE/FAILED/UNKNOWN). Provisional = technical-only signal: shown with a badge, 50% risk, suppressed if score < 50.

### 5.7 Audit ledger & tracker
- `logs/forward_test_ledger.jsonl`: one row per unique signal (`signal_id` = symbol + breakout bar time + direction), `config_hash`, `git_sha`, `scan_id`, price plan, data health, shadow features (`is_above_daily_sma50`), execution fields (for real fills), status (APPROVED / PROVISIONAL / SUPPRESSED / REJECTED).
- `logs/forward_test_outcomes.jsonl`: tracker output (append-only ledger, separate outcomes file), buckets: approved / provisional / suppressed / rejected avg R. **Not trustworthy until §2.4 #3 is fixed.**

### 5.8 Backtester
Per-symbol independent; results in `bt.results` (`realized_r`, `exit_reason`); `run()` returns metrics. Early-return paths return trade **count** (int) not a list — use `bt.results`.

### 5.9 Data providers
yfinance (free, fragile: 429s, per-symbol `.info`/`.calendar` calls are the usual culprits). Alternatives considered: Alpaca (free = IEX-only), Polygon/Massive, Twelve Data, Upstox (NSE). Recommendation so far: **stay on yfinance for 1h bars but batch downloads, disk-cache daily/sector data, and fail loudly**; paid data only when the edge justifies it.

### 5.10 Gotchas to remember
- Missing `allow_shorts` = shorts ON. Duplicate YAML keys silently override. Missing evidence scores 0 (not neutral).
- `scan_now.py` falls back to 1h/60d if the config has no `timeframe`.
- Use `python3 -I` when running untrusted code; sandbox had no pip/network (stubs for `yfinance`, `schedule`).
- Config changes mid-test change `config_hash` and restart the forward-test count.

---

## 6. Validation protocol (applies to everything below)

1. **Freeze the config** during a forward test; bug fixes only.
2. **Sample size:** ≥100 closed trades (≈3 months) before judging; more for sub-bucket claims.
3. **Real fills logged** (entry, exit, slippage). Go-live only if real-fill average R > 0 after costs and slippage ≲ 0.05%/fill.
4. **Size ladder:** paper → 0.25% risk → 0.5% → 1.0%, each rung needs its own sample.
5. **Hard stop:** cumulative −6R (manual until the automatic breaker exists) → stop and review.
6. **Out-of-sample discipline:** any new filter is first a *shadow feature* in the ledger, tested A/B on held-out or forward data, then promoted by a config change (new hash).
7. Every phase below has an **exit gate**; do not start the next phase until the gate is met.

---

## 7. Roadmap — from scanner to trading system

> Order matters: *edge → reliability → execution → options → brain.* A broker or an ML model on top of an unproven edge only loses money faster.

### Phase 0 — Make the pipeline trustworthy (days)
- Fix the 4 open defects (§2.4) + `pytz` in requirements, heartbeat newline, expiry vs 70 bars.
- Loud failure: if `symbols_scanned / symbols_total < 90%` → Telegram "SCAN INCOMPLETE n/N", never "no signals".
- Earnings: TTL cache (a few hours), never cache FAILED/UNKNOWN, treat UNKNOWN as "warn" not "pass".
- Disk cache for daily/sector/benchmark data (one SPY/sector fetch per day, not per symbol).
- Commit `research/` backtest harness with a README; add a regression test that replays a saved Yahoo-outage scenario.
- **Gate:** simulated outage produces a loud incomplete-scan alert; tracker fill probe passes; full suite green on EC2.

### Phase 1 — Find (or kill) the edge (4–8 weeks, parallel with forward test)
Goal: know *where* the edge lives instead of guessing.
1. **Out-of-sample framework:** walk-forward (train windows → test windows), purged splits, final untouched hold-out.
2. **Bigger, more honest data:** 5+ years, 100–500 liquid names (not just mega-caps), survivorship-bias-aware universe, include delisted names if possible.
3. **Portfolio-level backtest:** concurrent-position caps, sector/correlation limits, daily-loss limit, shared equity curve.
4. **Feature research (each as a shadow feature first):** daily SMA50/200 trend gate, relative strength vs SPY/sector (rank, not just sign), distance from 52-week high, base length/depth, volume dry-up quality, market-regime filter (SPY trend/VIX/breadth), time-of-day, gap behaviour, earnings-season proximity.
5. **Exit research:** fixed 1R/2R vs ATR trailing vs structure trailing, time stop length, partial sizes. Compare on expectancy **and** drawdown.
6. **Universe selection as an edge:** mid/large-cap with higher volatility often beats mega-caps for this pattern; rank universe by RS and liquidity weekly.
7. **Regime awareness:** most breakout strategies earn in trending/risk-on regimes and bleed in chop. Measure it; add an on/off or size-scaling rule; the current flat 12 months is the case to study.
8. **Cost realism:** measure real slippage from the ledger's execution fields; recalibrate `slippage_pct`.
- **Gate:** at least one configuration with positive expectancy **out-of-sample**, a 95% CI above zero (or a clear plan if not), and survival under 2× cost stress. If nothing passes, the honest conclusion is that VCP alone isn't an edge on this universe — pivot the pattern/universe, don't add leverage.

### Phase 2 — Comprehensive options layer (after Phase 1 shows an edge)
**Current state:** the options config only screens *underlying* momentum, with a manual-chain notice (delta 0.40–0.60, 14–30 DTE, spread <10%, IV <50%). That is a stock scanner with a reminder, not an options system.

**What "comprehensive" means — an options decision engine:**
1. **Data:** a real options chain source (Yahoo is poor/unreliable). Candidates: Polygon/Massive options, Tradier, Alpaca options data, broker API (IBKR). NSE F&O via Upstox/Zerodha/Angel feeds.
2. **Contract selection engine** per signal: filter expiries (e.g. 21–45 DTE, avoid expiry inside earnings), liquidity (open interest, volume, bid-ask % of mid), delta band, then rank by *cost of edge*.
3. **Volatility context:** IV rank/percentile, IV vs realised vol, term structure; **avoid buying expensive IV** (post-contraction IV is often cheap — a plus — but pre-earnings IV is crush risk).
4. **Structure choice (rule-based):** long call when IV low; **call debit spread** when IV mid/high or to cap cost; consider risk-defined structures only (no naked short) until proven.
5. **Expected-move test:** premium/breakeven vs the setup's TP1/TP2 — require the structure's reward-to-risk to be ≥ the underlying setup's; reject if breakeven needs more than ~the 1R move.
6. **Greeks & risk:** per-position delta/theta/vega, portfolio-level greek limits, max premium per trade (max loss = premium), theta budget, early-exit rules (time stop at ~50% of DTE, exit at stop/TP of underlying **and** at premium-based stops).
7. **Position sizing in premium terms** (not stock stop distance — the existing warning stands).
8. **Backtesting reality:** historical option chains are expensive/limited. Plan: (a) **forward-log** the chosen contract + quotes at signal time and +1h/+1d/exit to build real data; (b) approximate backtest with Black-Scholes + historical IV only for sanity, never as proof.
9. **NSE options:** lot sizes, weekly/monthly expiry rules, STT on exercise, index vs stock options differ — separate profile, separate validation.
- **Gate:** 100+ logged option trades (paper) where the option P&L beats the stock P&L per unit of risk, after real spreads.

### Phase 3 — Live broker integration
**Architecture (build it so mistakes can't become disasters):**
- `BrokerAdapter` interface (`place_order`, `cancel`, `positions`, `account`, `fills`) with adapters per broker; start with **paper** accounts.
- Candidate brokers: US — Alpaca (paper/live API), Interactive Brokers; NSE — Zerodha Kite Connect, Upstox, Angel One SmartAPI (verify current API terms, fees and order-type support before choosing).
- **Order Manager:** idempotent order IDs (use `signal_id`), bracket/OCO (entry + stop + TP) placed server-side so a daemon crash never leaves a naked position, retry with backoff, reconciliation loop (broker truth vs ledger every N minutes).
- **Pre-trade risk layer (deterministic, cannot be overridden by the brain):** per-trade risk %, max positions, sector/correlation caps, daily loss limit, kill-switch (Telegram `/halt`), max order size, price-sanity bands, market-hours check, stale-data check, duplicate-order check.
- **Human-in-the-loop first:** Telegram "Approve / Skip" buttons → order; full auto only after weeks of clean reconciliation.
- **Execution quality:** record intended vs actual fill, slippage, latency; feed back into the cost model.
- **Ladder:** paper → tiny live size → scale on evidence (§6.4).
- Secrets management (env/AWS Secrets Manager, never in git), audit log of every order.
- **Gate:** 4+ weeks paper with zero unreconciled positions, slippage within the model, kill-switch tested.

### Phase 4 — The "BS Brain" (learning layer)
**Principle:** the brain **advises and ranks**; the deterministic risk layer **vetoes**. It never edits live config on its own.

**Stage A — Data flywheel (start now):** every ledger row already stores features (score pillars, bias, data health, price geometry, shadow features). Add: market-regime features, sector/RS ranks, time-of-day, volatility state, earnings distance, and **labels** (realised R, MFE/MAE, hit TP1/stop, time-to-resolution) from real fills first, tracker second.
**Stage B — Analytics, no ML:** bucket analysis (score band, sector, regime, hour, stop size) with confidence intervals; weekly auto-report on Telegram. This alone often finds the filters that matter.
**Stage C — Meta-labeling model:** keep the rule-based signal as the *primary* model; train a small model (logistic regression / gradient boosting) to estimate P(win) or expected R for each signal and use it to **skip or size down**. Requirements: walk-forward CV, purged/embargoed splits, calibrated probabilities, must **beat the rule-only baseline OOS** by a margin, feature importance sanity checks. Training data: backtests over many symbols/years (thousands of labelled signals); live ledger is the validation set.
**Stage D — Regime classifier:** trend / chop / risk-off detection to switch the strategy on/off or scale risk.
**Stage E — Adaptive sizing/selection:** contextual bandit or Bayesian updating for which setup families deserve more risk — with hard caps, slow learning rates, and shadow-mode comparison.
**Stage F — LLM assistant (not decision maker):** daily post-mortems, anomaly explanations ("why did Section 2B empty out?"), drift alerts. Decisions stay quantitative.
**Safeguards:** model versioning tied to `config_hash`/`git_sha`; **shadow mode** (log what the brain would have done) for ≥ 3 months before it can influence real size; automatic rollback if live performance deviates from the model's calibration; no look-ahead features; sample-size floors before any bucket is acted on.
- **Gate:** brain-filtered shadow results beat the unfiltered baseline OOS with CI support, over a meaningful sample.

### Phase 5 — System reliability, monitoring & scale
- Observability: structured logs, scan completeness metrics, Prometheus/CloudWatch dashboard, alerting on missed scans, stale data, breaker trips.
- Performance dashboard: equity curve in R, drawdown, rolling expectancy, bucket stats, slippage drift, config-hash timeline.
- Data redundancy: second provider fallback; daily EOD reconciliation of Yahoo vs second source.
- Infra: container/IaC, automated deploy with tests as a gate, backups of `logs/` and models, staging vs prod daemon.
- Portfolio construction: sector/correlation-aware slate, volatility-targeted sizing, drawdown-scaled risk.
- Additional strategies for diversification (only after VCP is validated): pullback-to-MA continuation, relative-strength rotation, mean-reversion in chop — each with its own validation.

---

## 8. Suggested next 30 days (concrete)
1. Week 1: Phase 0 fixes; run forward test (paper) on US + NSE with frozen config; log real fills manually.
2. Week 1–2: commit `research/` harness; set up bigger dataset (100–500 names, 5 yrs) and portfolio-level backtester.
3. Week 2–4: shadow-feature A/B (daily SMA50 gate, RS rank, regime), exit comparison, walk-forward report.
4. Week 4: decision meeting — does any configuration clear the Phase-1 gate? If yes → Phase 2/3 design; if no → adjust universe/pattern before building more.

---

## 9. Starter prompt for a new chat
> "Attached: `PROJECT_CONTEXT.md` + the latest code (zip or diff). Read the context first. Today I want to work on: **<task>**. Follow my standing preferences in §0. Verify any 'done' claims in the code before trusting them."

## 10. Glossary
**R** = initial risk per trade (entry − stop). **PF** = profit factor (gross win / gross loss). **VDU** = volume dry-up. **CLV** = close location value (where the candle closed within its range). **Friction** = slippage + commission, expressed in R. **Provisional** = signal computed with incomplete external data. **Shadow feature** = value logged but not used for decisions. **Meta-labeling** = a second model that decides whether to take the first model's signal. **OOS** = out-of-sample.

## 11. Decision log
- 2026-11 : costs recalibrated (US 0.02% + 0.005%; NSE 0.03% + 0.10%), long-only, single confirmation policy (70).
- 2026-11 : stay on yfinance; batch + cache + fail loudly rather than switch vendor.
- 2026-11 : forward test regardless of in-sample caution; paper/0.25% risk until 100 trades.
- 2026-11-15: roadmap order set: Phase 0 trust → Phase 1 edge → Phase 2 options → Phase 3 broker → Phase 4 brain → Phase 5 reliability.
