"""Clean the TAMU 2021TXPowerOutage data (Wu et al.).

The TAMU folder is left untouched; cleaned copies go to data/external/tamu/.
Every fix and the evidence for it is logged in docs/tamu_cleaning.md; the numbers
quoted there are written to data/external/tamu/cleaning_stats.json by this script.

    uv run python scripts/clean_tamu.py

Conventions for every output file:
- `time_central`: ISO 8601 with offset, America/Chicago (CST = -06:00 for all of Feb 2021).
- Hourly data is labelled by the START of the hour (EIA's "hour ending" labels are shifted back 1 h).
- `timestamp_raw` keeps the source label wherever the source had one.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "2021TXPowerOutage"
EIA = SRC / "From EIA (Generation, Demand, Demand Forecasts, and Interchange by BA)"
ERCOT = ROOT / "data" / "sources" / "ercot" / "data"
OUT = ROOT / "data" / "external" / "tamu"
TZ = "America/Chicago"
CST = "Etc/GMT+6"  # fixed UTC-6; used where a source explicitly says CST
STATS: dict = {}


# ---------- helpers ----------
def write(df: pd.DataFrame, rel: str) -> None:
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False)
    STATS.setdefault("outputs", []).append(rel)


def iso(t: pd.Series | pd.DatetimeIndex) -> pd.Series:
    t = pd.Series(t)
    return t.map(lambda x: x.isoformat() if pd.notna(x) else "")


def with_time(df: pd.DataFrame, t, raw=None) -> pd.DataFrame:
    df = df.copy()
    if raw is not None:
        df.insert(0, "timestamp_raw", list(raw))
    df.insert(0, "time_central", iso(t).values)
    return df


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def eia_hour_beginning(labels: pd.Series) -> pd.Series:
    """'1/31/2021 12 a.m. CST' (hour ending, CST) -> tz-aware hour beginning, America/Chicago."""
    assert labels.str.endswith(" CST").all(), "non-CST label in EIA file"
    s = labels.str.replace(" CST", "", regex=False).str.replace("a.m.", "AM").str.replace("p.m.", "PM")
    he = pd.to_datetime(s, format="%m/%d/%Y %I %p").dt.tz_localize(CST)
    return (he - pd.Timedelta(hours=1)).dt.tz_convert(TZ)


def slug(name: str) -> str:
    return "_".join(name.lower().replace(",", "").split())


# ---------- 1. EIA grid monitor exports (hour ending CST) ----------
def clean_eia() -> pd.DataFrame:
    st = STATS["eia"] = {"files": {}, "skipped_empty": []}
    ercot_sub = None
    for folder in sorted(p for p in EIA.iterdir() if p.is_dir()):
        if folder.name.startswith("BES"):
            continue
        for f in sorted(folder.iterdir()):
            if f.stat().st_size == 0:
                st["skipped_empty"].append(f"{folder.name}/{f.name}")
                continue
            if f.suffix == ".json":
                j = json.loads(f.read_text(encoding="utf-8-sig"))
                cols = {s["name"]: pd.Series({d["Timestamp (Hour Ending)"]: d["value"] for d in s["data"]})
                        for s in j["series"]}
                df = pd.DataFrame(cols)
                df.index.name = "Timestamp (Hour Ending)"
                df = df.reset_index()
                df.insert(0, "BA Code", "MISO")
            elif f.name.startswith("All_available"):
                continue  # handled in clean_eia_api()
            else:
                df = pd.read_csv(f, encoding="utf-8-sig")
            raw = df["Timestamp (Hour Ending)"]
            t = eia_hour_beginning(raw)
            dups = int(t.duplicated().sum())
            out = with_time(df.drop(columns="Timestamp (Hour Ending)"), t, raw)
            rel = f"eia/{slug(folder.name)}.csv"
            write(out, rel)
            st["files"][rel] = {"source": f"{folder.name}/{f.name}", "rows": len(out),
                                "first": out.time_central.iloc[0], "last": out.time_central.iloc[-1],
                                "duplicate_times": dups}
            if folder.name == "ERCOT Demand by Subregion":
                ercot_sub = df.drop(columns=["BA Code", "Timestamp (Hour Ending)"]).set_index(t)
    return ercot_sub


def check_eia_clock() -> None:
    """EIA ERCOT demand vs ERCOT's own estimated load minus load shed (Central), 2/15."""
    d = pd.read_csv(EIA / "ERCOT Demand, Demand Forecast, and Net Interchange/2021-02-01 to 2020-02-19.csv",
                    encoding="utf-8-sig")
    d.index = eia_hour_beginning(d["Timestamp (Hour Ending)"]).dt.tz_localize(None)
    x = pd.read_excel(ERCOT / "Available_Generation_and_Estimated_Load_without_Load_Shed_Data.xlsx")
    est = x.iloc[:, [3, 4]].dropna()
    est = pd.Series(est.iloc[:, 1].astype(float).values, index=pd.to_datetime(est.iloc[:, 0]))  # hour ending
    shed = x.iloc[:, [6, 7]].dropna()
    shed = pd.Series(shed.iloc[:, 1].astype(float).values, index=pd.to_datetime(shed.iloc[:, 0]))  # minute ending
    served = est - shed.resample("h", closed="right", label="right").mean().reindex(est.index)
    served.index = served.index - pd.Timedelta(hours=1)  # -> hour beginning
    res = {}
    for lag in range(-3, 4):
        j = pd.concat([d["Demand (MWh)"], served.shift(lag, freq="h")], axis=1).dropna()
        res[lag] = round(float((j.iloc[:, 0] - j.iloc[:, 1]).abs().mean()), 1)
    first = pd.concat([d["Demand (MWh)"], served], axis=1).dropna().head(2)
    STATS["eia_clock_check"] = {"mae_by_lag_h": res,
                                "first_two_hours": first.reset_index().astype(str).values.tolist()}


