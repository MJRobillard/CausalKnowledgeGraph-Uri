"""The ERCOT generating fleet for the causal-ML panel: EIA-860 (2020) generators in ERCOT, matched to the stations in
ERCOT's 3/12 unit outage list, and daily outage energy per plant x fuel.

Matching (deterministic, ASSUMPTIONS S-items), in order: (1) the ERCOT station code is the prefix of the EIA
generator's RTO/ISO LMP node name, same fuel; (2) same county and fuel, names similar (token_set >= 85 after dropping
generic words and joining initials); (3) the only plant of that fuel in the county, capacity within 30%; (4) a unique
statewide name match at >= 92 (multi-county wind farms); (5) the unique unclaimed plant in the county with the same
fuel and capacity within 3%. Everything else is reported unmatched, with its MW, and kept out of the panel.

    uv run python -m kg.fleet          # writes data/processed/fleet/*.parquet and match_report.json
"""
from __future__ import annotations

import io
import json
import re
import zipfile

import numpy as np
import pandas as pd
from rapidfuzz import fuzz

from . import series as S
from .common import DATA, PROCESSED

RAW = DATA / "raw"
OUT = PROCESSED / "fleet"
DAYS = pd.date_range("2021-02-10", "2021-02-19", freq="D")
FUEL_FROM_EIA = {  # EIA-860 energy source code -> fuel group used in ERCOT's list
    "NG": "gas", "OG": "gas", "BFG": "gas", "SUB": "coal", "LIG": "coal", "BIT": "coal", "RC": "coal", "PC": "other",
    "WND": "wind", "SUN": "solar", "NUC": "nuclear", "WAT": "hydro", "MWH": "storage", "DFO": "other", "RFO": "other",
    "WDS": "other", "LFG": "other", "OBG": "other", "AB": "other", "MSW": "other", "OTH": "other", "PUR": "other",
    "WH": "other", "JF": "other", "KER": "other", "WO": "other", "TDF": "other", "OBS": "other", "BLQ": "other",
}
NAME_STOP = {"power", "plant", "station", "generating", "generation", "energy", "center", "facility", "project", "llc",
             "lp", "inc", "wind", "solar", "farm", "ranch", "unit", "units", "the", "of", "electric", "cogeneration",
             "cogen", "plt", "gen", "ctr", "i", "ii", "iii", "1", "2", "3"}


def name_key(s) -> str:
    """Lowercase content words; runs of single letters are joined ("W A Parish" -> "wa parish")."""
    toks = re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower()).split()
    joined, buf = [], ""
    for t in toks:
        if len(t) == 1 and t.isalpha():
            buf += t
            continue
        if buf:
            joined.append(buf); buf = ""
        joined.append(t)
    if buf:
        joined.append(buf)
    return " ".join(t for t in joined if t not in NAME_STOP)


# ---------------------------------------------------------------- EIA-860
def _read_xlsx(z: zipfile.ZipFile, pattern: str, sheet=0) -> pd.DataFrame:
    name = next(n for n in z.namelist() if re.search(pattern, n, re.I))
    return pd.read_excel(io.BytesIO(z.read(name)), sheet_name=sheet, header=1)


