"""One-command leave-one-year-out reconstruction and report generation."""

from __future__ import annotations

import argparse
import json
import logging
import platform
import time
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .align import align_data
from .features import build_features
from .ingest import read_demand, read_weather
from .methods.m_a_reference_year import ReferenceYear
from .methods.m_b_regression import Regression
from .methods.m_c_gbm import GradientBoosting
from .methods.m_w_external import ExternalProfile
from .metrics import evaluate
from .report import write_report
from .rescale import to_annual_total

LOG = logging.getLogger(__name__)


def run(config_path: Path, carrier: str, methods_override: list[str] | None = None) -> dict:
    started = time.perf_counter()
    config_path = config_path.resolve()
    settings = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    base = config_path.parent
    carrier_settings = settings["carriers"][carrier]
    timezone = settings.get("timezone", "Europe/Amsterdam")
    years = sorted(set(int(y) for y in settings["years"]))
    if not years:
        raise ValueError("config.years must not be empty.")
    start_hour = int(carrier_settings.get("daily_start_hour", 0))
    if not 0 <= start_hour <= 23:
        raise ValueError("daily_start_hour must be in 0..23.")
    label = carrier_settings.get("daily_label", "timestamp")
    demand = read_demand(carrier_settings["demand"], base, daily_label=label,
                         daily_start_hour=start_hour, timezone=timezone, years=years)
    weather = read_weather(settings["weather"], base)
    aligned, coverage, mode = align_data(
        demand, weather, years, resolution=settings.get("resolution", "auto"),
        timezone=timezone, daily_start_hour=start_hour,
    )
    minimum = float(settings.get("minimum_coverage", 0.9))
    if not 0.9 <= minimum <= 1:
        raise ValueError("minimum_coverage must be between 0.90 and 1.")
    eligible, excluded = [], []
    for item in coverage:
        item["included"] = item["coverage"] >= minimum
        item["reason"] = None if item["included"] else f"Original coverage below {minimum:.0%}"
        (eligible if item["included"] else excluded).append(item["year"])
    if len(eligible) < 2:
        raise ValueError(f"Need at least two eligible years; included={eligible}, excluded={excluded}.")
    if len(eligible) < 5:
        LOG.warning("Only %d eligible years; brief recommends at least five.", len(eligible))
    if excluded:
        LOG.warning("Excluded low-coverage years from training and evaluation: %s", excluded)
    selected = settings.get("held_out_years")
    heldout = sorted(set(int(y) for y in selected)) if selected is not None else eligible
    unknown = set(heldout) - set(years)
    if unknown:
        raise ValueError(f"Held-out years not in config.years: {sorted(unknown)}")
    heldout = [year for year in heldout if year in eligible]
    if not heldout:
        raise ValueError("No eligible held-out years.")
    data = aligned.loc[aligned["year"].isin(eligible)].copy()
    missing_weather = data[["temp_c", "wind_ms", "ghi_wm2"]].isna().any(axis=1)
    if missing_weather.any():
        first = data.index[missing_weather][0]
        raise ValueError(f"Weather missing for {missing_weather.sum()} eligible intervals, first {first}; supply complete weather windows.")
    processed = base / settings.get("processed_dir", "data/processed")
    processed.mkdir(parents=True, exist_ok=True)
    aligned.rename_axis("timestamp_utc").to_parquet(processed / f"{carrier}.parquet")
    features = build_features(data, mode, timezone)
    local_month = features.index.tz_convert(timezone).month
    heating_season = features.loc[np.isin(local_month, [1, 2, 3, 10, 11, 12])]
    temperatures = heating_season.groupby("year")["temp_c"].mean()
    coldest = int(temperatures.loc[heldout].idxmin())
    names = methods_override if methods_override is not None else list(settings.get("methods", ["A1", "B", "C"]))
    external_path = carrier_settings.get("external_profile")
    if external_path and methods_override is None and "A*" not in names:
        names.append("A*")
    if not names or len(set(names)) != len(names) or set(names) - {"A1", "A*", "B", "C"}:
        raise ValueError("Select unique methods from A1, A*, B, C.")
    external = None
    if "A*" in names:
        if not external_path:
            raise ValueError("A* requires carriers.<carrier>.external_profile.")
        external = read_demand(
            {"path": external_path}, base, daily_label=label, daily_start_hour=start_hour,
            timezone=timezone, years=years,
        )
    records, predictions = [], {}
    for year in heldout:
        LOG.info("Reconstructing %s %d (%s; methods %s)", carrier, year, mode, ", ".join(names))
        train = features.loc[features["year"].ne(year)]
        test = features.loc[features["year"].eq(year)]
        inputs = test.drop(columns="value")
        measured = test["value"]
        annual_total = float(measured.sum())
        raw, method_metadata = {}, {}
        for name in names:
            if name == "A1":
                method = ReferenceYear(timezone)
            elif name == "B":
                method = Regression()
            elif name == "C":
                method = GradientBoosting(settings.get("boosting"), timezone)
            else:
                method = ExternalProfile(external)
            method.fit(train)
            pred = method.predict(inputs, annual_total)
            if not pred.index.equals(inputs.index):
                raise ValueError(f"{name} returned mismatched prediction timestamps.")
            if pred.isna().any() or not np.isfinite(pred).all() or (pred < 0).any():
                raise ValueError(f"{name} must return finite non-negative predictions for every interval.")
            raw[name] = pred
            method_metadata[name] = getattr(method, "metadata", {})
            if name == "A1":
                method_metadata[name] = {"reference_year": method.reference_year}
        # All models predict every interval; only unavailable measurements restrict support.
        valid = measured.notna()
        observed = measured.where(valid)
        frame = pd.DataFrame({"measured": observed})
        for name in names:
            supported = raw[name].where(valid)
            rebuilt = to_annual_total(supported, annual_total)
            if not np.isclose(rebuilt.sum(), annual_total, rtol=1e-10):
                raise AssertionError("Annual-total rescaling failed.")
            level = None
            if name in {"B", "C"}:
                unscaled_total = float(supported.sum())
                level = {
                    "predicted_total": unscaled_total, "measured_total": annual_total,
                    "error": unscaled_total - annual_total,
                    "relative_error": (unscaled_total - annual_total) / annual_total,
                }
            metrics = evaluate(observed, rebuilt, mode, settings.get("cold_spells"), timezone)
            records.append({
                "year": year, "method": name, "annual_total": annual_total,
                "metrics": metrics, "unrescaled_level": level,
                "model": method_metadata[name],
            })
            frame[name] = rebuilt
        predictions[year] = frame
    output = base / settings.get("results_dir", "results") / carrier
    metadata = {
        "carrier": carrier, "resolution": mode, "units": carrier_settings["units"],
        "data_kind": settings.get("data_kind", "user-supplied; provenance must be verified"),
        "timezone": timezone, "daily_start_hour": start_hour, "coverage": coverage,
        "training_eligible_years": eligible, "held_out_years": heldout, "excluded_years": excluded,
        "coldest_year": coldest,
        "heating_season_mean_temp_c": {str(y): float(v) for y, v in temperatures.items()},
        "heating_season_definition": "Jan-Mar and Oct-Dec within each year",
        "annual_total_definition": "sum of available measurements after short-gap interpolation; identical support for every method",
        "relative_error_definition": "(predicted - measured) / measured; null if measured is zero",
        "worst_case_definition": "signed error with largest absolute magnitude; nRMSE uses maximum",
        "summary_percentages": True, "metrics_json_relative_errors_are_fractions": True,
        "source": {"demand": carrier_settings["demand"], "weather": settings["weather"], "external_profile": external_path},
        "boosting_settings": settings.get("boosting", {}),
        "versions": {"python": platform.python_version(), **{p: version(p) for p in ["pandas", "numpy", "scikit-learn", "holidays"]}},
        "compute_seconds_before_report": time.perf_counter() - started,
    }
    payload = write_report(output, records, metadata, predictions)
    elapsed = time.perf_counter() - started
    # Persist end-to-end timing, including tables and figures.
    payload["metadata"]["elapsed_seconds"] = elapsed
    (output / "metrics.json").write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    LOG.info("Wrote %s in %.1f s", output, elapsed)
    if elapsed >= 300:
        LOG.warning("Runtime target (<300 s) exceeded: %.1f s.", elapsed)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--carrier", nargs="+", required=True, help="One or more carrier names, or 'all'.")
    parser.add_argument("--methods", nargs="+", choices=["A1", "A*", "B", "C"], help="Override configured methods.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    carriers = args.carrier
    if carriers == ["all"]:
        config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
        carriers = list(config["carriers"])
    for carrier in carriers:
        run(args.config, carrier, args.methods)


if __name__ == "__main__":
    main()
