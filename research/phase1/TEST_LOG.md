# Quantitative Research Test-Count Log (Multiple-Testing Audit)

> **Purpose:** Track **every** strategy hypothesis and variant ever evaluated on our US/NSE universes. Every additional test increases multiple-testing / false-discovery risk (Bailey & López de Prado *Deflated Sharpe Ratio*): the more ideas we try, the higher the statistical bar required for a true `PASS`.
>
> **Rules:**
> 1. Register every test **before** running code (`STATUS = PRE-REGISTERED`).
> 2. Never delete a failed test. Honest negatives are permanent assets.
> 3. Count both total **hypotheses (`N_hyp`)** and total **graduation attempts / variants (`N_attempts`)** tested.

---

## Cumulative Testing Ledger

| Test ID | Date | Branch / Commit | Hypothesis & Mechanism | Markets | Data Tier | Graduation Attempts (Total Variants) | Holdout Window | Primary Holdout Result (Net) | Verdict | Artifact |
|---|---|---|---|---|---|---:|---|---|---|---|
| `T001` | 2026-11-20 | `main` (`6fc9ca6`) | 1H Intraday VCP Breakout + Score/Defensive Exits (Forensic Audit) | US + NSE | Free 1H (Survivorship-flattered) | 6 (6) | Rolling WF (2025–2026) | `+0.063R` point-in-time before live slippage (~0R net); score has no alpha | **FAIL** | `FORENSIC_VALIDATION_REPORT.md` |
| `T002` | 2026-11-23 | `research/daily-vcp` (`9a988e8`) | Daily VCP Breakout (`daily_swing.yaml`, 10d hold, 1R/2.5R) + Regime + RS + Trend Template | US + NSE | Free 12y Daily (Survivorship-flattered) | 2 (`V3`,`V4`; 5 total `V0`–`V4`) | `2022-01..2026-11` | Combined `V3=-0.036R (t=-0.85)`, `V4=-0.022R (t=-0.47)`; US `V3=+0.046R (1/5 +yrs)`; NSE `V3=-0.102R` | **FAIL** | `research_out/daily/REPORT.md` |
| `T003` | 2026-11-23 | `research/phase1-momentum` (`32dbb7e`) | 12–1 Month Cross-Sectional Momentum (Monthly Rebalance) + Vol-Target & Regime Overlay — **Free-Data Screen** | US (Primary), NSE (Diagnostic — `2022–2026` now **SEEN**) | Free 12y Daily (Survivorship-flattered) | **2** (`M1`,`M2`; +`M0` baseline & DEV `Q1`–`Q5` event study) | `2022-01..2026-10` (58 mos) | US S1 DEV `PASS` (`+8.07%/yr`); US S2 HOLDOUT **`FAIL`**: `M1` Sharpe `1.047` but `-1.61%/yr` vs matched `EW_M1` (drop `SMCI`: `-2.78%`); `M2` `-1.50%/yr` vs `EW_M2`. *(NSE diagnostic: monotone Q1→Q5, `M2` `+24.67%`, Sharpe `1.526`, `+7.03%/yr` vs `EW_M2` — not primary, survivorship-flattered; **NSE 2022–2026 marked SEEN**)* | **FAIL** *(accepted)* | `research_out/phase1/T003/REPORT.md` |
| `T004` | 2026-11-23 | `research/phase1-momentum` (`60e4dc0`) | US Sector ETF Rotation (9 original SPDR ETFs, 12–1m Top-3 + 15% Vol-Target & `SPY>SMA200` Regime, `2000–2026`) | US ETFs (Zero Survivorship Bias) | Free 26y Adjusted Daily (`1998–2026`) + `^IRX` T-bill | **0** *(killed at Stage 1 DEV; `S1`/`S2` HOLDOUT never opened)* | `2016-01..2026-10` (**SEALED / UNTOUCHED**) | Stage 1 DEV (`2000–2015`, 192 mos) **`FAIL`**: `T3` `+6.74%` vs `EW_S0` `+5.92%` → excess **`+0.83%/yr`** (`< +1.00%` gate); `T3 - T1 = +0.09%/mo` (`t = +0.30`). Stopped immediately; **HOLDOUT `2016–2026` remains sealed.** | **FAIL** *(Stage 1 DEV kill)* | `research_out/phase1/T004/REPORT.md` |
| `T005` | 2026-11-23 | `research/phase1-momentum` | Multi-Asset Time-Series Trend / Dual Momentum (`SPY, EFA, TLT, GLD, DBC` + `^IRX`, `2007–2026` — **FINAL `$0` Gate**) | Global Macro ETFs (Zero Survivorship Bias) | Free 20y Adjusted Daily (`2006–2026`) + `^IRX` T-bill | **2** (`A1`,`A2`; +`A0` Stage 1 DEV trend control) | `2016-01..2026-10` (130 mos; DEV `2007-03..2015-12`, 106 mos) | Stage 1 DEV **`PASS`** (`+0.296` Sharpe, `-16.9pp` DD); Stage 2 HOLDOUT **`FAIL`** (4/5 gates passed on `A1`: Sharpe `0.704`/`0.687`, MaxDD `-7.75%`, beats `60/40`, robust to drop-best-year/ETF; missed C2 vs static `EW_5` `0.798` & `RP_5` `0.764` in 2016–2021). Full 20y `A1` Sharpe `0.693`, MaxDD `-10.65%`. **Hard Stop triggered.** | **FAIL** *(4/5 gates; missed C2)* | `research_out/phase1/T005/REPORT.md` |

---

## Seen / Burned Holdout Windows (Do Not Reuse as Untouched OOS)
- **US Single-Stock Watchlist (`2022–2026`):** **SEEN** (`T002`, `T003`)
- **NSE Single-Stock Watchlist (`2022–2026`):** **SEEN** (`T002`, `T003`) — **no further NSE analysis on `2022–2026`**
- **US Sector ETFs (`2016–2026`):** **UNTOUCHED / SEALED** (`T004` killed on `2000–2015` DEV)
- **Global Macro ETFs (`2016–2026`):** **SEEN** (`T005`)

---

## Multiple-Testing Tracker
- **Completed Hypotheses (`N_hyp_done`):** `5` (`T001`, `T002`, `T003`, `T004`, `T005`) — **0 passed, 5 failed**
- **Completed Graduation Attempts (`N_attempts_done`):** `12` (`6` in `T001` + `2` in `T002` + `2` in `T003` + `0` in `T004` + `2` in `T005`; `16` total variants evaluated on HOLDOUT)
- **Active Pre-Registered:** `0` — **Phase 2 Hard Stop Triggered**
- **Parked Until Real Alpha Exists:** Options scan, "BS brain" learning layer, ML models, 1-week short-term reversal (dropped prior to testing due to turnover/STT cost drag).