def eia860() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(plants, generators) for operable generators in the ERCOT balancing authority."""
    z = zipfile.ZipFile(RAW / "eia860_2020" / "eia8602020.zip")
    plants = _read_xlsx(z, r"2___Plant")
    gens = _read_xlsx(z, r"3_1_Generator", sheet="Operable")
    plants = plants[plants["Balancing Authority Code"] == "ERCO"]
    gens = gens[gens["Plant Code"].isin(plants["Plant Code"])].copy()
    gens["fuel"] = gens["Energy Source 1"].map(FUEL_FROM_EIA).fillna("other")
    for c in ("Nameplate Capacity (MW)", "Winter Capacity (MW)", "Summer Capacity (MW)", "Operating Year"):
        gens[c] = pd.to_numeric(gens[c], errors="coerce")
    return plants, gens


def plant_fuel_table(plants: pd.DataFrame, gens: pd.DataFrame) -> pd.DataFrame:
    g = gens.assign(cap=gens["Winter Capacity (MW)"].fillna(gens["Nameplate Capacity (MW)"]),
                    age_w=lambda d: (2021 - d["Operating Year"]) * d["Nameplate Capacity (MW)"])
    g["lmp_station"] = g["RTO/ISO LMP Node Designation"].astype("string").str.upper().str.split("_").str[0]
    agg = g.groupby(["Plant Code", "fuel"]).agg(
        lmp_stations=("lmp_station", lambda s: sorted(set(s.dropna()))),
        capacity_mw=("cap", "sum"), nameplate_mw=("Nameplate Capacity (MW)", "sum"), n_generators=("Generator ID", "count"),
        age_w=("age_w", "sum"), technology=("Technology", lambda s: s.value_counts().index[0]),
        prime_mover=("Prime Mover", lambda s: s.value_counts().index[0])).reset_index()
    agg["age_years"] = agg.age_w / agg.nameplate_mw.replace(0, np.nan)
    p = plants[["Plant Code", "Plant Name", "County", "State", "Latitude", "Longitude", "Utility Name"]]
    out = agg.drop(columns="age_w").merge(p, on="Plant Code", how="left")
    out["county_key"] = out.County.str.upper().str.replace(r"[^A-Z ]", "", regex=True).str.strip()
    out["name_key"] = out["Plant Name"].map(name_key)
    out["plant_fuel_id"] = out["Plant Code"].astype(str) + ":" + out.fuel
    return out


# ---------------------------------------------------------------- ERCOT outage list -> stations, daily energy out
def ercot_stations() -> pd.DataFrame:
    u = S.unit_outages("2021_03_12").dropna(subset=["station"])
    units = u.drop_duplicates(["station", "unit"])
    st = units.groupby(["station", "fuel"]).agg(station_name=("station_name", "first"), county=("county", "first"),
                                                hsl_mw=("hsl_mw", "sum"), n_units=("unit", "nunique")).reset_index()
    st["county_key"] = st.county.str.upper().str.replace(r"[^A-Z ]", "", regex=True).str.strip()
    st["name_key"] = st.station_name.map(name_key)
    return st


def daily_mwh_out() -> pd.DataFrame:
    """MWh out per station x fuel x day, Feb 10-19, from the 3/12 list. Per unit, overlapping rows are not summed:
    the unit's MW out at any moment is the largest row covering it (a derate and an outage of the same unit overlap)."""
    u = S.unit_outages("2021_03_12").dropna(subset=["station"])
    t0, t1 = DAYS[0], DAYS[-1] + pd.Timedelta(days=1)
    grid = pd.date_range(t0, t1, freq="15min", inclusive="left")
    rows = []
    for (st, fuel, unit), g in u.groupby(["station", "fuel", "unit"]):
        mw = np.zeros(len(grid))
        for _, r in g.iterrows():
            a, b = max(r.start, t0), min(r.end, t1)
            if b <= a:
                continue
            i0, i1 = grid.searchsorted(a), grid.searchsorted(b)
            mw[i0:i1] = np.maximum(mw[i0:i1], r.mw_out)
        if mw.any():
            s = pd.Series(mw * 0.25, index=grid).resample("D").sum()          # MWh per day
            for d, v in s.items():
                rows.append({"station": st, "fuel": fuel, "unit": unit, "day": d, "mwh_out": v})
    df = pd.DataFrame(rows)
    return df.groupby(["station", "fuel", "day"], as_index=False).mwh_out.sum()


