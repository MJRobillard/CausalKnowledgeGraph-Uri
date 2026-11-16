"""Document -> page text -> paragraph-aligned chunks of ~1,200-1,500 words."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from .common import documents, extractable

MIN_WORDS, MAX_WORDS = 1200, 1500

# Slice for the scorecard: Feb 14 21:00 - Feb 16 12:00 Central.
SLICE_DOCS = ["ercot_board_2021_02_24", "ercot_causes_2021_04_27", "ferc_final_2021_11",
              "enverus_txoga_2021_04"]  # plus every doc with publisher_role: news
FERC_SLICE_RE = re.compile(r"Feb(?:ruary|\.)?\s+1[456]\b|31\.4\s*percent|44\.2\s*percent")


def page_texts(path: str) -> list[str]:
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        out = subprocess.run(["pdftotext", "-enc", "UTF-8", str(p), "-"],
                             capture_output=True, text=True, check=True).stdout
        pages = out.split("\f")
        if pages and not pages[-1].strip():
            pages = pages[:-1]
        return pages
    return [p.read_text(encoding="utf-8")]


def _is_heading(par: str) -> bool:
    words = par.split()
    return 0 < len(words) <= 12 and not par.rstrip().endswith((".", ",", ";", ":"))


def paragraphs(pages: list[str]) -> list[tuple[int, str]]:
    """(page_number starting at 1, paragraph) with lines inside a paragraph joined by spaces."""
    out = []
    for i, page in enumerate(pages, 1):
        for raw in re.split(r"\n\s*\n", page):
            par = " ".join(line.strip() for line in raw.splitlines() if line.strip())
            if par:
                out.append((i, par))
    return out


def _split_long(page: int, par: str) -> list[tuple[int, str]]:
    if len(par.split()) <= MAX_WORDS:
        return [(page, par)]
    sents = re.split(r"(?<=[.!?])\s+", par)
    pieces, cur = [], []
    for s in sents:
        if cur and len(" ".join(cur + [s]).split()) > MAX_WORDS:
            pieces.append((page, " ".join(cur)))
            cur = []
        cur.append(s)
    if cur:
        pieces.append((page, " ".join(cur)))
    return pieces


def chunk_doc(doc_id: str) -> list[dict]:
    doc = documents()[doc_id]
    if not extractable(doc):
        raise PermissionError(f"{doc_id} is held out or skipped; it must not be extracted")
    units = [u for page, par in paragraphs(page_texts(doc["path"])) for u in _split_long(page, par)]
    chunks, cur = [], []

    def words(us):
        return sum(len(t.split()) for _, t in us)

    def flush():
        if cur:
            chunks.append(list(cur))
            cur.clear()

    for page, par in units:
        n_cur, n_par = words(cur), len(par.split())
        if cur and (n_cur + n_par > MAX_WORDS or (_is_heading(par) and n_cur >= 1000)):
            flush()
        cur.append((page, par))
    flush()
    # fold a tiny tail into the previous chunk
    if len(chunks) > 1 and words(chunks[-1]) < 300 and words(chunks[-2]) + words(chunks[-1]) <= 1800:
        chunks[-2].extend(chunks.pop())
    return [{
        "doc_id": doc_id,
        "chunk_id": f"{doc_id}:c{i:03d}",
        "page_start": c[0][0],
        "page_end": c[-1][0],
        "n_words": words(c),
        "text": "\n\n".join(t for _, t in c),
    } for i, c in enumerate(chunks)]


def slice_doc_ids() -> list[str]:
    docs = documents()
    news = [d for d, v in docs.items() if v.get("publisher_role") == "news" and extractable(v)]
    return SLICE_DOCS + news


def select_slice(chunks: list[dict]) -> list[dict]:
    """FERC final is 316 pages; keep chunks about Feb 14-16 and the key cause shares."""
    return [c for c in chunks
            if c["doc_id"] != "ferc_final_2021_11" or FERC_SLICE_RE.search(c["text"])]


def halves(text: str) -> tuple[str, str]:
    """Split a chunk at its middle paragraph (a truncated chunk is re-extracted as halves 'a' and 'b')."""
    pars = text.split("\n\n")
    mid = max(1, len(pars) // 2)
    return "\n\n".join(pars[:mid]), "\n\n".join(pars[mid:])


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chunk_text(chunk_id: str, cache: dict) -> str:
    """Text of a chunk id such as 'doc:c003' or a half such as 'doc:c003ab', rebuilt from the local source."""
    doc_id, local = chunk_id.split(":", 1)
    if doc_id not in cache:
        cache[doc_id] = {c["chunk_id"]: c["text"] for c in chunk_doc(doc_id)}
    base = local.rstrip("ab")
    text = cache[doc_id][f"{doc_id}:{base}"]
    for side in local[len(base):]:
        text = halves(text)[side == "b"]
    return text


def load_extractions(path: Path) -> list[dict]:
    """A run's extractions.jsonl. Chunks of documents that may not be redistributed (documents.yaml:
    redistribute: false) are stored without their text; it is rebuilt from the local copy of the source
    (scripts/fetch_sources.py) and checked against the stored sha256."""
    recs, cache = [], {}
    for line in Path(path).open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("text") is None and r.get("text_sha256"):
            src = documents()[r["doc_id"]]["path"]
            if not Path(src).exists():
                raise FileNotFoundError(f"{src} is needed to rebuild {r['chunk_id']}: run scripts/fetch_sources.py")
            text = _chunk_text(r["chunk_id"], cache)
            if text_sha256(text) != r.pop("text_sha256"):
                raise ValueError(f"{r['chunk_id']}: the local copy of {src} differs from the one extracted "
                                 "(sha256 mismatch); see scripts/fetch_sources.py")
            r["text"] = text
        recs.append(r)
    return recs


def dump_extractions(path: Path, recs: list[dict]) -> None:
    """Write extractions.jsonl, dropping chunk text for documents that may not be redistributed."""
    docs = documents()
    with Path(path).open("w", encoding="utf-8") as f:
        for r in recs:
            if docs[r["doc_id"]].get("redistribute") is False and r.get("text") is not None:
                r = {**r, "text": None, "text_sha256": text_sha256(r["text"])}
            f.write(json.dumps(r) + "\n")


def build(doc_ids: list[str], slice_only: bool) -> list[dict]:
    out = []
    for d in doc_ids:
        cs = chunk_doc(d)
        out.extend(select_slice(cs) if slice_only else cs)
    return out


if __name__ == "__main__":
    import collections
    cs = build(slice_doc_ids(), slice_only=True)
    by = collections.Counter(c["doc_id"] for c in cs)
    print(dict(by), "total", len(cs), "words", sum(c["n_words"] for c in cs))
