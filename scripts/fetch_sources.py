"""Download the third-party documents that are not redistributed in this repository.

The news articles and the Enverus and UT Austin reports are copyrighted by their publishers, so git holds only their
citation (config/documents.yaml: url, publisher, date) and the short quotes the extractions cite. This script fetches
each one from its URL into data/sources/ (gitignored) and checks it against the sha256 in documents.yaml, which is the
copy the published results were built from.

    uv run python scripts/fetch_sources.py            # skips files that already exist
    uv run python scripts/fetch_sources.py --check    # only compare local copies with documents.yaml

News pages are saved as plain text with the same rule used for the published run: the page's <p> paragraphs of eight
words or more, outside script/style/nav/aside/footer/figcaption, under a TITLE/URL/PUBLISHED header, minus two pieces
of site furniture (social-link text in a title, a newsletter promo). Live pages change;
if the live page no longer matches, the Internet Archive's earliest 2021 capture is tried. A mismatch is reported, not
fatal: the pipeline refuses to rebuild chunk text from a copy that differs (kg/chunk.py: load_extractions).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "data" / "sources"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) causal-kg research script (public documents)"}
SKIP_TAGS = ("script", "style", "noscript", "figcaption", "aside", "nav", "footer")
# Site furniture removed by hand from the published KSAT copy, kept here as rules so the fetch reproduces it
TITLE_JUNK = re.compile(r"(?:facebook|twitter|instagram|youtube|rss)+$")
PROMO = re.compile(r"^Sign up for The Brief, our daily newsletter[^\n]*\n", re.M)   # the line, not its blank line


class Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ps, self.cur, self.skip, self.meta, self.title, self.in_title = [], None, 0, {}, "", False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP_TAGS:
            self.skip += 1
        if tag == "p" and not self.skip:
            self.cur = []
        if tag == "meta":
            k = a.get("property") or a.get("name") or a.get("itemprop")
            if k:
                self.meta[k] = a.get("content")
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1
        if tag == "p" and self.cur is not None:
            x = re.sub(r"\s+", " ", "".join(self.cur)).strip()
            if len(x.split()) >= 8:
                self.ps.append(x)
            self.cur = None
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.cur is not None and not self.skip:
            self.cur.append(data)
        if self.in_title:
            self.title += data


def html_to_text(html: str) -> str:
    p = Paragraphs()
    p.feed(html)
    m = p.meta
    pub = (m.get("article:published_time") or m.get("datePublished") or m.get("date") or m.get("pubdate")
           or m.get("parsely-pub-date"))
    title = TITLE_JUNK.sub("", p.title.strip())
    text = f"TITLE: {title}\nURL: {m.get('og:url')}\nPUBLISHED: {pub}\n\n" + "\n\n".join(p.ps) + "\n"
    return PROMO.sub("", text)


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def get(url: str) -> bytes:
    r = requests.get(url, headers=UA, timeout=120)
    r.raise_for_status()
    return r.content


def candidates(doc: dict) -> list[str]:
    return [doc["url"], f"https://web.archive.org/web/2021id_/{doc['url']}"]


def fetch(doc: dict, dest: Path) -> str:
    """Download one document to dest. Returns 'ok', 'changed' or 'failed'."""
    is_news = dest.suffix == ".txt"
    data = None
    for url in candidates(doc):
        try:
            raw = get(url)
        except requests.RequestException as e:
            print(f"  {url}: {e}")
            continue
        data = html_to_text(raw.decode("utf-8", errors="replace")).encode("utf-8") if is_news else raw
        if sha256(data) == doc["sha256"]:
            break
    if data is None:
        return "failed"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return "ok" if sha256(data) == doc["sha256"] else "changed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="only compare local copies with documents.yaml")
    ap.add_argument("--out", type=Path, default=SOURCES, help="folder to write into (default data/sources)")
    a = ap.parse_args()
    docs = yaml.safe_load((ROOT / "config" / "documents.yaml").read_text())["documents"]
    bad = 0
    for d in (d for d in docs if d.get("redistribute") is False):
        dest = a.out / d["file"]
        if dest.exists():
            status = "ok" if sha256(dest.read_bytes()) == d["sha256"] else "changed"
        elif a.check:
            status = "missing"
        else:
            status = fetch(d, dest)
        bad += status != "ok"
        print(f"{status:8s} {d['id']:30s} {d['file']}")
    if bad:
        print(f"\n{bad} document(s) missing or different from the copy behind the published results. Results that "
              "do not read these documents are unaffected; re-running kg.post or kg.scope_eval needs exact copies.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
