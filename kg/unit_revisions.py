"""Classify every difference between the 3/4 and 3/12 ERCOT unit outage lists (revised_by, Tier 0b).

Kinds (one per row; rows are 3/12 rows unless `side` says 0304):
- expansion: 3/12 row starting before 2/14 (the wider window).
- unchanged: same (station, unit, START) in both, same MW and END.
- correction: on/after 2/14, the same outage with MW, END, unit or START changed (`changed` says which).
  Unit renames match on (START, END, MW), or on (START, END) alone when that pair is unique.
- disclosure: a blank (redacted) 3/4 row whose (START, END) matches exactly one unmatched 3/12 row (D9).
- disclosure_ambiguous: a blank 3/4 row whose (START, END) matches several 3/12 rows. Not guessed.
- unmatched_blank (side 0304): a blank 3/4 row with no 3/12 row at its (START, END).
- added_unexplained / dropped_unexplained: left over after all of the above. Reported, not forced into a kind.
"""
from __future__ import annotations

import pandas as pd

from .series import unit_outages

WINDOW_START = pd.Timestamp("2021-02-14")
VALUE_FIELDS = ["mw_out", "hsl_mw", "available_mw", "end"]
MAX_START_SHIFT = pd.Timedelta(hours=24)


def _with_dupe_index(df: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    df = df.copy()
    df["_k"] = df.groupby(key, dropna=False).cumcount()
    return df


def _pair(a: pd.DataFrame, n: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    """One-to-one pairs between rows of a (3/4) and n (3/12) with equal key (duplicates paired in order)."""
    a, n = _with_dupe_index(a, key), _with_dupe_index(n, key)
    m = a.merge(n, on=key + ["_k"], suffixes=("_0304", "_0312"))
    return m


def _same(x, y) -> bool:
    if pd.isna(x) and pd.isna(y):
        return True
    return x == y


def classify(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    rows = []
    real = old[~old.blank]
    blank = old[old.blank]

    exp = new[new.start < WINDOW_START]
    for _, r in exp.iterrows():
        rows.append({"kind": "expansion", "row_0312": r.row, "fuel": r.fuel, "station": r.station, "unit": r.unit,
                     "mw_0312": r.mw_out, "start_0312": r.start, "end_0312": r.end})
    n_left = new[new.start >= WINDOW_START]
    a_left = real

    # 1. same outage: (station, unit, START)
    key = ["station", "unit", "start"]
    m = _pair(a_left, n_left, key)
    for _, r in m.iterrows():
        changed = [f for f in VALUE_FIELDS if not _same(r[f + "_0304"], r[f + "_0312"])]
        rows.append({"kind": "correction" if changed else "unchanged", "changed": ",".join(changed),
                     "row_0304": r.row_0304, "row_0312": r.row_0312, "fuel": r.fuel_0312, "station": r.station,
                     "unit": r.unit, "mw_0304": r.mw_out_0304, "mw_0312": r.mw_out_0312, "start_0304": r.start,
                     "start_0312": r.start, "end_0304": r.end_0304, "end_0312": r.end_0312})
    n_left = n_left[~n_left.row.isin(m.row_0312)]
    a_left = a_left[~a_left.row.isin(m.row_0304)]

    # 2. disclosure: blank 3/4 rows matched on (START, END)
    used = set()
    for (s, e), grp in blank.groupby(["start", "end"], dropna=False):
        cand = n_left[(n_left.start == s) & (n_left.end == e)]
        nb, nc = len(grp), len(cand)
        if nc == 0:
            for _, b in grp.iterrows():
                rows.append({"kind": "unmatched_blank", "side": "0304", "row_0304": b.row, "start_0304": s,
                             "end_0304": e})
            continue
        kind = "disclosure" if nb == 1 and nc == 1 else "disclosure_ambiguous"
        for _, c in cand.iterrows():
            rows.append({"kind": kind, "row_0312": c.row, "blank_rows_0304": nb, "candidates_0312": nc,
                         "fuel": c.fuel, "station": c.station, "unit": c.unit, "mw_0312": c.mw_out,
                         "start_0312": s, "end_0312": e})
            used.add(c.row)
        for _, b in grp.iloc[nc:].iterrows():     # more blanks than candidates
            rows.append({"kind": "unmatched_blank", "side": "0304", "row_0304": b.row, "start_0304": s,
                         "end_0304": e})
    n_left = n_left[~n_left.row.isin(used)]

    # 3. correction (unit): same (START, END, MW) under a different unit name
    m = _pair(a_left, n_left, ["start", "end", "mw_out"])
    m = m[(m.station_0304 != m.station_0312) | (m.unit_0304 != m.unit_0312)]
    for _, r in m.iterrows():
        rows.append({"kind": "correction", "changed": "unit", "row_0304": r.row_0304, "row_0312": r.row_0312,
                     "fuel": r.fuel_0312, "station": r.station_0312, "unit": r.unit_0312,
                     "station_0304": r.station_0304, "unit_0304": r.unit_0304, "mw_0304": r.mw_out,
                     "mw_0312": r.mw_out, "start_0304": r.start, "start_0312": r.start, "end_0304": r.end,
                     "end_0312": r.end})
    n_left = n_left[~n_left.row.isin(m.row_0312)]
    a_left = a_left[~a_left.row.isin(m.row_0304)]

    # 3b. correction (unit and MW): same (START, END), different unit and MW, only where the pair is unique
    m = a_left.merge(n_left, on=["start", "end"], suffixes=("_0304", "_0312"))
    m = m[~m.row_0304.duplicated(keep=False) & ~m.row_0312.duplicated(keep=False)]
    for _, r in m.iterrows():
        changed = ["unit"] + [f for f in ("mw_out", "hsl_mw", "available_mw") if not _same(r[f + "_0304"], r[f + "_0312"])]
        rows.append({"kind": "correction", "changed": ",".join(changed), "row_0304": r.row_0304,
                     "row_0312": r.row_0312, "fuel": r.fuel_0312, "station": r.station_0312, "unit": r.unit_0312,
                     "station_0304": r.station_0304, "unit_0304": r.unit_0304, "mw_0304": r.mw_out_0304,
                     "mw_0312": r.mw_out_0312, "start_0304": r.start, "start_0312": r.start, "end_0304": r.end,
                     "end_0312": r.end})
    n_left = n_left[~n_left.row.isin(m.row_0312)]
    a_left = a_left[~a_left.row.isin(m.row_0304)]

    # 4. correction (times): same (station, unit), START moved by at most 24 h; nearest first, one-to-one
    cand = a_left.merge(n_left, on=["station", "unit"], suffixes=("_0304", "_0312"))
    cand["shift"] = (cand.start_0312 - cand.start_0304).abs()
    cand = cand[cand["shift"] <= MAX_START_SHIFT].sort_values("shift")
    ua, un = set(), set()
    for _, r in cand.iterrows():
        if r.row_0304 in ua or r.row_0312 in un:
            continue
        ua.add(r.row_0304), un.add(r.row_0312)
        changed = ["start"] + [f for f in VALUE_FIELDS if not _same(r[f + "_0304"], r[f + "_0312"])]
        rows.append({"kind": "correction", "changed": ",".join(changed), "row_0304": r.row_0304,
                     "row_0312": r.row_0312, "fuel": r.fuel_0312, "station": r.station, "unit": r.unit,
                     "mw_0304": r.mw_out_0304, "mw_0312": r.mw_out_0312, "start_0304": r.start_0304,
                     "start_0312": r.start_0312, "end_0304": r.end_0304, "end_0312": r.end_0312})
    n_left = n_left[~n_left.row.isin(un)]
    a_left = a_left[~a_left.row.isin(ua)]

    for _, r in n_left.iterrows():
        rows.append({"kind": "added_unexplained", "row_0312": r.row, "fuel": r.fuel, "station": r.station,
                     "unit": r.unit, "mw_0312": r.mw_out, "start_0312": r.start, "end_0312": r.end})
    for _, r in a_left.iterrows():
        rows.append({"kind": "dropped_unexplained", "side": "0304", "row_0304": r.row, "fuel": r.fuel,
                     "station": r.station, "unit": r.unit, "mw_0304": r.mw_out, "start_0304": r.start,
                     "end_0304": r.end})
    out = pd.DataFrame(rows)
    out["side"] = out.get("side", pd.Series(dtype=object)).fillna("0312")
    return out


def revisions() -> pd.DataFrame:
    return classify(unit_outages("2021_03_04"), unit_outages("2021_03_12"))


def summary(rev: pd.DataFrame) -> dict:
    by_kind = rev.groupby("kind").size().to_dict()
    mw = rev.assign(mw=rev.mw_0312.fillna(rev.get("mw_0304"))).groupby("kind").mw.sum().round(0).to_dict()
    by_fuel = rev.assign(fuel=rev.fuel.fillna("unknown")).pivot_table(
        index="kind", columns="fuel", values="side", aggfunc="size", fill_value=0)
    corr = rev[rev.kind == "correction"].changed.str.split(",").explode().value_counts().to_dict()
    return {"rows_by_kind": by_kind, "mw_by_kind": mw, "by_kind_and_fuel": by_fuel.to_dict(orient="index"),
            "correction_fields": corr}


if __name__ == "__main__":
    import json
    r = revisions()
    print(json.dumps(summary(r), indent=1, default=str))
