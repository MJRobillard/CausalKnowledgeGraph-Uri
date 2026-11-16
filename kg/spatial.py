"""Spatial layer for the causal-ML panel: clean GeoJSONs, per-plant geographic features, and daily cold at each plant.

    uv run python -m kg.spatial        # after scripts/fetch_spatial.py and kg.fleet

Writes data/processed/spatial/geojson/*.geojson, data/processed/spatial/plant_features.parquet and data/processed/spatial/plant_daily_weather.parquet.
Distances are great-circle or, to lines and polygons, measured in EPSG:3083 (Texas Centric Albers Equal Area), in km.
Bearings are degrees clockwise from north, from the plant to the target. Rules are logged in ASSUMPTIONS (S-items).
"""
from __future__ import annotations

import io
import json
import zipfile
from functools import lru_cache

import numpy as np
import pandas as pd
import shapefile
from pyproj import Transformer
from shapely import ops
from shapely.geometry import LineString, Point, mapping, shape
from shapely.strtree import STRtree

from .common import DATA, PROCESSED

RAW = DATA / "raw"
FLEET = PROCESSED / "fleet"
OUT = PROCESSED / "spatial"
GJ = OUT / "geojson"
DAYS = pd.date_range("2021-02-01", "2021-02-28", freq="D")
TO_M = Transformer.from_crs(4326, 3083, always_xy=True).transform

# Hand-set reference points (WGS84). Approximate; log S-items.
LOAD_CENTRES = {"Houston": (-95.369, 29.760), "Dallas": (-96.797, 32.777), "San Antonio": (-98.494, 29.424),
                "Austin": (-97.743, 30.267), "Fort Worth": (-97.331, 32.755)}
GULF_COAST = [(-97.15, 25.95), (-97.17, 26.55), (-97.40, 27.80), (-96.85, 28.10), (-96.40, 28.45), (-95.95, 28.70),
              (-95.36, 28.95), (-94.80, 29.30), (-94.40, 29.55), (-93.87, 29.73)]       # Brownsville -> Sabine Pass
# Gas supply basins from EIA's shale/tight plays layer: (Basin, Shale_play) rules; None = any play in that basin
MAJOR_PLAYS = {"Permian": [("Permian", None)], "Eagle Ford": [("Western Gulf", "Eagle Ford")],
               "Haynesville": [("TX-LA-MS Salt Basin", "Haynesville-Bossier")], "Barnett": [("Ft. Worth", "Barnett")],
               "Anadarko": [("Anadarko", None)]}
ZONE_LABEL = {"ercot_weather_zones": "ZONE_NAME", "ercot_load_zones": "NAME"}
IDW_K, IDW_MAX_KM, IDW_POWER = 4, 75.0, 2.0


# ---------------------------------------------------------------- geometry helpers
def haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * np.arcsin(np.sqrt(a))


