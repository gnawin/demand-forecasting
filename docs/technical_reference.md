# Technical reference — Dutch standard gas profiles vs learning methods

Internal reference for the comparison shown in the outsider deck
[presentation/standard_profiles_vs_learning.html](presentation/standard_profiles_vs_learning.html) and its technical appendix
[presentation/standard_profiles_vs_learning_appendix.html](presentation/standard_profiles_vs_learning_appendix.html).
It records what was done, how, and with which numbers, so that the result can be checked,
reproduced and extended. Short result log: [results.md](results.md) (Result 2). Pipeline-wide
options and contracts: [details.md](details.md) and the [README](../README.md).

Status: hackathon result (Nov 2026). It reconstructs past years; it does not forecast.

---

## 1. Question and scope

**Question.** How well does the official Dutch standard gas profile (households G1A + small business G2A)
describe the day-to-day pattern of measured Dutch regional-grid gas demand, especially in peak
weeks and cold spells? How much better do methods that learn from measured demand do?

**Scope decisions**

| Decision | Choice | Reason |
|---|---|---|
| Time resolution | Daily (gas day) | Measured demand (ENaGaD) is daily only. Hourly weather and hourly profile fractions are aggregated to gas days. |
| Measured series | ENaGaD NL `DIS` (carrier `heat`) | Regional-grid demand is the closest public series to what the standard profiles cover. `TOT` (all gas) is shown only for learning methods. |
| Profile categories | G1A (0.75) + G2A (0.25) | GXX has no published volume, so it cannot be weighted (see §9). |
| Years | 2015–2025 (11 years) | Demand data starts in 2015. Weather before 2015 adds no training years. |
| Level vs shape | Every method is scaled to the measured annual total | Standard profiles are fractions without a level, and level depends on the 2022 crisis. We test how the total is spread over the year. |
| WPG profile | Not used | Dropped by decision. The earlier exploration is kept as backup in [results.md](results.md). |

---

## 2. Data

### 2.1 Measured demand — JRC ENaGaD

- File: `data/raw/ENAGAD.csv`, reader `format: enagad`, `country: NL`.
- Columns used: `DIS` (distribution grids) for carrier `heat`, `TOT` for carrier `gas_total`.
  For NL, `TOT = DIS + OTHER`; `IND` and `GPP` are 0.
- Unit: GWh per gas day. Gas day D = D 06:00 to D+1 06:00 local time (`daily_label: date`,
  `daily_start_hour: 6`).
- Level break: `DIS` is about 180 TWh/yr before 2022 and about 135 TWh/yr from 2022 (gas crisis).
  Scaling to the annual total removes the level; the shape may also have changed.
- `DIS` covers more than G1A + G2: also GXX users and other users with a connection on the
  regional grid. This is the main scope caveat (§9).

### 2.2 Weather — KNMI hourly (uurgegevens)

- 6 stations = the official TAC stations, each with two files (2011–2020 and 2021–2030;
  `.txt` or `.zip`, read as-is):

  | Station | Name | Official weight |
  |---|---|---:|
  | 260 | De Bilt | 0.28 |
  | 290 | Twente | 0.16 |
  | 380 | Beek (Maastricht) | 0.15 |
  | 235 | De Kooy | 0.15 |
  | 280 | Eelde | 0.14 |
  | 310 | Vlissingen | 0.12 |

- Variables: `T` (0.1 °C), `FH` (hourly mean wind, 0.1 m/s), `FF` (wind in the last 10 minutes
  of the hour, 0.1 m/s), `Q` (global radiation, J/cm² per hour).
- Conversions: `T/10` → `temp_c`; `FH/10` → `wind_ms`; `FF/10` → `ff_ms`;
  `Q × 10000/3600` → `ghi_wm2` (W/m²). `-9999` = missing.
- KNMI `HH` = 1..24 is hour *ending* (UT); subtract 1 h for the UTC hour beginning.
- Gaps: complete, except Twente (290): 2 × 60 hours (Dec 2021, Mar 2022). In those hours the
  other 5 stations are used with renormalised weights. Gaps ≤ 3 h are interpolated; longer gaps
  in eligible years raise an error.
- Weighting: **all** weather variables (`temp_c`, `wind_ms`, `ghi_wm2`, `ff_ms`) and the per-station
  TAC are averaged over stations with the official weights (`weather.stations: nedu`), renormalised
  per hour over the stations present. B and C therefore also use the 6-station weighted weather.
- Daily features (B, C) = mean of the hourly weighted values over the gas day.

### 2.3 Standard profiles

- Files: `data/raw/Profielen-*.zip`, one per year 2015–2025. The reader takes the `profielen*` and
  `gxx*` `.csv`/`.txt` files inside; SPT files and documents are ignored.
- Content: hourly parameters per category: `TST` (threshold temperature), `RER` (temperature
  sensitivity), `TOP` (temperature-independent part). Column names come from the `Code` row,
  for example `G1A_TST`.
