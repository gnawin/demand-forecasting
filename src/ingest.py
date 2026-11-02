"""Read canonical CSVs, configurable platform exports and KNMI hourly data."""

from __future__ import annotations

import io
import os
from pathlib import Path

import numpy as np
import pandas as pd


def utc_index(values: pd.Series, timezone: str | None = None) -> pd.DatetimeIndex:
    if pd.Timestamp(values.iloc[0]).tzinfo is None:
        if timezone is None:
            raise ValueError("Naive timestamps require an explicit source timezone.")
        parsed = pd.DatetimeIndex(pd.to_datetime(values, format="mixed"))
        # Repeated local autumn hours must appear in chronological order.
        parsed = parsed.tz_localize(timezone, ambiguous="infer", nonexistent="raise")
    else:
        if not values.astype(str).str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", regex=True).all():
            raise ValueError("Do not mix timezone-aware and naive timestamps.")
        parsed = pd.DatetimeIndex(pd.to_datetime(values, format="mixed", utc=True))
    return parsed.tz_convert("UTC")


def read_demand(
    settings: dict, base: Path, *, daily_label: str, daily_start_hour: int,
    timezone: str, years: list[int],
) -> pd.Series:
    kind = settings.get("format", "csv")
    if kind == "entsoe_api":
        from entsoe import EntsoePandasClient

        key = os.environ.get("ENTSOE_API_KEY")
        if not key:
            raise ValueError("entsoe_api requires ENTSOE_API_KEY; alternatively supply a CSV.")
        client = EntsoePandasClient(api_key=key)
        parts = [
            client.query_load(
                settings.get("bidding_zone", "NL"),
                start=pd.Timestamp(year=y, month=1, day=1, tz="UTC"),
                end=pd.Timestamp(year=y + 1, month=1, day=1, tz="UTC"),
            )
            for y in years
        ]
        result = pd.concat(parts)
        if isinstance(result, pd.DataFrame):
            if result.shape[1] != 1:
                raise ValueError("ENTSO-E returned multiple load columns; select a CSV column.")
            result = result.iloc[:, 0]
        result.index = result.index.tz_convert("UTC")
        # API endpoints may include an identical interval boundary twice.
        if result.index.has_duplicates and result.groupby(level=0).nunique(dropna=False).max() > 1:
            raise ValueError("Conflicting ENTSO-E values at duplicate API interval boundaries.")
        result = result[~result.index.duplicated()].sort_index()
        return validate_demand(result)
    if kind not in {"csv", "entsoe_csv", "entsog_csv"}:
        raise ValueError(f"Unknown demand format: {kind}")
    frame = pd.read_csv(base / settings["path"], sep=settings.get("separator", ","))
    timestamp = settings.get("timestamp_column", "timestamp_utc")
    value = settings.get("value_column", "value")
    if daily_label not in {"date", "timestamp"}:
        raise ValueError("daily_label must be 'timestamp' or 'date'.")
    parsed_utc = pd.to_datetime(frame[timestamp], format="mixed", utc=True)
    is_daily = parsed_utc.sort_values().diff().median() >= pd.Timedelta(hours=23)
    if daily_label == "date" and is_daily:
        if pd.Timestamp(frame[timestamp].iloc[0]).tzinfo is not None:
            dates = parsed_utc.dt.tz_convert(timezone).dt.tz_localize(None)
        else:
            dates = pd.to_datetime(frame[timestamp], format="mixed")
        dates = dates.dt.normalize() + pd.Timedelta(hours=daily_start_hour)
        index = pd.DatetimeIndex(dates).tz_localize(timezone).tz_convert("UTC")
    else:
        index = utc_index(frame[timestamp], settings.get("timezone"))
    return validate_demand(pd.Series(pd.to_numeric(frame[value], errors="raise").to_numpy(), index=index))


def validate_demand(series: pd.Series) -> pd.Series:
    series = series.astype(float).sort_index().rename("value")
    if series.index.has_duplicates:
        raise ValueError("Duplicate demand timestamps; aggregate source points explicitly before ingestion.")
    if len(series) < 2:
        raise ValueError("Demand needs at least two timestamps.")
    present = series.dropna()
    if not np.isfinite(present).all() or (present < 0).any():
        raise ValueError("Demand must contain finite, non-negative values or NaN.")
    return series


def read_knmi(path: Path, stations: list[int] | None = None) -> pd.DataFrame:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    headers = [line.lstrip("# ").strip() for line in lines if line.lstrip("# ").startswith("STN,")]
    if not headers:
        raise ValueError(f"No '# STN,YYYYMMDD,HH,...' header in {path}")
    names = [name.strip() for name in headers[-1].split(",")]
    body = "\n".join(line for line in lines if line.strip() and not line.lstrip().startswith("#"))
    frame = pd.read_csv(io.StringIO(body), names=names, skipinitialspace=True, na_values=["", " "])
    required = {"STN", "YYYYMMDD", "HH", "T", "FH", "Q"}
    if not required.issubset(frame):
        raise ValueError(f"KNMI export needs {sorted(required)}")
    if stations:
        frame = frame.loc[frame["STN"].isin(stations)].copy()
    if frame.empty:
        raise ValueError(f"No selected stations in {path}")
    if not frame["HH"].between(1, 24).all():
        raise ValueError("KNMI HH must be in 1..24 (hour ending).")
    times = pd.to_datetime(frame["YYYYMMDD"].astype(str), format="%Y%m%d", utc=True)
    times += pd.to_timedelta(frame["HH"] - 1, unit="h")
    data = pd.DataFrame({
        "station": frame["STN"].to_numpy(),
        "temp_c": frame["T"].replace(-9999, np.nan).to_numpy() / 10,
        "wind_ms": frame["FH"].replace(-9999, np.nan).to_numpy() / 10,
        "ghi_wm2": frame["Q"].replace(-9999, np.nan).to_numpy() * 10000 / 3600,
    }, index=pd.DatetimeIndex(times, name="timestamp_utc"))
    return data


def read_weather(settings: dict, base: Path) -> pd.DataFrame:
    paths = settings["path"]
    if isinstance(paths, str):
        paths = [paths]
    parts = []
    for path in paths:
        if settings.get("format", "csv") == "knmi":
            part = read_knmi(base / path, settings.get("stations"))
        elif settings.get("format", "csv") == "csv":
            frame = pd.read_csv(base / path)
            part = frame.set_index(utc_index(frame["timestamp_utc"]))
            part = part.drop(columns="timestamp_utc")
            if "station" in part and settings.get("stations"):
                part = part.loc[part["station"].isin(settings["stations"])]
        else:
            raise ValueError("Weather format must be csv or knmi.")
        parts.append(part)
    combined = pd.concat(parts)
    if "station" in combined:
        if combined.reset_index(names="timestamp_utc").duplicated(["timestamp_utc", "station"]).any():
            raise ValueError("Duplicate station/hour weather observations.")
    elif combined.index.has_duplicates:
        raise ValueError("Weather duplicates require a station column for explicit averaging.")
    columns = ["temp_c", "wind_ms", "ghi_wm2"]
    combined[columns] = combined[columns].apply(pd.to_numeric, errors="raise")
    weather = combined[columns].groupby(level=0).mean().sort_index()
    if weather.empty or not np.isfinite(weather.dropna().to_numpy()).all():
        raise ValueError("Weather is empty or contains infinite values.")
    return weather
