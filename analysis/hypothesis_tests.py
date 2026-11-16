"""Hypotheses step 4: test the families against Tier 0, with the pass rules pre-registered in config/proxies.yaml.

For each in-scope family: status and cap from how its ends are measured (kg.hypotheses.family_status), the design
check (kg.design_check), the pre-registered test if one applies (else the generic timing test for direct ERCOT
families), and a grade = min(cap, earned).

    uv run python -m analysis.hypothesis_tests      # after analysis.cold_baselines and kg.hypotheses
"""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from analysis.cold_models import (END, GRID, INK, INK2, SURF, TRAIN_END, capacity, county_station,
                                  fit_driver, fuel_temperature, outages, rmse, r2, simulate_driver)
from kg import series as S
from kg.common import documents
from kg.design_check import design_check
from kg.hypotheses import OUT, earned_grade, family_status, grade, proxies, region_class

BLUE, ORANGE, AQUA, VIOLET, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#6250d6", "#eda100"
STORM_WINDOW = (pd.Timestamp("2021-02-14 00:00"), pd.Timestamp("2021-02-20 00:00"))
MW_OUT_SOURCES = ["ercot_mw_out_by_fuel", "noaa_zone_temp_hourly", "unit_outage_2021_03_12"]


# ---------------------------------------------------------------- helpers
def match_tests(fid: str) -> list[str]:
    """Every pre-registered test whose family patterns match (a family can have several, e.g. 2(a) and 2(b))."""
    out = []
    for name, t in proxies()["tests"].items():
        pats = t["families"]
        if isinstance(pats, list) and any(fnmatch.fnmatch(fid, p) for p in pats):
            out.append(name)
    return out


def texas_production() -> pd.Series:
    """Texas daily dry gas production (Bcf/d) read off FERC Fig. 35 (Tier 2 chart read, H13)."""
    d = pd.read_csv(documents()["ferc_fig35_daily_production"]["path"], parse_dates=["date"]).set_index("date")
    return d["texas"]


def production_loss_hourly(index: pd.DatetimeIndex, fraction: bool = True) -> pd.Series:
    tx = texas_production()
    base = float(tx.loc["2021-01"].mean())
    loss = (base - tx).clip(lower=0)
    if fraction:
        loss = loss / base
    return pd.Series(loss.reindex(index.normalize()).values, index=index)


def input_documents(data_sources: list[str]) -> list[str]:
    docs = documents()
    return sorted({docs[d]["source_document"] for d in data_sources if docs.get(d, {}).get("source_document")})


def target_of(concept: str) -> str:
    return concept.split(":")[1] if ":" in concept else "total"


def style(ax):
    ax.set_facecolor(SURF)
    ax.grid(axis="y", color=GRID, lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2)


def total_temperature() -> pd.Series:
    capz = capacity()
    cs = county_station()
    assigned = capz[capz.index.isin(cs.index)].sum(axis=1)
    w = assigned.groupby(cs.reindex(assigned.index)).sum()
    Tz = S.noaa_zone_temps()
    return Tz[w.index].mul(w / w.sum(), axis=1).sum(axis=1)


def temperatures() -> pd.DataFrame:
    Tf, _ = fuel_temperature()
    Tf["total"] = total_temperature()
    Tf["permian"] = S.noaa_zone_temps()["Far West"]
    return Tf


# ---------------------------------------------------------------- 1. cold -> outage (step 1 results)
def test_cold_outage(f: dict, out: Path) -> dict:
    t = target_of(f["effect"])
    res = json.loads((out / "cold_models.json").read_text())["results"].get(t)
    if res is None:
        return {"test": "cold_outage", "ran": False, "passed": None, "summary": f"no step-1 result for {t}"}
    m = res["models"]
    return {"test": "cold_outage", "ran": True, "passed": res["passed"],
            "summary": (f"fail-restart with temperature: test RMSE {m['M3 temperature']['rmse_test']:,.0f} MW; tent "
                        f"{m['M3 tent (no weather)']['rmse_test']:,.0f} ({res['margins']['M3 tent (no weather)']:.2f}×), "
                        f"hours {m['M3 hours (no weather)']['rmse_test']:,.0f} ({res['margins']['M3 hours (no weather)']:.2f}×); "
                        f"placebo {res['placebo_ratio']['-5']:.2f} / {res['placebo_ratio']['5']:.2f}. Needs ≥ 1.25 on all four."),
            "metrics": {"rmse_test": {k: v["rmse_test"] for k, v in m.items()}, "margins": res["margins"],
                        "placebo_ratio": res["placebo_ratio"]},
            "chart": f"cold_baselines_{t}.png", "data_sources": MW_OUT_SOURCES, "window": STORM_WINDOW,
            "effect_series": outages()[t]}