- Format quirks, all handled by `parse_profile_file`:
  - separator `,` up to 2023, `;` in 2024–2025;
  - date format `dd-mm-yyyy HH:MM`, ISO `yyyy-mm-dd HH:MM` in 2024;
  - 2016 is a `.txt` file with trailing commas;
  - encoding latin-1.
- Time: the first column is the hour *end* in **continuous CET (UTC+1, no DST)**. UTC hour
  beginning = end − 1 h − 1 h. Timestamps never repeat at DST changes.
- Profile year = 06:00 CET to 06:00 CET.
- Not used: the newer GXX downloads (`GXX-profiel-aardgas-2017/18/19*.zip`, `IC190-GXX-…2016…txt`)
  don't match the configured glob. GXX is excluded anyway (§9).

### 2.4 Documents used

- Protocol 2.2 (`data/raw/Protocol-…-2.2-2026-06-02.pdf`, Bijlage 1): the TAC formula, the station
  weights and the old rule. It also states that GXX is applied with a forecast temperature.
- Protocol 2.0 (2021): earlier version, for comparison.
- Annual reports of the profile publisher, 2016–2023: G1A and G2 volumes, used for the 0.75/0.25 weights. Example 2021:
  G1A 9,288, G2 2,960 million m³. They also give yearly sums of profile fractions, used to validate
  TAC (§3.3), and the MCF mismatch (§9).

---

## 3. Profile temperature (TAC)

### 3.1 Formula (protocol 2.2, from 2016)

Per station, per hour:

```
TAC = 0.6·(t1 + w1) + 0.3·(t2 + w2) + 0.1·(t3 + w3) + q1
```

| Term | Definition |
|---|---|
| t1 | hourly temperature `T` (°C) |
| t2, t3 | daily mean temperature (00–24 CET) of the previous day and the day before |
| w1 | −√(`FF`)/0.35, hourly wind (10-minute value) |
| w2, w3 | −√(daily mean of `FH`)/0.35, for the same two previous days |
| q1 | `Q` [J/cm²]/40 for that hour |

At the start of the record, missing previous days reuse the nearest available day (affects only
the first 2 days of 2011, outside the evaluated years).

Then a weighted mean over stations (weights in §2.2), renormalised per hour.
Code: `station_tac` in `src/nedu.py`, combined in `read_weather` in `src/ingest.py`.

### 3.2 Old rule (the 2015 profile)

The 2015 profile was built with the old effective temperature: De Bilt only, daily (00–24 CET)
`T − W/1.5`, with W the daily mean wind in m/s. It is applied to the gas day that starts at 06:00 CET.
Column `tac_old` (`old_tac`). The switch is `nedu.new_tac_from: 2016`, applied per profile year.

### 3.3 Validation against the published figures

The annual reports give the yearly sum of hourly profile fractions at the actual temperature,
for G1A and G2. We compute the same sum from our TAC and the published parameters:

| Our TAC | Mean absolute difference (G1A + G2, 2016–2023, 16 values) |
|---|---:|
| De Bilt only | 0.051 |
| 6 stations, official weights | 0.002 |

Published sums:

| | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| G1A | .986 | .946 | .956 | .901 | .861 | .985 | .853 | .903 |
| G2 | .963 | .943 | .947 | .908 | .879 | .982 | .881 | .907 |

With 6 stations, 15 of 16 values match within 0.003. The exception is G1A 2022: ours 0.872, published
0.853. G2 2022 matches, so the weather is right. Most likely the G1A parameters in our 2022 file
(version 1.01) differ from the version the report used. Not investigated further.

Effect on results: with De Bilt-only TAC, the standard profiles' peak-week error was +17.0% (nRMSE 19.9%);
with 6 stations it is +12.1% (19.1%).

---

## 4. Standard-profile reference method

Per category c and hour h, with the published parameters of that year:

```
PF_c(h) = max( (TST_c(h) − TAC(h)) · RER_c(h) + TOP_c(h),  TOP_c(h) )
SP(h) = 0.75 · PF_G1A(h) + 0.25 · PF_G2A(h)
SP(gas day) = Σ SP(h) over the 24 (23/25 at DST) hours of the gas day
```

- Weights: G1A ≈ 75%, G2 ≈ 25% of the combined annual volume (annual reports 2016–2023, stable
  over the years). Only relative weights matter after scaling. G2 is modelled with G2A; G2C was
  treated as equal to G2A.
- Gas days with incomplete hours are dropped (`sum_complete_days`).
- The reference is not fitted to measured demand. It uses only the published parameters and the weather.
- Config: `nedu: {path, weights, new_tac_from}`. Code: `nedu_daily` in `src/nedu.py`.
- The reference is skipped with a warning for hourly carriers or when the `nedu` section is missing. It is
  excluded for `gas_total` via the per-carrier `methods: [A1, B, C]`.

