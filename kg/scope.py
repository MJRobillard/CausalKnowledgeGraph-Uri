"""Scope helpers for pass 2 (v0.2 scope fixes):

1. Section cards: the default scope of a report section (from the PDF outline), more specific than the
   document card. FERC's gas chapters are about producing states, not the electric Event Area.
2. Local context: each number-bearing sentence with the sentence before it, taken from the whole document,
   so it crosses chunk boundaries.
3. Date rule: a deterministic backstop. If a claim's sentence names exactly one February 2021 date or date
   range and the model left the window at a default, the window becomes that date.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .chunk import chunk_doc, page_texts
from .common import CONFIG, documents, norm_quote

# ---------------------------------------------------------------- sentences
_ABBREV = re.compile(r"\b(Jan|Feb|Mar|Apr|Aug|Sept?|Oct|Nov|Dec|No|Nos|Fig|Figs|approx|Inc|Corp|Co|vs|St|Mr|Ms|Dr)\.")
_DOTTED = re.compile(r"\b(?:[ap]\.m\.|U\.S\.|e\.g\.|i\.e\.)")
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(“\"•])")
_NUMBERISH = re.compile(r"\d|percent|\bhalf\b|\bthird\b|\bmajority\b", re.I)


def sentences(text: str) -> list[str]:
    t = re.sub(r"\s+", " ", text).strip()
    t = _ABBREV.sub(lambda m: m.group(1) + "§", t)
    t = _DOTTED.sub(lambda m: m.group(0).replace(".", "§"), t)
    return [s.replace("§", ".") for s in _SPLIT.split(t) if s.strip()]


def sentence_of(quote: str, text: str) -> str | None:
    """The sentence of `text` that contains `quote` (normalized), or None."""
    q = norm_quote(quote)
    if not q:
        return None
    ss = sentences(text)
    for s in ss:
        if q in norm_quote(s):
            return s
    for a, b in zip(ss, ss[1:]):  # quote spanning a sentence break
        if q in norm_quote(a + " " + b):
            return a + " " + b
    return None


@lru_cache(maxsize=32)
def _doc_sentences(doc_id: str) -> tuple[str, ...]:
    return tuple(sentences("\n\n".join(c["text"] for c in chunk_doc(doc_id))))


def number_context(doc_id: str, chunk_text: str, max_words: int = 70) -> str:
    """Each number-bearing sentence of the chunk with the sentence before it (from the whole document)."""
    doc_ss = _doc_sentences(doc_id)
    norm_doc = [norm_quote(s) for s in doc_ss]
    chunk_ss = sentences(chunk_text)
    # locate the chunk's first sentence in the document to find the sentence before it
    first = norm_quote(chunk_ss[0])[:60] if chunk_ss else ""
    start = next((i for i, s in enumerate(norm_doc) if first and first in s), None)

    def clip(s):
        w = s.split()
        return " ".join(w[:max_words]) + (" …" if len(w) > max_words else "")

    lines, prev = [], (doc_ss[start - 1] if start else None)
    if start is not None and chunk_ss and not norm_doc[start].startswith(norm_quote(chunk_ss[0])[:60]):
        # the chunk starts mid-sentence: show the whole sentence from the document
        chunk_ss = [doc_ss[start]] + chunk_ss[1:]
    for s in chunk_ss:
        if _NUMBERISH.search(s):
            lines.append(f'- PREV: "{clip(prev) if prev else "(none)"}"\n  THIS: "{clip(s)}"')
        prev = s
    return "\n".join(lines)


# ---------------------------------------------------------------- date rule
_MONTH = r"(?:February|Feb\.?)"
_D = r"(\d{1,2})\b"
_BETWEEN = re.compile(rf"between\s+{_MONTH}\s+{_D}\s+and\s+(?:{_MONTH}\s+)?{_D}", re.I)
_LIST = re.compile(rf"{_MONTH}\s+{_D}((?:\s*(?:,|and|or|&)\s*\d{{1,2}}\b)+)", re.I)
_RANGE = re.compile(rf"{_MONTH}\s+{_D}\s*(?:-|–|—|through|to)\s*(?:{_MONTH}\s+)?{_D}", re.I)
_DAY = re.compile(rf"{_MONTH}\s+{_D}", re.I)
_NUMERIC = re.compile(r"(?<![\d/])2/(\d{1,2})(?:/(?:20)?21)?(?![\d/])")
DATE_RULE_FROM = {"document_default", "section_default", "missing", None}


def feb_dates(sentence: str) -> list[tuple[str, str]]:
    """Distinct February 2021 dates or ranges named in a sentence, as (start, end) ISO dates.
    "between Feb 14 and 17" and "Feb 14-19" are ranges; "Feb 15 and 17" is two days."""
    found, used = [], []

    def free(m):
        return not any(s <= m.start() < e for s, e in used)

    for m in _BETWEEN.finditer(sentence):
        found.append((int(m.group(1)), int(m.group(2))))
        used.append(m.span())
    for m in _LIST.finditer(sentence):
        if free(m):
            found += [(int(d), int(d)) for d in [m.group(1)] + re.findall(r"\d{1,2}", m.group(2))]
            used.append(m.span())
    for m in _RANGE.finditer(sentence):
        if free(m):
            found.append((int(m.group(1)), int(m.group(2))))
            used.append(m.span())
    for rx in (_DAY, _NUMERIC):
        for m in rx.finditer(sentence):
            if free(m):
                found.append((int(m.group(1)), int(m.group(1))))
    out = []
    for a, b in found:
        if 1 <= a <= b <= 28:
            iso = (f"2021-02-{a:02d}", f"2021-02-{b:02d}")
            if iso not in out:
                out.append(iso)
    return out


def apply_date_rule(claim: dict, chunk_text: str) -> dict:
    """Return a copy with the window set from the sentence's only date, when the window was a default."""
    src = (claim.get("scope_source") or {}).get("window")
    incomplete = not (claim.get("window_start") and claim.get("window_end"))
    if src not in DATE_RULE_FROM and not incomplete:
        return claim
    s = sentence_of(claim.get("quote", ""), chunk_text)
    dates = feb_dates(s) if s else []
    if len(dates) != 1:
        return claim
    w0, w1 = dates[0]
    out = dict(claim)
    out["window_start"], out["window_end"] = w0, w1
    out["scope_source"] = {**(claim.get("scope_source") or {}), "window": "date_rule"}
    out["date_rule_from"] = [claim.get("window_start"), claim.get("window_end")]
    return out


