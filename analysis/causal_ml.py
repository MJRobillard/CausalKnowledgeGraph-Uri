"""Causal ML on the plant x fuel x day panel: does cold at the plant cause outages, how much, for which plants, and
what would have happened in a warmer storm?

Design (ASSUMPTIONS M-items):
  unit       EIA-860 ERCOT plant x fuel (gas, coal, wind, solar, nuclear), days Feb 10-19
  outcome    share of capacity out that day = MWh out (ERCOT 3/12 list, matched stations) / (capacity x 24), in [0, 1]
  treatment  cold_f = degrees F below freezing at the plant's daily low (inverse-distance weighted stations, kg.spatial)
  controls   day fixed effects (statewide shocks: load shed, gas supply, frequency events), cold accumulated before the
             day, plant features; so the effect is identified from plants that saw different cold on the same day
  models     LinearDML (average effect, plant-clustered SE on the final stage; per-fuel effects), CausalForestDML
             (per-plant effects), within-day permutation placebo
  counterfactuals  warmer storm (+5, +10 F), cold-sensitivity map, gas supply vs plant cold, winterization proxy

    uv run python -m analysis.causal_ml          # after kg.fleet and kg.spatial
Outputs in results/05_causal_ml/.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from econml.dml import CausalForestDML, LinearDML
from sklearn.ensemble import HistGradientBoostingRegressor

from kg import series as S
from kg.common import PROCESSED, RESULTS
from kg.spatial import IDW_K, IDW_MAX_KM, daily_temps, haversine_km, stations, tx_counties

warnings.filterwarnings("ignore", category=UserWarning)
OUT = RESULTS / "05_causal_ml"
FLEET = PROCESSED / "fleet"
SPATIAL = PROCESSED / "spatial"
FUELS = ["gas", "coal", "wind", "solar", "nuclear"]
DAYS = pd.date_range("2021-02-10", "2021-02-19", freq="D")
PEAK_DAY = pd.Timestamp("2021-02-16")
SEED = 20210215
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
FUEL_COLOR = {"gas": "#2a78d6", "coal": "#eb6834", "wind": "#1baf7a", "solar": "#eda100", "nuclear": "#e87ba4"}
X_COLS = ["log_capacity", "age_years", "lat", "lon", "dist_coast_km", "dist_load_centre_km", "dist_nearest_basin_km",
          "dist_permian_km", "dist_pipeline_km", "dist_processing_km", "dist_transmission_230kv_km"]


# ---------------------------------------------------------------- panel
def build_panel() -> tuple[pd.DataFrame, dict]:
    pf = pd.read_parquet(FLEET / "eia860_ercot_plant_fuel.parquet")
    pf = pf[pf.fuel.isin(FUELS) & (pf.capacity_mw > 0)].copy()
    m = pd.read_parquet(FLEET / "station_matches.parquet")
    daily = pd.read_parquet(FLEET / "station_daily_mwh_out.parquet")
    # An unmatched ERCOT station's outages belong to some EIA plant of that fuel in its county. Plants there that have
    # no matched station of their own might be that plant, so their zero is unknown: leave them out (M3).
    unm = m[m.match.isna()].assign(county_key=lambda d: d.county.str.upper().str.strip())
    risky = set(zip(unm.county_key, unm.fuel))
    matched_ids = set(m.plant_fuel_id.dropna())
    drop = [(c, f) in risky and pid not in matched_ids for c, f, pid in zip(pf.county_key, pf.fuel, pf.plant_fuel_id)]
    excluded = pf[drop]
    pf = pf[[not d for d in drop]]
    out = daily.dropna(subset=["plant_fuel_id"]).groupby(["plant_fuel_id", "day"]).mwh_out.sum()
    feats = pd.read_parquet(SPATIAL / "plant_features.parquet").rename(columns={"Plant Code": "plant_code"})
    wx = pd.read_parquet(SPATIAL / "plant_daily_weather.parquet").sort_values(["plant_code", "day"])
    wx["cold_before_f"] = wx.groupby("plant_code").cold_f.transform(lambda s: s.shift(1).fillna(0).cumsum())
    rows = []
    for _, r in pf.iterrows():
        for d in DAYS:
            rows.append({"plant_fuel_id": r.plant_fuel_id, "plant_code": r["Plant Code"], "fuel": r.fuel,
                         "capacity_mw": r.capacity_mw, "age_years": r.age_years, "day": d,
                         "mwh_out": float(out.get((r.plant_fuel_id, d), 0.0))})
    p = pd.DataFrame(rows)
    p["share_out"] = (p.mwh_out / (p.capacity_mw * 24)).clip(0, 1)
    p = p.merge(wx[["plant_code", "day", "cold_f", "tmin_f", "cold_before_f", "nearest_station_km", "fallback"]],
                on=["plant_code", "day"], how="left")
    keep = ["plant_code"] + [c for c in feats.columns if c not in ("plant_code", "plant_name", "county", "fuels", "capacity_mw")]
    p = p.merge(feats[keep], on="plant_code", how="left")
    p["log_capacity"] = np.log(p.capacity_mw)
    p = p.dropna(subset=["cold_f"])
    info = {"plant_fuel_units": int(p.plant_fuel_id.nunique()), "rows": len(p),
            "units_by_fuel": p.drop_duplicates("plant_fuel_id").fuel.value_counts().to_dict(),
            "units_with_any_outage": int(p.groupby("plant_fuel_id").mwh_out.sum().gt(0).sum()),
            "excluded_for_unmatched_station": int(len(excluded)),
            "excluded_capacity_mw": float(excluded.capacity_mw.sum())}
    return p, info


def reconcile(p: pd.DataFrame) -> pd.DataFrame:
    """Daily mean MW out by fuel: panel (matched plants) vs all 3/12 stations vs ERCOT's measured MW out (Tier 0b)."""
    panel = p.groupby(["day", "fuel"]).mwh_out.sum().div(24).unstack()
    allst = pd.read_parquet(FLEET / "station_daily_mwh_out.parquet").groupby(["day", "fuel"]).mwh_out.sum().div(24).unstack()
    ercot = S.minute_start(S.mw_out_by_fuel()).resample("D").mean()
    ercot = ercot.loc["2021-02-14":"2021-02-19"]
    rows = []
    for f in FUELS:
        for d in ercot.index:
            rows.append({"fuel": f, "day": d.date(), "ercot_measured_mw": ercot.loc[d, f],
                         "unit_list_all_mw": allst.get(f, pd.Series(dtype=float)).get(d, 0.0),
                         "panel_matched_mw": panel.get(f, pd.Series(dtype=float)).get(d, 0.0)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- models
def design(p: pd.DataFrame, fuel_dummies: bool = True):
    X = p[X_COLS].copy()
    X = X.fillna(X.median())
    if fuel_dummies:
        X = pd.concat([X, pd.get_dummies(p.fuel, prefix="fuel", dtype=float)], axis=1)
    W = pd.concat([pd.get_dummies(p.day.dt.strftime("d%m%d"), dtype=float), p[["cold_before_f"]]], axis=1)
    return X, W


def nuisance():
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, min_samples_leaf=20, random_state=SEED)


def clustered_ate(est, groups) -> dict:
    """Final-stage OLS of residualized Y on residualized T with plant-clustered standard errors."""
    ry, rt = est.residuals_[0], est.residuals_[1]
    ry, rt = np.ravel(ry), np.ravel(rt)
    fit = sm.OLS(ry, rt).fit(cov_type="cluster", cov_kwds={"groups": groups})
    lo, hi = fit.conf_int()[0]
    return {"ate": float(fit.params[0]), "se_cluster": float(fit.bse[0]), "ci95": [float(lo), float(hi)],
            "n": int(len(ry)), "clusters": int(pd.Series(groups).nunique())}


def fit_linear(p: pd.DataFrame, X=None, cache=True):
    Xd, W = design(p)
    est = LinearDML(model_y=nuisance(), model_t=nuisance(), cv=5, random_state=SEED)
    est.fit(p.share_out.values, p.cold_f.values, X=X if X is not None else None,
            W=pd.concat([Xd, W], axis=1).values, groups=p.plant_code.values, cache_values=cache)
    return est


def per_fuel_effects(p: pd.DataFrame) -> pd.DataFrame:
    Xf = pd.get_dummies(p.fuel, dtype=float)[FUELS]
    Xd, W = design(p, fuel_dummies=False)
    est = LinearDML(model_y=nuisance(), model_t=nuisance(), cv=5, random_state=SEED, fit_cate_intercept=False)
    est.fit(p.share_out.values, p.cold_f.values, X=Xf.values, W=pd.concat([Xd, W], axis=1).values,
            groups=p.plant_code.values)
    eye = np.eye(len(FUELS))
    eff = est.effect(eye)
    lo, hi = est.effect_interval(eye, alpha=0.05)
    cap = p.drop_duplicates("plant_fuel_id").groupby("fuel").capacity_mw.sum()
    return pd.DataFrame({"fuel": FUELS, "effect_share_per_F": eff, "ci_low": lo, "ci_high": hi,
                         "fleet_capacity_mw": [cap.get(f, 0) for f in FUELS],
                         "mw_per_F_fleet": eff * np.array([cap.get(f, 0) for f in FUELS])})


def placebo(p: pd.DataFrame, n: int = 20) -> dict:
    """Permute cold across plants within each day (keeps the day pattern, breaks the plant-level link)."""
    rng = np.random.default_rng(SEED)
    ates = []
    for _ in range(n):
        q = p.copy()
        q["cold_f"] = q.groupby("day").cold_f.transform(lambda s: rng.permutation(s.values))
        ates.append(clustered_ate(fit_linear(q), q.plant_code.values)["ate"])
    return {"n": n, "mean": float(np.mean(ates)), "sd": float(np.std(ates)), "draws": ates}


def fit_forest(p: pd.DataFrame):
    X, W = design(p)
    est = CausalForestDML(model_y=nuisance(), model_t=nuisance(), cv=5, n_estimators=1000, min_samples_leaf=20,
                          max_samples=0.45, random_state=SEED, inference=True)
    est.fit(p.share_out.values, p.cold_f.values, X=X.values, W=W.values, groups=p.plant_code.values)
    return est, X


# ---------------------------------------------------------------- counterfactuals
def unit_cate(p: pd.DataFrame, est, X) -> pd.DataFrame:
    """Per plant x fuel: the forest's effect at the unit's own features (features don't vary by day)."""
    first = p.drop_duplicates("plant_fuel_id").index
    eff = est.effect(X.loc[first].values)
    lo, hi = est.effect_interval(X.loc[first].values, alpha=0.05)
    u = p.loc[first, ["plant_fuel_id", "plant_code", "fuel", "capacity_mw", "lat", "lon", "age_years"]].copy()
    u["cate_share_per_F"], u["cate_ci_low"], u["cate_ci_high"] = eff, lo, hi
    u["cate_mw_per_F"] = u.cate_share_per_F * u.capacity_mw
    return u.reset_index(drop=True)


def warmer(p: pd.DataFrame, u: pd.DataFrame, deltas=(5, 10)) -> pd.DataFrame:
    """MW out avoided per day and fuel if every plant had seen `delta` F less cold (cold can't go below zero).
    Linear in the local effect: an extrapolation, not a simulation of the grid."""
    q = p.merge(u[["plant_fuel_id", "cate_share_per_F", "cate_ci_low", "cate_ci_high"]], on="plant_fuel_id")
    rows = []
    for dlt in deltas:
        reduction = q.cold_f - (q.cold_f - dlt).clip(lower=0)
        for col, lab in (("cate_share_per_F", "mw"), ("cate_ci_low", "mw_low"), ("cate_ci_high", "mw_high")):
            q[f"{lab}_{dlt}"] = q[col] * reduction * q.capacity_mw
        g = q.groupby(["day", "fuel"])[[f"mw_{dlt}", f"mw_low_{dlt}", f"mw_high_{dlt}"]].sum().reset_index()
        g["delta_F"] = dlt
        g = g.rename(columns={f"mw_{dlt}": "mw_avoided", f"mw_low_{dlt}": "mw_avoided_low", f"mw_high_{dlt}": "mw_avoided_high"})
        rows.append(g)
    out = pd.concat(rows)
    actual = p.groupby(["day", "fuel"]).mwh_out.sum().div(24).rename("mw_out_actual").reset_index()
    return out.merge(actual, on=["day", "fuel"])


def winterization(p: pd.DataFrame, u: pd.DataFrame) -> dict:
    """If the most cold-sensitive quarter of each fuel (CATE above its 75th percentile) had the fuel's median CATE."""
    res = {}
    q = p[p.day == PEAK_DAY].merge(u, on=["plant_fuel_id", "fuel", "capacity_mw", "plant_code"], suffixes=("", "_u"))
    for f, g in q.groupby("fuel"):
        q75, med = g.cate_share_per_F.quantile(0.75), g.cate_share_per_F.median()
        top = g[g.cate_share_per_F > q75]
        saved = float(((top.cate_share_per_F - med) * top.cold_f * top.capacity_mw).sum())
        res[f] = {"units_in_top_quarter": int(len(top)), "q75": float(q75), "median": float(med),
                  "mw_saved_peak_day": saved, "actual_mw_out_peak_day": float(g.mwh_out.sum() / 24)}
    return res


def basin_daily_cold(feats: pd.DataFrame) -> pd.DataFrame:
    """Daily cold at each major play's centroid (IDW over stations, as for plants)."""
    info = json.loads((SPATIAL / "spatial_report.json").read_text())
    cents = info.get("basin_centroids", {})
    t = daily_temps().join(stations()[["lat", "lon"]], on="station").dropna(subset=["lat"])
    rows = []
    for day, g in t.groupby("day"):
        for b, (lo, la) in cents.items():
            d = haversine_km(lo, la, g.lon.values, g.lat.values)
            o = np.argsort(d)
            near = o[d[o] <= IDW_MAX_KM * 2][:IDW_K] if (d <= IDW_MAX_KM * 2).any() else o[:1]
            w = 1 / np.maximum(d[near], 1) ** 2
            rows.append({"basin": b, "day": day, "basin_cold_f": max(0.0, 32 - float(np.sum(w * g.TMIN.values[near]) / w.sum()))})
    return pd.DataFrame(rows)


def gas_supply_vs_plant(p: pd.DataFrame) -> dict:
    """Gas plants: two treatments, cold at the plant and cold at the nearest major play (the supply-side proxy)."""
    g = p[p.fuel == "gas"].copy()
    bc = basin_daily_cold(g)
    g = g.merge(bc, left_on=["nearest_basin", "day"], right_on=["basin", "day"], how="left").dropna(subset=["basin_cold_f"])
    Xd, W = design(g, fuel_dummies=False)
    T = g[["cold_f", "basin_cold_f"]].values
    from sklearn.multioutput import MultiOutputRegressor
    est = LinearDML(model_y=nuisance(), model_t=MultiOutputRegressor(nuisance()), cv=5, random_state=SEED)
    est.fit(g.share_out.values, T, X=None, W=pd.concat([Xd, W], axis=1).values, groups=g.plant_code.values,
            cache_values=True)
    coef = np.ravel(est.const_marginal_effect())
    lo, hi = est.const_marginal_effect_interval(alpha=0.05)
    rt = est.residuals_[1]
    within_corr = float(np.corrcoef(rt[:, 0], rt[:, 1])[0, 1])
    contrib = {k: float((coef[i] * g[c] * g.capacity_mw).sum() / 24) for i, (k, c) in
               enumerate((("plant_cold", "cold_f"), ("basin_cold", "basin_cold_f")))}
    return {"effect_share_per_F": {"plant_cold": float(coef[0]), "basin_cold": float(coef[1])},
            "ci95": {"plant_cold": [float(np.ravel(lo)[0]), float(np.ravel(hi)[0])],
                     "basin_cold": [float(np.ravel(lo)[1]), float(np.ravel(hi)[1])]},
            "residual_treatment_correlation": within_corr,
            "storm_mwh_attributed": contrib,
            "share_attributed_to_basin_cold": contrib["basin_cold"] / max(sum(abs(v) for v in contrib.values()), 1e-9),
            "units": int(g.plant_fuel_id.nunique()), "basins": g.nearest_basin.value_counts().to_dict()}


# ---------------------------------------------------------------- charts, map, outputs
def _style(ax):
    ax.set_facecolor(SURF); ax.grid(axis="y", color=GRID, lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2)


def chart_fuel_effects(fe: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.2), facecolor=SURF); _style(ax)
    for i, r in fe.iterrows():
        ax.errorbar(r.effect_share_per_F * 100, i, xerr=[[(r.effect_share_per_F - r.ci_low) * 100], [(r.ci_high - r.effect_share_per_F) * 100]],
                    fmt="o", color=FUEL_COLOR[r.fuel], ms=8, lw=2, capsize=4)
        ax.text(r.ci_high * 100, i, f"  {r.mw_per_F_fleet:,.0f} MW per °F (fleet)", va="center", color=INK2, fontsize=8)
    ax.axvline(0, color=INK2, lw=1)
    ax.set_yticks(range(len(fe)), fe.fuel); ax.invert_yaxis()
    ax.set_xlabel("percentage points of capacity out per °F below freezing (daily low)", color=INK2)
    ax.set_title("Effect of plant-level cold on outages, by fuel (LinearDML, day fixed effects)", loc="left", color=INK, fontsize=11)
    fig.tight_layout(); fig.savefig(path, dpi=130, facecolor=SURF); plt.close(fig)


