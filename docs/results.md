# Results log

## Result 1 — baseline (2 Nov 2026)

**Setup**
- Measured demand: JRC ENaGaD, Netherlands, column `DIS` (residential + commercial gas), GWh per gas day (06:00–06:00 local time).
- Weather: KNMI hourly, De Bilt (260), averaged per gas day.
- Years: 2015–2025, leave-one-year-out. Every method is rescaled to the held-out year's **measured** annual total and scored on the same days (only 31 Dec 2025 is dropped).
- So the held-out year's daily values are never used for fitting, but its annual total is. That total is part of the measured outcome, so these are **conditional** results, not leakage-free forecasts. They answer: *given the correct annual total, how well does each method distribute it over the days?* All methods get the same total, so the comparison between them is fair.
- Methods:
  - A1: reference year (most recent other year, matched by weekday, no weather).
  - B: ridge regression on weather and calendar features.
  - C: gradient boosting on weather and calendar features.
  - A\*: the WPG profile "Eem en Zonnestein", space heating + tap water, used as given.
- Reproduce: `python -m src.run_loyo --config config.yaml --carrier heat` (about 30 s).

**Results** (errors in %, over the 11 held-out years)

| Method | Peak week: mean absolute | Peak week: worst | Top 1%: mean absolute | nRMSE: mean | Cold spells: mean absolute |
|---|---:|---:|---:|---:|---:|
| A1 reference year | 18.9 | −47.8 | 26.7 | 36.1 | 30.7 |
| B regression | **2.5** | −4.3 | **3.9** | 9.2 | **1.9** |
| C boosting | 3.6 | −11.4 | 5.1 | **8.6** | 8.7 |
| A\* WPG | 34.3 | +52.8 | 30.0 | 40.3 | 15.2 |

nRMSE = RMSE / measured mean daily demand of the held-out year, where RMSE = sqrt(mean over days of (rebuilt − measured)²), computed per year after rescaling; the table shows the mean over the 11 years. Example: 9.2% means the typical daily miss is about 9% of an average day, with large misses weighted more heavily.

Figures: `results/heat/figures/` (`fig5_external_<year>.png` shows measured vs. WPG per year).

**Findings**
1. **Weather is essential.** A1 has no weather information and misses peaks by about 19% on average, and by 48% in its worst year.
2. **B (regression) is the most reliable at peaks.** C (boosting) has a slightly lower daily error but is less stable in peak weeks.
3. **A\* overshoots winter peaks by about 34% in every year. This comparison is not like-for-like:**
   - WPG is residential space heating + tap water and is almost flat in summer. Only 7.9% of its 2019 energy falls in May–September, against 18.7% for `DIS`.
   - Measured `DIS` also contains commercial use, cooking and a base that doesn't depend on weather.
   - After scaling to the same annual total, the missing summer base is pushed into winter.
   - The timing of WPG is good: its daily correlation with `DIS` is about 0.9–0.97.
4. **Annual `DIS` drops from about 180 to about 135 TWh from 2022** (gas crisis). Scaling removes the level change; the shape may also have changed.

**Caveats**
- These results apply to daily national residential + commercial gas demand, not to residential space heating alone and not to hourly demand.
- A\* uses a single neighbourhood as a stand-in for the national residential stock.
- B and C use different feature sets (see `docs/details.md`), so the gap between B and C is not only a difference between model types.

## Backup — WPG + fitting (explored 2 Nov 2026; not a result)

**Status.** Explored as a possible next step after Result 1. **Not used as a result**: fitting WPG to measured `DIS` turns the test of WPG into a test of a calibrated model, and it is unclear what that legitimately shows (see "Why not a result"). Kept in the code as a backup and as input for discussion.

**What was tried** (all fitted on the other 10 years only, rescaled to the held-out total like every method):

| Method | Model |
|---|---|
| A\*b | `DIS ≈ a·WPG + b + c·weekend + e·holiday` (WPG + constant base) |
| A\*b22 | as A\*b, but *a* and *b* may change from 2022 (gas crisis) |
| B+W | regression B with WPG as one extra input |

Enable them by adding `A*b`, `A*b22`, `B+W` to `methods:` in `config.yaml` (`crisis_year: 2022` is used by A\*b22). Code: `src/methods/m_w_base.py`, `src/methods/m_b_regression.py`; definitions in `docs/details.md`.

**Learnings so far** (indicative only; peak-week mean absolute error over 2015–2025: A\* 34%, A\*b 6%, B 2.5%, B+W 1.5%)
1. **About 40% of `DIS` behaves like a base that residential heating doesn't explain** (fitted *b* ≈ 180 GWh/d), with a weekend dip of about −45 GWh/d (commercial use). This is the main reason A\* overshoots.
2. **A constant base is not enough.** Even calibrated, the WPG model is clearly worse than the weather regression B and misses the 2018 and 2021 freezes by 11–17%. The constant sits too high in summer and too low in a freeze: the non-residential part is itself weather-dependent.
3. **The 2022 break is a level change, not a shape change.** Base and heating fall by roughly the same proportion (base share 40% → 41%).
4. **WPG may carry information beyond daily weather** (B+W slightly beats B everywhere). Only valid if WPG for year Y is built from year Y's weather and no measured gas; **to confirm with the WPG owner.**
5. Implementation note: WPG values are about 10⁷ times larger than the 0/1 inputs, so WPG must be standardized before fitting, otherwise the weekend/holiday/crisis terms collapse to zero (fixed and covered by a test).

**Why not a result**
- Fitting *a*, *b*, … on measured `DIS` means the score belongs to "WPG + measured history", not to WPG. "WPG error drops from 34% to 6%" would be a wrong reading.
- All scores (Result 1 included) are conditional on the held-out year's measured annual total. Fine for comparing how methods distribute a known total, but it should be kept in mind. Open question, to think about; no change made.

**Possible ways forward without fitting on `DIS`** (ideas, not done)
- Fix the non-residential share from a published source (e.g. CBS household vs. other small-user gas) *before* looking at results, instead of learning it.
- Compare WPG only against a measured residential-only series, if one becomes available.
