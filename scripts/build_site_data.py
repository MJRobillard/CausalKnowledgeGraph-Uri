"""Build the data behind the knowledge-graph explorer on the walkthrough page (site/index.html).

Reads the pipeline outputs and writes site/data/kg.js (a script, so the page also works opened from disk):
  evidence  documents by trust tier, the measured series, and the 29 claims checked against them
            (results/03_graph: graph.json, anchors.parquet, reliability.csv)
  causal    the hypothesis layer: concepts and document-derived causal edges with grade, status and test result
            (results/04_hypotheses/hypothesis_graph.json), plus class-layer edges that passed a test but have no
            document family (results/04_hypotheses: edges.parquet, cold_models.json)

    uv run python scripts/build_site_data.py       # or: make site
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "results"
OUT = ROOT / "site" / "data" / "kg.js"

DOCS = {  # id: (tier lane, short label)
    "news_ap_2021_02_15": ("3", "AP via KRGV"),
    "news_txtrib_ksat_2021_02_15": ("3", "Texas Tribune via KSAT"),
    "news_abc13_2021_02_15": ("3", "ABC13 Houston"),
    "news_aljazeera_2021_02_15": ("3", "Al Jazeera"),
    "news_ap_pbs_2021_02_16": ("3", "AP via PBS"),
    "ferc_final_2021_11": ("2", "FERC/NERC final report"),
    "enverus_txoga_2021_04": ("2", "Enverus for TXOGA"),
    "ut_austin_timeline_2021_07": ("2", "UT Austin timeline"),
    "ercot_board_2021_02_24": ("1", "ERCOT board deck"),
    "ercot_causes_2021_04_27": ("1", "ERCOT cause report"),
}
SERIES = {  # group id: (tier lane, label, series-id prefixes, what it measures); order = left to right on the page
    "load_shed": ("0b", "ERCOT load shed", ["series:ercot_gen_load_shed"], "minute data"),
    "frequency": ("0b", "ERCOT frequency", ["series:ercot_frequency"], "1-second data"),
    "eia": ("0b", "EIA hourly demand", ["series:eia_ercot_hourly"], "hourly, from ERCOT"),
    "mw_out": ("0b", "ERCOT MW out by fuel", ["series:ercot_mw_out_by_fuel"], "minute data, 8 fuels"),
    "unit_outages": ("0b", "ERCOT unit outage lists", ["series:unit_outage"], "3/4 and 3/12 releases"),
    "ghcnd": ("0a", "NOAA daily lows", ["series:noaa_ghcnd_daily"], "GHCN-Daily, 9 airports"),
    "noaa_hourly": ("0a", "NOAA hourly temperature", ["series:noaa_zone_temp_hourly"], "9 ERCOT weather zones"),
}
SHORT = {
    "WeatherCondition": "Cold weather", "WeatherCondition:warming": "Warming weather",
    "PowerLoss": "Power loss at gas facilities", "GasSupplyDisruption:processing": "Gas processing loss",
    "GasSupplyDisruption:pipeline": "Pipeline and midstream", "GasSupplyDisruption:production": "Gas production loss",
    "GasSupplyDisruption:delivery": "Gas delivery shortfall", "GasSupplyDisruption": "Gas supply disruption",
    "GenerationOutage:gas": "Gas-fired outages", "GenerationOutage": "All generation outages",
    "GenerationOutage:coal": "Coal outages", "GenerationOutage:wind": "Wind outages",
    "GridEmergency": "Grid emergency (EEA, low frequency)", "LoadShed": "Load shed",
    "Restoration": "Restoration", "FacilityShutdown": "Facility shutdowns",
    "InstitutionalAction": "Institutional actions", "MarketAction": "Market actions (prices, caps)",
}


def series_group(sid: str) -> str:
    return next(g for g, (_, _, prefixes, _) in SERIES.items() if any(sid.startswith(p) for p in prefixes))


def num(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x


def evidence() -> dict:
    g = json.loads((R / "03_graph" / "graph.json").read_text())
    nodes = pd.DataFrame(g["nodes"])
    rel = pd.read_csv(R / "03_graph" / "reliability.csv").set_index("doc_id")
    docs = []
    for d, (tier, label) in DOCS.items():
        r = rel.loc[d] if d in rel.index else None
        docs.append({"id": d, "tier": tier, "label": label, "items": 0 if r is None else int(r["items"]),   # claims and events
                     "held_out": d == "ut_austin_timeline_2021_07",
                     "flags": [] if r is None or pd.isna(r["flags"]) else str(r["flags"]).split(","),
                     "reliability": None if r is None else round(float(r["r"]), 2),
                     "prior": None if r is None else float(r["prior_mean"])})
    a = pd.read_parquet(R / "03_graph" / "anchors.parquet")
    a = a[a.status.isin(["anchored_ok", "anchored_mismatch"])]
    anchors = [{"doc": x.doc_id, "series": series_group(x.series), "series_id": x.series.removeprefix("series:"),
                "agree": x.status == "anchored_ok", "metric": x.metric, "value": num(x.value), "unit": num(x.unit),
                "observed": num(round(float(x.observed), 3)) if x.observed is not None else None,
                "observed_at": str(x.observed_at)[:16], "quote": x.quote} for x in a.itertuples()]
    series = [{"id": k, "tier": t, "label": lab, "detail": det} for k, (t, lab, _, det) in SERIES.items()]
    counts = {"nodes": len(g["nodes"]), "edges": len(g["edges"]),
              "by_kind": nodes.kind.value_counts().to_dict()}
    return {"docs": docs, "series": series, "anchors": anchors, "counts": counts}


def causal() -> dict:
    h = json.loads((R / "04_hypotheses" / "hypothesis_graph.json").read_text())
    fam = {e["id"]: e for e in h["edges"]}
    nodes = [{"id": n["id"], "label": SHORT.get(n["id"], n["label"]), "measured": n["measured_kind"],
              "series": n["series"]} for n in h["nodes"]]
    edges = []
    for p in h["pairs"]:
        best = fam.get(p["best_family"], {})
        edges.append({"src": p["src"], "dst": p["dst"], "grade": p["grade"], "status": p["status"],
                      "support": p["n_support"], "contrary": p["n_contrary"], "contested": p["contested"],
                      "headline": p["headline"], "by_tier": p["support_by_tier"], "result": p.get("result_label"),
                      "claim": best.get("claim"), "regions": p.get("regions"), "layer": "hypothesis"})
    # class-layer edges that passed a test but that no document family asserts (e.g. cold -> coal)
    present = {(e["src"], e["dst"]) for e in edges}
    ce = pd.read_parquet(R / "04_hypotheses" / "edges.parquet")
    ce = ce[(ce.kind == "class_caused") & (ce.grade.isin(["G2", "G3"]))]
    tests = {(t["src_class"], t["dst_class"]): t
             for t in json.loads((R / "04_hypotheses" / "cold_models.json").read_text())["edge_tests"]}
    for x in ce.itertuples():
        s, d = x.src.removeprefix("class:"), x.dst.removeprefix("class:")
        if (s, d) in present or s not in SHORT or d not in SHORT:
            continue
        t = tests.get((s, d))
        result = None
        if t:
            m = t["margins"]
            result = (f"{'passed' if t['passed'] else 'failed'}: {t['method']}; beats weather-free baselines by "
                      + " and ".join(f"{v:.1f}×" for v in m.values()) + " out of sample")
        edges.append({"src": s, "dst": d, "grade": x.grade, "status": "class_test", "support": 0, "contrary": 0,
                      "contested": False, "headline": True, "by_tier": {}, "result": result,
                      "claim": f"{SHORT[s]} caused {SHORT[d].lower()} (ERCOT), from the system time-series test.",
                      "regions": ["ERCOT"], "layer": "class"})
    return {"nodes": nodes, "edges": edges}


if __name__ == "__main__":
    data = {"evidence": evidence(), "causal": causal()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("// Generated by scripts/build_site_data.py from results/; do not edit.\nwindow.KG = "
                   + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n")
    e, c = data["evidence"], data["causal"]
    print(f"wrote {OUT.relative_to(ROOT)}: {len(e['docs'])} documents, {len(e['anchors'])} checked claims "
          f"({sum(a['agree'] for a in e['anchors'])} agree), {len(c['nodes'])} concepts, {len(c['edges'])} causal edges")
