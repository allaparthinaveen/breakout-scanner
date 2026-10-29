# 📊 Breakout Scanner: Weekly Noise & Follow-Through Report
> **Generated:** 2026-10-28T15:56:14.437950+00:00  
> **Sample Size:** 429 Candidates Tracked (Forward 10 Bars)

---

## 1. Executive Summary: Follow-Through vs. False Breakouts
- **True Follow-Through Rate (TP1 Hit):** **56.18%**
- **False Breakout / Stop-Out Rate:** **46.85%**
- **Average Expectancy:** **+0.20R** per candidate
- **Average Favorable Excursion (MFE):** **8.67R**
- **Average Adverse Excursion (MAE):** **1.85R**

---

## 2. Noise Attribution by Quant Score Tier
| Score Tier | Setups | Win Rate (TP1) | Avg Expectancy (R) | Avg MFE (R) | Sample Confidence |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Grade A+ [90-100]** | 0 | 0.0% | +0.00R | 0.00R | — |
| **Grade A  [80-89]** | 0 | 0.0% | +0.00R | 0.00R | — |
| **Grade B  [70-79]** | 0 | 0.0% | +0.00R | 0.00R | — |
| **Grade C  [<70]** | 429 | 56.2% | +0.20R | 8.67R | ✅ Adequate (N >= 30) |

---

## 3. Noise Attribution by Box Quality (Base Width)
| Base Width Quality | Setups | Win Rate (TP1) | Avg Expectancy (R) | Avg MFE (R) | Sample Confidence |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Ultra-Tight (<= 4.0%)** | 125 | 54.4% | +0.14R | 7.58R | ✅ Adequate (N >= 30) |
| **Medium Box (4.1% - 7.0%)** | 174 | 62.1% | +0.32R | 9.82R | ✅ Adequate (N >= 30) |
| **Loose Box (> 7.0%)** | 130 | 50.0% | +0.10R | 8.18R | ✅ Adequate (N >= 30) |

---

## 4. Volume Surge (RVOL) Impact on Breakout Hold
| RVOL Expansion Tier | Setups | Win Rate (TP1) | Avg Expectancy (R) | Sample Confidence |
| :--- | :---: | :---: | :---: | :---: |
| **Explosive (>= 2.0x)** | 245 | 60.0% | +0.29R | ✅ Adequate (N >= 30) |
| **Solid (1.5x - 2.0x)** | 95 | 42.1% | -0.09R | ✅ Adequate (N >= 30) |
| **Moderate (1.2x - 1.5x)** | 89 | 60.7% | +0.25R | ✅ Adequate (N >= 30) |
| **Low (< 1.2x or Unconfirmed)** | 0 | 0.0% | +0.00R | — |

---

## 5. Higher Timeframe (1D) Macro Alignment
| HTF Macro Status | Setups | Win Rate (TP1) | Avg Expectancy (R) | Sample Confidence |
| :--- | :---: | :---: | :---: | :---: |
| **⭐ 1D Confirmed** | 0 | 0.0% | +0.00R | — |
| **Neutral / Unconfirmed** | 429 | 56.2% | +0.20R | ✅ Adequate (N >= 30) |

---

## 6. Top Rejection Reasons Histogram
| Filter / Quality Gate Reason | Frequency | Actionable Interpretation |
| :--- | :---: | :--- |
| `NO_VOLUME_DRYUP` | 1137 | Filter successfully rejected low-conviction structure |
| `COUNTER_OR_NO_HTF_ALIGNMENT` | 870 | Filter successfully rejected low-conviction structure |
| `SUBOPTIMAL_GRADE_C` | 529 | Filter successfully rejected low-conviction structure |
| `IDLE_NO_STRUCTURE` | 239 | Filter successfully rejected low-conviction structure |
| `UNCOILED_DECAY_GT_0_70` | 214 | Filter successfully rejected low-conviction structure |
| `LOOSE_RANGE_GT_10PCT` | 145 | Filter successfully rejected low-conviction structure |

---

## 7. Actionable Empirical Insights (Where Noise Originates)
1. **Box Width Disparity**: Ultra-tight bases (<= 4%) demonstrate a **54.4% win rate** vs. only **50.0%** for loose boxes (> 7%). Loose bases are the single largest source of false-breakout noise.
2. **Quant Score Reliability**: High-scoring setups (Grade A/A+) achieve higher expectancy than Grade C candidates (0.0% vs 56.2%).
3. **Recommendation for Phase 2**: Prioritize close-location scoring and failed-breakout penalties to further widen the edge between tight and loose setups without adding hard kill-gates.
4. **Statistical Significance Notice**: Tiers with sample size $N < 30$ carry high sampling variance and are labeled with ⚠️. Replicate findings across multiple market regimes and higher sample sizes before hard-filtering.