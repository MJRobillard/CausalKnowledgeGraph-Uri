"""Tier 0 series loaders. Every loader returns naive timestamps in Central time (CST, UTC-6 for all of
Feb 2021, T1). Label conventions are kept explicit:

- ERCOT minute data (MW out by fuel, load shed): labels are minute-ENDING. The file starts at 00:01 and ends at
  2/20 00:00 (8,640 rows = 6 days). `minute_start()` gives the start of each minute.
- ERCOT hourly estimated load without shed: hour-ending (first label 2/15 01:00); moved back 1 h to hour-start (T2).
- NOAA: UTC, hour-start labels (T4) -> Central by subtracting 6 h.
- EIA: already hour-start Central in the cleaned file (T5).
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd

from .common import SOURCES, documents

ERCOT_DATA = SOURCES / "ercot" / "data"
FUEL_COLS = {"NG MW OUT TOTAL": "gas", "COAL MW OUT TOTAL": "coal", "SOLAR MW OUT TOTAL": "solar",
             "WIND MW OUT TOTAL": "wind", "HYDRO MW OUT TOTAL": "hydro", "ESR MW OUT TOTAL": "storage",
             "NUCLEAR MW OUT TOTAL": "nuclear", "Total": "total"}
UTC_OFFSET = pd.Timedelta(hours=6)


def _path(doc_id: str):
    return documents()[doc_id]["path"]


@lru_cache
def mw_out_by_fuel() -> pd.DataFrame:
    """Minute MW out by fuel, index = minute-ending label (Central)."""
    d = documents()["ercot_mw_out_by_fuel"]
    g = pd.read_excel(d["path"], sheet_name=d["sheet"])
    g = g.loc[:, ~g.columns.astype(str).str.startswith("Unnamed")]
    g["DATE"] = pd.to_datetime(g["DATE"])
    g = g.set_index("DATE").rename(columns=FUEL_COLS)[list(FUEL_COLS.values())].astype(float)
    g.index.name = "t_end"
    return g


@lru_cache
def frequency() -> pd.Series:
    d = documents()["ercot_frequency"]
    f = pd.read_excel(d["path"], sheet_name=d["sheet"])
    s = f.set_index(pd.to_datetime(f["Date-Time"]))["TAYLOR_ERCOT_HZ_Frequency"].astype(float)
    s.index.name = "t"
    return s


@lru_cache
def _gen_load_shed() -> pd.DataFrame:
    return pd.read_excel(_path("ercot_gen_load_shed"))


def load_shed() -> pd.Series:
    """Minute load shed (MW), index = minute-ending label (Central)."""
    a = _gen_load_shed()[["DATE.2", "LOAD SHED MW"]].dropna()
    s = a.set_index(pd.to_datetime(a["DATE.2"]))["LOAD SHED MW"].astype(float)
    s.index.name = "t_end"
    return s


def available_generation() -> pd.Series:
    a = _gen_load_shed()[["DATE", "AVAILABLE GENERATION"]].dropna()
    s = a.set_index(pd.to_datetime(a["DATE"]))["AVAILABLE GENERATION"].astype(float)
    s.index.name = "t"
    return s


def estimated_load() -> pd.Series:
    """Hourly estimated load without shed, index = hour START (Central). Raw labels are hour-ending."""
    a = _gen_load_shed()[["DATE.1", "ESTIMATED LOAD WITHOUT LOAD SHED"]].dropna()
    s = a.set_index(pd.to_datetime(a["DATE.1"]) - pd.Timedelta(hours=1))["ESTIMATED LOAD WITHOUT LOAD SHED"]
    s.index.name = "t_start"
    return s.astype(float)


def minute_start(s: pd.Series | pd.DataFrame):
    """Shift a minute-ending series to minute-start labels."""
    s = s.copy()
    s.index = s.index - pd.Timedelta(minutes=1)
    s.index.name = "t_start"
    return s


@lru_cache
def noaa_zone_temps() -> pd.DataFrame:
    """Hourly temperature (degF) per zone station, index = hour start in Central."""
    w = pd.read_csv(_path("noaa_zone_temp_hourly"), parse_dates=["t_utc"])
    w = w.set_index(w["t_utc"] - UTC_OFFSET).drop(columns="t_utc")
    w.index.name = "t_start"
    return w


@lru_cache
def ghcnd_daily() -> pd.DataFrame:
    """GHCN-Daily TMIN/TMAX (degF) per airport station; `day` = local standard date (Central)."""
    d = pd.read_csv(_path("noaa_ghcnd_daily"))
    d["day"] = pd.to_datetime(d["DATE"])
    return d[["STATION", "day", "TMIN", "TMAX"]]


@lru_cache
def eia_hourly() -> pd.DataFrame:
    e = pd.read_csv(_path("eia_ercot_hourly"))
    t = pd.to_datetime(e["time_central"].str.slice(0, 19))
    e = e.set_index(t).rename(columns={"Demand (MWh)": "demand", "Demand Forecast (MWh)": "demand_forecast",
                                        "Net Generation (MWh)": "net_generation",
                                        "Total Interchange (MWh)": "total_interchange"})
    e.index.name = "t_start"
    return e[["demand", "demand_forecast", "net_generation", "total_interchange"]].astype(float)


UNIT_COLS = {"STATION": "station", "STATION LONG NAME": "station_name", "UNIT NAME": "unit",
             "SEASONAL MAX MW (HSL)": "hsl_mw", "AVAILABLE MW AFTER OUTAGE/DERATE": "available_mw",
             "MW REDUCTION FROM OUTAGE/DERATE": "mw_out", "FUEL TYPE": "fuel_raw", "START": "start", "END": "end",
             "RESOURCE ENTITY": "resource_entity", "COUNTY": "county"}
FUEL_MAP = {"NG": "gas", "COAL": "coal", "WIND": "wind", "SOLAR": "solar", "NUCLEAR": "nuclear",
            "HYDRO": "hydro", "STORAGE": "storage", "ESR": "storage", "OTHER": "other", "BIOMASS": "other",
            "DFO": "other", "OIL": "other", "WSTH": "other", "LANDFILL": "other", "PURCHASE": "other"}


@lru_cache
def unit_outages(version: str) -> pd.DataFrame:
    """Unit outage list, version '2021_03_04' or '2021_03_12'. Keeps blank (redacted) rows, flagged `blank`.
    START/END are Central (T9)."""
    df = pd.read_excel(_path(f"unit_outage_{version}"), sheet_name="OutageData")
    df = df.rename(columns=UNIT_COLS)
    for c in ("start", "end"):
        df[c] = pd.to_datetime(df[c], format="mixed")
    others = [c for c in UNIT_COLS.values() if c not in ("start", "end")]
    df["blank"] = df[others].isna().all(axis=1)
    df["fuel"] = df["fuel_raw"].map(lambda f: FUEL_MAP.get(str(f).strip().upper(), "other") if pd.notna(f) else None)
    for c in ("station", "unit", "station_name", "county"):
        df[c] = df[c].astype("string").str.strip()
    df["row"] = range(len(df))
    df["version"] = version
    return df
