"""Spatial visuals for Winter Storm Uri (static PNGs).

    uv run python -m analysis.maps        # after kg.spatial, analysis.causal_ml, analysis.grid_replay, analysis.coal_test

Writes results/07_maps/:
  map_cold_vs_outages_daily.png   counties shaded by cold at the daily low, plants by share of capacity out, Feb 14-19
  map_frequency_trips.png         the 12 units tripped around the 01:55 frequency low, and how long each stayed out
  map_gas_supply_chain.png        shale plays, pipelines, processing plants; gas plants by outage share (Feb 15-17)
  map_cold_sensitivity.png        per-plant causal-forest effect (MW out per F of cold), by fuel
  map_grid_congestion.png         synthetic-grid lines by hours at their limit during the replay

Encodings are shared across maps: blue = cold, orange = share of capacity out, fixed colours per fuel.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.colors import Normalize, TwoSlopeNorm
from matplotlib.patches import Polygon as MplPolygon
from shapely.geometry import shape

from kg.common import PROCESSED, ROOT, RESULTS

OUT = RESULTS / "07_maps"
GJ = PROCESSED / "spatial" / "geojson"
INK, INK2, MUTED, SURF = "#0b0b0b", "#52514e", "#8a8984", "#fcfcfb"
COUNTY_EDGE, ZONE_EDGE = "#d9d8d3", "#9a9993"
FUEL = {"gas": "#2a78d6", "coal": "#eb6834", "wind": "#1baf7a", "solar": "#eda100", "nuclear": "#e87ba4"}
EXTENT = (-106.75, -93.45, 25.75, 36.55)
ASPECT = 1 / np.cos(np.radians(31))
DAYS = pd.date_range("2021-02-14", "2021-02-19", freq="D")


# ---------------------------------------------------------------- base map
def _geojson(name: str) -> list:
    p = GJ / f"{name}.geojson"
    return json.loads(p.read_text())["features"] if p.exists() else []


def _polys(geom):
    g = shape(geom) if isinstance(geom, dict) else geom
    return [p for p in getattr(g, "geoms", [g]) if hasattr(p, "exterior")]


_CACHE = {}


def counties() -> list:
    if "c" not in _CACHE:
        _CACHE["c"] = [(f["properties"]["county"], shape(f["geometry"])) for f in _geojson("tx_counties")]
    return _CACHE["c"]


def base(ax, zones: bool = True, county_fill: dict | None = None, cmap=None, norm=None):
    ax.set_facecolor(SURF)
    patches, colors = [], []
    for name, g in counties():
        for p in _polys(g):
            patches.append(MplPolygon(np.asarray(p.exterior.coords)[:, :2], closed=True))
            colors.append(county_fill.get(name, np.nan) if county_fill is not None else np.nan)
    pc = PatchCollection(patches, edgecolor=COUNTY_EDGE, linewidth=0.25, zorder=1)
    if county_fill is not None:
        pc.set_array(np.ma.masked_invalid(np.array(colors, dtype=float)))
        pc.set_cmap(cmap); pc.set_norm(norm)
    else:
        pc.set_facecolor("#f6f5f1")
    ax.add_collection(pc)
    if zones:
        segs = []
        for f in _geojson("ercot_weather_zones"):
            for p in _polys(f["geometry"]):
                segs.append(np.asarray(p.exterior.coords)[:, :2])
        ax.add_collection(LineCollection(segs, colors=ZONE_EDGE, linewidths=0.7, zorder=2))
    ax.set_xlim(EXTENT[0], EXTENT[1]); ax.set_ylim(EXTENT[2], EXTENT[3]); ax.set_aspect(ASPECT)
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    return pc


def size_for(mw, scale=0.12, lo=4, hi=260):
    return np.clip(np.asarray(mw, dtype=float) * scale, lo, hi)


def capacity_legend(ax, values=(100, 500, 1500), scale=0.12, loc="lower left", title="capacity"):
    hs = [ax.scatter([], [], s=size_for(v, scale), facecolor="none", edgecolor=INK2, linewidth=0.8) for v in values]
    return ax.legend(hs, [f"{v:,} MW" for v in values], title=title, loc=loc, frameon=False, fontsize=7.5,
                     title_fontsize=8, labelcolor=INK2, scatterpoints=1)


def save(fig, name: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    fig.savefig(p, dpi=150, facecolor=SURF, bbox_inches="tight")
    plt.close(fig)
    return p


# ---------------------------------------------------------------- data
def plant_points() -> pd.DataFrame:
    f = pd.read_parquet(PROCESSED / "spatial" / "plant_features.parquet")
    return f.rename(columns={"Plant Code": "plant_code"})[["plant_code", "plant_name", "lat", "lon", "county"]]


def county_daily_cold() -> pd.DataFrame:
    """Daily cold (F below 32 at the daily low) at each county's centroid, by the same IDW as for plants."""
    cache = OUT / "_county_daily_cold.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    from kg.spatial import plant_daily_weather
    c = pd.DataFrame([{"Plant Code": n, "lon": g.centroid.x, "lat": g.centroid.y} for n, g in counties()])
    w = plant_daily_weather(c).rename(columns={"plant_code": "county"})
    OUT.mkdir(parents=True, exist_ok=True)
    w.to_parquet(cache, index=False)
    return w


