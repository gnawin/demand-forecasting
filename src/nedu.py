"""NEDU standard gas profiles: TAC (profile temperature), profile files and the daily reference."""

from __future__ import annotations

import glob
import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .align import sum_complete_days

# Protocol 2.2, Bijlage 1, Tabel 3.
NEDU_STATION_WEIGHTS = {260: 0.28, 280: 0.14, 380: 0.15, 235: 0.15, 310: 0.12, 290: 0.16}
CET = pd.Timedelta(hours=1)  # NEDU uses continuous CET (UTC+1) without DST


def _cet_days(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return (index.tz_convert("UTC").tz_localize(None) + CET).normalize()


def _previous_days(daily: pd.Series, days: int) -> pd.Series:
    shifted = daily.shift(days, freq="D").reindex(daily.index)
    # Only the first days of a record lack history; reuse the nearest available day there.
    return shifted.bfill()


def station_tac(frame: pd.DataFrame) -> pd.Series:
    """Hourly TAC for one station (NEDU definition since 2016).

    frame: hourly UTC hour-beginning index with temp_c, wind_ms (hourly mean, FH),
    ghi_wm2 and optionally ff_ms (last 10 minutes, FF; NEDU's hourly wind).
    TAC = 0.6(t1+w1) + 0.3(t2+w2) + 0.1(t3+w3) + q1, w = -sqrt(wind)/0.35, q1 = Q[J/cm2]/40.
    Daily values are 00-24 CET means.
    """
    hourly_wind = frame["ff_ms"] if "ff_ms" in frame else frame["wind_ms"]
    days = _cet_days(frame.index)
    daily_t = frame["temp_c"].groupby(days).mean()
    daily_w = -np.sqrt(frame["wind_ms"].clip(lower=0).groupby(days).mean()) / 0.35
    lag1 = (_previous_days(daily_t, 1) + _previous_days(daily_w, 1)).reindex(days).to_numpy()
    lag2 = (_previous_days(daily_t, 2) + _previous_days(daily_w, 2)).reindex(days).to_numpy()
    w1 = -np.sqrt(hourly_wind.clip(lower=0)) / 0.35
    q1 = frame["ghi_wm2"] * 3600 / 10000 / 40
    return (0.6 * (frame["temp_c"] + w1) + 0.3 * lag1 + 0.1 * lag2 + q1).rename("tac")


def old_tac(frame: pd.DataFrame) -> pd.Series:
    """Hourly TAC by the pre-2016 rule: De Bilt daily (00-24 CET) T - W/1.5, applied to the gas day starting 06:00 CET."""
    days = _cet_days(frame.index)
    daily = frame["temp_c"].groupby(days).mean() - frame["wind_ms"].groupby(days).mean() / 1.5
    gas_days = _cet_days(frame.index - pd.Timedelta(hours=6))
    values = daily.reindex(gas_days)
    values = pd.Series(values.to_numpy(), index=frame.index).bfill()
    return values.rename("tac_old")


def _data_rows(lines: list[str], separator: str) -> tuple[list[str], list[list[str]]]:
    code = next(line for line in lines if separator + "Code" + separator in line)
    columns = [c.strip() for c in code.split(separator)]
    pattern = re.compile(r"^\s*(\d{2}-\d{2}-\d{4}|\d{4}-\d{2}-\d{2}) \d{1,2}:\d{2}")
    rows = [line.split(separator) for line in lines if pattern.match(line)]
    return columns, rows


def parse_profile_file(text: str) -> pd.DataFrame:
    """One NEDU profile csv/txt. Returns hourly parameters indexed by UTC hour beginning."""
    lines = text.splitlines()
    separator = ";" if any(";Code;" in line for line in lines[:10]) else ","
    columns, rows = _data_rows(lines, separator)
    keep = [i for i, c in enumerate(columns) if re.fullmatch(r"G\w{2}_(TST|RER|TOP)", c)]
    stamps = pd.Series([r[0].strip() for r in rows])
    iso = stamps.str.match(r"^\d{4}-").all()
    # The first column is the hour end in continuous CET; it never repeats at DST changes.
    ends = pd.to_datetime(stamps, format="%Y-%m-%d %H:%M" if iso else "%d-%m-%Y %H:%M")
    index = pd.DatetimeIndex(ends - pd.Timedelta(hours=1) - CET).tz_localize("UTC")
    values = {columns[i]: pd.to_numeric([r[i].strip() for r in rows], errors="raise") for i in keep}
    frame = pd.DataFrame(values, index=index)
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("NEDU profile hours must be unique and increasing.")
    return frame


def read_nedu(paths: list[str] | str, base: Path) -> pd.DataFrame:
    """All profile and GXX files in the given NEDU zips (globs allowed); SPT and documents are ignored."""
    paths = [paths] if isinstance(paths, str) else paths
    files = sorted({f for p in paths for f in glob.glob(str(base / p))})
    if not files:
        raise ValueError(f"No NEDU zips match {paths}")
    parts: dict[str, list[pd.DataFrame]] = {}
    for path in files:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                stem = Path(name).name.lower()
                if stem.endswith((".csv", ".txt")) and stem.startswith(("profielen", "gxx")):
                    frame = parse_profile_file(archive.read(name).decode("latin-1"))
                    kind = "GXX" if stem.startswith("gxx") else "G"
                    parts.setdefault(kind, []).append(frame)
    joined = [pd.concat(frames).sort_index() for frames in parts.values()]
    result = pd.concat(joined, axis=1)
    if result.index.has_duplicates:
        raise ValueError("Overlapping NEDU profile years.")
    return result


def profile_fractions(params: pd.DataFrame, tac: pd.Series, category: str) -> pd.Series:
    tst, rer, top = (params[f"{category}_{p}"] for p in ("TST", "RER", "TOP"))
    tac = tac.reindex(params.index)
    return np.maximum((tst - tac) * rer + top, top).rename(category)


def nedu_daily(settings: dict, base: Path, weather: pd.DataFrame, timezone: str, start_hour: int) -> pd.Series:
    """Weighted sum of NEDU profile fractions per gas day. Only relative weights matter after rescaling."""
    params = read_nedu(settings["path"], base)
    weights = settings.get("weights", {"G1A": 0.75, "G2A": 0.25})
    switch = int(settings.get("new_tac_from", 2016))
    # Profile year runs 06:00 CET to 06:00 CET; profiles before the switch used the old temperature rule.
    years = (params.index.tz_localize(None) + CET - pd.Timedelta(hours=6)).year
    tac = weather["tac"].reindex(params.index).where(years >= switch, weather["tac_old"].reindex(params.index))
    total = None
    for category, weight in weights.items():
        if f"{category}_TST" not in params:
            raise ValueError(f"NEDU category {category} not found; available {sorted({c[:3] for c in params})}")
        part = float(weight) * profile_fractions(params, tac, category)
        # A category missing in a year (e.g. GXX before 2020) leaves that year uncovered.
        total = part if total is None else total + part
    return sum_complete_days(total, timezone, start_hour).rename("NEDU")
