"""Paths, config loading, and the deterministic trust rules (tier, claim_status)."""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
DATA = ROOT / "data"
SOURCES = DATA / "sources"        # documents in config/documents.yaml; `file:` paths are relative to this
EXTERNAL = DATA / "external"      # third-party datasets (ERCOT-derived TAMU tables, NOAA, EIA)
PROCESSED = DATA / "processed"    # built by this repo's code (fleet match, spatial layer, chart reads)
RAW = DATA / "raw"                # public downloads (gitignored; scripts/fetch_spatial.py)
RESULTS = ROOT / "results"        # one folder per pipeline stage
TAMU_REPO = ROOT / "2021TXPowerOutage"   # git clone of the TAMU repo (gitignored), needed for the grid model


@lru_cache
def ontology() -> dict:
    return yaml.safe_load((CONFIG / "ontology.yaml").read_text())


@lru_cache
def schema() -> dict:
    return json.loads((CONFIG / "extraction_schema.json").read_text())


def _matches(cond: dict, doc: dict) -> bool:
    for key, want in cond.items():
        if key not in doc:
            return False
        have = doc[key]
        if isinstance(want, list):
            if have not in want:
                return False
        elif have != want:
            return False
    return True


def assign_tier(doc: dict) -> str | None:
    """First matching rule in ontology.tier_rules wins. None if no rule matches."""
    for rule in ontology()["tier_rules"]:
        if _matches(rule["if"], doc):
            return rule["then"]["tier"]
    return None


def claim_status(doc: dict) -> str:
    rules = ontology()["claim_status_rules"]
    return rules.get(doc.get("doc_type"), rules["default"])


@lru_cache
def documents() -> dict[str, dict]:
    """documents.yaml keyed by id, with tier and claim_status attached in code."""
    docs = yaml.safe_load((CONFIG / "documents.yaml").read_text())["documents"]
    out = {}
    for d in docs:
        d = dict(d)
        d["path"] = str(SOURCES / d["file"])
        d["tier"] = assign_tier(d)
        d["claim_status"] = claim_status(d)
        d.setdefault("flags", [])
        d.setdefault("scope", {})
        out[d["id"]] = d
    return out


def extractable(doc: dict) -> bool:
    """Hard guard: held-out answer keys, skipped duplicates and measured datasets never reach the model."""
    return not (doc.get("held_out") or doc.get("skip") or doc.get("doc_type") == "chart_read"
                or (doc.get("doc_type") == "dataset" and doc.get("measured")))


_DASHES = str.maketrans({"\u2212": "-", "\u2013": "-"})   # minus sign, en dash


def norm_quote(s: str) -> str:
    """Lowercase alphanumerics and '%', plus number punctuation, after NFKC (expands ligatures).

    Robust to line-break hyphenation ("genera- tion"), whitespace, curly quotes and other punctuation,
    but keeps what changes a number: '.' and ',' between digits ("31.4" != "314", "2, 300" == "2,300"),
    '-' before a digit ("-2", "52-60"), and '%'.
    """
    s = unicodedata.normalize("NFKC", s or "").lower().translate(_DASHES)
    s = re.sub(r"([a-z])-\s+([a-z])", r"\1\2", s)      # hyphen at a line break inside a word
    s = re.sub(r"\s+", "", s)
    out = []
    for i, ch in enumerate(s):
        prev_d = i > 0 and s[i - 1].isdigit()
        next_d = i + 1 < len(s) and s[i + 1].isdigit()
        if ("0" <= ch <= "9") or ("a" <= ch <= "z") or ch == "%":
            out.append(ch)
        elif ch in ".," and prev_d and next_d:
            out.append(ch)
        elif ch == "-" and next_d:
            out.append(ch)
    return "".join(out)


def quote_in(quote: str, text: str) -> bool:
    q = norm_quote(quote)
    return bool(q) and q in norm_quote(text)


_SCALE_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9}
_UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
          "sixteen seventeen eighteen nineteen").split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90, "hundred": 100}
_ORDINALS = ("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth "
             "fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth").split()
_WORD_NUM = {w: i for i, w in enumerate(_UNITS)} | _TENS | {w: i + 1 for i, w in enumerate(_ORDINALS)}
_WORD_RE = re.compile(r"\b(" + "|".join(sorted(_WORD_NUM, key=len, reverse=True)) + r")(?:-(" +
                      "|".join(_UNITS[1:10]) + r"))?\b")
_NUM = re.compile(r"(?<![\d.])-?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    """Numbers written in a quote: '20,000' -> 20000, '2, 300' -> 2300, '-2' -> -2, '31.4%' -> 31.4,
    and number words and ordinals up to the twenties ('six' -> 6, 'twenty-five' -> 25, 'third' -> 3)."""
    t = unicodedata.normalize("NFKC", text or "").translate(_DASHES)
    t = re.sub(r"(\d),\s+(\d{3})(?!\d)", r"\1,\2", t)
    out = []
    for m in _NUM.finditer(t):
        try:
            out.append(float(m.group().replace(",", "")))
        except ValueError:
            pass
    for m in _WORD_RE.finditer(t.lower()):      # "six degrees", "twenty-five", "the third largest"
        out.append(float(_WORD_NUM[m.group(1)] + (_WORD_NUM[m.group(2)] if m.group(2) else 0)))
    return out


def value_in_quote(claim: dict) -> bool:
    """X13: a quant claim's value must be written in its quote, in some form.

    Accepts the number as written (31.4, 31.4%, 31.4 percent, 20,000), its absolute value
    ("-2" vs "2 below zero"), and the number times a scale word in the quote ("4.5 million").
    For a range, value_low or value_high may stand in for the midpoint."""
    targets = [claim.get(k) for k in ("value", "value_low", "value_high")]
    targets = [abs(float(v)) for v in targets if isinstance(v, (int, float))]
    if not targets:
        return False
    quote = claim.get("quote") or ""
    scales = [1.0] + [f for w, f in _SCALE_WORDS.items() if w in quote.lower()]
    for n in numbers_in(quote):
        for f in scales:
            for v in targets:
                if abs(abs(n) * f - v) <= 1e-9 * max(1.0, v):
                    return True
    return False
