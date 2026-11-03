# Details

Reference for the pipeline. For setup, inputs and outputs see the [README](../README.md).

## More run options

- macOS/Linux: `python3.12 -m venv .venv` and `source .venv/bin/activate`.
- `--carrier all` runs every carrier defined in `config.yaml`.
- `--methods A1` runs a single method for a quick smoke test.
- Paths in the config are relative to the config file.
- Optional extras: `python -m pip install -e ".[entsoe,lightgbm]"` (ENTSO-E API,
  LightGBM). Not needed for CSV inputs; keep `boosting.backend: sklearn` so
  everyone gets the same results.
- Changing dependencies: update `pyproject.toml`, reinstall, run the tests, then
  `python -m pip freeze --exclude-editable > requirements.lock` (keep the header).

## Runnable demo without measured data

```powershell
python -m src.demo_data --output data\raw\demo
python -m src.run_loyo --config data\raw\demo\config.yaml --carrier all
```

This generates ten years (2015-2024), hourly synthetic electricity, daily
synthetic gas, weather, and an external electricity profile. Outputs are under
`data\raw\demo\results\<carrier>`. It exercises all required methods and both
resolutions, **not an empirical validation**. Demo units and JSON provenance
explicitly say synthetic. Raw files are never modified by the pipeline.
Real measured input files are not included. Do not present synthetic errors
as evidence that a method outperforms current practice.

Validated here with all ten LOYO folds and the default 500-iteration cap:
hourly electricity (A1/B/C/A*) **69.5 s**, daily gas (A1/B/C) **18.4 s**,
including figures and tables, using the sklearn backend and two threads.
Both are below the 300-second target; this is a synthetic-data benchmark on
the current Windows machine, not a guarantee for other hardware/data.

## Input contracts and sources

### Demand and external profiles

Canonical CSV: `timestamp_utc,value`, one national series, explicit UTC offsets,
finite non-negative values or blank/NaN. Use consistent units within a carrier;
the pipeline does not infer energy units or convert gas volume to energy.
Daily values can be interval means or interval totals, but all inputs for that
carrier must use the same convention. Annual normalization is the **sum of
interval values**, not a separate conversion from power to energy.
Hourly/subhourly input is averaged to hourly; daily input is never upsampled.
Subhourly intervals must divide the hour and align to interval boundaries;
an hour with missing source intervals counts as missing, not as a full hour.
Set the units in the carrier configuration; heat's default is only a placeholder.

- **Electricity**: ENTSO-E Transparency, Actual Total Load [6.1.A], bidding zone NL.
  Endpoint: `https://web-api.tp.entsoe.eu/api`, query `documentType=A65`,
  `processType=A16`, `outBiddingZone_Domain=10YNL----------L`, annual
  `periodStart`/`periodEnd` windows in UTC. With `format: entsoe_api`,
  `entsoe-py`'s `query_load("NL", start=..., end=...)` uses `ENTSOE_API_KEY`.
  Without a key, supply the canonical CSV or use `format: entsoe_csv` with
  `timestamp_column`, `value_column`, `separator`, and source `timezone`.
  There is no guessed parser for arbitrary portal column layouts: configure
  the columns or convert the export to the canonical contract.
  CET/CEST naive timestamps require `timezone: Europe/Amsterdam`. Repeated
  autumn hours must be chronologically ordered; nonexistent local hours error.
- **Heat**: ENTSOG Transparency, `https://transparency.entsog.eu/api/v1/operationaldatas`,
  `operatorKey=NL-TSO-0001`, `indicator=Physical Flow`, `directionKey=exit`,
  `periodType=day` (or `hour` if verified), requested `from`/`to` dates.
  Partner must select and aggregate **Distribution** exit points using the
  platform's point/operator metadata (e.g. `/operatorpointdirections`), retain
  matching direction/units, and resolve revisions before exporting.
  Do **not** substitute **Final Consumers** (large industry/power). There is no
  automatic ENTSOG download or implicit aggregation of point-level rows:
  use `format: entsog_csv`, configure columns, or deliver canonical CSV.
  Duplicate timestamps error rather than risking double-counted flows.
  Distribution includes small non-residential users, cooking and hot water;
  interpret this as **gas demand of the built environment**, not isolated
  space-heating or residential-only demand.