---

## 5. Evaluation design

### 5.1 Leave one year out

For each held-out year Y in 2015–2025:

1. Training data: measured demand, weather and calendar of the other 10 years.
2. Held-out inputs: Y's weather and calendar, and Y's measured annual total. No daily demand of Y.
3. Every method rebuilds the 365/366 gas days of Y.
4. Scale to Y's measured annual total (§5.2).
5. Compare day by day with Y's measured demand.

The standard profiles and A1 don't learn a model. The profiles use only Y's own parameters and weather; A1 copies a
training year.

### 5.2 Common support and rescaling

- Common support = the days of Y where the measurement and every method have a value. The reference is in
  `PARTIAL_METHODS`, so days it cannot cover are dropped for **all** methods.
- Annual total = the sum of measured demand on the common support. It is the same for every method.
- Every rebuilt series is scaled to that total: `rebuilt × (total / Σ rebuilt)`. The annual mean
  error is therefore 0 by construction.
- B and C errors before scaling are kept in `tables/unrescaled_level.csv`.

**Consequence.** These are *conditional reconstructions*: given actual weather and the correct
annual total, how well does each method spread the total over the days? This is fair between
methods, but it is not a forecast skill score.

### 5.3 Eligibility

Years with less than 90% measured coverage are excluded from training and evaluation
(`minimum_coverage: 0.90`). All 11 years are eligible.

---

## 6. Methods

| Method | Role | Learns from measured demand? |
|---|---|---|
| Standard | Reference: official standard profiles, current practice (§4) | No |
| A1 | Floor: no weather | Copies one training year |
| B | Learning, transparent (regression) | Yes, 10 years |
| C | Learning, flexible (gradient boosting) | Yes, 10 years |

### 6.1 Features (daily mode)

Defined in `src/features.py`, computed from the 6-station weighted weather and the Dutch calendar.
Rolling means are computed within each year.

| Feature | Definition | B | C |
|---|---|:-:|:-:|
| `heating` | max(0, 15.5 − T) | ✓ | ✓ |
| `heating_24h`, `heating_72h` | mean of `heating` over the last 1 and 3 gas days | ✓ | ✓ |
| `cooling` | max(0, T − 22) | ✓ | ✓ |
| `wind_ms` | mean wind speed (FH) | ✓ | ✓ |
| `wind_heating` | `wind_ms × heating` | ✓ | ✓ |
| `ghi_wm2` | mean global radiation | ✓ | ✓ |
| `holiday` | Dutch public holiday (`holidays` package, NL) | ✓ | ✓ |
| `hour_weekend` | in daily mode: weekend flag (categorical) | ✓ one-hot | ✓ |
| `month` | month (categorical) | ✓ one-hot | ✓ |
| `day_of_year`, `temp_c`, `weekday` | | | ✓ |
| `tac` | gas-day mean of the 6-station TAC | | ✓ |

B does not use TAC. TAC is mostly a combination of temperature, wind and radiation that B
already has, so adding it would add collinearity. C does use TAC. The B–C gap is therefore not
purely a model-type difference.

### 6.2 A1 — copy another year

The most recent training year (2025 for every held-out year except 2025, which uses 2024),
mapped date by date to the nearest date with the same ISO weekday. Leap days are anchored on
28 Feb. No weather.

### 6.3 B — ridge regression

`Ridge(alpha=1.0)` on the standardized numeric features and the one-hot categorical features.
Numeric scaling is fitted on the training years only. Negative predictions are clipped to 0
(with a log message).

### 6.4 C — gradient boosting

sklearn `HistGradientBoostingRegressor` (`boosting.backend: sklearn`, pinned for reproducibility):

| Setting | Value |
|---|---|
| max iterations | 500 |
| learning rate | 0.05 |
| max leaf nodes | 31 |
| min samples per leaf | 50 |
| early stopping | on a validation set of 10% of whole training days (seed 42), patience 30, tol 1e-7 |
| prediction | uses the best validation iteration |
| threads | 2 |

No tuning on held-out years.

Stopping rounds actually used (best validation iteration, `metrics.json` → `runs[].model`):
2015 93, 2016 99, 2017 109, 2018 89, 2019 109, 2020 90, 2021 101, 2022 94, 2023 91, 2024 131,
2025 137. So C stops well before the 500-round cap.

### 6.5 Step by step (B and C)

Per held-out year Y (`src/run_loyo.py`, loop over `heldout`):

1. **Align.** Read ENaGaD daily `DIS` (gas day D = D 06:00 to D+1 06:00 local). Read the 12 KNMI
   hourly files, weight the 6 stations with the official TAC weights (renormalised over stations
   present in each hour, `src/ingest.py`), and average the hourly weather over each gas day
   (`src/align.py`).
   Years below 90% coverage are dropped. Missing weather in an eligible year is an error, not
   filled.