def plant_daily_share() -> pd.DataFrame:
    p = pd.read_parquet(RESULTS / "05_causal_ml" / "panel.parquet")
    g = p.groupby(["plant_code", "day"]).agg(mwh=("mwh_out", "sum"), cap=("capacity_mw", "sum"),
                                             fuel=("fuel", lambda s: s.value_counts().index[0])).reset_index()
    g["share"] = (g.mwh / (g.cap * 24)).clip(0, 1)
    return g.merge(plant_points(), on="plant_code", how="left")


# ---------------------------------------------------------------- 1. cold vs outages, daily
def map_cold_vs_outages() -> Path:
    cold = county_daily_cold()
    share = plant_daily_share()
    fig, axes = plt.subplots(2, 3, figsize=(15, 10.6), facecolor=SURF)
    cnorm, snorm = Normalize(0, 30), Normalize(0, 1)
    for ax, d in zip(axes.ravel(), DAYS):
        cd = cold[cold.day == d].set_index("county").cold_f.to_dict()
        pc = base(ax, county_fill=cd, cmap="Blues", norm=cnorm)
        s = share[share.day == d].sort_values("share")
        sc = ax.scatter(s.lon, s.lat, s=size_for(s.cap), c=s.share, cmap="Oranges", norm=snorm,
                        edgecolor=INK2, linewidth=0.35, zorder=4)
        mw_out = (s.mwh.sum() / 24) / 1000
        ax.set_title(f"{d:%a %b %d}", loc="left", color=INK, fontsize=11)
        ax.text(0.98, 0.98, f"{mw_out:,.1f} GW out\n(panel plants, daily mean)", transform=ax.transAxes, color=INK2,
                fontsize=8, ha="right", va="top")
    cb1 = fig.colorbar(pc, ax=axes, orientation="horizontal", fraction=0.025, pad=0.03, aspect=50, location="bottom")
    cb1.set_label("county: °F below freezing at the daily low (interpolated from 758 stations)", color=INK2, fontsize=9)
    cb2 = fig.colorbar(sc, ax=axes, orientation="vertical", fraction=0.015, pad=0.01)
    cb2.set_label("plant: share of capacity out that day", color=INK2, fontsize=9)
    capacity_legend(axes[0, 0], loc="upper left")
    fig.suptitle("Where the cold went, and which plants failed under it: Feb 14–19, 2021", x=0.01, ha="left",
                 color=INK, fontsize=15, y=0.98)
    fig.text(0.01, 0.945, "ERCOT weather zones outlined. Plants: 329 EIA-860 plant-fuel units in the causal-ML panel "
             "(including 118 that never failed), sized by capacity.", color=INK2, fontsize=9.5)
    return save(fig, "map_cold_vs_outages_daily.png")


