import pandas as pd
import pytest

from analysis.causal_ml import PEAK_DAY, warmer, winterization


def _panel():
    days = [pd.Timestamp("2021-02-15"), PEAK_DAY]
    rows = []
    for uid, fuel, cap in (("1:gas", "gas", 100.0), ("2:gas", "gas", 200.0), ("3:gas", "gas", 100.0), ("4:gas", "gas", 100.0)):
        for d, cold in zip(days, (3.0, 12.0)):
            rows.append({"plant_fuel_id": uid, "plant_code": int(uid[0]), "fuel": fuel, "capacity_mw": cap,
                         "day": d, "cold_f": cold, "mwh_out": 240.0})
    p = pd.DataFrame(rows)
    u = pd.DataFrame({"plant_fuel_id": ["1:gas", "2:gas", "3:gas", "4:gas"], "plant_code": [1, 2, 3, 4], "fuel": "gas",
                      "capacity_mw": [100.0, 200.0, 100.0, 100.0], "cate_share_per_F": [0.01, 0.02, 0.01, 0.05],
                      "cate_ci_low": 0.0, "cate_ci_high": 0.1})
    return p, u


def test_warmer_counterfactual_cannot_remove_more_cold_than_there_was():
    p, u = _panel()
    cf = warmer(p, u, deltas=(5,)).set_index("day")
    # Feb 15: cold 3 F, so only 3 F can be removed; peak day: 5 F of 12
    exp_15 = (0.01 * 100 + 0.02 * 200 + 0.01 * 100 + 0.05 * 100) * 3
    exp_16 = exp_15 / 3 * 5
    assert cf.loc[pd.Timestamp("2021-02-15"), "mw_avoided"] == pytest.approx(exp_15)
    assert cf.loc[PEAK_DAY, "mw_avoided"] == pytest.approx(exp_16)
    assert cf.loc[PEAK_DAY, "mw_out_actual"] == pytest.approx(40.0)          # 4 units x 240 MWh / 24


def test_winterization_moves_the_top_quarter_to_the_median():
    p, u = _panel()
    w = winterization(p, u)["gas"]
    med = 0.015
    assert w["units_in_top_quarter"] == 1                                    # only the 0.05 unit is above Q75
    assert w["mw_saved_peak_day"] == pytest.approx((0.05 - med) * 12 * 100)