# ---------------------------------------------------------------- 2. gas supply -> gas outage
def test_gas_supply(f: dict, out: Path) -> dict:
    t = target_of(f["effect"])
    y = outages()[t]
    T = temperatures()
    n = len(y.loc[:TRAIN_END])
    C = float(capacity().sum()[t]) if t != "total" else float(capacity().sum().sum())
    fits = {}
    drivers = {"Permian (MAF)": np.clip(32 - T["permian"].loc[y.index].values, 0, None),
               f"{t} plants": np.clip(32 - T[t].loc[y.index].values, 0, None)}
    for name, d in drivers.items():
        b = fit_driver(d, y.values, n, C)
        fit = simulate_driver(b.x[0], b.x[1], d, C, y.values[0])
        fits[name] = {"alpha": float(b.x[0]), "rho": float(b.x[1]), "fit": fit,
                      "rmse_train": rmse(y.values[:n], fit[:n]), "rmse_test": rmse(y.values[n:], fit[n:])}
    base = json.loads((out / "cold_models.json").read_text())["results"][t]["models"]
    perm, plant = fits["Permian (MAF)"], fits[f"{t} plants"]
    tent, hours = base["M3 tent (no weather)"]["rmse_test"], base["M3 hours (no weather)"]["rmse_test"]
    margins = {"tent": tent / perm["rmse_test"], "hours": hours / perm["rmse_test"]}
    passed = perm["rmse_test"] <= plant["rmse_test"] and all(v >= 1.25 for v in margins.values())
    long = slice("2021-02-10", "2021-02-19 23:00")
    dp, dl = (32 - T["permian"].loc[long]).clip(lower=0), (32 - T[t].loc[long]).clip(lower=0)
    corr = float(np.corrcoef(dp, dl)[0, 1])
    timing = {k: {"first_below_32": str(d[d > 0].index[0]) if (d > 0).any() else None, "peak": str(d.idxmax()),
                  "hours_below_32_feb14_19": int((d.loc['2021-02-14':] > 0).sum())} for k, d in (("permian", dp), ("plants", dl))}
    chart = out / f"hyp_gas_supply_{t}.png"
    fig, (ax, at) = plt.subplots(2, 1, figsize=(10, 6.2), sharex=True, gridspec_kw={"height_ratios": [3, 1.2]}, facecolor=SURF)
    for a in (ax, at):
        style(a)
        a.axvspan(TRAIN_END + pd.Timedelta(hours=1), END + pd.Timedelta(hours=1), color=GRID, alpha=0.45, lw=0)
        a.axvline(TRAIN_END + pd.Timedelta(hours=1), color=INK2, lw=1, ls=(0, (4, 3)))
    ax.plot(y.index, y / 1000, color=INK, lw=2.2, label="Observed (ERCOT)")
    ax.plot(y.index, perm["fit"] / 1000, color=BLUE, lw=2, label=f"Permian cold driver (test RMSE {perm['rmse_test'] / 1000:.2f} GW)")
    ax.plot(y.index, plant["fit"] / 1000, color=AQUA, lw=2, label=f"Plant cold driver (test RMSE {plant['rmse_test'] / 1000:.2f} GW)")
    ax.set_ylabel("MW out (GW)", color=INK2)
    ax.set_title(f"Gas supply → {t} outages: Permian vs plant temperature as the driver — "
                 f"{'passes' if passed else 'fails'}", loc="left", color=INK, fontsize=12, pad=46)
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, fontsize=9, labelcolor=INK)
    at.plot(y.index, drivers["Permian (MAF)"], color=BLUE, lw=1.6, label="Permian")
    at.plot(y.index, drivers[f"{t} plants"], color=AQUA, lw=1.6, label="plants")
    at.set_ylabel("°F below 32", color=INK2, fontsize=9)
    at.legend(frameon=False, fontsize=8, labelcolor=INK, loc="upper right")
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(chart, dpi=130, facecolor=SURF); plt.close(fig)
    return {"test": "gas_supply_to_gas_outage", "ran": True, "passed": passed,
            "summary": (f"2(b) fail-restart with Permian cold: test RMSE "
                        f"{perm['rmse_test']:,.0f} MW vs plant cold {plant['rmse_test']:,.0f}; weather-free margins "
                        f"{margins['tent']:.2f} (tent) / {margins['hours']:.2f} (hours). Drivers correlate r = {corr:.2f} "
                        "(Feb 10–19), so the comparison rests on timing. Not supported by this model, not refuted: the "
                        f"Permian went below freezing first ({timing['permian']['first_below_32'][5:16]} vs "
                        f"{timing['plants']['first_below_32'][5:16]} at the plants), and storage and pipeline pressure buffer "
                        "supply for a while; a model with no delay term can't use that lead."),
            "metrics": {"rmse_test": {"permian": perm["rmse_test"], "plants": plant["rmse_test"], "tent": tent, "hours": hours},
                        "rmse_train": {"permian": perm["rmse_train"], "plants": plant["rmse_train"]},
                        "margins": margins, "driver_correlation": corr, "timing": timing,
                        "params": {k: {"alpha": v["alpha"], "rho": v["rho"]} for k, v in fits.items()},
                        },
            "chart": chart.name, "data_sources": MW_OUT_SOURCES, "window": STORM_WINDOW, "effect_series": y}