- **Heat (used)**: JRC ENaGaD daily gas demand (`format: enagad`, `country: NL`,
  `column: DIS`), GWh per gas day. `DIS` is distribution-grid demand:
  residential **and** commercial, cooking and hot water. Note the structural
  drop from 2022 (gas crisis): ~180 TWh/yr before, ~135 TWh/yr after.
- **A***: set `carriers.<name>.external_profile` to the department/WPG profile:
  either a canonical `timestamp_utc,value` CSV path, or a mapping with `path`
  (list of CSV/xlsx files), `timestamp_column`, `timezone` (for naive local
  time incl. DST) and `value_columns` (summed). Hourly power in daily mode is
  summed to energy per gas day (23/25-hour DST days included); incomplete gas
  days are dropped.
  It is enabled automatically unless `--methods` explicitly overrides selection.
  A* is only run in held-out years the profile covers for at least
  `minimum_coverage`. In those years every method is scored on the same common
  support (measured and covered by A*); dropped intervals are listed under
  `common_support` in `metrics.json`. `summary_external_years.csv` and
  `fig5_external_<year>.png` compare all methods in just those years.
  It is normalized identically to all other methods. A supplied profile must
  itself satisfy the held-out-input contract; the pipeline cannot audit its
  provenance. A residential-only profile (WPG) has no space heating in summer,
  whereas `DIS` keeps a commercial/cooking base, so A* misses the summer base
  and, after rescaling to the annual total, overshoots winter.

Daily gas date D denotes D **06:00 local** to D+1 **06:00 local**. For date
labels set `daily_label: date` and `daily_start_hour: 6` (heat defaults).
The configured timestamp column may contain date-only strings. Already UTC
window-start timestamps should use `daily_label: timestamp`; these are checked
against the configured local hour. `daily_label: date` applies only to
daily-resolution sources; hourly heat retains its actual UTC timestamps.
Daily electricity normally uses local midnight. For UTC-calendar-day demand,
configure a separate run with the matching timezone and window definition.

### Weather

Canonical CSV: `timestamp_utc,temp_c,wind_ms,ghi_wm2`, hourly **hour-beginning UTC**.
Optional `station` column enables equal-weight station averaging.
The weather `path` can be a file or a list of files.

KNMI endpoint: `https://www.daggegevens.knmi.nl/klimatologie/uurgegevens`,
request `stns=260`, `vars=T:FH:Q` (optionally `U`),
`start=YYYYMMDD01`, `end=YYYYMMDD24`; use the requested historical range
plus enough trailing hours for the final daily window.
Set `weather.format: knmi` to read standard commented text exports with a
`# STN,YYYYMMDD,HH,...` header. KNMI HH=1..24 is **hour ending**: subtract
one hour to get the demand interval start. Temperature and wind are divided
by 10, radiation in J/cm2 multiplied by 10000/3600 to obtain W/m2.
`-9999` is missing, never a physical value.

Minimum station: De Bilt 260. Suggested stations:
260 (De Bilt), 240 (Schiphol), 280 (Eelde), 380 (Maastricht),
310 (Vlissingen), 290 (Twenthe). `weather.stations` filters the input; averaging
uses available stations equally at each timestamp (not population weights).
Station availability is not silently interpreted as national coverage.
Weather gaps up to three consecutive hours are interpolated as whole interior
gaps; longer gaps or incomplete daily windows error in eligible years.

## Alignment, support and validation

- Detect native resolution by median timestamp gap (daily tolerates DST's
  23/25-hour spacing); `resolution: hourly|daily|auto` overrides aggregation,
  never daily-to-hourly interpolation. Daily weather averages use exactly
  the corresponding local demand window. DST gas days contain 23/25 hours.
  Daily calendar features refer to the local start-date of that window.
