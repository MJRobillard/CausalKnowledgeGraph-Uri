"""Extend data/external/noaa/ercot_zone_temp_hourly_utc_F.csv (Feb 1-21) to Feb 26 for the +5-day placebo (step 4).

Same rule as the original file, reconstructed 2026-11-15 by reproducing it from noaa_isd_hourly_raw.csv
(100% of cells within 0.05 F): every TMP report (all report types, +9999 dropped), degC/10 -> degF,
mean per station per UTC hour (floor), gaps of up to 3 hours interpolated. Original rows are kept unchanged.

    uv run python scripts/extend_zone_temps.py
"""
from pathlib import Path

import pandas as pd

W = Path(__file__).resolve().parents[1] / "data" / "external" / "noaa"
ZONES = {"72243012960": "Coast", "72244813972": "East", "72265023023": "Far West", "72351013966": "North",
         "72259003927": "North Central", "72254013904": "South Central", "72253012921": "South Central (extra)",
         "72251012924": "Southern", "72266013962": "West"}


def hourly(raw: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    r = raw.copy()
    r["t"] = pd.to_datetime(r.DATE)
    tmp = r.TMP.str.split(",", expand=True)[0]
    r = r[tmp != "+9999"].assign(F=tmp[tmp != "+9999"].astype(float) / 10 * 9 / 5 + 32)
    g = r.groupby([r.t.dt.floor("h"), r.STATION]).F.mean().unstack().rename(columns=ZONES)
    g = g.reindex(pd.date_range(start, end, freq="h")).interpolate(limit=3, limit_area="inside").round(1)
    return g[list(ZONES.values())]


def main():
    orig = pd.read_csv(W / "ercot_zone_temp_hourly_utc_F.csv", parse_dates=["t_utc"]).set_index("t_utc")
    old = pd.read_csv(W / "noaa_isd_hourly_raw.csv", dtype=str)
    check = (hourly(old, "2021-02-01", "2021-02-21 23:00") - orig).abs()
    assert float((check < 0.051).mean().mean()) > 0.99, "rule no longer reproduces the original file"
    new = pd.read_csv(W / "raw_isd_2021-02-22_26" / "noaa_isd_hourly_raw_2021-02-22_26.csv", dtype=str)
    both = pd.concat([old, new])
    ext = hourly(both, "2021-02-01", "2021-02-26 23:00")
    ext.loc[orig.index] = orig.values                         # keep the original rows exactly
    ext.index.name = "t_utc"
    out = W / "ercot_zone_temp_hourly_utc_F_2021-02-01_26.csv"
    ext.to_csv(out)
    print(out.name, ext.shape, "missing cells:", int(ext.isna().sum().sum()))


if __name__ == "__main__":
    main()