def clean_eia_api() -> None:
    f = EIA / "ERCOT Demand by Subregion/All_available_load_from_EIA_API.csv"
    df = pd.read_csv(f, index_col=0, parse_dates=True).sort_index(kind="stable")
    raw = df.index.astype(str)
    dup_times = [str(x) for x in df.index[df.index.duplicated(keep=False)].unique()]
    # labels are local prevailing Central, hour ending (verified: exact match to the CSV export at -1 h;
    # the only duplicate labels are the 1 a.m. DST fall-back hours)
    he = df.index.tz_localize(TZ, ambiguous="infer", nonexistent="shift_forward")
    t = (he.tz_convert("UTC") - pd.Timedelta(hours=1)).tz_convert(TZ)
    out = with_time(df.reset_index(drop=True), t, raw)
    write(out, "eia/ercot_demand_by_subregion_api_2019_2021.csv")
    STATS["eia_api"] = {"rows": len(out), "dst_duplicate_labels": dup_times,
                        "first": out.time_central.iloc[0], "last": out.time_central.iloc[-1]}


# ---------- 2. BES profiles and model outputs (the "2016" dates) ----------
def clean_bes(ercot_sub: pd.DataFrame) -> pd.Series:
    st = STATS["bes"] = {}
    B = EIA / "BES profiles"

    # demand: every row is an EIA subregion row; match on values to recover its true hour
    bd = pd.read_csv(B / "BES_demand_Feb2021_v20210224.csv", encoding="utf-8-sig")
    key = lambda r: tuple(sorted(int(v) for v in r))  # BES reorders the 8 zones
    emap = {}
    for t, r in zip(ercot_sub.index, ercot_sub.values):
        emap.setdefault(key(r), t)
    true_t = [emap.get(key(r)) for r in bd.iloc[:, 1:].values]
    assert all(x is not None for x in true_t), "BES demand row with no EIA match"
    true_t = pd.Series(true_t)
    lbl = pd.to_datetime(bd["CST"], format="%m/%d/%Y %H:%M")
    offsets = (lbl - true_t.dt.tz_localize(None)).value_counts()
    assert len(offsets) == 1, offsets
    write(with_time(bd.drop(columns="CST"), true_t, bd["CST"]), "bes_profiles/bes_demand_feb2021.csv")
    st["demand"] = {"rows": len(bd), "rows_matched_to_eia": len(true_t),
                    "label_minus_true": str(offsets.index[0]),
                    "first_label": bd["CST"].iloc[0], "first_true": true_t.iloc[0].isoformat()}

    # TAMU's simulation clock: row i = 2021-02-01 00:00 CST + i h (process_event.m puts 2/14 00:00 at row 313)
    sim_clock = pd.Series(pd.date_range("2021-02-01 00:00", periods=len(bd), freq="h", tz=TZ))

    # solar / hydro: 2016 weather-year profiles used as stand-ins; put on the simulation clock (year 2016 -> 2021)
    for name in ["solar", "hydro"]:
        f = B / f"BES_{name}_Feb2016_v20210224.csv"
        df = pd.read_csv(f, encoding="utf-8-sig")
        lbl = pd.to_datetime(df["CST"], format="%m/%d/%Y %H:%M")
        t = lbl.map(lambda x: x.replace(year=2021)).dt.tz_localize(TZ)
        assert (t.values == sim_clock.values).all()
        write(with_time(df.drop(columns="CST"), t, df["CST"]), f"bes_profiles/bes_{name}_feb2016_profile.csv")
        st[name] = {"rows": len(df), "first_label": df["CST"].iloc[0], "first_out": t.iloc[0].isoformat()}

    # wind: labelled UTC, 4 versions (+ a byte-identical copy in data/)
    st["wind"] = {}
    for f in sorted(B.glob("BES_wind_*.csv")):
        df = pd.read_csv(f)
        t = pd.to_datetime(df["UTC"]).dt.tz_localize("UTC").dt.tz_convert(TZ)
        rel = f"bes_profiles/{f.stem.lower()}.csv"
        write(with_time(df.drop(columns="UTC"), t, df["UTC"]), rel)
        st["wind"][rel] = {"rows": len(df), "first_out": t.iloc[0].isoformat()}
    dup = SRC / "data/BES_wind_20210224.csv"
    st["wind_duplicate_skipped"] = {"file": "data/BES_wind_20210224.csv",
                                    "identical_to": "BES_wind_Feb2021_v20210224.csv",
                                    "identical": md5(dup) == md5(B / "BES_wind_Feb2021_v20210224.csv")}

    # model outputs PG / PF: "UTC 2016-01-01 00:00" is a placeholder row index
    st["outputs"] = {}
    ds = bd.iloc[:, 1:].sum(axis=1)
    for name in ["PG", "PF"]:
        f = EIA / "BES model outputs" / f"{name}.csv"
        df = pd.read_csv(f)
        assert len(df) == len(bd)
        out = with_time(df.drop(columns="UTC"), sim_clock, df["UTC"])
        out.insert(1, "demand_hour_central", iso(true_t).values)
        write(out, f"bes_outputs/{name.lower()}.csv")
        info = {"rows": len(df), "first_label": df["UTC"].iloc[0], "last_label": df["UTC"].iloc[-1],
                "code_copy_identical": md5(f) == md5(SRC / "code" / f"{name}.csv")}
        if name == "PG":
            ratio = df.iloc[:, 1:].sum(axis=1) / ds
            info["pg_sum_over_demand_sum"] = [round(float(ratio.min()), 6), round(float(ratio.max()), 6)]
        st["outputs"][name] = info
    st["outputs"]["LMP.pkl"] = "skipped: pickle only (loading a pickle runs code); no CSV copy exists"
    return true_t


