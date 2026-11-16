"""Two-pass extraction per chunk, following config/extraction_prompt.md.

The model sees the document card (scope) but never tier or flags.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .chunk import build, dump_extractions, halves, load_extractions, slice_doc_ids
from .common import CONFIG, RESULTS, documents, schema
from .llm import LLM, DEFAULT_MODEL, SchemaFailure, Truncated


def _prompt_blocks() -> tuple[str, ...]:
    md = (CONFIG / "extraction_prompt.md").read_text()
    blocks = re.findall(r"```\n(.*?)```", md, flags=re.S)
    system, pass1, pass2, _card_sys, _card_user, sec_add, ctx_add = blocks[:7]
    return system.strip(), pass1.strip(), pass2.strip(), "\n" + sec_add.strip() + "\n", "\n" + ctx_add.strip() + "\n"


SYSTEM, PASS1, PASS2, SECTION_ADDENDUM, CONTEXT_ADDENDUM = _prompt_blocks()


def _system() -> str:
    return (SYSTEM + "\n\nOutput a single JSON object that validates against this JSON Schema. "
            "Include all five top-level arrays.\n" + json.dumps(schema(), separators=(",", ":")))


def _card_values(doc: dict) -> dict:
    s = doc.get("scope") or {}
    title = doc.get("title") or Path(doc["file"]).stem.replace("_", " ")
    return {
        "{title}": title,
        "{publisher}": doc.get("publisher", "unknown"),
        "{published_at}": str(doc.get("published_at", "unknown")),
        "{scope.region}": str(s.get("region")),
        "{scope.window}": str(s.get("window")),
        "{scope.as_of}": str(s.get("as_of")),
        "{scope.cause_scheme}": str(s.get("cause_scheme")),
        "{scope.cause_metric_default}": str(s.get("cause_metric_default")),
    }


def _fill(template: str, values: dict) -> str:
    for k, v in values.items():
        template = template.replace(k, v)
    return template


def _only(keys):
    def check(obj):
        return [f"{k} must be empty in this pass" for k in
                ("entities", "events", "relations", "quant_claims", "other")
                if k not in keys and obj.get(k)]
    return check


def pass2_prompt(doc: dict, text: str, events: list[dict], section_cards: list[dict] | None = None,
                 context: str | None = None) -> str:
    """Pass-2 user message. section_cards / context switch on the v0.2 scope fixes."""
    vals = _card_values(doc)
    sec = (_fill(SECTION_ADDENDUM, {"{section_cards_json}": json.dumps(section_cards, indent=1)})
           if section_cards else "")
    ctx = _fill(CONTEXT_ADDENDUM, {"{number_context}": context}) if context else ""
    ev = [{"local_id": e["local_id"], "type": e["type"], "summary": e["summary"]} for e in events]
    # fill the addenda first: the chunk text may contain brace-like strings
    p2 = PASS2.replace("{section_cards_block}", sec).replace("{context_block}", ctx)
    return _fill(p2, vals | {"{pass1_events_json}": json.dumps(ev), "{chunk_text}": text})


def extract_text(llm: LLM, doc: dict, text: str, section_cards: list[dict] | None = None,
                 context: str | None = None, pass1: dict | None = None) -> dict:
    """Two passes. `pass1` reuses an earlier pass-1 result (the 2x2 eval re-runs pass 2 only)."""
    vals = _card_values(doc)
    sys_msg = {"role": "system", "content": _system()}
    if pass1 is None:
        p1 = _fill(PASS1, vals | {"{chunk_text}": text})
        out1 = llm.json_call([sys_msg, {"role": "user", "content": p1}], schema(),
                             check=_only({"entities", "events", "relations", "other"}))
    else:
        out1 = pass1
    p2 = pass2_prompt(doc, text, out1["events"], section_cards, context)
    out2 = llm.json_call([sys_msg, {"role": "user", "content": p2}], schema(),
                         check=_only({"quant_claims"}))
    return {"entities": out1["entities"], "events": out1["events"], "relations": out1["relations"],
            "other": out1["other"], "quant_claims": out2["quant_claims"]}


_halves = halves


def scope_inputs(llm: LLM, chunk: dict, section_cards: bool, context: bool) -> dict:
    from .scope import number_context, section_cards_for
    out = {}
    if section_cards:
        out["section_cards"] = section_cards_for(llm, chunk["doc_id"], chunk["page_start"], chunk["page_end"]) or None
    if context:
        out["context"] = number_context(chunk["doc_id"], chunk["text"]) or None
    return out


def extract_chunk(llm: LLM, chunk: dict, depth: int = 0, section_cards: bool = False,
                  context: bool = False) -> list[dict]:
    """Returns one record per (sub)chunk. Splits the chunk in half if output is truncated."""
    doc = documents()[chunk["doc_id"]]
    try:
        extra = scope_inputs(llm, chunk, section_cards, context)
        return [{**chunk, "extraction": extract_text(llm, doc, chunk["text"], **extra), "error": None,
                 "scope_inputs": extra or None}]
    except Truncated:
        if depth >= 2:
            return [{**chunk, "extraction": None, "error": "truncated"}]
        a, b = _halves(chunk["text"])
        kw = {"section_cards": section_cards, "context": context}
        return (extract_chunk(llm, {**chunk, "chunk_id": chunk["chunk_id"] + "a", "text": a, "n_words": len(a.split())}, depth + 1, **kw)
                + extract_chunk(llm, {**chunk, "chunk_id": chunk["chunk_id"] + "b", "text": b, "n_words": len(b.split())}, depth + 1, **kw))
    except SchemaFailure as e:
        return [{**chunk, "extraction": None, "error": f"schema: {e}"}]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", help="run dir name (default: <date>_<model>)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--effort", default="high", choices=["low", "high", "max"])
    ap.add_argument("--docs", nargs="*", help="doc ids (default: scorecard slice)")
    ap.add_argument("--full", action="store_true", help="all chunks, not just the FERC slice")
    ap.add_argument("--limit", type=int, help="first N chunks only (smoke test)")
    ap.add_argument("--chunk", help="only this chunk_id")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--section-cards", action="store_true", help="v0.2 scope fix: per-section scope cards")
    ap.add_argument("--context", action=argparse.BooleanOptionalAction, default=True,
                    help="v0.2 scope fix: previous sentence + nearby-date rule (default on since 2026-11-15; "
                         "--no-context for the old behaviour)")
    a = ap.parse_args()

    run = RESULTS / (a.run or f"{dt.date.today()}_{a.model}")
    run.mkdir(parents=True, exist_ok=True)
    chunks = build(a.docs or slice_doc_ids(), slice_only=not a.full)
    if a.chunk:
        chunks = [c for c in chunks if c["chunk_id"] == a.chunk]
    if a.limit:
        chunks = chunks[: a.limit]
    llm = LLM(run / "cache", model=a.model, effort=a.effort)
    (run / "config.json").write_text(json.dumps({"model": a.model, "effort": a.effort,
                                                 "section_cards": a.section_cards, "context": a.context,
                                                 "docs": sorted({c['doc_id'] for c in chunks}),
                                                 "n_chunks": len(chunks), "date": str(dt.date.today())}, indent=1))
    recs = []
    with ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(extract_chunk, llm, c, 0, a.section_cards, a.context): c["chunk_id"] for c in chunks}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            recs.extend(r)
            bad = [x["chunk_id"] for x in r if x["error"]]
            print(f"[{i}/{len(chunks)}] {futs[f]}" + (f"  ERROR {bad}" if bad else ""), flush=True)
    # merge with earlier records in this run (re-extracted chunks replace their old records)
    path = run / "extractions.jsonl"
    old = load_extractions(path) if path.exists() else []
    done = {c["chunk_id"] for c in chunks}
    keep = [r for r in old if r["chunk_id"].rstrip("ab") not in done]
    allrecs = sorted(keep + recs, key=lambda r: r["chunk_id"])
    dump_extractions(path, allrecs)
    cfg = json.loads((run / "config.json").read_text())
    cfg["docs"] = sorted({r["doc_id"] for r in allrecs})
    cfg["n_chunks"] = len({r["chunk_id"].rstrip("ab") for r in allrecs})
    (run / "config.json").write_text(json.dumps(cfg, indent=1))
    print(f"wrote {len(recs)} new records ({len(allrecs)} total) to {path}; errors: "
          f"{sum(1 for r in recs if r['error'])}")


if __name__ == "__main__":
    main()
