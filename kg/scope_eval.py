"""2x2 test of the scope fixes on hand-labelled cause-share sentences (data/answer_key/scope_gold.yaml).

Factors:
  A  scope card:     document card only  vs  document + section cards
  B  local context:  off  vs  each number sentence with the sentence before it + the nearby-date prompt rule
Every cell is also scored with and without the code date rule (kg/scope.apply_date_rule), which costs nothing.

Pass 1 is reused from a base run (default: run 1), so only pass 2 is re-run: 4 cells x the gold chunks.

    uv run python -m kg.scope_eval                      # run + score
    uv run python -m kg.scope_eval --score-only         # re-score cached outputs
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import yaml

from .chunk import load_extractions
from .common import DATA, RESULTS, documents, norm_quote, schema
from .extract import _halves, _only, _system, pass2_prompt, scope_inputs
from .llm import LLM, DEFAULT_MODEL, Truncated
from .scope import apply_date_rule, sentence_of

GOLD = DATA / "answer_key" / "scope_gold.yaml"
CELLS = {"A0B0": (False, False), "A1B0": (True, False), "A0B1": (False, True), "A1B1": (True, True)}
FIELDS = ["metric", "denominator", "region", "window"]


# ---------------------------------------------------------------- run
def run_cell(llm: LLM, rec: dict, section_cards: bool, context: bool, depth: int = 0) -> dict:
    """Pass 2 only. If the output is truncated, split the chunk in half (as kg.extract does) and merge."""
    doc = documents()[rec["doc_id"]]
    extra = scope_inputs(llm, rec, section_cards, context)
    p2 = pass2_prompt(doc, rec["text"], rec["extraction"]["events"], **extra)
    try:
        out = llm.json_call([{"role": "system", "content": _system()}, {"role": "user", "content": p2}],
                            schema(), check=_only({"quant_claims"}))
    except Truncated:
        if depth >= 2:
            return {"chunk_id": rec["chunk_id"], "quant_claims": [], "scope_inputs": extra, "error": "truncated"}
        halves = [run_cell(llm, {**rec, "text": h}, section_cards, context, depth + 1) for h in _halves(rec["text"])]
        return {"chunk_id": rec["chunk_id"], "quant_claims": [q for h in halves for q in h["quant_claims"]],
                "scope_inputs": extra, "split": depth + 1,
                "error": next((h["error"] for h in halves if h.get("error")), None)}
    return {"chunk_id": rec["chunk_id"], "quant_claims": out["quant_claims"], "scope_inputs": extra}


def run_all(out_dir: Path, base: Path, model: str, effort: str, workers: int) -> None:
    gold = yaml.safe_load(GOLD.read_text())["items"]
    recs = {r["chunk_id"]: r for r in load_extractions(base / "extractions.jsonl")}
    chunks = sorted({g["chunk"] for g in gold})
    llm = LLM(out_dir / "cache", model=model, effort=effort)
    jobs = [(cell, recs[c]) for cell in CELLS for c in chunks]
    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(lambda j: (j[0], run_cell(llm, j[1], *CELLS[j[0]])), jobs))
    for cell in CELLS:
        with (out_dir / f"cell_{cell}.jsonl").open("w") as f:
            for c, r in results:
                if c == cell:
                    f.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------- score
def _overlaps(quote: str, sentence: str) -> bool:
    q, s = norm_quote(quote), norm_quote(sentence)
    if not q or not s:
        return False
    if q in s or s in q:
        return True
    m = SequenceMatcher(None, q, s, autojunk=False).find_longest_match(0, len(q), 0, len(s))
    return m.size >= 20


def _value_ok(c: dict, g: dict) -> bool:
    if "value_low" in g:
        lo, hi = c.get("value_low"), c.get("value_high")
        return (lo is not None and hi is not None and abs(lo - g["value_low"]) < 0.051
                and abs(hi - g["value_high"]) < 0.051) or c["value"] in (g["value_low"], g["value_high"], g["value"])
    return abs(c["value"] - g["value"]) < 0.051


def match(claims: list[dict], g: dict, text: str) -> dict | None:
    sent = sentence_of(g["anchor"], text) or g["anchor"]
    hits = [c for c in claims if isinstance(c.get("value"), (int, float)) and _value_ok(c, g)
            and _overlaps(c.get("quote", ""), sent)]
    return hits[0] if hits else None


def score_claim(c: dict | None, g: dict) -> dict:
    if c is None:
        return {f: False for f in FIELDS} | {"all4": False, "found": False}
    w = (str(c.get("window_start") or "")[:10], str(c.get("window_end") or "")[:10])
    ok = {
        "metric": c.get("metric") in g["metric"],
        "denominator": bool(c.get("denominator")) and bool(re.search(g["denominator"], c["denominator"], re.I)),
        "region": bool(c.get("region")) and bool(re.search(g["region"], c["region"], re.I)),
        "window": list(w) in [list(x) for x in g["window"]]
                  or bool(g.get("as_of") and c.get("as_of") and re.search(g["as_of"], str(c["as_of"]), re.I)),
    }
    return ok | {"all4": all(ok.values()), "found": True}


def score(out_dir: Path, base: Path) -> dict:
    gold = yaml.safe_load(GOLD.read_text())["items"]
    recs = {r["chunk_id"]: r for r in load_extractions(base / "extractions.jsonl")}
    sources = {cell: {json.loads(l)["chunk_id"]: json.loads(l)["quant_claims"]
                      for l in (out_dir / f"cell_{cell}.jsonl").open()}
               for cell in CELLS if (out_dir / f"cell_{cell}.jsonl").exists()}
    sources["run1_v0"] = {cid: r["extraction"]["quant_claims"] for cid, r in recs.items() if r["extraction"]}
    rows = []
    for src, by_chunk in sources.items():
        for g in gold:
            text = recs[g["chunk"]]["text"]
            c = match(by_chunk.get(g["chunk"], []), g, text)
            for rule in (False, True):
                cc = apply_date_rule(c, text) if (c and rule) else c
                s = score_claim(cc, g)
                rows.append({"cell": src, "date_rule": rule, "item": g["id"], "tests": g.get("tests", ""), **s,
                             "got_metric": cc and cc.get("metric"), "got_denominator": cc and cc.get("denominator"),
                             "got_region": cc and cc.get("region"),
                             "got_window": cc and f'{cc.get("window_start")}..{cc.get("window_end")}',
                             "got_as_of": cc and cc.get("as_of"),
                             "scope_source": cc and json.dumps(cc.get("scope_source")),
                             "quote": cc and cc.get("quote")})
    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "item_scores.csv", index=False)
    summ = (df.groupby(["cell", "date_rule"])[FIELDS + ["all4", "found"]].sum().astype(int)
            .reset_index())
    summ["n"] = len(gold)
    summ.to_csv(out_dir / "summary.csv", index=False)
    return {"summary": summ.to_dict("records")}


def cost(out_dir: Path) -> dict:
    u = [json.loads(l)["usage"] for l in (out_dir / "usage.jsonl").open()] if (out_dir / "usage.jsonl").exists() else []
    p = sum(x["prompt_tokens"] for x in u)
    c = sum(x["completion_tokens"] for x in u)
    hit = sum(x.get("prompt_cache_hit_tokens", 0) for x in u)
    return {"calls": len(u), "prompt_tokens": p, "prompt_cache_hit": hit, "completion_tokens": c}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="02_scope_eval")
    ap.add_argument("--base", default="01_extraction")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--effort", default="high")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--score-only", action="store_true")
    a = ap.parse_args()
    out_dir, base = RESULTS / a.out, RESULTS / a.base
    out_dir.mkdir(parents=True, exist_ok=True)
    if not a.score_only:
        run_all(out_dir, base, a.model, a.effort, a.workers)
    res = score(out_dir, base) | {"usage": cost(out_dir)}
    (out_dir / "results.json").write_text(json.dumps(res, indent=1))
    print(pd.DataFrame(res["summary"]).to_string(index=False))
    print(res["usage"])


if __name__ == "__main__":
    main()
