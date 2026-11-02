"""Demand-independent weather and Dutch local calendar features."""

from __future__ import annotations

import holidays
import numpy as np
import pandas as pd


def build_features(frame: pd.DataFrame, mode: str, timezone: str) -> pd.DataFrame:
    result = frame.copy()
    local = result.index.tz_convert(timezone)
    nl = holidays.country_holidays("NL", years=range(int(frame["year"].min()) - 1, int(frame["year"].max()) + 2))
    result["weekend"] = (local.day_of_week >= 5).astype(int)
    result["holiday"] = [int(date in nl) for date in local.date]
    result["month"] = local.month
    result["day_of_year"] = local.day_of_year
    result["weekday"] = local.day_of_week
    result["hour"] = local.hour if mode == "hourly" else 0
    result["hour_weekend"] = result["hour"] + 24 * result["weekend"] if mode == "hourly" else result["weekend"]
    result["heating"] = np.maximum(0, 15.5 - result["temp_c"])
    result["cooling"] = np.maximum(0, result["temp_c"] - 22)
    result["wind_heating"] = result["wind_ms"] * result["heating"]
    for hours in (24, 72):
        # Only weather enters the rolling windows. Each year starts independently.
        window = hours if mode == "hourly" else hours // 24
        result[f"heating_{hours}h"] = result.groupby("year")["heating"].transform(
            lambda s: s.rolling(window, min_periods=1).mean()
        )
    return result


LINEAR_NUMERIC = ["heating", "heating_24h", "heating_72h", "cooling", "wind_ms", "wind_heating", "ghi_wm2", "holiday"]
CATEGORICAL = ["hour_weekend", "month"]
GBM_FEATURES = LINEAR_NUMERIC + CATEGORICAL + ["day_of_year", "temp_c", "weekday"]