# ---------- 3. poweroutage.us county records ----------
def county_map(tx: pd.DataFrame) -> dict:
    """Map the 13 numeric CountyName values (all Southwest Texas Electric Coop, 2/22 12:29 UTC, no FIPS)."""
    fips = (tx.dropna(subset=["CountyFIPS"]).groupby("CountyFIPS").CountyName
            .agg(lambda s: s.mode()[0]))
    num = tx[tx.CountyName.str.isdigit()]
    util = num.UtilityName.unique()
    assert len(util) == 1
    named = (tx[(tx.UtilityName == util[0]) & ~tx.CountyName.str.isdigit()]
             .groupby("CountyName").agg(tracked=("CustomersTracked", "max"), fips=("CountyFIPS", "first")))
    m, rows = {}, []
    for _, r in num.iterrows():
        code = int(r.CountyName)
        alpha = fips.get(float(48000 + 2 * code - 1))  # Texas FIPS = 2*(alphabetical index) - 1
        diff = (named.tracked - r.CustomersTracked).abs() / r.CustomersTracked
        best = diff.idxmin()
        if diff[best] <= 0.01:
            name, how = best, "tracked-count match"
        else:
            name, how = alpha, "alphabetical-index FIPS only"
        m[r.CountyName] = name
        rows.append({"code": r.CountyName, "mapped_to": name, "fips": int(named.fips.get(name, 48000 + 2 * code - 1)),
                     "how": how, "tracked": int(r.CustomersTracked),
                     "named_tracked": int(named.tracked.get(name, -1)),
                     "alphabetical_index_says": alpha})
    STATS["county_map"] = {"utility": util[0], "rows": rows}
    return m


