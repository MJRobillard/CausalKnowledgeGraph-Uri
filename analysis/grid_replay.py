"""Replay Feb 14 00:00 - Feb 18 23:00 (Central) hour by hour on the synthetic Texas grid (kg.grid), with measured inputs.

Inputs per hour (ASSUMPTIONS G-items):
  demand     ERCOT estimated load without shed (Tier 0b) from 2/15 00:00, EIA demand before (no shed on 2/14);
             split by weather zone with EIA's subregion shares (2/18 reuses 2/17's shares hour by hour), then over the
             zone's buses by base-case load
  outages    ERCOT measured MW out by fuel (Tier 0b, hourly mean) for the total; placed on the grid's generators of that
             fuel by the county pattern of the 3/12 unit list at that hour (capped at each generator's size; the
             remainder spread statewide by remaining capacity)
  wind/solar after outages, scaled so the total equals EIA's actual hourly generation
  reserve    RESERVE_MW of headroom held back (total dispatch <= total available - RESERVE_MW)

    uv run python -m analysis.grid_replay                 # writes results/06_grid/
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from kg import series as S
from kg.common import EXTERNAL, RESULTS
from kg.grid import Grid, dcopf, load_grid

OUT = RESULTS / "06_grid"
HOURS = pd.date_range("2021-02-14 00:00", "2021-02-18 23:00", freq="h")
RESERVE_MW = 1000.0
EIA = EXTERNAL / "tamu" / "eia"
ZONE_COLS = {"Coast": "Coast", "East": "East", "Far West": "Far West", "North": "North", "North Central": "North Central",
             "South": "South", "South Central": "South Central", "West": "West"}
THERMAL = ["gas", "coal", "nuclear"]
VRE = ["wind", "solar"]


# ---------------------------------------------------------------- inputs
def _eia(name: str) -> pd.DataFrame:
    d = pd.read_csv(EIA / name)
    d.index = pd.to_datetime(d.time_central.str.slice(0, 19))
    return d


def total_demand() -> pd.Series:
    est = S.estimated_load()
    served = S.eia_hourly()["demand"]
    d = served.reindex(HOURS)
    d.loc[est.index.intersection(HOURS)] = est.loc[est.index.intersection(HOURS)]
    return d


def zone_shares() -> pd.DataFrame:
    z = _eia("ercot_demand_by_subregion.csv")
    z = z[[f"{k} Demand (MWh)" for k in ZONE_COLS]].set_axis(list(ZONE_COLS), axis=1)
    sh = z.div(z.sum(axis=1), axis=0).reindex(HOURS)
    for t in HOURS[sh.isna().any(axis=1)]:                 # 2/18: reuse the previous day's share at the same hour
        sh.loc[t] = sh.loc[t - pd.Timedelta(days=1)]
    return sh


def bus_demand(grid: Grid, total: float, shares: pd.Series) -> pd.Series:
    b = grid.bus
    zone_total = b.groupby("zone").pd_mw.transform("sum")
    return b.pd_mw / zone_total.replace(0, np.nan) * b.zone.map(shares).fillna(0) * total


def measured_out() -> pd.DataFrame:
    return S.minute_start(S.mw_out_by_fuel()).resample("h").mean().reindex(HOURS)


def vre_actual() -> pd.DataFrame:
    g = _eia("ercot_generation_by_source.csv")
    return pd.DataFrame({"wind": g["Wind Generation (MWh)"], "solar": g["Solar Generation (MWh)"]}).reindex(HOURS)


def county_outage_shares() -> dict:
    """{fuel: DataFrame hour x county of MW out in the 3/12 list} (largest overlapping row per unit, S3)."""
    u = S.unit_outages("2021_03_12").dropna(subset=["station"])
    u = u.assign(county=u.county.str.upper().str.strip())
    out = {}
    for fuel, g in u.groupby("fuel"):
        rows = {}
        for (st, unit), gg in g.groupby(["station", "unit"]):
            s = pd.Series(0.0, index=HOURS)
            for _, r in gg.iterrows():
                m = (HOURS + pd.Timedelta(minutes=30) >= r.start) & (HOURS + pd.Timedelta(minutes=30) < r.end)
                s[m] = np.maximum(s[m], r.mw_out)
            rows.setdefault(gg.county.iloc[0], pd.Series(0.0, index=HOURS))
            rows[gg.county.iloc[0]] = rows[gg.county.iloc[0]] + s
        out[fuel] = pd.DataFrame(rows)
    return out


def place_outages(grid: Grid, fuel: str, total_mw: float, county_mw: pd.Series) -> pd.Series:
    """MW unavailable per generator of `fuel`: total from ERCOT, geography from the unit list."""
    gens = grid.gen[grid.gen.fuel == fuel]
    out = pd.Series(0.0, index=gens.index)
    total_mw = float(min(max(total_mw, 0.0), gens.pmax.sum()))
    if total_mw <= 0:
        return out
    shares = county_mw[county_mw > 0]
    shares = shares / shares.sum() if shares.sum() > 0 else shares
    left = total_mw
    for c, sh in shares.items():
        g = gens[gens.county == c]
        if g.empty:
            continue
        want = total_mw * sh
        take = np.minimum(g.pmax / g.pmax.sum() * want, g.pmax - out[g.index])
        out[g.index] += take
        left -= take.sum()
    if left > 1e-6:                                             # rest: statewide by remaining capacity
        room = gens.pmax - out
        out += room / room.sum() * min(left, room.sum())
    return out


# ---------------------------------------------------------------- replay
def run(grid: Grid | None = None, scenario=None, out: Path = OUT, tag: str = "replay") -> dict:
    """`scenario(t, avail) -> avail` may modify availability (counterfactuals). Returns the hourly table and results."""
    grid = grid or load_grid()
    out.mkdir(parents=True, exist_ok=True)
    dem, sh, mo, vre, cos = total_demand(), zone_shares(), measured_out(), vre_actual(), county_outage_shares()
    rows, shed_rows, line_rows, lmp_rows = [], [], [], []
    for t in HOURS:
        avail = grid.gen.pmax.copy()
        for f in THERMAL + VRE:
            cm = cos[f].loc[t] if f in cos else pd.Series(dtype=float)
            avail[grid.gen.fuel == f] -= place_outages(grid, f, mo.loc[t, f], cm)
        for f in VRE:
            m = grid.gen.fuel == f
            tot = avail[m].sum()
            avail[m] = avail[m] * (min(vre.loc[t, f], tot) / tot if tot > 0 else 0.0)
        if scenario is not None:
            avail = scenario(t, avail, grid)
        # reserve: hold back RESERVE_MW of thermal headroom (scaled off flexible thermal units)
        th = grid.gen.fuel.isin(["gas", "coal"])
        if avail[th].sum() > RESERVE_MW:
            avail[th] *= (avail[th].sum() - RESERVE_MW) / avail[th].sum()
        pdb = bus_demand(grid, dem.loc[t], sh.loc[t])
        r = dcopf(grid, pdb, avail)
        if r.status != "ok":
            rows.append({"hour": t, "status": r.status}); continue
        rows.append({"hour": t, "status": "ok", "demand_mw": float(pdb.sum()), "available_mw": float(avail.sum() + (RESERVE_MW if avail[th].sum() > 0 else 0)),
                     "dispatch_mw": float(r.pg.sum()), "shed_mw": float(r.shed.sum()),
                     **{f"avail_{f}": float(avail[grid.gen.fuel == f].sum()) for f in THERMAL + VRE},
                     "binding_lines": int((r.loading >= 0.99).sum()), "lmp_max": float(r.lmp.max()),
                     "lmp_min": float(r.lmp.min()), "lmp_spread": float(r.lmp.quantile(0.95) - r.lmp.quantile(0.05))})
        sb = r.shed[r.shed > 0.5]
        shed_rows += [{"hour": t, "bus": int(b), "shed_mw": float(v)} for b, v in sb.items()]
        line_rows.append(r.loading.rename(t))
        lmp_rows.append(r.lmp.rename(t))
    hourly = pd.DataFrame(rows).set_index("hour")
    actual = S.minute_start(S.load_shed()).resample("h").mean().reindex(HOURS).fillna(0)
    hourly["actual_shed_mw"] = actual
    loading = pd.concat(line_rows, axis=1)
    lmp = pd.concat(lmp_rows, axis=1)
    hourly.to_parquet(out / f"{tag}_hourly.parquet")
    pd.DataFrame(shed_rows).to_parquet(out / f"{tag}_bus_shed.parquet", index=False)
    loading.columns = loading.columns.astype(str); loading.to_parquet(out / f"{tag}_line_loading.parquet")
    lmp.columns = lmp.columns.astype(str); lmp.to_parquet(out / f"{tag}_lmp.parquet")
    return {"hourly": hourly, "loading": loading, "lmp": lmp, "bus_shed": pd.DataFrame(shed_rows)}


def validate(h: pd.DataFrame) -> dict:
    sim, act = h.shed_mw, h.actual_shed_mw
    tamu = pd.read_csv(EXTERNAL / "tamu" / "ercot_derived" / "load_shed_simulated.csv")
    tamu.index = pd.to_datetime(tamu.time_central.str.slice(0, 19))
    tsim = tamu["sim"].reindex(HOURS)
    first = lambda s: str(s[s > 100].index[0]) if (s > 100).any() else None
    return {"shed_mwh_sim": float(sim.sum()), "shed_mwh_actual": float(act.sum()),
            "peak_shed_sim": float(sim.max()), "peak_shed_actual": float(act.max()),
            "corr_hourly": float(sim.corr(act)), "mae_mw": float((sim - act).abs().mean()),
            "first_shed_hour_sim": first(sim), "first_shed_hour_actual": first(act),
            "tamu_corr_with_actual": float(tsim.corr(act)) if tsim.notna().sum() > 10 else None,
            "tamu_shed_mwh": float(tsim.sum()) if tsim.notna().any() else None}


if __name__ == "__main__":
    res = run()
    v = validate(res["hourly"])
    (OUT / "replay_validation.json").write_text(json.dumps(v, indent=1, default=str))
    print(json.dumps(v, indent=1, default=str))