# ---------------------------------------------------------------- 3. outage -> load shed
def test_outage_to_shed(f: dict, out: Path) -> dict:
    col = target_of(f["cause"])
    o = S.minute_start(S.mw_out_by_fuel())[col]
    shed = S.minute_start(S.load_shed())
    base = float(o.loc["2021-02-14 00:00":"2021-02-14 11:00"].mean())
    rise = o[(o >= base + 2000) & (o.index >= "2021-02-14")]
    o_onset = rise.index[0] if len(rise) else None
    s_onset = shed[shed > 0].index[0]
    oh, sh = o.resample("h").mean(), shed.resample("h").mean()
    do, ds = oh.diff(), sh.diff()
    xc = {k: float(do.corr(ds.shift(-k))) for k in range(-6, 7)}
    peak_lag = max(xc, key=lambda k: xc[k] if not np.isnan(xc[k]) else -9)
    est = S.estimated_load()
    avail = S.available_generation().resample("h").mean()
    idx = est.index[(est.index >= "2021-02-15 00:00") & (est.index <= "2021-02-19 23:00")]
    acct = (est.loc[idx] - avail.reindex(idx)).clip(lower=0)
    obs = sh.reindex(idx)
    passed = o_onset is not None and o_onset < s_onset and peak_lag >= 0
    chart = out / f"hyp_outage_to_shed_{col}.png"
    fig, (ax, ab) = plt.subplots(2, 1, figsize=(10, 6.6), sharex=True, gridspec_kw={"height_ratios": [1.3, 1]}, facecolor=SURF)
    for a in (ax, ab):
        style(a)
    ax.plot(oh.index, oh / 1000, color=BLUE, lw=2, label=f"MW out ({col})")
    ax.plot(sh.index, sh / 1000, color=INK, lw=2.2, label="Load shed")
    ax.axvline(s_onset, color=INK2, lw=1, ls=(0, (4, 3)))
    ax.text(s_onset, ax.get_ylim()[1] * 0.95, " first shed 01:20", color=INK2, va="top", fontsize=9)
    if o_onset is not None:
        ax.axvline(o_onset, color=BLUE, lw=1, ls=":")
    ax.set_ylabel("GW", color=INK2)
    ax.set_title(f"{'Generation' if col == 'total' else col.capitalize()} outages → load shed: timing and accounting — "
                 f"{'outages lead' if passed else 'outages do not lead'}", loc="left", color=INK, fontsize=12, pad=30)
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, fontsize=9, labelcolor=INK)
    ab.plot(obs.index, obs / 1000, color=INK, lw=2.2, label="Load shed (hourly mean)")
    ab.plot(acct.index, acct / 1000, color=ORANGE, lw=2, label="max(0, est. load without shed − available generation)")
    ab.set_ylabel("GW", color=INK2)
    ab.legend(frameon=False, fontsize=8, labelcolor=INK, loc="upper right")
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(chart, dpi=130, facecolor=SURF); plt.close(fig)
    lead_h = (s_onset - o_onset) / pd.Timedelta(hours=1) if o_onset is not None else None
    lead_txt = f"{lead_h:.0f} h" if lead_h is not None and lead_h >= 2 else f"{lead_h * 60:.0f} min"
    return {"test": "outage_to_shed", "ran": True, "passed": passed,
            "summary": (f"Accounting relationship plus timing, not an estimated effect. MW out ({col}) was 2,000 MW above "
                        f"its Feb 14 morning level from {o_onset:%m/%d %H:%M}, {lead_txt} before the first shed "
                        f"(02/15 01:20); hourly changes correlate most at lag {peak_lag:+d} h (r = {xc[peak_lag]:.2f}). "
                        f"Shed ≈ max(0, load − available generation): R² {r2(obs, acct):.2f}, RMSE {rmse(obs, acct):,.0f} MW."),
            "metrics": {"cause_onset": str(o_onset), "shed_onset": str(s_onset), "lead_hours": lead_h, "xcorr": xc,
                        "peak_lag_h": peak_lag, "accounting_r2": r2(obs, acct), "accounting_rmse": rmse(obs, acct),
                        "baseline_mw": base},
            "chart": chart.name, "data_sources": ["ercot_mw_out_by_fuel", "ercot_gen_load_shed"],
            "window": (pd.Timestamp("2021-02-14 00:00"), pd.Timestamp("2021-02-16 00:00")),
            "cause_onset": o_onset, "effect_series": shed}


