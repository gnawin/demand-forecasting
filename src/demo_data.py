"""Generate labelled synthetic fixtures, never claimed to be measured evidence."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .align import aggregate_days, daily_grid


def generate(destination: Path, first: int = 2015, last: int = 2024) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    index = pd.date_range(f"{first - 1}-12-31", f"{last + 1}-01-02", freq="h", tz="UTC", inclusive="left")
    local = index.tz_convert("Europe/Amsterdam")
    doy = local.day_of_year.to_numpy()
    hour = local.hour.to_numpy()
    temp = 10 - 9 * np.cos(2 * np.pi * (doy - 20) / 365.25)
    temp += 3 * np.sin(np.arange(len(index)) / (24 * 5)) + rng.normal(0, 0.6, len(index))
    temp -= np.where(local.year == first + 1, 2, 0)
    wind = 4 + 2 * np.sin(np.arange(len(index)) / 99) ** 2
    ghi = np.maximum(0, np.sin((hour - 6) * np.pi / 12)) * (200 + 100 * np.sin(doy * np.pi / 365))
    heating = np.maximum(0, 15.5 - temp)
    weather = pd.DataFrame({"temp_c": temp, "wind_ms": wind, "ghi_wm2": ghi}, index=index)
    weather.rename_axis("timestamp_utc").to_csv(destination / "weather.csv")
    electric = 9000 + heating * 150 + 1200 * np.sin((hour - 7) * np.pi / 12)
    electric += np.where(local.day_of_week < 5, 800, -500) + rng.normal(0, 100, len(index))
    pd.Series(electric, index=index, name="value").rename_axis("timestamp_utc").to_csv(destination / "electricity.csv")
    gas = pd.Series(300 + heating * 70 + 15 * wind, index=index, name="value")
    daily = aggregate_days(gas, "Europe/Amsterdam", 6)
    wanted = pd.DatetimeIndex([t for year in range(first, last + 1) for t in daily_grid(year, "Europe/Amsterdam", 6)])
    daily = daily.reindex(wanted)
    daily.index = daily.index.tz_convert("Europe/Amsterdam").strftime("%Y-%m-%d")
    daily.rename_axis("timestamp_utc").to_csv(destination / "heat.csv")
    # A* is deliberately imperfect but shares the canonical time contract.
    pd.Series(electric * (1 + 0.015 * np.cos(hour * np.pi / 12)), index=index, name="value").rename_axis(
        "timestamp_utc",
    ).to_csv(destination / "external.csv")
    config = {
        "timezone": "Europe/Amsterdam", "years": list(range(first, last + 1)),
        "resolution": "auto", "minimum_coverage": 0.9, "methods": ["A1", "B", "C"],
        "processed_dir": "processed", "results_dir": "results",
        "weather": {"path": "weather.csv", "format": "csv"},
        "carriers": {
            "electricity": {
                "demand": {"path": "electricity.csv"}, "units": "synthetic MW",
                "daily_start_hour": 0, "external_profile": "external.csv",
            },
            "heat": {
                "demand": {"path": "heat.csv"}, "units": "synthetic daily mean MW",
                "daily_start_hour": 6, "daily_label": "date",
            },
        },
        "cold_spells": [{"name": "Synthetic winter window", "start": f"{first + 1}-02-01", "end": f"{first + 1}-02-10"}],
        "boosting": {"backend": "sklearn", "max_iter": 500, "threads": 2, "random_seed": 42},
        "data_kind": "synthetic",
    }
    path = destination / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data") / "raw" / "demo")
    parser.add_argument("--first-year", type=int, default=2015)
    parser.add_argument("--last-year", type=int, default=2024)
    args = parser.parse_args()
    if args.last_year <= args.first_year:
        parser.error("Use at least two years.")
    print(generate(args.output, args.first_year, args.last_year))


if __name__ == "__main__":
    main()
