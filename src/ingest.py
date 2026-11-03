"""Read canonical CSVs, configurable platform exports and KNMI hourly data."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .nedu import NEDU_STATION_WEIGHTS, old_tac, station_tac

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
    if kind == "enagad":
        frame = read_enagad(base / settings["path"], settings.get("country", "NL"), settings.get("column", "DIS"))
        timestamp, value = "GASDAY", "value"
    elif kind in {"csv", "entsoe_csv", "entsog_csv"}:
        frame = pd.read_csv(base / settings["path"], sep=settings.get("separator", ","))
        timestamp = settings.get("timestamp_column", "timestamp_utc")
        value = settings.get("value_column", "value")
    else:
        raise ValueError(f"Unknown demand format: {kind}")
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


def read_enagad(path: Path, country: str, column: str) -> pd.DataFrame:
    """JRC ENaGaD daily gas table: '#' comment header, one row per gas day and country, ';' line ends."""
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    header = next((i for i, line in enumerate(lines) if line.startswith("GASDAY,")), None)
    if header is None:
        raise ValueError(f"No 'GASDAY,COUNTRY,...' header in {path}")
    body = "\n".join(line.rstrip().rstrip(";") for line in lines[header:] if line.strip(" ;"))
    frame = pd.read_csv(io.StringIO(body), na_values=["NA"])
    if column not in frame:
        raise ValueError(f"ENaGaD column {column!r} not found; available: {list(frame.columns)}")
    frame = frame.loc[frame["COUNTRY"].eq(country), ["GASDAY", column]]
    if frame.empty:
        raise ValueError(f"No ENaGaD rows for country {country!r}")
    return frame.rename(columns={column: "value"}).reset_index(drop=True)


def read_profile(settings: dict | str, base: Path) -> pd.Series:
    """External profile (e.g. WPG): canonical CSV, or CSV/Excel with naive local timestamps and value columns that are summed."""
    if isinstance(settings, str):
        settings = {"path": settings}
    paths = settings["path"] if isinstance(settings["path"], list) else [settings["path"]]
    timestamp = settings.get("timestamp_column", "timestamp_utc")
    columns = settings.get("value_columns", ["value"])
    parts = []
    for path in paths:
        if Path(path).suffix.lower() in {".xlsx", ".xls"}:
            frame = pd.read_excel(base / path, sheet_name=settings.get("sheet", 0))
        else:
            frame = pd.read_csv(base / path)
        missing = set(columns + [timestamp]) - set(frame.columns)
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}; available {list(frame.columns)}")
        values = frame[columns].apply(pd.to_numeric, errors="raise").sum(axis=1, min_count=len(columns))
        index = utc_index(frame[timestamp], settings.get("timezone"))
        parts.append(pd.Series(values.to_numpy(), index=index))
    return validate_demand(pd.concat(parts))


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
    if path.suffix.lower() == ".zip":
        # KNMI downloads come as a zip holding one .txt.
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".txt")]
            if len(names) != 1:
                raise ValueError(f"{path}: expected one .txt inside, found {names}")
            text = archive.read(names[0]).decode("utf-8-sig", errors="replace")
    else:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    header_rows = [i for i, line in enumerate(lines) if line.lstrip("# ").startswith("STN,")]
    if not header_rows:
        raise ValueError(f"No '# STN,YYYYMMDD,HH,...' header in {path}")
    names = [name.strip() for name in lines[header_rows[-1]].lstrip("# ").split(",")]
    # Station downloads put an uncommented legend above the header; data follow it.
    body = "\n".join(
        line for line in lines[header_rows[-1] + 1:] if line.strip() and not line.lstrip().startswith("#")
    )
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
    if "FF" in frame:
        data["ff_ms"] = frame["FF"].replace(-9999, np.nan).to_numpy() / 10
    return data


def station_weights(stations) -> dict[int, float] | None:
    """stations: list (equal weights), mapping station -> weight, or 'nedu' for the NEDU TAC weights."""
    if not stations:
        return None
    if stations == "nedu":
        return dict(NEDU_STATION_WEIGHTS)
    if isinstance(stations, dict):
        return {int(k): float(v) for k, v in stations.items()}
    return {int(s): 1.0 for s in stations}


def read_weather(settings: dict, base: Path) -> pd.DataFrame:
    """Hourly weather averaged over stations with weights, plus NEDU TAC (per station, then weighted)."""
    paths = settings["path"]
    if isinstance(paths, str):
        paths = [paths]
    weights = station_weights(settings.get("stations"))
    selected = list(weights) if weights else None
    parts = []
    for path in paths:
        if settings.get("format", "csv") == "knmi":
            part = read_knmi(base / path, selected)
        elif settings.get("format", "csv") == "csv":
            frame = pd.read_csv(base / path)
            part = frame.set_index(utc_index(frame["timestamp_utc"]))
            part = part.drop(columns="timestamp_utc")
            if "station" in part and selected:
                part = part.loc[part["station"].isin(selected)]
        else:
            raise ValueError("Weather format must be csv or knmi.")
        parts.append(part)
    combined = pd.concat(parts)
    if "station" in combined:
        if combined.reset_index(names="timestamp_utc").duplicated(["timestamp_utc", "station"]).any():
            raise ValueError("Duplicate station/hour weather observations.")
    elif combined.index.has_duplicates:
        raise ValueError("Weather duplicates require a station column for explicit averaging.")
    else:
        combined["station"] = 0
    columns = ["temp_c", "wind_ms", "ghi_wm2"]
    extra = ["ff_ms"] if "ff_ms" in combined else []
    combined[columns + extra] = combined[columns + extra].apply(pd.to_numeric, errors="raise")
    stations = sorted(combined["station"].unique())
    if weights and set(stations) - set(weights) - {0}:
        raise ValueError("Weather contains stations without a weight.")
    per_station = {}
    for station in stations:
        frame = combined.loc[combined["station"].eq(station), columns + extra].sort_index()
        frame = frame.reindex(pd.date_range(frame.index.min(), frame.index.max(), freq="h", name=frame.index.name))
        frame["tac"] = station_tac(frame)
        per_station[station] = frame
    stacked = pd.concat(per_station, names=["station", "timestamp_utc"])
    weight = stacked.index.get_level_values("station").map(lambda s: (weights or {}).get(s, 1.0))
    weight = pd.Series(np.asarray(weight, dtype=float), index=stacked.index)
    values = stacked[columns + ["tac"]]
    # Weighted mean over the stations present in each hour; missing stations renormalise the weights.
    present = values.notna().mul(weight, axis=0)
    weather = (values.fillna(0).mul(weight, axis=0).groupby(level="timestamp_utc").sum()
               / present.groupby(level="timestamp_utc").sum())
    weather = weather.where(present.groupby(level="timestamp_utc").sum() > 0).sort_index()
    # The pre-2016 TAC rule used De Bilt only.
    reference = per_station.get(260, per_station[stations[0]] if len(stations) == 1 else None)
    if reference is None:
        reference = weather
    weather["tac_old"] = old_tac(reference).reindex(weather.index)
    weather.index.name = None
    if weather.empty or not np.isfinite(weather.dropna().to_numpy()).all():
        raise ValueError("Weather is empty or contains infinite values.")
    return weather
