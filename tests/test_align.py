import numpy as np
import pandas as pd
import pytest

from src.align import aggregate_days, align_data, daily_grid, detect_resolution, fill_short_gaps, hourly_means
from src.features import build_features


def test_fill_whole_gaps_not_partial_or_edges():
    index = pd.date_range("2020-01-01", periods=16, freq="h", tz="UTC")
    series = pd.Series([np.nan, 1, np.nan, np.nan, np.nan, 5, np.nan, np.nan, np.nan, np.nan, 10, 11, 12, 13, 14, np.nan], index=index)
    result = fill_short_gaps(series)
    assert result.iloc[2:5].tolist() == [2, 3, 4]
    assert result.iloc[6:10].isna().all()
    assert pd.isna(result.iloc[0]) and pd.isna(result.iloc[-1])


def test_subhourly_average_requires_complete_intervals():
    index = pd.date_range("2020-01-01", periods=12, freq="15min", tz="UTC")
    demand = pd.Series(np.arange(12.0), index=index).drop(index[5])
    means = hourly_means(demand)
    assert means.iloc[0] == 1.5
    assert pd.isna(means.iloc[1])
    assert means.iloc[2] == 9.5
    with pytest.raises(ValueError, match="boundaries"):
        hourly_means(pd.Series(10.0, index=pd.date_range("2020-01-01 00:05", periods=12, freq="h", tz="UTC")))


@pytest.mark.parametrize(("start", "end", "hours"), [
    ("2021-03-27 06:00", "2021-03-28 06:00", 23),
    ("2021-10-30 06:00", "2021-10-31 06:00", 25),
])
def test_gas_window_dst(start, end, hours):
    a = pd.Timestamp(start, tz="Europe/Amsterdam").tz_convert("UTC")
    b = pd.Timestamp(end, tz="Europe/Amsterdam").tz_convert("UTC")
    index = pd.date_range(a, b, inclusive="left", freq="h")
    assert len(index) == hours
    series = pd.Series(np.arange(hours), index=index)
    daily = aggregate_days(series, "Europe/Amsterdam", 6)
    assert daily.index.tolist() == [a]
    assert daily.iloc[0] == pytest.approx((hours - 1) / 2)


@pytest.mark.parametrize(("start", "periods", "expected"), [
    ("2021-03-27 23:00Z", 23, list(range(2)) + list(range(3, 24))),
    ("2021-10-30 22:00Z", 25, [0, 1, 2, 2] + list(range(3, 24))),
])
def test_local_calendar_dst(start, periods, expected):
    index = pd.date_range(start, periods=periods, freq="h")
    frame = pd.DataFrame({"year": 2021, "temp_c": 10, "wind_ms": 3, "ghi_wm2": 0}, index=index)
    result = build_features(frame, "hourly", "Europe/Amsterdam")
    assert result["hour"].tolist() == expected


def test_resolution_leap_and_daily_dst():
    grid = daily_grid(2020, "Europe/Amsterdam", 6)
    assert len(grid) == 366
    assert detect_resolution(grid) == "daily"
    with pytest.raises(ValueError, match="upsample"):
        detect_resolution(grid, "hourly")


def test_coverage_before_interpolation():
    index = pd.date_range("2020-01-01", "2021-01-01", inclusive="left", freq="h", tz="UTC")
    demand = pd.Series(100.0, index=index).drop(index[20:23])
    weather = pd.DataFrame({"temp_c": 10.0, "wind_ms": 2.0, "ghi_wm2": 0.0}, index=index)
    aligned, report, mode = align_data(demand, weather, [2020])
    assert mode == "hourly"
    assert report[0]["expected_intervals"] == 8784
    assert report[0]["observed_intervals"] == 8781
    assert report[0]["filled_intervals"] == 3
    assert aligned["value"].notna().all()


def test_daily_weather_exact_window_and_no_demand_interpolation():
    grid = daily_grid(2021, "Europe/Amsterdam", 6)
    end = pd.Timestamp("2022-01-01 06:00", tz="Europe/Amsterdam").tz_convert("UTC")
    hours = pd.date_range(grid[0], end, inclusive="left", freq="h")
    weather = pd.DataFrame({"temp_c": np.arange(len(hours)), "wind_ms": 1.0, "ghi_wm2": 0.0}, index=hours)
    demand = pd.Series(100.0, index=grid).drop(grid[10])
    result, report, mode = align_data(demand, weather, [2021], daily_start_hour=6)
    assert mode == "daily"
    assert result.iloc[0]["temp_c"] == pytest.approx(11.5)
    assert pd.isna(result.iloc[10]["value"])
    assert report[0]["filled_intervals"] == 0
    start = pd.Timestamp("2021-03-27 06:00", tz="Europe/Amsterdam").tz_convert("UTC")
    assert result.loc[start, "temp_c"] == weather.loc[start:start + pd.Timedelta(hours=22), "temp_c"].mean()


def test_hourly_to_daily_override_preserves_window_completeness():
    grid = daily_grid(2021, "Europe/Amsterdam", 6)
    end = pd.Timestamp("2022-01-01 06:00", tz="Europe/Amsterdam").tz_convert("UTC")
    hours = pd.date_range(grid[0], end, inclusive="left", freq="h")
    weather = pd.DataFrame({"temp_c": 10.0, "wind_ms": 1.0, "ghi_wm2": 0.0}, index=hours)
    demand = pd.Series(100.0, index=hours).drop(hours[30])
    aligned, reports, mode = align_data(demand, weather, [2021], resolution="daily", daily_start_hour=6)
    assert mode == "daily"
    assert pd.isna(aligned.iloc[1]["value"])
    assert aligned.iloc[0]["value"] == 100
    assert reports[0]["observed_intervals"] == 364
    assert reports[0]["filled_intervals"] == 0


def test_rolling_weather_does_not_cross_year_or_use_demand():
    index = pd.date_range("2020-12-31 23:00Z", periods=3, freq="h")
    frame = pd.DataFrame({"year": [2020, 2021, 2021], "temp_c": [-100, 10, 12], "wind_ms": 2, "ghi_wm2": 0, "value": [1, 2, 3]}, index=index)
    features = build_features(frame, "hourly", "Europe/Amsterdam")
    assert features.iloc[1]["heating_72h"] == 5.5
    changed = build_features(frame.assign(value=99999), "hourly", "Europe/Amsterdam")
    pd.testing.assert_frame_equal(features.drop(columns="value"), changed.drop(columns="value"))
