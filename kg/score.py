"""Feasibility scorecard (config/scorecard.md) for one run.

    python -m kg.score <run>              # automatic tests + writes review CSVs
    python -m kg.score <run> --reviewed   # also reads the hand-marked CSVs
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import random

import pandas as pd

from .common import DATA, ROOT, RESULTS, documents
from .post import SHARE_METRICS, compare

ANSWER_KEY = DATA / "answer_key" / "ut_timeline_events.csv"
CST = dt.timezone(dt.timedelta(hours=-6))
SLICE = (dt.datetime(2021, 2, 14, 21), dt.datetime(2021, 2, 16, 12))


def _central(s):
    """ISO string -> naive Central datetime, or None if it has no clock time."""
    if not s or "T" not in str(s):
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone(CST).replace(tzinfo=None) if t.tzinfo else t


def test_coverage(g):
    n = {k: len(g[k]) for k in ("events", "relations", "quant_claims", "other")}
    share = n["other"] / max(1, sum(n.values()))
    clusters = collections.Counter(o["suggested_type"].strip().lower() for o in g["other"])
    return {"other_share": share, "pass": share < 0.15, "counts": n,
            "top_other_types": clusters.most_common(15)}


def test_recall(g):
    if not ANSWER_KEY.exists():
        return {"status": "no answer key yet"}
    key = pd.read_csv(ANSWER_KEY)
    unverified = 0
    if "verified" in key:  # rows marked n/no are dropped; blank rows are kept but counted
        v = key["verified"].astype(object).where(key["verified"].notna(), "").astype(str).str.strip().str.lower()
        unverified = int((v == "").sum())
        key = key[~v.str.startswith("n")]
    evs = [(e["type"], _central(e["time"].get("time_resolved")), e["gid"]) for e in g["events"]]
    evs = [e for e in evs if e[1]]
    rows = []
    for _, k in key.iterrows():
        t = _central(k["time_central"])
        hit = next((gid for typ, et, gid in evs
                    if typ == k["event_type"] and t and abs((et - t).total_seconds()) <= 3600), None)
        rows.append({"time": k["time_central"], "type": k["event_type"], "in_slice": bool(k["in_slice"]),
                     "summary": k["summary"], "matched": hit})
    res = {}
    for name, rs in (("in_slice", [r for r in rows if r["in_slice"]]), ("all", rows)):
        res[name] = {"n": len(rs), "hits": sum(1 for r in rs if r["matched"]),
                     "recall": (sum(1 for r in rs if r["matched"]) / len(rs)) if rs else None}
    res["pass"] = (res["in_slice"]["recall"] or 0) > 0.60
    res["answer_key_rows_unverified"] = unverified
    res["misses"] = [r for r in rows if not r["matched"] and r["in_slice"]]
    return res


def test_scope(g, run, reviewed):
    shares = [q for q in g["quant_claims"] if q["metric"] in SHARE_METRICS]
    src = collections.Counter()
    for q in shares:
        for f, v in q["scope_source"].items():
            src[f"{f}:{v}"] += 1
    filled = sum(1 for q in shares if q["denominator"] and q["region"] and q["window_start"])
    path = run / "review_scope.csv"
    res = {"n_shares": len(shares), "all_three_filled": filled / max(1, len(shares)),
           "scope_source_counts": dict(src), "review_file": str(path.relative_to(ROOT))}
    if not path.exists():
        rnd = random.Random(0)
        sample = rnd.sample(shares, min(20, len(shares)))
        pd.DataFrame([{"gid": q["gid"], "doc_id": q["doc_id"], "quote": q["quote"], "value": q["value"],
                       "value_low": q.get("value_low"), "value_high": q.get("value_high"),
                       "approximate": q.get("approximate"),
                       "unit": q["unit"], "metric": q["metric"], "denominator": q["denominator"],
                       "region": q["region"], "window_start": q["window_start"],
                       "window_end": q["window_end"], "as_of": q["as_of"],
                       "scope_source": json.dumps(q["scope_source"]),
                       "correct_y_n": "", "note": ""} for q in sample]).to_csv(path, index=False)
    if reviewed:
        df = pd.read_csv(path).fillna("")
        marked = df[df.correct_y_n.astype(str).str.strip() != ""]
        ok = marked.correct_y_n.astype(str).str.lower().str.startswith("y").sum()
        res["hand_checked"] = len(marked)
        res["correct"] = ok / len(marked) if len(marked) else None
        res["pass"] = bool((res["correct"] or 0) > 0.70)
    return res


def test_12_vs_314(g):
    docs = documents()
    fuel = [q for q in g["quant_claims"] if q["cause_category"] == "fuel_supply"]
    ercot = [q for q in fuel if q["doc_id"] == "ercot_causes_2021_04_27" and abs(q["value"] - 12) < 0.6]
    ferc = [q for q in fuel if q["doc_id"] == "ferc_final_2021_11" and abs(q["value"] - 31.4) < 0.06]
    res = {"ercot_12_found": len(ercot), "ferc_31_4_found": len(ferc)}
    if ercot and ferc:
        label, diffs = compare(ercot[0], ferc[0], docs)
        res.update({"label": label, "differs_on": diffs,
                    "ercot": {k: ercot[0][k] for k in ("metric", "region", "window_start", "window_end",
                                                        "denominator", "cause_label_raw", "quote")},
                    "ferc": {k: ferc[0][k] for k in ("metric", "region", "window_start", "window_end",
                                                      "denominator", "cause_label_raw", "quote")}})
        res["pass"] = label == "different_scope" and {"metric", "region"} <= set(diffs)
    else:
        res["pass"] = False
    return res


def test_precision(g, run, reviewed):
    path = run / "review_precision.csv"
    if not path.exists():
        pool = [(k, x) for k in ("entities", "events", "relations", "quant_claims") for x in g[k]]
        sample = random.Random(1).sample(pool, min(40, len(pool)))
        rows = []
        for kind, x in sample:
            fields = {k: v for k, v in x.items() if k not in
                      ("quote", "gid", "doc_id", "chunk_id", "tier", "flags", "claim_status", "published_at")}
            rows.append({"gid": x["gid"], "kind": kind, "quote": x["quote"],
                         "extracted": json.dumps(fields, default=str),
                         "stated_y_n": "", "type_ok_y_n": "", "fields_ok_y_n": "", "note": ""})
        pd.DataFrame(rows).to_csv(path, index=False)
    res = {"review_file": str(path.relative_to(ROOT))}
    if reviewed:
        df = pd.read_csv(path).fillna("")
        m = df[df.stated_y_n.astype(str).str.strip() != ""]
        yes = lambda c: m[c].astype(str).str.lower().str.startswith("y").mean() if len(m) else None
        res.update({"hand_checked": len(m), "stated": yes("stated_y_n"), "type_ok": yes("type_ok_y_n"),
                    "fields_ok": yes("fields_ok_y_n"),
                    "all_three": (m[["stated_y_n", "type_ok_y_n", "fields_ok_y_n"]].astype(str)
                                  .apply(lambda s: s.str.lower().str.startswith("y")).all(axis=1).mean()
                                  if len(m) else None)})
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--reviewed", action="store_true")
    a = ap.parse_args()
    run = RESULTS / a.run
    g = json.loads((run / "graph.json").read_text())
    cfg = json.loads((run / "config.json").read_text())
    s = g["stats"]
    scores = {
        "run": a.run, "model": cfg["model"], "effort": cfg.get("effort"), "date": cfg["date"],
        "1_coverage": test_coverage(g),
        "2_recall": test_recall(g),
        "3_scope": test_scope(g, run, a.reviewed),
        "4_12_vs_31_4": test_12_vs_314(g),
        "precision": test_precision(g, run, a.reviewed),
        "quote_check": {"fail_rate": s["quote_fail_rate"], "pass": (s["quote_fail_rate"] or 0) < 0.02,
                        "fails_by_kind": s["quote_fail"]},
        "chunk_errors": s["chunk_errors"],
    }
    (run / "scores.json").write_text(json.dumps(scores, indent=1, default=str))
    print(json.dumps(scores, indent=1, default=str))


if __name__ == "__main__":
    main()