def clean_load_outage() -> None:
    D = SRC / "data/load_outage"
    st = STATS["load_outage"] = {}

    tx = pd.read_csv(D / "txraw.csv", index_col=0)
    m = county_map(tx)
    twins = {r["code"] for r in STATS["county_map"]["rows"] if r["how"] == "tracked-count match"}
    fipsmap = {r["mapped_to"]: r["fips"] for r in STATS["county_map"]["rows"]}

    def fix_rows(df, label):
        mask = df.CountyName.isin(m.keys()) & (df.StateName == "Texas")
        df.loc[mask, "CountyFIPS"] = df.loc[mask, "CountyName"].map(m).map(fipsmap)
        df.loc[mask, "CountyName"] = df.loc[mask, "CountyName"].map(m)
        t = pd.to_datetime(df.RecordedDateTime).dt.tz_localize("UTC").dt.tz_convert(TZ)
        out = with_time(df.drop(columns="RecordedDateTime"), t, df.RecordedDateTime)
        out["out_exceeds_tracked"] = out.CustomersOut > out.CustomersTracked
        st[label] = {"rows": len(out), "numeric_counties_mapped": int(mask.sum()),
                     "out_exceeds_tracked_rows": int(out.out_exceeds_tracked.sum()),
                     "first": out.time_central.min(), "last": out.time_central.max()}
        return out

    tx = tx.reset_index().rename(columns={"index": "raw_row"})
    write(fix_rows(tx, "txraw"), "load_outage/txraw.csv")
    raw = pd.read_csv(D / "raw.csv", encoding="utf-8-sig")
    st["raw_numeric_counties_other_states"] = int(
        (raw.CountyName.astype(str).str.isdigit() & (raw.StateName != "Texas")).sum())
    write(fix_rows(raw, "raw"), "load_outage/raw_all_states.csv")

    # clock check: statewide customers out vs ERCOT load shed onset
    tx["t"] = pd.to_datetime(tx.RecordedDateTime)
    key = tx.UtilityName + "|" + tx.CountyName + "|" + tx.CityName
    w = tx.assign(k=key).pivot_table(index="t", columns="k", values="CustomersOut", aggfunc="last")
    tot = w.sort_index().ffill().fillna(0).sum(axis=1).resample("10min").last().ffill()
    st["clock_check"] = {"first_10min_over_500k_customers_out_raw_label": str(tot[tot > 5e5].index[0])}

    # hourly county matrices (TAMU labelled these "UTC"; they are built straight from RecordedDateTime)
    for f in ["cty_outage", "cty_total", "cty_out_pct", "cty_out_dist", "cty_outage_all", "cty_total_all"]:
        df = pd.read_csv(D / f"{f}.csv")
        info = {"rows": len(df)}
        num = [c for c in df.columns if c.isdigit()]
        if num:
            dropped, added, renamed = [], [], {}
            for c in num:
                name = m[c]
                if name not in df.columns:
                    renamed[c] = name
                elif c in twins:  # same customers already counted under the named column
                    dropped.append(f"{c}->{name}")
                    info.setdefault("dropped_col_abs_sum", 0)
                    info["dropped_col_abs_sum"] += float(df[c].abs().sum())
                else:  # this utility's customers are not in the named column yet
                    df[name] = df[name] + df[c]
                    added.append(f"{c}->{name}")
            df = df.drop(columns=[c for c in num if c not in renamed]).rename(columns=renamed)
            info.update(numeric_cols=len(num), dropped_as_duplicate=dropped, added_into_named=added, renamed=renamed)
        if f == "cty_out_pct":
            vals = df.drop(columns="UTC")
            over = vals > 1
            info["cells_over_100pct"] = int(over.sum().sum())
            info["max_before_cap"] = float(vals.max().max())
            info["counties_over_100pct"] = {c: int(n) for c, n in over.sum().items() if n}
            info["rows_zero_by_construction"] = "first 48 rows (TAMU loop starts at hour 48)"
            df[vals.columns] = vals.clip(upper=1.0)
        t = pd.to_datetime(df["UTC"]).dt.tz_localize("UTC").dt.tz_convert(TZ)
        write(with_time(df.drop(columns="UTC"), t, df["UTC"]), f"load_outage/{f}.csv")
        info["first"] = t.iloc[0].isoformat()
        st[f] = info

    # avg_out_and_ttl.xlsx: rows = header / out / ttl, columns = counties (no timestamps)
    a = pd.read_excel(D / "avg_out_and_ttl.xlsx", header=None)
    orig = [str(x) for x in a.iloc[0]]
    hdr = [m.get(x, x) for x in orig]
    a.columns = orig
    a = a.iloc[1:].copy()
    dupnames, added = [], []
    for o in [x for x in orig if x.isdigit()]:
        h = m[o]
        if h not in a.columns:
            continue
        if o in twins:
            dupnames.append(f"{o}->{h}")
        else:
            a[h] = a[h].astype(float) + a[o].astype(float)
            added.append(f"{o}->{h}")
        a = a.drop(columns=o)
    a = a.rename(columns={**m, "UTC": "measure"})
    write(a, "load_outage/avg_out_and_ttl.csv")
    st["avg_out_and_ttl"] = {"dropped_as_duplicate": dupnames, "added_into_named": added}