2. **Features.** `build_features` (`src/features.py`) adds weather and calendar features only
   (§6.1). Demand never enters a feature. The 24 h / 72 h rolling means are computed per year,
   so no window reaches into another year.
3. **Split.** `train` = all rows of the other 10 years; `test` = rows of Y without the `value`
   column. Y's measured series is kept aside for scoring only.
4. **Fit.**
   - B: drop rows with missing inputs; `ColumnTransformer` (StandardScaler on numeric features,
     OneHotEncoder on `hour_weekend` and `month`, unknown categories ignored) → `Ridge(alpha=1)`.
     The scaler and encoder are fitted inside the pipeline on training rows only.
   - C: draw 10% of the *training days* (seed 42) as the early-stopping validation set, train
     on the other 90%, stop after 30 rounds without improvement, and predict with the best
     iteration (sklearn keeps the extra patience rounds; the code uses `staged_predict` up to
     the best one).
5. **Predict.** Rebuild every gas day of Y from Y's weather and calendar. Negative predictions
   are clipped to 0 (logged). The code checks that predictions are finite, non-negative and on
   exactly Y's timestamps.
6. **Common support and rescaling.** Keep only days where the measurement and every method have
   a value (§5.2), then scale each method to Y's measured total on those days. The unscaled
   total is stored first (§6.6).
7. **Score.** `evaluate` (`src/metrics.py`) computes the indicators of §7 on the common support.
   Results are aggregated over the 11 years in `src/report.py`.

### 6.6 Level before rescaling (drift)

Relative error of the B/C yearly total *before* step 6 (`results/heat/tables/unrescaled_level.csv`), %:

| | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| B | −8.4 | −8.5 | −10.1 | −8.3 | −7.7 | −5.5 | −4.3 | +12.9 | +18.4 | +17.6 | +21.4 |
| C | −9.2 | −8.7 | −10.0 | −8.4 | −7.1 | −5.6 | −5.4 | +12.8 | +19.0 | +18.0 | +22.0 |

Annual `DIS` falls from about 180 to about 135 TWh from 2022. Training pools all other years and
has no year or trend term, so each model predicts a level in between: too low before 2022,
too high after. The rescaling to the measured total removes this error from every indicator in §8.
A real forecast does not have that total, so drift has to be modelled explicitly.

### 6.7 Takeaways from the ML process

How it should be done (what this pipeline does, and should keep doing):

1. **Hold out whole years**, never random days. Neighbouring days share weather and behaviour,
   so a random-day split leaks information and flatters the model.
2. **Fit every learned transform on training data only**: scaler, encoder and the
   early-stopping set. Here the early-stopping set is drawn from the training years.
3. **Inputs from weather and calendar only.** Lagged or rolling demand would turn the test into
   a short-term forecast and is not available for a whole hidden year.
4. **Same days, same total, same weather for every method** (common support, §5.2). Without it,
   the comparison is not fair.
5. **Validate the reference before comparing** (§3.3). The 1-station weather looked plausible
   but was off by 0.05 in yearly fraction sums.
6. **Never fit the reference.** Calibrating a reference profile on measured demand measures the
   calibration, not the profile (WPG, §11).
7. **Reproducibility**: pinned sklearn backend, fixed seed, logged stopping round, pinned
   environment (`requirements.lock`), 45 tests.

What needs extra attention:

1. **Rescaling hides level errors** of up to 22% (§6.6). All results are conditional on the
   correct annual total. Any forward-looking use must model the level (trend, efficiency,
   prices) separately.
2. **Trees do not extrapolate.** Gradient boosting predicts piecewise constants, so a day colder
   than anything in training is treated like the coldest training day. C misses the cold spells
   by −8.2% on average against −1.9% for B. This matters for extreme or future weather years.
3. **The metric picks the winner.** C has the lowest nRMSE (7.7% vs 8.8%) but B is better in the
   peak week, top 1% and cold spells. Choose the indicator from the use case before looking at
   results.
4. **B and C use different inputs** (§6.1). The B–C gap is a mix of model type and features; use
   the same features for a clean model comparison.
5. **Few extremes.** Two cold spells in 11 years; "worst" values rest on a handful of days.
6. **Scale inputs of very different size.** In the WPG experiments, an unscaled input about
   10⁷ times larger than the 0/1 flags made the flag coefficients collapse to zero.
7. **No hyperparameter tuning yet.** If tuning is added, it must happen inside the training
   years (nested leave-one-year-out), never on the held-out year.
8. **Early stopping on random training days** is mildly optimistic (neighbouring days in the
   training years). Grouping the validation set by whole weeks or years would be stricter.
9. **Drift within training.** Insulation, heat pumps and the 2022 price shock change temperature
   sensitivity; pooled training averages over it.

---

## 7. Indicators

Signed relative error = (rebuilt − measured) / measured, after scaling, in %. Positive means the
method gives too much gas. All indicators are computed per held-out year Y on the common support,
with r = rebuilt series and m = measured series of Y.