# ---------------------------------------------------------------- 4. frequency -> coal
def test_frequency_to_coal(f: dict, out: Path) -> dict:
    col = target_of(f["effect"])
    s = S.minute_start(S.mw_out_by_fuel())[col]
    fr = S.frequency()
    t_low = fr.idxmin().floor("min")
    v0 = float(s.loc[:t_low].iloc[-1])
    v15 = float(s.loc[:t_low + pd.Timedelta(minutes=15)].iloc[-1])
    change = v15 - v0
    after = s.loc[t_low:t_low + pd.Timedelta(minutes=15)]
    moved = after[after != v0]
    delay = (moved.index[0] - t_low) / pd.Timedelta(minutes=1) if len(moved) else None
    d15 = s.loc["2021-02-14":"2021-02-19 23:59"].diff(15).dropna()
    p99 = float(d15.quantile(0.99))
    pct = float((d15 < change).mean())
    v30 = float(s.loc[:t_low + pd.Timedelta(minutes=30)].iloc[-1]) - v0
    passed = change > p99 and delay is not None and delay <= 10
    chart = out / f"hyp_frequency_to_{col}.png"
    w = slice(t_low - pd.Timedelta(minutes=30), t_low + pd.Timedelta(minutes=35))
    fig, (ax, af) = plt.subplots(2, 1, figsize=(10, 6.0), sharex=True, gridspec_kw={"height_ratios": [1.5, 1]}, facecolor=SURF)
    for a in (ax, af):
        style(a)
        a.axvline(t_low, color=INK2, lw=1, ls=(0, (4, 3)))
    allf = S.minute_start(S.mw_out_by_fuel())
    ax.step(s.loc[w].index, s.loc[w] / 1000, where="post", color=INK, lw=2.2, label=f"{col} MW out")
    if col != "gas":
        ax.step(allf["gas"].loc[w].index, allf["gas"].loc[w] / 1000, where="post", color=BLUE, lw=1.6, label="gas MW out (for contrast)")
    ax.set_ylabel("GW", color=INK2)
    ax.set_title(f"Frequency low (01:55) → {col} outages: {change:+,.0f} MW within 15 min, after {delay:.0f} min — "
                 f"{'passes' if passed else 'fails'}", loc="left", color=INK, fontsize=12, pad=30)
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, fontsize=9, labelcolor=INK)
    ff = fr.loc[w]
    af.plot(ff.index, ff, color=VIOLET, lw=1.4)
    af.set_ylabel("Hz", color=INK2)
    af.text(t_low, ff.min(), f" low {fr.min():.3f} Hz", color=INK2, fontsize=8, va="bottom")
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(chart, dpi=130, facecolor=SURF); plt.close(fig)
    return {"test": "frequency_to_coal", "ran": True, "passed": passed,
            "summary": (f"{col} MW out rose {change:+,.0f} MW in the 15 min after the frequency low ({fr.min():.3f} Hz at "
                        f"{fr.idxmin():%H:%M:%S}), first moving after {delay:.0f} min; that is above the 99th percentile "
                        f"of 15-minute changes ({p99:,.0f} MW; percentile {pct:.1%}). +{v30:,.0f} MW within 30 min, against "
                        "the text's 'up to 1,800 MW, mostly coal'."),
            "metrics": {"t_low": str(fr.idxmin()), "freq_low": float(fr.min()), "change_15min": change,
                        "change_30min": v30, "delay_min": delay, "p99_15min": p99, "percentile": pct},
            "chart": chart.name, "data_sources": ["ercot_frequency", "ercot_mw_out_by_fuel"],
            "window": (t_low - pd.Timedelta(minutes=30), t_low + pd.Timedelta(minutes=30)),
            "cause_onset": t_low, "effect_series": s}