# ---------------------------------------------------------------- 2. frequency trips
def map_frequency_trips() -> Path:
    from analysis.coal_test import frequency_trips
    t = frequency_trips().copy()
    m = pd.read_parquet(PROCESSED / "fleet" / "station_matches.parquet")
    t = t.merge(m[["station", "fuel", "plant_fuel_id", "station_name"]], on=["station", "fuel"], how="left", suffixes=("", "_m"))
    t["plant_code"] = pd.to_numeric(t.plant_fuel_id.str.split(":").str[0], errors="coerce")
    pts = plant_points().set_index("plant_code")
    cent = {n: (g.centroid.x, g.centroid.y) for n, g in counties()}
    t["lon"] = [pts.lon.get(pc, cent.get(c, (np.nan, np.nan))[0]) if pd.notna(pc) else cent.get(c, (np.nan, np.nan))[0]
                for pc, c in zip(t.plant_code, t.county)]
    t["lat"] = [pts.lat.get(pc, cent.get(c, (np.nan, np.nan))[1]) if pd.notna(pc) else cent.get(c, (np.nan, np.nan))[1]
                for pc, c in zip(t.plant_code, t.county)]
    t["hours_out"] = (t.end - t.start).dt.total_seconds() / 3600
    fig = plt.figure(figsize=(15, 7.6), facecolor=SURF)
    ax = fig.add_axes([0.0, 0.02, 0.47, 0.82]); base(ax)
    by_site = t.groupby(["station", "fuel"]).agg(mw=("mw_out", "sum"), lon=("lon", "first"), lat=("lat", "first"),
                                                  name=("station_name", "first"), hours=("hours_out", "median")).reset_index()
    offsets = {"SCES": (-12, 14, "right"), "LEG": (12, -16, "left"), "CBECII": (-14, 10, "right"),
               "DOWGEN": (10, -18, "left"), "SL": (-14, 16, "right"), "CCEC": (12, -4, "left"), "DECKER": (-12, 4, "right"),
               "JACKCNTY": (10, 6, "left")}
    for _, r in by_site.iterrows():
        ax.scatter(r.lon, r.lat, s=size_for(r.mw, 0.35, 30, 500), marker="s" if r.fuel == "coal" else "o",
                   color=FUEL[r.fuel], edgecolor=INK, linewidth=0.8, zorder=5, alpha=0.9)
        dx, dy, ha = offsets.get(r.station, (8, 5, "left"))
        ax.annotate(f"{(r['name'] or r.station).title()}\n{r.mw:,.0f} MW · out {r.hours:.0f} h", (r.lon, r.lat),
                    xytext=(dx, dy), textcoords="offset points", fontsize=7.5, color=INK, zorder=6, ha=ha,
                    arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.5))
    ax.scatter([], [], marker="s", color=FUEL["coal"], edgecolor=INK, label="coal"); ax.scatter([], [], marker="o", color=FUEL["gas"], edgecolor=INK, label="gas")
    ax.legend(frameon=False, loc="lower left", fontsize=9, labelcolor=INK)
    ax.set_title("Units that tripped 01:50–02:30 on Feb 15 (ERCOT 3/12 unit list)", loc="left", color=INK, fontsize=11)
    g = fig.add_axes([0.60, 0.12, 0.39, 0.74]); g.set_facecolor(SURF)
    t = t.sort_values(["fuel", "start"], ascending=[True, True]).reset_index(drop=True)     # coal first
    t0, t1 = pd.Timestamp("2021-02-15 00:00"), pd.Timestamp("2021-02-19 00:00")
    for i, r in t.iterrows():
        g.barh(i, (min(r.end, t1) - r.start) / pd.Timedelta(hours=1), left=(r.start - t0) / pd.Timedelta(hours=1),
               height=0.62, color=FUEL[r.fuel])
        g.text(-1.5, i, f"{r.station}/{r.unit}  {r.mw_out:,.0f} MW", ha="right", va="center", fontsize=8, color=INK)
        if r.end > t1:
            g.text((t1 - t0) / pd.Timedelta(hours=1) + 0.5, i, "→", va="center", fontsize=9, color=INK2)
    g.axvline((pd.Timestamp("2021-02-15 01:55") - t0) / pd.Timedelta(hours=1), color=INK, lw=1, ls=(0, (4, 3)))
    g.set_yticks([]); g.invert_yaxis()
    ticks = np.arange(0, 97, 12)
    g.set_xticks(ticks, [(t0 + pd.Timedelta(hours=int(h))).strftime("%b %d\n%H:%M") for h in ticks], fontsize=8, color=INK2)
    g.set_xlim(0, 97)
    for s in ("top", "right", "left"):
        g.spines[s].set_visible(False)
    g.set_title("How long each unit stayed out (dashed: 01:55 frequency low, 59.302 Hz)", loc="left", color=INK, fontsize=11)
    coal_mw, gas_mw = t[t.fuel == "coal"].mw_out.sum(), t[t.fuel == "gas"].mw_out.sum()
    fig.suptitle(f"The 01:55 frequency trips: {coal_mw:,.0f} MW of coal came back within hours; {gas_mw:,.0f} MW of gas "
                 "mostly stayed out for days", x=0.01, ha="left", color=INK, fontsize=14, y=0.99)
    fig.text(0.01, 0.935, "FERC: 1,769 MW coal and 2,190 MW gas tripped on low frequency. UT Austin: 'up to 1,800 MW … "
             "mostly coal'. The unit list matches FERC.", color=INK2, fontsize=9.5)
    return save(fig, "map_frequency_trips.png")


