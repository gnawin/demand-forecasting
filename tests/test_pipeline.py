import json

import numpy as np
import pandas as pd
import pytest
import yaml

from src.demo_data import generate
from src.methods.m_a_reference_year import ReferenceYear
from src.methods.m_b_regression import Regression
from src.methods.m_c_gbm import GradientBoosting
from src.methods.m_w_external import ExternalProfile
from src.run_loyo import run


@pytest.mark.parametrize("carrier", ["electricity", "heat"])
def test_complete_pipeline_outputs_support_and_no_leakage(tmp_path, monkeypatch, carrier):
    config_path = generate(tmp_path, 2018, 2020)
    config = yaml.safe_load(config_path.read_text())
    config["boosting"]["max_iter"] = 20
    config_path.write_text(yaml.safe_dump(config))
    for cls in (ReferenceYear, Regression, GradientBoosting, ExternalProfile):
        original_fit, original_predict = cls.fit, cls.predict

        def fit(self, train, original=original_fit):
            self.seen_years = set(train["year"])
            return original(self, train)

        def predict(self, features, annual_total, original=original_predict):
            assert "value" not in features
            assert not self.seen_years & set(features["year"])
            assert isinstance(annual_total, float)
            return original(self, features, annual_total)

        monkeypatch.setattr(cls, "fit", fit)
        monkeypatch.setattr(cls, "predict", predict)
    payload = run(config_path, carrier)
    assert payload["metadata"]["resolution"] == ("hourly" if carrier == "electricity" else "daily")
    assert payload["metadata"]["coldest_year"] == 2019
    assert payload["metadata"]["held_out_years"] == [2018, 2019, 2020]
    output = tmp_path / "results" / carrier
    persisted = json.loads((output / "metrics.json").read_text())
    assert persisted["metadata"]["elapsed_seconds"] > 0
    assert (tmp_path / "processed" / f"{carrier}.parquet").exists()
    summary = pd.read_csv(output / "tables" / "summary.csv")
    assert len(summary) == (4 if carrier == "electricity" else 3)
    assert len(summary.columns) == 16
    assert np.max(summary["annual_mean_error_pct_mean"].abs()) < 1e-10
    for name in ("fig1_peak_week.png", "fig2_metric_bars.png", "fig3_annual_scatter.png", "fig4_loyo.png"):
        assert (output / "figures" / name).stat().st_size > 10000
    for year in (2018, 2019, 2020):
        frame = pd.read_parquet(output / "predictions" / f"{year}.parquet")
        for column in frame:
            assert frame[column].notna().equals(frame["measured"].notna())
            assert frame[column].sum() == pytest.approx(frame["measured"].sum(), rel=1e-12)
    for record in payload["runs"]:
        assert abs(record["metrics"]["annual_mean"]["relative_error"]) < 1e-12
        if record["method"] in {"B", "C"}:
            assert record["unrescaled_level"] is not None


def test_low_coverage_excluded_even_when_requested_and_weather_error(tmp_path):
    path = generate(tmp_path, 2018, 2020)
    config = yaml.safe_load(path.read_text())
    config["methods"] = ["A1"]
    config["carriers"]["electricity"]["external_profile"] = None
    config["held_out_years"] = [2018, 2019, 2020]
    path.write_text(yaml.safe_dump(config))
    demand = pd.read_csv(tmp_path / "electricity.csv")
    missing = demand["timestamp_utc"].str.startswith("2020")
    demand.loc[demand.index[missing][:2000], "value"] = np.nan
    # A short gap must be filled, a long one excluded identically for all methods.
    demand.loc[demand.index[demand["timestamp_utc"].str.startswith("2019")][100:102], "value"] = np.nan
    demand.loc[demand.index[demand["timestamp_utc"].str.startswith("2019")][200:210], "value"] = np.nan
    demand.to_csv(tmp_path / "electricity.csv", index=False)
    payload = run(path, "electricity")
    assert payload["metadata"]["excluded_years"] == [2020]
    assert payload["metadata"]["held_out_years"] == [2018, 2019]
    coverage = {item["year"]: item for item in payload["metadata"]["coverage"]}
    assert coverage[2019]["filled_intervals"] == 2
    assert coverage[2019]["remaining_missing"] == 10
    for record in payload["runs"]:
        if record["method"] == "A1" and record["year"] == 2018:
            assert record["model"]["reference_year"] == 2019
    weather = pd.read_csv(tmp_path / "weather.csv")
    weather.loc[100:120, "temp_c"] = np.nan
    weather.to_csv(tmp_path / "weather.csv", index=False)
    with pytest.raises(ValueError, match="Weather missing"):
        run(path, "electricity")
