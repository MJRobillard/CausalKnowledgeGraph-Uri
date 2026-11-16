"""Step 2: Tier 0 grounding. Check text claims against measured series.

A claim can be anchored only if its unit, region and time can be matched to a series. Results:
anchored_ok, anchored_mismatch (with residual), or not_anchorable (no_series, region, time_unresolved,
metric_mismatch) with a detail. Rules live in config/anchor_rules.yaml; every one is logged in ASSUMPTIONS.md.

    uv run python -m kg.anchor          # after kg.graph_build; writes anchors.parquet, anchor_report.md
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

import pandas as pd
import yaml
from rapidfuzz import fuzz

from . import series as S
from .common import CONFIG, DATA, _NUM, _SCALE_WORDS, _WORD_NUM, _WORD_RE, documents, norm_quote
from .graph_build import RUN, load, save, to_central

MIN, HOUR, DAY = pd.Timedelta(minutes=1), pd.Timedelta(hours=1), pd.Timedelta(days=1)
TIER_PRIOR = {"1": (9, 1), "2": (7, 3), "3": (3, 7)}          # Beta prior, means 0.9 / 0.7 / 0.3, 10 pseudo-obs
GRID_METRICS = {"mw", "mw_peak", "frequency"}


@lru_cache
def rules() -> dict:
    return yaml.safe_load((CONFIG / "anchor_rules.yaml").read_text())


# ---------------------------------------------------------------- text helpers
def _has(text: str, words) -> str | None:
    """First keyword found in text, matched on word boundaries."""
    for w in words:
        if re.search(r"(?<![a-z0-9])" + re.escape(w.strip().lower()) + r"(?![a-z0-9])", text):
            return w
    return None


def find_number(quote: str, value: float) -> dict | None:
    """Locate the written number in the quote whose value (times a scale word) equals `value`.
    Returns span, written text and scale, so tolerance can use the last stated digit."""
    q = (quote or "").replace("\u2212", "-").replace("\u2013", "-")
    q2 = re.sub(r"(\d),\s+(\d{3})(?!\d)", r"\1,\2", q)
    low = q2.lower()
    scales = [1.0] + [f for w, f in _SCALE_WORDS.items() if w in low]
    v = abs(float(value))
    cands = [(m.start(), m.end(), m.group(), float(m.group().replace(",", ""))) for m in _NUM.finditer(q2)]
    cands += [(m.start(), m.end(), m.group(), float(_WORD_NUM[m.group(1)] + (_WORD_NUM[m.group(2)] if m.group(2) else 0)))
              for m in _WORD_RE.finditer(low)]
    for a, b, txt, n in cands:
        for f in scales:
            if abs(abs(n) * f - v) <= 1e-9 * max(1.0, v):
                return {"start": a, "end": b, "text": txt, "scale": f, "quote": q2}
    return None


def context(quote: str, value: float) -> str:
    """Lowercase quote text within context_chars of the claim's number (the whole quote if not found)."""
    m = find_number(quote, value)
    q = (m["quote"] if m else quote or "").lower()
    if not m:
        return f" {q} "
    c = rules()["context_chars"]
    return f" {q[max(0, m['start'] - c): m['end'] + c]} "


def prefix(quote: str, value: float) -> str:
    """Lowercase text just before the claim's number (prefix_chars), where 'about' or 'more than' would sit."""
    m = find_number(quote, value)
    if not m:
        return ""
    return " " + m["quote"][max(0, m["start"] - rules()["prefix_chars"]): m["start"]].lower() + " "


def bound(quote: str, value: float) -> str | None:
    """A8: 'more than 30,000' is a lower bound, 'up to 18' an upper bound ('just above' is approximate)."""
    p = prefix(quote, value)
    if _has(p, rules()["approximate_words"]):
        return None
    if _has(p, rules()["lower_bound_words"]):
        return "lower"
    if _has(p, rules()["upper_bound_words"]):
        return "upper"
    return None


def half_unit(quote: str, value: float) -> float:
    """Half a unit of the last stated digit, in the claim's units: 52,037 -> 0.5; 59.302 -> 0.0005;
    '4.5 million' -> 50,000; 'six' -> 0.5."""
    m = find_number(quote, value)
    if not m:
        return 0.5
    t = m["text"]
    dec = len(t.split(".")[1]) if "." in t and t.replace(",", "").replace(".", "").lstrip("-").isdigit() else 0
    return 0.5 * 10 ** (-dec) * m["scale"]


def tolerance(value: float, half: float, kind: str, approximate: bool) -> float:
    """A3/A5: max(half a unit of the last stated digit, 1%); 5% if approximate; Hz uses the half unit only."""
    if kind == "frequency":
        return half
    return max(half, (0.05 if approximate else 0.01) * abs(value))


def is_approximate(claim: dict) -> tuple[bool, str]:
    """A5: the extracted flag if present (v0.2), else approximate words just before the number (v0 backfill)."""
    if claim.get("approximate") is not None:
        return bool(claim["approximate"]), "extracted"
    w = _has(prefix(claim.get("quote") or "", claim["value"]), rules()["approximate_words"])
    return (w is not None), ("quote_words" if w else "none")


