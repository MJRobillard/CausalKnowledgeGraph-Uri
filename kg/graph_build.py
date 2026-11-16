"""Step 1: build the tiered, bitemporal graph from post-processed extractions plus the Tier 0 registry.

Trust (tier, flags, claim_status) is copied from the item's Document, never from the model.
Valid time = when it happened (event time, claim window). Recorded time = when the source said it
(document published_at, or the dataset version date).

    uv run python -m kg.graph_build            # reads results/03_graph/post_graph.json

Writes nodes.parquet, edges.parquet, unit_revisions.parquet and a compact graph.json to the run folder.
Later steps (anchor, invariants, cold models) add rows and columns to the same tables via `save()`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import pandas as pd

from .common import RESULTS, documents
from .unit_revisions import revisions, summary

RUN = RESULTS / "03_graph"
CAUSAL = {"caused", "contributed_to"}
EXTRACTED_PREDICATES = {"caused", "contributed_to", "associated_with", "preceded", "affected", "located_in",
                        "part_of", "operated_by", "supplies_fuel_to", "requires_power_from", "ordered"}
# Document version chains. Kind is a hand judgment (Choice G-R1 in ASSUMPTIONS.md).
DOC_REVISION_KIND = {("ercot_causes_2021_04_06", "ercot_causes_2021_04_27"): "correction",
                     ("ferc_prelim_2021_09", "ferc_final_2021_11"): "expansion"}
NODE_COLS = ["id", "kind", "subtype", "label", "doc_id", "tier", "flags", "claim_status", "valid_start",
             "valid_end", "precision", "recorded_at", "region", "modality", "quote", "attrs"]
EDGE_COLS = ["src", "dst", "kind", "modality", "quote", "doc_id", "tier", "flags", "claim_status", "gid", "attrs"]


# ---------------------------------------------------------------- time
def to_central(s) -> pd.Timestamp | None:
    """ISO date or datetime (any offset) -> naive Central (CST, UTC-6) timestamp. None if unparseable."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()
    if not s or s[0] not in "0123456789":
        return None
    try:
        t = pd.Timestamp(s)
    except (ValueError, TypeError):
        return None
    if t.tzinfo is not None:
        t = t.tz_convert(dt.timezone(dt.timedelta(hours=-6))).tz_localize(None)
    return t


def recorded_at(doc: dict) -> str | None:
    p = doc.get("published_at")
    return str(p) if p is not None else None


# ---------------------------------------------------------------- nodes
def _trust(doc: dict) -> dict:
    return {"doc_id": doc["id"], "tier": doc.get("tier"), "flags": ",".join(doc.get("flags") or []),
            "claim_status": doc.get("claim_status"), "recorded_at": recorded_at(doc)}


def document_nodes(docs: dict) -> list[dict]:
    out = []
    for d in docs.values():
        attrs = {k: d.get(k) for k in ("publisher", "publisher_role", "doc_type", "scope", "held_out", "skip",
                                       "version", "resolution", "clock", "superseded_by", "revises", "funded_by")
                 if d.get(k) is not None}
        out.append({"id": f"doc:{d['id']}", "kind": "Document", "subtype": d.get("doc_type"),
                    "label": d.get("title") or d.get("file"), **_trust(d),
                    "region": (d.get("scope") or {}).get("region"), "attrs": json.dumps(attrs, default=str)})
    return out


def series_nodes(docs: dict) -> list[dict]:
    out = []
    for d in docs.values():
        if d.get("doc_type") != "dataset" or not d.get("measured"):
            continue
        for v in d.get("variables") or []:
            w = (d.get("scope") or {}).get("window") or [None, None]
            attrs = {"dataset": d["id"], "variable": v, "resolution": d.get("resolution"), "unit": d.get("unit"),
                     "version": d.get("version"), "clock": d.get("clock")}
            out.append({"id": f"series:{d['id']}:{v}", "kind": "Series", "subtype": d.get("unit"),
                        "label": f"{d['id']} / {v}", **_trust(d), "valid_start": to_central(w[0]),
                        "valid_end": to_central(w[1]), "region": (d.get("scope") or {}).get("region"),
                        "attrs": json.dumps(attrs, default=str)})
    return out


