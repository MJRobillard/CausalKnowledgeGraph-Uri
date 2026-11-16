"""Hypothesis families: group the documents' extracted causal claims, deterministically.

Family key = (cause class, effect class, fuel, region class). Classes come from kg.invariants.event_class (the fuel
is part of a GenerationOutage class). Each family keeps its support (every relation with its document, tier, flags,
modality, source reliability and quote id) and when the claim first appeared in the record. Families are marked
`origin`: documents, system_knowledge (the hand-built gas-power skeleton) or both.

Status, grades and caps for tested families are also here (`status_and_cap`, `grade`), so they can be unit tested.

    uv run python -m kg.hypotheses          # writes results/04_hypotheses/families.json
"""
from __future__ import annotations

import collections
import json
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from .common import CONFIG, RESULTS, documents
from .graph_build import load
from .invariants import event_class

OUT = RESULTS / "04_hypotheses"
RELIABILITY = RESULTS / "03_graph" / "reliability.csv"
TIER_PRIOR_MEAN = {"1": 0.9, "2": 0.7, "3": 0.3}


@lru_cache
def cfg() -> dict:
    return yaml.safe_load((CONFIG / "hypotheses.yaml").read_text())


# ---------------------------------------------------------------- region classes
def region_class(region) -> tuple[str | None, bool]:
    """(class, texas_as_ercot). None if the region is empty."""
    r = str(region or "").lower().strip()
    if not r or r in ("nan", "none"):
        return None, False
    for rule in cfg()["region_classes"]:
        words = rule.get("any") or []
        if "two_of" in rule:
            if sum(w in r for w in rule["two_of"]) >= 2:
                return rule["class"], False
            continue
        if any(w in r for w in words):
            return rule["class"], bool(rule.get("texas_as_ercot"))
    return "other", False


def relation_region(effect_region, cause_region, doc_id) -> tuple[str, bool, str]:
    """Region class of a relation: the effect's, else the cause's, else the document's default scope."""
    for src, reg in (("effect", effect_region), ("cause", cause_region),
                     ("document_default", (documents().get(doc_id, {}).get("scope") or {}).get("region"))):
        c, tx = region_class(reg)
        if c:
            return c, tx, src
    return "other", False, "none"


# ---------------------------------------------------------------- concepts
def split_class(cls: str) -> tuple[str, str | None]:
    base, _, fuel = cls.partition(":")
    return base, (fuel or None)


def concept_label(cls: str) -> str:
    return cfg()["concept_labels"].get(cls, cls)


def family_id(cause: str, effect: str, region: str) -> str:
    return f"{cause}->{effect}@{region}"


def claim_text(cause: str, effect: str, region: str, predicates: list[str]) -> str:
    verb = "caused" if "caused" in predicates else "contributed to"
    reg = cfg()["region_labels"].get(region, region)
    c = concept_label(cause)
    return f"{c[:1].upper() + c[1:]} {verb} {concept_label(effect)} ({reg})."


# ---------------------------------------------------------------- grouping
def reliability() -> dict:
    if RELIABILITY.exists():
        r = pd.read_csv(RELIABILITY)
        return dict(zip(r.doc_id, r.r))
    return {}


def _text(row) -> str:
    return f"{row.get('label') or ''} {row.get('quote') or ''}".lower()


def event_direction(row) -> str | None:
    """Direction of an event from its own words (amendment 2): warming/cooling for weather, rising/falling otherwise."""
    R = cfg()["reclassify"]
    t = _text(row)
    if row.get("subtype") == "WeatherCondition":
        return "warming" if re.search(R["warming"], t) else "cooling"
    for d in ("falling", "rising"):
        if re.search(R["direction"][d], t):
            return d
    return None


def gas_stage(row) -> str:
    t = _text(row)
    for stage, pat in cfg()["reclassify"]["stages"].items():
        if re.search(pat, t):
            return stage
    return "unspecified"


