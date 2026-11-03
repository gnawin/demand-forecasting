"""Machine-readable metrics, detailed tables and compact slide summaries."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .metrics import flatten
from .plots import save_figures


def aggregate(records: list[dict], coldest: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    flat = [{"year": r["year"], "method": r["method"], **flatten(r["metrics"])} for r in records]
    yearly = pd.DataFrame(flat)
    rows, structured = [], {}
    metric_names = [name for name in yearly if name not in {"year", "method"}]
    for method, group in yearly.groupby("method", sort=False):
        row, entries = {"method": method}, {}
        for metric in metric_names:
            values = group[metric].dropna()
            cold = group.loc[group["year"].eq(coldest), metric]
            entry = {
                "mean": float(values.mean()) if len(values) else None,
                "mean_absolute": float(values.abs().mean()) if len(values) else None,
                "worst": float(values.loc[values.abs().idxmax()]) if len(values) else None,
                "coldest_year": float(cold.iloc[0]) if len(cold) and pd.notna(cold.iloc[0]) else None,
            }
            entries[metric] = entry
            for statistic, value in entry.items():
                row[f"{metric}_{statistic}"] = value
        rows.append(row)
        structured[method] = entries
    return pd.DataFrame(rows), yearly, structured


def compact_summary(summary: pd.DataFrame) -> pd.DataFrame:
    # Keep the slide table to five metric groups; full absolute/relative detail is separate.
    compact = {"method": summary["method"]}
    headline = {
        "peak_week_error_pct": "peak_week_relative_error",
        "top_1pct_error_pct": "top_1pct_measured_relative_error",
        "annual_mean_error_pct": "annual_mean_relative_error",
        "nrmse_pct": "nrmse",
        "cold_spell_mean_error_pct": "cold_spell_mean_relative_error",
    }
    for label, metric in headline.items():
        for statistic in ("mean", "worst", "coldest_year"):
            compact[f"{label}_{statistic}"] = 100 * pd.to_numeric(summary[f"{metric}_{statistic}"])
    return pd.DataFrame(compact)


def write_report(
    output: Path, records: list[dict], metadata: dict, predictions: dict[int, pd.DataFrame],
) -> dict:
    tables = output / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    summary, yearly, aggregated = aggregate(records, metadata["coldest_year"])
    yearly.to_csv(tables / "by_year.csv", index=False)
    summary.to_csv(tables / "summary_detailed.csv", index=False)
    compact_summary(summary).to_csv(tables / "summary.csv", index=False)
    external_years = metadata.get("external_profile_years") or []
    partial_external = bool(external_years) and set(external_years) != set(predictions)
    if partial_external:
        # Like-for-like: every method only in the years the external profile covers.
        subset = [r for r in records if r["year"] in external_years]
        temps = metadata["heating_season_mean_temp_c"]
        external_summary, _, _ = aggregate(subset, min(external_years, key=lambda y: temps[str(y)]))
        compact_summary(external_summary).to_csv(tables / "summary_external_years.csv", index=False)
    pd.DataFrame(metadata["coverage"]).to_csv(tables / "coverage.csv", index=False)
    cold_rows = [
        {"year": r["year"], "method": r["method"], **spell}
        for r in records for spell in r["metrics"]["cold_spells"]
    ]
    if cold_rows:
        pd.json_normalize(cold_rows).to_csv(tables / "cold_spells.csv", index=False)
    pd.DataFrame([
        {"year": r["year"], "method": r["method"], **r["unrescaled_level"]}
        for r in records if r.get("unrescaled_level")
    ], columns=["year", "method", "predicted_total", "measured_total", "error", "relative_error"]).to_csv(
        tables / "unrescaled_level.csv", index=False,
    )
    payload = {"metadata": metadata, "aggregate": aggregated, "runs": records}
    (output / "metrics.json").write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    prediction_dir = output / "predictions"
    prediction_dir.mkdir(exist_ok=True)
    for year, frame in predictions.items():
        frame.rename_axis("timestamp_utc").to_parquet(prediction_dir / f"{year}.parquet")
    save_figures(predictions, yearly, metadata["coldest_year"], output / "figures",
                 metadata["resolution"], metadata["units"], external_years)
    return payload