# ---------------------------------------------------------------- 3. gas supply chain
def map_gas_supply_chain() -> Path:
    share = plant_daily_share()
    gas = share[(share.fuel == "gas") & share.day.between("2021-02-15", "2021-02-17")]
    gas = gas.groupby("plant_code").agg(mwh=("mwh", "sum"), cap=("cap", "first"), lon=("lon", "first"), lat=("lat", "first")).reset_index()
    gas["share"] = (gas.mwh / (gas.cap * 72)).clip(0, 1)
    fig, ax = plt.subplots(figsize=(12, 10.5), facecolor=SURF)
    base(ax)
    plays = _geojson("shale_plays")
    pp = [MplPolygon(np.asarray(p.exterior.coords)[:, :2], closed=True) for f in plays for p in _polys(f["geometry"])]
    ax.add_collection(PatchCollection(pp, facecolor="#f1dfb4", edgecolor="#d8c08a", linewidth=0.5, alpha=0.75, zorder=2))
    from shapely.ops import unary_union
    names = {"Permian": "Permian Basin", "Ft. Worth": "Barnett (Fort Worth Basin)", "Western Gulf": "Eagle Ford",
             "TX-LA-MS Salt Basin": "Haynesville", "Anadarko": "Anadarko"}
    by_basin = {}
    for f in plays:
        b = f["properties"].get("Basin")
        if b in names:
            by_basin.setdefault(b, []).append(shape(f["geometry"]))
    for b, gs in by_basin.items():
        c = unary_union(gs).representative_point()
        if EXTENT[0] < c.x < EXTENT[1] and EXTENT[2] < c.y < EXTENT[3]:
            ax.text(c.x, c.y, names[b], fontsize=9, color="#7a5c1c", ha="center", zorder=6, style="italic",
                    weight="bold", alpha=0.9)
    segs = []
    for f in _geojson("gas_pipelines"):
        geom = f.get("geometry")
        if not geom:
            continue
        g = shape(geom)
        for ln in getattr(g, "geoms", [g]):
            segs.append(np.asarray(ln.coords)[:, :2])
    ax.add_collection(LineCollection(segs, colors="#7d7c77", linewidths=0.25, alpha=0.45, zorder=3))
    proc = [f for f in _geojson("gas_processing_plants") if f.get("geometry")]
    px = np.array([f["geometry"]["coordinates"][0] for f in proc]); py = np.array([f["geometry"]["coordinates"][1] for f in proc])
    pcap = np.array([float(f["properties"].get("Cap_MMcfd") or 0) for f in proc])
    ax.scatter(px, py, s=np.clip(pcap / 6, 6, 160), marker="^", facecolor="#6b5a2e", edgecolor="none", alpha=0.75,
               zorder=4, label="gas processing plant (size: MMcf/d)")
    gas = gas.sort_values("share")
    sc = ax.scatter(gas.lon, gas.lat, s=size_for(gas.cap), c=gas.share, cmap="Oranges", norm=Normalize(0, 1),
                    edgecolor=INK, linewidth=0.5, zorder=5)
    cb = fig.colorbar(sc, ax=ax, fraction=0.03, pad=0.01); cb.set_label("gas plant: share of capacity out, Feb 15–17", color=INK2)
    ax.plot([], [], color="#7d7c77", lw=1, label="gas pipeline (EIA)")
    ax.add_patch(MplPolygon([[0, 0]], facecolor="#f1dfb4", edgecolor="#d8c08a", label="shale / tight-gas play"))
    ax.legend(frameon=False, loc="lower left", fontsize=8.5, labelcolor=INK)
    ax.set_title("The gas supply chain behind ERCOT's gas fleet: plays, pipelines, processing, and the plants that failed",
                 loc="left", color=INK, fontsize=12.5)
    ax.text(0.0, -0.035, "Gas supply → gas-plant outages is contested (FERC asserts, Enverus denies); power loss at gas "
            "facilities → gas supply is the best-documented edge and untestable: no public series meters power to these sites.",
            transform=ax.transAxes, color=INK2, fontsize=8.5, wrap=True)
    return save(fig, "map_gas_supply_chain.png")