def reclassify(cause_cls: str, effect_cls: str, c, e, rel_quote: str) -> tuple[str, str, str | None]:
    """Amendment 2 rules (config/hypotheses.yaml -> reclassify). Returns (cause, effect, via)."""
    R = cfg()["reclassify"]
    tc, te = _text(c), _text(e)
    power, gas = R["power_loss"], R["gas_facility"]
    via = None
    if cause_cls == "WeatherCondition" and event_direction(c) == "warming":
        cause_cls = "WeatherCondition:warming"
    if effect_cls == "WeatherCondition" and event_direction(e) == "warming":
        effect_cls = "WeatherCondition:warming"
    for side in ("cause", "effect"):                      # rule 1: gas-facility shutdowns
        cls, t = (cause_cls, tc) if side == "cause" else (effect_cls, te)
        if cls == "FacilityShutdown" and re.search(gas, t):
            cls = "PowerLoss" if re.search(power, t) else "GasSupplyDisruption"
            if side == "cause":
                cause_cls = cls
            else:
                effect_cls = cls
    if effect_cls in ("GasSupplyDisruption", "PowerLoss") and cause_cls not in ("PowerLoss", "WeatherCondition"):
        if cause_cls == "LoadShed" and effect_cls == "GasSupplyDisruption":     # rule 3
            cause_cls, via = "PowerLoss", "LoadShed"
        elif re.search(power, tc) and effect_cls == "GasSupplyDisruption":      # rule 2
            via = cause_cls
            cause_cls = "PowerLoss"
    if cause_cls == "PowerLoss" and effect_cls == "PowerLoss":
        effect_cls = "GasSupplyDisruption"
    if cause_cls.startswith("GasSupplyDisruption") and effect_cls.startswith("GasSupplyDisruption"):
        cause_cls, effect_cls = f"GasSupplyDisruption:{gas_stage(c)}", f"GasSupplyDisruption:{gas_stage(e)}"
    return cause_cls, effect_cls, via