def bearing_deg(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    x = np.sin(lon2 - lon1) * np.cos(lat2)
    y = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(lon2 - lon1)
    return (np.degrees(np.arctan2(x, y)) + 360) % 360


def compass(b: float) -> str:
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int(((b + 22.5) % 360) // 45)]


def to_m(geom):
    return ops.transform(TO_M, geom)


def nearest_on(geoms_m: list, tree: STRtree, pt_ll: Point) -> tuple[float, float, int]:
    """Distance (km) and bearing from a lon/lat point to the nearest geometry (projected), and its index."""
    p = to_m(pt_ll)
    i = int(tree.nearest(p))
    g = geoms_m[i]
    q = ops.nearest_points(p, g)[1]
    lonq, latq = Transformer.from_crs(3083, 4326, always_xy=True).transform(q.x, q.y)
    return p.distance(g) / 1000.0, float(bearing_deg(pt_ll.x, pt_ll.y, lonq, latq)), i


# ---------------------------------------------------------------- sources
def write_geojson(name: str, feats: list[dict]) -> None:
    GJ.mkdir(parents=True, exist_ok=True)
    (GJ / f"{name}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, default=str))


@lru_cache
def tx_counties() -> pd.DataFrame:
    z = zipfile.ZipFile(RAW / "census_counties" / "cb_2020_us_county_5m.zip")
    get = lambda ext: io.BytesIO(z.read(next(n for n in z.namelist() if n.endswith(ext))))
    r = shapefile.Reader(shp=get(".shp"), dbf=get(".dbf"), shx=get(".shx"))
    fields = [f[0] for f in r.fields[1:]]
    rows = []
    for sr in r.shapeRecords():
        rec = dict(zip(fields, sr.record))
        if rec["STATEFP"] == "48":
            rows.append({"county": rec["NAME"].upper(), "geoid": rec["GEOID"], "geometry": shape(sr.shape.__geo_interface__)})
    return pd.DataFrame(rows)


def arcgis(name: str) -> list[dict]:
    p = RAW / "arcgis" / f"{name}.geojson"
    return json.loads(p.read_text())["features"] if p.exists() else []


@lru_cache
def stations() -> pd.DataFrame:
    rows = []
    for line in (RAW / "ghcnd_stations" / "ghcnd-stations.txt").read_text().splitlines():
        rows.append({"station": line[0:11], "lat": float(line[12:20]), "lon": float(line[21:30]),
                     "state": line[38:40], "name": line[41:71].strip()})
    return pd.DataFrame(rows).set_index("station")


@lru_cache
def daily_temps() -> pd.DataFrame:
    d = pd.read_csv(RAW / "ghcnd_tx_feb2021" / "daily_tmin_tmax.csv", parse_dates=["DATE"])
    d = d.rename(columns={"STATION": "station", "DATE": "day"})
    return d[["station", "day", "TMIN", "TMAX"]].dropna(subset=["TMIN"])


@lru_cache
def plants() -> pd.DataFrame:
    pf = pd.read_parquet(FLEET / "eia860_ercot_plant_fuel.parquet")
    p = pf.groupby("Plant Code").agg(plant_name=("Plant Name", "first"), county=("County", "first"),
                                     lat=("Latitude", "first"), lon=("Longitude", "first"),
                                     capacity_mw=("capacity_mw", "sum"),
                                     fuels=("fuel", lambda s: ",".join(sorted(set(s))))).reset_index()
    return p.dropna(subset=["lat", "lon"])


# ---------------------------------------------------------------- weather at each plant
def plant_daily_weather(pl: pd.DataFrame) -> pd.DataFrame:
    """Daily TMIN/TMAX at each plant: inverse-distance weighting (power 2) over the IDW_K nearest stations that
    reported that day within IDW_MAX_KM; if none, the nearest reporting station anywhere (flagged)."""
    t = daily_temps().join(stations()[["lat", "lon"]], on="station").dropna(subset=["lat"])
    rows = []
    for day, g in t.groupby("day"):
        slat, slon = g.lat.values, g.lon.values
        for _, p in pl.iterrows():
            d = haversine_km(p.lon, p.lat, slon, slat)
            order = np.argsort(d)
            near = order[d[order] <= IDW_MAX_KM][:IDW_K]
            fallback = len(near) == 0
            if fallback:
                near = order[:1]
            w = 1.0 / np.maximum(d[near], 1.0) ** IDW_POWER
            tmin = float(np.sum(w * g.TMIN.values[near]) / w.sum())
            tmax = g.TMAX.values[near]
            ok = ~np.isnan(tmax)
            tmax = float(np.sum(w[ok] * tmax[ok]) / w[ok].sum()) if ok.any() else np.nan
            rows.append({"plant_code": p["Plant Code"], "day": day, "tmin_f": tmin, "tmax_f": tmax,
                         "n_stations": len(near), "nearest_station_km": float(d[near[0]]), "fallback": fallback})
    w = pd.DataFrame(rows)
    w["cold_f"] = (32 - w.tmin_f).clip(lower=0)            # degrees below freezing at the daily low
    return w


# ---------------------------------------------------------------- per-plant features
def plant_features(pl: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    feats = pl.copy()
    pts = [Point(lo, la) for lo, la in zip(pl.lon, pl.lat)]
    # load centres and coast
    for k, (lo, la) in LOAD_CENTRES.items():
        feats[f"dist_{k.lower().replace(' ', '_')}_km"] = haversine_km(pl.lon, pl.lat, lo, la)
    lc = np.vstack([haversine_km(pl.lon, pl.lat, lo, la) for lo, la in LOAD_CENTRES.values()])
    feats["dist_load_centre_km"] = lc.min(axis=0)
    feats["nearest_load_centre"] = [list(LOAD_CENTRES)[i] for i in lc.argmin(axis=0)]
    coast = to_m(LineString(GULF_COAST))
    feats["dist_coast_km"] = [to_m(p).distance(coast) / 1000 for p in pts]
    # gas plays / basins
    plays = arcgis("shale_plays")
    groups = {}
    for f in plays:
        pr = f["properties"]
        for basin, rules in MAJOR_PLAYS.items():
            if any(pr.get("Basin") == b and (sp is None or pr.get("Shale_play") == sp) for b, sp in rules):
                groups.setdefault(basin, []).append(shape(f["geometry"]))
    info = {"plays_features": len(plays), "plays_grouped": {k: len(v) for k, v in groups.items()}}
    basin_geo = {k: to_m(ops.unary_union(v)) for k, v in groups.items()}
    for basin, g in basin_geo.items():
        key = basin.lower().replace(" ", "_")
        res = [nearest_on([g], STRtree([g]), p) for p in pts]
        feats[f"dist_{key}_km"] = [r[0] for r in res]
        feats[f"bearing_{key}_deg"] = [r[1] for r in res]
    if basin_geo:
        cols = [f"dist_{b.lower().replace(' ', '_')}_km" for b in basin_geo]
        feats["nearest_basin"] = feats[cols].idxmin(axis=1).str.replace("dist_", "").str.replace("_km", "")
        feats["dist_nearest_basin_km"] = feats[cols].min(axis=1)
        centroids = {b.lower().replace(" ", "_"): Transformer.from_crs(3083, 4326, always_xy=True).transform(g.centroid.x, g.centroid.y)
                     for b, g in basin_geo.items()}
        info["basin_centroids"] = centroids
    # point and line layers: processing plants, pipelines, transmission
    for name, col in (("gas_processing_plants", "processing"), ("gas_pipelines", "pipeline"),
                      ("transmission_lines_230kv", "transmission_230kv"), ("power_plants", "eia_atlas_plant")):
        layer = [shape(f["geometry"]) for f in arcgis(name) if f.get("geometry")]
        info[f"{name}_features"] = len(layer)
        if not layer:
            continue
        geoms_m = [to_m(g) for g in layer]
        tree = STRtree(geoms_m)
        res = [nearest_on(geoms_m, tree, p) for p in pts]
        feats[f"dist_{col}_km"] = [r[0] for r in res]
        feats[f"bearing_{col}_deg"] = [r[1] for r in res]
        if name == "gas_processing_plants":
            feats["processing_within_50km"] = [int(sum(to_m(p).distance(g) <= 50_000 for g in geoms_m)) for p in pts]
    # ERCOT zones (third-party polygons)
    for name, col in (("ercot_weather_zones", "weather_zone"), ("ercot_load_zones", "load_zone")):
        polys = [(f["properties"], shape(f["geometry"])) for f in arcgis(name) if f.get("geometry")]
        info[f"{name}_features"] = len(polys)
        if not polys:
            continue
        label_key = ZONE_LABEL[name]
        info[f"{name}_label_field"] = label_key
        zone, border = [], []
        for p in pts:
            hit = next((pr for pr, g in polys if g.contains(p)), None)
            zone.append(None if hit is None else str(hit.get(label_key)))
            pm = to_m(p)
            border.append(min(pm.distance(to_m(g).boundary) for _, g in polys) / 1000)
        feats[col] = zone
        feats[f"dist_{col}_border_km"] = border
    for c in [c for c in feats.columns if c.startswith("bearing_")]:
        feats[c.replace("bearing_", "dir_").replace("_deg", "")] = feats[c].map(lambda b: compass(b) if pd.notna(b) else None)
        feats[c.replace("_deg", "_sin")] = np.sin(np.radians(feats[c]))
        feats[c.replace("_deg", "_cos")] = np.cos(np.radians(feats[c]))
    return feats, info


# ---------------------------------------------------------------- GeoJSON outputs
def write_layers(pl: pd.DataFrame, feats: pd.DataFrame, weather: pd.DataFrame) -> None:
    c = tx_counties()
    write_geojson("tx_counties", [{"type": "Feature", "geometry": mapping(r.geometry),
                                   "properties": {"county": r.county, "geoid": r.geoid}} for r in c.itertuples()])
    props = feats.drop(columns=["lat", "lon"]).replace({np.nan: None})
    write_geojson("ercot_plants", [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                                    "properties": props.iloc[i].to_dict()} for i, r in enumerate(feats.itertuples())])
    st = stations().loc[sorted(set(daily_temps().station) & set(stations().index))]
    storm = daily_temps()[daily_temps().day.between("2021-02-14", "2021-02-19")].groupby("station").TMIN.min()
    write_geojson("weather_stations", [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                                        "properties": {"station": sid, "name": r.name, "state": r.state,
                                                       "storm_min_tmin_f": None if pd.isna(storm.get(sid)) else float(storm.get(sid))}}
                                       for sid, r in st.iterrows()])
    for name in ("sedimentary_basins", "shale_plays", "gas_processing_plants", "gas_pipelines", "transmission_lines_230kv",
                 "ercot_weather_zones", "ercot_load_zones"):
        fs = arcgis(name)
        if fs:
            write_geojson(name, fs)


def main() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    pl = plants()
    feats, info = plant_features(pl)
    weather = plant_daily_weather(pl)
    feats.to_parquet(OUT / "plant_features.parquet", index=False)
    weather.to_parquet(OUT / "plant_daily_weather.parquet", index=False)
    write_layers(pl, feats, weather)
    info |= {"plants": len(pl), "weather_rows": len(weather),
             "weather_fallback_share": float(weather.fallback.mean()),
             "median_nearest_station_km": float(weather.nearest_station_km.median()),
             "stations_reporting": int(daily_temps().station.nunique())}
    (OUT / "spatial_report.json").write_text(json.dumps(info, indent=1, default=str))
    return info


if __name__ == "__main__":
    print(json.dumps(main(), indent=1, default=str))
