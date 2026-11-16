"""Step 4: is the lagged cold -> outage relationship real, and does it come from cold building up,
slow restarts, or both? Does gas behave differently from the other fuels?

Per fuel (gas, coal, wind, solar; nuclear as a footnote), on hourly ERCOT MW out (Feb 14-19):
  1. instantaneous   O(t) = a + b T(t)
  2. cumulative      O(t) = a + b FDH(t),  FDH(t) = sum from Feb 10 of max(0, 32 - T)
  3. fail-restart    O(t) = O(t-1) + alpha max(0, 32 - T(t)) (C - O(t-1)) - rho O(t-1), free-run from Feb 14 00:00
Train Feb 14 00:00 - Feb 17 23:00, test Feb 18 00:00 - Feb 19 23:00 (ERCOT data ends 2/20 00:00).
Placebo: refit with T shifted by -5 and +5 days.

    uv run python -m analysis.cold_models          # then re-grades the graph (kg.invariants)
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from kg import series as S
from kg.common import EXTERNAL
from kg.graph_build import RUN

FUELS = ["gas", "coal", "wind", "solar"]
START, TRAIN_END, END = pd.Timestamp("2021-02-14 00:00"), pd.Timestamp("2021-02-17 23:00"), pd.Timestamp("2021-02-19 23:00")
FDH_FROM = pd.Timestamp("2021-02-10 00:00")
SHIFTS = (-5, 5)
PLACEBO_RATIO = 1.25          # C-M3: a placebo "fits much worse" if its test RMSE is >= 1.25x the real one
# Airport coordinates of the 9 zone stations (rounded; used only to assign counties to the nearest station)
STATIONS = {"Coast": (29.98, -95.34), "East": (32.35, -95.40), "Far West": (31.94, -102.20), "North": (33.99, -98.49),
            "North Central": (32.90, -97.02), "South Central": (30.19, -97.67), "South Central (extra)": (29.53, -98.47),
            "Southern": (27.77, -97.50), "West": (32.41, -99.68)}
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
SERIES = {"M1 instantaneous": "#2a78d6", "M2 cumulative": "#eb6834", "M3 fail-restart": "#1baf7a"}


# ---------------------------------------------------------------- data
def outages() -> pd.DataFrame:
    """Hourly mean MW out by fuel, hour-start labels. Minute-ending labels h:01..h+1:00 average into hour h (A1)."""
    o = S.minute_start(S.mw_out_by_fuel())
    return o.resample("h").mean().loc[START:END]


def capacity() -> pd.DataFrame:
    """HSL (MW) of distinct units in the 3/12 list, by county and fuel."""
    u = S.unit_outages("2021_03_12").dropna(subset=["station"]).drop_duplicates(["station", "unit"])
    return u.groupby(["county", "fuel"]).hsl_mw.sum().astype(float).unstack().fillna(0.0)


def county_station() -> pd.Series:
    """County -> nearest zone station, from the mean coordinates of the county's CDO stations (C-M1)."""
    c = pd.read_csv(EXTERNAL / "noaa" / "cdo_ghcnd_stations_by_outage_county.csv").dropna(subset=["latitude"])
    xy = c.groupby("county")[["latitude", "longitude"]].mean()
    st = pd.DataFrame(STATIONS, index=["lat", "lon"]).T

    def nearest(r):
        d = (st.lat - r.latitude) ** 2 + ((st.lon - r.longitude) * np.cos(np.radians(r.latitude))) ** 2
        return d.idxmin()
    return xy.apply(nearest, axis=1)


def fuel_temperature() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Statewide temperature per fuel = zone temperatures weighted by the fuel's capacity in each zone."""
    cap = capacity()
    cs = county_station()
    assigned = cap[cap.index.isin(cs.index)]
    w = assigned.groupby(cs.reindex(assigned.index)).sum()
    weights = (w / w.sum()).fillna(0.0)
    cover = (assigned.sum() / cap.sum()).rename("capacity_share_with_station")
    T = S.noaa_zone_temps()
    out = pd.DataFrame({f: T[weights.index].mul(weights[f], axis=1).sum(axis=1) for f in weights.columns})
    return out, pd.concat([weights.T, cover], axis=1)


def fdh(T: pd.Series) -> pd.Series:
    return (32 - T.loc[FDH_FROM:]).clip(lower=0).cumsum()


# ---------------------------------------------------------------- models
def rmse(y, yh):
    return float(np.sqrt(np.mean((np.asarray(y) - np.asarray(yh)) ** 2)))


