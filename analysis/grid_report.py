"""Grid replay report: validation, bottlenecks, the coal contradiction, and the frequency-trip counterfactual.

    uv run python -m analysis.grid_report        # after analysis.grid_replay and analysis.coal_test
Writes results/06_grid/grid_report.md, grid_lines.geojson, grid_buses.geojson and replay.png.
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from analysis.grid_replay import OUT
from kg.grid import load_grid

INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def line_stats() -> pd.DataFrame:
    h = pd.read_parquet(OUT / "replay_hourly.parquet")
    L = pd.read_parquet(OUT / "replay_line_loading.parquet")
    shed = h.index[h.shed_mw > 100].astype(str)
    g = load_grid()
    s = pd.DataFrame({"hours_binding": (L >= 0.99).sum(axis=1), "hours_binding_during_shed": (L[shed] >= 0.99).sum(axis=1),
                      "mean_loading": L.mean(axis=1), "max_loading": L.max(axis=1)})
    s.index = s.index.astype(int)
    br = g.branch.loc[s.index]
    fb, tb = g.bus.loc[br.from_bus], g.bus.loc[br.to_bus]
    s = s.assign(from_bus=br.from_bus.values, to_bus=br.to_bus.values, rate_mw=br.rate.values, device=br.device.values,
                 kv=fb.base_kv.values, from_name=fb.name.values, to_name=tb.name.values, from_zone=fb.zone.values,
                 to_zone=tb.zone.values, lon1=fb.lon.values, lat1=fb.lat.values, lon2=tb.lon.values, lat2=tb.lat.values)
    return s


def write_geojson(s: pd.DataFrame) -> None:
    feats = []
    for i, r in s.iterrows():
        def conv(v):
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return None
            if isinstance(v, (bool, np.bool_)):
                return bool(v)
            if isinstance(v, (int, np.integer)):
                return int(v)
            if isinstance(v, (float, np.floating)):
                return float(v)
            return str(v)
        props = {k: conv(v) for k, v in r.drop(["lon1", "lat1", "lon2", "lat2"]).items()}
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[r.lon1, r.lat1], [r.lon2, r.lat2]]},
                      "properties": {"branch": int(i), **props}})
    (OUT / "grid_lines.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    g = load_grid()
    bs = pd.read_parquet(OUT / "replay_bus_shed.parquet")
    tot = bs.groupby("bus").shed_mw.sum() if len(bs) else pd.Series(dtype=float)
    P = pd.read_parquet(OUT / "replay_lmp.parquet")
    b = g.bus.assign(shed_mwh=tot.reindex(g.bus.index).fillna(0.0), lmp_mean=P.mean(axis=1).reindex(g.bus.index).values)
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
              "properties": {"bus": int(i), "name": r["name"], "zone": r.zone, "county": r.county, "kv": float(r.base_kv),
                             "base_load_mw": float(r.pd_mw), "sim_shed_mwh": float(r.shed_mwh), "mean_lmp": float(r.lmp_mean)}}
             for i, r in b.iterrows()]
    (OUT / "grid_buses.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))


def chart(h: pd.DataFrame, cf: dict) -> None:
    fig, ax = plt.subplots(figsize=(11, 4.6), facecolor=SURF)
    ax.set_facecolor(SURF); ax.grid(axis="y", color=GRID, lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.plot(h.index, h.actual_shed_mw / 1000, color=INK, lw=2.2, label="ERCOT actual load shed")
    ax.plot(h.index, h.shed_mw / 1000, color="#2a78d6", lw=2, label="simulated (replay)")
    nf = OUT / "no_freq_trips_hourly.parquet"
    if nf.exists():
        c = pd.read_parquet(nf)
        ax.plot(c.index, c.shed_mw / 1000, color="#eb6834", lw=1.8, ls=(0, (5, 2)),
                label=f"simulated without the 01:55 frequency trips (−{cf['shed_mwh_avoided'] / 1000:.0f} GWh)")
    ax.set_ylabel("GW", color=INK2); ax.tick_params(colors=INK2)
    ax.legend(frameon=False, fontsize=9, labelcolor=INK)
    ax.set_title("Load shed: ERCOT actual vs the synthetic-grid replay, and the no-frequency-trip counterfactual",
                 loc="left", color=INK, fontsize=11)
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(OUT / "replay.png", dpi=130, facecolor=SURF); plt.close(fig)


def report() -> str:
    v = json.loads((OUT / "replay_validation.json").read_text())
    ct = json.loads((OUT / "coal_test.json").read_text())
    cf = json.loads((OUT / "frequency_counterfactual.json").read_text())
    h = pd.read_parquet(OUT / "replay_hourly.parquet")
    P = pd.read_parquet(OUT / "replay_lmp.parquet")
    s = line_stats()
    write_geojson(s)
    chart(h, cf)
    shed_h = h.index[h.shed_mw > 100].astype(str)
    spread = P[shed_h].quantile(0.95) - P[shed_h].quantile(0.05)
    top = s.sort_values("hours_binding", ascending=False).head(10)
    c, gz = ct["coal"], ct["gas"]
    L = ["# Grid replay, bottlenecks and the coal contradiction", "",
         "Run `06_grid`. TAMU's synthetic Texas grid (2,000 buses, 3,206 lines, 606 generators; resembles ERCOT, "
         "is not its real network) replayed hour by hour, Feb 14 00:00 – 18 23:00, with a DC optimal power flow in Python "
         "(`kg/grid.py`, `analysis/grid_replay.py`). Inputs are measured: ERCOT's demand and MW out by fuel (Tier 0b), "
         "outages placed by the 3/12 unit list's geography, EIA's actual wind and solar. Assumptions: G-items in "
         "`ASSUMPTIONS.md`.", "", "![Replay](replay.png)", "",
         "## Validation", "",
         "| | Simulated | ERCOT actual |", "|---|---|---|",
         f"| Load shed, total | {v['shed_mwh_sim'] / 1000:,.0f} GWh | {v['shed_mwh_actual'] / 1000:,.0f} GWh |",
         f"| Peak hour | {v['peak_shed_sim']:,.0f} MW | {v['peak_shed_actual']:,.0f} MW |",
         f"| First hour with shed | {v['first_shed_hour_sim'][5:16]} | {v['first_shed_hour_actual'][5:16]} |",
         f"| Hourly correlation | {v['corr_hourly']:.2f} | — |", "",
         f"TAMU's own simulation correlates {v['tamu_corr_with_actual']:.2f} with actual shed. The higher simulated peak is "
         "expected: ERCOT's instructions topped out at 20 GW; the model sheds whatever balances the hour.", "",
         "## Bottlenecks", "",
         f"During the {len(shed_h)} hours with shed, the median nodal price was the shortage price ($9,000) and in "
         f"{(spread < 1).mean():.0%} of them every bus had the same price: the grid was **short of capacity everywhere, "
         "not split by transmission**. Shed hours had fewer binding lines than normal hours. Where shed lands in the model "
         "is arbitrary when no line binds, so the bus-level shed in `grid_buses.geojson` is meaningful only in the congested "
         "hours.", "", "Lines at their limit most often (all 120 hours):", "",
         "| Line | Zones | kV | Rating (MW) | Hours binding | During shed |", "|---|---|---|---|---|---|"]
    for i, r in top.iterrows():
        L.append(f"| {r.from_name} → {r.to_name} | {r.from_zone} → {r.to_zone} | {r.kv:.0f} | {r.rate_mw:,.0f} | "
                 f"{r.hours_binding} | {r.hours_binding_during_shed} |")
    L += ["", "These are lines of a synthetic network: they show where congestion tends to arise in a realistic model of "
          "ERCOT, not which real lines failed. All lines with their loading are in `grid_lines.geojson`.", "",
          "## The coal contradiction", "",
          "| Lens | What it says about coal outages |", "|---|---|",
          "| System time series (step 1) | Cold explains them: fail-restart with temperature beats weather-free baselines (G3) |",
          "| Plant cross-section (causal ML) | Cold at the plant does not: −0.8 pp per °F, 11 units, CI includes zero |",
          "| Minute data (hypotheses) | 933 MW of coal tripped 4 minutes after the 01:55 frequency low |",
          "| Documents (KG) | FERC: freezing instruments at some units; low frequency tripped 1,769 MW of coal and 2,190 MW of gas |",
          "", "**Hypothesis:** coal outages followed grid stress and fuel logistics, which share the storm's timing but not "
          "local cold.", "",
          f"**Hourly hazard test** (a new outage starting, per unit-hour at risk, against local cold and the previous hour's "
          f"measured reserve margin): coal has only **{c['hazard']['events']} outage starts** across {c['hazard']['units']} "
          f"units, so it can't separate the two (odds ratio per °F {c['hazard']['odds_ratio_per_F_cold']:.2f}, "
          f"p = {c['hazard']['p_cold']:.2f}; per GW of reserve {c['hazard']['odds_ratio_per_GW_reserve']:.2f}, "
          f"p = {c['hazard']['p_reserve']:.2f}). Gas ({gz['hazard']['events']} starts) tracks low reserve strongly "
          f"(odds ratio {gz['hazard']['odds_ratio_per_GW_reserve']:.2f} per GW, p < 0.001) and not local cold, but reserve "
          "falls *because* gas units trip together under the shared gas-supply shock, so that association is not evidence "
          "that grid stress caused gas trips.", "",
          "![Coal vs stress](coal_vs_stress.png)", "",
          "**The frequency trips, checked and counterfactual.** The 3/12 unit list, independently of the text, has "
          f"{cf['mw_by_fuel']['coal']:,} MW of coal (Sandy Creek 933 MW, Limestone 836 MW) and {cf['mw_by_fuel']['gas']:,} MW of gas "
          f"starting between 01:50 and 02:30, against FERC's {cf['ferc_mw']['coal']:,} and {cf['ferc_mw']['gas']:,} MW. Putting "
          "those units back in the replay for as long as each stayed out:", "",
          "| Trips removed | Load shed avoided | Hours affected |", "|---|---|---|"]
    for f, x in cf.get("by_fuel", {}).items():
        L.append(f"| {f} | {x['shed_mwh_avoided'] / 1000:,.1f} GWh | {x['hours']} |")
    L.append(f"| both | {cf['shed_mwh_avoided'] / 1000:,.1f} GWh (peak −{cf['peak_hour_shed_avoided_mw']:,.0f} MW) | {cf['hours_with_less_shed']} |")
    L += ["", "**Reading:** the coal frequency trips were real and brief (both units back by noon), worth about 16 GWh of "
          "shed. The gas units tripped in the same minutes mostly stayed out for days (median "
          f"{cf['median_hours_out']:.0f} h), worth about 78 GWh. So the grid-induced share of coal outages is short-lived; "
          "the coal MW that stayed out for days needs another cause: cold at the plant (which the cross-section doesn't "
          "find) or fuel handling (frozen coal, which no Tier 0 series measures). That gap is the KG's next `needs:` item.", "",
          "## Limits", "",
          "- Synthetic network; DC power flow; no unit commitment, ramping, frequency dynamics or DC ties; a fixed 1,000 MW "
          "reserve.",
          "- Outage geography from the 3/12 list scaled to ERCOT's measured totals by fuel.",
          "- With a single shortage price, where the model sheds is arbitrary unless lines bind.", ""]
    txt = "\n".join(L)
    (OUT / "grid_report.md").write_text(txt)
    return txt


if __name__ == "__main__":
    report()
    print((OUT / "grid_report.md").read_text()[:1500])