- Hourly evaluation years use full UTC Jan-Dec grids; daily evaluation years
  use all local date labels Jan-Dec. Leap years are retained.
- Expand missing demand timestamps onto the grid. Original coverage, missing
  share, interpolated count, and remaining missing count are recorded per year.
  Only whole **interior demand gaps <=3 hours** are linearly filled; no partial
  filling of longer gaps and no filling of daily gaps.
- Years below **90% original coverage** (or a stricter configured threshold)
  are excluded from **training and evaluation**, with reasons in JSON and
  `coverage.csv`. Requested excluded held-out years are listed but skipped.
  At least two eligible years are required, fewer than five produces a warning.
- Models predict the complete held-out grid. Missing measurements mask every
  model identically. Annual totals are the measured sum **after short-gap
  interpolation over that shared support**; long gaps are not extrapolated to
  an imagined complete annual total. This means incomplete years' totals are
  coverage-dependent. All annual means match on the common valid intervals.
- Demand-independent weather rolling features are computed separately within
  each year, with shorter windows at year starts. Missing weather is not silently
  fed into a model. Local calendar/holidays use `Europe/Amsterdam` and `holidays`
  country NL. Learned preprocessing is fit only on training years.
- C's validation set is 10% of **whole local training days**, deterministic seed
  42. No hourly random validation or held-out-year tuning.

## Methods

- **A1**: most recent eligible training year, mapped to the nearest reference
  date with matching ISO weekday and local hour. Leap-day target date is
  anchored on February 28 in non-leap reference years. Repeated DST local
  hours are averaged. Missing reference slots use training weekday/hour means
  with an explicit warning. Annual normalization is external to the method.
- **B**: Ridge (alpha=1) on HDD(15.5 C), trailing 24/72-hour HDD means, cooling
  above 22 C, wind, wind x HDD, irradiance, holiday flag, one-hot month and
  local-hour x weekend (48 possible categories; weekend only in daily mode).
  Numeric features are standardized on training data.
- **C**: same features plus raw temperature, day-of-year and weekday, using
  LightGBM when available or sklearn HistGradientBoostingRegressor.
  Up to 500 trees/iterations, learning rate .05, 31 leaves, minimum 50 samples
  per leaf; validation early stopping with 30-iteration patience.
  sklearn >=1.7 uses explicit validation arrays for the fixed whole-day split;
  both backends retain the best model without accessing held-out demand.
- **A***: externally supplied reference/WPG profile, no fit; identical
  annual-total scaling and metrics. The key real-world comparison to use when
  the department profile is available.
- **Backup, not used as results** (see `docs/results.md`, "Backup"). Off by
  default; enable by adding them to `methods:`.
- **A\*b** (WPG + base): ordinary least squares fitted on the training years only,
  `measured ≈ a·WPG + b + c·weekend + e·holiday`. *b* is a constant base (all
  demand WPG does not explain: commercial, cooking, other); *c*, *e* capture
  calendar effects of commercial use. It is a fitted constant, not a separate
  commercial profile. WPG is divided by its training mean before the fit
  (unscaled ~1e7 Wh values made the solver drop the 0/1 columns). Coefficients
  and the base share (b / mean training demand) are in `metrics.json`.
- **A\*b22**: as A\*b, plus a post-crisis indicator (`year >= crisis_year`,
  default 2022) and its interaction with WPG, so both *b* and *a* may change
  from 2022.
- **B+W**: B with the WPG gas-day energy as one extra standardized numeric
  input. Tests whether WPG adds information beyond the weather features.
- Methods needing WPG run only in years it covers (A\*b, A\*b22 and B+W also
  need it in at least one training year) and are skipped with a warning for a
  carrier without `external_profile`. Before rescaling, their annual levels are
  in `unrescaled_level.csv`, like B and C.

B/C negative predictions are explicitly logged and clipped to zero before
normalization; unrescaled annual level errors refer to these physical,
non-negative profiles. Zero-sum/invalid predictions fail explicitly.
A2, C2 quantiles and year-trend experiments are intentionally not implemented;
the required A1/B/C/A* path is prioritized.

