"""Hypotheses step 5: frontend files, the overview figure and the report.

    uv run python -m analysis.hypothesis_outputs     # after analysis.hypothesis_tests
Writes hypotheses.json, hypothesis_graph.json, hypothesis_overview.png and hypotheses_report.md.
"""
from __future__ import annotations

import collections
import json
import textwrap
from pathlib import Path

import matplotlib
import matplotlib.dates

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

from kg.common import documents
from analysis.hypothesis_tests import style
from kg.hypotheses import OUT, concept_label, proxies

INK, INK2, MUTED, SURF, GRIDC = "#0b0b0b", "#52514e", "#8a8984", "#fcfcfb", "#e4e3df"
GRADE_COLOR = {"G0": "#c3ccd8", "G1": "#8fb4e3", "G2": "#3f80d4", "G3": "#163f78", None: "#d9d8d3"}
STATUS_STYLE = {"tested_direct": "-", "tested_proxy": (0, (6, 3)), "untestable": (0, (1.5, 2.5)),
                "correlational": (0, (1, 1)), "accounting": (0, (8, 2, 1, 2))}
STATUS_RANK = {"tested_direct": 0, "tested_proxy": 1, "accounting": 2, "untestable": 3, "correlational": 4}
GRADE_RANK = {"G3": 0, "G2": 1, "G1": 2, "G0": 3, None: 4}
POS = {"WeatherCondition": (0.07, 0.62), "WeatherCondition:warming": (0.07, 0.14), "InstitutionalAction": (0.07, 0.93),
       "GasSupplyDisruption": (0.30, 0.82), "PowerLoss": (0.30, 0.46), "FacilityShutdown": (0.30, 0.14),
       "GenerationOutage:gas": (0.53, 0.88), "GenerationOutage:coal": (0.53, 0.66),
       "GenerationOutage:wind": (0.53, 0.44), "GenerationOutage": (0.53, 0.20), "GridEmergency": (0.76, 0.42),
       "MarketAction": (0.76, 0.88), "LoadShed": (0.93, 0.66), "Restoration": (0.93, 0.14)}
SERIES_LABEL = {"noaa_zone_temp_hourly": ("0a", "NOAA hourly temperature\n(9 zone stations)"),
                "ercot_mw_out_by_fuel": ("0b", "ERCOT MW out by fuel\n(minute)"),
                "ercot_gen_load_shed": ("0b", "ERCOT load shed, available gen.,\nest. load (minute / 15 s / hour)"),
                "ercot_frequency": ("0b", "ERCOT frequency\n(1 s, 42 min on 2/15)"),
                "eia_ercot_hourly": ("0b", "EIA ERCOT demand\n(hourly)"),
                "unit_outage_2021_03_12": ("0b", "Unit outage list 3/12\n(capacity weights)"),
                "ferc_fig35_daily_production": ("2", "FERC Fig. 35 daily\ngas production (chart read)")}


# ---------------------------------------------------------------- data products
def merged(out: Path) -> list[dict]:
    fams = {f["id"]: f for f in json.loads((out / "families.json").read_text())["families"]}
    res = {r["id"]: r for r in json.loads((out / "hypothesis_results.json").read_text())}
    docs = documents()
    rows = []
    for fid, f in fams.items():
        r = res[fid]
        t = r.get("test") or {}
        rows.append({
            "id": fid, "claim": f["claim"], "cause": f["cause"], "effect": f["effect"],
            "cause_label": concept_label(f["cause"]), "effect_label": concept_label(f["effect"]),
            "fuel": f["fuel"], "region": f["region"], "origin": f["origin"], "headline": f["headline"],
            "within_type": f["within_type"], "supply_chain": f.get("supply_chain", False), "mechanism": f.get("mechanism"),
            "support": [_ev_row(s, docs) for s in f["support"]],
            "contrary": [_ev_row(s, docs) for s in f.get("contrary", [])],
            "support_by_tier": f["support_by_tier"], "contrary_by_tier": f.get("contrary_by_tier", {}),
            "n_support": f["n_support"], "n_contrary": f.get("n_contrary", 0), "n_asserted": f["n_asserted"],
            "contested": f.get("contested", False),
            "first_appeared": f["first_appeared"], "first_appeared_doc": f["first_appeared_doc"],
            "measured": {"cause": _measure(r["cause_measure"]), "effect": _measure(r["effect_measure"])},
            "status": r["status"], "cap": r["cap"], "grade": r["grade"], "earned": r["earned"], "needs": r["needs"],
            "independent_docs": r["independent_docs"],
            "test": {k: t.get(k) for k in ("test", "ran", "passed", "result_label", "summary", "metrics",
                                           "input_documents", "window")} if t else None,
            "tests": [{k: x.get(k) for k in ("test", "ran", "passed", "result_label", "summary", "chart")}
                      for x in r.get("tests", [])],
            "threats": r["threats"], "chart": t.get("chart") if t else None,
            "charts": t.get("charts", []) if t else []})
    order = {"G3": 0, "G2": 1, "G1": 2, "G0": 3, None: 4}
    return sorted(rows, key=lambda x: (order[x["grade"]], -x["n_support"], x["id"]))