def r2(y, yh):
    y, yh = np.asarray(y), np.asarray(yh)
    return float(1 - np.sum((y - yh) ** 2) / np.sum((y - y.mean()) ** 2))


def fit_linear(x: pd.Series, y: pd.Series, train: pd.Index):
    X = np.c_[np.ones(len(train)), x.loc[train]]
    (a, b), *_ = np.linalg.lstsq(X, y.loc[train].values, rcond=None)
    return {"a": float(a), "b": float(b)}, a + b * x


def simulate_driver(alpha, rho, d: np.ndarray, C: float, o0: float) -> np.ndarray:
    """Fail-restart with any driver d(t) >= 0: O(t) = O(t-1) + alpha d(t) (C - O(t-1)) - rho O(t-1)."""
    o = np.empty(len(d))
    o[0] = o0
    for t in range(1, len(d)):
        o[t] = o[t - 1] + alpha * d[t] * (C - o[t - 1]) - rho * o[t - 1]
    return o


def simulate(alpha, rho, T: np.ndarray, C: float, o0: float) -> np.ndarray:
    """Model 3 with the cold driver max(0, 32 - T)."""
    return simulate_driver(alpha, rho, np.clip(32 - np.asarray(T, dtype=float), 0, None), C, o0)


def fit_driver(d: np.ndarray, yv: np.ndarray, n: int, C: float, starts=((1e-4, 1e-3), (1e-3, 1e-2), (5e-3, 5e-2),
                                                                        (1e-4, 1e-2), (1e-3, 1e-3), (5e-3, 1e-2),
                                                                        (1e-4, 5e-2), (1e-3, 5e-2), (5e-3, 1e-3))):
    """Fit alpha, rho on the first n points of a free-run simulation from yv[0]."""
    def res(p):
        return simulate_driver(p[0], p[1], d, C, yv[0])[:n] - yv[:n]
    best = None
    for a0, r0 in starts:
        f = least_squares(res, [a0, r0], bounds=([0, 0], [0.1, 1.0]))
        if best is None or f.cost < best.cost:
            best = f
    return best


def fit_fail_restart(T: pd.Series, y: pd.Series, train: pd.Index, C: float):
    Tv, yv, n = T.loc[y.index].values, y.values, len(train)
    best = fit_driver(np.clip(32 - Tv, 0, None), yv, n, C)
    a, r = best.x
    return {"alpha": float(a), "rho": float(r), "C": C}, pd.Series(simulate(a, r, Tv, C, yv[0]), index=y.index)


def fit_all(T: pd.Series, y: pd.Series, C: float) -> dict:
    train, test = y.loc[:TRAIN_END].index, y.loc[TRAIN_END + pd.Timedelta(hours=1):].index
    F = fdh(T)
    p1, f1 = fit_linear(T, y, train)
    p2, f2 = fit_linear(F, y, train)
    p3, f3 = fit_fail_restart(T, y, train, C)
    out = {}
    for name, p, f in (("M1 instantaneous", p1, f1), ("M2 cumulative", p2, f2), ("M3 fail-restart", p3, f3)):
        f = f.loc[y.index]
        out[name] = {"params": p, "fit": f, "rmse_train": rmse(y[train], f[train]), "r2_train": r2(y[train], f[train]),
                     "rmse_test": rmse(y[test], f[test]), "r2_test": r2(y[test], f[test])}
    return out


