import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingRegressor

from src.features import build_features
from src.methods.m_a_reference_year import ReferenceYear
from src.methods.m_c_gbm import GradientBoosting, split_days
from src.methods.m_w_external import ExternalProfile


def test_day_validation_has_no_shared_local_day():
    index = pd.date_range("2021-03-01", periods=24 * 60, freq="h", tz="UTC")
    mask = split_days(index, "Europe/Amsterdam", 42)
    local_dates = index.tz_convert("Europe/Amsterdam").date
    assert not set(local_dates[mask]) & set(local_dates[~mask])
    np.testing.assert_array_equal(mask, split_days(index, "Europe/Amsterdam", 42))


def test_reference_most_recent_weekday_and_leap_mapping():
    training_index = pd.date_range("2019-01-01", "2020-01-01", freq="h", inclusive="left", tz="UTC")
    local = training_index.tz_convert("Europe/Amsterdam")
    train = pd.DataFrame({
        "year": 2019, "value": local.day_of_week * 100 + local.hour, "weekday": local.day_of_week, "hour": local.hour,
    }, index=training_index)
    method = ReferenceYear()
    method.fit(train)
    target = pd.date_range("2020-02-28", periods=72, freq="h", tz="UTC")
    local_target = target.tz_convert("Europe/Amsterdam")
    inputs = pd.DataFrame({"weekday": local_target.day_of_week, "hour": local_target.hour}, index=target)
    result = method.predict(inputs, 1000)
    np.testing.assert_array_equal(result.to_numpy(), local_target.day_of_week * 100 + local_target.hour)
    assert method.reference_year == 2019


def test_sklearn_boosting_day_split_and_prediction(monkeypatch):
    index = pd.date_range("2020-01-01", periods=24 * 40, freq="h", tz="UTC")
    temp = np.linspace(-5, 15, len(index))
    frame = pd.DataFrame({"year": 2020, "temp_c": temp, "wind_ms": 3, "ghi_wm2": 0, "value": 500 + np.maximum(0, 15.5 - temp) * 50}, index=index)
    train = build_features(frame, "hourly", "Europe/Amsterdam")
    original_fit = HistGradientBoostingRegressor.fit
    observed_splits = []

    def fit(self, x, y, **kwargs):
        x_val = kwargs["X_val"]
        training_days = set(x.index.tz_convert("Europe/Amsterdam").date)
        validation_days = set(x_val.index.tz_convert("Europe/Amsterdam").date)
        assert not training_days & validation_days
        assert "value" not in x and "value" not in x_val
        observed_splits.append((len(x), len(x_val)))
        return original_fit(self, x, y, **kwargs)

    monkeypatch.setattr(HistGradientBoostingRegressor, "fit", fit)
    method = GradientBoosting({"backend": "sklearn", "max_iter": 20, "threads": 2})
    method.fit(train)
    result = method.predict(train.drop(columns="value"), 10000)
    assert result.index.equals(train.index)
    assert result.notna().all()
    assert method.metadata["backend"] == "sklearn"
    assert method.metadata["validation_rows"] > 0
    assert len(observed_splits) == 1
    assert 1 <= method.metadata["best_iteration"] <= 20


def test_external_profile_leaves_uncovered_intervals_missing():
    index = pd.date_range("2020-01-01", periods=48, freq="h", tz="UTC")
    method = ExternalProfile(pd.Series(10.0, index=index[:-1]))
    result = method.predict(pd.DataFrame(index=index), 1000)
    assert result.index.equals(index)
    assert result.isna().sum() == 1