def chart_warmer(cf: pd.DataFrame, path: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.2), sharey=False, facecolor=SURF)
    for ax, f in zip(axes, ["gas", "wind", "coal"]):
        _style(ax)
        g = cf[(cf.fuel == f) & (cf.delta_F == 5)].sort_values("day")
        g10 = cf[(cf.fuel == f) & (cf.delta_F == 10)].sort_values("day")
        ax.plot(g.day, g.mw_out_actual / 1000, color=INK, lw=2.2, label="actual (matched plants)")
        for gg, lab, col in ((g, "+5 °F", FUEL_COLOR[f]), (g10, "+10 °F", "#6250d6")):
            ax.plot(gg.day, (gg.mw_out_actual - gg.mw_avoided) / 1000, color=col, lw=2, label=f"counterfactual {lab}")
            ax.fill_between(gg.day, (gg.mw_out_actual - gg.mw_avoided_high) / 1000, (gg.mw_out_actual - gg.mw_avoided_low) / 1000,
                            color=col, alpha=0.15, lw=0)
        ax.set_title(f.capitalize(), loc="left", color=INK, fontsize=11)
        ax.set_ylabel("daily mean MW out (GW)", color=INK2, fontsize=8)
        ax.tick_params(axis="x", rotation=45)
    axes[0].legend(frameon=False, fontsize=8, labelcolor=INK)
    fig.suptitle("Warmer-storm counterfactual: outages if every plant had seen 5 or 10 °F less cold", x=0.01, ha="left", color=INK)
    fig.tight_layout(); fig.savefig(path, dpi=130, facecolor=SURF); plt.close(fig)


