# Study `T004` Report: US Sector ETF Rotation (`2000–2026`, Zero Survivorship Bias)

## Final Pre-Registered Verdict: **FAIL**

- **Stage 1 Kill Gate (DEV `2000–2015`, 192 mos):** **FAIL** (`T3 - EW_S0` 1× CAGR = `+0.83%`, `T3 - T1` monthly mean = `+0.09%`, `t = +0.30`)
- **Stage 2 Graduation Gate (HOLDOUT `2016–2026`, 130 mos, Single Touch):** **SKIPPED (Stage 1 FAIL — HOLDOUT SEALED)**

### Methodological Disclosures
- **Zero Survivorship Bias:** Fixed 9 original SPDR Select Sector ETFs (`XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY`), all launched simultaneously on `1998-12-22`, continuously traded through `2026-11-20`.
- **Sharpe & Cash Convention:** Uninvested cash earns strictly causal `^IRX` 13-week T-bill yield; `Sharpe_tbill` uses true excess returns (`r_p - r_f`) identically across `S0..S2`, `EW_S0..EW_S2`, and `SPY`. Zero-cash CAGR and `rf=0` Sharpe are also shown.
- **Costs:** `1× = 10 bps` round-trip (`5 bps` one-way); `2× = 20 bps` round-trip (`10 bps` one-way) on exact drifted-weight turnover.
- **Statistical Power:** 130 monthly HOLDOUT observations (10.83 yrs) give Sharpe SE ~`±0.31`; full 26-year span = 322 monthly observations.


---

## Stage 1 — DEV Tercile Event Study (`2000–2015`, 192 mos, 1× Costs)

| Bucket | Months | CAGR (TBill cash) | Ann Vol | Sharpe (excess ^IRX) | Sharpe (rf=0) | Max DD | 1-Way TO/mo | +Years |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **T1** | 192 | +4.92% | 19.07% | +0.260 | +0.348 | -60.37% | 23.6% | 11/16 (69%) |
| **T2** | 192 | +4.33% | 15.37% | +0.243 | +0.354 | -50.50% | 39.8% | 10/16 (62%) |
| **T3** | 192 | +6.74% | 15.29% | +0.392 | +0.504 | -39.93% | 22.1% | 11/16 (69%) |
| **EW_S0** | 192 | +5.92% | 15.16% | +0.344 | +0.457 | -49.32% | 1.5% | 12/16 (75%) |
| **SPY** | 192 | +3.81% | 15.51% | +0.210 | +0.320 | -51.30% | 0.0% | 11/16 (69%) |
| **TBILL** | 192 | +1.70% | 0.55% | +0.000 | +3.068 | -0.00% | 0.0% | 16/16 (100%) |