Formulas:

- **Peak week**: W = the 7 consecutive gas days with the highest 7-day mean of m (rolling window
  ending at the maximum). e = (mean r(W) − mean m(W)) / mean m(W). The window is defined on the
  measurement, so every method is scored on the same days.
- **Top 1%**: D = the 4 days with the highest m. e = (mean r(D) − mean m(D)) / mean m(D).
  (`top_1pct_predicted`, on the method's own 4 highest days, is also stored.)
- **Cold spell**: S = the fixed date window. e = (mean r(S) − mean m(S)) / mean m(S). Spell peak
  and value at the measured peak day are also stored.
- **nRMSE** = √( mean over days of (r − m)² ) / mean(m).
- **Peak-week shift** = start of the 7-day window with the highest mean of r − start of W, in days.

| Indicator | Definition |
|---|---|
| Peak week | The 7 consecutive gas days with the highest **measured** mean in Y. Error of the rebuilt mean over the same 7 days. |
| Top 1% | The 4 gas days with the highest measured demand in Y (1% of 365 ≈ 4). Error of the rebuilt mean on those days. |
| Cold spells | Fixed windows 24 Feb–4 Mar 2018 (9 days) and 7–14 Feb 2021 (8 days). Error of the window mean. Only years with a window count. |
| nRMSE | √(mean over days of (rebuilt − measured)²) / measured mean daily demand of Y. |
| Peak-week shift | Start of the rebuilt peak week minus start of the measured peak week, in days (side information). |

Aggregation over the 11 years:

- **mean**: signed mean (over- and underestimates can cancel);
- **mean absolute**: mean of |error|, shown for peak week, top 1% and cold spells;
- **worst**: the signed value with the largest magnitude;
- **coldest year**: the value in the year with the lowest Jan–Mar + Oct–Dec mean temperature (2016).

Code: `src/metrics.py`, `compact_summary` in `src/report.py`.

---

## 8. Results

All values in %. Carrier `heat` = ENaGaD `DIS`, 2015–2025.
Source: `results/heat/tables/summary.csv`, `by_year.csv`, `cold_spells.csv`.

### 8.1 Summary

| Method | Peak week signed / abs / worst | Top 1% signed / abs / worst | Cold spells signed / abs | nRMSE mean / worst |
|---|---|---|---|---|
| A1 | −18.1 / 18.9 / −47.8 | −26.7 / 26.7 / −49.5 | −30.7 / 30.7 | 36.1 / 42.5 |
| **Standard** | **+12.1 / 12.1 / +17.5** | **+12.7 / 12.7 / +19.7** | **+16.2 / 16.2** | **19.1 / 23.7** |
| B | −3.1 / 3.1 / −5.3 | −4.3 / 4.3 / −7.1 | −1.9 / 1.9 | 8.8 / 12.9 |
| C | −1.4 / 3.9 / −11.8 | −2.5 / 5.3 / −16.4 | −8.2 / 8.2 | 7.7 / 11.7 |

### 8.2 Per year

Peak-week error:

| Year | A1 | Standard | B | C |
|---|---:|---:|---:|---:|
| 2015 | −3.4 | +9.5 | −3.5 | +0.2 |
| 2016 | +1.3 | +8.6 | −3.0 | +0.3 |
| 2017 | −6.9 | +6.9 | −3.3 | −0.3 |
| 2018 | −47.8 | +16.8 | −1.3 | −11.8 |
| 2019 | −7.5 | +14.3 | −1.5 | +6.7 |
| 2020 | −40.5 | +10.5 | −4.0 | −4.4 |
| 2021 | −18.1 | +15.4 | −3.5 | −8.2 |
| 2022 | −43.9 | +17.5 | −1.9 | +6.6 |
| 2023 | +2.9 | +8.1 | −2.4 | −1.4 |
| 2024 | −22.6 | +14.7 | −3.8 | −0.5 |
| 2025 | −12.5 | +10.7 | −5.3 | −2.2 |

nRMSE:

| Year | A1 | Standard | B | C |
|---|---:|---:|---:|---:|
| 2015 | 35.1 | 19.1 | 8.1 | 6.9 |
| 2016 | 35.1 | 16.0 | 7.8 | 6.9 |
| 2017 | 31.9 | 16.6 | 7.2 | 6.4 |
| 2018 | 36.5 | 17.3 | 7.9 | 9.3 |
| 2019 | 32.6 | 17.6 | 8.6 | 6.0 |
| 2020 | 35.0 | 18.3 | 10.1 | 8.0 |
| 2021 | 36.7 | 18.6 | 7.0 | 7.6 |
| 2022 | 42.5 | 20.1 | 12.9 | 11.7 |
| 2023 | 37.5 | 18.9 | 9.1 | 7.0 |
| 2024 | 37.3 | 23.5 | 9.5 | 7.1 |
| 2025 | 37.4 | 23.7 | 8.9 | 7.2 |

The standard profiles overestimate the peak week and the top 1% in **every** year. B underestimates slightly in
every year. C changes sign from year to year.

### 8.3 Cold spells

| Window | Measured mean (GWh/d) | A1 | Standard | B | C |
|---|---:|---:|---:|---:|---:|
| 24 Feb–4 Mar 2018 | 1,340 | −44.6 | +17.0 | −0.7 | −9.1 |
| 7–14 Feb 2021 | 1,370 | −16.9 | +15.5 | −3.2 | −7.2 |

Example of the 2018 peak week (25 Feb–3 Mar, mean in GWh/d): measured 1,428; standard profiles 1,669;
B 1,409; C 1,259; A1 745.

### 8.4 Seasonal diagnostics (why the standard profiles are too high in winter)

Monthly error over all years: Σ rebuilt / Σ measured − 1 per calendar month, pooled over
2015–2025, local time. Source: `docs/presentation/monthly_error_pct.csv`.

| Month | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Standard | +9.6 | +8.9 | +7.4 | +4.0 | −27.6 | −38.8 | −38.2 | −36.9 | −35.0 | −6.9 | +9.1 | +12.4 |
| B | −0.1 | +0.2 | +0.2 | −0.1 | +0.8 | −0.0 | −0.1 | −0.0 | −0.1 | +0.4 | −0.2 | −0.4 |
| C | +0.1 | −0.8 | −0.5 | +0.5 | +0.3 | +0.6 | +1.9 | +3.4 | +0.5 | −0.8 | +0.1 | −0.4 |

Base load: the mean of the 30 lowest days of each year, as % of that year's mean day. Source:
`docs/presentation/lowest30_pct_of_mean.csv`.

| Year | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Measured | 29.9 | 29.0 | 30.1 | 26.4 | 28.7 | 30.0 | 30.5 | 28.7 | 32.2 | 31.2 | 30.7 |
| Standard | 21.6 | 19.6 | 21.3 | 20.6 | 21.8 | 21.8 | 19.0 | 21.1 | 20.5 | 19.9 | 20.4 |
| B | 27.8 | 25.0 | 25.5 | 22.4 | 23.8 | 26.4 | 25.8 | 25.4 | 26.0 | 25.6 | 26.4 |
| C | 32.0 | 29.2 | 31.6 | 29.5 | 30.3 | 32.2 | 30.0 | 32.0 | 31.8 | 31.3 | 30.4 |

Reading:

- With the same annual total, the standard profiles' base is too small and their winter too peaky.
- The summer shortfall of 35–39% is pushed into winter after scaling, giving about +10%.
- Most likely cause: scope. `DIS` includes users with steadier, less temperature-dependent
  demand that G1A/G2A don't describe.
- This is consistent with the data but not proven, because no measured G1A + G2 series is
  available.

### 8.5 `TOT` view (all Dutch gas, carrier `gas_total`, no standard profiles)

| Method | Peak week signed / abs / worst | Top 1% signed / abs | Cold spells | nRMSE |
|---|---|---|---:|---:|
| A1 | −16.1 / 18.8 / −33.0 | −19.8 / 20.7 | −23.4 | 26.8 |
| B | −4.2 / 5.9 / −15.2 | −5.5 / 7.2 | −3.4 | 9.6 |
| C | −2.6 / 6.3 / −12.6 | −3.6 / 7.1 | −7.3 | 9.3 |

There is no standard profile for industry and power plants (about half of `TOT`). Applied to `TOT` anyway,
its peak-week error was +52%, which mostly reflects that gap. So it is left out.

---

## 9. Limitations and open points

1. **Scope mismatch.** Standard profiles G1A + G2A vs measured `DIS`, which also contains GXX and other
   regional-grid users. Part of the standard profiles' error is scope, not profile quality (§8.4). B and C learn the
   full `DIS` shape, so part of their advantage is scope as well.
   The split between scope and profile quality is unknown; settling it is the first item of §12.2.
2. **GXX volume not available.** We searched the publisher's annual reports and protocols (local PDFs),
   the publisher's website (domain parked, no web archive) and MFFBAS public pages (documents behind the
   mijnMFFBAS login). No GXX volume was found. Possible sources: mijnMFFBAS, grid operators/EDSN,
   ACM. GXX profiles exist only from 2020, and the protocol applies GXX with a forecast temperature.
