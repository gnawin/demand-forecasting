import zipfile

import numpy as np
import pandas as pd
import pytest
import yaml

from src.demo_data import generate
from src.ingest import read_knmi, read_weather
from src.nedu import nedu_daily, parse_profile_file, read_nedu, station_tac
from src.run_loyo import run


def profile_text(start: str, hours: int, separator: str = ",", iso: bool = False, gxx: bool = False) -> str:
    """NEDU-style file: three header rows, blank, clock rows, then 'end CET, van, tot, values' rows."""
    s = separator
    codes = ["GXX_TST", "GXX_RER", "GXX_TOP"] if gxx else [
        "G1A_TST", "G1A_RER", "G1A_TOP", "G2A_TST", "G2A_RER", "G2A_TOP", "LEEG", "LEEG", "LEEG",
        "G2C_TST", "G2C_RER", "G2C_TOP"]
    lines = [f"HASH{s}{s}Versienr{s}" + s.join(codes), f"{s}{s}Code{s}" + s.join(codes),
             f"{s}{s}Toepassingsjaar{s}" + s.join(["2019"] * len(codes)), "", f"CET{s}CEST", f"{s}van{s}tot"]
    fmt = "%Y-%m-%d %H:%M" if iso else "%d-%m-%Y %H:%M"
    for end in pd.date_range(start, periods=hours, freq="h"):
        values = ["15", "0.0001", "0.00002"] if gxx else ["15", "0.0001", "0.00002", "16", "0.0002", "0.00003",
                                                            "", "", "", "16", "0.0002", "0.00003"]
        lines.append(f"{end:{fmt}}{s}{end:{fmt}}{s}{end:{fmt}}{s}" + s.join(values))
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("separator,iso", [(",", False), (";", True)])
def test_parse_profile_formats_cet_hour_end(separator, iso):
    frame = parse_profile_file(profile_text("2019-01-01 07:00", 3, separator, iso))
    # 07:00 CET hour end = 06:00 CET start = 05:00 UTC.
    assert frame.index[0] == pd.Timestamp("2019-01-01 05:00Z")
    assert list(frame.columns) == ["G1A_TST", "G1A_RER", "G1A_TOP", "G2A_TST", "G2A_RER", "G2A_TOP",
                                   "G2C_TST", "G2C_RER", "G2C_TOP"]
    assert frame["G2A_RER"].iloc[0] == pytest.approx(0.0002)


def test_station_tac_formula():
    index = pd.date_range("2018-12-31 23:00", periods=4 * 24, freq="h", tz="UTC")  # CET days Jan 1-4
    frame = pd.DataFrame({"temp_c": 5.0, "wind_ms": 4.0, "ff_ms": 9.0, "ghi_wm2": 0.0}, index=index)
    frame.loc[frame.index >= "2019-01-02 23:00Z", "temp_c"] = 0.0  # Jan 3 and 4 (CET) colder
    tac = station_tac(frame)
    hour = pd.Timestamp("2019-01-03 23:00Z")  # 00:00 CET on Jan 4; lags are Jan 3 (0 C) and Jan 2 (5 C)
    w_hour, w_day = -np.sqrt(9) / 0.35, -np.sqrt(4) / 0.35
    expected = 0.6 * (0 + w_hour) + 0.3 * (0 + w_day) + 0.1 * (5 + w_day)
    assert tac.loc[hour] == pytest.approx(expected)


def test_knmi_zip_reads_inner_txt(tmp_path):
    text = ("# STN,YYYYMMDD,   HH,   FH,   FF,    T,    Q\n"
            "  290,20190101,    1,   40,   50,   55,   0\n  290,20190101,    2,   30,   30,   45,   0\n")
    with zipfile.ZipFile(tmp_path / "uurgeg_290_2011-2020.zip", "w") as archive:
        archive.writestr("uurgeg_290_2011-2020.txt", text)
    frame = read_knmi(tmp_path / "uurgeg_290_2011-2020.zip")
    assert frame["temp_c"].tolist() == [5.5, 4.5]
    assert frame["ff_ms"].tolist() == [5.0, 3.0]


def test_weighted_stations_renormalise(tmp_path):
    index = pd.date_range("2019-01-01", periods=48, freq="h", tz="UTC")
    rows = []
    for station, temp in ((260, 10.0), (280, 0.0)):
        for t in index:
            rows.append({"timestamp_utc": t.strftime("%Y-%m-%dT%H:%MZ"), "station": station,
                         "temp_c": temp, "wind_ms": 1.0, "ghi_wm2": 0.0})
    frame = pd.DataFrame(rows)
    frame.loc[(frame["station"] == 280) & (frame.index % 48 == 5), "temp_c"] = np.nan
    frame.to_csv(tmp_path / "w.csv", index=False)
    weather = read_weather({"path": "w.csv", "format": "csv", "stations": "nedu"}, tmp_path)
    assert weather["temp_c"].iloc[0] == pytest.approx(10 * 0.28 / (0.28 + 0.14))
    assert weather["temp_c"].iloc[5] == pytest.approx(10.0)  # Eelde missing: De Bilt only
    assert {"tac", "tac_old"} <= set(weather)


def test_nedu_reference_in_pipeline(tmp_path):
    path = generate(tmp_path, 2018, 2020)
    config = yaml.safe_load(path.read_text())
    config["methods"] = ["A1", "NEDU"]
    hours = int((pd.Timestamp("2021-01-01 07:00") - pd.Timestamp("2018-01-01 07:00")) / pd.Timedelta(hours=1)) + 1
    with zipfile.ZipFile(tmp_path / "Profielen-test.zip", "w") as archive:
        archive.writestr("profielen aardgas test.csv", profile_text("2018-01-01 07:00", hours))
        archive.writestr("GXX profiel aardgas test.csv", profile_text("2020-01-01 07:00", 24 * 366 + 1, gxx=True))
        archive.writestr("SPT test.csv", "ignored")
    config["nedu"] = {"path": "Profielen-*.zip", "weights": {"G1A": 0.75, "G2A": 0.25}}
    path.write_text(yaml.safe_dump(config))
    payload = run(path, "heat")
    assert payload["metadata"]["nedu_years"] == [2018, 2019, 2020]
    assert {r["year"] for r in payload["runs"] if r["method"] == "NEDU"} == {2018, 2019, 2020}
    frame = pd.read_parquet(tmp_path / "results" / "heat" / "predictions" / "2019.parquet")
    assert frame["NEDU"].sum() == pytest.approx(frame["measured"].sum(), rel=1e-12)
    # GXX only covers 2020, so a GXX weight limits NEDU to that year.
    params = read_nedu("Profielen-*.zip", tmp_path)
    assert params["GXX_TST"].first_valid_index() == pd.Timestamp("2020-01-01 05:00Z")
    weather = read_weather(config["weather"], tmp_path)
    daily = nedu_daily({"path": "Profielen-*.zip", "weights": {"G1A": 1, "GXX": 1}}, tmp_path, weather,
                       "Europe/Amsterdam", 6)
    assert set(daily.index.tz_convert("Europe/Amsterdam").year) == {2020}
