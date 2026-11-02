import numpy as np
import pandas as pd
import pytest

from src.metrics import evaluate, peak_window
from src.report import aggregate


@pytest.mark.parametrize(("mode", "count", "size"), [("hourly", 24 * 30, 168), ("daily", 30, 7)])
def test_known_peak_and_top_errors(mode, count, size):
    index = pd.date_range("2020-01-01", periods=count, freq="h" if mode == "hourly" else "D", tz="UTC")
    measured = pd.Series(10.0, index=index)
    measured.iloc[-size:] = 20
    predicted = measured * 1.1
    result = evaluate(measured, predicted, mode)
    assert result["peak_week"]["measured_start"] == index[-size].isoformat()
    assert result["peak_week"]["relative_error"] == pytest.approx(0.1)
    assert result["peak_week"]["date_shift_days"] == 0
    assert result["top_1pct_measured"]["relative_error"] == pytest.approx(0.1)
    assert result["top_1pct_predicted"]["count"] == (88 if mode == "hourly" else 4)
    assert result["annual_mean"]["relative_error"] == pytest.approx(0.1)
    assert result["nrmse"] == pytest.approx(np.sqrt(np.mean((predicted - measured) ** 2)) / measured.mean())


def test_peak_timing_and_predicted_peak_selection():
    index = pd.date_range("2020-01-01", periods=28, freq="D", tz="UTC")
    measured = pd.Series([20.0] * 7 + [10.0] * 21, index=index)
    predicted = pd.Series([10.0] * 14 + [20.0] * 7 + [10.0] * 7, index=index)
    result = evaluate(measured, predicted, "daily")
    assert result["peak_week"]["date_shift_days"] == 14
    assert result["top_1pct_measured"]["error"] == -10
    assert result["top_1pct_predicted"]["error"] == 10


def test_missing_does_not_compress_peak_window():
    index = pd.date_range("2020-01-01", periods=15, freq="D", tz="UTC")
    series = pd.Series(10.0, index=index)
    series.iloc[4] = np.nan
    assert peak_window(series, "daily")[0] == index[5]
    result = evaluate(series, series.copy(), "daily")
    assert result["valid_intervals"] == 14
    assert result["nrmse"] == 0
    with pytest.raises(ValueError, match="support"):
        evaluate(series, series.fillna(10), "daily")


def test_cold_spells_inclusive_local_dates_and_both_peaks():
    index = pd.date_range("2021-02-01", periods=28, freq="D", tz="UTC")
    measured = pd.Series(10.0, index=index)
    predicted = measured.copy()
    measured.iloc[6] = 20
    predicted.iloc[7] = 30
    result = evaluate(measured, predicted, "daily", [{"start": "2021-02-07", "end": "2021-02-08"}])
    spell = result["cold_spells"][0]
    assert spell["valid_intervals"] == 2
    assert spell["mean"]["error"] == 5
    assert spell["peak"]["error"] == 10
    assert spell["at_measured_peak"]["error"] == -10


def test_aggregation_worst_by_magnitude_and_zero_denominator():
    index = pd.date_range("2020-01-01", periods=14, freq="D", tz="UTC")
    measured = pd.Series(10.0, index=index)
    records = [
        {"year": year, "method": "A1", "metrics": evaluate(measured, measured * ratio, "daily")}
        for year, ratio in [(2020, 0.5), (2021, 1.1)]
    ]
    summary, _, detailed = aggregate(records, 2021)
    assert detailed["A1"]["peak_week_relative_error"]["worst"] == -0.5
    assert detailed["A1"]["peak_week_relative_error"]["mean"] == pytest.approx(-0.2)
    assert detailed["A1"]["peak_week_relative_error"]["coldest_year"] == pytest.approx(0.1)
    assert pd.isna(summary.iloc[0]["cold_spell_mean_error_mean"])