# ---------------------------------------------------------------- 4. cold sensitivity
def map_cold_sensitivity() -> Path:
    u = pd.read_parquet(RESULTS / "05_causal_ml" / "unit_cate.parquet")
    groups = [("gas", ["gas"]), ("wind", ["wind"]), ("coal, solar, nuclear", ["coal", "solar", "nuclear"])]
    lim = float(np.nanpercentile(np.abs(u.cate_mw_per_F), 95))
    norm = TwoSlopeNorm(vmin=-lim, vcenter=0, vmax=lim)
    fig, axes = plt.subplots(1, 3, figsize=(17, 6.6), facecolor=SURF)
    for ax, (title, fuels) in zip(axes, groups):
        base(ax)
        d = u[u.fuel.isin(fuels)].sort_values("cate_mw_per_F", key=np.abs)
        markers = {"gas": "o", "wind": "o", "coal": "s", "solar": "D", "nuclear": "*"}
        for f in fuels:
            x = d[d.fuel == f]
            sc = ax.scatter(x.lon, x.lat, s=size_for(x.capacity_mw), c=x.cate_mw_per_F, cmap="PuOr_r", norm=norm,
                            marker=markers[f], edgecolor=INK2, linewidth=0.4, zorder=4, label=f)
        tot = d.cate_mw_per_F.sum()
        ax.set_title(f"{title}: {len(d)} units, {tot:,.0f} MW per °F in total", loc="left", color=INK, fontsize=11)
        if len(fuels) > 1:
            ax.legend(frameon=False, loc="lower left", fontsize=8, labelcolor=INK)
    cb = fig.colorbar(sc, ax=axes, fraction=0.02, pad=0.01)
    cb.set_label("MW out per °F below freezing (causal forest, per plant)", color=INK2)
    fig.suptitle("Which plants were most sensitive to local cold? Causal-forest effects with day fixed effects",
                 x=0.01, ha="left", color=INK, fontsize=14, y=1.0)
    fig.text(0.01, 0.94, "Effects identified from plants that saw different cold on the same day. Only wind is clearly "
             "non-zero on average (1.14 pp per °F); per-plant intervals are wide. Orange = more outage with cold.",
             color=INK2, fontsize=9.5)
    return save(fig, "map_cold_sensitivity.png")


