"""Hypotheses step 1: is the cold -> outage fit about cold, or would any rise-and-fall shape do?

The fail-restart model (M3) is refitted with temperature replaced by weather-free drivers of the same shape:
  tent   rises linearly from Feb 14 00:00 to a fitted peak hour, falls to zero at a fitted end hour (no weather input)
  hours  hours since Feb 14 00:00
Both are scaled to the temperature driver's maximum. Same split as before (train Feb 14 00:00-17 23:00, test
Feb 18 00:00-19 23:00; ERCOT data ends 2/20 00:00).

Grade rule (H2): the weather -> outage edge stays above G1 only if M3-with-temperature passes its placebo AND both
weather-free M3 versions have test RMSE >= 1.25 x the temperature version.

    uv run python -m analysis.cold_baselines        # writes results/04_hypotheses/cold_models.json etc.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from analysis.cold_models import (END, GRID, INK, INK2, PLACEBO_RATIO, SHIFTS, START, SURF, TRAIN_END, capacity,
                                  fit_all, fit_driver, fuel_temperature, outages, rmse, r2, simulate_driver)
from kg.common import RESULTS, documents

OUT = RESULTS / "04_hypotheses"
PREV = RESULTS / "03_graph"
TARGETS = ["gas", "coal", "wind", "solar", "total"]
MARGIN = 1.25                     # H2: same ratio as the placebo rule (C-M3)
TENT_PEAKS = range(24, 97, 4)     # hours after Feb 14 00:00
TENT_END_STEP = 8
INPUT_DATASETS = ["ercot_mw_out_by_fuel", "noaa_zone_temp_hourly", "unit_outage_2021_03_12"]
COLORS = {"M3 temperature": "#1baf7a", "M3 tent (no weather)": "#6250d6", "M3 hours (no weather)": "#eda100",
          "M1 instantaneous": "#2a78d6"}


def tent(n: int, peak: int, end: int, height: float) -> np.ndarray:
    h = np.arange(n, dtype=float)
    up = np.clip(h / peak, 0, 1)
    down = np.clip((end - h) / max(end - peak, 1), 0, 1)
    return height * np.where(h <= peak, up, down)


def fit_tent(yv: np.ndarray, n_train: int, C: float, height: float):
    best = None
    starts = ((1e-3, 1e-2), (5e-3, 5e-2))
    for p in TENT_PEAKS:
        for e in range(p + 12, len(yv) + 25, TENT_END_STEP):
            d = tent(len(yv), p, e, height)
            f = fit_driver(d, yv, n_train, C, starts=starts)
            if best is None or f.cost < best[0].cost:
                best = (f, p, e, d)
    f, p, e, d = best
    return {"alpha": float(f.x[0]), "rho": float(f.x[1]), "peak_h": p, "end_h": e,
            "peak_time": str(START + pd.Timedelta(hours=p)), "end_time": str(START + pd.Timedelta(hours=e))}, d


def metrics(y: pd.Series, fit: np.ndarray, n_train: int) -> dict:
    yv = y.values
    return {"rmse_train": rmse(yv[:n_train], fit[:n_train]), "r2_train": r2(yv[:n_train], fit[:n_train]),
            "rmse_test": rmse(yv[n_train:], fit[n_train:]), "r2_test": r2(yv[n_train:], fit[n_train:])}


def input_documents() -> list[str]:
    docs = documents()
    return sorted({docs[d]["source_document"] for d in INPUT_DATASETS if docs[d].get("source_document")})


def run_target(target: str, O: pd.DataFrame, Tf: pd.DataFrame, C: pd.Series) -> dict:
    y = O[target]
    T = Tf[target]
    cap = float(C[target])
    base = fit_all(T, y, cap)                     # M1, M2, M3 with temperature (as in step 4 of the last run)
    n = len(y.loc[:TRAIN_END])
    cold = np.clip(32 - T.loc[y.index].values, 0, None)
    height = float(cold.max())
    res = {k: {kk: vv for kk, vv in v.items()} for k, v in base.items()}
    res["M3 temperature"] = res.pop("M3 fail-restart")
    pt, d_tent = fit_tent(y.values, n, cap, height)
    f_tent = simulate_driver(pt["alpha"], pt["rho"], d_tent, cap, y.values[0])
    res["M3 tent (no weather)"] = {"params": pt, "fit": pd.Series(f_tent, index=y.index), **metrics(y, f_tent, n)}
    d_hours = np.arange(len(y), dtype=float) / (len(y) - 1) * height
    fh = fit_driver(d_hours, y.values, n, cap)
    f_hours = simulate_driver(fh.x[0], fh.x[1], d_hours, cap, y.values[0])
    res["M3 hours (no weather)"] = {"params": {"alpha": float(fh.x[0]), "rho": float(fh.x[1])},
                                    "fit": pd.Series(f_hours, index=y.index), **metrics(y, f_hours, n)}
    # placebo for M3 with temperature (as before)
    ratios = {}
    for sh in SHIFTS:
        Ts = T.copy()
        Ts.index = Ts.index + pd.Timedelta(days=sh)
        Ts = Ts.reindex(T.index).dropna()
        ratios[sh] = fit_all(Ts, y, cap)["M3 fail-restart"]["rmse_test"] / res["M3 temperature"]["rmse_test"]
    t_rmse = res["M3 temperature"]["rmse_test"]
    margins = {k: res[k]["rmse_test"] / t_rmse for k in ("M3 tent (no weather)", "M3 hours (no weather)")}
    placebo_pass = all(r >= PLACEBO_RATIO for r in ratios.values())
    margin_pass = all(m >= MARGIN for m in margins.values())
    return {"target": target, "C": cap, "models": res, "placebo_ratio": ratios, "placebo_pass": placebo_pass,
            "margins": margins, "margin_pass": margin_pass, "passed": placebo_pass and margin_pass}


def chart(r: dict, y: pd.Series, path: Path) -> None:
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, ax = plt.subplots(figsize=(10, 5.2), facecolor=SURF)
    ax.set_facecolor(SURF)
    ax.axvspan(TRAIN_END + pd.Timedelta(hours=1), END + pd.Timedelta(hours=1), color=GRID, alpha=0.45, lw=0)
    ax.axvline(TRAIN_END + pd.Timedelta(hours=1), color=INK2, lw=1, ls=(0, (4, 3)))
    ax.grid(axis="y", color=GRID, lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2)
    ax.plot(y.index, y / 1000, color=INK, lw=2.2, label="Observed (ERCOT)", zorder=5)
    for name, col in COLORS.items():
        m = r["models"][name]
        ls = "-" if "temperature" in name or "M1" in name else (0, (5, 2))
        ax.plot(y.index, m["fit"] / 1000, color=col, lw=2, ls=ls, label=f"{name}  (test RMSE {m['rmse_test'] / 1000:.2f} GW)")
    verdict = "passes" if r["passed"] else "fails"
    ax.set_title(f"{r['target'].capitalize()}: cold model vs weather-free baselines — {verdict} the H2 rule",
                 loc="left", color=INK, fontsize=12, pad=64)
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, fontsize=9, labelcolor=INK)
    ax.set_ylabel("MW out (GW)", color=INK2)
    ax.text(TRAIN_END + pd.Timedelta(hours=3), ax.get_ylim()[1] * 0.98, "test (Feb 18–19)", color=INK2, va="top")
    ax.text(START + pd.Timedelta(hours=2), ax.get_ylim()[1] * 0.98, "train", color=INK2, va="top")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURF)
    plt.close(fig)


def main(out: Path = OUT) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    O = outages()
    Tf, weights = fuel_temperature()
    C = capacity().sum()
    C["total"] = sum(C[f] for f in ("gas", "coal", "wind", "solar", "nuclear", "hydro", "storage"))
    # total: temperature weighted by all capacity
    capz = capacity()
    from analysis.cold_models import county_station
    from kg import series as S
    cs = county_station()
    assigned = capz[capz.index.isin(cs.index)].sum(axis=1)
    w = assigned.groupby(cs.reindex(assigned.index)).sum()
    Tz = S.noaa_zone_temps()
    Tf["total"] = Tz[w.index].mul(w / w.sum(), axis=1).sum(axis=1)
    results = {t: run_target(t, O, Tf, C) for t in TARGETS}
    inputs = input_documents()
    tests = []
    for t, r in results.items():
        dst = "GenerationOutage" if t == "total" else f"GenerationOutage:{t}"
        chart(r, O[t], out / f"cold_baselines_{t}.png")
        tests.append({"src_class": "WeatherCondition", "dst_class": dst, "passed": r["passed"],
                      "method": "M3 fail-restart (temperature) vs weather-free tent and hours drivers",
                      "data_sources": INPUT_DATASETS, "input_documents": inputs,
                      "rmse_test": r["models"]["M3 temperature"]["rmse_test"], "margins": r["margins"],
                      "placebo_ratio": r["placebo_ratio"], "chart": f"cold_baselines_{t}.png",
                      "link": "results/04_hypotheses/cold_baselines.md"})
    js = {"edge_tests": tests, "margin_threshold": MARGIN,
          "results": {t: {k: v for k, v in r.items() if k != "models"} |
                      {"models": {m: {k: v for k, v in d.items() if k != "fit"} for m, d in r["models"].items()}}
                      for t, r in results.items()}}
    (out / "cold_models.json").write_text(json.dumps(js, indent=1, default=str))
    return {"results": results, "json": js}


def class_grades(run: Path) -> pd.DataFrame:
    e = pd.read_parquet(run / "edges.parquet")
    c = e[e.kind == "class_caused"]
    return c.assign(edge=c.src.str.removeprefix("class:") + " → " + c.dst.str.removeprefix("class:")).set_index("edge").grade


def report(r: dict, before: pd.Series, after: pd.Series) -> str:
    L = ["# Step 1: weather-free baselines for the cold → outage models", "",
         "The fail-restart model (M3) can fit almost any single rise and fall, and the +5-day placebo used warm days, so "
         "it was bound to fail. Here M3 is refitted with the cold driver max(0, 32 − T) replaced by two weather-free "
         "drivers of the same scale: a **tent** (rises from Feb 14 00:00 to a fitted peak hour, falls to zero at a fitted "
         "end hour) and **hours** since Feb 14 00:00. Train Feb 14 00:00 – 17 23:00, test Feb 18 00:00 – 19 23:00 (ERCOT "
         "data ends 2/20 00:00). `total` is all fuels, for the fuel-less WeatherCondition → GenerationOutage family.", "",
         f"**Rule (H2):** the weather → outage edge stays above G1 only if M3 with temperature passes its placebo **and** "
         f"each weather-free M3 has test RMSE ≥ **{MARGIN}×** the temperature version (temperature at least 20% better). "
         "1.25 is the ratio the placebo rule already uses (C-M3); with 48 autocorrelated test hours a smaller gap is not "
         "a clear win.", "",
         "## Out-of-sample RMSE (MW)", "",
         "| Target | M1 instantaneous | M2 cumulative | M3 temperature | M3 tent | M3 hours | Margin tent / hours | Placebo −5 / +5 | H2 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for t, x in r["results"].items():
        m = x["models"]
        L.append(f"| {t} | {m['M1 instantaneous']['rmse_test']:,.0f} | {m['M2 cumulative']['rmse_test']:,.0f} | "
                 f"{m['M3 temperature']['rmse_test']:,.0f} | {m['M3 tent (no weather)']['rmse_test']:,.0f} | "
                 f"{m['M3 hours (no weather)']['rmse_test']:,.0f} | {x['margins']['M3 tent (no weather)']:.2f} / "
                 f"{x['margins']['M3 hours (no weather)']:.2f} | {x['placebo_ratio'][-5]:.2f} / {x['placebo_ratio'][5]:.2f} | "
                 f"{'✅ pass' if x['passed'] else '❌ fail'} |")
    L += ["", "Fitted tents (training data only, no weather input):", ""]
    for t, x in r["results"].items():
        p = x["models"]["M3 tent (no weather)"]["params"]
        tr = x["models"]["M3 tent (no weather)"]["rmse_train"] / x["models"]["M3 temperature"]["rmse_train"]
        L.append(f"- {t}: peak {p['peak_time'][5:16]}, end {p['end_time'][5:16]}; training RMSE {tr:.2f}× the temperature model's")
    L += ["", "## Grades before and after", "", "| Class edge | 2026-11-15 | 2026-11-16 |", "|---|---|---|"]
    keys = sorted(set(before.index) | set(after.index))
    changed = [k for k in keys if before.get(k) != after.get(k)]
    for k in changed:
        L.append(f"| {k} | {before.get(k, '—')} | **{after.get(k, '—')}** |")
    if not changed:
        L.append("| (no change) | | |")
    L += ["", "Independence for G3 (H4): a second passed test on a different data source, or an asserted Tier 1–2 text "
          "claim from a document that wasn't used to build the test's inputs. The ERCOT board deck published the MW-out "
          f"and frequency data, so it doesn't count (test input documents: {', '.join(r['json']['edge_tests'][0]['input_documents'])}).",
          "", "Charts: " + ", ".join(f"`cold_baselines_{t}.png`" for t in r["results"]), ""]
    return "\n".join(L)


if __name__ == "__main__":
    # the hypotheses stage starts from the graph stage's nodes and edges (later steps here update them in place)
    OUT.mkdir(parents=True, exist_ok=True)
    for f in ("nodes.parquet", "edges.parquet"):
        shutil.copy2(PREV / f, OUT / f)
    r = main()
    from kg import invariants
    invariants.main(OUT)
    before, after = class_grades(PREV), class_grades(OUT)
    (OUT / "cold_baselines.md").write_text(report(r, before, after))
    print("changed:", {k: (before.get(k), after.get(k)) for k in sorted(set(before.index) | set(after.index))
                       if before.get(k) != after.get(k)})
    for t, x in r["results"].items():
        print(t, "pass" if x["passed"] else "FAIL", {k: round(v, 2) for k, v in x["margins"].items()},
              "placebo", {k: round(v, 2) for k, v in x["placebo_ratio"].items()},
              {m: round(d["rmse_test"]) for m, d in x["models"].items()})
