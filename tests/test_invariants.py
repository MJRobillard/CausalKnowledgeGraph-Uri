
import networkx as nx
import pandas as pd

from kg.invariants import (LOOP_EDGES, causal_cycles, cause_before_effect, class_layer, dataset_family,
                           instance_grades, into_weather, loop_entity_graph, loop_unrolled, tier0_overrides)

T = pd.Timestamp


def _ev(i, typ, t, prec, label=""):
    return {"id": i, "kind": "Event", "subtype": typ, "valid_start": t, "precision": prec, "label": label,
            "quote": label, "tier": "2", "doc_id": "d"}


def _edge(s, d, kind="caused", modality="asserted"):
    return {"src": s, "dst": d, "kind": kind, "modality": modality, "doc_id": "d", "quote": "q"}


NODES = pd.DataFrame([
    _ev("cold", "WeatherCondition", T("2021-02-14 06:00"), "hour", "cold snap"),
    _ev("gasout", "GenerationOutage", T("2021-02-15 01:00"), "minute", "gas-fired units tripped"),
    _ev("shed", "LoadShed", T("2021-02-15 01:20"), "minute", "load shed"),
    _ev("early", "GasSupplyDisruption", T("2021-02-16"), "day", "production fell"),
    _ev("sameday", "GridEmergency", T("2021-02-16 09:00"), "minute", "EEA"),
    _ev("notime", "MarketAction", None, "unknown", "price cap"),
    {"id": "series:noaa:x", "kind": "Series", "subtype": "degF", "tier": "0a", "doc_id": "noaa_zone_temp_hourly"},
    {"id": "series:unit_outage_2021_03_04:u", "kind": "Series", "tier": "0b", "doc_id": "unit_outage_2021_03_04"},
    {"id": "series:unit_outage_2021_03_12:u", "kind": "Series", "tier": "0b", "doc_id": "unit_outage_2021_03_12"},
    {"id": "series:ercot:x", "kind": "Series", "tier": "0b", "doc_id": "ercot_mw_out_by_fuel"},
    {"id": "claim", "kind": "QuantClaim", "tier": "2", "doc_id": "d"},
])


def test_cause_before_effect():
    edges = pd.DataFrame([_edge("cold", "gasout"),            # ok
                          _edge("shed", "gasout"),            # violation: 01:20 cannot cause 01:00
                          _edge("early", "sameday"),          # same day, day precision: unverifiable
                          _edge("notime", "shed")])           # unknown time: unverifiable
    r = cause_before_effect(NODES, edges)
    assert r.status.tolist() == ["ok", "violation", "time_unverifiable", "time_unverifiable"]


def test_nothing_causes_the_weather():
    edges = pd.DataFrame([_edge("gasout", "cold"), _edge("shed", "series:noaa:x"), _edge("cold", "gasout")])
    assert into_weather(NODES, edges).edge.tolist() == [0, 1]


def test_tier0_is_never_overridden():
    edges = pd.DataFrame([_edge("claim", "series:ercot:x", kind="conflict"),
                          _edge("series:unit_outage_2021_03_04:u", "series:unit_outage_2021_03_12:u", kind="revised_by"),
                          _edge("series:ercot:x", "series:unit_outage_2021_03_12:u", kind="revised_by")])
    r = tier0_overrides(NODES, edges)
    assert r.edge.tolist() == [0, 2]
    assert dataset_family("unit_outage_2021_03_04") == dataset_family("unit_outage_2021_03_12") == "unit_outage"


def test_causal_cycles():
    edges = pd.DataFrame([_edge("a", "b"), _edge("b", "c", kind="contributed_to"), _edge("c", "a"),
                          _edge("c", "d", kind="preceded")])
    assert [sorted(c) for c in causal_cycles(edges)] == [["a", "b", "c"]]


def test_loop_has_cycle_until_unrolled():
    assert not nx.is_directed_acyclic_graph(loop_entity_graph())
    g = loop_unrolled("2021-02-14 00:00", "2021-02-14 05:00")
    assert nx.is_directed_acyclic_graph(g) and g.number_of_nodes() == 24
    # with every lag set to zero the unrolled graph would still loop within the hour
    zero = nx.DiGraph([(f"{a}@0", f"{b}@0") for a, b, _, _ in LOOP_EDGES])
    assert not nx.is_directed_acyclic_graph(zero)


def test_grades():
    nodes = NODES
    edges = pd.DataFrame([_edge("cold", "gasout"), _edge("gasout", "shed", modality="hedged"),
                          _edge("early", "sameday", modality="denied")])
    assert instance_grades(edges).tolist() == ["G1", "G0", "G0"]
    _, ce = class_layer(nodes, edges, {})
    g = {(r.src, r.dst): (r.grade, None if pd.isna(r.invariant_flags) else r.invariant_flags)
         for r in ce[ce.kind == "class_caused"].itertuples()}
    assert g[("class:WeatherCondition", "class:GenerationOutage:gas")] == ("G1", None)
    assert g[("class:GenerationOutage:gas", "class:LoadShed")] == ("G0", "hedged_only")
    test = {"passed": True, "method": "fail_restart", "data_sources": ["ercot_mw_out_by_fuel"],
            "input_documents": ["ercot_board_2021_02_24"]}
    key_gas, key_coal = ("WeatherCondition", "GenerationOutage:gas"), ("WeatherCondition", "GenerationOutage:coal")
    edges = edges.assign(doc_id=["ferc_final_2021_11", "d", "d"])           # the asserted cold edge is from FERC
    _, ce = class_layer(nodes, edges, {key_gas: [test], key_coal: [test]})
    g = {(r.src, r.dst): r.grade for r in ce[ce.kind == "class_caused"].itertuples()}
    assert g[("class:WeatherCondition", "class:GenerationOutage:gas")] == "G3"     # test + independent Tier 2 text
    assert g[("class:WeatherCondition", "class:GenerationOutage:coal")] == "G2"    # test only
    # H4: text from the document that published the test's data is not independent, nor is Tier 3 text
    for doc in ("ercot_board_2021_02_24", "news_ap_2021_02_15"):
        _, ce = class_layer(nodes, edges.assign(doc_id=[doc, "d", "d"]), {key_gas: [test]})
        g = {(r.src, r.dst): r.grade for r in ce[ce.kind == "class_caused"].itertuples()}
        assert g[("class:WeatherCondition", "class:GenerationOutage:gas")] == "G2", doc
    # a second passed test counts only if it uses a different data source
    other = {**test, "data_sources": ["eia_ercot_hourly"]}
    _, ce = class_layer(nodes, edges, {key_coal: [test, dict(test)]})
    assert ce[(ce.kind == "class_caused") & (ce.dst == "class:GenerationOutage:coal")].grade.item() == "G2"
    _, ce = class_layer(nodes, edges, {key_coal: [test, other]})
    assert ce[(ce.kind == "class_caused") & (ce.dst == "class:GenerationOutage:coal")].grade.item() == "G3"
    failed = {**test, "passed": False}
    _, ce = class_layer(nodes, edges, {("WeatherCondition", "GenerationOutage:solar"): [failed]})
    g = {(r.src, r.dst): r.grade for r in ce[ce.kind == "class_caused"].itertuples()}
    assert g[("class:WeatherCondition", "class:GenerationOutage:solar")] == "G0"