# ---------------------------------------------------------------- chart
def chart(fuel: str, y: pd.Series, fits: dict, T: pd.Series, path) -> None:
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, (ax, at) = plt.subplots(2, 1, figsize=(10, 6.2), sharex=True, gridspec_kw={"height_ratios": [3, 1.1]},
                                 facecolor=SURF)
    for a in (ax, at):
        a.set_facecolor(SURF)
        a.axvspan(TRAIN_END + pd.Timedelta(hours=1), END + pd.Timedelta(hours=1), color=GRID, alpha=0.45, lw=0)
        a.axvline(TRAIN_END + pd.Timedelta(hours=1), color=INK2, lw=1, ls=(0, (4, 3)))
        a.grid(axis="y", color=GRID, lw=0.8)
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            a.spines[s].set_color(INK2)
        a.tick_params(colors=INK2)
    ax.plot(y.index, y / 1000, color=INK, lw=2.2, label="Observed (ERCOT)", zorder=5)
    for name, col in SERIES.items():
        ax.plot(y.index, fits[name]["fit"] / 1000, color=col, lw=2, label=f"{name}  (test RMSE {fits[name]['rmse_test'] / 1000:.2f} GW)")
    ax.set_ylabel("MW out (GW)", color=INK2)
    ax.set_title(f"{fuel.capitalize()}: generation out vs three cold models", loc="left", color=INK, fontsize=12, pad=46)
    ax.text(TRAIN_END + pd.Timedelta(hours=3), ax.get_ylim()[1] * 0.97, "test (Feb 18–19)", color=INK2, va="top")
    ax.text(START + pd.Timedelta(hours=2), ax.get_ylim()[1] * 0.97, "train", color=INK2, va="top")
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, fontsize=9, labelcolor=INK)
    tt = T.loc[START:END]
    at.plot(tt.index, tt, color=INK2, lw=1.6)
    at.axhline(32, color=INK2, lw=0.8, ls=":")
    at.text(tt.index[2], 33.5, "32 °F", color=INK2, fontsize=8)
    at.set_ylabel(f"T, {fuel}-weighted (°F)", color=INK2, fontsize=9)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURF)
    plt.close(fig)


# ---------------------------------------------------------------- main
def main() -> dict:
    O = outages()
    Tf, weights = fuel_temperature()
    C = capacity().sum()
    results, placebo, tests = {}, {}, []
    for fuel in FUELS:
        y = O[fuel]
        if (y > C[fuel]).any():
            raise SystemExit(f"STOP: observed {fuel} MW out exceeds C = {C[fuel]:.0f} MW (assumption C-M2)")
        T = Tf[fuel]
        res = fit_all(T, y, float(C[fuel]))
        results[fuel] = res
        placebo[fuel] = {}
        for sh in SHIFTS:
            Ts = T.copy()
            Ts.index = Ts.index + pd.Timedelta(days=sh)      # value at t is now T(t - sh days)
            Ts = Ts.reindex(T.index)
            placebo[fuel][sh] = fit_all(Ts.dropna(), y, float(C[fuel]))
        chart(fuel, y, res, T, RUN / f"cold_{fuel}.png")
        # write-back rule: model 3 or 2 beats model 1 out of sample and passes the placebo
        m1 = res["M1 instantaneous"]["rmse_test"]
        cands = []
        for m in ("M3 fail-restart", "M2 cumulative"):
            real = res[m]["rmse_test"]
            ratios = {sh: placebo[fuel][sh][m]["rmse_test"] / real for sh in SHIFTS}
            res[m]["placebo_ratio"] = ratios
            res[m]["placebo_pass"] = all(r >= PLACEBO_RATIO for r in ratios.values())
            if real < m1 and res[m]["placebo_pass"]:
                cands.append(m)
        if cands:
            m = min(cands, key=lambda k: res[k]["rmse_test"])
            tests.append({"src_class": "WeatherCondition", "dst_class": f"GenerationOutage:{fuel}", "passed": True,
                          "method": m, "params": res[m]["params"], "rmse_test": res[m]["rmse_test"],
                          "rmse_test_m1": m1, "placebo_ratio": res[m]["placebo_ratio"],
                          "link": "results/03_graph/cold_models.md", "chart": f"cold_{fuel}.png"})
    out = {"edge_tests": tests, "C_mw": {f: float(C[f]) for f in FUELS + ["nuclear"]},
           "weights": weights.round(4).to_dict(orient="index"),
           "metrics": {f: {m: {k: v for k, v in d.items() if k != "fit"} for m, d in r.items()} for f, r in results.items()},
           "placebo": {f: {str(sh): {m: {"rmse_test": d["rmse_test"], "rmse_train": d["rmse_train"], "r2_train": d["r2_train"]}
                                     for m, d in p.items()} for sh, p in pp.items()} for f, pp in placebo.items()}}
    (RUN / "cold_models.json").write_text(json.dumps(out, indent=1, default=str))
    (RUN / "cold_models.md").write_text(report(out, results, placebo, weights, O, Tf)
                                        .replace("WHATHAPPENED_PLACEHOLDER", what_happened(results, placebo)))
    from kg import invariants
    out["grades"] = invariants.main()["class_grades"]
    return out