def chart_map(u: pd.DataFrame, path: Path) -> None:
    counties = tx_counties()
    fig, ax = plt.subplots(figsize=(10, 9.5), facecolor=SURF); ax.set_facecolor(SURF)
    for g in counties.geometry:
        for poly in getattr(g, "geoms", [g]):
            x, y = poly.exterior.xy
            ax.plot(x, y, color="#cfcec8", lw=0.4)
    basins = SPATIAL / "geojson" / "shale_plays.geojson"
    if basins.exists():
        from shapely.geometry import shape
        for f in json.loads(basins.read_text())["features"]:
            g = shape(f["geometry"])
            for poly in getattr(g, "geoms", [g]):
                if hasattr(poly, "exterior"):
                    x, y = poly.exterior.xy
                    ax.fill(x, y, color="#f4e7c7", alpha=0.5, lw=0)
    v = u.cate_mw_per_F
    sc = ax.scatter(u.lon, u.lat, s=np.clip(u.capacity_mw / 8, 6, 220), c=v, cmap="Blues", vmin=0, vmax=np.nanpercentile(v, 95),
                    edgecolor=INK2, linewidth=0.4, zorder=3)
    cb = fig.colorbar(sc, ax=ax, shrink=0.6); cb.set_label("MW out per °F of cold (causal forest)", color=INK2)
    ax.set_xlim(-107, -93.4); ax.set_ylim(25.7, 36.6); ax.set_aspect(1.1)
    ax.set_title("Cold sensitivity by plant (size = capacity; tan = shale/tight-gas plays)", loc="left", color=INK, fontsize=11)
    ax.set_xlabel("longitude", color=INK2); ax.set_ylabel("latitude", color=INK2)
    fig.tight_layout(); fig.savefig(path, dpi=120, facecolor=SURF); plt.close(fig)


