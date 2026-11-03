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

from .align import align_data, detect_resolution, sum_complete_days
from .features import build_features
from .ingest import read_demand, read_profile, read_weather
from .methods.m_a_reference_year import ReferenceYear
from .methods.m_b_regression import Regression, RegressionWithProfile
from .methods.m_c_gbm import GradientBoosting
from .methods.m_w_base import ExternalPlusBase
from .methods.m_w_external import ExternalProfile
from .metrics import evaluate
from .nedu import nedu_daily
from .report import write_report
from .rescale import to_annual_total

LOG = logging.getLogger(__name__)
ALL_METHODS = ["A1", "A*", "A*b", "A*b22", "B", "B+W", "C", "NEDU"]
EXTERNAL_METHODS = {"A*", "A*b", "A*b22", "B+W"}
FITTED_METHODS = {"A*b", "A*b22", "B", "B+W", "C"}
# Unfitted profiles may leave intervals uncovered; the driver drops those for every method.
PARTIAL_METHODS = EXTERNAL_METHODS | {"NEDU"}


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
    names = methods_override if methods_override is not None else list(
        carrier_settings.get("methods", settings.get("methods", ["A1", "B", "C"])))
    external_settings = carrier_settings.get("external_profile")
    if external_settings and methods_override is None and "A*" not in names:
        names.append("A*")
    if not external_settings and methods_override is None and set(names) & EXTERNAL_METHODS:
        LOG.warning("No external_profile for %s; skipping %s.", carrier, sorted(set(names) & EXTERNAL_METHODS))
        names = [n for n in names if n not in EXTERNAL_METHODS]
    if not names or len(set(names)) != len(names) or set(names) - set(ALL_METHODS):
        raise ValueError(f"Select unique methods from {', '.join(ALL_METHODS)}.")
    crisis_year = int(settings.get("crisis_year", 2022))
    external, external_years, covered, external_minimum = None, [], [], minimum
    if set(names) & EXTERNAL_METHODS:
        if not external_settings:
            raise ValueError(f"{sorted(set(names) & EXTERNAL_METHODS)} require carriers.<carrier>.external_profile.")
        if isinstance(external_settings, str):
            external = read_demand(
                {"path": external_settings}, base, daily_label=label, daily_start_hour=start_hour,
                timezone=timezone, years=years,
            )
        else:
            external = read_profile(external_settings, base)
            external_minimum = float(external_settings.get("minimum_coverage", minimum))
            if mode == "daily" and detect_resolution(external.index) == "hourly":
                external = sum_complete_days(external, timezone, start_hour)
        # The profile is a weather-driven input, never measured demand, so it may be used as a feature.
        features["wpg"] = external.reindex(features.index)
        covered = [
            year for year in eligible
            if features.loc[features["year"].eq(year), "wpg"].notna().mean() >= external_minimum
        ]
        external_years = [year for year in heldout if year in covered]
        if not external_years:
            raise ValueError(f"External profile covers none of the held-out years {heldout}.")
        if set(names) <= EXTERNAL_METHODS:
            heldout = external_years
            coldest = int(temperatures.loc[heldout].idxmin())
    nedu, nedu_years = None, []
    if "NEDU" in names and methods_override is None and (not settings.get("nedu") or mode != "daily"):
        LOG.warning("NEDU needs a 'nedu' section and daily data; skipping it for %s.", carrier)
        names = [n for n in names if n != "NEDU"]
    if "NEDU" in names:
        if not settings.get("nedu"):
            raise ValueError("Method NEDU requires a top-level 'nedu' section.")
        if mode != "daily":
            raise ValueError("Method NEDU is implemented for daily data only.")
        nedu = nedu_daily(settings["nedu"], base, weather, timezone, start_hour)
        covered_nedu = nedu.reindex(features.index).notna()
        nedu_years = [y for y in heldout if covered_nedu[features["year"].eq(y)].mean() >= minimum]
        if not nedu_years:
            raise ValueError(f"NEDU profiles cover none of the held-out years {heldout}.")
        if names == ["NEDU"]:
            heldout = nedu_years
    records, predictions, support = [], {}, {}
    for year in heldout:
        # Fitted profile methods also need the profile in at least one training year.
        year_names = [
            n for n in names if (n not in EXTERNAL_METHODS
            or (year in external_years and (n == "A*" or set(covered) - {year})))
            and (n != "NEDU" or year in nedu_years)
        ]
        LOG.info("Reconstructing %s %d (%s; methods %s)", carrier, year, mode, ", ".join(year_names))
        train = features.loc[features["year"].ne(year)]
        test = features.loc[features["year"].eq(year)]
        inputs = test.drop(columns="value")
        measured = test["value"]
        raw, method_metadata = {}, {}
        for name in year_names:
            if name == "A1":
                method = ReferenceYear(timezone)
            elif name == "B":
                method = Regression()
            elif name == "B+W":
                method = RegressionWithProfile()
            elif name == "C":
                method = GradientBoosting(settings.get("boosting"), timezone)
            elif name == "A*b":
                method = ExternalPlusBase()
            elif name == "A*b22":
                method = ExternalPlusBase(crisis_year)
            elif name == "NEDU":
                method = ExternalProfile(nedu, "NEDU")
            else:
                method = ExternalProfile(external)
            method.fit(train)
            pred = method.predict(inputs, float(measured.sum()))
            if not pred.index.equals(inputs.index):
                raise ValueError(f"{name} returned mismatched prediction timestamps.")
            if name not in PARTIAL_METHODS and pred.isna().any():
                raise ValueError(f"{name} must predict every interval.")
            present = pred.dropna()
            if not np.isfinite(present).all() or (present < 0).any():
                raise ValueError(f"{name} must return finite non-negative predictions.")
            raw[name] = pred
            method_metadata[name] = getattr(method, "metadata", {})
            if name == "A1":
                method_metadata[name] = {"reference_year": method.reference_year}
        # Every method is scored on the same intervals: measured and, in A* years, covered by the profile.
        valid = measured.notna()
        for pred in raw.values():
            valid &= pred.notna()
        support[str(year)] = {
            "measured_intervals": int(measured.notna().sum()),
            "common_support_intervals": int(valid.sum()),
            "dropped_intervals": [t.isoformat() for t in measured.index[measured.notna() & ~valid]],
        }
        observed = measured.where(valid)
        annual_total = float(observed.sum())
        frame = pd.DataFrame({"measured": observed})
        for name in year_names:
            supported = raw[name].where(valid)
            rebuilt = to_annual_total(supported, annual_total)
            if not np.isclose(rebuilt.sum(), annual_total, rtol=1e-10):
                raise AssertionError("Annual-total rescaling failed.")
            level = None
            if name in FITTED_METHODS:
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
        "coldest_year": coldest, "external_profile_years": external_years, "nedu_years": nedu_years,
        "crisis_year": crisis_year, "common_support": support,
        "heating_season_mean_temp_c": {str(y): float(v) for y, v in temperatures.items()},
        "heating_season_definition": "Jan-Mar and Oct-Dec within each year",
        "annual_total_definition": "sum of measurements on the common support (measured and, in A* years, covered by the external profile); identical for every method",
        "relative_error_definition": "(predicted - measured) / measured; null if measured is zero",
        "worst_case_definition": "signed error with largest absolute magnitude; nRMSE uses maximum",
        "summary_percentages": True, "metrics_json_relative_errors_are_fractions": True,
        "source": {"demand": carrier_settings["demand"], "weather": settings["weather"], "external_profile": external_settings,
                   "nedu": settings.get("nedu") if nedu is not None else None},
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
    parser.add_argument("--methods", nargs="+", choices=ALL_METHODS, help="Override configured methods.")
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