# ---------------------------------------------------------------- 5. synthetic-grid congestion
def map_grid_congestion() -> Path:
    lines = json.loads((RESULTS / "06_grid" / "grid_lines.geojson").read_text())["features"]
    fig, ax = plt.subplots(figsize=(12, 10.5), facecolor=SURF)
    base(ax)
    all_segs = [np.asarray(f["geometry"]["coordinates"]) for f in lines]
    ax.add_collection(LineCollection(all_segs, colors="#c9c8c2", linewidths=0.35, zorder=3))
    hot = sorted([f for f in lines if f["properties"]["hours_binding"] > 0], key=lambda f: f["properties"]["hours_binding"])
    segs = [np.asarray(f["geometry"]["coordinates"]) for f in hot]
    hrs = np.array([f["properties"]["hours_binding"] for f in hot])
    kv = np.array([f["properties"]["kv"] or 115 for f in hot])
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("congestion", plt.get_cmap("RdPu")(np.linspace(0.35, 1.0, 256)))
    norm = Normalize(1, max(hrs.max(), 2))
    lc = LineCollection(segs, cmap=cmap, norm=norm, linewidths=np.clip(kv / 80, 1.8, 4.5), zorder=4)
    lc.set_array(hrs); ax.add_collection(lc)
    mids = np.array([s_.mean(axis=0) for s_ in segs])
    ax.scatter(mids[:, 0], mids[:, 1], s=8 + hrs * 2.2, c=hrs, cmap=cmap, norm=norm, edgecolor=INK, linewidth=0.5,
               zorder=5, alpha=0.9)
    cb = fig.colorbar(lc, ax=ax, fraction=0.03, pad=0.01); cb.set_label("hours at its thermal limit, Feb 14–18 (of 120)", color=INK2)
    ax.scatter([], [], s=8 + 50 * 2.2, color=cmap(0.6), edgecolor=INK, label="congested line (dot size: hours at limit)")
    ax.legend(frameon=False, loc="lower left", fontsize=8.5, labelcolor=INK)
    label_at = {"PASADENA": (-95.6, 28.55, "right"), "ROCKDALE": (-95.55, 31.55, "left"),
                "MARBLE FALLS": (-99.7, 31.75, "right"), "LA GRANGE": (-95.55, 30.65, "left"),
                "SHINER": (-98.7, 29.75, "right"), "ODESSA": (-102.7, 31.15, "right"),
                "SINTON": (-98.3, 27.6, "right"), "SAN YGNACIO": (-99.4, 26.55, "right")}
    for f in hot[-8:]:
        pr = f["properties"]
        c = np.asarray(f["geometry"]["coordinates"]).mean(axis=0)
        key = next((k for k in label_at if pr["from_name"].upper().startswith(k)), None)
        xt, yt, ha = label_at.get(key, (c[0] + 0.4, c[1] + 0.3, "left"))
        ax.annotate(f"{pr['from_name'].title()} – {pr['to_name'].title()}\n{pr['hours_binding']} h at limit · {pr['kv']:.0f} kV",
                    c, xytext=(xt, yt), textcoords="data", fontsize=7.5, color=INK, zorder=7, ha=ha, va="center",
                    arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.6))
    ax.set_title("Synthetic Texas grid: where lines hit their limits during the replay", loc="left", color=INK, fontsize=12.5)
    ax.text(0.0, -0.035, "TAMU / Breakthrough Energy synthetic network (2,000 buses), DC power flow, hourly. It resembles "
            "ERCOT but is not its real network. During shedding, 64% of hours had one shortage price statewide: the shortfall "
            "was capacity, not transmission.", transform=ax.transAxes, color=INK2, fontsize=8.5, wrap=True)
    return save(fig, "map_grid_congestion.png")


def main() -> list[Path]:
    return [map_cold_vs_outages(), map_frequency_trips(), map_gas_supply_chain(), map_cold_sensitivity(), map_grid_congestion()]


if __name__ == "__main__":
    for p in main():
        print(p.relative_to(ROOT), f"{p.stat().st_size / 1e6:.1f} MB")