def write_cate_geojson(u: pd.DataFrame, feats: pd.DataFrame, path: Path) -> None:
    f = feats.rename(columns={"Plant Code": "plant_code"})
    keep = ["plant_code", "plant_name", "county"] + [c for c in f.columns if c.startswith(("dist_", "dir_", "nearest_", "weather_zone", "load_zone"))]
    m = u.merge(f[[c for c in keep if c in f.columns]], on="plant_code", how="left").replace({np.nan: None})
    feats_out = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                  "properties": {k: v for k, v in r.items() if k not in ("lat", "lon")}} for r in m.to_dict(orient="records")]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats_out}, default=float))


def main() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    p, info = build_panel()
    p.to_parquet(OUT / "panel.parquet", index=False)
    rec = reconcile(p); rec.to_csv(OUT / "reconciliation.csv", index=False)
    lin = fit_linear(p)
    ate = clustered_ate(lin, p.plant_code.values)
    fe = per_fuel_effects(p); fe.to_csv(OUT / "fuel_effects.csv", index=False)
    plc = placebo(p)
    forest, X = fit_forest(p)
    u = unit_cate(p, forest, X)
    cf = warmer(p, u); cf.to_csv(OUT / "counterfactual_warmer.csv", index=False)
    wint = winterization(p, u)
    gas = gas_supply_vs_plant(p)
    feats = pd.read_parquet(SPATIAL / "plant_features.parquet")
    u.to_parquet(OUT / "unit_cate.parquet", index=False)
    write_cate_geojson(u, feats, OUT / "cate_plants.geojson")
    chart_fuel_effects(fe, OUT / "fuel_effects.png")
    chart_warmer(cf, OUT / "counterfactual_warmer.png")
    chart_map(u, OUT / "cate_map.png")
    peak = cf[cf.day == PEAK_DAY].groupby("delta_F")[["mw_avoided", "mw_avoided_low", "mw_avoided_high", "mw_out_actual"]].sum()
    res = {"panel": info, "ate": ate, "placebo": {k: v for k, v in plc.items() if k != "draws"},
           "placebo_z": (ate["ate"] - plc["mean"]) / plc["sd"] if plc["sd"] else None,
           "fuel_effects": fe.to_dict(orient="records"),
           "cate_by_fuel": u.groupby("fuel").cate_mw_per_F.describe()[["count", "mean", "50%", "max"]].round(2).to_dict(orient="index"),
           "warmer_peak_day": peak.round(0).to_dict(orient="index"), "winterization": wint, "gas_supply_vs_plant": gas,
           "forest_ate": float(forest.ate(X.values))}
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def _ci(a, b, scale=100, fmt="{:.2f}"):
    return f"[{fmt.format(a * scale)}, {fmt.format(b * scale)}]"


