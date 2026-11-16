"""Download the public data for the causal-ML and spatial work. Each source goes into its own folder under data/raw/
(kept out of git); data/raw/manifest.json records URL, retrieval time, size and sha256 for every file.

    uv run python scripts/fetch_spatial.py            # skips files that already exist, then checks every sha256

Sources (all public, no key):
  eia860_2020        EIA-860 2020 (generators, plants with lat/lon)                     eia.gov archive
  census_counties    Census cartographic boundary counties 2020, 1:5m (shapefile)        census.gov
  ghcnd_stations     GHCN-Daily station list (coordinates)                                ncei.noaa.gov
  ghcnd_tx_feb2021   GHCN-Daily TMIN/TMAX, Feb 1-28 2021, stations in and near Texas      NCEI access API
  arcgis/*           GeoJSON layers clipped to a Texas bounding box:
                     EIA sedimentary basins and tight-oil/shale-gas plays (EIA's own org);
                     gas processing plants, gas pipelines, power plants, transmission lines (>= 230 kV): EIA/HIFLD
                     layers as mirrored by Esri's Federal User Community org;
                     ERCOT weather zones and load zones: third-party user layers (provenance weaker, see ASSUMPTIONS S-items)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
import urllib.parse
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
UA = {"User-Agent": "causal-kg research script (public data download)"}
TX_BBOX = (-106.7, 25.8, -93.5, 36.6)          # xmin, ymin, xmax, ymax (WGS84)
NEAR_TX_STATES = ("TX", "NM", "OK", "LA", "AR")

FILES = {
    "eia860_2020/eia8602020.zip": "https://www.eia.gov/electricity/data/eia860/archive/xls/eia8602020.zip",
    "census_counties/cb_2020_us_county_5m.zip": "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_county_5m.zip",
    "ghcnd_stations/ghcnd-stations.txt": "https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt",
}
FED = "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services"
EIA = "https://services7.arcgis.com/FGr1D95XCGALKXqM/arcgis/rest/services"
LAYERS = {
    "sedimentary_basins": (f"{EIA}/SedimentaryBasins_US_EIA/FeatureServer/109", "1=1"),
    "shale_plays": (f"{EIA}/TightOil_ShaleGas_Plays_Lower48_EIA/FeatureServer/0", "1=1"),
    # the Federal User Community copy needs a token; this public copy keeps EIA's fields (capacity, source)
    "gas_processing_plants": ("https://services2.arcgis.com/wdQEqhSQSuYA89VW/arcgis/rest/services/"
                              "NaturalGas_ProcessingPlants_US_EIA/FeatureServer/0", "1=1"),
    "gas_pipelines": (f"{FED}/Natural_Gas_Interstate_and_Intrastate_Pipelines_1/FeatureServer/0", "1=1"),
    "power_plants": (f"{FED}/Power_Plants_in_the_US/FeatureServer/0", "1=1"),
    "transmission_lines_230kv": (f"{FED}/US_Electric_Power_Transmission_Lines/FeatureServer/0", "VOLTAGE >= 230"),
    "ercot_weather_zones": ("https://services.arcgis.com/l483wZ43iEK8alrh/arcgis/rest/services/"
                            "ERCOT_Weather_Zone_Peak_Forecast_v1/FeatureServer/0", "1=1"),
    "ercot_load_zones": ("https://services3.arcgis.com/fwwoCWVtaahwlvxO/arcgis/rest/services/ERCOT_Load_Zones/FeatureServer/7", "1=1"),
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def manifest_add(rel: str, url: str, extra: dict | None = None) -> None:
    """Record a new download. A file already in the committed manifest is checked against it, never overwritten:
    live layers change, and the manifest documents the inputs behind the published results."""
    mp = RAW / "manifest.json"
    m = json.loads(mp.read_text()) if mp.exists() else {}
    p = RAW / rel
    got = sha256(p)
    want = m.get(rel, {}).get("sha256")
    if want:
        if want != got:
            print(f"WARNING {rel}: sha256 {got[:12]} differs from the manifest ({want[:12]}). The source has changed "
                  "since the published run, so downstream results may differ.")
        return
    m[rel] = {"url": url, "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "bytes": p.stat().st_size, "sha256": got, **(extra or {})}
    mp.write_text(json.dumps(m, indent=1, sort_keys=True))


def verify() -> bool:
    """Compare every file in the manifest with the local copy. True if all are present and identical."""
    m = json.loads((RAW / "manifest.json").read_text())
    ok = True
    for rel, rec in sorted(m.items()):
        p = RAW / rel
        status = "missing" if not p.exists() else ("ok" if sha256(p) == rec["sha256"] else "CHANGED")
        ok &= status == "ok"
        print(f"{status:8s} {rel}")
    return ok


def download(rel: str, url: str) -> None:
    p = RAW / rel
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, headers=UA, timeout=300, stream=True) as r:
        r.raise_for_status()
        tmp = p.with_suffix(p.suffix + ".part")
        with tmp.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        tmp.rename(p)
    manifest_add(rel, url)
    print("downloaded", rel, p.stat().st_size)


def arcgis_layer(name: str, url: str, where: str) -> None:
    """All features intersecting the Texas bounding box as GeoJSON, paged by resultOffset."""
    rel = f"arcgis/{name}.geojson"
    p = RAW / rel
    if p.exists():
        return
    meta = requests.get(url, params={"f": "json"}, headers=UA, timeout=120).json()
    page = int(meta.get("maxRecordCount") or 1000)
    feats, offset = [], 0
    while True:
        params = {"where": where, "geometry": ",".join(map(str, TX_BBOX)), "geometryType": "esriGeometryEnvelope",
                  "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "outSR": 4326,
                  "f": "geojson", "resultOffset": offset, "resultRecordCount": page}
        r = requests.get(f"{url}/query", params=params, headers=UA, timeout=300)
        r.raise_for_status()
        js = r.json()
        if "error" in js:
            raise RuntimeError(f"{name}: {js['error']}")
        got = js.get("features", [])
        feats += got
        if len(got) < page and not js.get("exceededTransferLimit") and not js.get("properties", {}).get("exceededTransferLimit"):
            break
        offset += len(got)
        if not got:
            break
        time.sleep(0.3)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    manifest_add(rel, f"{url}/query?where={urllib.parse.quote(where)}&bbox=texas",
                 {"features": len(feats), "layer_name": meta.get("name"), "copyright": meta.get("copyrightText"),
                  "description": (meta.get("description") or "")[:300]})
    print("layer", name, len(feats))


def tx_stations() -> list[str]:
    """GHCN-Daily stations in Texas and neighbouring states (fixed-width ghcnd-stations.txt)."""
    out = []
    for line in (RAW / "ghcnd_stations" / "ghcnd-stations.txt").read_text().splitlines():
        sid, lat, lon, state = line[0:11], float(line[12:20]), float(line[21:30]), line[38:40]
        if sid.startswith("US1"):                 # CoCoRaHS gauges: precipitation only, no temperature
            continue
        if state in NEAR_TX_STATES and TX_BBOX[0] - 1 <= lon <= TX_BBOX[2] + 1 and TX_BBOX[1] - 1 <= lat <= TX_BBOX[3] + 1:
            out.append(sid)
    return out


def ghcnd_daily(batch: int = 50) -> None:
    rel = "ghcnd_tx_feb2021/daily_tmin_tmax.csv"
    p = RAW / rel
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    ids = tx_stations()
    url = "https://www.ncei.noaa.gov/access/services/data/v1"
    parts, header = [], None
    for i in range(0, len(ids), batch):
        params = {"dataset": "daily-summaries", "stations": ",".join(ids[i:i + batch]), "startDate": "2021-02-01",
                  "endDate": "2021-02-28", "dataTypes": "TMIN,TMAX", "units": "standard", "format": "csv"}
        for attempt in range(3):
            r = requests.get(url, params=params, headers=UA, timeout=300)
            if r.ok:
                break
            time.sleep(3 * (attempt + 1))
        r.raise_for_status()
        lines = r.text.strip().splitlines()
        if lines:
            header = header or lines[0]
            parts += lines[1:]
        time.sleep(0.25)
    if header is None:
        raise RuntimeError("GHCN-Daily: the access API returned no rows for any station batch")
    p.write_text("\n".join([header] + parts) + "\n")
    manifest_add(rel, url + "?dataset=daily-summaries&dataTypes=TMIN,TMAX&2021-02-01..28",
                 {"stations_requested": len(ids), "rows": len(parts)})
    print("ghcnd rows", len(parts), "stations requested", len(ids))


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for rel, url in FILES.items():
        download(rel, url)
    ghcnd_daily()
    for name, (url, where) in LAYERS.items():
        try:
            arcgis_layer(name, url, where)
        except Exception as e:                    # report and continue; verify() lists what's missing
            print("FAILED", name, e)
    if not verify():
        print("Some inputs are missing or differ from data/raw/manifest.json (see above).")


if __name__ == "__main__":
    main()
