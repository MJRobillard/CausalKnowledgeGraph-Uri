import pandas as pd

from kg.hypotheses import (build_families, earned_grade, grade, region_class, relation_region, status_and_cap)


def test_region_classes():
    assert region_class("ERCOT") == ("ERCOT", False)
    assert region_class("Texas") == ("ERCOT", True)            # A2: Texas read as ERCOT, flagged
    assert region_class("Houston")[0] == "ERCOT"
    assert region_class("ERCOT+SPP+MISO")[0] == "multi-region"
    assert region_class("ERCOT and SPP")[0] == "multi-region"
    assert region_class("U.S. Lower 48")[0] == "multi-region"
    assert region_class("Permian Basin")[0] == "gas_basin"
    assert region_class("SPP")[0] == "SPP" and region_class("MISO South")[0] == "MISO"
    assert region_class(None) == (None, False)
    # no region on either event: the document's default scope decides
    assert relation_region(None, None, "ferc_final_2021_11")[0] == "multi-region"
    assert relation_region(None, "ERCOT", "ferc_final_2021_11")[:2] == ("ERCOT", False)


def _ev(i, typ, region, label=""):
    return {"id": i, "kind": "Event", "subtype": typ, "region": region, "label": label, "quote": label}


def _rel(gid, s, d, doc, kind="caused", modality="asserted"):
    return {"src": s, "dst": d, "kind": kind, "doc_id": doc, "modality": modality, "gid": gid, "quote": "q"}


def test_families_group_and_mark_origin():
    nodes = pd.DataFrame([_ev("c1", "WeatherCondition", "ERCOT"), _ev("o1", "GenerationOutage", "ERCOT", "gas-fired units froze"),
                          _ev("c2", "WeatherCondition", "Texas"), _ev("o2", "GenerationOutage", "Texas", "natural gas plants"),
                          _ev("s1", "LoadShed", "ERCOT"), _ev("s2", "LoadShed", "ERCOT"),
                          _ev("g1", "GasSupplyDisruption", "SPP"), _ev("o3", "GenerationOutage", "SPP", "gas units"),
                          _ev("p1", "GasSupplyDisruption", "ERCOT"), _ev("o4", "GenerationOutage", "ERCOT", "gas-fired"),
                          _ev("x1", "LoadShed", "ERCOT"), _ev("x2", "LoadShed", "ERCOT")])
    edges = pd.DataFrame([_rel("r1", "c1", "o1", "ferc_final_2021_11"),
                          _rel("r2", "c2", "o2", "news_ap_2021_02_15", kind="contributed_to"),
                          _rel("r3", "s1", "s2", "ferc_final_2021_11", modality="hedged"),
                          _rel("r4", "g1", "o3", "ferc_final_2021_11"),                  # SPP: out of scope
                          _rel("r5", "p1", "o4", "ercot_board_2021_02_24"),
                          _rel("r6", "x1", "x2", "ferc_final_2021_11", kind="preceded")])  # ignored predicate
    fams, stats = build_families(nodes, edges)
    f = {x["id"]: x for x in fams}
    cold = f["WeatherCondition->GenerationOutage:gas@ERCOT"]
    assert cold["n_support"] == 2 and cold["support_by_tier"] == {"2": 1, "3": 1} and cold["headline"]
    assert cold["first_appeared"] == "2021-02-15" and cold["origin"] == "documents"
    assert {s["quote_id"] for s in cold["support"]} == {"r1", "r2"}
    assert f["LoadShed->LoadShed@ERCOT"]["within_type"] and not f["LoadShed->LoadShed@ERCOT"]["headline"]
    assert f["GasSupplyDisruption->GenerationOutage:gas@ERCOT"]["origin"] == "both"
    assert f["GenerationOutage:gas->LoadShed@ERCOT"]["origin"] == "system_knowledge"
    assert f["GenerationOutage:gas->LoadShed@ERCOT"]["n_support"] == 0
    assert stats["out_of_scope_relations"] == {"SPP": 1}
    assert not any("@SPP" in k for k in f)


def test_status_and_grade_caps():
    assert status_and_cap("direct", "direct", True, False) == ("tested_direct", "G3")
    assert status_and_cap("direct", "proxy", True, False) == ("tested_proxy", "G2")
    assert status_and_cap("text_derived", "direct", True, False) == ("tested_proxy", "G2")
    assert status_and_cap("direct", "none", True, False) == ("untestable", "G1")
    assert status_and_cap("direct", "direct", False, False) == ("untestable", "G1")      # region has no series
    assert status_and_cap("direct", "direct", True, True) == ("correlational", None)
    assert grade("tested_proxy", "G2", earned_grade(3, 3, True, True)) == "G2"           # capped
    assert grade("tested_direct", "G3", earned_grade(1, 1, True, True)) == "G3"
    assert grade("untestable", "G1", earned_grade(2, 2, False, False)) == "G1"
    assert grade("tested_direct", "G3", earned_grade(0, 2, False, False)) == "G0"        # hedged only, test failed
    assert grade("correlational", None, "G1") is None


def _fam(cause, effect, region="ERCOT", within=False):
    return {"id": f"{cause}->{effect}@{region}", "cause": cause, "effect": effect, "region": region,
            "within_type": within, "correlational": False}


