# Demand profile validation

Checks how well demand profiles match measured reality during peak weeks and
cold spells. For each historical year, every method rebuilds that year's profile
from only its weather, calendar and annual total, and is compared with the
measured data (leave-one-year-out).

## What you need locally

- **Python 3.12** (on Windows via the `py` launcher) and **Git**
- **The input data files** — not in the repo; get them from the shared folder

## Setup (once)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
```

`requirements.lock` pins the exact package versions, so everyone gets the same
environment. Optional check: `python -m pytest -q` (all tests should pass).

## Input

Put the files in `data/raw/`. File names and settings are in `config.yaml`.

| File | Format | Notes |
|---|---|---|
| `heat.csv` | `timestamp_utc,value` | One row per gas day, e.g. `2018-02-28,123456`. Gas day D runs 06:00 to 06:00 the next day (local time). Set `units` in `config.yaml`. |
| `knmi_uurgeg.txt` | KNMI hourly export (`uurgegevens`) with `T`, `FH`, `Q` | Hourly is fine: it is averaged to gas days automatically. Stations listed in `weather.stations` (default De Bilt, 260) are averaged. Cover up to 1 January after the last year. |
| `electricity.csv` (optional) | `timestamp_utc,value` | Hourly, UTC, e.g. `2018-02-28T23:00:00Z,12345`. Test bench only. |

Years with less than 90% data are skipped and listed in the output.

## Run

```powershell
.\.venv\Scripts\Activate.ps1          # in each new terminal
python -m src.run_loyo --config config.yaml --carrier heat
```

Use `--carrier electricity` for the test bench, or `--carrier electricity heat` for both.

## Output

Written to `results/<carrier>/`:

| File | Content |
|---|---|
| `tables/summary.csv` | One row per method: peak-week, top-1%, annual-mean, nRMSE and cold-spell errors in %; mean, worst and coldest year. Ready for the Result slide. |
| `figures/fig1_peak_week.png` | Measured (black) vs. each method in the coldest year's peak week |
| `figures/fig2_metric_bars.png` | Peak-week and top-1% errors per method |
| `figures/fig3_annual_scatter.png` | Measured vs. rebuilt, coldest year |
| `figures/fig4_loyo.png` | Peak-week error per year |
| `metrics.json` | All numbers, data coverage and included/excluded years |

## More

Data sources, methods, metrics, limitations and the synthetic demo:
[docs/details.md](docs/details.md).