def _ev_row(s: dict, docs: dict) -> dict:
    return {"doc": s["doc"], "tier": s["tier"], "flags": s["flags"], "modality": s["modality"],
            "reliability": s["reliability"], "quote_id": s["quote_id"], "quote": s["quote"], "predicate": s["predicate"],
            "publisher": docs.get(s["doc"], {}).get("publisher"), "via": s.get("via"),
            "cause_type_raw": s.get("cause_type_raw"), "effect_type_raw": s.get("effect_type_raw")}


def pairs(rows: list[dict]) -> list[dict]:
    """Concept-pair rollup across regions, for the visual: the best-tested family sets status and grade."""
    out = {}
    for r in rows:
        if r["within_type"] and not r.get("supply_chain"):
            continue
        k = (r["cause"], r["effect"])
        p = out.setdefault(k, {"src": r["cause"], "dst": r["effect"], "families": [], "n_support": 0, "n_contrary": 0,
                               "support_by_tier": collections.Counter(), "origin": set(), "first_appeared": None,
                               "contested": False, "headline": False})
        p["families"].append(r["id"]); p["n_support"] += r["n_support"]; p["n_contrary"] += r["n_contrary"]
        p["support_by_tier"].update(r["support_by_tier"]); p["origin"].add(r["origin"])
        p["contested"] |= r["contested"]; p["headline"] |= r["headline"]
        if r["first_appeared"] and (p["first_appeared"] is None or r["first_appeared"] < p["first_appeared"]):
            p["first_appeared"] = r["first_appeared"]
        cur = p.get("best")
        if cur is None or (STATUS_RANK[r["status"]], GRADE_RANK[r["grade"]]) < (STATUS_RANK[cur["status"]], GRADE_RANK[cur["grade"]]):
            p["best"] = r
    res = []
    for p in out.values():
        b = p.pop("best")
        o = p.pop("origin")
        res.append({**p, "support_by_tier": dict(p["support_by_tier"]), "status": b["status"], "grade": b["grade"],
                    "result_label": (b["test"] or {}).get("result_label"), "best_family": b["id"],
                    "origin": "both" if "both" in o or len(o) > 1 else o.pop(),
                    "regions": sorted({x.split("@")[1] for x in p["families"]})})
    return res


def _measure(m: dict) -> dict:
    return {k: m.get(k) for k in ("series", "kind", "transform", "resolution", "regions", "reason", "needs", "override", "coverage")
            if m.get(k) is not None}


def graph(rows: list[dict]) -> dict:
    concepts = sorted({r["cause"] for r in rows} | {r["effect"] for r in rows})
    P = proxies()["concepts"]
    nodes = []
    for c in concepts:
        entry = P.get(c) or P.get(c.split(":")[0]) or {"measures": [{"kind": "none"}]}
        m = next((x for x in entry["measures"] if x.get("available") and x["kind"] != "none"), entry["measures"][0])
        nodes.append({"id": c, "label": concept_label(c), "measured_kind": m["kind"], "series": m.get("series", "none")})
    edges = [{"id": r["id"], "src": r["cause"], "dst": r["effect"], "region": r["region"], "origin": r["origin"],
              "grade": r["grade"], "status": r["status"], "headline": r["headline"], "n_support": r["n_support"],
              "n_contrary": r["n_contrary"], "contested": r["contested"], "supply_chain": r["supply_chain"],
              "within_type": r["within_type"], "support_by_tier": r["support_by_tier"],
              "first_appeared": r["first_appeared"], "claim": r["claim"],
              "tested": bool(r["test"] and r["test"].get("ran")), "passed": (r["test"] or {}).get("passed"),
              "result_label": (r["test"] or {}).get("result_label")}
             for r in rows]
    return {"nodes": nodes, "edges": edges, "pairs": pairs(rows)}