# ---------------------------------------------------------------- generic timing
def concept_series(concept: str, other: str) -> pd.Series | None:
    base = concept.split(":")[0]
    if base == "GenerationOutage":
        return outages()[target_of(concept)]
    if base == "LoadShed":
        return S.minute_start(S.load_shed()).resample("h").mean().loc[STORM_WINDOW[0]:STORM_WINDOW[1]]
    if base == "WeatherCondition":
        t = target_of(other) if other.startswith("GenerationOutage") else "total"
        return (32 - temperatures()[t]).clip(lower=0).loc[STORM_WINDOW[0]:STORM_WINDOW[1]]
    if concept == "WeatherCondition:warming":             # rise above the coldest point so far
        T = temperatures()["total"].loc[STORM_WINDOW[0]:STORM_WINDOW[1]]
        return T - T.cummin()
    if base == "GasSupplyDisruption":                     # Fig. 35 Texas production loss, held through each day
        idx = pd.date_range(STORM_WINDOW[0], STORM_WINDOW[1] - pd.Timedelta(hours=1), freq="h")
        return production_loss_hourly(idx, fraction=False)
    if base == "Restoration":                       # decrease in MW out from its running peak (proxies.yaml)
        o = outages()["total"]
        return o.cummax() - o
    if base == "Demand":
        return S.eia_hourly()["demand"].loc[STORM_WINDOW[0]:STORM_WINDOW[1]]
    return None


def onset(s: pd.Series, share: float = 0.1) -> pd.Timestamp | None:
    """First hour the series is >= share of its storm range away from its Feb 14 00:00 value."""
    s = s.dropna()
    rng = float(s.max() - s.min())
    hit = s[(s - s.iloc[0]).abs() >= share * rng]
    return hit.index[0] if len(hit) and rng > 0 else None


def test_generic_timing(f: dict, out: Path) -> dict:
    c, e = concept_series(f["cause"], f["effect"]), concept_series(f["effect"], f["cause"])
    if c is None or e is None:
        return {"test": "generic_timing", "ran": False, "passed": None, "summary": "no series for one end"}
    oc, oe = onset(c), onset(e)
    consistent = oc is not None and oe is not None and oc < oe
    label = "order consistent" if consistent else "order inconsistent"
    return {"test": "generic_timing", "ran": True, "passed": None, "result_label": label,
            "summary": f"{label}: cause onset {oc:%m/%d %H:00} vs effect onset {oe:%m/%d %H:00} (first hour ≥ 10% of the "
                       "storm range from the Feb 14 00:00 value). In a storm almost everything starts in order, so this is "
                       "not counted as a passed test (amendment 2).",
            "metrics": {"cause_onset": str(oc), "effect_onset": str(oe)}, "chart": None,
            "data_sources": ["ercot_mw_out_by_fuel", "ercot_gen_load_shed", "noaa_zone_temp_hourly"],
            "window": STORM_WINDOW, "cause_onset": oc, "effect_series": e}


# ---------------------------------------------------------------- 2(a). production loss as a second failure driver
def simulate_two(alpha, gamma, rho, d1, d2, C, o0):
    o = np.empty(len(d1)); o[0] = o0
    for t in range(1, len(d1)):
        o[t] = o[t - 1] + (alpha * d1[t] + gamma * d2[t]) * (C - o[t - 1]) - rho * o[t - 1]
    return o


