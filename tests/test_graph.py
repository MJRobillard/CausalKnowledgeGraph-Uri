import pandas as pd

from kg.graph_build import to_central
from kg.unit_revisions import classify

T = pd.Timestamp


def _rows(rows, version):
    df = pd.DataFrame(rows, columns=["station", "unit", "mw_out", "start", "end", "fuel"])
    df["hsl_mw"] = df["available_mw"] = 0.0
    df["blank"] = df.station.isna()
    df["row"] = range(len(df))
    df["version"] = version
    return df


def test_unit_list_revision_kinds():
    old = _rows([("A", "U1", 100, T("2021-02-14 01:00"), T("2021-02-15"), "gas"),        # unchanged
                 ("A", "U2", 100, T("2021-02-14 02:00"), T("2021-02-15"), "gas"),        # MW corrected
                 (None, None, None, T("2021-02-14 03:00"), T("2021-02-16"), None),       # blank -> one unit
                 (None, None, None, T("2021-02-14 04:00"), T("2021-02-16"), None),       # blank -> two units
                 ("C", "OLD", 50, T("2021-02-14 05:00"), T("2021-02-15"), "wind")],      # renamed
                "0304")
    new = _rows([("A", "U1", 100, T("2021-02-14 01:00"), T("2021-02-15"), "gas"),
                 ("A", "U2", 120, T("2021-02-14 02:00"), T("2021-02-15"), "gas"),
                 ("B", "U1", 80, T("2021-02-14 03:00"), T("2021-02-16"), "solar"),
                 ("D", "U1", 30, T("2021-02-14 04:00"), T("2021-02-16"), "wind"),
                 ("D", "U2", 30, T("2021-02-14 04:00"), T("2021-02-16"), "wind"),
                 ("C", "NEW", 50, T("2021-02-14 05:00"), T("2021-02-15"), "wind"),
                 ("E", "U1", 10, T("2021-02-11 00:00"), T("2021-02-12"), "wind")],      # before 2/14
                "0312")
    r = classify(old, new)
    k = r.groupby("kind").size().to_dict()
    assert k == {"unchanged": 1, "correction": 2, "disclosure": 1, "disclosure_ambiguous": 2, "expansion": 1}
    assert set(r[r.kind == "correction"].changed) == {"mw_out", "unit"}


def test_to_central():
    assert to_central("2021-02-15T07:20:00Z") == T("2021-02-15 01:20")
    assert to_central("2021-02-15T01:20:00-06:00") == T("2021-02-15 01:20")
    assert to_central("2021-02-15") == T("2021-02-15")
    assert to_central("peak") is None and to_central(None) is None


def test_fig35_chart_read():
    import json
    import pandas as pd
    from kg.common import ROOT, documents
    d = pd.read_csv(ROOT / "data" / "processed" / "chart_reads" / "ferc_fig35_daily_production.csv").set_index("date")
    cal = json.loads((ROOT / "data" / "processed" / "chart_reads" / "ferc_fig35_daily_production_calibration.json").read_text())
    assert all(c["ok"] for c in cal["checks_against_ferc_text"].values())       # 90.8 on 2/8, 65.4 on 2/17
    assert len(d) == 59 and d.texas.idxmin() == "2021-02-17"
    assert 20 < d.loc["2021-01-01":"2021-01-31", "texas"].mean() < 23
    assert documents()["ferc_fig35_daily_production"]["tier"] == "2"            # a document's chart, not Tier 0