def build_families(nodes: pd.DataFrame, edges: pd.DataFrame) -> tuple[list[dict], dict]:
    C = cfg()
    docs = documents()
    rel_r = reliability()
    ev = nodes[nodes.kind == "Event"].copy()
    ev["cls"] = ev.apply(event_class, axis=1)
    ev = ev.set_index("id")
    preds = C["predicates"]["causal"] + C["predicates"]["correlational"]
    rel = edges[edges.kind.isin(preds) & edges.src.isin(ev.index) & edges.dst.isin(ev.index)]
    cutoff = pd.Timestamp(C.get("reclassify", {}).get("exclude_before", "1900-01-01"))
    fams = {}
    out_of_scope, historical, reclassified = collections.Counter(), 0, collections.Counter()
    for _, r in rel.iterrows():
        c, e = ev.loc[r.src], ev.loc[r.dst]
        if any(pd.notna(x.get("valid_start")) and x.get("valid_start") < cutoff for x in (c, e)):
            historical += 1
            continue
        reg, tx, reg_src = relation_region(e.region, c.region, r.doc_id)
        correlational = r.kind in C["predicates"]["correlational"]
        cause, effect, via = reclassify(c.cls, e.cls, c.to_dict(), e.to_dict(), r.quote)
        if (cause, effect) != (c.cls, e.cls):
            reclassified[f"{c.cls}->{e.cls} => {cause}->{effect}"] += 1
        fid = family_id(cause, effect, reg) + ("~assoc" if correlational else "")
        if reg not in C["in_scope"]:
            out_of_scope[reg] += 1
            continue
        d = docs.get(r.doc_id, {})
        tier = d.get("tier")
        f = fams.setdefault(fid, {"id": fid, "cause": cause, "effect": effect,
                                  "fuel": split_class(effect)[1] or split_class(cause)[1], "region": reg,
                                  "correlational": correlational,
                                  "within_type": split_class(cause)[0] == split_class(effect)[0],
                                  "supply_chain": split_class(cause)[0] == split_class(effect)[0] == "GasSupplyDisruption"
                                                  and cause != effect,
                                  "support": [], "contrary": []})
        row = {"quote_id": r.gid, "doc": r.doc_id, "tier": tier, "flags": d.get("flags", []),
               "modality": r.modality, "predicate": r.kind,
               "reliability": round(float(rel_r.get(r.doc_id, TIER_PRIOR_MEAN.get(tier, 0.5))), 3),
               "published_at": str(d.get("published_at") or ""), "quote": r.quote,
               "region_approx": tx, "region_source": reg_src, "cause_event": r.src, "effect_event": r.dst,
               "cause_type_raw": c.cls, "effect_type_raw": e.cls, "via": via,
               "cause_direction": event_direction(c.to_dict()), "effect_direction": event_direction(e.to_dict())}
        (f["contrary"] if r.modality == "denied" else f["support"]).append(row)
    # system-knowledge skeleton
    for s in C["skeleton"]:
        fid = family_id(s["cause"], s["effect"], s["region"])
        f = fams.get(fid)
        if f is None:
            fams[fid] = {"id": fid, "cause": s["cause"], "effect": s["effect"],
                         "fuel": split_class(s["effect"])[1] or split_class(s["cause"])[1], "region": s["region"],
                         "correlational": False, "within_type": False, "supply_chain": False, "support": [],
                         "contrary": [], "origin": "system_knowledge", "mechanism": s["mechanism"]}
        else:
            f["origin"] = "both"
            f["mechanism"] = s["mechanism"]
    for f in fams.values():
        f.setdefault("origin", "documents")
        sup = f["support"]
        f["support_by_tier"] = dict(collections.Counter(x["tier"] for x in sup))
        f["contrary_by_tier"] = dict(collections.Counter(x["tier"] for x in f["contrary"]))
        f["n_support"] = len(sup)
        f["n_contrary"] = len(f["contrary"])
        f["n_docs"] = len({x["doc"] for x in sup})
        f["n_asserted"] = sum(x["modality"] == "asserted" for x in sup)
        f["contested"] = f["n_asserted"] > 0 and any(x["tier"] in ("1", "2") for x in f["contrary"])
        dates = sorted(x["published_at"][:10] for x in sup + f["contrary"] if x["published_at"])
        f["first_appeared"] = dates[0] if dates else None
        f["first_appeared_doc"] = next((x["doc"] for x in sup + f["contrary"]
                                        if x["published_at"][:10] == (dates[0] if dates else None)), None)
        f["headline"] = (len(sup) >= cfg()["headline_min_support"] or f["origin"] != "documents") and \
            not (f["within_type"] and not f["supply_chain"])
        f["claim"] = claim_text(f["cause"], f["effect"], f["region"], [x["predicate"] for x in sup + f["contrary"]])
    stats = {"relations_used": int(sum(f["n_support"] + f["n_contrary"] for f in fams.values())),
             "out_of_scope_relations": dict(out_of_scope), "historical_relations_excluded": historical,
             "reclassified": dict(reclassified.most_common()),
             "families": len(fams), "by_origin": dict(collections.Counter(f["origin"] for f in fams.values())),
             "by_region": dict(collections.Counter(f["region"] for f in fams.values())),
             "headline": sum(f["headline"] for f in fams.values()),
             "contested": sum(f["contested"] for f in fams.values()),
             "correlational": sum(f["correlational"] for f in fams.values()),
             "within_type": sum(f["within_type"] for f in fams.values()),
             "supply_chain": sum(f["supply_chain"] for f in fams.values())}
    return sorted(fams.values(), key=lambda f: (-f["n_support"], f["id"])), stats


# ---------------------------------------------------------------- measurement (config/proxies.yaml)
@lru_cache
def proxies() -> dict:
    return yaml.safe_load((CONFIG / "proxies.yaml").read_text())