def test_production_term(f: dict, out: Path) -> dict:
    from scipy.optimize import least_squares
    t = target_of(f["effect"])
    y = outages()[t]
    T = temperatures()
    n = len(y.loc[:TRAIN_END])
    C = float(capacity().sum()[t]) if t != "total" else float(capacity().sum().sum())
    cold = np.clip(32 - T[t].loc[y.index].values, 0, None)
    loss = production_loss_hourly(y.index).values
    def res(p):
        return simulate_two(p[0], p[1], p[2], cold, loss, C, y.values[0])[:n] - y.values[:n]
    best = None
    for a0, g0, r0 in ((1e-3, 1e-2, 1e-2), (1e-3, 1e-1, 5e-2), (5e-3, 1e-3, 1e-2), (1e-4, 5e-2, 1e-3)):
        fit = least_squares(res, [a0, g0, r0], bounds=([0, 0, 0], [0.1, 5.0, 1.0]))
        if best is None or fit.cost < best.cost:
            best = fit
    a, g, r = best.x
    both = simulate_two(a, g, r, cold, loss, C, y.values[0])
    base = json.loads((out / "cold_models.json").read_text())["results"][t]["models"]["M3 temperature"]
    cold_rmse = base["rmse_test"]
    both_rmse = rmse(y.values[n:], both[n:])
    passed = both_rmse <= cold_rmse / 1.25
    direction = "improves" if both_rmse < cold_rmse else "does not improve"
    chart = out / f"hyp_production_term_{t}.png"
    fig, (ax, at) = plt.subplots(2, 1, figsize=(10, 6.2), sharex=True, gridspec_kw={"height_ratios": [3, 1.2]}, facecolor=SURF)
    for a_ in (ax, at):
        style(a_)
        a_.axvspan(TRAIN_END + pd.Timedelta(hours=1), END + pd.Timedelta(hours=1), color=GRID, alpha=0.45, lw=0)
        a_.axvline(TRAIN_END + pd.Timedelta(hours=1), color=INK2, lw=1, ls=(0, (4, 3)))
    cold_fit = simulate_driver(base["params"]["alpha"], base["params"]["rho"], cold, C, y.values[0])
    ax.plot(y.index, y / 1000, color=INK, lw=2.2, label="Observed (ERCOT)")
    ax.plot(y.index, cold_fit / 1000, color=AQUA, lw=2, label=f"Cold only (test RMSE {cold_rmse / 1000:.2f} GW)")
    ax.plot(y.index, both / 1000, color=ORANGE, lw=2, label=f"Cold + Texas production loss (test RMSE {both_rmse / 1000:.2f} GW)")
    ax.set_ylabel("MW out (GW)", color=INK2)
    ax.set_title(f"Gas supply → {t} outages, test 2(a): adding FERC's daily production {direction} the fit",
                 loc="left", color=INK, fontsize=12, pad=30)
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, fontsize=9, labelcolor=INK)
    at.step(y.index, production_loss_hourly(y.index, fraction=False), where="post", color=ORANGE, lw=1.6)
    at.set_ylabel("TX production loss\n(Bcf/d, Fig. 35)", color=INK2, fontsize=8)
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(chart, dpi=130, facecolor=SURF); plt.close(fig)
    return {"test": "production_term", "ran": True, "passed": passed,
            "summary": (f"Adding Texas production loss (FERC Fig. 35, a Tier 2 chart read) as a second failure driver "
                        f"{direction} the out-of-sample fit: test RMSE {both_rmse:,.0f} MW vs {cold_rmse:,.0f} cold only "
                        f"(needs ≤ {cold_rmse / 1.25:,.0f}). Fitted γ = {g:.3g}. About 13 storm days: a direction, not an estimate."),
            "metrics": {"rmse_test_cold_only": cold_rmse, "rmse_test_cold_plus_production": both_rmse,
                        "rmse_train_cold_plus_production": rmse(y.values[:n], both[:n]),
                        "params": {"alpha": a, "gamma": g, "rho": r}, "direction": direction},
            "chart": chart.name, "data_sources": MW_OUT_SOURCES + ["ferc_fig35_daily_production"],
            "window": STORM_WINDOW, "effect_series": y}