# ---------------------------------------------------------------- overview figure
def overview(rows: list[dict], path: Path) -> None:
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    fig = plt.figure(figsize=(16, 12.5), facecolor=SURF)
    fig.text(0.02, 0.975, "Winter Storm Uri: hypotheses from the documents, measured against Tier 0",
             fontsize=15, color=INK, weight="bold", va="top")
    fig.text(0.02, 0.952, "Documents (Tiers 1–3) propose causal claims; families group them; Tier 0 series test them "
             "through a pre-registered measurement table.\nOne edge per concept pair (ERCOT, Texas gas and multi-region "
             "families combined; the best-tested family sets style and colour). Same-type families are not drawn.",
             fontsize=10, color=INK2, va="top", linespacing=1.4)
    docs = documents()
    # ---- top band: documents by tier
    used = collections.defaultdict(set)
    for r in rows:
        for s in r["support"]:
            used[s["doc"]].add(r["id"])
    rel = {}
    for r in rows:
        for s in r["support"]:
            rel[s["doc"]] = s["reliability"]
    top = fig.add_axes([0.02, 0.775, 0.96, 0.145]); top.axis("off"); top.set_xlim(0, 1); top.set_ylim(0, 1)
    tiers = [("1", "Tier 1 · first-party (ERCOT)"), ("2", "Tier 2 · independent analysis"), ("3", "Tier 3 · news")]
    for i, (t, name) in enumerate(tiers):
        x0 = 0.005 + i * 0.335
        top.add_patch(FancyBboxPatch((x0, 0.02), 0.32, 0.93, boxstyle="round,pad=0.004,rounding_size=0.01",
                                     fc="#f3f2ee", ec=GRIDC, lw=1))
        top.text(x0 + 0.01, 0.88, name, color=INK, weight="bold", fontsize=10, va="top")
        ds = sorted([d for d in used if docs[d].get("tier") == t])
        for j, d in enumerate(ds):
            fl = ", ".join(docs[d].get("flags") or []) or "no flags"
            top.text(x0 + 0.012, 0.70 - j * 0.135, f"{d}", color=INK, fontsize=8.3, va="top", family="DejaVu Sans Mono")
            top.text(x0 + 0.20, 0.70 - j * 0.135, f"r {rel.get(d, 0):.2f} · {len(used[d])} fam.", color=INK2, fontsize=8, va="top")
            top.text(x0 + 0.012, 0.645 - j * 0.135, fl, color=MUTED, fontsize=7, va="top")
    fig.text(0.5, 0.768, "▼ claims (caused / contributed_to), grouped into families: cause → effect · fuel · region",
             ha="center", color=INK2, fontsize=9)
    # ---- middle band: concept graph
    mid = fig.add_axes([0.02, 0.27, 0.70, 0.48]); mid.axis("off"); mid.set_xlim(-0.04, 1.04); mid.set_ylim(0.05, 1.0)
    bg = fig.add_axes([0, 0, 1, 1], zorder=-1); bg.axis("off"); bg.set_xlim(0, 1); bg.set_ylim(0, 1)
    draw = [p for p in pairs(rows) if (p["headline"] or p["status"] != "untestable" or p["n_support"] >= 2)
            and p["src"] in POS and p["dst"] in POS and p["src"] != p["dst"]]
    for p in draw:
        a, b = POS[p["src"]], POS[p["dst"]]
        rad = 0.10
        col = MUTED if p["status"] == "accounting" else GRADE_COLOR[p["grade"]]
        lw = 1.2 + 0.55 * min(p["n_support"], 12) if p["n_support"] else 1.6
        mid.add_patch(FancyArrowPatch(a, b, connectionstyle=f"arc3,rad={rad}", arrowstyle="-|>,head_length=7,head_width=4",
                                      color=col, lw=lw, ls=STATUS_STYLE[p["status"]], shrinkA=34, shrinkB=34, zorder=2))
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2       # arc midpoint (arc3 control point, data coords)
        cx, cy = mx + rad * (b[1] - a[1]), my - rad * (b[0] - a[0])
        px, py = 0.5 * mx + 0.5 * cx, 0.5 * my + 0.5 * cy
        if p["origin"] != "documents":
            mid.plot([px], [py], marker="D", ms=8, mfc=SURF if p["origin"] == "system_knowledge" else INK,
                     mec=INK, mew=1.4, zorder=5)
        if p["contested"]:
            mid.plot([px + 0.025], [py], marker="X", ms=10, mfc=INK, mec=SURF, mew=1.2, zorder=5)
    kinds = {}
    for r in rows:                             # a concept's measure as used anywhere (a cause-only proxy still counts)
        for end in ("cause", "effect"):
            k = r["measured"][end]["kind"]
            if kinds.get(r[end]) in (None, "none") and k != "none":
                kinds[r[end]] = k + (" (as cause)" if end == "cause" and r[end] == "GasSupplyDisruption" else "")
            kinds.setdefault(r[end], k)
    for c, (x, y) in POS.items():
        if c not in kinds:
            continue
        k = kinds[c]
        ls = "-" if k == "direct" else ((0, (4, 2)) if k.startswith(("proxy", "text_derived")) else (0, (1, 2)))
        mid.add_patch(FancyBboxPatch((x - 0.08, y - 0.05), 0.16, 0.10, boxstyle="round,pad=0.006,rounding_size=0.02",
                                     fc="#ffffff", ec=INK if k == "direct" else MUTED, lw=1.4, ls=ls, zorder=3))
        mid.text(x, y + 0.014, textwrap.fill(concept_label(c), 20), ha="center", va="center", fontsize=8.4,
                 color=INK, zorder=4, linespacing=1.05)
        mid.text(x, y - 0.032, f"measured: {k}", ha="center", va="center", fontsize=7, color=INK2, zorder=4)
    # ---- right: legend and inference path
    leg = fig.add_axes([0.74, 0.27, 0.24, 0.48]); leg.axis("off"); leg.set_xlim(0, 1); leg.set_ylim(0, 1)
    leg.text(0, 0.99, "Edge colour = grade", weight="bold", color=INK, va="top")
    for i, g in enumerate(["G0", "G1", "G2", "G3"]):
        leg.add_line(Line2D([0.02, 0.14], [0.93 - i * 0.045] * 2, color=GRADE_COLOR[g], lw=4))
        desc = {"G0": "hedged only / no text", "G1": "asserted in text", "G2": "passed a test", "G3": "test + independent source"}[g]
        n = sum(r["grade"] == g for r in rows)
        leg.text(0.17, 0.93 - i * 0.045, f"{g}  {desc}  ({n})", va="center", color=INK, fontsize=8.5)
    leg.text(0, 0.72, "Line style = status", weight="bold", color=INK, va="top")
    for i, (s, name) in enumerate([("tested_direct", "tested, both ends measured"), ("tested_proxy", "tested via a proxy or chart read (cap G2)"),
                                   ("accounting", "accounting relation (no causal grade)"), ("untestable", "untestable (cap G1)")]):
        leg.add_line(Line2D([0.02, 0.14], [0.66 - i * 0.045] * 2, color=MUTED if s == "accounting" else INK2, lw=2, ls=STATUS_STYLE[s]))
        n = sum(r["status"] == s for r in rows)
        leg.text(0.17, 0.66 - i * 0.045, f"{name}  ({n})", va="center", color=INK, fontsize=8.5)
    leg.text(0, 0.47, "Width = supporting relations (all regions)\n◆ filled = documents + hand-built skeleton\n◇ open = skeleton only "
             "(no document asserts it)\n✖ = contested: a Tier 1–2 source denies it", color=INK2, fontsize=8.3, va="top")
    leg.text(0, 0.335, "How a claim becomes a grade", weight="bold", color=INK, va="top")
    steps = ["Claim in a Tier 1–3 document (LLM-extracted, quote-checked)", "Family: cause → effect · fuel · region",
             "Measured with: proxies.yaml (pre-registered)", "Design check: competing causes, confounders, pretrend",
             "Test vs weather-free / no-cause baseline, out of sample", "Cap by status → grade G0–G3"]
    for i, s in enumerate(steps):
        y = 0.275 - i * 0.052
        leg.add_patch(FancyBboxPatch((0.0, y - 0.022), 0.98, 0.044, boxstyle="round,pad=0.004,rounding_size=0.01",
                                     fc="#f3f2ee", ec=GRIDC, lw=0.8))
        leg.text(0.03, y, f"{i + 1}. {s}", va="center", fontsize=7.8, color=INK)
    # ---- bottom band: Tier 0 series
    bot = fig.add_axes([0.02, 0.03, 0.96, 0.20]); bot.axis("off"); bot.set_xlim(0, 1); bot.set_ylim(0, 1)
    fig.text(0.5, 0.245, "▲ Tier 0 measurements (datasets, never extracted) and one Tier 2 chart read feed the concepts they measure",
             ha="center", color=INK2, fontsize=9)
    sx = {s: 0.07 + i * (0.86 / (len(SERIES_LABEL) - 1)) for i, s in enumerate(SERIES_LABEL)}
    for s, x in sx.items():
        tier, lab = SERIES_LABEL[s]
        bot.add_patch(FancyBboxPatch((x - 0.064, 0.08), 0.128, 0.40, boxstyle="round,pad=0.004,rounding_size=0.015",
                                     fc={"0b": "#eef3fa", "0a": "#eef7f2"}.get(tier, "#f6f1e6"), ec=INK2, lw=1,
                                     ls="-" if tier.startswith("0") else (0, (4, 2))))
        bot.text(x, 0.40, f"Tier {tier}", ha="center", weight="bold", color=INK, fontsize=9)
        bot.text(x, 0.24, lab, ha="center", va="center", color=INK, fontsize=7.4)
    # links series -> concepts (figure coordinates)
    measured_by = collections.defaultdict(set)
    for r in rows:
        for end in ("cause", "effect"):
            m = r["measured"][end]
            if m["kind"] not in ("none",) and m.get("series"):
                measured_by[r[end]].add(m["series"].split("/")[0])
    measured_by["WeatherCondition"].add("unit_outage_2021_03_12")      # capacity weights for the temperature (C-M1)
    def fig_xy(ax, x, y):
        return fig.transFigure.inverted().transform(ax.transData.transform((x, y)))
    for c, ss in measured_by.items():
        if c not in POS:
            continue
        cx, cy = fig_xy(mid, POS[c][0], POS[c][1] - 0.045)
        for s in ss:
            if s not in sx:
                continue
            bx, by = fig_xy(bot, sx[s], 0.48)
            bg.add_line(Line2D([bx, cx], [by, cy], color="#cfcec8", lw=0.7, zorder=0))
    fig.savefig(path, dpi=120, facecolor=SURF)
    plt.close(fig)