3. **Reconstruction, not forecast.** Every method gets actual weather and the measured annual total.
4. **Few extremes.** Two real cold spells in 11 years. Worst-case statistics rest on very few days.
5. **Drift.** Insulation, heat pumps and the 2022 price shock change temperature sensitivity.
   Pooled training averages over this. There is no year trend.
6. **Daily only.** No statement about peak hours.
7. **Context.** The publisher itself reports a mismatch (MCF) of about 12% for 2016–2021, 19.4% for 2022
   and 16.9% for 2023.
8. **G1A 2022 parameters.** Version difference suspected (§3.3), not checked.
9. **Unused files.** New GXX files for 2016–2019 are not read.

---

## 10. Reproduction

Environment: Windows, Python 3.12, project `.venv` (`requirements.lock`).

```powershell
.\.venv\Scripts\python.exe -m src.run_loyo --config config.yaml --carrier heat gas_total   # ~1 min (56 s measured)
$env:PYTHONDONTWRITEBYTECODE=1; .\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider  # 45 tests
```

Key configuration (`config.yaml`):

- `weather.path`: the 12 KNMI files.
- `weather.stations: nedu`.
- `methods: [A1, NEDU, B, C]` (the code name of the standard-profile reference).
- `carriers.heat.demand.column: DIS`.
- `carriers.gas_total.methods: [A1, B, C]`.
- `nedu: {path: data/raw/Profielen-*.zip, weights: {G1A: 0.75, G2A: 0.25}, new_tac_from: 2016}`.
- `cold_spells`.
- `boosting` (§6.4).

