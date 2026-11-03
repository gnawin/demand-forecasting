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

## Result 2 — NEDU reference vs learning (3 Nov 2026)

**Question.** How well does the NEDU standard gas profile describe the daily pattern of Dutch regional-grid gas demand, especially peak weeks and cold spells, and how much better do methods that learn from measured data do?

**Data**
- Measured: ENaGaD NL `DIS` (residential + commercial on the regional grids), GWh per gas day (06:00–06:00 local), 2015–2025.
- Weather: KNMI hourly data for the 6 NEDU stations (De Bilt 260, Twente 290, Beek 380, De Kooy 235, Eelde 280, Vlissingen 310): temperature `T`, wind `FH`/`FF`, radiation `Q`. Twente misses 2 × 60 hours (Dec 2021, Mar 2022); those hours use the other 5 stations.
- NEDU: published hourly profile parameters (TST, RER, TOP) per year, 2015–2025.

**Profile temperature (TAC)**, computed by us with protocol 2.2:
`TAC = 0.6(t1+w1) + 0.3(t2+w2) + 0.1(t3+w3) + q1`
- t1 = hourly temperature; t2, t3 = daily mean (00–24 CET) of the previous day and the day before;
- w = −√wind/0.35 (hourly wind `FF`, daily wind the mean of `FH`); q1 = radiation [J/cm²]/40;
- computed per station, then weighted: De Bilt 0.28, Twente 0.16, Beek 0.15, De Kooy 0.15, Eelde 0.14, Vlissingen 0.12;
- the 2015 profile used the old rule (De Bilt daily `T − W/1.5`), so 2015 uses that rule.

**TAC check against NEDU.** NEDU's annual reports give the yearly sum of profile fractions at actual temperatures. Ours (2016–2023):

| | Mean absolute difference |
|---|---:|
| De Bilt only | 0.051 |
| 6 stations | 0.002 |

With 6 stations, 15 of 16 values (G1A and G2, 8 years) match within 0.003. The exception is G1A 2022 (ours 0.872, NEDU 0.853) while G2 2022 matches, so the weather is right; the G1A parameters in our 2022 file (version 1.01) probably differ from the version the report used.

**Evaluation: leave one year out.** Each year 2015–2025 is rebuilt using only the other 10 years of measured demand, plus that year's weather and calendar, and compared day by day with the measurement. Every rebuilt year is scaled to the measured annual total, so we compare how demand is spread over the year, not its level.

**Methods**

| Method | How | Learns from measured demand? |
|---|---|---|
| **NEDU** (reference) | Hourly `PF = max((TST − TAC)·RER + TOP, TOP)` with the year's published parameters; `0.75·G1A + 0.25·G2A` (volume shares, NEDU annual reports 2016–2023; G2C = G2A); summed per gas day | No |
| A1 (floor) | Copies the most recent other year's measured daily demand, weekday-matched | Copies only |
| B (regression) | Linear: heating degrees (also 24 h and 72 h means), wind, wind × heating, radiation, holiday, weekday/weekend, month | Yes, 10 years |
| C (boosting) | Gradient-boosted trees: same weather and calendar inputs plus TAC, day of year, temperature; 10% of training days held back for early stopping | Yes, 10 years |

**Indicators** (relative error (rebuilt − measured)/measured, %): peak week (7 days with the highest measured mean), top 1% (4 highest measured days), cold spells (24 Feb–4 Mar 2018 and 7–14 Feb 2021), nRMSE (daily RMSE / measured mean). Reported as mean over 11 years, worst year, and coldest year (2016 by heating-season temperature).

**Results — `DIS`** (%)

| Method | Peak week signed mean | Peak week mean absolute | Peak week worst | Top 1% signed mean | Top 1% mean absolute | Top 1% worst | Cold spells mean | nRMSE mean |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A1 | −18.1 | 18.9 | −47.8 | −26.7 | 26.7 | −49.5 | −30.7 | 36.1 |
| **NEDU** | **+12.1** | **12.1** | **+17.5** | **+12.7** | **12.7** | **+19.7** | **+16.2** | **19.1** |
| B | −3.1 | 3.1 | −5.3 | −4.3 | 4.3 | −7.1 | −1.9 | 8.8 |
| C | −1.4 | 3.9 | −11.8 | −2.5 | 5.3 | −16.4 | −8.2 | 7.7 |

Signed means can cancel across years; mean absolute columns show typical error size without cancellation. For example, C's peak-week signed mean is −1.4%, but its mean absolute error is 3.9%, because some years are overestimates and others underestimates. Worst is the signed error with the largest absolute magnitude.

For reference, De Bilt-only TAC gave NEDU +17.0% peak week and 19.9% nRMSE.

**Reading**
- The NEDU small-user profile, applied to all regional-grid gas, overestimates the peak week by about 12% on average (up to 18%) and cold spells by about 16%.
- Learning from measured demand reduces the peak-week error to about 1–3% on average. B is the most stable (worst year −5%); C has the lowest average error but a worse worst year.
- Copying another year (A1) is far worse than both: weather differs too much from year to year.

**Caveats**
1. **Scope mismatch.** NEDU covers households (G1A) and small business (G2). `DIS` also includes GXX and hourly-metered large users with flatter demand, so part of NEDU's overestimate is scope, not profile quality. No published GXX volume was found (searched NEDU documents, MFFBAS, web), so GXX is not added.
2. **B and C learn the full `DIS` shape**, so part of their advantage is scope too.
3. **Reconstruction, not forecast:** actual weather and the measured annual total are used for every method.
4. For context: NEDU itself reports a mismatch (MCF) of about 12% for these profiles before 2022 and 17–19% in 2022–2023.

**`TOT` view (all Dutch gas, learning methods only).** NEDU has no profile for industry and power plants (about half of `TOT`), so it is left out here; with it, the error was dominated by that gap (+52% peak week).

| Method | Peak week mean | Peak week worst | Top 1% mean | Cold spells | nRMSE mean |
|---|---:|---:|---:|---:|---:|
| A1 | −16.1 | −33.0 | −19.8 | −23.4 | 26.8 |
| B | −4.2 | −15.2 | −5.5 | −3.4 | 9.6 |
| C | −2.6 | −12.6 | −3.6 | −7.3 | 9.3 |

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
