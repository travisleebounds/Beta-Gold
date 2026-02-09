#!/usr/bin/env python3
"""
fetch_boundaries.py — Download district boundary GeoJSON for all 3 geographies.

Sources:
  - Congressional: US Census Bureau TIGER/Line (119th Congress)
  - IL House: Illinois redistricting shapefiles via Census
  - IL Senate: Illinois redistricting shapefiles via Census

Output: data/boundaries/{district_key}.geojson
"""

import json
import os
import sys
import time
import requests

OUT_DIR = "data/boundaries"
os.makedirs(OUT_DIR, exist_ok=True)

# ─── Census Bureau ArcGIS endpoints ───────────────────────────────────
# These are the public TIGER/Line feature services for 119th Congress boundaries
# and IL state legislative districts.

# 119th Congressional Districts (all states — we filter to IL STATEFP=17)
CONGRESS_URL = (
    "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
    "USA_119th_Congressional_Districts/FeatureServer/0/query"
)

# IL State House districts (SLDL = State Legislative District Lower)
# Census TIGER cartographic boundaries
IL_HOUSE_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/"
    "TIGERweb/Legislative/MapServer/18/query"
)

# IL State Senate districts (SLDU = State Legislative District Upper)
IL_SENATE_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/"
    "TIGERweb/Legislative/MapServer/16/query"
)

# Fallback: Census cartographic boundary GeoJSON download (simpler, pre-built)
CENSUS_CD_GEOJSON = (
    "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_17_cd119_500k.zip"
)
CENSUS_SLDL_GEOJSON = (
    "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_17_sldl_500k.zip"
)
CENSUS_SLDU_GEOJSON = (
    "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_17_sldu_500k.zip"
)


def fetch_arcgis_geojson(url, where_clause, out_fields="*", max_records=200):
    """Query an ArcGIS FeatureServer and return GeoJSON features."""
    params = {
        "where": where_clause,
        "outFields": out_fields,
        "outSR": "4326",
        "f": "geojson",
        "resultRecordCount": max_records,
        "returnGeometry": "true"
    }
    try:
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data.get("features", [])
    except Exception as e:
        print(f"  ⚠️  ArcGIS query failed: {e}")
        return []


def fetch_congressional():
    """Fetch IL congressional district boundaries (119th Congress)."""
    print("\n📍 Fetching Congressional District boundaries...")

    # Try the Esri Living Atlas service first
    features = fetch_arcgis_geojson(
        CONGRESS_URL,
        where_clause="STATE_ABBR='IL'",
        out_fields="DISTRICTID,NAME,PARTY,STATE_ABBR,CDFIPS",
        max_records=20
    )

    if not features:
        # Fallback: try a different known endpoint
        alt_url = (
            "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
            "USA_119th_Congressional_Districts_v/FeatureServer/0/query"
        )
        features = fetch_arcgis_geojson(
            alt_url,
            where_clause="STATE_ABBR='IL'",
            out_fields="*",
            max_records=20
        )

    if not features:
        print("  ❌ Could not fetch congressional boundaries from ArcGIS.")
        print("  💡 Fallback: download manually from Census Bureau:")
        print(f"     {CENSUS_CD_GEOJSON}")
        print("     Then convert with: python convert_shp_to_geojson.py")
        return 0

    count = 0
    for feat in features:
        props = feat.get("properties", {})
        # Try various field names for district number
        dist_num = (
            props.get("CDFIPS") or
            props.get("DISTRICTID", "")[-2:] or
            props.get("CD119FP") or
            props.get("DISTRICT", "")
        )
        try:
            dist_int = int(str(dist_num).strip().lstrip("0") or "0")
        except ValueError:
            continue

        if dist_int < 1 or dist_int > 17:
            continue

        key = f"US-IL-CD-{dist_int:02d}"
        out_path = os.path.join(OUT_DIR, f"{key}.geojson")

        geojson = {
            "type": "Feature",
            "properties": {
                "district_key": key,
                "district_num": dist_int,
                "name": f"Illinois Congressional District {dist_int}",
                "geography": "congressional"
            },
            "geometry": feat.get("geometry")
        }

        with open(out_path, "w") as f:
            json.dump(geojson, f)
        count += 1
        print(f"  ✅ {key}")

    return count


