"""Paired-support shape metrics; signed errors are prediction minus measurement."""

from __future__ import annotations

import numpy as np
import pandas as pd


def error(predicted: float, measured: float) -> dict:
    absolute = float(predicted - measured)
    return {
        "measured": float(measured), "predicted": float(predicted),
        "error": absolute, "relative_error": absolute / measured if measured != 0 else None,
    }


def peak_window(series: pd.Series, mode: str) -> pd.DatetimeIndex:
    size = 168 if mode == "hourly" else 7
    rolling = series.rolling(size, min_periods=size).mean()
    if not rolling.notna().any():
        raise ValueError("No complete seven-day window on common valid support.")
    end = rolling.idxmax()
    position = series.index.get_loc(end)
    return series.index[position - size + 1:position + 1]


def evaluate(
    measured: pd.Series, predicted: pd.Series, mode: str,
    cold_spells: list[dict] | None = None, timezone: str = "Europe/Amsterdam",
) -> dict:
    if not measured.index.equals(predicted.index):
        raise ValueError("Metric timestamps must match exactly.")
    if not measured.notna().equals(predicted.notna()):
        raise ValueError("Metrics require identical non-NaN support.")
    valid = measured.notna()
    obs, pred = measured.loc[valid], predicted.loc[valid]
    if obs.empty or obs.mean() <= 0:
        raise ValueError("Metrics need observed positive-mean demand.")
    if not np.isfinite(obs).all() or not np.isfinite(pred).all():
        raise ValueError("Metrics require finite values on common support.")
    measured_week = peak_window(measured, mode)
    rebuilt_week = peak_window(predicted, mode)
    n_top = min(88 if mode == "hourly" else 4, len(obs))
    top_observed = obs.nlargest(n_top).index
    top_predicted = pred.nlargest(n_top).index
    rmse = float(np.sqrt(np.mean((pred - obs) ** 2)))
    output = {
        "peak_week": {
            **error(predicted.loc[measured_week].mean(), measured.loc[measured_week].mean()),
            "measured_start": measured_week[0].isoformat(),
            "predicted_start": rebuilt_week[0].isoformat(),
            "date_shift_days": float((rebuilt_week[0] - measured_week[0]) / pd.Timedelta(days=1)),
        },
        "top_1pct_measured": {**error(pred.loc[top_observed].mean(), obs.loc[top_observed].mean()), "count": n_top},
        "top_1pct_predicted": {**error(pred.loc[top_predicted].mean(), obs.loc[top_predicted].mean()), "count": n_top},
        "annual_mean": error(pred.mean(), obs.mean()),
        "rmse": rmse, "nrmse": rmse / float(obs.mean()), "valid_intervals": int(valid.sum()),
        "cold_spells": [],
    }
    local_dates = measured.index.tz_convert(timezone).tz_localize(None).normalize()
    for spell in cold_spells or []:
        start, end = pd.Timestamp(spell["start"]), pd.Timestamp(spell["end"])
        if end < start:
            raise ValueError("Cold-spell end precedes start.")
        mask = (local_dates >= start) & (local_dates <= end)
        if not mask.any():
            continue
        spell_obs = measured.loc[mask].dropna()
        if spell_obs.empty:
            output["cold_spells"].append({
                "name": spell.get("name", str(spell["start"])), "start": str(spell["start"]),
                "end": str(spell["end"]), "valid_intervals": 0, "mean": None, "peak": None,
            })
            continue
        spell_pred = predicted.reindex(spell_obs.index)
        measured_peak_time = spell_obs.idxmax()
        output["cold_spells"].append({
            "name": spell.get("name", str(spell["start"])), "start": str(spell["start"]),
            "end": str(spell["end"]), "valid_intervals": len(spell_obs),
            "mean": error(spell_pred.mean(), spell_obs.mean()),
            "peak": error(spell_pred.max(), spell_obs.max()),
            "at_measured_peak": error(spell_pred.loc[measured_peak_time], spell_obs.max()),
        })
    return output


def flatten(metrics: dict) -> dict[str, float | None]:
    result = {"rmse": metrics["rmse"], "nrmse": metrics["nrmse"]}
    for key in ("peak_week", "top_1pct_measured", "top_1pct_predicted", "annual_mean"):
        for field in ("error", "relative_error"):
            result[f"{key}_{field}"] = metrics[key][field]
    result["peak_week_date_shift_days"] = metrics["peak_week"]["date_shift_days"]
    for field in ("mean", "peak", "at_measured_peak"):
        for kind in ("error", "relative_error"):
            values = [s[field][kind] for s in metrics["cold_spells"] if s.get(field) and s[field][kind] is not None]
            result[f"cold_spell_{field}_{kind}"] = float(np.mean(values)) if values else None
    return result