def item_nodes(g: dict, docs: dict) -> tuple[list[dict], dict]:
    """Entity mentions, canonical entities, events and quant claims. Returns nodes and gid -> node id."""
    nodes, node_of = [], {}
    canon = {}
    for e in g["entities"]:
        d = docs[e["doc_id"]]
        nodes.append({"id": e["gid"], "kind": "EntityMention", "subtype": e["type"], "label": e["name"],
                      **_trust(d), "quote": e.get("quote"),
                      "attrs": json.dumps({"canonical_id": e["canonical_id"], "attrs": e.get("attrs")}, default=str)})
        node_of[e["gid"]] = e["canonical_id"]
        c = canon.setdefault(e["canonical_id"], {"type": e["type"], "names": set(), "docs": set(), "tiers": set(),
                                                 "mentions": 0})
        c["names"].add(e["name"]), c["docs"].add(e["doc_id"]), c["tiers"].add(d.get("tier"))
        c["mentions"] += 1
    for cid, c in canon.items():
        # A canonical entity is not an item: it has no single source. Its tier is the best (lowest) of its mentions.
        nodes.append({"id": cid, "kind": "Entity", "subtype": c["type"], "label": cid.split(":", 2)[-1].replace("_", " "),
                      "tier": min(c["tiers"]), "attrs": json.dumps({"names": sorted(c["names"]), "docs": sorted(c["docs"]),
                                                                    "mentions": c["mentions"]})})
    for ev in g["events"]:
        d, t = docs[ev["doc_id"]], ev.get("time") or {}
        nodes.append({"id": ev["gid"], "kind": "Event", "subtype": ev["type"], "label": ev.get("summary"),
                      **_trust(d), "valid_start": to_central(t.get("time_resolved")),
                      "valid_end": to_central(t.get("time_end")), "precision": t.get("precision"),
                      "region": ev.get("region"), "modality": ev.get("modality"), "quote": ev.get("quote"),
                      "attrs": json.dumps({"time_text": t.get("time_text"), "attrs": ev.get("attrs")}, default=str)})
        node_of[ev["gid"]] = ev["gid"]
    for q in g["quant_claims"]:
        d = docs[q["doc_id"]]
        as_of = to_central(q.get("as_of")) if q.get("as_of") and str(q["as_of"])[:2] == "20" else None
        attrs = {k: q.get(k) for k in ("value", "value_low", "value_high", "unit", "metric", "denominator",
                                       "window_start", "window_end", "as_of", "cause_category", "cause_label_raw",
                                       "cause_label_canon", "cause_scheme", "scope_source", "approximate")}
        nodes.append({"id": q["gid"], "kind": "QuantClaim", "subtype": q["metric"],
                      "label": f"{q['value']} {q.get('unit') or ''}".strip(), **_trust(d),
                      "valid_start": as_of or to_central(q.get("window_start")),
                      "valid_end": as_of or to_central(q.get("window_end")),
                      "precision": "minute" if as_of is not None and as_of.minute else ("as_of" if as_of else "window"),
                      "region": q.get("region"), "modality": q.get("modality"), "quote": q.get("quote"),
                      "attrs": json.dumps(attrs, default=str)})
        node_of[q["gid"]] = q["gid"]
    return nodes, node_of


# ---------------------------------------------------------------- edges
def item_edges(g: dict, docs: dict, node_of: dict) -> list[dict]:
    out = []
    for kind in ("entities", "events", "quant_claims"):
        for it in g[kind]:
            out.append({"src": it["gid"], "dst": f"doc:{it['doc_id']}", "kind": "stated_in", "doc_id": it["doc_id"],
                        "tier": docs[it["doc_id"]].get("tier")})
    for e in g["entities"]:
        out.append({"src": e["gid"], "dst": e["canonical_id"], "kind": "mention_of"})
    for r in g["relations"]:
        d = docs[r["doc_id"]]
        out.append({"src": node_of[r["subject_gid"]], "dst": node_of[r["object_gid"]], "kind": r["predicate"],
                    "modality": r.get("modality"), "quote": r.get("quote"), "gid": r["gid"],
                    **{k: v for k, v in _trust(d).items() if k != "recorded_at"},
                    "attrs": json.dumps({"subject_gid": r["subject_gid"], "object_gid": r["object_gid"]})})
    for q in g["quant_claims"]:
        if q.get("about_gid") and q["about_gid"] in node_of:
            out.append({"src": q["gid"], "dst": node_of[q["about_gid"]], "kind": "about", "doc_id": q["doc_id"]})
    for l in g["links"]:
        out.append({"src": l["a"], "dst": l["b"], "kind": l["label"],
                    "attrs": json.dumps({"cause_category": l["cause_category"], "differs_on": l["differs_on"],
                                         "values": l["values"], "approximate": l.get("approximate", False)})})
    return out


def revision_edges(docs: dict, rev: pd.DataFrame) -> list[dict]:
    out = []
    for d in docs.values():
        nxt = d.get("superseded_by")
        if nxt and d.get("doc_type") != "dataset":
            out.append({"src": f"doc:{d['id']}", "dst": f"doc:{nxt}", "kind": "revised_by",
                        "attrs": json.dumps({"revision_kind": DOC_REVISION_KIND.get((d["id"], nxt)),
                                             "revision_kind_source": "hand (ASSUMPTIONS G-R1)"})})
    s = summary(rev)
    src, dst = "series:unit_outage_2021_03_04:unit_outages", "series:unit_outage_2021_03_12:unit_outages"
    kinds = {"correction": ["correction"], "disclosure": ["disclosure", "disclosure_ambiguous"],
             "expansion": ["expansion"]}
    for rk, members in kinds.items():
        attrs = {"revision_kind": rk,
                 "rows": {m: s["rows_by_kind"].get(m, 0) for m in members},
                 "mw": {m: s["mw_by_kind"].get(m, 0) for m in members},
                 "by_fuel": {m: {f: n for f, n in s["by_kind_and_fuel"].get(m, {}).items() if n} for m in members},
                 "detail": "unit_revisions.parquet"}
        if rk == "correction":
            attrs["fields"] = s["correction_fields"]
        out.append({"src": src, "dst": dst, "kind": "revised_by", "attrs": json.dumps(attrs, default=str)})
    for d in docs.values():
        if d.get("doc_type") == "dataset" and d.get("measured"):
            for v in d.get("variables") or []:
                out.append({"src": f"series:{d['id']}:{v}", "dst": f"doc:{d['id']}", "kind": "stated_in",
                            "doc_id": d["id"], "tier": d.get("tier")})
    return out