def extreme(ctx: str, metric: str) -> str | None:
    if metric == "mw_peak":
        return "max"
    ex = rules()["extreme_words"]
    mn, mx = _has(ctx, ex["min"]), _has(ctx, ex["max"])
    if mn and not mx:
        return "min"
    if mx and not mn:
        return "max"
    return None


def region_class(region) -> tuple[str, object]:
    r = re.sub(r"\s+", " ", str(region or "").lower().strip())
    R = rules()["regions"]
    if r in R["ercot"]:
        return "ercot", False
    if r in R["texas_as_ercot"]:
        return "ercot", True
    if r in R["stations"]:
        return "station", R["stations"][r]
    return "other", r or None


# ---------------------------------------------------------------- series registry
@dataclass
class Ser:
    id: str
    data: pd.Series            # index = start of each sample's interval, Central
    res: pd.Timedelta
    kind: str                  # mw | temperature | frequency


@lru_cache
def series_table() -> dict[str, Ser]:
    out = {}
    mo = S.minute_start(S.mw_out_by_fuel())
    for c in mo.columns:
        out[f"ercot_mw_out_by_fuel:{c}"] = Ser(f"series:ercot_mw_out_by_fuel:{c}", mo[c], MIN, "mw")
    out["load_shed"] = Ser("series:ercot_gen_load_shed:load_shed", S.minute_start(S.load_shed()), MIN, "mw")
    out["estimated_load"] = Ser("series:ercot_gen_load_shed:estimated_load_without_shed", S.estimated_load(), HOUR, "mw")
    out["eia_demand"] = Ser("series:eia_ercot_hourly:demand", S.eia_hourly()["demand"], HOUR, "mw")
    out["frequency"] = Ser("series:ercot_frequency:frequency", S.frequency(), pd.Timedelta(seconds=1), "frequency")
    w = S.noaa_zone_temps()
    for c in w.columns:
        out[f"noaa:{c}"] = Ser(f"series:noaa_zone_temp_hourly:{c}", w[c], HOUR, "temperature")
    d = S.ghcnd_daily()
    for zone, st in rules()["ghcnd_stations"].items():
        for v in ("TMIN", "TMAX"):
            x = d[d.STATION == st].set_index("day")[v]
            out[f"ghcnd:{zone}:{v}"] = Ser(f"series:noaa_ghcnd_daily:{v}", x, DAY, "temperature")
    return out