def fetch_il_house():
    """Fetch IL House district boundaries."""
    print("\n📍 Fetching IL House District boundaries...")

    features = fetch_arcgis_geojson(
        IL_HOUSE_URL,
        where_clause="STATE='17'",
        out_fields="*",
        max_records=200
    )

    if not features:
        # Try alternate TIGERweb endpoint
        alt_url = (
            "https://tigerweb.geo.census.gov/arcgis/rest/services/"
            "TIGERweb/tigerWMS_Current/MapServer/22/query"
        )
        features = fetch_arcgis_geojson(
            alt_url,
            where_clause="STATE='17'",
            out_fields="*",
            max_records=200
        )

    if not features:
        print("  ❌ Could not fetch IL House boundaries.")
        print(f"  💡 Download manually: {CENSUS_SLDL_GEOJSON}")
        return 0

    count = 0
    for feat in features:
        props = feat.get("properties", {})
        dist_num_str = (
            props.get("SLDLST") or
            props.get("DISTRICT") or
            props.get("NAME", "")
        )
        try:
            dist_int = int(str(dist_num_str).strip().lstrip("0") or "0")
        except ValueError:
            continue

        if dist_int < 1 or dist_int > 118:
            continue

        key = f"IL-H-{dist_int:03d}"
        out_path = os.path.join(OUT_DIR, f"{key}.geojson")

        geojson = {
            "type": "Feature",
            "properties": {
                "district_key": key,
                "district_num": dist_int,
                "name": f"Illinois House District {dist_int}",
                "geography": "il_house"
            },
            "geometry": feat.get("geometry")
        }

        with open(out_path, "w") as f:
            json.dump(geojson, f)
        count += 1

    print(f"  ✅ {count} IL House districts saved")
    return count


def fetch_il_senate():
    """Fetch IL Senate district boundaries."""
    print("\n📍 Fetching IL Senate District boundaries...")

    features = fetch_arcgis_geojson(
        IL_SENATE_URL,
        where_clause="STATE='17'",
        out_fields="*",
        max_records=100
    )

    if not features:
        alt_url = (
            "https://tigerweb.geo.census.gov/arcgis/rest/services/"
            "TIGERweb/tigerWMS_Current/MapServer/20/query"
        )
        features = fetch_arcgis_geojson(
            alt_url,
            where_clause="STATE='17'",
            out_fields="*",
            max_records=100
        )

    if not features:
        print("  ❌ Could not fetch IL Senate boundaries.")
        print(f"  💡 Download manually: {CENSUS_SLDU_GEOJSON}")
        return 0

    count = 0
    for feat in features:
        props = feat.get("properties", {})
        dist_num_str = (
            props.get("SLDUST") or
            props.get("DISTRICT") or
            props.get("NAME", "")
        )
        try:
            dist_int = int(str(dist_num_str).strip().lstrip("0") or "0")
        except ValueError:
            continue

        if dist_int < 1 or dist_int > 59:
            continue

        key = f"IL-S-{dist_int:03d}"
        out_path = os.path.join(OUT_DIR, f"{key}.geojson")

        geojson = {
            "type": "Feature",
            "properties": {
                "district_key": key,
                "district_num": dist_int,
                "name": f"Illinois Senate District {dist_int}",
                "geography": "il_senate"
            },
            "geometry": feat.get("geometry")
        }

        with open(out_path, "w") as f:
            json.dump(geojson, f)
        count += 1

    print(f"  ✅ {count} IL Senate districts saved")
    return count


def main():
    print("=" * 60)
    print("IDOT Dashboard — Boundary Fetcher")
    print("=" * 60)

    cd_count = fetch_congressional()
    time.sleep(1)
    house_count = fetch_il_house()
    time.sleep(1)
    senate_count = fetch_il_senate()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"  Congressional: {cd_count}/17")
    print(f"  IL House:      {house_count}/118")
    print(f"  IL Senate:     {senate_count}/59")
    print(f"  Total files:   {cd_count + house_count + senate_count}")
    print(f"  Output dir:    {OUT_DIR}/")
    print("=" * 60)

    if cd_count + house_count + senate_count == 0:
        print("\n⚠️  No boundaries fetched. This likely means the ArcGIS")
        print("   endpoints have changed. Try the Census shapefile fallback:")
        print("   pip install geopandas")
        print("   python fetch_boundaries_shp.py")
        sys.exit(1)


if __name__ == "__main__":
    main()