Outputs in `results/<carrier>/` (git-ignored):

- `tables/summary.csv` (19 columns), `summary_detailed.csv`, `by_year.csv`, `cold_spells.csv`,
  `coverage.csv`, `unrescaled_level.csv`;
- `metrics.json` (all metrics, metadata, provenance);
- `predictions/<year>.parquet` (measured and rebuilt series);
- `figures/`.

Code map:

| File | Content |
|---|---|
| `src/ingest.py` | Readers: ENaGaD, KNMI (`.txt`/`.zip`), station weighting, TAC columns |
| `src/nedu.py` | TAC formulas, profile file parser and reader, profile fractions, daily standard-profile reference |
| `src/features.py` | Weather and calendar features |
| `src/methods/` | A1 (`m_a_reference_year.py`), B (`m_b_regression.py`), C (`m_c_gbm.py`) |
| `src/run_loyo.py` | Leave-one-year-out loop, common support, scaling, standard-profile wiring |
| `src/metrics.py`, `src/report.py` | Indicators, summaries, figures |
| `tests/test_nedu.py` | Parser formats, TAC formula, KNMI zip, weight renormalisation, standard profiles in the pipeline |

Presentation (`docs/presentation/`, self-contained HTML, open in a browser; arrow keys, `F` for
full screen; `?static` turns animations off, e.g. for printing to PDF):

- `standard_profiles_vs_learning.html`: 9-slide, ~10-minute deck for outsiders. Purpose: show the
  signal, state the open questions and say what would help; no plans, timelines, hours or
  budget. Slides: title; why it matters; the process (four questions that changed the plan);
  human steers, AI speeds up; the first signal (interactive year explorer of the coldest week per
  held-out year, `?year=2021` preselects a year; headline "+12% on average, every year; part of
  this may be scope"); four open questions; what would help (unordered wishes, §12 for the
  internal plan); what is already in place (validated copy of the official profiles, 11 years,
  full rerun under a minute, ~56 s measured, 45 checks); closing summary with a teaser: other carriers (electricity) and physics + data with the WPG team "would be a project of its own". Both decks say
  "official / Dutch standard profiles".
- `standard_profiles_vs_learning_appendix.html`: 17 technical slides: A1 data and scope, A2 official profile formula,
  A3 TAC validation (§3.3), A4 evaluation design, A5 ML step by step (§6.5), A6 B and C settings,
  A7 indicator definitions (§7), A8 all numbers, A9 per year, A10 2018 cold spell, A11 seasonal
  shape, A12 level drift (§6.6), A13 ML takeaways (§6.7), A14 WPG (§11), A15 wishes (what would help, unordered),
  A16 limitations. The internal plan with hours is only in §12.
- Built from `results/heat/` and the TAC check figures with a standalone script (inline SVG
  charts, TNO template colours, background and logo). The script is not part of the pipeline.
- Chart definitions: explorer = held-out
  predictions around each year's peak week (3 weeks before to 4 weeks after); monthly chart =
  §8.4; TAC chart = G1A sums from §3.3.
- Monthly and base-load tables: `docs/presentation/*.csv`.

---

## 11. WPG — tried, too different to compare

Day 1 setup (1 weather station, De Bilt); details and numbers in [results.md](results.md)
(Result 1 and the backup section).

- **What:** the WPG profile "Eem en Zonnestein": a physics-based model of residential space
  heating + hot water for one neighbourhood (our methods are data-driven), used as given and
  scaled to the `DIS` total (method A\*).
- **Result:** coldest week about 34% too high in every year (mean absolute peak-week error 34.3%).
- **Why it is not comparable:** WPG is almost flat in summer (7.9% of its 2019 energy in
  May–September vs 18.7% for `DIS`), while about 40% of `DIS` behaves like a base that home
  heating does not explain (~180 GWh/d, weekend dip ~45 GWh/d). Scaling to the same total
  pushes the missing summer base into winter.