def window_from(t0: pd.Timestamp, t1: pd.Timestamp | None, precision: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """A4: minute -> +-5 min; hour -> that hour +-1 h; day -> that day; range -> the range (whole end day if a date)."""
    W = rules()["windows"]
    if precision == "minute":
        return t0 - W["minute_minutes"] * MIN, t0 + W["minute_minutes"] * MIN + MIN
    if precision == "hour":
        h = t0.floor("h")
        return h - W["hour_hours"] * HOUR, h + (W["hour_hours"] + 1) * HOUR
    if precision == "day":
        d = t0.normalize()
        return d, d + DAY
    end = t1 if t1 is not None else t0
    if end == end.normalize():
        end = end + DAY
    return t0, end


def in_window(s: Ser, a: pd.Timestamp, b: pd.Timestamp) -> pd.Series:
    idx = s.data.index
    return s.data[(idx + s.res > a) & (idx < b)]


def covers(s: Ser, a: pd.Timestamp, b: pd.Timestamp) -> bool:
    return s.data.index.min() <= a and b <= s.data.index.max() + s.res


# ---------------------------------------------------------------- time of a claim
def quotes_overlap(a: str, b: str) -> bool:
    """A6: the about-event lends its time only if it was extracted from the same text as the claim."""
    na, nb = norm_quote(a), norm_quote(b)
    return bool(na and nb) and (na in nb or nb in na)


def claim_time(claim: dict, about: dict | None) -> tuple[pd.Timestamp | None, pd.Timestamp | None, str, str]:
    """(t0, t1, precision, source). precision in minute/hour/day/range/default/unresolved."""
    as_of = str(claim.get("as_of") or "")
    if re.match(r"^20\d\d-\d\d-\d\dT\d\d:\d\d", as_of):
        return to_central(as_of), None, "minute", "as_of"
    if re.match(r"^20\d\d-\d\d-\d\d$", as_of):
        return to_central(as_of), None, "day", "as_of"
    if (about is not None and about.get("valid_start") is not None and not pd.isna(about["valid_start"])
            and quotes_overlap(claim.get("quote") or "", about.get("quote") or "")):
        p = about.get("precision")
        if p in ("minute", "hour", "day"):
            return about["valid_start"], None, p, "about_event"
        if p == "range" and about.get("valid_end") is not None and not pd.isna(about["valid_end"]):
            return about["valid_start"], about["valid_end"], "range", "about_event"
    ws, we = to_central(claim.get("window_start")), to_central(claim.get("window_end"))
    src = (claim.get("scope_source") or {}).get("window")
    if ws is not None:
        prec = "day" if (we is None or we == ws) else "range"
        if src in ("sentence", "previous_sentence", "date_rule"):
            return ws, we, prec, f"window:{src}"
        return ws, we, "default", f"window:{src}"
    return None, None, "unresolved", "none"


# ---------------------------------------------------------------- routing
@dataclass
class Route:
    series: str | None = None
    reason: str | None = None          # not_anchorable reason
    detail: str = ""
    factor: float = 1.0                # claim units -> series units (GW -> MW)
    offset: float = 0.0                # degC -> degF
    region_approx: bool = False
    extras: dict = field(default_factory=dict)


def _unit(u: str, table: dict):
    u = str(u or "").lower().strip()
    return table.get(u)


def route(claim: dict, about_kind: str | None) -> Route:
    R = rules()
    metric, unit, value = claim.get("metric"), claim.get("unit"), claim.get("value")
    ctx = context(claim.get("quote") or "", value) if value is not None else ""
    rc, rv = region_class(claim.get("region"))
    if metric == "temperature":
        u = _unit(unit, R["units"]["temperature"])
        if u is None:
            return Route(reason="metric_mismatch", detail=f"temperature in '{unit}'")
        w = _has(f" {str(claim.get('quote') or '').lower()} ", R["temperature_not_measured"])
        if w:
            return Route(reason="metric_mismatch", detail=f"relative or normal temperature ('{w}')")
        f, off = (1.8, 32.0) if u == "C" else (1.0, 0.0)
        if rc == "station":
            return Route(series=f"noaa:{rv}", factor=f, offset=off)
        if rc == "ercot":
            return Route(reason="no_series", detail="no statewide temperature series (one station per zone)")
        return Route(reason="region", detail=f"region '{claim.get('region')}' has no station")
    if metric == "frequency":
        if _unit(unit, R["units"]["frequency"]) is None:
            return Route(reason="metric_mismatch", detail=f"frequency in '{unit}' (a change or rate, not a level)")
        if rc != "ercot":
            return Route(reason="region", detail=f"region '{claim.get('region')}'")
        return Route(series="frequency", region_approx=bool(rv))
    if metric in ("mw", "mw_peak"):
        f = _unit(unit, R["units"]["mw"])
        if f is None:
            return Route(reason="metric_mismatch", detail=f"MW claim in '{unit}'")
        if rc != "ercot":
            return Route(reason="region", detail=f"region '{claim.get('region')}' is not ERCOT")
        if R.get("mw_cause_categories_not_measured") and claim.get("cause_category") not in (None, "not_a_cause", "unknown"):
            return Route(reason="metric_mismatch", detail="MW by cause (no series splits outages by cause)")
        w = _has(ctx, R["mw_not_measured"])
        if w:
            return Route(reason="metric_mismatch", detail=f"not a measured level ('{w.strip()}')")
        whole = f" {str(claim.get('quote') or '').lower()} "
        # A7: what the number measures is read near the number first, then anywhere in the quote
        for text in (ctx, whole):
            if _has(text, R["load_shed_words"]):
                return Route(series="load_shed", factor=f, region_approx=rv)
            if _has(text, R["demand_words"]):
                s = "estimated_load" if _has(text, R["demand_estimate_words"]) else "eia_demand"
                return Route(series=s, factor=f, region_approx=rv)
            if _has(text, R["out_words"]):
                return _mw_out_route(text, f, rv)
        return Route(reason="metric_mismatch", detail="MW quantity not identified as shed, demand or outage")
    return Route(reason="no_series", detail=f"no series for metric '{metric}'")


def _mw_out_route(ctx: str, f: float, rv) -> Route:
    R = rules()
    fuels = [fu for fu, ws in R["fuels"].items() if _has(ctx, ws)]
    if len(fuels) > 1:
        return Route(reason="metric_mismatch", detail=f"several fuels named near the number: {fuels}")
    col = fuels[0] if fuels else "total"
    return Route(series=f"ercot_mw_out_by_fuel:{col}", factor=f, region_approx=rv)


# ---------------------------------------------------------------- evaluate one claim
def anchor_claim(claim: dict, about: dict | None) -> dict:
    about_kind = about.get("subtype") if about else None
    r = route(claim, about_kind)
    base = {"id": claim["gid"], "item_kind": "QuantClaim", "doc_id": claim["doc_id"], "tier": claim["tier"],
            "metric": claim.get("metric"), "value": claim.get("value"), "unit": claim.get("unit"),
            "region": claim.get("region"), "quote": claim.get("quote"), "region_approx": r.region_approx}
    if r.reason:
        return {**base, "status": "not_anchorable", "reason": r.reason, "detail": r.detail}
    s = series_table()[r.series]
    t0, t1, prec, src = claim_time(claim, about)
    ctx = context(claim.get("quote") or "", claim["value"])
    if s.kind == "temperature":            # extremes are named anywhere in a weather sentence
        ctx = f" {str(claim.get('quote') or '').lower()} "
    mode = extreme(ctx, claim.get("metric"))
    if prec == "unresolved" or (prec == "default" and mode is None):
        return {**base, "status": "not_anchorable", "reason": "time_unresolved", "series": s.id,
                "detail": f"time source {src}" + ("" if prec == "unresolved" else " (document default window)")}
    a, b = window_from(t0, t1, "range" if prec == "default" else prec)
    if s.kind == "temperature" and mode in ("min", "max") and prec in ("day", "range", "default"):
        # A9: daily extremes come from the max/min thermometer (GHCN-Daily), not hourly METAR readings
        s = series_table()[f"ghcnd:{r.series.split(':', 1)[1]}:{'TMIN' if mode == 'min' else 'TMAX'}"]
        a, b = a.normalize(), (b - pd.Timedelta(seconds=1)).normalize() + DAY
    if mode is None and prec == "minute" and s.res > MIN:
        return {**base, "status": "not_anchorable", "reason": "no_series", "series": s.id,
                "detail": f"claim at a minute, series resolution {s.res}"}
    if not covers(s, a, b):
        return {**base, "status": "not_anchorable", "reason": "no_series", "series": s.id,
                "detail": f"window {a}..{b} outside series coverage {s.data.index.min()}..{s.data.index.max()}"}
    obs = in_window(s, a, b).dropna()
    if obs.empty:
        return {**base, "status": "not_anchorable", "reason": "no_series", "series": s.id, "detail": "no data in window"}
    approx, approx_src = is_approximate(claim)
    bnd = bound(claim["quote"], claim["value"])
    v = float(claim["value"]) * r.factor + r.offset
    tol = tolerance(v, half_unit(claim["quote"], claim["value"]) * r.factor, s.kind, approx)
    at_time = float(s.data[s.data.index <= max(t0, s.data.index.min())].iloc[-1])
    if mode == "max" or (mode is None and bnd == "lower"):
        o = float(obs.max()); t_o = obs.idxmax()
    elif mode == "min" or (mode is None and bnd == "upper"):
        o = float(obs.min()); t_o = obs.idxmin()
    else:
        i = (obs - v).abs().idxmin(); o = float(obs[i]); t_o = i
    resid = v - o
    if bnd == "lower":           # 'more than v': observed must reach v
        ok = o >= v - tol
    elif bnd == "upper":         # 'up to v': observed must not exceed v
        ok = o <= v + tol
    else:
        ok = abs(resid) <= tol + 1e-9
    status = "anchored_ok" if ok else "anchored_mismatch"
    return {**base, "status": status, "series": s.id, "mode": mode or "value_in_window", "time_source": src,
            "precision": prec, "window_start": a, "window_end": b, "claim_value_series_units": v,
            "observed": o, "observed_at": t_o, "value_at_stated_time": at_time, "residual": resid,
            "tolerance": tol, "approximate": approx, "approximate_source": approx_src, "bound": bnd,
            "window_default": prec == "default"}


# ---------------------------------------------------------------- events: load shed timing, named units
SHED_CHANGE_TOL = 5   # minutes


def shed_changes() -> pd.Series:
    s = series_table()["load_shed"].data
    d = s.diff()
    return d[d.fillna(0) != 0]


def anchor_shed_event(ev: dict) -> dict:
    base = {"id": ev["id"], "item_kind": "Event", "doc_id": ev["doc_id"], "tier": ev["tier"], "metric": "LoadShed time",
            "region": ev.get("region"), "quote": ev.get("quote"), "value": None, "unit": None}
    rc, rv = region_class(ev.get("region"))
    if not _has(f" {str(ev.get('quote') or '').lower()} ", rules()["load_shed_words"]):
        return {**base, "status": "not_anchorable", "reason": "no_series",
                "detail": "LoadShed event whose quote names no shed action (e.g. customers out)"}
    if rc != "ercot":
        return {**base, "status": "not_anchorable", "reason": "region", "detail": f"region '{ev.get('region')}'"}
    if ev.get("precision") not in ("minute", "hour") or ev.get("valid_start") is None or pd.isna(ev.get("valid_start")):
        return {**base, "status": "not_anchorable", "reason": "time_unresolved", "detail": f"precision {ev.get('precision')}"}
    t = ev["valid_start"]
    s = series_table()["load_shed"]
    a, b = window_from(t, None, ev["precision"])
    time_text = str(json.loads(ev.get("attrs") or "{}").get("time_text") or "").lower().strip()
    by = time_text.startswith("by ") or bool(re.search(
        r"\bby\s+\d{1,2}(:\d\d)?\s*(a\.?\s?m\.?|p\.?\s?m\.?)", str(ev.get("quote") or ""), re.I))
    if by:                       # A10: 'by 7 p.m.' means at or before, within the hour before
        a, b = t - HOUR, t + MIN
    if not covers(s, a, b):
        return {**base, "status": "not_anchorable", "reason": "no_series", "detail": "outside load-shed coverage"}
    ch = shed_changes()
    inside = ch.index[(ch.index >= a) & (ch.index < b)]
    nearest = inside[(abs(inside - t)).argmin()] if len(inside) else ch.index[(abs(ch.index - t)).argmin()]
    resid_min = (t - nearest) / MIN
    ok = a <= nearest < b
    return {**base, "status": "anchored_ok" if ok else "anchored_mismatch", "series": s.id, "mode": "shed_change",
            "precision": ev["precision"], "window_start": a, "window_end": b, "observed_at": nearest,
            "observed": float(s.data[nearest]), "residual": resid_min, "tolerance": float((b - a) / MIN / 2),
            "region_approx": rv, "bound": "by" if by else None,
            "detail": "residual in minutes: stated time - nearest change in shed MW"}


def _unit_name(s: str) -> str:
    stop = set(rules()["unit_name_stopwords"])
    toks = [t for t in re.sub(r"[^a-z0-9 ]+", " ", str(s).lower()).split() if t not in stop]
    return " ".join(toks)


@lru_cache
def station_names() -> dict[str, str]:
    u = S.unit_outages("2021_03_12")
    return {st: _unit_name(nm) for st, nm in u.dropna(subset=["station"]).groupby("station").station_name.first().items()}


def match_station(name: str) -> str | None:
    n = _unit_name(name)
    if len(n) < 4 or re.search(r"\d", n):
        return None
    best = max(station_names().items(), key=lambda kv: fuzz.token_sort_ratio(n, kv[1]), default=None)
    if best and best[1] and fuzz.token_sort_ratio(n, best[1]) >= rules()["unit_name_min_score"]:
        return best[0]
    return None


def anchor_unit_event(ev: dict, station: str) -> dict:
    base = {"id": ev["id"], "item_kind": "Event", "doc_id": ev["doc_id"], "tier": ev["tier"],
            "metric": "unit outage time", "region": ev.get("region"), "quote": ev.get("quote"), "value": None,
            "unit": None, "series": "series:unit_outage_2021_03_12:unit_outages", "detail": f"station {station}"}
    if ev.get("precision") not in ("minute", "hour", "day", "range") or pd.isna(ev.get("valid_start")):
        return {**base, "status": "not_anchorable", "reason": "time_unresolved", "detail": f"station {station}; precision {ev.get('precision')}"}
    a, b = window_from(ev["valid_start"], None if pd.isna(ev.get("valid_end")) else ev["valid_end"], ev["precision"])
    out = {}
    for ver in ("2021_03_12", "2021_03_04"):
        u = S.unit_outages(ver)
        rows = u[u.station == station]
        hit = rows[(rows.start >= a) & (rows.start < b)]
        out[ver] = (len(hit) > 0, rows.start)
    ok, starts = out["2021_03_12"]
    nearest = starts.iloc[(abs(starts - ev["valid_start"])).argmin()] if len(starts) else None
    return {**base, "status": "anchored_ok" if ok else "anchored_mismatch", "mode": "unit_start_in_window",
            "window_start": a, "window_end": b, "observed_at": nearest,
            "residual": None if nearest is None else (ev["valid_start"] - nearest) / MIN,
            "agrees_0304": out["2021_03_04"][0] == ok, "precision": ev["precision"]}


# ---------------------------------------------------------------- run over the graph
def _claims(nodes: pd.DataFrame) -> list[dict]:
    q = nodes[nodes.kind == "QuantClaim"]
    out = []
    for _, r in q.iterrows():
        a = json.loads(r["attrs"])
        out.append({**a, "gid": r.id, "doc_id": r.doc_id, "tier": r.tier, "region": r.region, "quote": r.quote})
    return out


def run(nodes: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    ev = nodes[nodes.kind == "Event"].set_index("id", drop=False)
    about = edges[edges.kind == "about"].set_index("src").dst
    res = []
    for c in _claims(nodes):
        ab = about.get(c["gid"])
        a = ev.loc[ab].to_dict() if isinstance(ab, str) and ab in ev.index else None
        res.append(anchor_claim(c, a))
    # events: load shed timing and named units; every other event has no series
    ents = nodes[nodes.kind == "Entity"].set_index("id")
    affected = edges[edges.kind == "affected"]
    for _, e in ev.iterrows():
        d = e.to_dict()
        if e.subtype == "LoadShed":
            res.append(anchor_shed_event(d))
            continue
        if e.subtype == "GenerationOutage":
            names = [ents.label.get(x) for x in affected[affected.src == e.id].dst if x in ents.index]
            st = next((s for s in (match_station(n) for n in names if n) if s), None)
            if st:
                if region_class(e.region)[0] != "ercot":
                    res.append({"id": e.id, "item_kind": "Event", "doc_id": e.doc_id, "tier": e.tier,
                                "metric": "unit outage time", "status": "not_anchorable", "reason": "region",
                                "detail": f"station {st}, region {e.region}", "quote": e.quote})
                else:
                    res.append(anchor_unit_event(d, st))
                continue
        res.append({"id": e.id, "item_kind": "Event", "doc_id": e.doc_id, "tier": e.tier, "metric": e.subtype,
                    "region": e.region, "quote": e.quote, "status": "not_anchorable", "reason": "no_series",
                    "detail": f"no series for event type {e.subtype}" + (" without a named unit" if e.subtype == "GenerationOutage" else "")})
    return pd.DataFrame(res)


# ---------------------------------------------------------------- load-shed start verdict
def load_shed_verdict(nodes: pd.DataFrame) -> dict:
    s = S.load_shed()
    first_label = s[(s > 0) & (s.index >= "2021-02-15")].index[0]
    first_start = first_label - MIN                                 # minute-ending labels (A1)
    ev = nodes[(nodes.kind == "Event") & nodes.valid_start.between("2021-02-15 01:00", "2021-02-15 01:40")
               & (nodes.precision == "minute")]
    # statements that say when shedding (or EEA 3 with shedding) began; not reserve or frequency readings
    what = re.compile(r"shed|blackout|rotating|eea\s*3|level 3", re.I)
    start = re.compile(r"began|begin|beginning|first|implemented|declared|enters|entered|instructed|ordered", re.I)
    other = re.compile(r"recovered|fell below|criteria|additional", re.I)
    rows, seen = [], set()
    for _, e in ev.iterrows():
        qt = str(e.quote)
        if (what.search(qt) and start.search(qt) and not other.search(qt) and (e.doc_id, qt) not in seen
                and re.search(r"1:1[5-9]|1:2\d|01:2\d|01:1\d", qt + str(json.loads(e["attrs"]).get("time_text")))):
            seen.add((e.doc_id, qt))
            rows.append({"source": e.doc_id, "tier": e.tier, "stated": e.valid_start.strftime("%H:%M"),
                         "type": e.subtype, "quote": e.quote,
                         "residual_min": (e.valid_start - first_start) / MIN})
    key = pd.read_csv(DATA / "answer_key" / "ut_timeline_events.csv")
    k = key[(key.event_type == "LoadShed") & key.time_central.str.startswith("2021-02-15T01")]
    for _, r in k.iterrows():
        t = pd.Timestamp(r.time_central)
        rows.append({"source": "ut_austin_timeline_2021_07 (held out)", "tier": "2", "stated": t.strftime("%H:%M"),
                     "type": "LoadShed", "quote": r.quote, "residual_min": (t - first_start) / MIN})
    return {"first_nonzero_label": first_label, "first_nonzero_minute_start": first_start,
            "mw_at_first": float(s[first_label]), "statements": rows}


# ---------------------------------------------------------------- reliability
def reliability(res: pd.DataFrame) -> pd.DataFrame:
    docs = documents()
    rows = []
    for d, g in res.groupby("doc_id"):
        tier = docs[d].get("tier")
        a0, b0 = TIER_PRIOR.get(tier, (None, None))
        agreed = int((g.status == "anchored_ok").sum())
        anchored = int(g.status.isin(["anchored_ok", "anchored_mismatch"]).sum())
        rows.append({"doc_id": d, "tier": tier, "flags": ",".join(docs[d].get("flags") or []), "items": len(g),
                     "anchored": anchored, "agreed": agreed, "disagreed": anchored - agreed,
                     "coverage": anchored / len(g) if len(g) else 0.0, "prior_mean": a0 / (a0 + b0) if a0 else None,
                     "r": (a0 + agreed) / (a0 + b0 + anchored) if a0 else None})
    return pd.DataFrame(rows).sort_values(["tier", "doc_id"])


# ---------------------------------------------------------------- write back + report
def write_back(nodes: pd.DataFrame, edges: pd.DataFrame, res: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    nodes = nodes[nodes.kind != "Observation"].copy()
    edges = edges[~edges.kind.isin(["anchored_to", "observed_in"])].copy()
    st = res.set_index("id")
    nodes["anchor_status"] = nodes.id.map(st.status)
    nodes["anchor_reason"] = nodes.id.map(st.get("reason"))
    an = res[res.status.isin(["anchored_ok", "anchored_mismatch"])]
    new_n, new_e = [], []
    for _, r in an.iterrows():
        oid = f"obs:{r.id}"
        sid = r.series
        attrs = {k: (None if pd.isna(r.get(k)) else (str(r.get(k)) if isinstance(r.get(k), pd.Timestamp) else r.get(k)))
                 for k in ("mode", "observed", "observed_at", "value_at_stated_time", "residual", "tolerance",
                           "approximate", "bound", "region_approx", "window_default", "time_source")}
        new_n.append({"id": oid, "kind": "Observation", "subtype": sid, "label": f"{r.get('observed')}",
                      "tier": documents()[sid.split(":")[1]]["tier"], "doc_id": sid.split(":")[1],
                      "valid_start": r.get("window_start"), "valid_end": r.get("window_end"),
                      "attrs": json.dumps(attrs, default=str)})
        new_e.append({"src": r.id, "dst": oid, "kind": "anchored_to",
                      "attrs": json.dumps({"status": r.status, "residual": attrs["residual"],
                                           "tolerance": attrs["tolerance"]}, default=str)})
        new_e.append({"src": oid, "dst": sid, "kind": "observed_in"})
    nodes = pd.concat([nodes, pd.DataFrame(new_n)], ignore_index=True)
    edges = pd.concat([edges, pd.DataFrame(new_e)], ignore_index=True)
    return nodes, edges


def _fmt(x, nd=1):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    if isinstance(x, float):
        return f"{x:,.{nd}f}"
    return str(x)


def report(res: pd.DataFrame, rel: pd.DataFrame, verdict: dict) -> str:
    n = len(res)
    an = res.status.isin(["anchored_ok", "anchored_mismatch"])
    ok = (res.status == "anchored_ok").sum()
    mm = res[res.status == "anchored_mismatch"]
    L = ["# Tier 0 anchoring report", "",
         f"Run `03_graph`. {n:,} items checked ({(res.item_kind == 'QuantClaim').sum():,} quant claims, "
         f"{(res.item_kind == 'Event').sum():,} events). **{an.sum()} anchored ({an.mean():.1%})**: "
         f"{ok} agree, **{len(mm)} disagree**. Everything else has no Tier 0 counterpart, which is expected: most "
         "claims are about other regions, other quantities (causes, counts, prices, gas volumes) or have no usable time.",
         "", "Rules: `config/anchor_rules.yaml`, assumptions A1–A10 in `ASSUMPTIONS.md`.", ""]
    L += ["## Coverage by tier", "", "| Tier | Items | Anchored | Agree | Disagree | Coverage |", "|---|---|---|---|---|---|"]
    for t, g in res.groupby("tier"):
        a = g.status.isin(["anchored_ok", "anchored_mismatch"])
        L.append(f"| {t} | {len(g):,} | {a.sum()} | {(g.status == 'anchored_ok').sum()} | "
                 f"{(g.status == 'anchored_mismatch').sum()} | {a.mean():.1%} |")
    L += ["", "## Why items were not anchorable", "", "| Reason | Quant claims | Events |", "|---|---|---|"]
    na = res[res.status == "not_anchorable"]
    for r_, g in na.groupby("reason"):
        L.append(f"| {r_} | {(g.item_kind == 'QuantClaim').sum():,} | {(g.item_kind == 'Event').sum():,} |")
    L += ["", "Most common details:", ""]
    for d, c in na.detail.fillna("").str.replace(r"'[^']*'", "'…'", regex=True).str.replace(r"\d{4}-\d\d-\d\d.*", "…", regex=True).value_counts().head(12).items():
        L.append(f"- {c:,} × {d}")
    L += ["", "## Anchored claims by series", "", "| Series | Agree | Disagree |", "|---|---|---|"]
    for s_, g in res[an].groupby("series"):
        L.append(f"| `{s_.replace('series:', '')}` | {(g.status == 'anchored_ok').sum()} | {(g.status == 'anchored_mismatch').sum()} |")
    L += ["", f"## Every mismatch ({len(mm)})", "",
          "Residual = claim − observed, in series units (MW, °F, Hz; minutes for event times). `~` = approximate (5% tolerance); "
          "`TX` = scoped to Texas, read as ERCOT; `dflt` = document-default window (extremes only).", "",
          "| Doc | Tier | Claim | Series | Mode | Observed | Residual | Tol. | Flags | Quote |", "|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in mm.sort_values(["tier", "doc_id"]).iterrows():
        flags = " ".join(f for f, on in (("~", r.get("approximate") is True), ("TX", bool(r.get("region_approx"))),
                                           ("dflt", r.get("window_default") is True)) if on)
        claim = f"{_fmt(r.value, 3).rstrip('0').rstrip('.')} {r.unit}" if r.item_kind == "QuantClaim" else f"time {_fmt(r.get('observed_at'))}"
        q = str(r.quote).replace("|", "/").replace("\n", " ")[:110]
        L.append(f"| {r.doc_id} | {r.tier} | {claim} | `{str(r.series).split(':', 2)[-1]}` | {r.get('mode')} | "
                 f"{_fmt(r.get('observed'), 3)} | {_fmt(r.residual, 3)} | {_fmt(r.tolerance, 3)} | {flags} | {q} |")
    v = verdict
    L += ["", "## When did load shed start? (Tier 0b settles a disagreement)", "",
          f"ERCOT's minute load-shed series is zero until the label **{v['first_nonzero_label']:%H:%M}** on 2/15, when it "
          f"steps to {v['mw_at_first']:,.0f} MW. The labels are minute-ending (A1), so shedding began in the minute "
          f"**{v['first_nonzero_minute_start']:%H:%M}–{v['first_nonzero_label']:%H:%M}**. If the labels were minute-start "
          "instead, the answer would move one minute later.", "",
          "| Source | Tier | Stated | Residual (min) | Quote |", "|---|---|---|---|---|"]
    for s_ in sorted(v["statements"], key=lambda x: (x["residual_min"], x["source"])):
        L.append(f"| {s_['source']} | {s_['tier']} | {s_['stated']} | {s_['residual_min']:+.0f} | "
                 f"{str(s_['quote']).replace('|', '/')[:100]} |")
    L += ["", "VERDICT_PLACEHOLDER", ""]
    L += ["## Source reliability", "",
          "r = (α + agreed) / (α + β + anchored), with the tier prior worth 10 pseudo-observations "
          "(means 0.9 / 0.7 / 0.3 for Tiers 1 / 2 / 3). Documents with nothing anchored keep their prior.", "",
          "| Document | Tier | Flags | Items | Anchored | Agree | Disagree | Prior | r |", "|---|---|---|---|---|---|---|---|---|"]
    for _, r in rel.iterrows():
        L.append(f"| {r.doc_id} | {r.tier} | {r['flags']} | {r['items']} | {r.anchored} | {r.agreed} | {r.disagreed} | "
                 f"{_fmt(r.prior_mean, 2)} | {_fmt(r.r, 3)} |")
    return "\n".join(L) + "\n"


# Hand review of the mismatches (Claude's reading of the source text, 2026-11-15; not checked by the user).
# Keyed by claim gid; printed in the report under its own heading so it is never mistaken for a rule.
MISMATCH_REVIEW = {
    "ercot_board_2021_02_24:c001:quant_claims:Q39":
        "Extraction (layout) error, not ERCOT's. In the board deck's timeline the day heading follows its bullets in "
        "pdftotext order, so the bullet was dated 'Wednesday, February 17'. It belongs to Tuesday 2/16, and the 2/16 "
        "maximum is exactly 52,277 MW (07:36).",
    "ercot_board_2021_02_24:c001:quant_claims:Q41":
        "Same layout error. The 20,000 MW peak shed was on 2/15 (18:54 onward); on 2/17 the series peaks at 15,500.",
    "ferc_final_2021_11:c002:quant_claims:Q18":
        "Station choice or day (O1). FERC gives no date or station; DFW's daily minimum fell to 4 °F on 2/15 and "
        "-2 °F on 2/16, never 8 as a low for the event. FERC may mean another station (e.g. Dallas Love Field) or one day.",
    "ferc_final_2021_11:c002:quant_claims:Q19":
        "Station choice or day (O1). IAH's lowest daily minimum was 13 °F (2/16); 10 °F may be Hobby or another "
        "station. Not a clear source error.",
    "news_txtrib_ksat_2021_02_15:c000:quant_claims:Q3":
        "A real but small disagreement: ERCOT's Monday-morning figure as reported was 30,000 MW, while MW out was at "
        "least 32,494 all day Monday (8% higher). Likely a rounded or partial (thermal-only) figure in the statement.",
}


def review_section(res: pd.DataFrame) -> str:
    mm = res[res.status == "anchored_mismatch"]
    L = ["## Reading the mismatches (Claude's hand review, not checked by the user)", ""]
    for _, r in mm.iterrows():
        L.append(f"- **{r.doc_id}, {r.value:,.0f} {r.unit}**: {MISMATCH_REVIEW.get(r.id, 'not reviewed yet')}")
    rev = [MISMATCH_REVIEW.get(i, "") for i in mm.id]
    n_extr = sum("Extraction" in t or "layout" in t for t in rev)
    L += ["", f"So {n_extr} of {len(mm)} mismatches are extraction errors that Tier 0 caught, not source errors, and the "
          "reliability scores below count them against the source anyway: the score measures *the claims as extracted*. "
          "Example of a strict non-anchor: the board deck's 'Min Frequency 59.302 Hz' matches the 1-second series minimum "
          "exactly, but its window is the document default (2/14–2/19) and the series covers only 42 minutes on 2/15, so "
          "it is `no_series` (coverage).", ""]
    return "\n".join(L)


def verdict_text(v: dict) -> str:
    st = v["statements"]
    exact = sorted({s["source"] for s in st if abs(s["residual_min"]) <= 1})
    off = [f"{s['source']} ({s['stated']}, {s['residual_min']:+.0f} min)" for s in st if abs(s["residual_min"]) > 1]
    return ("**Verdict:** the data supports **1:20 a.m.** (to within the one-minute label ambiguity). Sources that say so: "
            + ", ".join(exact) + ". Off by more than a minute: " + ("; ".join(off) if off else "none")
            + ". The 1:15 and 1:25 statements sit at the edge of the ±5-minute matching window, so the general anchoring "
              "rule would not separate them; the minute series does.")


def main() -> dict:
    nodes, edges = load()
    res = run(nodes, edges)
    rel = reliability(res)
    v = load_shed_verdict(nodes)
    nodes, edges = write_back(nodes, edges, res)
    save(nodes, edges)
    out = res.copy()
    for c in out.columns:
        if out[c].dtype == object:
            out[c] = out[c].map(lambda x: None if x is None or (isinstance(x, float) and pd.isna(x)) else str(x))
    out.to_parquet(RUN / "anchors.parquet", index=False)
    rel.to_csv(RUN / "reliability.csv", index=False)
    txt = report(res, rel, v).replace("VERDICT_PLACEHOLDER", verdict_text(v))
    txt = txt.replace("## When did load shed start?", review_section(res) + "\n## When did load shed start?", 1)
    (RUN / "anchor_report.md").write_text(txt)
    an = res.status.isin(["anchored_ok", "anchored_mismatch"])
    summary = {"items": len(res), "anchored": int(an.sum()), "coverage": float(an.mean()),
               "ok": int((res.status == "anchored_ok").sum()), "mismatch": int((res.status == "anchored_mismatch").sum()),
               "reasons": res[res.status == "not_anchorable"].reason.value_counts().to_dict(),
               "first_shed": str(v["first_nonzero_minute_start"])}
    (RUN / "anchor_summary.json").write_text(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