def measure_for(concept: str, fid: str, end: str) -> dict:
    """The measure used for one end of a family: a family override first, then the concept (or its base concept);
    the first measure with available: true, else a `none` measure carrying every unavailable option's needs."""
    key = fid.split("@")[0]
    ov = proxies().get("family_overrides", {})
    for k in (key, key.replace(":gas", "").replace(":coal", "").replace(":wind", "")):
        if k in ov and end in ov[k]:
            return {**ov[k][end]["measures"][0], "override": k}
    entry = proxies()["concepts"].get(concept) or proxies()["concepts"].get(split_class(concept)[0])
    if entry is None:
        return {"series": "none", "kind": "none", "reason": f"concept {concept} not in proxies.yaml", "needs": "a measurement"}
    for m in entry["measures"]:
        if m["kind"] != "none" and m.get("available") and not (end == "effect" and m.get("usable_as_effect") is False):
            alts = [x for x in entry["measures"] if x is not m and not x.get("available")]
            return {**m, "unavailable_alternatives": alts}
    first = entry["measures"][0]
    reasons = [x.get("effect_reason") for x in entry["measures"] if end == "effect" and x.get("usable_as_effect") is False]
    effect_needs = [x.get("effect_needs") for x in entry["measures"] if end == "effect" and x.get("usable_as_effect") is False]
    needs = effect_needs or [x.get("needs") for x in entry["measures"]]
    return {"series": "none", "kind": "none", "reason": "; ".join(r for r in [first.get("reason")] + reasons if r),
            "needs": "; ".join(dict.fromkeys(n for n in needs if n))}


FULL_STORM_RESOLUTIONS = {"hour", "minute"}


def family_status(f: dict, dedicated_test: bool) -> dict:
    """Status, cap and `needs` for a family, from proxies.yaml plus two rules fixed before any test ran:
    same-type families compare a series with itself (untestable), and a family with no dedicated test needs both
    series to cover the storm (the frequency series covers 42 minutes)."""
    mc, me = measure_for(f["cause"], f["id"], "cause"), measure_for(f["effect"], f["id"], "effect")
    needs = [m["needs"] for m in (mc, me) if m["kind"] == "none" and m.get("needs")]
    region_ok = all(f["region"] in m.get("regions", []) for m in (mc, me) if m["kind"] != "none")
    if not region_ok:
        needs.append(f"series for {cfg()['region_labels'].get(f['region'], f['region'])}")
    status, cap = status_and_cap(mc["kind"], me["kind"], region_ok, f.get("correlational", False))
    if status.startswith("tested") and f.get("within_type"):
        status, cap = "untestable", CAP["untestable"]
        needs.append("event-level data that separates cause and effect (both ends are the same series)")
    if status.startswith("tested") and not dedicated_test:
        short = [m for m in (mc, me) if m.get("coverage")]
        if short:
            status, cap = "untestable", CAP["untestable"]
            needs.append("; ".join(f"{m['series']} covering Feb 14-19 (has {m['coverage']})" for m in short))
    return {"cause_measure": mc, "effect_measure": me, "status": status, "cap": cap, "needs": needs}


# ---------------------------------------------------------------- status, caps and grades (step 4)
GRADE_ORDER = ["G0", "G1", "G2", "G3"]
CAP = {"tested_direct": "G3", "tested_proxy": "G2", "untestable": "G1"}


def status_and_cap(cause_kind: str, effect_kind: str, region_testable: bool, correlational: bool) -> tuple[str, str | None]:
    """Status from how each end is measured (config/proxies.yaml kinds) and whether the region has a series."""
    if correlational:
        return "correlational", None
    if "none" in (cause_kind, effect_kind) or not region_testable:
        return "untestable", CAP["untestable"]
    if {"proxy", "text_derived"} & {cause_kind, effect_kind}:
        return "tested_proxy", CAP["tested_proxy"]
    return "tested_direct", CAP["tested_direct"]


def earned_grade(n_asserted: int, n_support: int, test_passed: bool, independent: bool) -> str:
    """G0 hedged only (or no text), G1 asserted text, G2 passed test, G3 G2 + independent support (H4)."""
    if test_passed and independent:
        return "G3"
    if test_passed:
        return "G2"
    if n_asserted > 0:
        return "G1"
    return "G0"


def grade(status: str, cap: str | None, earned: str) -> str | None:
    if status == "correlational":
        return None
    return min(earned, cap, key=GRADE_ORDER.index) if cap else earned


# ---------------------------------------------------------------- main
def main(out: Path = OUT) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    nodes, edges = load(out)
    fams, stats = build_families(nodes, edges)
    (out / "families.json").write_text(json.dumps({"stats": stats, "families": fams}, indent=1, default=str))
    return stats


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