# ---------------------------------------------------------------- lead figure: confirmed, contested, untestable
def _wrap(t, n=58):
    return "\n".join(textwrap.wrap(str(t), n))


def lead(rows: list[dict], path: Path) -> None:
    from kg import series as S
    R = {r["id"]: r for r in rows}
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    fig = plt.figure(figsize=(17, 7.2), facecolor=SURF)
    fig.text(0.015, 0.965, "Three hypothesis edges from the documents", fontsize=15, weight="bold", color=INK, va="top")
    fig.text(0.015, 0.915, "What the data confirms, where the sources disagree, and which measurement is missing.",
             fontsize=10.5, color=INK2, va="top")
    heads = [("Tested and confirmed", "low frequency → coal outages", R.get("GridEmergency->GenerationOutage:coal@ERCOT")),
             ("Documented and contested", "gas supply → gas-fired outages", R.get("GasSupplyDisruption->GenerationOutage:gas@ERCOT")),
             ("Documented but untestable", "loss of power at gas facilities → gas supply", None)]
    xs = [0.015, 0.345, 0.675]
    for (title, claim, r), x in zip(heads, xs):
        fig.add_artist(FancyBboxPatch((x, 0.04), 0.31, 0.82, boxstyle="round,pad=0.004,rounding_size=0.01",
                                      transform=fig.transFigure, fc="#f3f2ee", ec=GRIDC, lw=1, zorder=-5))
        fig.text(x + 0.012, 0.84, title, fontsize=12, weight="bold", color=INK, va="top")
        fig.text(x + 0.012, 0.795, claim, fontsize=10.5, color=INK2, va="top")
    # 1. confirmed: coal minute data and frequency
    coal = S.minute_start(S.mw_out_by_fuel())["coal"].loc["2021-02-15 01:25":"2021-02-15 02:30"]
    fr = S.frequency()
    ax = fig.add_axes([0.05, 0.42, 0.25, 0.30]); style(ax)
    ax.step(coal.index, coal / 1000, where="post", color=INK, lw=2.2)
    ax.axvline(fr.idxmin(), color=INK2, lw=1, ls=(0, (4, 3)))
    ax.set_ylabel("coal MW out (GW)", color=INK2, fontsize=8)
    ax.tick_params(labelbottom=False)
    af = fig.add_axes([0.05, 0.27, 0.25, 0.13]); style(af)
    ff = fr.loc["2021-02-15 01:25":]
    af.plot(ff.index, ff, color="#6250d6", lw=1.3); af.axvline(fr.idxmin(), color=INK2, lw=1, ls=(0, (4, 3)))
    af.set_ylabel("Hz", color=INK2, fontsize=8); af.set_xlim(coal.index[0], coal.index[-1])
    af.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M"))
    g1 = R.get("GridEmergency->GenerationOutage:coal@ERCOT") or {}
    fig.text(0.027, 0.20, _wrap(f"Grade {g1.get('grade')}. Coal MW out flat at 2,340 MW, then +933 MW four minutes after "
                                "the 01:55 frequency low (99.8th percentile of 15-minute changes). FERC names a 933 MW "
                                "coal unit that tripped on the frequency swing.", 60), fontsize=8.6, color=INK, va="top")
    # 2. contested
    r2 = heads[1][2] or {}
    sup = [x for x in r2.get("support", []) if x["modality"] == "asserted"]
    con = r2.get("contrary", [])
    y = 0.72
    fig.text(0.357, y, "Asserted", fontsize=9.5, weight="bold", color=INK, va="top"); y -= 0.04
    for x in sup[:2]:
        fig.text(0.357, y, _wrap(f"“{x['quote']}” — {x['doc']} (Tier {x['tier']})", 62), fontsize=8.3, color=INK, va="top")
        y -= 0.025 * (1 + len(textwrap.wrap(x["quote"], 62))) + 0.015
    fig.text(0.357, y, "Denied", fontsize=9.5, weight="bold", color=INK, va="top"); y -= 0.04
    for x in con[:2]:
        fl = ", ".join(x["flags"]) or "no flags"
        fig.text(0.357, y, _wrap(f"“{x['quote']}” — {x['doc']} (Tier {x['tier']}, {fl})", 62), fontsize=8.3, color=INK, va="top")
        y -= 0.025 * (1 + len(textwrap.wrap(x["quote"], 62))) + 0.015
    fig.text(0.357, 0.20, _wrap(f"Grade {r2.get('grade')}, contested. Test 2(b), Permian cold as the driver: not supported by "
                                "this model (no delay term; the Permian froze a day earlier). Test 2(a), FERC's daily "
                                "production added to the gas model: does not improve the out-of-sample fit.", 62),
             fontsize=8.6, color=INK, va="top")
    # 3. untestable: power loss -> gas supply, support by document
    pl = [R[k] for k in ("PowerLoss->GasSupplyDisruption@ERCOT", "PowerLoss->GasSupplyDisruption@multi-region") if k in R]
    by_doc = collections.Counter(x["doc"] for r in pl for x in r["support"])
    ab = fig.add_axes([0.80, 0.50, 0.16, 0.22]); style(ab)
    docs_sorted = sorted(by_doc, key=by_doc.get)
    ab.barh(range(len(docs_sorted)), [by_doc[d] for d in docs_sorted], color="#8fb4e3", height=0.6)
    ab.set_yticks(range(len(docs_sorted)), docs_sorted, fontsize=8)
    ab.set_xlabel("supporting relations", color=INK2, fontsize=8)
    for i, d in enumerate(docs_sorted):
        ab.text(by_doc[d] + 0.2, i, str(by_doc[d]), va="center", fontsize=8, color=INK)
    needs = pl[0]["needs"][0] if pl and pl[0]["needs"] else "metered load at gas facilities"
    fig.text(0.687, 0.40, _wrap(f"{sum(by_doc.values())} relations in {len(by_doc)} documents, the best-documented edge "
                                "in the graph. FERC: loss of power caused 18.1% of the production decline on Feb 14 (before "
                                "any firm load shed), 21.6% on Feb 17, 23.5% over the event, and the extent of power loss "
                                "at gas facilities is unknown.", 60), fontsize=8.6, color=INK, va="top")
    fig.text(0.687, 0.20, _wrap(f"Grade G1, untestable. Needs: {needs}.", 60), fontsize=8.6, color=INK, va="top",
             weight="bold")
    fig.savefig(path, dpi=120, facecolor=SURF)
    plt.close(fig)