# ---------- 4. ERCOT unit outage list (3/4 version) and the per-county matrices built from it ----------
def clean_unit_outage() -> None:
    U = SRC / "data/unit_outage"
    st = STATS["unit_outage"] = {}
    df = pd.read_excel(U / "unit_outage.xlsx")
    assert df["Unnamed: 4"].isna().all()
    df = df.drop(columns="Unnamed: 4")
    blank = df.drop(columns=["START", "END"]).isna().all(axis=1)
    partial = df.drop(columns=["START", "END"]).isna().any(axis=1) & ~blank
    st["rows_in"] = len(df)
    st["blank_rows_dropped"] = int(blank.sum())
    st["partially_blank_rows"] = int(partial.sum())
    blanks = df[blank]
    df = df[~blank].copy()
    for c in ["START", "END"]:
        t = pd.to_datetime(df[c], format="%m/%d/%y %I:%M %p").dt.tz_localize(TZ)
        df.insert(df.columns.get_loc(c), f"{c.lower()}_central", iso(t).values)
        df = df.rename(columns={c: f"{c.lower()}_raw"})
    write(df, "unit_outage/unit_outage_20210304.csv")
    st["rows_out"] = len(df)

    # same as ERCOT's own 3/4 file? and do the blank rows' times show up in the 3/12 version?
    e = pd.read_excel(ERCOT / "Unit_Outage_Data_20210304__Public_.xlsx", sheet_name="OutageData")
    st["ercot_0304_rows"] = len(e)
    st["ercot_0304_blank_rows"] = int(e.drop(columns=["START", "END"]).isna().all(axis=1).sum())
    e12 = pd.read_excel(ERCOT / "Unit_Outage_Data_20210312.xlsx", sheet_name=None)
    e12 = max(e12.values(), key=len)
    e12 = e12[[c for c in e12.columns if str(c).strip().upper() in {"START", "END", "UNIT NAME"}]].dropna()
    e12.columns = [str(c).strip().upper() for c in e12.columns]
    pairs12 = set(zip(pd.to_datetime(e12["START"]), pd.to_datetime(e12["END"])))
    bs = zip(pd.to_datetime(blanks.START, format="%m/%d/%y %I:%M %p"),
             pd.to_datetime(blanks.END, format="%m/%d/%y %I:%M %p"))
    st["blank_rows_whose_times_match_a_named_0312_row"] = sum(p in pairs12 for p in bs)

    # per-county hourly matrices: labelled "UTC" but filled from START/END, which are Central, with no conversion
    ng = df[df["FUEL TYPE"] == "NG"]
    first_ng = pd.to_datetime(ng.start_central).min()
    for f in ["coal_outage", "ng_outage", "pv_outage", "ng_derate_fac"]:
        d = pd.read_csv(U / f"{f}.csv")
        t = pd.to_datetime(d["UTC"]).dt.tz_localize(TZ)  # relabel only, no shift
        write(with_time(d.drop(columns="UTC"), t, d["UTC"]), f"unit_outage/{f}.csv")
        info = {"rows": len(d), "shift_applied_h": 0}
        if f == "ng_outage":
            nz = d.drop(columns="UTC").sum(axis=1) > 0
            info["first_nonzero_label"] = d["UTC"][nz].iloc[0]
            info["earliest_ng_start_central"] = first_ng.isoformat()
        st[f] = info