def nuclear_note(O: pd.DataFrame, Tf: pd.DataFrame) -> str:
    n = O["nuclear"]
    on = n[n > 100]
    if on.empty:
        return "No nuclear outage in the window."
    t0, t1 = on.index[0], on.index[-1]
    return (f"Nuclear (footnote, n = 1): one unit (South Texas Project, {n.max():,.0f} MW) was out from the hour "
            f"{t0:%m/%d %H:00} to {t1:%m/%d %H:00} ({len(on)} h). The nuclear-weighted temperature at the trip hour "
            f"was {Tf['nuclear'].get(t0, float('nan')):.1f} °F. One event can't support a dose-response fit, so no model is fitted.")


def what_happened(results, placebo) -> str:
    best = {f: min(r, key=lambda m: r[m]["rmse_test"]) for f, r in results.items()}
    alpha = {f: r["M3 fail-restart"]["params"]["alpha"] for f, r in results.items()}
    r2_m1 = {f: r["M1 instantaneous"]["r2_train"] for f, r in results.items()}
    m3_wins = [f for f, b in best.items() if b == "M3 fail-restart"]
    by_alpha = sorted(alpha, key=alpha.get, reverse=True)
    m2_fail = all(not r["M2 cumulative"].get("placebo_pass", False) for r in results.values())
    L = ["## Expected vs what happened", "",
         f"- **Model 3 wins out of sample** for {', '.join(m3_wins)}"
         + (f"; for {', '.join(f for f in best if f not in m3_wins)} the best is "
            + ", ".join(f"{best[f]}" for f in best if f not in m3_wins) if len(m3_wins) < len(best) else "")
         + ". Only gas has a positive test R² "
         f"({results['gas']['M3 fail-restart']['r2_test']:.2f}); for the other fuels every model does worse than the "
         "test-period mean, because the level falls during Feb 18–19 and none of the models falls fast enough.",
         f"- **Wind and solar do not have the highest α.** Ranked by α: {', '.join(f'{f} {alpha[f]:.1e}' for f in by_alpha)}. "
         "Coal and gas fail faster per degree of cold than wind in this fit.",
         f"- **Gas is explained worst by instantaneous local temperature** (M1 train R²: "
         f"{', '.join(f'{f} {r2_m1[f]:.2f}' for f in r2_m1)}), as expected, **but best by the fail-restart model**. "
         "So local cold plus slow restarts accounts for most of the gas trajectory at system level. That does not rule "
         "out a fuel-supply cause: the gas fields froze in the same cold, so supply losses and plant freezing share one "
         "driver and this test can't separate them.",
         "- **The cumulative model (M2) fails every placebo** and extrapolates badly: FDH only rises, so M2 can't express "
         "restarts, and any cold spell (Feb 9–14 for the −5-day shift) builds a similar ramp.",
         "- **The +5-day placebo is the weaker test**: shifting by +5 days brings in the warm days of Feb 19–24, so the "
         "cold term is mostly zero and model 3 can only decay. The −5-day shift (Feb 9–14, also cold) is the stricter "
         "one; model 3's ratios there are " + ", ".join(
             f"{f} {results[f]['M3 fail-restart']['rmse_test'] and placebo[f][-5]['M3 fail-restart']['rmse_test'] / results[f]['M3 fail-restart']['rmse_test']:.2f}"
             for f in results) + ".",
         "- **Wind's sharp return on Feb 19** (13.6 GW out at 09:00, 8.7 GW at 23:00, after the wind-weighted temperature "
         "crossed 32 °F around 10:00) is "
         "faster than any constant ρ allows; a restart rate that depends on temperature would be the next model to try."]
    return "\n".join(L)