def test_family_status_rules():
    from kg.hypotheses import family_status, measure_for
    assert family_status(_fam("WeatherCondition", "GenerationOutage:gas"), True)["status"] == "tested_direct"
    st = family_status(_fam("GasSupplyDisruption", "GenerationOutage:gas"), True)
    assert st["status"] == "tested_proxy" and st["cause_measure"]["series"].startswith("ferc_fig35")  # first available
    # amendment 1: the Permian temperature proxy can't be an effect; amendment 2: Fig. 35 production can
    st = family_status(_fam("WeatherCondition", "GasSupplyDisruption", "gas_basin"), True)
    assert st["status"] == "tested_proxy" and st["effect_measure"]["series"].startswith("ferc_fig35")
    st = family_status(_fam("PowerLoss", "GasSupplyDisruption"), False)
    assert st["status"] == "untestable" and "processing facilities" in " ".join(st["needs"])
    # power_supply_loss override
    st = family_status(_fam("LoadShed", "GasSupplyDisruption"), False)
    assert st["status"] == "untestable" and "processing facilities" in " ".join(st["needs"])
    # same-type families and short coverage without a dedicated test
    assert family_status(_fam("LoadShed", "LoadShed", within=True), False)["status"] == "untestable"
    assert family_status(_fam("GenerationOutage", "GridEmergency"), False)["status"] == "untestable"
    assert family_status(_fam("GridEmergency", "GenerationOutage:coal"), True)["status"] == "tested_direct"
    # multi-region: ERCOT-only series can't speak for it
    st = family_status(_fam("WeatherCondition", "GenerationOutage", "multi-region"), True)
    assert st["status"] == "untestable" and st["cap"] == "G1"
    assert measure_for("FacilityShutdown", "x@ERCOT", "effect")["kind"] == "none"


# ---------------------------------------------------------------- amendment 2: reclassification, contrary evidence
def _e(i, typ, label, region="ERCOT", t=None):
    return {"id": i, "kind": "Event", "subtype": typ, "region": region, "label": label, "quote": label, "valid_start": t}


def test_reclassification_rules():
    T = pd.Timestamp
    nodes = pd.DataFrame([
        _e("ls", "LoadShed", "ERCOT ordered manual firm load shed"),
        _e("g1", "GasSupplyDisruption", "Natural gas production declined"),
        _e("pl", "GenerationOutage", "Loss of power at natural gas production facilities"),   # mistyped by the extractor
        _e("g2", "GasSupplyDisruption", "Natural gas production fell"),
        _e("fs", "FacilityShutdown", "Power outages occurred at natural gas processing facilities"),
        _e("ap", "FacilityShutdown", "The airport closed"),
        _e("w1", "WeatherCondition", "Moderating temperatures"), _e("r1", "Restoration", "Controlled outages reduced"),
        _e("w2", "WeatherCondition", "Stress caused by freeze-thaw cycles"), _e("o1", "GenerationOutage", "units tripped"),
        _e("p1", "GasSupplyDisruption", "Pipeline force majeure notices"), _e("p2", "GasSupplyDisruption", "Upstream wellhead production losses"),
        _e("h1", "WeatherCondition", "The 2011 cold front", t=T("2011-02-01")), _e("h2", "GasSupplyDisruption", "2011 production decline"),
        _e("x1", "GasSupplyDisruption", "fuel supply issues"), _e("x2", "GenerationOutage", "gas-fired units out")])
    rels = [("r1", "ls", "g1", "ferc_final_2021_11"), ("r2", "pl", "g2", "ferc_final_2021_11"),
            ("r3", "ls", "fs", "ferc_final_2021_11"), ("r4", "w1", "r1", "ercot_board_2021_02_24"),
            ("r5", "w2", "ap", "ferc_final_2021_11"), ("r6", "w2", "o1", "ferc_final_2021_11"),
            ("r7", "p1", "p2", "enverus_txoga_2021_04"), ("r8", "h1", "h2", "ferc_final_2021_11"),
            ("r9", "x1", "x2", "ferc_final_2021_11"), ("r10", "x1", "x2", "enverus_txoga_2021_04")]
    edges = pd.DataFrame([{"src": s, "dst": d, "kind": "caused", "doc_id": doc, "gid": g, "quote": "q",
                           "modality": "denied" if g == "r10" else "asserted"} for g, s, d, doc in rels])
    fams, stats = build_families(nodes, edges)
    f = {x["id"]: x for x in fams}
    pl = f["PowerLoss->GasSupplyDisruption@ERCOT"]
    assert {s["quote_id"] for s in pl["support"]} == {"r1", "r2"}             # shed and mistyped power loss merge
    assert {s["via"] for s in pl["support"]} == {"LoadShed", "GenerationOutage:gas"}
    assert "LoadShed->PowerLoss@ERCOT" in f                                    # power outage at a gas facility
    assert "WeatherCondition:warming->Restoration@ERCOT" in f
    assert "WeatherCondition->FacilityShutdown@ERCOT" in f                    # an airport is not a gas facility
    assert "WeatherCondition->GenerationOutage@ERCOT" in f                    # freeze-thaw is not warming
    sc = f["GasSupplyDisruption:pipeline->GasSupplyDisruption:production@ERCOT"]
    assert sc["supply_chain"] and sc["within_type"]
    assert stats["historical_relations_excluded"] == 1
    gas = f["GasSupplyDisruption->GenerationOutage:gas@ERCOT"]
    assert gas["n_support"] == 1 and gas["n_contrary"] == 1 and gas["contested"]
