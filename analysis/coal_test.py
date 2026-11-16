"""The coal contradiction: do coal outages track grid stress, or cold at the plant?

Evidence so far (results/04_hypotheses, results/05_causal_ml):
  system time series   cold -> coal outages passes the weather-free baseline (G3)
  plant cross-section  plant-level cold doesn't explain coal outages (-0.8 pp per F, 11 units)
  minute data          933 MW of coal tripped 4 minutes after the 01:55 frequency low (grid stress)
  documents            FERC: frozen coal and fuel handling; ERCOT: weather
Hypothesis: coal outages followed grid stress and fuel logistics, which share the storm's timing but not local cold.

Test (discrete-time hazard, one row per unit-hour while the unit is not already out):
  P(new outage starts in hour h) ~ cold at the unit (F below 32, hourly zone station) + reserve margin in hour h-1
  (ERCOT available generation - demand served, Tier 0b) [+ simulated local stress from the grid replay, descriptive]
Same model for gas units as the contrast. Feb 14 00:00 - Feb 18 23:00.

    uv run python -m analysis.coal_test           # after analysis.grid_replay
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm

from analysis.cold_models import STATIONS
from analysis.grid_replay import HOURS, OUT
from kg import series as S
from kg.spatial import haversine_km, tx_counties

INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
COLOR = {"coal": "#eb6834", "gas": "#2a78d6"}


def reserve_margin() -> pd.Series:
    """Measured headroom: ERCOT available generation (15 s, hourly mean) minus demand served (EIA, hourly)."""
    avail = S.available_generation().resample("h").mean()
    served = S.eia_hourly()["demand"]
    return (avail - served).reindex(HOURS)


def county_station() -> dict:
    c = tx_counties()
    cent = {r.county: (r.geometry.centroid.x, r.geometry.centroid.y) for r in c.itertuples()}
    st = pd.DataFrame(STATIONS, index=["lat", "lon"]).T
    return {k: st.index[np.argmin(haversine_km(lo, la, st.lon.values, st.lat.values))] for k, (lo, la) in cent.items()}


def unit_hours(fuel: str) -> pd.DataFrame:
    u = S.unit_outages("2021_03_12").dropna(subset=["station"])
    u = u[u.fuel == fuel].assign(county=lambda d: d.county.str.upper().str.strip())
    temps = S.noaa_zone_temps()
    cs = county_station()
    rs = reserve_margin().shift(1)
    rows = []
    for (st, unit), g in u.groupby(["station", "unit"]):
        county = g.county.iloc[0]
        zone = cs.get(county)
        T = temps[zone].reindex(HOURS) if zone else pd.Series(np.nan, index=HOURS)
        out = np.zeros(len(HOURS), dtype=bool)
        start = np.zeros(len(HOURS), dtype=bool)
        for _, r in g.iterrows():
            covers = (HOURS + pd.Timedelta(hours=1) > r.start) & (HOURS < r.end)
            out |= covers
            i = HOURS.searchsorted(r.start.floor("h"))
            if 0 <= i < len(HOURS) and HOURS[i] == r.start.floor("h"):
                start[i] = True
        prev_out = np.r_[False, out[:-1]]
        for i, h in enumerate(HOURS):
            if prev_out[i]:
                continue                                    # not at risk while already out
            rows.append({"unit": f"{st}/{unit}", "station": st, "county": county, "zone": zone, "hour": h,
                         "event": int(start[i]), "cold_f": max(0.0, 32 - T.iloc[i]) if pd.notna(T.iloc[i]) else np.nan,
                         "reserve_lag_gw": rs.iloc[i] / 1000 if pd.notna(rs.iloc[i]) else np.nan,
                         "hours_since_storm": i})
    return pd.DataFrame(rows).dropna(subset=["cold_f", "reserve_lag_gw"])


def hazard(d: pd.DataFrame) -> dict:
    X = sm.add_constant(d[["cold_f", "reserve_lag_gw"]])
    m = sm.Logit(d.event, X).fit(disp=0, cov_type="cluster", cov_kwds={"groups": pd.factorize(d.unit)[0]})
    ci = m.conf_int()
    return {"events": int(d.event.sum()), "unit_hours": len(d), "units": int(d.unit.nunique()),
            "odds_ratio_per_F_cold": float(np.exp(m.params["cold_f"])),
            "or_cold_ci": [float(np.exp(ci.loc["cold_f", 0])), float(np.exp(ci.loc["cold_f", 1]))],
            "odds_ratio_per_GW_reserve": float(np.exp(m.params["reserve_lag_gw"])),
            "or_reserve_ci": [float(np.exp(ci.loc["reserve_lag_gw", 0])), float(np.exp(ci.loc["reserve_lag_gw", 1]))],
            "p_cold": float(m.pvalues["cold_f"]), "p_reserve": float(m.pvalues["reserve_lag_gw"])}


def event_profile(d: pd.DataFrame) -> dict:
    """Where outage starts sit in the distribution of each driver, against all at-risk unit-hours (percentiles)."""
    e = d[d.event == 1]
    pct = lambda col: float((d[col].values[None, :] < e[col].values[:, None]).mean()) if len(e) else None
    return {"median_cold_at_starts_f": float(e.cold_f.median()), "median_cold_at_risk_f": float(d.cold_f.median()),
            "median_reserve_at_starts_gw": float(e.reserve_lag_gw.median()), "median_reserve_at_risk_gw": float(d.reserve_lag_gw.median()),
            "mean_percentile_of_cold_at_starts": pct("cold_f"), "mean_percentile_of_reserve_at_starts": pct("reserve_lag_gw")}


def kg_coal_claims() -> list[dict]:
    """What the documents say about coal outages (extracted relations and claims mentioning coal)."""
    from kg.graph_build import load
    from kg.common import RESULTS
    n, e = load(RESULTS / "04_hypotheses")
    ev = n[(n.kind == "Event") & n.subtype.eq("GenerationOutage")]
    coal = ev[(ev.label.str.contains("coal", case=False, na=False)) | (ev.quote.str.contains("coal", case=False, na=False))]
    rel = e[e.kind.isin(["caused", "contributed_to"]) & e.dst.isin(coal.id)]
    src = n.set_index("id")
    out = []
    for _, r in rel.iterrows():
        out.append({"doc": r.doc_id, "modality": r.modality, "cause_type": src.subtype.get(r.src),
                    "cause": str(src.label.get(r.src))[:120], "quote": str(r.quote)[:200]})
    return out


TRIP_WINDOW = (pd.Timestamp("2021-02-15 01:50"), pd.Timestamp("2021-02-15 02:30"))
FERC_FREQ_TRIPS = {"coal": 1769, "gas": 2190}      # FERC: MW tripped by low frequency / rate of change (KG claim)


def frequency_trips() -> pd.DataFrame:
    """Coal and gas outage rows in the 3/12 list starting in the minutes around the 01:55 frequency low."""
    u = S.unit_outages("2021_03_12").dropna(subset=["station"])
    t = u[u.fuel.isin(["coal", "gas"]) & u.start.between(*TRIP_WINDOW)]
    return t.assign(county=t.county.str.upper().str.strip())


def no_trip_scenario(trips: pd.DataFrame):
    """Counterfactual availability: the tripped units' MW is back, at their county's grid generators of the same
    fuel, for as long as each stayed out (capped at each generator's size)."""
    def scen(t, avail, grid):
        live = trips[(trips.start <= t + pd.Timedelta(minutes=30)) & (trips.end > t + pd.Timedelta(minutes=30))]
        for _, r in live.iterrows():
            g = grid.gen[(grid.gen.fuel == r.fuel) & (grid.gen.county == r.county)]
            if g.empty:
                g = grid.gen[grid.gen.fuel == r.fuel]
            room = (g.pmax - avail[g.index]).clip(lower=0)
            if room.sum() > 0:
                avail[g.index] += room / room.sum() * min(r.mw_out, room.sum())
        return avail
    return scen


def frequency_counterfactual() -> dict:
    from analysis.grid_replay import run
    trips = frequency_trips()
    base = pd.read_parquet(OUT / "replay_hourly.parquet")
    cf = run(scenario=no_trip_scenario(trips), tag="no_freq_trips")["hourly"]
    diff = (base.shed_mw - cf.shed_mw)
    dur_h = ((trips.end - trips.start).dt.total_seconds() / 3600)
    by_fuel = {}
    for fuel in ("coal", "gas"):
        tf = trips[trips.fuel == fuel]
        c = run(scenario=no_trip_scenario(tf), tag=f"no_freq_trips_{fuel}")["hourly"]
        d = base.shed_mw - c.shed_mw
        by_fuel[fuel] = {"shed_mwh_avoided": float(d.sum()), "peak_mw_avoided": float(d.max()), "hours": int((d > 1).sum()),
                         "mwh_tripped_capacity": float(((tf.end.clip(upper=pd.Timestamp("2021-02-19")) - tf.start)
                                                        .dt.total_seconds() / 3600 * tf.mw_out).sum())}
    return {"units": len(trips), "mw_by_fuel": trips.groupby("fuel").mw_out.sum().to_dict(), "ferc_mw": FERC_FREQ_TRIPS,
            "median_hours_out": float(dur_h.median()), "units_detail": trips[["station", "unit", "fuel", "county", "mw_out", "start", "end"]]
            .astype(str).to_dict(orient="records"),
            "by_fuel": by_fuel, "shed_mwh_avoided": float(diff.sum()), "peak_hour_shed_avoided_mw": float(diff.max()),
            "hours_with_less_shed": int((diff > 1).sum()),
            "first_shed_hour_with_trips": str(base.shed_mw[base.shed_mw > 100].index[0]),
            "first_shed_hour_without": str(cf.shed_mw[cf.shed_mw > 100].index[0]) if (cf.shed_mw > 100).any() else None}


def no_coal_counterfactual() -> dict:
    """Every coal unit fully available all storm: how much load shed would the replay still have?"""
    from analysis.grid_replay import run
    base = pd.read_parquet(OUT / "replay_hourly.parquet")

    def no_coal(t, avail, grid):
        m = grid.gen.fuel == "coal"
        avail[m] = grid.gen.pmax[m]
        return avail
    cf = run(scenario=no_coal, tag="no_coal_outages")["hourly"]
    first = lambda s: str(s[s > 100].index[0]) if (s > 100).any() else None
    res = {"shed_gwh_replay": base.shed_mw.sum() / 1000, "shed_gwh_no_coal_outages": cf.shed_mw.sum() / 1000,
           "share_avoided": float((base.shed_mw - cf.shed_mw).sum() / base.shed_mw.sum()),
           "peak_shed_replay_mw": float(base.shed_mw.max()), "peak_shed_no_coal_mw": float(cf.shed_mw.max()),
           "shed_hours_replay": int((base.shed_mw > 100).sum()), "shed_hours_no_coal": int((cf.shed_mw > 100).sum()),
           "first_shed_replay": first(base.shed_mw), "first_shed_no_coal": first(cf.shed_mw),
           "coal_mw_restored_mean": float((cf.avail_coal - base.avail_coal).mean()),
           "coal_mw_restored_max": float((cf.avail_coal - base.avail_coal).max())}
    (OUT / "no_coal_outages.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def chart(dd: dict, rm: pd.Series, path) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.4), sharex=True, gridspec_kw={"height_ratios": [1.3, 1]}, facecolor=SURF)
    for ax in axes:
        ax.set_facecolor(SURF); ax.grid(axis="y", color=GRID, lw=0.8)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.tick_params(colors=INK2)
    a0, a1 = axes
    a0.plot(rm.index, rm / 1000, color=INK, lw=2, label="reserve margin (available − served, GW)")
    for fuel, d in dd.items():
        e = d[d.event == 1].groupby("hour").size()
        a0.scatter(e.index, np.full(len(e), -1.5 if fuel == "coal" else -3.0), s=12 + 12 * e.values, color=COLOR[fuel],
                   label=f"{fuel} outage starts", zorder=3)
    a0.axhline(0, color=INK2, lw=0.8)
    a0.set_ylabel("GW", color=INK2); a0.legend(frameon=False, fontsize=8, loc="upper right", labelcolor=INK)
    a0.set_title("Coal and gas outage starts against measured reserve margin and local cold", loc="left", color=INK, fontsize=11)
    for fuel, d in dd.items():
        c = d.groupby("hour").cold_f.mean()
        a1.plot(c.index, c, color=COLOR[fuel], lw=1.8, label=f"mean cold at {fuel} units (°F below 32)")
    a1.set_ylabel("°F below 32", color=INK2); a1.legend(frameon=False, fontsize=8, labelcolor=INK)
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(path, dpi=130, facecolor=SURF); plt.close(fig)


def main() -> dict:
    dd = {f: unit_hours(f) for f in ("coal", "gas")}
    res = {f: {"hazard": hazard(d), "profile": event_profile(d)} for f, d in dd.items()}
    rm = reserve_margin()
    res["reserve_margin_gw"] = {"min": float(rm.min() / 1000), "at_first_shed": float(rm.loc["2021-02-15 01:00"] / 1000)}
    res["kg_coal_claims"] = kg_coal_claims()
    res["frequency_counterfactual"] = frequency_counterfactual()
    (OUT / "frequency_counterfactual.json").write_text(json.dumps(res["frequency_counterfactual"], indent=1, default=str))
    res["no_coal_counterfactual"] = no_coal_counterfactual()
    chart(dd, rm, OUT / "coal_vs_stress.png")
    (OUT / "coal_test.json").write_text(json.dumps(res, indent=1, default=str))
    return res


if __name__ == "__main__":
    r = main()
    print(json.dumps({k: v for k, v in r.items() if k != "kg_coal_claims"}, indent=1, default=str))
    for c in r["kg_coal_claims"]:
        print(" KG:", c["doc"], c["modality"], c["cause_type"], "|", c["quote"][:140])
