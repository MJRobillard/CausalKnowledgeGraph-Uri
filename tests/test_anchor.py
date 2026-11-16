import pandas as pd
import pytest

from kg.anchor import (anchor_claim, bound, half_unit, is_approximate, load_shed_verdict, quotes_overlap,
                       region_class, route, tolerance, window_from)

T = pd.Timestamp


def test_tolerance_last_digit_or_one_percent():
    assert half_unit("was 52,037 MW", 52037) == 0.5
    assert tolerance(52037, 0.5, "mw", False) == pytest.approx(520.37)          # 1% wins
    assert half_unit("a low of 59.302 Hz", 59.302) == pytest.approx(0.0005)
    assert tolerance(59.302, 0.0005, "frequency", False) == pytest.approx(0.0005)  # Hz: half unit only
    assert half_unit("4.5 million Texans", 4_500_000) == pytest.approx(50_000)
    assert tolerance(70000, 0.5, "mw", True) == pytest.approx(3500)            # approximate: 5%


def test_approximate_and_bounds_read_just_before_the_number():
    q = "loss of generation was 52,277 MW (approximately 48.6%) at the highest point."
    assert is_approximate({"quote": q, "value": 52277})[0] is False            # 'approximately' belongs to 48.6
    assert is_approximate({"quote": q, "value": 48.6})[0] is True
    assert bound("Over 30,000 megawatts of power generation was offline", 30000) == "lower"
    assert bound("Demand was at just above 49,000 megawatts", 49000) is None    # 'just above' is approximate
    assert bound("up to 18 degrees lower", 18) == "upper"


def test_regions():
    assert region_class("ERCOT") == ("ercot", False)
    assert region_class("Texas") == ("ercot", True)
    assert region_class("Dallas") == ("station", "North Central")
    assert region_class("U.S. Lower 48")[0] == "other"


def test_routes_refuse_wrong_region_and_units():
    lower48 = {"metric": "mw", "unit": "MW", "value": 1000, "region": "U.S. Lower 48", "quote": "1,000 MW out"}
    assert route(lower48, None).reason == "region"
    multi = {**lower48, "region": "ERCOT+SPP+MISO"}
    assert route(multi, None).reason == "region"
    mph = {"metric": "temperature", "unit": "mph", "value": 30, "region": "Dallas", "quote": "winds of 30 mph"}
    assert route(mph, None).reason == "metric_mismatch"
    normal = {"metric": "temperature", "unit": "degF", "value": 39, "region": "Dallas",
              "quote": "normal daily minimum temperature for the same day of 39 degrees"}
    assert route(normal, None).reason == "metric_mismatch"
    reserve = {"metric": "mw", "unit": "MW", "value": 3000, "region": "ERCOT", "quote": "Reserves below 3,000 MW."}
    assert route(reserve, None).reason == "metric_mismatch"
    cause = {"metric": "mw", "unit": "MW", "value": 6130, "region": "ERCOT", "cause_category": "fuel_supply",
             "quote": "Fuel Limitations (6,130 MW, 12%)"}
    assert route(cause, None).reason == "metric_mismatch"
    shed = {"metric": "mw", "unit": "GW", "value": 20, "region": "Texas", "quote": "20 GW of load shed"}
    r = route(shed, None)
    assert r.series == "load_shed" and r.factor == 1000 and r.region_approx is True
    gas = {"metric": "mw", "unit": "MW", "value": 25000, "region": "ERCOT", "quote": "25,000 MW of gas-fired generation was offline"}
    assert route(gas, None).series == "ercot_mw_out_by_fuel:gas"


def test_windows_from_precision():
    t = T("2021-02-15 01:20")
    assert window_from(t, None, "minute") == (T("2021-02-15 01:15"), T("2021-02-15 01:26"))
    assert window_from(T("2021-02-15 19:30"), None, "hour") == (T("2021-02-15 18:00"), T("2021-02-15 21:00"))
    assert window_from(T("2021-02-16"), None, "day") == (T("2021-02-16"), T("2021-02-17"))
    assert window_from(T("2021-02-14"), T("2021-02-19"), "range") == (T("2021-02-14"), T("2021-02-20"))


def test_about_event_time_needs_overlapping_quotes():
    assert quotes_overlap("20,000 MW peak load shed.", "20,000 MW peak load shed")
    assert not quotes_overlap("as low as six degrees in Austin", "Temperatures began to drop below freezing on February 8")


# ---------------------------------------------------------------- against the real Tier 0 data
def test_ercot_peak_claim_anchors():
    c = {"gid": "x", "doc_id": "ercot_causes_2021_04_27", "tier": "1", "metric": "mw_peak", "unit": "MW",
         "value": 52037, "region": "ERCOT", "as_of": "2021-02-16T08:00:00-06:00", "cause_category": "not_a_cause",
         "quote": "The highest amount of unavailable capacity during the period of February 14-19, 2021 occurred on "
                  "February 16 at ~8:00 AM and was 52,037 MW."}
    r = anchor_claim(c, None)
    assert r["status"] == "anchored_ok" and r["series"].endswith("ercot_mw_out_by_fuel:total")


def test_dallas_low_uses_daily_minimum():
    c = {"gid": "y", "doc_id": "enverus_txoga_2021_04", "tier": "2", "metric": "temperature", "unit": "degF",
         "value": -2, "region": "Dallas", "as_of": "2021-02-16", "quote": "Dallas recorded temperatures as low as -2° F."}
    r = anchor_claim(c, None)
    assert r["status"] == "anchored_ok" and r["series"].endswith("TMIN") and r["observed"] == -2


def test_load_shed_started_at_1_20():
    from kg.graph_build import load
    v = load_shed_verdict(load()[0])
    assert v["first_nonzero_minute_start"] == T("2021-02-15 01:20")