# ---------------------------------------------------------------- io
def frame(rows: list[dict], cols: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df:
            df[c] = None
    return df[cols + [c for c in df.columns if c not in cols]]


def load(run: Path = RUN) -> tuple[pd.DataFrame, pd.DataFrame]:
    return pd.read_parquet(run / "nodes.parquet"), pd.read_parquet(run / "edges.parquet")


def save(nodes: pd.DataFrame, edges: pd.DataFrame, run: Path = RUN) -> None:
    for c in ("valid_start", "valid_end"):
        nodes[c] = pd.to_datetime(nodes[c])
    nodes.to_parquet(run / "nodes.parquet", index=False)
    edges.to_parquet(run / "edges.parquet", index=False)
    export_json(nodes, edges, run)


def export_json(nodes: pd.DataFrame, edges: pd.DataFrame, run: Path = RUN) -> None:
    """Compact graph for a frontend: ids, kinds, trust, times, quotes; no chunk text."""
    keep_n = [c for c in nodes.columns if c not in ("attrs",)]
    n = nodes[keep_n].copy()
    for c in ("valid_start", "valid_end"):
        n[c] = n[c].map(lambda t: None if pd.isna(t) else t.isoformat())
    n = n.astype(object).where(n.notna(), None)
    e = edges.astype(object).where(edges.notna(), None)
    out = {"generated": dt.datetime.now().isoformat(timespec="seconds"),
           "nodes": n.to_dict(orient="records"),
           "edges": [{k: v for k, v in r.items() if v is not None} for r in e.to_dict(orient="records")]}
    (run / "graph.json").write_text(json.dumps(out, default=str, separators=(",", ":")))


def counts(nodes: pd.DataFrame, edges: pd.DataFrame) -> dict:
    return {"nodes_by_kind": nodes.kind.value_counts().to_dict(),
            "edges_by_kind": edges.kind.value_counts().to_dict(),
            "nodes_by_tier": nodes.tier.fillna("none").value_counts().sort_index().to_dict(),
            "items_by_kind_and_tier": nodes[nodes.kind.isin(["EntityMention", "Event", "QuantClaim"])]
            .pivot_table(index="kind", columns="tier", values="id", aggfunc="size", fill_value=0)
            .to_dict(orient="index")}


def check(nodes: pd.DataFrame, edges: pd.DataFrame) -> None:
    """Every extracted item has exactly one stated_in; its tier equals its document's tier."""
    items = nodes[nodes.kind.isin(["EntityMention", "Event", "QuantClaim", "Series"])]
    si = edges[edges.kind == "stated_in"].groupby("src").size()
    bad = items[~items.id.isin(si[si == 1].index)]
    assert bad.empty, f"{len(bad)} items without exactly one stated_in"
    doc_tier = nodes[nodes.kind == "Document"].set_index("doc_id").tier
    assert (items.tier.values == doc_tier.loc[items.doc_id].values).all(), "item tier differs from its document"
    rel = edges[edges.kind.isin(EXTRACTED_PREDICATES)]
    assert rel.doc_id.notna().all(), "relation without a document"
    ids = set(nodes.id)
    dangling = edges[~edges.src.isin(ids) | ~edges.dst.isin(ids)]
    assert dangling.empty, f"{len(dangling)} edges point at missing nodes: {dangling.kind.value_counts().to_dict()}"


def build(run: Path = RUN) -> dict:
    docs = documents()
    g = json.loads((run / "post_graph.json").read_text())
    rev = revisions()
    rev.to_parquet(run / "unit_revisions.parquet", index=False)
    inodes, node_of = item_nodes(g, docs)
    nodes = frame(document_nodes(docs) + series_nodes(docs) + inodes, NODE_COLS)
    edges = frame(item_edges(g, docs, node_of) + revision_edges(docs, rev), EDGE_COLS)
    check(nodes, edges)
    save(nodes, edges, run)
    c = counts(nodes, edges)
    c["unit_revisions"] = summary(rev)
    c["skipped_other_items"] = len(g.get("other", []))
    (run / "graph_counts.json").write_text(json.dumps(c, indent=1, default=str))
    return c


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=str(RUN))
    a = ap.parse_args()
    c = build(Path(a.run))
    print(json.dumps({k: v for k, v in c.items() if k != "unit_revisions"}, indent=1, default=str))
    print("unit revisions:", json.dumps(c["unit_revisions"]["rows_by_kind"]))
