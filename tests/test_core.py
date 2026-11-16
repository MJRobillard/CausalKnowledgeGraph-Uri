import pytest

from kg.chunk import chunk_doc
from kg.common import documents, quote_in
from kg.post import compare


def test_quote_normalizer_handles_hyphens_ligatures_quotes():
    text = "the gas-fired genera- tion ﬂeet “froze” on\nFebruary 15"
    assert quote_in("gas-fired generation fleet \"froze\" on February 15", text)
    assert not quote_in("coal fleet froze", text)
    assert not quote_in("", text)


def test_tiers():
    d = documents()
    assert d["ferc_final_2021_11"]["tier"] == "2"
    assert d["ercot_causes_2021_04_27"]["tier"] == "1"
    assert d["enverus_txoga_2021_04"]["tier"] == "2"
    assert "funded_by_interested_party" in d["enverus_txoga_2021_04"]["flags"]
    for doc in d.values():
        if doc.get("publisher_role") == "news":
            assert doc["tier"] == "3"
    assert d["noaa_zone_temp_hourly"]["tier"] == "0a"
    for ds in ("ercot_mw_out_by_fuel", "ercot_frequency", "ercot_gen_load_shed", "unit_outage_2021_03_04",
               "unit_outage_2021_03_12", "eia_ercot_hourly"):
        assert d[ds]["tier"] == "0b"
    assert d["ercot_gen_list_2021_02_14"]["tier"] == "1"      # a list of units, not measurements


def test_held_out_and_skipped_docs_are_refused():
    with pytest.raises(PermissionError):
        chunk_doc("ercot_mw_out_by_fuel")              # Tier 0 datasets are never extracted
    with pytest.raises(PermissionError):
        chunk_doc("ut_austin_timeline_2021_07")
    with pytest.raises(PermissionError):
        chunk_doc("puct_51878_26")


def _claim(doc, value, metric, region, label, scheme, den, w0, w1):
    return {"doc_id": doc, "value": value, "unit": "percent", "metric": metric, "region": region,
            "cause_label_raw": label, "cause_scheme": scheme, "denominator": den,
            "window_start": w0, "window_end": w1}


ERCOT_12 = _claim("ercot_causes_2021_04_27", 12, "share_of_mw", "ERCOT", "Fuel Limitations",
                  "ercot_2021_04_27", "total MW out at max unavailability", "2021-02-14", "2021-02-19")
FERC_314 = _claim("ferc_final_2021_11", 31.4, "share_of_events", "ERCOT+SPP+MISO", "fuel issues",
                  "ferc_nerc_2021_11", "all outages, derates and failures to start", "2021-02-08", "2021-02-20")
FERC_273 = _claim("ferc_final_2021_11", 27.3, "share_of_events", "ERCOT+SPP+MISO",
                  "natural gas fuel supply issues", "ferc_nerc_2021_11",
                  "all outages, derates and failures to start", "2021-02-08", "2021-02-20")


def test_12_vs_31_4_is_different_scope():
    label, diffs = compare(ERCOT_12, FERC_314, documents())
    assert label == "different_scope"
    assert {"metric", "region", "cause_scheme"} <= set(diffs)


def test_subcategory_is_not_a_conflict():
    label, diffs = compare(FERC_314, FERC_273, documents())
    assert label == "different_scope" and diffs == ["cause_label_raw"]


def test_same_scope_close_values_comparable_far_values_conflict():
    assert compare(FERC_314, {**FERC_314, "value": 31.0}, documents())[0] == "comparable"
    assert compare(FERC_314, {**FERC_314, "value": 40.0}, documents())[0] == "conflict"


def test_prelim_vs_final_is_superseded():
    prelim = {**FERC_314, "doc_id": "ferc_prelim_2021_09", "value": 30.0}
    assert compare(prelim, FERC_314, documents())[0] == "superseded"


# ---------------------------------------------------------------- v0.2
def test_v02_vocabulary_in_schema_and_ontology():
    from kg.common import ontology, schema
    o, p = ontology(), schema()["properties"]
    assert "Region" in o["entity_types"] and "Region" in p["entities"]["items"]["properties"]["type"]["enum"]
    for ev in ("Demand", "WaterServiceDisruption"):
        assert ev in o["event_types"] and ev in p["events"]["items"]["properties"]["type"]["enum"]
    assert "associated_with" in o["predicates"]
    assert "associated_with" in p["relations"]["items"]["properties"]["predicate"]["enum"]
    q = p["quant_claims"]["items"]
    assert "power_supply_loss" in o["cause_category_values"]
    assert "power_supply_loss" in q["properties"]["cause_category"]["enum"]
    for m in ("share_of_energy", "share_of_responses", "wind_speed"):
        assert m in o["quant_claim"]["metric_values"] and m in q["properties"]["metric"]["enum"]
    assert {"value_low", "value_high", "approximate"} <= set(q["required"])


def test_ranges_compare_by_overlap():
    rng = {**FERC_314, "value": 56, "value_low": 52, "value_high": 60}
    assert compare(rng, {**FERC_314, "value": 58}, documents())[0] == "comparable"
    assert compare(rng, {**FERC_314, "value": 70}, documents())[0] == "conflict"