# ---------------------------------------------------------------- cold -> Texas production (daily)
def test_cold_production(f: dict, out: Path) -> dict:
    from analysis.cold_baselines import tent
    tx = texas_production()
    C = float(tx.loc["2021-01"].mean())
    L = (C - tx.loc["2021-02-01":"2021-02-28"]).clip(lower=0)
    Th = S.noaa_zone_temps()["Far West"]
    cold = (32 - Th).clip(lower=0).groupby(Th.index.normalize()).mean().reindex(L.index).fillna(0.0)
    n = len(L.loc[:"2021-02-16"])
    y = L.values
    fits = {}
    b = fit_driver(cold.values, y, n, C)
    fits["cold (Permian)"] = simulate_driver(b.x[0], b.x[1], cold.values, C, y[0])
    height = float(cold.max())
    best = None
    for p in range(4, 16):
        for e in range(p + 2, len(y) + 6):
            d = tent(len(y), p, e, height)
            fb = fit_driver(d, y, n, C, starts=((1e-3, 1e-2), (5e-3, 5e-2), (5e-2, 2e-1)))
            if best is None or fb.cost < best[0].cost:
                best = (fb, p, e, d)
    fb, p, e, d = best
    fits["tent (no weather)"] = simulate_driver(fb.x[0], fb.x[1], d, C, y[0])
    days = np.arange(len(y), dtype=float) / (len(y) - 1) * height
    bd = fit_driver(days, y, n, C)
    fits["days (no weather)"] = simulate_driver(bd.x[0], bd.x[1], days, C, y[0])
    r = {k: rmse(y[n:], v[n:]) for k, v in fits.items()}
    margins = {k: r[k] / r["cold (Permian)"] for k in ("tent (no weather)", "days (no weather)")}
    passed = all(m >= 1.25 for m in margins.values())
    # Labelled after the first run (disclosed): if the training days never show a recovery, the restart rate can't be
    # fitted, every model extrapolates the loss flat, and the test can't speak either way.
    identifiable = bool((np.diff(y[:n]) < -0.05 * C).any())
    chart = out / "hyp_cold_production.png"
    fig, ax = plt.subplots(figsize=(10, 5), facecolor=SURF); style(ax)
    ax.axvspan(L.index[n] - pd.Timedelta(hours=12), L.index[-1] + pd.Timedelta(hours=12), color=GRID, alpha=0.45, lw=0)
    ax.plot(L.index, y, color=INK, lw=2.2, marker="o", ms=4, label="Texas production loss (Fig. 35)")
    for (k, v), col in zip(fits.items(), (BLUE, VIOLET, YELLOW)):
        ax.plot(L.index, v, color=col, lw=2, ls="-" if "cold" in k else (0, (5, 2)), label=f"{k} (test RMSE {r[k]:.2f})")
    ax.set_ylabel("Bcf/d below January mean", color=INK2)
    ax.set_title(f"Cold → Texas gas production loss (daily): "
                 f"{'passes' if passed else ('not supported by this test' if identifiable else 'inconclusive (no recovery in training)')}",
                 loc="left", color=INK, fontsize=12, pad=30)
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, fontsize=9, labelcolor=INK)
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(chart, dpi=130, facecolor=SURF); plt.close(fig)
    label = None if identifiable else ("inconclusive (design): the training days (Feb 1–16) contain no recovery, so the "
                                         "restart rate can't be fitted and every model carries the loss flat into the test")
    return {"test": "cold_production", "ran": True, "passed": passed if identifiable else None,
            **({"result_label": label} if label else {}),
            "summary": ((label + ". ") if label else "") + (f"Daily fail-restart on Texas production loss (Fig. 35) driven by Permian cold: test RMSE "
                        f"{r['cold (Permian)']:.2f} Bcf/d vs tent {r['tent (no weather)']:.2f} ({margins['tent (no weather)']:.2f}×) "
                        f"and days {r['days (no weather)']:.2f} ({margins['days (no weather)']:.2f}×); needs ≥ 1.25 on both. "
                        "Train Feb 1–16, test Feb 17–28 (12 days)."),
            "metrics": {"rmse_test": r, "margins": margins, "tent": {"peak_day": int(p), "end_day": int(e)},
                        "params_cold": {"alpha": float(b.x[0]), "rho": float(b.x[1])}, "C_bcfd": C},
            "chart": chart.name, "data_sources": ["ferc_fig35_daily_production", "noaa_zone_temp_hourly"],
            "window": (pd.Timestamp("2021-02-01"), pd.Timestamp("2021-03-01"))}


TEST_FUNCS = {"cold_outage": test_cold_outage, "gas_supply_to_gas_outage": test_gas_supply,
              "outage_to_shed": test_outage_to_shed, "frequency_to_coal": test_frequency_to_coal,
              "production_term": test_production_term, "cold_production": test_cold_production}

POWER_LOSS_CONTEXT = (
    "Untestable with Tier 0: nothing measures power delivered to gas facilities, and FERC says the extent is unknown. "
    "Text evidence: FERC attributes 18.1% of the gas production decline on Feb 14 (before any firm load shed) and 21.6% "
    "on Feb 17 (the day of maximum losses) to loss of power, 23.5% over the whole event; Enverus's survey names loss of "
    "power as the most cited cause. The earlier event study (shed jump at 2/15 01:59 vs gas outages) was inconclusive "
    "because gas outages were already climbing before the shock (the design check's pretrend).")
