# 📊 Breakout Scanner: Weekly Noise & Follow-Through Report
> **Generated:** 2026-10-24T05:05:08.395147+00:00  
> **Sample Size:** 216 Candidates Tracked (Forward 10 Bars)

---

## 1. Executive Summary: Follow-Through vs. False Breakouts
- **True Follow-Through Rate (TP1 Hit):** **12.5%**
- **False Breakout / Stop-Out Rate:** **79.63%**
- **Average Expectancy:** **-0.67R** per candidate
- **Average Favorable Excursion (MFE):** **2.31R**
- **Average Adverse Excursion (MAE):** **2.28R**

---

## 2. Noise Attribution by Quant Score Tier
| Score Tier | Setups | Win Rate (TP1) | Avg Expectancy (R) | Avg MFE (R) |
| :--- | :---: | :---: | :---: | :---: |
| **Grade A+ [90-100]** | 0 | 0.0% | +0.00R | 0.00R |
| **Grade A  [80-89]** | 0 | 0.0% | +0.00R | 0.00R |
| **Grade B  [70-79]** | 0 | 0.0% | +0.00R | 0.00R |
| **Grade C  [<70]** | 216 | 12.5% | -0.67R | 2.31R |

---

## 3. Noise Attribution by Box Quality (Base Width)
| Base Width Quality | Setups | Win Rate (TP1) | Avg Expectancy (R) | Avg MFE (R) |
| :--- | :---: | :---: | :---: | :---: |
| **Ultra-Tight (<= 4.0%)** | 37 | 21.6% | -0.32R | 0.84R |
| **Medium Box (4.1% - 7.0%)** | 71 | 14.1% | -0.61R | 1.60R |
| **Loose Box (> 7.0%)** | 108 | 8.3% | -0.82R | 3.28R |

---

## 4. Volume Surge (RVOL) Impact on Breakout Hold
| RVOL Expansion Tier | Setups | Win Rate (TP1) | Avg Expectancy (R) |
| :--- | :---: | :---: | :---: |
| **Explosive (>= 2.0x)** | 17 | 58.8% | +0.19R |
| **Solid (1.5x - 2.0x)** | 8 | 12.5% | -0.40R |
| **Moderate (1.2x - 1.5x)** | 2 | 50.0% | +0.00R |
| **Low (< 1.2x or Unconfirmed)** | 189 | 7.9% | -0.76R |

---

## 5. Higher Timeframe (1D) Macro Alignment
| HTF Macro Status | Setups | Win Rate (TP1) | Avg Expectancy (R) |
| :--- | :---: | :---: | :---: |
| **⭐ 1D Confirmed** | 0 | 0.0% | +0.00R |
| **Neutral / Unconfirmed** | 216 | 12.5% | -0.67R |

---

## 6. Top Rejection Reasons Histogram
| Filter / Quality Gate Reason | Frequency | Actionable Interpretation |
| :--- | :---: | :--- |

---

## 7. Actionable Empirical Insights (Where Noise Originates)
1. **Box Width Disparity**: Ultra-tight bases (<= 4%) demonstrate a **21.6% win rate** vs. only **8.3%** for loose boxes (> 7%). Loose bases are the single largest source of false-breakout noise.
2. **Quant Score Reliability**: High-scoring setups (Grade A/A+) achieve higher expectancy than Grade C candidates (0.0% vs 12.5%).
3. **Recommendation for Phase 2**: Prioritize close-location scoring and failed-breakout penalties to further widen the edge between tight and loose setups without adding hard kill-gates.