def test_approximate_is_still_flagged_as_conflict():
    from kg.post import comparability_links
    mw = {**FERC_314, "unit": "MW", "cause_category": "fuel_supply", "chunk_id": "x"}
    approx = {**mw, "gid": "a", "value": 13000, "approximate": True, "quote": "nearly 13,000"}
    exact = {**mw, "gid": "b", "value": 12800, "chunk_id": "y", "quote": "12,800"}
    assert compare(approx, exact, documents())[0] == "conflict"
    (link,) = comparability_links([approx, exact], documents())
    assert link["label"] == "conflict" and link["approximate"] is True


def test_generic_roles_are_not_entities():
    from kg.post import is_generic_role
    assert is_generic_role({"name": "Balancing Authorities"})
    assert is_generic_role({"name": "the GOs/GOPs"})
    assert not is_generic_role({"name": "ERCOT"})
    assert not is_generic_role({"name": "MISO South"})


# ---------------------------------------------------------------- v0.2 scope fixes
def test_sentences_keep_abbreviations():
    from kg.scope import sentences
    ss = sentences("At 1:55 a.m. on Feb. 15 the U.S. grid dipped. Then it recovered.")
    assert ss == ["At 1:55 a.m. on Feb. 15 the U.S. grid dipped.", "Then it recovered."]


def test_feb_dates_ranges_and_singles():
    from kg.scope import feb_dates
    assert feb_dates("From February 8 through 20, in the Event Area") == [("2021-02-08", "2021-02-20")]
    assert feb_dates("On February 14, 88.4 percent") == [("2021-02-14", "2021-02-14")]
    assert feb_dates("at time of Max Unavailability: 2/16/2021 at 8:00 a.m.") == [("2021-02-16", "2021-02-16")]
    assert len(feb_dates("February 14 (18.1 percent), and February 17 (21.6 percent)")) == 2
    assert feb_dates("between February 14 and 17, 30 percent") == [("2021-02-14", "2021-02-17")]
    assert feb_dates("Both February 15 and 17 saw 20 percent") == [("2021-02-15", "2021-02-15"), ("2021-02-17", "2021-02-17")]


def test_date_rule_only_overrides_defaults_with_one_date():
    from kg.scope import apply_date_rule
    text = "Background. On February 14, 40 percent of production was lost. Both February 15 and 17 saw 20 percent."
    c = {"quote": "40 percent of production", "window_start": "2021-02-08", "window_end": "2021-02-20",
         "scope_source": {"window": "document_default"}}
    assert apply_date_rule(c, text)["window_start"] == "2021-02-14"
    assert apply_date_rule({**c, "scope_source": {"window": "sentence"}}, text)["window_start"] == "2021-02-08"
    two = {**c, "quote": "saw 20 percent"}
    assert apply_date_rule(two, text)["window_start"] == "2021-02-08"   # two dates: leave it to the model


# ---------------------------------------------------------------- step 0 (2026-11-15): numbers and labels
def test_quote_check_keeps_number_punctuation():
    text = "Fuel issues caused 31.4 percent of outages; reserves fell below 2, 300 MW and -2° F in Dallas."
    assert quote_in("caused 31.4 percent", text)
    assert not quote_in("caused 314 percent", text)          # was a pass when only [a-z0-9] were kept
    assert quote_in("below 2,300 MW", text)
    assert quote_in("−2° F in Dallas", text)                  # unicode minus


def test_value_must_appear_in_quote():
    from kg.common import value_in_quote
    ok = lambda v, q, **kw: value_in_quote({"value": v, "quote": q, **kw})
    assert ok(31.4, "fuel issues (31.4%)") and ok(31.4, "31.4 percent") and ok(20000, "20,000 MW")
    assert not ok(31.4, "fuel issues 314 units") and not ok(314, "31.4 percent")
    assert ok(4_500_000, "4.5 million Texans") and ok(2300, "Reserves below 2, 300 MW.")
    assert ok(-2, "as low as −2° F") and ok(6, "as low as six degrees") and ok(3, "the third largest")
    assert ok(25, "twenty-five units")
    assert not ok(4.383, "Below 59.4 Hz for 4m 23s")          # derived values are not written in the quote
    assert ok(56, "52 to 60 percent", value_low=52, value_high=60)


def test_cause_labels_normalize_within_scheme():
    from kg.post import canonical_labels, labels_match
    assert labels_match("fuel-related issues", "Fuel Issues")
    assert labels_match("Icing on blades", "blade icing")
    for other in ("other fuel issues", "natural gas fuel supply issues", "Freezing issues and fuel issues"):
        assert not labels_match("fuel issues", other)
    assert not labels_match("freezing issues", "freezing issues at processing facilities")
    assert not labels_match("Planned Generation Outages", "Unplanned Generation Outages")
    cs = [{**FERC_314, "cause_label_raw": l} for l in ("fuel issues", "Fuel Issues", "fuel-related issues")]
    cs.append(FERC_273)
    canonical_labels(cs)
    assert len({c["cause_label_canon"] for c in cs[:3]}) == 1
    assert cs[3]["cause_label_canon"] != cs[0]["cause_label_canon"]
    # a label variant with a different value is now a conflict; the subcategory stays different_scope
    assert compare(cs[0], {**cs[2], "value": 40.0}, documents())[0] == "conflict"
    assert compare(cs[0], cs[3], documents())[0] == "different_scope"
