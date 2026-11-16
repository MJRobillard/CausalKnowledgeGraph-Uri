"""After extraction, in code: trust attach, quote check, global ids, entity resolution,
comparability links. Implements the 'After extraction' list in config/extraction_prompt.md."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import itertools
import json
import re
from pathlib import Path

import yaml
from rapidfuzz import fuzz

from .chunk import load_extractions
from .common import CONFIG, RESULTS, documents, ontology, quote_in, value_in_quote

KINDS = ["entities", "events", "relations", "quant_claims", "other"]
TRUST_KEYS = ["tier", "flags", "claim_status", "published_at"]
SHARE_METRICS = {"share_of_events", "share_of_mw", "share_of_units", "share_of_volume",
                 "share_of_energy", "share_of_responses"}


# ---------------------------------------------------------------- comparability
def _norm_text(s) -> str:
    return re.sub(r"[^a-z0-9+]+", " ", str(s or "").lower()).strip()


def _similar(a, b, thr=80) -> bool:
    a, b = _norm_text(a), _norm_text(b)
    if not a or not b:
        return False
    return a == b or fuzz.token_set_ratio(a, b) >= thr


def _date(s):
    if not s:
        return None
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _windows_overlap(a: dict, b: dict) -> bool | None:
    a0, a1, b0, b1 = (_date(a.get("window_start")), _date(a.get("window_end")),
                      _date(b.get("window_start")), _date(b.get("window_end")))
    if not (a0 and b0):
        return None
    a1, b1 = a1 or a0, b1 or b0
    return a0 <= b1 and b0 <= a1


def _range(c: dict) -> tuple[float, float]:
    lo, hi = c.get("value_low"), c.get("value_high")
    if lo is None or hi is None:
        return c["value"], c["value"]
    return min(lo, hi), max(lo, hi)


def _close(a: dict, b: dict) -> bool:
    """Values agree if their ranges overlap within rounding tolerance (0.5 points for percents, 1% otherwise).
    `approximate` does NOT widen the tolerance: differences are still flagged as conflicts,
    and the link records that an approximate value was involved."""
    (a0, a1), (b0, b1) = _range(a), _range(b)
    gap = max(b0 - a1, a0 - b1, 0.0)
    unit = a.get("unit") or ""
    if "percent" in unit.lower() or unit == "%":
        return gap <= 0.5          # rounding: 31.4 vs 31 is fine
    return gap <= 0.01 * max(abs(a0), abs(a1), abs(b0), abs(b1), 1e-9)


# ---------------------------------------------------------------- cause labels (X7)
LABEL_FILLER = {"related", "the", "a", "an", "of", "to", "due", "by", "for", "on", "in", "at", "from", "with"}


def label_key(label) -> tuple[str, ...]:
    """Content words of a cause label: lowercase, split on - and /, singular, no filler words.
    'Fuel-related issues' -> ('fuel', 'issue'); 'other fuel issues' -> ('fuel', 'issue', 'other')."""
    toks = re.split(r"[\s\-/]+", re.sub(r"[^a-z0-9\-/ ]+", " ", str(label or "").lower()))
    out = set()
    for t in toks:
        if not t or t in LABEL_FILLER:
            continue
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.add(t)
    return tuple(sorted(out))


def labels_match(a, b) -> bool:
    """Same cause label: equal content-word sets, or one typo (exactly one differing word on each side,
    same first 3 letters, ratio >= 85). Not token_set, which scores subsets as 100 ('fuel issues' vs
    'natural gas fuel supply issues'); not plain token_sort, which merges 'planned' with 'unplanned'."""
    ka, kb = set(label_key(a)), set(label_key(b))
    if ka == kb:
        return True
    da, db = ka - kb, kb - ka
    if len(da) == len(db) == 1:
        x, y = da.pop(), db.pop()
        return x[:3] == y[:3] and fuzz.ratio(x, y) >= 85
    return False


def canonical_labels(claims: list[dict]) -> list[dict]:
    """Within each cause_scheme, map every cause_label_raw to a canonical label (the most frequent
    raw form of its group). Sets claim['cause_label_canon']. Returns the merges made."""
    by_scheme = collections.defaultdict(collections.Counter)
    for c in claims:
        if c.get("cause_label_raw"):
            by_scheme[c.get("cause_scheme")][c["cause_label_raw"]] += 1
    canon, merges = {}, []
    for scheme, counts in by_scheme.items():
        reps: list[str] = []
        for label, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            rep = next((r for r in reps if labels_match(r, label)), None)
            if rep is None:
                reps.append(label)
                rep = label
            elif rep != label:
                merges.append({"cause_scheme": scheme, "label": label, "into": rep})
            canon[(scheme, label)] = rep
    for c in claims:
        c["cause_label_canon"] = canon.get((c.get("cause_scheme"), c.get("cause_label_raw")))
    return merges


def _same_label(a: dict, b: dict) -> bool:
    if "cause_label_canon" in a and "cause_label_canon" in b:
        return a["cause_label_canon"] == b["cause_label_canon"]
    return labels_match(a.get("cause_label_raw"), b.get("cause_label_raw"))


def compare(a: dict, b: dict, docs: dict) -> tuple[str, list[str]]:
    """ontology.comparability_rule, plus two fixes:
    - within one cause scheme, a different cause label (after label_key normalization) is a different
      (sub)category;
    - claims from a document and the one that supersedes it are `superseded`."""
    da, db = docs[a["doc_id"]], docs[b["doc_id"]]
    if da.get("superseded_by") == b["doc_id"] or db.get("superseded_by") == a["doc_id"]:
        return "superseded", []
    diffs = []
    if a["metric"] != b["metric"]:
        diffs.append("metric")
    if not _similar(a.get("denominator"), b.get("denominator")):
        diffs.append("denominator")
    if _norm_text(a.get("region")) != _norm_text(b.get("region")):
        diffs.append("region")
    if a.get("cause_scheme") != b.get("cause_scheme"):
        diffs.append("cause_scheme")
    elif not _same_label(a, b):
        diffs.append("cause_label_raw")
    ov = _windows_overlap(a, b)
    if ov is not True:
        diffs.append("window" if ov is False else "window_missing")
    if diffs:
        return "different_scope", diffs
    return ("comparable" if _close(a, b) else "conflict"), []


def comparability_links(claims: list[dict], docs: dict) -> list[dict]:
    by_cause = collections.defaultdict(list)
    for c in claims:
        if c["cause_category"] not in ("not_a_cause", "unknown") and c["metric"] in SHARE_METRICS:
            by_cause[c["cause_category"]].append(c)
    links = []
    for cause, cs in by_cause.items():
        for a, b in itertools.combinations(cs, 2):
            if a["chunk_id"] == b["chunk_id"] and (a["value"] == b["value"] or a["quote"] == b["quote"]):
                continue  # same number repeated, or two ends of one range ("52 to 60 percent")
            label, diffs = compare(a, b, docs)
            links.append({"a": a["gid"], "b": b["gid"], "cause_category": cause, "label": label,
                          "differs_on": diffs, "values": [a["value"], b["value"]],
                          "approximate": bool(a.get("approximate") or b.get("approximate"))})
    return links


# ---------------------------------------------------------------- generic roles
def is_generic_role(entity: dict) -> bool:
    """ontology.generic_role_names: roles and classes are not entities."""
    generic = {_norm_text(n) for n in ontology().get("generic_role_names", [])}
    name = _norm_text(entity.get("name"))
    return name in generic or name.removeprefix("the ") in generic


# ---------------------------------------------------------------- entity resolution
def resolve_entities(entities: list[dict]) -> tuple[dict, list[dict]]:
    """gid -> canonical id. Exact name, then aliases.yaml, then fuzzy match within type."""
    alias_path = CONFIG / "aliases.yaml"
    aliases = {}
    if alias_path.exists():
        for canon, names in (yaml.safe_load(alias_path.read_text()) or {}).items():
            for n in [canon] + list(names):
                aliases[_norm_text(n)] = canon
    canon_of, reps, merges = {}, collections.defaultdict(list), []
    for e in entities:
        name = _norm_text(e["name"])
        key = aliases.get(name, name)
        how = "alias" if name in aliases and key != name else "exact"
        pool = reps[e["type"]]
        hit = next((r for r in pool if r["key"] == key), None)
        if hit is None:
            # token_sort, not token_set: token_set scores any subset as 100
            # ("oil fired generating units" vs "natural gas fired generating units")
            best = max(pool, key=lambda r: fuzz.token_sort_ratio(r["key"], key), default=None)
            nums = lambda t: re.findall(r"\d+", t)
            if (best and len(key) > 3 and nums(best["key"]) == nums(key)  # "411 MW unit" != "511 MW unit"
                    and fuzz.token_sort_ratio(best["key"], key) >= 90):
                hit, how = best, "fuzzy"
        if hit is None:
            hit = {"key": key, "cid": f"ent:{e['type']}:{key.replace(' ', '_')}", "name": e["name"]}
            pool.append(hit)
        elif how != "exact" or hit["name"] != e["name"]:
            merges.append({"gid": e["gid"], "name": e["name"], "into": hit["cid"], "how": how})
        canon_of[e["gid"]] = hit["cid"]
    return canon_of, merges


# ---------------------------------------------------------------- main
def process(run: Path, date_rule: bool = False, out_dir: Path | None = None) -> dict:
    """Post-process run/extractions.jsonl. Writes run/graph.json, or out_dir/post_graph.json if given."""
    docs = documents()
    recs = load_extractions(run / "extractions.jsonl")
    out = {k: [] for k in KINDS}
    stats = {"chunks": len(recs), "chunk_errors": [r["chunk_id"] for r in recs if r["error"]],
             "quote_fail": collections.Counter(), "items_raw": collections.Counter(),
             "relations_dangling": 0, "generic_roles_dropped": collections.Counter(),
             "dropped_examples": [], "value_fail": 0, "value_fail_examples": []}
    for r in recs:
        x = r["extraction"]
        if not x:
            continue
        doc = docs[r["doc_id"]]
        trust = {k: doc.get(k) for k in TRUST_KEYS} | {"doc_id": r["doc_id"], "chunk_id": r["chunk_id"]}
        local = {}
        for kind in KINDS:
            for i, item in enumerate(x[kind]):
                stats["items_raw"][kind] += 1
                if not quote_in(item.get("quote", ""), r["text"]):
                    stats["quote_fail"][kind] += 1
                    if len(stats["dropped_examples"]) < 25:
                        stats["dropped_examples"].append({"chunk": r["chunk_id"], "kind": kind,
                                                          "quote": item.get("quote")})
                    continue
                if kind == "quant_claims" and not value_in_quote(item):
                    stats["value_fail"] += 1   # X13: the number must be written in the quote
                    stats["value_fail_examples"].append({"chunk": r["chunk_id"], "value": item.get("value"),
                                                         "unit": item.get("unit"), "quote": item.get("quote")})
                    continue
                if kind == "entities" and is_generic_role(item):
                    stats["generic_roles_dropped"][item["name"]] += 1
                    continue  # relations that point at it become dangling and are dropped below
                lid = item.get("local_id") or f"{kind[0].upper()}{i + 1}"
                gid = f"{r['chunk_id']}:{kind}:{lid}"
                if item.get("local_id"):
                    local[item["local_id"]] = gid
                rec = {**item, **trust, "gid": gid}
                if kind == "quant_claims":
                    rec["cause_scheme"] = (doc.get("scope") or {}).get("cause_scheme")
                    if date_rule:
                        from .scope import apply_date_rule
                        rec = apply_date_rule(rec, r["text"])
                        stats.setdefault("date_rule_applied", 0)
                        stats["date_rule_applied"] += rec.get("date_rule_from") is not None
                out[kind].append(rec)
        # rewrite local ids to global ids for this chunk
        for rel in [o for o in out["relations"] if o["chunk_id"] == r["chunk_id"]]:
            rel["subject_gid"], rel["object_gid"] = local.get(rel["subject"]), local.get(rel["object"])
        for q in [o for o in out["quant_claims"] if o["chunk_id"] == r["chunk_id"]]:
            q["about_gid"] = local.get(q.get("about")) if q.get("about") else None
    dangling = [r for r in out["relations"] if not (r["subject_gid"] and r["object_gid"])]
    stats["relations_dangling"] = len(dangling)
    out["relations"] = [r for r in out["relations"] if r["subject_gid"] and r["object_gid"]]

    canon_of, merges = resolve_entities(out["entities"])
    for e in out["entities"]:
        e["canonical_id"] = canon_of[e["gid"]]
    label_merges = canonical_labels(out["quant_claims"])
    links = comparability_links(out["quant_claims"], docs)

    raw = sum(stats["items_raw"].values())
    fail = sum(stats["quote_fail"].values())
    stats.update({"items_raw": dict(stats["items_raw"]), "quote_fail": dict(stats["quote_fail"]),
                  "generic_roles_dropped": dict(stats["generic_roles_dropped"].most_common()),
                  "quote_fail_rate": fail / raw if raw else None,
                  "entities_canonical": len(set(canon_of.values())), "merges": len(merges),
                  "links": dict(collections.Counter(l["label"] for l in links))})
    stats["label_merges"] = len(label_merges)
    graph = {**out, "links": links, "entity_merges": merges, "label_merges": label_merges, "stats": stats}
    if out_dir is None:
        (run / "graph.json").write_text(json.dumps(graph, indent=1, default=str))
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "post_graph.json").write_text(json.dumps(graph, indent=1, default=str))
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--date-rule", action="store_true", help="v0.2 scope fix: a sentence's only date sets the window")
    ap.add_argument("--out", help="write post_graph.json to this folder instead of overwriting <run>/graph.json")
    a = ap.parse_args()
    s = process(RESULTS / a.run, date_rule=a.date_rule, out_dir=Path(a.out) if a.out else None)
    print(json.dumps({k: v for k, v in s.items() if k != "dropped_examples"}, indent=1))
