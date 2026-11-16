import pandas as pd
import pytest

from kg.fleet import match, name_key
from kg.spatial import bearing_deg, compass, haversine_km


def test_haversine_and_bearing():
    # Houston -> Dallas: about 362 km, roughly north-northwest
    d = haversine_km(-95.369, 29.760, -96.797, 32.777)
    assert d == pytest.approx(362, abs=5)
    b = bearing_deg(-95.369, 29.760, -96.797, 32.777)
    assert 330 < b < 345
    assert compass(0) == "N" and compass(90) == "E" and compass(225) == "SW"
    assert haversine_km(-97, 30, -97, 30) == 0


def test_name_key_joins_initials_and_drops_generic_words():
    assert name_key("W A Parish") == "wa parish"
    assert name_key("WA PARISH") == "wa parish"
    assert name_key("Capricorn Ridge Wind LLC") == "capricorn ridge"


def _pf(rows):
    df = pd.DataFrame(rows, columns=["plant_fuel_id", "Plant Name", "county_key", "fuel", "capacity_mw", "lmp_stations"])
    df["name_key"] = df["Plant Name"].map(name_key)
    return df


def test_station_matching_rules():
    pf = _pf([("1:gas", "W A Parish", "FORT BEND", "gas", 1176, ["WAP"]),
              ("2:coal", "J K Spruce", "BEXAR", "coal", 1345, []),
              ("3:coal", "J T Deely", "BEXAR", "coal", 840, []),
              ("4:wind", "Electra Wind Farm", "WILBARGER", "wind", 230, []),
              ("5:wind", "Blue Summit Wind LLC", "WILBARGER", "wind", 135, [])])
    st = pd.DataFrame([("WAP", "gas", "WA PARISH", "FORT BEND", 1161),          # LMP code
                       ("CALAVERS", "coal", "CALAVERAS", "BEXAR", 1345),       # capacity in county within 3%
                       ("DIGBY", "wind", "DIGBY SUBSTATION", "WILBARGER", 230),
                       ("BLSUMMIT", "wind", "BLUE SUMMIT", "WILBARGER", 135),  # name
                       ("NOPE", "gas", "PASGEN", "HARRIS", 521)],              # nothing to match
                      columns=["station", "fuel", "station_name", "county", "hsl_mw"])
    st["county_key"] = st.county
    st["name_key"] = st.station_name.map(name_key)
    m = match(st, pf).set_index("station")
    assert m.loc["WAP", "match"] == "lmp_code" and m.loc["WAP", "plant_fuel_id"] == "1:gas"
    assert m.loc["CALAVERS", "plant_fuel_id"] == "2:coal" and m.loc["CALAVERS", "match"] == "capacity_county"
    assert m.loc["DIGBY", "plant_fuel_id"] == "4:wind"
    assert m.loc["BLSUMMIT", "match"] == "name"
    assert pd.isna(m.loc["NOPE", "match"])
