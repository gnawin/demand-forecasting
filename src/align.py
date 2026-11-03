"""Regular grids, coverage accounting and local-day weather aggregation."""

from __future__ import annotations

import logging

import pandas as pd

LOG = logging.getLogger(__name__)


def detect_resolution(index: pd.DatetimeIndex, override: str = "auto") -> str:
    gap = index.to_series().diff().median()
    if gap <= pd.Timedelta(hours=1):
        native = "hourly"
    elif pd.Timedelta(hours=23) <= gap <= pd.Timedelta(hours=25):
        native = "daily"
    else:
        raise ValueError(f"Unsupported median interval {gap}; expected hourly/subhourly or daily.")
    if override not in {"auto", "hourly", "daily"}:
        raise ValueError("resolution must be auto, hourly or daily.")
    if native == "daily" and override == "hourly":
        raise ValueError("Cannot upsample daily demand to hourly.")
    return native if override == "auto" else override


def fill_short_gaps(series: pd.Series, max_steps: int = 3) -> pd.Series:
    """Interpolate whole interior gaps only; never partially fill a long gap."""
    missing = series.isna()
    groups = missing.ne(missing.shift()).cumsum()
    sizes = missing.groupby(groups).transform("sum")
    fillable = missing & sizes.le(max_steps)
    interpolated = series.interpolate(method="time", limit_area="inside")
    return series.where(~fillable, interpolated)


def hourly_means(demand: pd.Series) -> pd.Series:
    cadence = demand.index.to_series().diff().median()
    intervals_per_hour = pd.Timedelta(hours=1) / cadence
    if intervals_per_hour < 1 or not float(intervals_per_hour).is_integer():
        raise ValueError("Subhourly cadence must divide one hour exactly.")
    if not (demand.index == demand.index.floor(cadence)).all():
        raise ValueError("Demand timestamps must align with their interval boundaries.")
    groups = demand.resample("h")
    return groups.mean().where(groups.count() == int(intervals_per_hour))


def daily_grid(year: int, timezone: str, start_hour: int) -> pd.DatetimeIndex:
    days = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="D", inclusive="left")
    return (days + pd.Timedelta(hours=start_hour)).tz_localize(timezone).tz_convert("UTC")


def day_labels(index: pd.DatetimeIndex, timezone: str, start_hour: int) -> pd.DatetimeIndex:
    # Subtract in wall-clock space, not UTC: DST gas days contain 23 or 25 hours.
    wall = index.tz_convert(timezone).tz_localize(None)
    return (wall - pd.Timedelta(hours=start_hour)).normalize()


def aggregate_days(series: pd.Series | pd.DataFrame, timezone: str, start_hour: int):
    labels = day_labels(series.index, timezone, start_hour)
    result = series.groupby(labels).mean()
    result.index = (result.index + pd.Timedelta(hours=start_hour)).tz_localize(timezone).tz_convert("UTC")
    return result


def sum_complete_days(series: pd.Series, timezone: str, start_hour: int) -> pd.Series:
    """Energy per local day window from (sub)hourly power; days missing any hour are dropped."""
    hourly = hourly_means(series)
    labels = day_labels(hourly.index, timezone, start_hour)
    counts = hourly.notna().groupby(labels).sum()
    totals = hourly.groupby(labels).sum(min_count=1)
    starts = (totals.index + pd.Timedelta(hours=start_hour)).tz_localize(timezone)
    ends = pd.DatetimeIndex([(t.tz_localize(None) + pd.DateOffset(days=1)).tz_localize(timezone) for t in starts])
    # DST days hold 23 or 25 hours, so sum energy instead of averaging power.
    expected = ((ends - starts) / pd.Timedelta(hours=1)).to_numpy()
    totals.index = starts.tz_convert("UTC")
    return totals.loc[counts.to_numpy() == expected]


def align_data(
    demand: pd.Series, weather: pd.DataFrame, years: list[int],
    *, resolution: str = "auto", timezone: str = "Europe/Amsterdam",
    daily_start_hour: int = 0,
) -> tuple[pd.DataFrame, list[dict], str]:
    mode = detect_resolution(demand.index, resolution)
    native = detect_resolution(demand.index)
    if native == "hourly":
        demand = hourly_means(demand)
    if not (weather.index == weather.index.floor("h")).all():
        raise ValueError("Weather timestamps must be hour-beginning UTC.")
    weather = weather.reindex(pd.date_range(weather.index.min(), weather.index.max(), freq="h"))
    weather = weather.apply(fill_short_gaps)
    if mode == "daily":
        if native == "hourly":
            # Missing source hours must not masquerade as fully observed daily values.
            counts = demand.notna().groupby(day_labels(demand.index, timezone, daily_start_hour)).sum()
            daily = aggregate_days(demand, timezone, daily_start_hour)
            ends = pd.DatetimeIndex([
                (t.tz_convert(timezone).tz_localize(None) + pd.DateOffset(days=1)).tz_localize(timezone)
                for t in daily.index
            ]).tz_convert("UTC")
            expected = (ends - daily.index) / pd.Timedelta(hours=1)
            demand = daily.where(counts.to_numpy() == expected)
        else:
            local = demand.index.tz_convert(timezone)
            if not ((local.hour == daily_start_hour) & (local.minute == 0)).all():
                raise ValueError("Daily timestamps do not match the local window; use daily_label: date.")
        weather_grid = pd.DatetimeIndex(sorted(set(t for year in years for t in daily_grid(year, timezone, daily_start_hour))))
        # Complete every local-day window before taking its mean.
        end = (weather_grid[-1].tz_convert(timezone).tz_localize(None) + pd.DateOffset(days=1)).tz_localize(timezone).tz_convert("UTC")
        required_hours = pd.date_range(weather_grid[0], end, freq="h", inclusive="left")
        hourly_weather = weather.reindex(required_hours)
        weather = aggregate_days(hourly_weather, timezone, daily_start_hour)
        complete = hourly_weather.notna().all(axis=1).groupby(day_labels(required_hours, timezone, daily_start_hour)).all()
        weather.loc[~complete.to_numpy(), :] = float("nan")
    elif (demand.index != demand.index.floor("h")).any():
        raise ValueError("Hourly demand timestamps must start on the hour.")

    frames, reports = [], []
    for year in years:
        index = (
            pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="h", tz="UTC", inclusive="left")
            if mode == "hourly" else daily_grid(year, timezone, daily_start_hour)
        )
        observed = demand.reindex(index)
        filled = fill_short_gaps(observed) if mode == "hourly" else observed
        reports.append({
            "year": year, "expected_intervals": len(index),
            "observed_intervals": int(observed.notna().sum()),
            "coverage": float(observed.notna().mean()),
            "missing_share": float(observed.isna().mean()),
            "filled_intervals": int((observed.isna() & filled.notna()).sum()),
            "remaining_missing": int(filled.isna().sum()),
        })
        frame = weather.reindex(index).copy()
        frame["value"] = filled
        frame["year"] = year
        frames.append(frame)
    return pd.concat(frames), reports, mode