- **What is good:** the timing. Daily correlation with `DIS` is about 0.90–0.97.
- **Fitted variants** (A\*b: WPG + base 6%; B+W: regression + WPG input 1.5% vs B 2.5%) were fitted
  on `DIS`, so they test "WPG + measured history", not WPG. Kept as backup, not as results.
- **Synergy to explore:** physics (WPG) for households and behaviour, data for the rest and the
  measured total. First confirm with the WPG owner that WPG for year Y uses no measured gas.
  Planned for 2027 as a separate, cuttable work package: apply WPG's temperature-sensitivity
  ratio to the learned baseline to describe heat scenario change (§12.4).

---

## 12. Next steps

### 12.1 What the two days established

- **Headline.** The official standard profile (G1A + G2A) overestimates the measured peak week by
  +12% on average (worst +17.5%) and the cold spells by +16%, in every year 2015–2025. The learning
  methods bring the peak-week error to about 3% (§8.1).
- **Weakness to resolve before the headline is used as evidence: scope.** `DIS` also holds GXX and
  hourly-metered users; the profile covers only G1A/G2. §8.4 shows the mechanism: a summer
  shortfall of 35–39% is pushed into winter by the annual rescaling, which gives about +10%. How
  much of the +12% is profile quality and how much is scope is unknown.
- **Two method findings that carry into next year:**
  1. Trees do not extrapolate: C underestimates both cold spells by 7–9%, B stays within 1–3%
     (§8.3, §6.7).
  2. The level error before rescaling is up to ±20% (break in 2022) and is hidden by the
     rescaling (§6.6).

### 12.2 Next 40 h — TES demonstrator, December 2026

Priority order. No electricity, no hourly data, no interviews.

| # | Work | What it decides |
|---|---|---|
| 1a | **Seasonal rescaling:** add an option to scale to the heating-season total (Oct–Mar) instead of the annual total, and rerun | If the standard profile's winter error largely disappears: the headline becomes "winter overestimate caused by a missing summer base". If it remains: the headline stands as the profile's temperature response. |
| 1b | **A-priori non-residential share:** fix the share of non-profiled use from a published source (CBS household vs other small-user gas) and compare the standard profile with `DIS` minus that share. No fitting on `DIS`. | Second, independent estimate of the scope effect |
| 2a | **Same features for B and C** | Makes the B–C gap a model-type difference only (§6.7, point 4) |
| 2b | **Extrapolation:** a hybrid (linear model + boosting on its residuals) and/or monotone constraints on the heating features; cold-spell errors for B, C and the hybrid | Which model class to trust outside the training range |
| 2c | **Quantile bands** P10/P50/P90 with a coverage check on held-out years | Whether the uncertainty is honest |
| 3 | **Report on the template** (Description / Result / Learned / Follow-up), with the scope caveat next to the headline number | Demonstrator deliverable |

Dropped from the 40 h: interviews with model owners, FAIR packaging, licence and release work,
electricity.

### 12.3 Additional 40 h this year (if available) — data and liaison, not modelling

Only needed to make the headline defensible for the 2027 proposal; the demonstrator does not
depend on it.

1. Collect the demand profile **files** actually used in TES models (files, not interviews), so the
   comparison can also be run against the department's own profiles.
2. Obtain GXX volumes (mijnMFFBAS, grid operators) and CBS household vs small-business gas shares,
   to close the scope question properly (§9, point 2).
3. Check whether hourly ENTSOG distribution-exit data exist and are usable. This decides whether
   2027 can address peak hours.

### 12.4 2027 plan

Same storyline as the pitch (from a signal to an answer), with updated content. Sized to the
existing proposal budget; the outsider deck shows no hour figures.

| Work package | Content | Notes |
|---|---|---|
| **Electricity (main line)** | Official standard electricity profiles (E1A etc.) vs learning, same leave-one-year-out protocol, with base-load separation | Extends a demonstrated method to a second carrier instead of introducing a new one |
| **All climate years** | A method question, not a batch run: which model class is trustworthy outside the training range (linear / hybrid / constrained boosting), tested on historic extremes and KNMI scenario years | Builds on 12.2, item 2b |
| **Level term** | Trend or post-2022 break; the generator must give level *and* shape | Mandatory for any forward use (§6.6) |
| **Heat scenario change** | Temperature-sensitivity ratio from WPG applied to the learned baseline | Separate, cuttable work package, with the WPG owner (§11) |

The decks show none of this plan; they list wishes only (appendix A15).

### 12.5 Removed from the plan

The earlier "FAIR code base ~140 h, interviews ~60 h, ~580 h in three steps" plan and the
"~80 h small start" ask are removed from the deck and from this reference.

Presenters (title slide only; the closing slide has no contact line): Ni Wang and Thije Teunis.