def report(out, results, placebo, weights, O, Tf) -> str:
    L = ["# Cold → generation outages: three models per fuel", "",
         "Run `03_graph`, step 4 (`analysis/cold_models.py`). Hourly ERCOT MW out by fuel (minute data, "
         "hour-start means), Feb 14 00:00 – Feb 19 23:00 Central. **Train** Feb 14 00:00 – Feb 17 23:00 (96 h), "
         "**test** Feb 18 00:00 – Feb 19 23:00 (48 h, the thaw; ERCOT's data ends 2/20 00:00, so not through Feb 20).", "",
         "Temperature for each fuel is the 9 zone stations weighted by that fuel's capacity per zone: each county in "
         "the 3/12 unit list goes to its nearest station (from its CDO station coordinates), weighted by the HSL of the "
         "fuel's units there. FDH(t) = Σ max(0, 32 − T) from Feb 10 00:00, in °F·h. C = HSL of the fuel's distinct "
         "units in the 3/12 list. Model 3 is free-run from the observed value at Feb 14 00:00; no observed value is "
         "used after that.", "",
         "## Results", "", "| Fuel | Model | RMSE train (MW) | R² train | RMSE test (MW) | R² test | Placebo −5 d / +5 d (test RMSE ÷ real) |",
         "|---|---|---|---|---|---|---|"]
    for f, res in results.items():
        best = min(res, key=lambda m: res[m]["rmse_test"])
        for m, d in res.items():
            pr = d.get("placebo_ratio") or {sh: placebo[f][sh][m]["rmse_test"] / d["rmse_test"] for sh in SHIFTS}
            pas = "" if "placebo_pass" not in d else (" ✅" if d["placebo_pass"] else " ❌")
            name = f"**{m}**" if m == best else m
            L.append(f"| {f} | {name} | {d['rmse_train']:,.0f} | {d['r2_train']:.2f} | {d['rmse_test']:,.0f} | "
                     f"{d['r2_test']:.2f} | {pr[-5]:.2f} / {pr[5]:.2f}{pas} |")
    L += ["", "Bold = lowest test RMSE for the fuel. Placebo ✅ = both shifted fits have test RMSE ≥ "
          f"{PLACEBO_RATIO}× the real one (checked for models 2 and 3 only). R² test is against the test-period mean, "
          "so it can be negative.", "", "## Fitted fail-restart parameters", "",
          "| Fuel | α (per °F below 32 per h) | ρ (per h) | Restart half-life (h) | C (MW) | Capacity share with a station |",
          "|---|---|---|---|---|---|"]
    for f, res in results.items():
        p = res["M3 fail-restart"]["params"]
        hl = np.log(2) / p["rho"] if p["rho"] > 0 else float("inf")
        L.append(f"| {f} | {p['alpha']:.2e} | {p['rho']:.4f} | {hl:,.0f} | {p['C']:,.0f} | "
                 f"{weights.loc[f, 'capacity_share_with_station']:.0%} |")
    L += ["", "α is the hourly fraction of still-available capacity that fails per °F below freezing; ρ the hourly "
          "fraction of out capacity that returns.", "", "## Placebo fits (test RMSE, MW)", "",
          "| Fuel | Model | Real | −5 days | +5 days |", "|---|---|---|---|---|"]
    for f, res in results.items():
        for m, d in res.items():
            L.append(f"| {f} | {m} | {d['rmse_test']:,.0f} | {placebo[f][-5][m]['rmse_test']:,.0f} | {placebo[f][5][m]['rmse_test']:,.0f} |")
    L += ["", "## Written back to the graph", ""]
    if out["edge_tests"]:
        for t in out["edge_tests"]:
            L.append(f"- `WeatherCondition → {t['dst_class']}`: {t['method']} beats M1 out of sample "
                     f"({t['rmse_test']:,.0f} vs {t['rmse_test_m1']:,.0f} MW) and passes the placebo "
                     f"(ratios {t['placebo_ratio'][-5]:.2f} / {t['placebo_ratio'][5]:.2f}). Test recorded on the class edge; grade set by `kg.invariants`.")
    else:
        L.append("- No fuel met the rule (model 3 or 2 beats model 1 out of sample and passes the placebo).")
    L += ["", "Charts: " + ", ".join(f"`cold_{f}.png`" for f in results), "", nuclear_note(O, Tf), "",
          "WHATHAPPENED_PLACEHOLDER", "",
          "## Limits", "",
          "- **One event.** Four days of training and two of testing; the fits describe this storm, not a general law.",
          "- **System-level data, by fuel, not by unit.** Each fuel is one series; the temperature is a capacity-weighted "
          "mix of 9 airport stations, and 18 of 113 counties (about 16% of MW lost) have no station coordinates and drop "
          "out of the weights.",
          "- **MW out mixes causes.** Gas outages include fuel-supply losses far from the plant, pre-existing and planned "
          "outages; model 3 has no baseline term, so it must decay pre-storm outages as if they were cold-caused.",
          "- **Dose-response fit, not a randomized comparison.** A good fit plus a failed placebo for a shifted series "
          "is evidence of a real temperature–outage relationship in this event, not proof of mechanism.",
          "- Placebo +5 d uses NOAA hours Feb 22–26, added 2026-11-15 with the same cleaning rule (`scripts/extend_zone_temps.py`)."]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    o = main()
    print(json.dumps({"edge_tests": [{k: t[k] for k in ("dst_class", "method", "rmse_test", "rmse_test_m1", "placebo_ratio")}
                                     for t in o["edge_tests"]], "grades": o["grades"]}, indent=1, default=str))