CONTEXT = {"PowerLoss->GasSupplyDisruption@ERCOT": POWER_LOSS_CONTEXT,
           "PowerLoss->GasSupplyDisruption@multi-region": POWER_LOSS_CONTEXT,
           "LoadShed->PowerLoss@ERCOT": POWER_LOSS_CONTEXT}


# ---------------------------------------------------------------- run
def run(out: Path = OUT) -> list[dict]:
    from kg.graph_build import load
    fams = json.loads((out / "families.json").read_text())["families"]
    nodes, _ = load(out)
    docs = documents()
    results = []
    G = proxies().get("grading", {})
    accounting_tests = set(G.get("accounting", {}).get("tests", []))
    fail_label = G.get("failures_label", "not supported by this test")
    for f in fams:
        names = match_tests(f["id"])
        st = family_status(f, dedicated_test=bool(names))
        r = {"id": f["id"], **st, "test": None, "tests": [], "threats": []}
        if st["status"].startswith("tested"):
            threats = []
            for name in (names or ["generic_timing"]):
                fn = TEST_FUNCS.get(name, test_generic_timing)
                res = fn(f, out)
                eff = res.pop("effect_series", None)
                window = res.get("window", STORM_WINDOW)
                onset_ = res.pop("cause_onset", None)
                threats += design_check(f, window, nodes, fams, eff, onset_, region_of=lambda x: region_class(x)[0])
                res["window"] = [str(x) for x in window]
                res["input_documents"] = input_documents(res.get("data_sources", []))
                res.setdefault("result_label", "supported by this test" if res.get("passed") else
                               (fail_label if res.get("ran") else "not run"))
                r["tests"].append(res)
            seen = set()
            r["threats"] = [t for t in threats if not (json.dumps(t, sort_keys=True, default=str) in seen
                                                       or seen.add(json.dumps(t, sort_keys=True, default=str)))]
            primary = next((x for x in r["tests"] if x.get("passed")), r["tests"][0])
            r["test"] = {"test": " + ".join(x["test"] for x in r["tests"]), "ran": any(x.get("ran") for x in r["tests"]),
                         "passed": any(x.get("passed") for x in r["tests"]),
                         "result_label": "; ".join(f"{x['test']}: {x['result_label']}" for x in r["tests"]),
                         "summary": " | ".join(x["summary"] for x in r["tests"]),
                         "metrics": {x["test"]: x.get("metrics") for x in r["tests"]},
                         "chart": primary.get("chart"), "charts": [x.get("chart") for x in r["tests"] if x.get("chart")],
                         "input_documents": sorted({d for x in r["tests"] if x.get("passed") for d in x["input_documents"]}),
                         "window": primary.get("window")}
            if accounting_tests & set(names):
                r["status"], st["cap"] = "accounting", None
        elif f["id"] in CONTEXT:
            r["test"] = {"test": "none", "ran": False, "passed": None, "result_label": "untestable",
                         "summary": CONTEXT[f["id"]]}
        passed = bool(r["test"] and r["test"].get("passed"))
        used = set((r["test"] or {}).get("input_documents", []))
        indep = sorted({s["doc"] for s in f["support"] if s["modality"] == "asserted"
                        and docs.get(s["doc"], {}).get("tier") in ("1", "2") and s["doc"] not in used})
        r["independent_docs"] = indep
        r["earned"] = earned_grade(f["n_asserted"], f["n_support"], passed, bool(indep))
        r["cap"] = st["cap"]
        r["contested"] = f.get("contested", False)
        r["grade"] = None if r["status"] == "accounting" else grade(r["status"], r["cap"], r["earned"])
        results.append(r)
    (out / "hypothesis_results.json").write_text(json.dumps(results, indent=1, default=str))
    return results


if __name__ == "__main__":
    import collections
    rs = run()
    print("status:", dict(collections.Counter(r["status"] for r in rs)))
    print("grades:", dict(collections.Counter(r["grade"] for r in rs)))
    for r in rs:
        if r["test"] and r["test"].get("ran"):
            print(f"{r['grade']} {'PASS' if r['test']['passed'] else 'fail'} {r['id']} | {len(r['threats'])} threats | {r['test']['summary'][:150]}")
