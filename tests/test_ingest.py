import pandas as pd
import pytest

from src.ingest import read_demand, read_knmi, read_weather, utc_index


def test_knmi_hour_ending_units_and_station_average(tmp_path):
    path = tmp_path / "weather.txt"
    path.write_text(
        "# metadata\n# STN,YYYYMMDD,HH,T,FH,Q\n"
        "260,20200101,1,100,30,36\n260,20200101,24,120,40,0\n"
        "240,20200101,1,140,50,72\n240,20200101,24,160,60,0\n",
    )
    one = read_knmi(path, [260])
    assert one.index[0] == pd.Timestamp("2020-01-01 00:00Z")
    assert one.index[-1] == pd.Timestamp("2020-01-01 23:00Z")
    assert one.iloc[0]["temp_c"] == 10
    assert one.iloc[0]["wind_ms"] == 3
    assert one.iloc[0]["ghi_wm2"] == 100
    average = read_weather({"path": "weather.txt", "format": "knmi", "stations": [260, 240]}, tmp_path)
    assert average.iloc[0]["temp_c"] == 12
    assert average.iloc[0]["ghi_wm2"] == 150


def test_utc_offsets_and_naive_dst():
    result = utc_index(pd.Series(["2021-10-31T02:00:00+02:00", "2021-10-31T02:00:00+01:00"]))
    assert result.tolist() == [pd.Timestamp("2021-10-31 00:00Z"), pd.Timestamp("2021-10-31 01:00Z")]
    result = utc_index(pd.Series(["2021-10-31 01:00", "2021-10-31 02:00", "2021-10-31 02:00", "2021-10-31 03:00"]), "Europe/Amsterdam")
    assert result.is_unique
    with pytest.raises(ValueError, match="timezone"):
        utc_index(pd.Series(["2021-01-01", "2021-01-02"]))


def test_date_label_gas_and_hourly_heat(tmp_path):
    path = tmp_path / "gas.csv"
    path.write_text("timestamp_utc,value\n2021-03-27,10\n2021-03-28,20\n")
    settings = {"path": "gas.csv"}
    kwargs = {"base": tmp_path, "daily_label": "date", "daily_start_hour": 6, "timezone": "Europe/Amsterdam", "years": [2021]}
    series = read_demand(settings, **kwargs)
    assert series.index.tolist() == [pd.Timestamp("2021-03-27 05:00Z"), pd.Timestamp("2021-03-28 04:00Z")]
    path.write_text("timestamp_utc,value\n2021-01-01T00:00Z,10\n2021-01-01T01:00Z,20\n")
    series = read_demand(settings, **kwargs)
    assert series.index[0] == pd.Timestamp("2021-01-01 00:00Z")


def test_duplicate_demand_is_not_silently_aggregated(tmp_path):
    (tmp_path / "d.csv").write_text("timestamp_utc,value\n2021-01-01T00:00Z,10\n2021-01-01T00:00Z,20\n")
    with pytest.raises(ValueError, match="Duplicate"):
        read_demand({"path": "d.csv"}, tmp_path, daily_label="timestamp", daily_start_hour=0, timezone="Europe/Amsterdam", years=[2021])
