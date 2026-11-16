"""DC optimal power flow on TAMU's synthetic Texas grid (2021TXPowerOutage/bte2k, from Breakthrough Energy Sciences).

The grid: 2,000 buses, 3,206 rated branches, 606 generators with fuel and polynomial costs, 1,250 substations with
lat/lon in the eight ERCOT weather zones. It resembles ERCOT; it is not ERCOT's real network (ASSUMPTIONS G-items).

The model, one hour at a time (linear program, scipy HiGHS):
    min  sum_g c_g Pg + VOLL * sum_b shed_b
    s.t. bus balance   sum_{g at b} Pg - (Pd_b - shed_b) = sum_{lines out of b} f - sum_{lines into b} f
         line flow     f_l = (theta_from - theta_to) / x_l * baseMVA,   |f_l| <= rateA_l
         generation    0 <= Pg <= available_g,   0 <= shed_b <= Pd_b,   theta_ref = 0
Duals of the bus balance are locational prices ($/MWh); a line is binding when |f| >= 99% of its rating.

No unit commitment, no ramping, no reserves, no losses, no HVDC or DC ties; steady state only.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd
import scipy.io as sio
import scipy.sparse as sp
from scipy.optimize import linprog
from shapely.geometry import Point
from shapely.strtree import STRtree

from .common import TAMU_REPO

CASE = TAMU_REPO / "bte2k" / "case_Mar3_5pm.mat"
VOLL = 9000.0                        # $/MWh, ERCOT's offer cap in Feb 2021; shedding is the last resort
BINDING = 0.99
FUEL = {"ng": "gas", "coal": "coal", "wind": "wind", "solar": "solar", "nuclear": "nuclear", "hydro": "hydro", "other": "other"}


@dataclass
class Grid:
    bus: pd.DataFrame        # index = bus number; pd_mw, zone, sub, lat, lon, county
    gen: pd.DataFrame        # gen index; bus, pmax, pmin, fuel, cost ($/MWh at mid-output), lat, lon, county, zone
    branch: pd.DataFrame     # from_bus, to_bus, x, rate, status
    base_mva: float


@lru_cache
def load_grid() -> Grid:
    if not CASE.is_file():
        raise FileNotFoundError(f"{CASE} not found. The grid model comes from the TAMU repo: "
                                "git clone https://github.com/tamu-engineering-research/2021TXPowerOutage")
    m = sio.loadmat(CASE, squeeze_me=True, struct_as_record=False)["mpc"]
    zones = {int(z[0]): str(z[1]) for z in m.zone}
    sub = pd.DataFrame(m.sub, columns=["name", "sub_id", "lat", "lon", "interconnect"]).astype({"sub_id": int, "lat": float, "lon": float})
    b = m.bus
    bus = pd.DataFrame({"bus": b[:, 0].astype(int), "type": b[:, 1].astype(int), "pd_mw": b[:, 2].astype(float),
                        "zone": [zones.get(int(z), str(z)) for z in b[:, 6]],          # bus area = weather zone 301-308
                        "load_zone_code": b[:, 10].astype(int), "base_kv": b[:, 9].astype(float)})
    bus["sub_id"] = np.array([int(x[0]) for x in m.bus2sub]) if np.ndim(m.bus2sub) == 2 else m.bus2sub.astype(int)
    # bus2sub rows are (sub id, interconnect) aligned with bus rows; map to the substation table by row position
    bus = bus.join(sub.set_index("sub_id")[["name", "lat", "lon"]], on="sub_id")
    if bus.lat.isna().mean() > 0.5:          # bus2sub uses BES's global sub ids: align by order instead
        ids = sorted(set(bus.sub_id))
        pos = {sid: i for i, sid in enumerate(ids)}
        bus["lat"] = [sub.lat.iloc[pos[s]] for s in bus.sub_id]
        bus["lon"] = [sub.lon.iloc[pos[s]] for s in bus.sub_id]
        bus["name"] = [sub.name.iloc[pos[s]] for s in bus.sub_id]
    bus = bus.set_index("bus")
    g = m.gen
    c = m.gencost
    # polynomial cost (model 2): ncost coefficients, highest power first; marginal cost at half of Pmax
    pmax = g[:, 8].astype(float)
    mc = []
    for i in range(len(g)):
        n = int(c[i, 3]); coef = c[i, 4:4 + n]
        p = pmax[i] / 2
        mc.append(sum(coef[k] * (n - 1 - k) * p ** (n - 2 - k) for k in range(n - 1)) if n >= 2 else 0.0)
    gen = pd.DataFrame({"bus": g[:, 0].astype(int), "pmax": pmax, "pmin": g[:, 9].astype(float),
                        "status": g[:, 7].astype(int), "fuel": [FUEL.get(str(f), "other") for f in m.genfuel],
                        "cost": np.maximum(np.array(mc, dtype=float), 0.0)})
    gen = gen.join(bus[["lat", "lon", "zone"]], on="bus")
    br = m.branch
    branch = pd.DataFrame({"from_bus": br[:, 0].astype(int), "to_bus": br[:, 1].astype(int), "x": br[:, 3].astype(float),
                           "tap": np.where(br[:, 8] == 0, 1.0, br[:, 8]).astype(float), "rate": br[:, 5].astype(float),
                           "status": br[:, 10].astype(int), "device": m.branchdevicetype})
    grid = Grid(bus=bus, gen=gen, branch=branch, base_mva=float(m.baseMVA))
    add_counties(grid)
    return grid


def add_counties(grid: Grid) -> None:
    """County of each bus and generator by point-in-polygon on Census counties (no third-party pickles)."""
    from .spatial import tx_counties
    c = tx_counties()
    tree = STRtree(list(c.geometry))
    def county(lon, lat):
        p = Point(lon, lat)
        hits = [i for i in tree.query(p) if c.geometry.iloc[i].contains(p)]
        if hits:
            return c.county.iloc[hits[0]]
        return c.county.iloc[int(tree.nearest(p))]          # just offshore or on a border
    grid.bus["county"] = [county(lo, la) for lo, la in zip(grid.bus.lon, grid.bus.lat)]
    grid.gen["county"] = grid.gen.bus.map(grid.bus.county)


@dataclass
class Result:
    status: str
    cost: float
    pg: pd.Series            # MW by generator
    shed: pd.Series          # MW by bus
    flow: pd.Series          # MW by branch (from -> to positive)
    loading: pd.Series       # |flow| / rating
    lmp: pd.Series           # $/MWh by bus


def dcopf(grid: Grid, pd_mw: pd.Series, avail: pd.Series, voll: float = VOLL) -> Result:
    """One-hour DC OPF. pd_mw: MW demand by bus; avail: MW available by generator (<= pmax)."""
    bus_ids = grid.bus.index.to_numpy()
    bi = {b: i for i, b in enumerate(bus_ids)}
    nb = len(bus_ids)
    br = grid.branch[grid.branch.status == 1]
    nl, ng = len(br), len(grid.gen)
    fb = br.from_bus.map(bi).to_numpy(); tb = br.to_bus.map(bi).to_numpy()
    susc = grid.base_mva / (br.x.to_numpy() * br.tap.to_numpy())
    gb = grid.gen.bus.map(bi).to_numpy()
    # variable order: Pg (ng), shed (nb), f (nl), theta (nb)
    n = ng + nb + nl + nb
    cost = np.concatenate([grid.gen.cost.to_numpy(), np.full(nb, voll), np.zeros(nl), np.zeros(nb)])
    # bus balance: A_g Pg + shed - (A_from - A_to) f = Pd
    Ag = sp.csr_matrix((np.ones(ng), (gb, np.arange(ng))), shape=(nb, ng))
    Ash = sp.identity(nb, format="csr")
    Af = sp.csr_matrix((np.concatenate([-np.ones(nl), np.ones(nl)]), (np.concatenate([fb, tb]), np.concatenate([np.arange(nl)] * 2))),
                       shape=(nb, nl))
    bal = sp.hstack([Ag, Ash, Af, sp.csr_matrix((nb, nb))])
    # flow definition: f - susc (theta_f - theta_t) = 0
    At = sp.csr_matrix((np.concatenate([-susc, susc]), (np.concatenate([np.arange(nl)] * 2), np.concatenate([fb, tb]))), shape=(nl, nb))
    fdef = sp.hstack([sp.csr_matrix((nl, ng)), sp.csr_matrix((nl, nb)), sp.identity(nl), At])
    # reference angle
    ref = sp.csr_matrix(([1.0], ([0], [ng + nb + nl])), shape=(1, n))
    A_eq = sp.vstack([bal, fdef, ref]).tocsc()
    pdv = pd_mw.reindex(bus_ids).fillna(0.0).clip(lower=0).to_numpy()
    b_eq = np.concatenate([pdv, np.zeros(nl), [0.0]])
    av = avail.reindex(grid.gen.index).fillna(0.0).clip(lower=0).to_numpy()
    rate = np.where(br.rate.to_numpy() > 0, br.rate.to_numpy(), 1e5)
    bounds = ([(0.0, a) for a in av] + [(0.0, d) for d in pdv] + [(-r, r) for r in rate] + [(-np.pi, np.pi)] * nb)
    res = linprog(cost, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if res.status != 0:
        return Result(res.message, np.nan, *(pd.Series(dtype=float) for _ in range(5)))
    x = res.x
    pg = pd.Series(x[:ng], index=grid.gen.index)
    shed = pd.Series(x[ng:ng + nb], index=bus_ids)
    flow = pd.Series(x[ng + nb:ng + nb + nl], index=br.index)
    loading = flow.abs() / pd.Series(rate, index=br.index)
    lmp = pd.Series(res.eqlin.marginals[:nb], index=bus_ids)
    return Result("ok", float(res.fun), pg, shed, flow, loading, lmp)