# ---------------------------------------------------------------- report
def _q(s, n=90):
    return str(s or "").replace("|", "/").replace("\n", " ")[:n]


def report(rows: list[dict], out: Path) -> str:
    st = collections.Counter(r["status"] for r in rows)
    og = collections.Counter(r["origin"] for r in rows)
    gr = collections.Counter(r["grade"] for r in rows)
    fstats = json.loads((out / "families.json").read_text())["stats"]
    L = ["# Hypotheses discovered in the documents, tested against Tier 0", "",
         "![Lead](hypothesis_lead.png)", "", "![Overview](hypothesis_overview.png)", "",
         f"Run `04_hypotheses`, built on `03_graph` (no re-extraction). {fstats['relations_used']} in-scope "
         f"causal relations (`caused`, `contributed_to`) form **{len(rows)} families**; {sum(fstats['out_of_scope_relations'].values())} "
         f"relations are out of scope ({', '.join(f'{k} {v}' for k, v in fstats['out_of_scope_relations'].items())}). "
         "Run 1 has no `associated_with` relations (the v0 vocabulary lacked it), so there are no correlational families.", "",
         f"- **Origin:** {og['documents']} documents only, {og['both']} both, {og['system_knowledge']} system knowledge only.",
         f"- **Status:** {st['tested_direct']} tested (both ends measured), {st['tested_proxy']} tested via a proxy or "
         f"chart read, {st['accounting']} accounting relations (no causal grade), {st['untestable']} untestable.",
         f"- **Contested** (a Tier 1–2 source denies it): {sum(r['contested'] for r in rows)}. "
         f"**Supply-chain** families (gas → gas, split by stage): {sum(r['supply_chain'] for r in rows)}. "
         f"Historical (pre-2021) relations excluded: {fstats.get('historical_relations_excluded', 0)}.",
         "- **Grades:** " + ", ".join(f"{g} {gr[g]}" for g in ("G3", "G2", "G1", "G0")) + ".",
         f"- **Headline** (≥ 2 relations or skeleton): {sum(r['headline'] for r in rows)}.", "",
         "Measurement was fixed in `config/proxies.yaml` before any test ran (commit `07ab729`). Two amendments followed, "
         "each in its own commit:", "",
         "1. `fb89615`, after the first run: Permian temperature can stand for gas supply only as a cause.",
         "2. After the hypotheses review, before any new test ran: gas supply is measured by FERC Fig. 35 (read off the chart, "
         "`scripts/digitize_fig35.py`, checked against FERC's stated totals); tests 2(a) and cold → production were "
         "pre-registered; timing-only checks no longer count as passes (\"order consistent\", max G1); accounting "
         "identities get status `accounting`; failures read \"not supported by this test\"; denials are contrary evidence.",
         "", "Grouping rules added with amendment 2 (`config/hypotheses.yaml` → `reclassify`): loss of power at gas facilities "
         "is one concept (`PowerLoss`), wherever the extractor typed it; load shed → gas supply is read as power loss → gas "
         "supply; gas-facility shutdowns are gas supply disruption; warming weather is its own class; gas → gas families are "
         "split by stage. The cold-production test came out inconclusive by design (no recovery in its training days); "
         "that label was added after it ran and doesn't change its grade.", ""]
    L += ["## Tested families", "", "| Claim | Support by tier | Measured with (cause / effect) | Test | Result | Grade |",
          "|---|---|---|---|---|---|"]
    for r in rows:
        if not (r["test"] and r["test"].get("ran")):
            continue
        sup = " ".join(f"T{t}:{n}" for t, n in sorted(r["support_by_tier"].items())) or "—"
        ms = f"{r['measured']['cause']['kind']} / {r['measured']['effect']['kind']}"
        res = r["test"].get("result_label") or ("supported" if r["test"]["passed"] else "not supported")
        L.append(f"| {r['claim']} {'◆' if r['origin'] != 'documents' else ''}{' ✖' if r['contested'] else ''} | {sup} | {ms} | {r['test']['test']}: "
                 f"{_q(r['test']['summary'], 300)} | {res} | **{r['grade'] or r['status']}** |")
    L += ["", "◆ = in the hand-built gas–power skeleton. Tests: `cold_outage` (step 1 rule H2), `gas_supply_to_gas_outage` "
          "(2b; 2a blocked), `outage_to_shed` (accounting relationship plus timing, not an estimated effect), "
          "`frequency_to_coal`, `generic_timing` (timing evidence only, cap G2).", ""]
    L += ["## Contested families", ""]
    for r in rows:
        if r["contested"]:
            L.append(f"- **{r['claim']}** — asserted by " + ", ".join(sorted({x['doc'] for x in r['support'] if x['modality'] == 'asserted'}))
                     + "; denied by " + "; ".join(f"{x['doc']} (Tier {x['tier']}, {', '.join(x['flags']) or 'no flags'}): “{_q(x['quote'], 140)}”"
                                                for x in r["contrary"]))
    L += ["", "## Gas supply chain (gas → gas families split by stage)", ""]
    for r in rows:
        if r["supply_chain"]:
            L.append(f"- {r['cause_label']} → {r['effect_label']} ({r['region']}, {r['n_support']}): "
                     + "; ".join(f"“{_q(x['quote'], 110)}”" for x in r["support"][:2]))
    L += ["", "## Design-check threats (tested families)", "",
          "Threats don't change a grade (the pre-registered rules grade on the test), but they say how much a pass is worth. "
          "The clearest case: **grid emergencies → generation outages (all fuels)** passes, but total MW out had already "
          "risen 7.7 GW in the 3 hours before the 01:55 frequency low (gas outages were climbing), so most of that jump "
          "isn't attributable to the frequency excursion. The **coal-specific** family is the clean test: coal MW out was "
          "flat at 2,340 MW for at least 30 minutes before the low, then stepped up 933 MW four minutes after it.", ""]
    for r in rows:
        if r["test"] and r["test"].get("ran") and r["threats"]:
            L.append(f"- **{r['id']}**: " + "; ".join(_q(t["description"], 110) for t in r["threats"][:6])
                     + (f"; … {len(r['threats']) - 6} more" if len(r["threats"]) > 6 else ""))
    L += ["", "## Untestable families and what each needs", "", "| Claim | Support | Grade | Needs |", "|---|---|---|---|"]
    for r in rows:
        if r["status"] == "untestable":
            L.append(f"| {r['claim']} | {r['n_support']} | {r['grade']} | {_q('; '.join(r['needs']), 200)} |")
    need = collections.Counter(n for r in rows if r["status"] == "untestable" for n in r["needs"])
    L += ["", "Most common needs:", ""] + [f"- {c} × {n}" for n, c in need.most_common(8)]
    L += ["", "## Step 1: weather-free baselines re-grade the cold models", "",
          (out / "cold_baselines.md").read_text().split("## Out-of-sample RMSE (MW)")[1].split("Charts:")[0].strip()
          .replace("## Grades before and after", "### Grades before and after"), ""]
    L += ["## All families", "", "| Family | Origin | Support | First appeared | Status | Grade |", "|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| `{r['id']}` | {r['origin']} | {r['n_support']} | {r['first_appeared'] or '—'} | {r['status']} | {r['grade']} |")
    return "\n".join(L) + "\n"


def main(out: Path = OUT) -> dict:
    rows = merged(out)
    (out / "hypotheses.json").write_text(json.dumps(rows, indent=1, default=str))
    (out / "hypothesis_graph.json").write_text(json.dumps(graph(rows), indent=1, default=str))
    overview(rows, out / "hypothesis_overview.png")
    lead(rows, out / "hypothesis_lead.png")
    (out / "hypotheses_report.md").write_text(report(rows, out))
    return {"families": len(rows), "status": dict(collections.Counter(r["status"] for r in rows)),
            "grades": dict(collections.Counter(r["grade"] for r in rows)),
            "origin": dict(collections.Counter(r["origin"] for r in rows))}


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