# ---------------------------------------------------------------- matching
def match(stations: pd.DataFrame, pf: pd.DataFrame) -> pd.DataFrame:
    out = []
    by_code = {}
    for _, r in pf.iterrows():
        for c in r.lmp_stations:
            by_code.setdefault((c, r.fuel), []).append(r)
    for _, s in stations.iterrows():
        cand = pf[(pf.county_key == s.county_key) & (pf.fuel == s.fuel)]
        how, best, score = None, None, 0.0
        coded = by_code.get((str(s.station).upper(), s.fuel), [])
        if len(coded) == 1:                          # ERCOT station code in EIA's LMP node name: strongest key
            how, best, score = "lmp_code", coded[0], 100.0
        elif len(cand):
            scores = cand.name_key.map(lambda k: fuzz.token_set_ratio(k, s.name_key) if k and s.name_key else 0)
            j = scores.idxmax()
            score = float(scores[j])
            if score >= 85:
                how, best = "name", cand.loc[j]
            elif len(cand) == 1 and abs(cand.iloc[0].capacity_mw - s.hsl_mw) <= 0.3 * max(s.hsl_mw, 1):
                how, best = "only_plant_capacity", cand.iloc[0]
        if how is None and s.name_key:              # statewide fallback (multi-county wind farms): strict and unique
            same = pf[pf.fuel == s.fuel]
            sc = same.name_key.map(lambda k: fuzz.token_set_ratio(k, s.name_key) if k else 0)
            top = sc[sc >= 92]
            if len(top) == 1:
                how, best, score = "name_statewide", same.loc[top.index[0]], float(top.iloc[0])
        out.append({"station": s.station, "fuel": s.fuel, "station_name": s.station_name, "county": s.county,
                    "hsl_mw": s.hsl_mw, "match": how, "plant_fuel_id": None if best is None else best.plant_fuel_id,
                    "eia_plant_name": None if best is None else best["Plant Name"], "name_score": score})
    res = pd.DataFrame(out)
    # last rule: the unique unclaimed EIA plant in the county with the same fuel and capacity within 3%
    used = set(res.plant_fuel_id.dropna())
    for i, s in res[res.match.isna()].iterrows():
        cand = pf[(pf.county_key == str(s.county).upper().strip()) & (pf.fuel == s.fuel) & ~pf.plant_fuel_id.isin(used)]
        cand = cand[(cand.capacity_mw - s.hsl_mw).abs() <= 0.03 * max(s.hsl_mw, 1)]
        if len(cand) == 1:
            r = cand.iloc[0]
            res.loc[i, ["match", "plant_fuel_id", "eia_plant_name"]] = ["capacity_county", r.plant_fuel_id, r["Plant Name"]]
            used.add(r.plant_fuel_id)
    return res


def main() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    plants, gens = eia860()
    pf = plant_fuel_table(plants, gens)
    st = ercot_stations()
    m = match(st, pf)
    daily = daily_mwh_out().merge(m[["station", "fuel", "plant_fuel_id"]], on=["station", "fuel"], how="left")
    pf.to_parquet(OUT / "eia860_ercot_plant_fuel.parquet", index=False)
    m.to_parquet(OUT / "station_matches.parquet", index=False)
    daily.to_parquet(OUT / "station_daily_mwh_out.parquet", index=False)
    mwh = daily.groupby(daily.plant_fuel_id.notna()).mwh_out.sum()
    rep = {"eia_plant_fuel_rows": len(pf), "eia_capacity_by_fuel_mw": pf.groupby("fuel").capacity_mw.sum().round(0).to_dict(),
           "ercot_stations": len(st), "matched": int(m.match.notna().sum()),
           "match_how": m.match.value_counts(dropna=False).to_dict(),
           "matched_share_of_outage_mwh": float(mwh.get(True, 0) / mwh.sum()),
           "unmatched_hsl_by_fuel": m[m.match.isna()].groupby("fuel").hsl_mw.sum().round(0).to_dict(),
           "plants_shared_by_several_stations": int(m.plant_fuel_id.dropna().duplicated().sum())}
    (OUT / "match_report.json").write_text(json.dumps(rep, indent=1, default=str))
    return rep


if __name__ == "__main__":
    print(json.dumps(main(), indent=1, default=str))