def report(out: Path = OUT) -> str:
    r = json.loads((out / "results.json").read_text())
    fe = pd.DataFrame(r["fuel_effects"])
    rec = pd.read_csv(out / "reconciliation.csv").groupby("fuel")[["ercot_measured_mw", "unit_list_all_mw", "panel_matched_mw"]].mean()
    cf = pd.read_csv(out / "counterfactual_warmer.csv", parse_dates=["day"])
    pk = cf[cf.day == PEAK_DAY].pivot_table(index="fuel", columns="delta_F", values="mw_avoided", aggfunc="sum")
    pn, a, pl, gas, wi = r["panel"], r["ate"], r["placebo"], r["gas_supply_vs_plant"], r["winterization"]
    L = ["# Causal ML: does cold at the plant cause outages, and for which plants?", "",
         "Run `05_causal_ml` (`analysis/causal_ml.py`). Built on the EIA-860 fleet matched to ERCOT's 3/12 outage "
         "list (`kg/fleet.py`) and the spatial layer (`kg/spatial.py`). Design and assumptions: M- and S-items in "
         "`ASSUMPTIONS.md`.", "",
         "## Design", "",
         f"- **Units:** {pn['plant_fuel_units']} plant × fuel units (" + ", ".join(f"{k} {v}" for k, v in pn['units_by_fuel'].items())
         + f"), Feb 10–19, {pn['rows']:,} rows; {pn['units_with_any_outage']} had any outage. {pn['excluded_for_unmatched_station']} units "
         f"({pn['excluded_capacity_mw']:,.0f} MW) are left out because an unmatched ERCOT station's outages might belong to them.",
         "- **Outcome:** share of the unit's capacity out that day (MWh out ÷ capacity × 24).",
         "- **Treatment:** °F below freezing at the plant's daily low, interpolated from the 4 nearest reporting stations "
         "(median 15 km away).",
         "- **Controls:** day fixed effects (statewide shocks: load shed, gas supply, frequency events), cold accumulated "
         "before the day, and the plant's features (size, age, location, distances to coast, load centres, basins, "
         "pipelines, processing plants and transmission). The effect is identified by plants that saw different cold "
         "on the same day.",
         "- **Estimators:** LinearDML (econml) with gradient-boosted nuisance models, 5-fold cross-fitting grouped by "
         "plant; final-stage standard errors clustered by plant. CausalForestDML (1,000 trees) for per-plant effects.", "",
         "## Results", "",
         f"**Average effect:** {a['ate'] * 100:.2f} percentage points of capacity out per °F of cold, 95% CI "
         f"{_ci(*a['ci95'])} (plant-clustered, {a['clusters']} clusters). The causal forest's average is "
         f"{r['forest_ate'] * 100:.2f}. **Placebo** (cold shuffled across plants within each day, {pl['n']} draws): "
         f"{pl['mean'] * 100:.3f} ± {pl['sd'] * 100:.3f} pp. The real effect sits far outside the placebo spread, but "
         "that spread ignores clustering; the clustered interval, which just touches zero, is the honest one.", "",
         "| Fuel | Effect (pp per °F) | 95% CI | Capacity in panel (MW) | MW per °F (panel capacity) |", "|---|---|---|---|---|"]
    for _, x in fe.iterrows():
        L.append(f"| {x.fuel} | {x.effect_share_per_F * 100:.2f} | {_ci(x.ci_low, x.ci_high)} | {x.fleet_capacity_mw:,.0f} | {x.mw_per_F_fleet:,.0f} |")
    L += ["", "Only **wind** has an interval that excludes zero. Gas is positive but not distinguishable from zero; coal "
          "(11 units) and nuclear (2) are too few to say anything.", "",
          "![Fuel effects](fuel_effects.png)", "",
          "## Counterfactuals", "",
          "**1. A warmer storm.** If every plant had seen 5 °F (or 10 °F) less cold, using each plant's own effect "
          "(causal forest), on the peak day, Feb 16:", "",
          "| Fuel | MW avoided at +5 °F | MW avoided at +10 °F |", "|---|---|---|"]
    for f in FUELS:
        if f in pk.index:
            L.append(f"| {f} | {pk.loc[f].get(5, 0):,.0f} | {pk.loc[f].get(10, 0):,.0f} |")
    wp = r["warmer_peak_day"]
    L += ["", f"Total: {wp['5']['mw_avoided']:,.0f} MW at +5 °F (95% CI {wp['5']['mw_avoided_low']:,.0f} to "
          f"{wp['5']['mw_avoided_high']:,.0f}) and {wp['10']['mw_avoided']:,.0f} MW at +10 °F, against "
          f"{wp['5']['mw_out_actual']:,.0f} MW out at these plants that day. This is linear in each plant's local effect, "
          "not a grid simulation: it ignores load shed, gas supply and restarts.", "",
          "![Warmer storm](counterfactual_warmer.png)", "",
          "**2. Who was most cold-sensitive.** Per-plant effects are in `cate_plants.geojson` (with every spatial "
          "feature attached) and mapped below. Panhandle wind and gas plants near the Houston coast are among the most "
          "sensitive per °F. One reading, to be checked in the geographic interpretation: plants that rarely see "
          "freezing were the least winterized. Negative estimates are shown as zero on the map.", "",
          "![Map](cate_map.png)", "",
          "**3. Gas supply vs plant cold.** Gas plants with two treatments: cold at the plant and cold at their nearest "
          f"major gas play. Plant cold: {gas['effect_share_per_F']['plant_cold'] * 100:.2f} pp per °F "
          f"{_ci(*gas['ci95']['plant_cold'])}; basin cold: {gas['effect_share_per_F']['basin_cold'] * 100:.2f} "
          f"{_ci(*gas['ci95']['basin_cold'])}. **Not identified.** The basin effect has the wrong sign: with day fixed effects, "
          "the only variation in basin cold is which basin a plant is nearest, which is geography (north vs south), so it "
          "absorbs regional differences rather than measuring supply. The gas-supply question needs plant-level fuel "
          "receipts or pipeline nominations, not distance.", "",
          "**4. Winterization proxy.** If the most cold-sensitive quarter of each fuel had the fuel's median sensitivity, "
          "on Feb 16:", "", "| Fuel | Units in top quarter | MW saved | MW out that day (panel) |", "|---|---|---|---|"]
    for f, w in wi.items():
        L.append(f"| {f} | {w['units_in_top_quarter']} | {w['mw_saved_peak_day']:,.0f} | {w['actual_mw_out_peak_day']:,.0f} |")
    L += ["", "A what-if on estimated effects, not a measured intervention; no intervals (the forest's per-plant intervals "
          "are wide).", "",
          "## How much of the outage the panel sees", "",
          "Daily mean MW out, Feb 14–19: ERCOT's measured series (Tier 0b), all stations in the 3/12 list, and the "
          "matched panel.", "", "| Fuel | ERCOT measured | 3/12 list | Panel |", "|---|---|---|---|"]
    for f, x in rec.iterrows():
        L.append(f"| {f} | {x.ercot_measured_mw:,.0f} | {x.unit_list_all_mw:,.0f} | {x.panel_matched_mw:,.0f} |")
    L += ["", "The 3/12 list itself has about 70% of ERCOT's measured gas MW out and the panel about 61%; wind is about "
          "81%. Effects describe the plants in the panel.", "",
          "## Limits", "",
          "- One storm, ten days; plants' cold is spatially correlated, so 328 clusters overstate independence.",
          "- The outcome comes from the 3/12 list (self-reported, no causes) and the treatment from interpolated "
          "station lows, not plant-site temperatures.",
          "- Day fixed effects remove statewide shocks but not local ones (a local gas lateral, a local load-shed block).",
          "- Matching ERCOT stations to EIA plants is rule-based (88% of outage energy); ambiguous plants are excluded.",
          "- Counterfactuals extrapolate local, linear effects.", ""]
    txt = "\n".join(L)
    (out / "causal_ml.md").write_text(txt)
    return txt


if __name__ == "__main__":
    import sys
    if "--report-only" in sys.argv:
        report(); raise SystemExit
    r = main()
    report()
    print(json.dumps({k: r[k] for k in ("panel", "ate", "placebo", "placebo_z", "forest_ate", "warmer_peak_day")}, indent=1, default=str))