# ---------- 5. other ERCOT-derived files in data/ ----------
def clean_ercot_derived() -> None:
    D = SRC / "data"
    st = STATS["ercot_derived"] = {}

    # load_shed_processed: 10-min, Central (best fit to ERCOT's minute load shed at 0 offset)
    ls = pd.read_csv(D / "load_shed_processed.csv")
    ls = ls.drop(columns=[c for c in ls.columns if ls[c].isna().all()])
    t = pd.to_datetime(ls.t_formated, format="%m/%d/%Y %H:%M")
    x = pd.read_excel(ERCOT / "Available_Generation_and_Estimated_Load_without_Load_Shed_Data.xlsx")
    shed = x.iloc[:, [6, 7]].dropna()
    shed = pd.Series(shed.iloc[:, 1].astype(float).values, index=pd.to_datetime(shed.iloc[:, 0])).resample("10min").mean()
    s = pd.Series(ls.v_load_shed_interp.values, index=t)
    st["load_shed_processed"] = {"rows": len(ls), "mae_vs_ercot_by_shift_min": {
        k * 10: round(float((pd.concat([s, shed.shift(k, freq="10min")], axis=1).dropna().diff(axis=1).iloc[:, 1]).abs().mean()), 1)
        for k in (-6, -1, 0, 1, 6)}}
    write(with_time(ls.drop(columns="t_formated"), t.dt.tz_localize(TZ), ls.t_formated),
          "ercot_derived/load_shed_processed.csv")

    # capacity_out_processed: 10-min, Central; check NG against ERCOT board-deck minute data
    co = pd.read_csv(D / "capacity_out_processed.csv")
    t = pd.to_datetime(co.t_formated, format="%d-%b-%Y %H:%M:%S")
    bd = pd.read_excel(ERCOT / "Data_for_Graphs_in_Board_Deck_-_Generation_Capacity_Out_by_Fuel_Type_Chart_and_Frequency_Chart.xlsx",
                       sheet_name="GRAPH - GEN OUT BY FUEL TYPE")
    ng = pd.Series(bd["NG MW OUT TOTAL"].values, index=pd.to_datetime(bd["DATE"])).resample("10min").mean()
    s = pd.Series(co.genout_ng_interp.values, index=t)
    st["capacity_out_processed"] = {"rows": len(co), "ng_mae_vs_board_deck_by_shift_min": {
        k * 10: round(float((pd.concat([s, ng.shift(k, freq="10min")], axis=1).dropna().diff(axis=1).iloc[:, 1]).abs().mean()), 1)
        for k in (-6, -1, 0, 1, 6)}}
    write(with_time(co.drop(columns="t_formated"), t.dt.tz_localize(TZ), co.t_formated),
          "ercot_derived/capacity_out_processed.csv")

    # ercot_capacity: only the first row has a time; process_event.m places the 168 rows hourly from 2/12 00:00
    ec = pd.read_csv(D / "ercot_capacity.csv")
    assert ec.t_formated.notna().sum() == 1 and ec.t_formated.iloc[0] == "2/12/2021 0:00"
    t = pd.date_range("2021-02-12 00:00", periods=len(ec), freq="h", tz=TZ)
    ec = ec.rename(columns={"Unnamed: 2": "unlabeled_col3"})
    write(with_time(ec.drop(columns="t_formated"), t, ec.t_formated.fillna("")), "ercot_derived/ercot_capacity.csv")
    st["ercot_capacity"] = {"rows": len(ec), "times_reconstructed": len(ec) - 1}

    # ercot_load_forecast: DeliveryDate + HourEnding 1:00..24:00, Central, DSTFlag all N
    lf = pd.read_csv(D / "ercot_load_forecast.csv")
    assert (lf.DSTFlag == "N").all()
    he = lf.HourEnding.str.split(":").str[0].astype(int)
    t = (pd.to_datetime(lf.DeliveryDate, format="%m/%d/%Y") + pd.to_timedelta(he - 1, unit="h")).dt.tz_localize(TZ)
    raw = lf.DeliveryDate + " HE " + lf.HourEnding
    write(with_time(lf.drop(columns=["DeliveryDate", "HourEnding"]), t, raw), "ercot_derived/ercot_load_forecast.csv")
    st["ercot_load_forecast"] = {"rows": len(lf)}

    # load_shed_simulated.xlsx: EIA-style hour-ending CST labels
    sim = pd.read_excel(D / "load_shed_simulated.xlsx")
    t = eia_hour_beginning(sim.ts)
    write(with_time(sim.drop(columns="ts"), t, sim.ts), "ercot_derived/load_shed_simulated.csv")
    st["load_shed_simulated"] = {"rows": len(sim)}

    st["skipped"] = {
        "capacity_out_copied.xlsx": "one timestamp ('2/12 00：00') for 48 rows; step unknown and not used by TAMU code",
        "*.mat, *.pkl": "binary/pickle; same data as the CSVs except LMP.pkl",
    }


if __name__ == "__main__":
    if not SRC.is_dir():
        raise SystemExit(f"{SRC} not found: git clone https://github.com/tamu-engineering-research/2021TXPowerOutage")
    OUT.mkdir(parents=True, exist_ok=True)
    check_eia_clock()
    ercot_sub = clean_eia()
    clean_eia_api()
    clean_bes(ercot_sub)
    clean_load_outage()
    clean_unit_outage()
    clean_ercot_derived()
    (OUT / "cleaning_stats.json").write_text(json.dumps(STATS, indent=2, default=str))
    print(json.dumps({k: v for k, v in STATS.items() if k != "outputs"}, indent=1, default=str)[:12000])
