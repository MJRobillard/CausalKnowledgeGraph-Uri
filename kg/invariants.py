"""Step 3: structural invariants and causal-edge grades. Pure functions over the nodes/edges tables.

1. Cause before effect (caused / contributed_to between events with resolved times).
2. Nothing causes the weather (no causal edge into a WeatherCondition event or a Tier 0a series).
3. Tier 0 is never overridden (no comparability link marks a Tier 0 value; revised_by into a series only
   from the same dataset family).
4. No causal cycles among event instances.
5. The gas-power loop: a cycle at the entity level, acyclic once unrolled hourly.
6. Grades: G0 time order only, G1 asserted in text, G2 statistical test with a passed placebo (step 4),
   G3 = G2 plus a second independent source or test.

Violations are flagged on the edge (`invariant_flags`), never deleted.

    uv run python -m kg.invariants
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import networkx as nx
import pandas as pd

from .graph_build import RUN, load, save

CAUSAL = ("caused", "contributed_to")
MIN, HOUR, DAY = pd.Timedelta(minutes=1), pd.Timedelta(hours=1), pd.Timedelta(days=1)
FUEL_WORDS = {"gas": r"natural gas|gas-fired|gas fired|\bgas\b|\bng\b", "coal": r"\bcoal\b",
              "wind": r"\bwind\b", "solar": r"\bsolar\b", "nuclear": r"\bnuclear\b"}
FUELED_TYPES = ("GenerationOutage",)


# ---------------------------------------------------------------- 1. cause before effect
def start_interval(row) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """Earliest and latest possible start of an event, from its precision."""
    t, p = row.get("valid_start"), row.get("precision")
    if t is None or pd.isna(t) or p in (None, "unknown"):
        return None
    if p == "minute":
        return t, t + MIN
    if p == "hour":
        return t, t + HOUR
    if p == "day":
        return t.normalize(), t.normalize() + DAY
    if p == "range":   # a range's start: a clock time if one is given, else the whole day
        return (t, t + MIN) if (t.hour or t.minute) else (t.normalize(), t.normalize() + DAY)
    return None


def cause_before_effect(nodes: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    ev = nodes[nodes.kind == "Event"].set_index("id")
    rows = []
    for i, e in edges[edges.kind.isin(CAUSAL)].iterrows():
        if e.src not in ev.index or e.dst not in ev.index:
            rows.append({"edge": i, "status": "not_event_to_event"})
            continue
        a, b = start_interval(ev.loc[e.src]), start_interval(ev.loc[e.dst])
        if a is None or b is None:
            st = "time_unverifiable"
        elif a[1] <= b[0]:
            st = "ok"                       # cause certainly starts first
        elif a[0] >= b[1]:
            st = "violation"                # cause certainly starts after the effect has started
        else:
            st = "time_unverifiable"        # overlapping or same-time at this precision
        rows.append({"edge": i, "src": e.src, "dst": e.dst, "kind": e.kind, "doc_id": e.doc_id, "status": st,
                     "cause_start": None if a is None else a[0], "effect_start": None if b is None else b[0],
                     "cause_type": ev.subtype.get(e.src), "effect_type": ev.subtype.get(e.dst), "quote": e.quote})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 2. nothing causes the weather
def into_weather(nodes: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    n = nodes.set_index("id")
    c = edges[edges.kind.isin(CAUSAL + ("class_caused",))]
    bad = []
    for i, e in c.iterrows():
        if e.dst not in n.index:
            continue
        d = n.loc[e.dst]
        weather = (d.kind in ("Event", "EventClass") and str(d.subtype).startswith("WeatherCondition")) or \
                  (d.kind == "Series" and d.tier == "0a")
        if weather:
            bad.append({"edge": i, "src": e.src, "dst": e.dst, "kind": e.kind, "doc_id": e.doc_id,
                        "src_type": n.subtype.get(e.src), "quote": e.quote,
                        "src_summary": n.label.get(e.src), "dst_summary": d.label})
    return pd.DataFrame(bad)


# ---------------------------------------------------------------- 3. Tier 0 never overridden
COMPARABILITY = ("different_scope", "comparable", "conflict", "superseded")


def dataset_family(doc_id: str) -> str:
    return re.sub(r"_\d{4}_\d\d(_\d\d)?$", "", str(doc_id))


def tier0_overrides(nodes: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    n = nodes.set_index("id")
    tier0 = set(n.index[n.tier.isin(["0a", "0b"])])
    bad = []
    for i, e in edges[edges.kind.isin(COMPARABILITY)].iterrows():
        if e.src in tier0 or e.dst in tier0:
            bad.append({"edge": i, "kind": e.kind, "src": e.src, "dst": e.dst, "rule": "comparability link on Tier 0"})
    for i, e in edges[edges.kind == "revised_by"].iterrows():
        if e.dst in tier0 and n.kind.get(e.dst) == "Series":
            fs, fd = dataset_family(n.doc_id.get(e.src)), dataset_family(n.doc_id.get(e.dst))
            if n.kind.get(e.src) != "Series" or fs != fd:
                bad.append({"edge": i, "kind": e.kind, "src": e.src, "dst": e.dst,
                            "rule": f"revised_by into a series from another family ({fs} -> {fd})"})
    if "proposal" in edges.kind.values:   # none yet; the rule is here for when an optimizer proposes changes
        for i, e in edges[edges.kind == "proposal"].iterrows():
            if e.dst in tier0:
                bad.append({"edge": i, "kind": e.kind, "src": e.src, "dst": e.dst, "rule": "proposal targets Tier 0"})
    return pd.DataFrame(bad)


# ---------------------------------------------------------------- 4. no causal cycles
def canonical_cycles(g: nx.DiGraph) -> list[list[str]]:
    """Cycles rotated to start at their smallest node and sorted, so reruns give identical output."""
    out = []
    for c in nx.simple_cycles(g):
        i = c.index(min(c))
        out.append(c[i:] + c[:i])
    return sorted(out)


def causal_cycles(edges: pd.DataFrame, kinds=CAUSAL) -> list[list[str]]:
    g = nx.DiGraph()
    g.add_edges_from(edges[edges.kind.isin(kinds)][["src", "dst"]].itertuples(index=False, name=None))
    return canonical_cycles(g)


# ---------------------------------------------------------------- 5. the gas-power loop
LOOP_NODES = {"Cold": "WeatherCondition", "GasProduction": "GasSupplyDisruption",
              "GasGeneration": "GenerationOutage:gas", "LoadShed": "LoadShed"}
# Related class edges shown as weaker support when the exact class edge has no text instance
LOOP_RELATED = {("GasGeneration", "LoadShed"): [("GenerationOutage", "LoadShed"), ("GenerationOutage", "GridEmergency"),
                                               ("GenerationOutage:gas", "GridEmergency")],
                ("GasProduction", "GasGeneration"): [("GasSupplyDisruption", "GenerationOutage")]}
POWER_LOSS_LABEL = re.compile(r"loss of power|power outage|power supply|power_supply_loss|loss of electric", re.I)
LOOP_EDGES = [("Cold", "GasProduction", "freeze-offs and wellhead freezing", 1),
              ("GasProduction", "GasGeneration", "fuel supply to gas-fired units", 1),
              ("GasGeneration", "LoadShed", "generation loss forces shedding", 0),
              ("LoadShed", "GasProduction", "power_supply_loss: shed circuits feed gas facilities", 1)]


def loop_entity_graph() -> nx.DiGraph:
    g = nx.DiGraph()
    for a, b, why, lag in LOOP_EDGES:
        g.add_edge(a, b, mechanism=why, lag_h=lag)
    return g


def loop_unrolled(start="2021-02-14 00:00", end="2021-02-18 23:00") -> nx.DiGraph:
    """Each variable at each hour is a node; an edge with lag L goes from X(t) to Y(t+L)."""
    hours = pd.date_range(start, end, freq="h")
    g = nx.DiGraph()
    for t in hours:
        for v in LOOP_NODES:
            g.add_node(f"{v}@{t:%m-%dT%H}", var=v, t=t.isoformat())
    for i, t in enumerate(hours):
        for a, b, why, lag in LOOP_EDGES:
            if i + lag < len(hours):
                g.add_edge(f"{a}@{t:%m-%dT%H}", f"{b}@{hours[i + lag]:%m-%dT%H}", mechanism=why, lag_h=lag)
    return g


def graph_json(g: nx.DiGraph) -> dict:
    return {"nodes": [{"id": n, **d} for n, d in g.nodes(data=True)],
            "edges": [{"src": a, "dst": b, **d} for a, b, d in g.edges(data=True)]}


# ---------------------------------------------------------------- 6. classes and grades
def event_fuel(row) -> str | None:
    text = f"{row.get('label') or ''} {row.get('quote') or ''}".lower()
    hits = [f for f, pat in FUEL_WORDS.items() if re.search(pat, text)]
    return hits[0] if len(hits) == 1 else None


def event_class(row) -> str:
    if row["subtype"] in FUELED_TYPES:
        f = event_fuel(row)
        if f:
            return f"{row['subtype']}:{f}"
    return row["subtype"]


def instance_grades(edges: pd.DataFrame) -> pd.Series:
    """G1 if the text asserts it (not hedged, not denied); G0 otherwise."""
    c = edges.kind.isin(CAUSAL)
    g = pd.Series(None, index=edges.index, dtype=object)
    g[c] = edges.loc[c, "modality"].map(lambda m: "G1" if m == "asserted" else "G0")
    return g


def class_layer(nodes: pd.DataFrame, edges: pd.DataFrame, tests: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """EventClass nodes, instance_of edges, and class_caused edges graded G0-G3.
    `tests` maps (src_class, dst_class) -> list of passed statistical tests (from step 4)."""
    ev = nodes[nodes.kind == "Event"].copy()
    ev["cls"] = ev.apply(event_class, axis=1)
    cls_of = ev.set_index("id").cls
    c = edges[edges.kind.isin(CAUSAL) & edges.src.isin(cls_of.index) & edges.dst.isin(cls_of.index)].copy()
    c["sc"], c["dc"] = c.src.map(cls_of), c.dst.map(cls_of)
    agg = {}
    for (sc, dc), g in c.groupby(["sc", "dc"]):
        asserted = g[g.modality == "asserted"]
        agg[(sc, dc)] = {"n_instances": len(g), "n_asserted": len(asserted),
                         "n_hedged": int((g.modality == "hedged").sum()), "n_denied": int((g.modality == "denied").sum()),
                         "asserted_docs": sorted(asserted.doc_id.dropna().unique()),
                         "docs": sorted(g.doc_id.dropna().unique())}
    for key in tests:
        agg.setdefault(key, {"n_instances": 0, "n_asserted": 0, "n_hedged": 0, "n_denied": 0,
                             "asserted_docs": [], "docs": []})
    new_n = [{"id": f"class:{k}", "kind": "EventClass", "subtype": k, "label": k,
              "attrs": json.dumps({"instances": int((ev.cls == k).sum())})}
             for k in sorted(set(ev.cls) | {x for kk in tests for x in kk})]
    new_e = [{"src": i, "dst": f"class:{k}", "kind": "instance_of"} for i, k in cls_of.items()]
    for (sc, dc), a in agg.items():
        passed = [t for t in tests.get((sc, dc), []) if t.get("passed")]
        indep = independent_support(a["asserted_docs"], passed)
        a["independent_docs"] = indep
        if passed and (indep or second_test(passed)):
            grade = "G3"
        elif passed:
            grade = "G2"
        elif a["n_asserted"] > 0:
            grade = "G1"
        else:
            grade = "G0"
        flags = "hedged_only" if a["n_asserted"] == 0 and a["n_hedged"] > 0 else None
        new_e.append({"src": f"class:{sc}", "dst": f"class:{dc}", "kind": "class_caused", "grade": grade,
                      "invariant_flags": flags,
                      "attrs": json.dumps({**a, "tests": tests.get((sc, dc), [])}, default=str)})
    return pd.DataFrame(new_n), pd.DataFrame(new_e)


def independent_support(asserted_docs: list[str], passed: list[dict]) -> list[str]:
    """H4: asserted Tier 1-2 documents that weren't used to build any passed test's inputs.
    A document is an input if it publishes a dataset the test uses (`source_document` in the registry)."""
    if not passed:
        return []
    from .common import documents
    docs = documents()
    used = {d for t in passed for d in t.get("input_documents", [])}
    return [d for d in asserted_docs if docs.get(d, {}).get("tier") in ("1", "2") and d not in used]


def second_test(passed: list[dict]) -> bool:
    """H4: two passed tests count only if they use different data sources."""
    srcs = {tuple(sorted(t.get("data_sources", []))) for t in passed}
    return len(passed) > 1 and len(srcs) > 1


# ---------------------------------------------------------------- loop support from extracted edges
def loop_support(class_edges: pd.DataFrame, claims: pd.DataFrame) -> list[dict]:
    out = []
    ce = class_edges[class_edges.kind == "class_caused"]
    for a, b, why, lag in LOOP_EDGES:
        sc, dc = f"class:{LOOP_NODES[a]}", f"class:{LOOP_NODES[b]}"
        hit = ce[(ce.src == sc) & (ce.dst == dc)]
        info = json.loads(hit.iloc[0]["attrs"]) if len(hit) else {}
        row = {"edge": f"{a} -> {b}", "mechanism": why, "lag_h": lag, "class_edge": f"{sc} -> {dc}",
               "text_instances": info.get("n_instances", 0), "asserted": info.get("n_asserted", 0),
               "docs": info.get("docs", []), "grade": hit.iloc[0]["grade"] if len(hit) else "none"}
        rel = []
        for rs, rd in LOOP_RELATED.get((a, b), []):
            h = ce[(ce.src == f"class:{rs}") & (ce.dst == f"class:{rd}")]
            if len(h):
                rel.append(f"{rs} → {rd} ({json.loads(h.iloc[0]['attrs'])['n_instances']}, {h.iloc[0]['grade']})")
        row["related"] = rel
        if a == "LoadShed":     # the evidence for requires_power_from is in quant claims, not relations
            at = claims["attrs"].map(json.loads)
            # v0 extractions predate the power_supply_loss category, so FERC's labels are matched too
            ps = claims[at.map(lambda d: d.get("cause_category") == "power_supply_loss"
                               or bool(POWER_LOSS_LABEL.search(str(d.get("cause_label_raw") or ""))))]
            row["power_supply_loss_claims"] = len(ps)
            row["power_supply_loss_docs"] = sorted(ps.doc_id.unique())
        out.append(row)
    return out


# ---------------------------------------------------------------- run
def load_tests(run: Path = RUN) -> dict:
    p = run / "cold_models.json"
    if not p.exists():
        return {}
    out = {}
    for t in json.loads(p.read_text()).get("edge_tests", []):
        out.setdefault((t["src_class"], t["dst_class"]), []).append(t)
    return out


def main(run: Path = RUN) -> dict:
    nodes, edges = load(run)
    nodes = nodes[nodes.kind != "EventClass"].copy()
    edges = edges[~edges.kind.isin(["instance_of", "class_caused"])].copy()
    edges["invariant_flags"] = None
    edges["grade"] = instance_grades(edges)
    hedged = edges.kind.isin(CAUSAL) & (edges.modality == "hedged")
    edges.loc[hedged, "invariant_flags"] = "hedged"

    cbe = cause_before_effect(nodes, edges)
    for _, r in cbe[cbe.status.isin(["violation", "time_unverifiable"])].iterrows():
        prev = edges.at[r.edge, "invariant_flags"]
        edges.at[r.edge, "invariant_flags"] = ",".join(x for x in (prev, r.status) if x)

    tests = load_tests(run)
    cn, ce = class_layer(nodes, edges, tests)
    nodes = pd.concat([nodes, cn], ignore_index=True)
    edges = pd.concat([edges, ce], ignore_index=True)

    weather = into_weather(nodes, edges)
    for _, r in weather.iterrows():
        prev = edges.at[r.edge, "invariant_flags"]
        edges.at[r.edge, "invariant_flags"] = ",".join(x for x in (prev, "into_weather") if x)
    t0 = tier0_overrides(nodes, edges)
    cycles = causal_cycles(edges)
    class_cycles = causal_cycles(edges, kinds=("class_caused",))
    ent, unrolled = loop_entity_graph(), loop_unrolled()
    loop = {"entity_has_cycle": not nx.is_directed_acyclic_graph(ent),
            "entity_cycles": canonical_cycles(ent),
            "unrolled_nodes": unrolled.number_of_nodes(), "unrolled_edges": unrolled.number_of_edges(),
            "unrolled_is_dag": nx.is_directed_acyclic_graph(unrolled)}
    (run / "loop_entity.json").write_text(json.dumps(graph_json(ent), indent=1))
    (run / "loop_hourly.json").write_text(json.dumps(graph_json(unrolled), separators=(",", ":"), default=str))
    support = loop_support(edges, nodes[nodes.kind == "QuantClaim"])
    save(nodes, edges, run)

    summary = {"causal_edges": int(edges.kind.isin(CAUSAL).sum()),
               "cause_before_effect": cbe.status.value_counts().to_dict(),
               "into_weather": len(weather), "tier0_overrides": len(t0), "instance_cycles": len(cycles),
               "class_cycles": len(class_cycles), "loop": loop,
               "instance_grades": edges.loc[edges.kind.isin(CAUSAL), "grade"].value_counts().to_dict(),
               "hedged_instances": int(hedged.sum()),
               "class_grades": edges.loc[edges.kind == "class_caused", "grade"].value_counts().to_dict()}
    (run / "invariants_report.md").write_text(report(summary, cbe, weather, t0, cycles, class_cycles, support, edges))
    (run / "invariants_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary


def _q(s, n=110):
    return str(s or "").replace("|", "/").replace("\n", " ")[:n]


def report(s, cbe, weather, t0, cycles, class_cycles, support, edges) -> str:
    L = ["# Invariants report", "", f"Run `03_graph`. {s['causal_edges']} extracted causal edges "
         "(`caused`, `contributed_to`). Violations are flagged on the edge (`invariant_flags`), never deleted.", "",
         "| # | Rule | Result |", "|---|---|---|"]
    cb = s["cause_before_effect"]
    L.append(f"| 1 | Cause before effect | {cb.get('ok', 0)} ok, **{cb.get('violation', 0)} violations**, "
             f"{cb.get('time_unverifiable', 0)} time_unverifiable |")
    L.append(f"| 2 | Nothing causes the weather | **{s['into_weather']} violations** |")
    L.append(f"| 3 | Tier 0 never overridden | {s['tier0_overrides']} violations |")
    L.append(f"| 4 | No causal cycles (event instances) | {s['instance_cycles']} cycles |")
    lp = s["loop"]
    L.append(f"| 5 | Gas–power loop | entity level has a cycle: **{lp['entity_has_cycle']}**; hourly unrolled "
             f"({lp['unrolled_nodes']} nodes, {lp['unrolled_edges']} edges) is acyclic: **{lp['unrolled_is_dag']}** |")
    L.append(f"| 6 | Grades | instances {s['instance_grades']}; classes {s['class_grades']} |")
    L += ["", "## 1. Cause before effect", "",
          "An event's start is an interval set by its precision (minute, hour, day; a range's start is a day unless a "
          "clock time is given). `ok` = the cause's interval ends before the effect's begins; `violation` = the cause "
          "starts after the effect has started; anything overlapping or unknown is `time_unverifiable`.", "",
          "By cause → effect type (all statuses):", "", "| Cause → effect | ok | violation | unverifiable |", "|---|---|---|---|"]
    if len(cbe):
        pt = cbe.pivot_table(index=["cause_type", "effect_type"], columns="status", values="edge", aggfunc="size",
                             fill_value=0)
        for (a, b), r in pt.sort_values(by=list(pt.columns), ascending=False).iterrows():
            L.append(f"| {a} → {b} | {r.get('ok', 0)} | {r.get('violation', 0)} | {r.get('time_unverifiable', 0)} |")
    v = cbe[cbe.status == "violation"]
    L += ["", f"Every violation ({len(v)}):", "", "| Doc | Edge | Cause start | Effect start | Quote |", "|---|---|---|---|---|"]
    for _, r in v.iterrows():
        L.append(f"| {r.doc_id} | {r.cause_type} → {r.effect_type} ({r.kind}) | {r.cause_start} | {r.effect_start} | {_q(r.quote)} |")
    L += ["", "## 2. Nothing causes the weather", "", f"Every violation ({len(weather)}):", "",
          "| Doc | Edge | Cause | Weather event | Quote |", "|---|---|---|---|---|"]
    for _, r in weather.iterrows():
        L.append(f"| {r.doc_id} | {r.kind} | {r.src_type}: {_q(r.src_summary, 60)} | {_q(r.dst_summary, 60)} | {_q(r.quote)} |")
    L += ["", "## 3. Tier 0 is never overridden", "",
          "Checked: no comparability link (`different_scope`, `comparable`, `conflict`, `superseded`) touches a Tier 0 "
          "node, and `revised_by` into a series comes only from an earlier version of the same dataset "
          "(the unit list 3/4 → 3/12). No proposals exist yet; the rule is in place for them.", ""]
    for _, r in t0.iterrows():
        L.append(f"- {r.rule}: {r.src} → {r.dst}")
    L += ["", "## 4. No causal cycles", "", f"Event instances: {len(cycles)} cycles. "
          f"At the class level (event types, the layer that aggregates instances) there are {len(class_cycles)} cycles, "
          "which is expected: classes are not time-indexed, which is why the loop is unrolled in 5.", ""]
    for c in cycles[:20]:
        L.append(f"- {' → '.join(c)}")
    L += ["", "## 5. The gas–power loop", "",
          "Skeleton from system knowledge, with the extracted text edges that support each link "
          "(`loop_entity.json`, `loop_hourly.json` for the frontend).", "",
          "| Edge | Lag (h) | Mechanism | Text instances | Asserted | Grade | Sources | Related class edges |", "|---|---|---|---|---|---|---|---|"]
    for r in support:
        extra = ""
        if "power_supply_loss_claims" in r:
            extra = (f"; plus {r['power_supply_loss_claims']} quant claims whose cause is loss of power "
                     f"({', '.join(r['power_supply_loss_docs'])}; matched by label, since v0 had no `power_supply_loss`)")
        L.append(f"| {r['edge']} | {r['lag_h']} | {r['mechanism']} | {r['text_instances']} | {r['asserted']} | "
                 f"{r['grade']} | {', '.join(r['docs'])}{extra} | {'; '.join(r['related'])} |")
    L += ["", f"Entity level: cycles {lp['entity_cycles']}. Unrolled hourly over Feb 14–18 with the lags above "
          f"(Cold(t)→GasProduction(t+1), GasProduction(t)→GasGeneration(t+1), GasGeneration(t)→LoadShed(t), "
          f"LoadShed(t)→GasProduction(t+1)): every edge goes forward in time or stays within an hour without "
          f"closing a loop, so the graph is a DAG ({lp['unrolled_is_dag']}). The feedback is real, but each turn of "
          "it happens at a later hour.", ""]
    cls = edges[edges.kind == "class_caused"].copy()
    cls["n"] = cls["attrs"].map(lambda a: json.loads(a)["n_instances"])
    L += ["## 6. Grades", "",
          "G0 time order only; G1 asserted (not hedged) in text by at least one source; G2 supported by a statistical "
          "test in step 4 with a passed placebo; G3 G2 plus a second independent source or test. Instance edges get "
          "G0/G1 from their own modality; class edges aggregate instances (GenerationOutage is split by fuel when the "
          "event names one fuel).", "",
          f"Instance edges: {s['instance_grades']}. {s['hedged_instances']} are hedged (flag `hedged`).", "",
          f"Class edges: {s['class_grades']}.", "", "| Class edge | Grade | Instances | Flags |", "|---|---|---|---|"]
    order = {"G3": 0, "G2": 1, "G1": 2, "G0": 3}
    for _, r in cls.assign(o=cls.grade.map(order)).sort_values(["o", "n"], ascending=[True, False]).iterrows():
        fl = "" if r.invariant_flags is None or pd.isna(r.invariant_flags) else r.invariant_flags
        L.append(f"| {r.src.removeprefix('class:')} → {r.dst.removeprefix('class:')} | {r.grade} | {r.n} | {fl} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    print(json.dumps(main(), indent=1, default=str))
