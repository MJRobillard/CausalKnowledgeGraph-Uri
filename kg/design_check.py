"""Design check: before a hypothesis test, list what else could move the effect inside the test window.

Threats (rules in config/proxies.yaml -> design_check):
  competing_cause   other hypothesis families with the same effect whose cause events fall in the window
  confounder_events events in the graph of types that also move the effect, inside the window (ERCOT scope)
  named_confounder  hand-listed events (South Texas Project trip, frequency trips, load shed in progress)
  pretrend          the effect was already moving before the cause's onset (>= pretrend_threshold_share of its
                    storm range within pretrend_hours) - the check that flags the load-shed event study

Pure functions of the family, the window, the graph tables and a series; nothing is fitted.
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd
import yaml

from .common import CONFIG

STORM = (pd.Timestamp("2021-02-14 00:00"), pd.Timestamp("2021-02-20 00:00"))


@lru_cache
def rules() -> dict:
    return yaml.safe_load((CONFIG / "proxies.yaml").read_text())["design_check"]


def _base(concept: str) -> str:
    return concept.split(":")[0]


def _overlaps(a0, a1, b0, b1) -> bool:
    return a0 < b1 and b0 < a1


def pretrend(effect: pd.Series, onset: pd.Timestamp, hours: float | None = None, share: float | None = None) -> dict | None:
    """Change of the effect series in the hours before the cause's onset, as a share of its storm range."""
    hours = rules()["pretrend_hours"] if hours is None else hours
    share = rules()["pretrend_threshold_share"] if share is None else share
    storm = effect.loc[STORM[0]:STORM[1]]
    rng = float(storm.max() - storm.min()) or 1.0
    before = effect.loc[onset - pd.Timedelta(hours=hours):onset]
    if len(before) < 2:
        return None
    change = float(before.iloc[-1] - before.iloc[0])
    if abs(change) >= share * rng:
        return {"type": "pretrend", "description": f"effect moved {change:+,.0f} in the {hours:g} h before the cause's "
                f"onset ({abs(change) / rng:.0%} of its storm range)", "change": change, "share_of_range": abs(change) / rng}
    return None


def design_check(family: dict, window: tuple[pd.Timestamp, pd.Timestamp], nodes: pd.DataFrame,
                 families: list[dict], effect_series: pd.Series | None = None,
                 cause_onset: pd.Timestamp | None = None, region_of=None) -> list[dict]:
    """Threats to validity for testing `family` over `window`. `region_of(region) -> class` filters graph events."""
    w0, w1 = window
    eff = family["effect"]
    threats = []
    ev = nodes[nodes.kind == "Event"].set_index("id")
    # 1. competing causes asserted in the documents
    for f in families:
        same_effect = f["effect"] == eff or _base(f["effect"]) == _base(eff)
        if f["id"] == family["id"] or not same_effect:
            continue
        if f["cause"] == family["cause"] or f.get("correlational"):
            continue
        times = [ev.valid_start.get(s["cause_event"]) for s in f["support"]]
        inside = [t for t in times if t is not None and not pd.isna(t) and w0 <= t < w1]
        if inside:
            threats.append({"type": "competing_cause", "family": f["id"],
                            "description": f"{f['cause']} is also claimed to cause {f['effect']} ({len(inside)} dated "
                                           f"cause events in the window)", "n_events": len(inside)})
    # 2. confounder events in the graph
    types = rules()["confounder_event_types"].get(eff) or rules()["confounder_event_types"].get(_base(eff)) or []
    types = [t for t in types if t != family["cause"] and t != _base(family["cause"])]
    e = ev[ev.subtype.isin(types) & ev.valid_start.between(w0, w1, inclusive="left")]
    if region_of is not None:
        e = e[e.region.map(region_of).isin(["ERCOT"])]
    for t, g in e.groupby("subtype"):
        threats.append({"type": "confounder_events", "event_type": t, "n_events": len(g),
                        "description": f"{len(g)} {t} events in the window (graph)",
                        "examples": [str(x)[:120] for x in g.quote.head(3)]})
    # 3. named confounders
    for c in rules()["named_confounders"]:
        if (eff in c["affects"] or _base(eff) in c["affects"]) and _overlaps(pd.Timestamp(c["start"]), pd.Timestamp(c["end"]), w0, w1):
            threats.append({"type": "named_confounder", "description": c["name"], "start": c["start"], "end": c["end"]})
    # 4. pretrend in the measured effect
    if effect_series is not None and cause_onset is not None:
        p = pretrend(effect_series, cause_onset)
        if p:
            threats.append(p)
    return threats
