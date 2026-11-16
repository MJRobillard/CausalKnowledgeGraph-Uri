import pandas as pd

from kg import series as S
from kg.design_check import design_check, pretrend

T = pd.Timestamp


def test_pretrend_flags_the_load_shed_event_study():
    """The old event study (cause: shed jump at 2/15 01:59, effect: gas MW out) was confounded:
    gas outages were already climbing before the shock. The design check must say so."""
    gas = S.minute_start(S.mw_out_by_fuel())["gas"]
    p = pretrend(gas, T("2021-02-15 01:59"))
    assert p is not None and p["change"] > 3000


def test_no_pretrend_when_flat():
    s = pd.Series([100.0] * 10 + list(range(100, 200, 10)) * 1, index=pd.date_range("2021-02-14", periods=20, freq="h"))
    assert pretrend(s, T("2021-02-14 05:00")) is None


def _ev(i, typ, t, region="ERCOT", quote="q"):
    return {"id": i, "kind": "Event", "subtype": typ, "valid_start": t, "region": region, "quote": quote}


def test_design_check_lists_competing_causes_confounders_and_named_events():
    nodes = pd.DataFrame([_ev("c1", "WeatherCondition", T("2021-02-15 00:00")),
                          _ev("g1", "GasSupplyDisruption", T("2021-02-15 03:00")),
                          _ev("e1", "GenerationOutage", T("2021-02-15 04:00")),
                          _ev("f1", "GridEmergency", T("2021-02-15 01:55")),
                          _ev("f2", "GridEmergency", T("2021-02-10 01:55"))])           # outside the window
    fam = {"id": "WeatherCondition->GenerationOutage:gas@ERCOT", "cause": "WeatherCondition",
           "effect": "GenerationOutage:gas", "support": []}
    other = {"id": "GasSupplyDisruption->GenerationOutage:gas@ERCOT", "cause": "GasSupplyDisruption",
             "effect": "GenerationOutage:gas", "support": [{"cause_event": "g1"}]}
    th = design_check(fam, (T("2021-02-14"), T("2021-02-20")), nodes, [fam, other])
    kinds = [t["type"] for t in th]
    assert "competing_cause" in kinds
    assert {t["event_type"] for t in th if t["type"] == "confounder_events"} == {"GasSupplyDisruption", "GridEmergency"}
    assert any("load shed" in t["description"] for t in th if t["type"] == "named_confounder")
    assert [t for t in th if t.get("event_type") == "GridEmergency"][0]["n_events"] == 1