# ---------------------------------------------------------------- sections
@lru_cache(maxsize=16)
def outline(path: str) -> tuple[tuple[int, int, str], ...]:
    """(page, depth, title) for each PDF bookmark, in order. Empty if the PDF has none."""
    import pypdf
    if not str(path).lower().endswith(".pdf"):
        return ()
    r = pypdf.PdfReader(path)
    out = []

    def walk(items, depth):
        for x in items:
            if isinstance(x, list):
                walk(x, depth + 1)
            else:
                try:
                    out.append((r.get_destination_page_number(x) + 1, depth, re.sub(r"\d+F$", "", x.title).strip()))
                except Exception:
                    pass
    try:
        walk(r.outline, 0)
    except Exception:
        return ()
    return tuple(out)


def _path(ol, i) -> list[str]:
    """Heading path of outline entry i (its ancestors by depth, then itself)."""
    page, depth, title = ol[i]
    path = [title]
    for j in range(i - 1, -1, -1):
        if ol[j][1] < depth:
            path.insert(0, ol[j][2])
            depth = ol[j][1]
    return path


def sections_for(doc_id: str, page_start: int, page_end: int) -> list[dict]:
    """Outline sections overlapping the chunk's pages: the one active at page_start plus any starting inside."""
    ol = outline(documents()[doc_id]["path"])
    if not ol:
        return []
    idx = [i for i, (p, _, _) in enumerate(ol) if p <= page_start]
    active = idx[-1] if idx else 0
    ids = [active] + [i for i, (p, _, _) in enumerate(ol) if page_start < p <= page_end]
    out = []
    for i in ids:
        nxt = next((ol[j][0] for j in range(i + 1, len(ol))), None)
        out.append({"section_id": f"{doc_id}:s{i:03d}", "page": ol[i][0], "next_page": nxt,
                    "heading_path": _path(ol, i)})
    return out


def section_text(doc_id: str, sec: dict, words: int = 700) -> str:
    pages = page_texts(documents()[doc_id]["path"])
    p0 = sec["page"] - 1
    text = re.sub(r"\s+", " ", " ".join(pages[p0:p0 + 4]))
    title = sec["heading_path"][-1]
    key = re.sub(r"^[A-Za-z0-9ivx]+\.\s*", "", title)[:30]
    i = text.find(key) if key else -1
    return " ".join(text[max(i, 0):].split()[:words])


SECTION_CARD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["region", "window_start", "window_end", "as_of", "metric_default", "population", "quote"],
    "properties": {
        "region": {"type": ["string", "null"]},
        "window_start": {"type": ["string", "null"]},
        "window_end": {"type": ["string", "null"]},
        "as_of": {"type": ["string", "null"]},
        "metric_default": {"type": ["string", "null"]},
        "population": {"type": ["string", "null"]},
        "quote": {"type": ["string", "null"]},
    },
}


def _card_prompt() -> tuple[str, str]:
    md = (CONFIG / "extraction_prompt.md").read_text()
    blocks = re.findall(r"```\n(.*?)```", md, flags=re.S)
    return blocks[3].strip(), blocks[4].strip()


def section_card(llm, doc_id: str, sec: dict) -> dict:
    """LLM-written default scope for one section. Cached by the LLM's request cache."""
    from .extract import _card_values, _fill
    doc = documents()[doc_id]
    system, user = _card_prompt()
    text = section_text(doc_id, sec)
    vals = _card_values(doc) | {"{section_path}": " > ".join(sec["heading_path"]), "{section_text}": text}
    card = llm.json_call([{"role": "system", "content": system},
                          {"role": "user", "content": _fill(user, vals)}], SECTION_CARD_SCHEMA)
    from .common import quote_in
    return {"section": " > ".join(sec["heading_path"]), "pages": [sec["page"], sec["next_page"]],
            **{k: v for k, v in card.items() if k != "quote"},
            "quote_found": bool(card.get("quote")) and quote_in(card["quote"], text)}


def section_cards_for(llm, doc_id: str, page_start: int, page_end: int) -> list[dict]:
    return [section_card(llm, doc_id, s) for s in sections_for(doc_id, page_start, page_end)]
