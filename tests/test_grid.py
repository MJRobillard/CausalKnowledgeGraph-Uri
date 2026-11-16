import numpy as np
import pandas as pd
import pytest

from kg.grid import Grid, dcopf


def three_bus(rate_12=1e5):
    """Bus 1: cheap gen (20 $/MWh). Bus 2: expensive gen (50). Bus 3: load. Lines 1-3, 2-3, 1-2, equal reactance."""
    bus = pd.DataFrame({"pd_mw": [0.0, 0.0, 150.0], "zone": "Z", "lat": 0.0, "lon": 0.0, "county": "C",
                        "name": ["b1", "b2", "b3"], "base_kv": 115.0}, index=[1, 2, 3])
    gen = pd.DataFrame({"bus": [1, 2], "pmax": [200.0, 200.0], "pmin": 0.0, "status": 1, "fuel": ["gas", "gas"],
                        "cost": [20.0, 50.0], "lat": 0.0, "lon": 0.0, "county": "C", "zone": "Z"})
    branch = pd.DataFrame({"from_bus": [1, 2, 1], "to_bus": [3, 3, 2], "x": [0.1, 0.1, 0.1], "tap": 1.0,
                           "rate": [rate_12, 1e5, 1e5], "status": 1, "device": "Line"})
    return Grid(bus=bus, gen=gen, branch=branch, base_mva=100.0)


def test_uncongested_dispatch_uses_the_cheap_unit():
    g = three_bus()
    r = dcopf(g, g.bus.pd_mw, g.gen.pmax)
    assert r.status == "ok" and r.shed.sum() == pytest.approx(0, abs=1e-6)
    assert r.pg[0] == pytest.approx(150, abs=1e-4) and r.pg[1] == pytest.approx(0, abs=1e-4)
    assert np.allclose(r.lmp.values, 20.0, atol=1e-4)                       # one price everywhere


def test_congestion_splits_prices():
    g = three_bus(rate_12=60.0)                                              # line 1-3 limited to 60 MW
    r = dcopf(g, g.bus.pd_mw, g.gen.pmax)
    assert r.loading.max() == pytest.approx(1.0, abs=1e-6)
    assert r.pg[1] > 0                                                       # the expensive unit must run
    assert r.lmp[3] > r.lmp[1]                                               # the load bus pays more


def test_shortage_sheds_at_voll():
    g = three_bus()
    r = dcopf(g, g.bus.pd_mw, pd.Series([60.0, 40.0]), voll=9000.0)
    assert r.shed.sum() == pytest.approx(50.0, abs=1e-4)
    assert r.lmp[3] == pytest.approx(9000.0, rel=1e-6)
