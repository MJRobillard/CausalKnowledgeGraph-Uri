import numpy as np
import pandas as pd

from analysis.cold_models import fdh, simulate


def test_fail_restart_dynamics():
    warm = np.full(10, 40.0)
    o = simulate(0.01, 0.1, warm, 1000.0, 500.0)
    assert np.allclose(o, 500 * 0.9 ** np.arange(10))           # above freezing: only restarts
    cold = np.full(10, 22.0)
    o = simulate(0.01, 0.0, cold, 1000.0, 0.0)
    assert np.isclose(o[-1], 1000 * (1 - 0.9 ** 9))                     # 10 F below: 10%/h of what is still available
    assert (np.diff(o) > 0).all() and o.max() < 1000


def test_freezing_degree_hours():
    t = pd.date_range("2021-02-10", periods=4, freq="h")
    f = fdh(pd.Series([40, 30, 20, 33], index=t))
    assert f.tolist() == [0, 2, 14, 14]


def test_tent_driver_shape():
    from analysis.cold_baselines import tent
    d = tent(10, peak=4, end=8, height=2.0)
    assert d.tolist() == [0.0, 0.5, 1.0, 1.5, 2.0, 1.5, 1.0, 0.5, 0.0, 0.0]