## Metrics and output interpretation

Each carrier writes to `results/<carrier>/`, preventing electricity/heat
overwrites. The generated demo uses the directory relative to its own config.

- `tables/summary.csv`: one method per row, five headline groups (peak-week,
  measured-top-1%, annual mean, nRMSE, cold-spell mean); columns for mean,
  worst and coldest year, **percentages** ready for a Result slide.
  Signed relative errors are `(rebuilt - measured)/measured`. nRMSE is positive.
- `tables/summary_detailed.csv`, `tables/by_year.csv`: absolute errors in input
  units and relative fractions, timing shift, both top selections, cold-spell
  mean/peak, raw RMSE and nRMSE. Mean absolute errors are included alongside
  signed means (which can cancel). Worst is the **signed value with largest
  magnitude**. Missing cold-spell metrics are blank, not zero.
- `tables/cold_spells.csv`: inclusive local-date windows, per-window mean error,
  maximum-within-window peak error, and error at the measured peak interval.
  It is written when at least one configured window overlaps evaluated years.
- `tables/unrescaled_level.csv`: B/C annual total errors before annual scaling.
- `metrics.json`: complete per-run metrics, aggregates, coverage, included and
  excluded years, mode, source settings, units, versions, model validation
  metadata, timing, and provenance. Relative errors are **fractions**, not %.
  Undefined relative errors (zero denominator) are JSON `null`.
- `predictions/<year>.parquet`: measured and rebuilt series, shared NaN mask.
- `data/processed/<carrier>.parquet`: aligned weather/demand and year labels.
- `figures/fig1_peak_week.png`: coldest year's measured peak week, black measured
  line; `fig2_metric_bars.png`: mean **absolute** relative errors with worst
  absolute-error whiskers; `fig3_annual_scatter.png`: A1/C coldest-year scatter;
  `fig4_loyo.png`: signed peak-week error by year. All 11 x 5 in, 150 dpi,
  fonts >=12 pt, color-blind-safe colors, labelled units.

Measured peak week is the maximum complete 168-interval (hourly) or 7-interval
(daily) rolling mean; compare rebuilt mean over **that same window**, and
report rebuilt peak-window start minus measured start in days. Missing intervals
are not compressed across gaps; no complete week is an explicit error.
Daily weeks are seven local dates, including DST days.
Top-1% means the specified **88 hourly intervals or 4 daily intervals**, even in
leap years. Compare measured and rebuilt means at the same measured-top indices;
repeat independently at rebuilt-top indices. nRMSE divides RMSE by measured mean.
Cold-spell summary averages configured window errors within each year, then
averages only years that have such windows; it is not an all-year statistic.

The coldest year is the evaluated year with the lowest mean temperature over
**Jan-Mar and Oct-Dec within that year**. Its values are separate summary
columns, and the per-year temperatures and exact definition are persisted.
With a held-out subset, this selects among that subset, not unevaluated years.
Read JSON/coverage tables for the actual included/excluded years and mode;
those depend on the supplied data, not the default desired 2015-2024 range.

## Limitations and demo reporting

Gas temperature sensitivity changes with insulation, heat pumps and the 2022
price shock. Pooled historical training averages over this **drift**; it is not
a causal or future-policy model. No numeric year trend is used. Giving every
method an annual total deliberately isolates shape, not annual-level accuracy;
raw B/C totals are only a side result. Sparse national weather station averaging,
interpolation, time resolution and unverified upstream exports limit conclusions.
Daily gas cannot establish peak-hour accuracy: all daily metrics and figure
labels explicitly switch to days.

For the slide template: **Title** the comparison and carrier; **Description**
the held-out-input contract and coverage; **Result** paste the summary and peak
week figure; **What did you learn** report supported outcomes (including A1/A*
holding up); **Follow-up** request better verified hourly gas, external profiles
and weather representativeness. Do not infer success from the electricity
sanity-check target alone: the measured-data comparison is the test.
