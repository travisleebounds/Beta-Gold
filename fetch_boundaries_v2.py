#!/usr/bin/env python3
"""
fetch_boundaries_v2.py — Download REAL district boundaries for the IDOT Dashboard.

Downloads Census Bureau cartographic boundary shapefiles and converts to GeoJSON.
Covers:
  - 119th Congressional Districts (17 districts)
  - IL State House Districts (118 districts)
  - IL State Senate Districts (59 districts)

Requirements: pip install geopandas requests
Output: data/boundaries/*.geojson + il_congressional_boundaries.json (for app.py)
"""

import json
import os
import sys
import zipfile
import tempfile
import requests
import geopandas as gpd

OUT_DIR = "data/boundaries"
os.makedirs(OUT_DIR, exist_ok=True)

# Census Bureau Cartographic Boundary Files (500k resolution — good balance of detail vs size)
# Illinois FIPS = 17
SOURCES = {
    "congressional": {
        "url": "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_17_cd119_500k.zip",
        "district_field": "CD119FP",  # Field containing district number
        "key_prefix": "IL",
        "key_format": "IL-{:02d}",
        "name_format": "Illinois Congressional District {}",
        "geography": "congressional",
        "max_district": 17,
    },
    "il_house": {
        "url": "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_17_sldl_500k.zip",
        "district_field": "SLDLST",
        "key_prefix": "IL-H",
        "key_format": "IL-H-{:03d}",
        "name_format": "Illinois House District {}",
        "geography": "il_house",
        "max_district": 118,
    },
    "il_senate": {
        "url": "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_17_sldu_500k.zip",
        "district_field": "SLDUST",
        "key_prefix": "IL-S",
        "key_format": "IL-S-{:03d}",
        "name_format": "Illinois Senate District {}",
        "geography": "il_senate",
        "max_district": 59,
    },
}

# ArcGIS fallback endpoints
ARCGIS_FALLBACKS = {
    "congressional": [
        {
            "url": "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_119th_Congressional_Districts/FeatureServer/0/query",
            "where": "STATE_ABBR='IL'",
            "district_field": "DISTRICTID",
            "parse": lambda v: int(str(v).strip()[-2:]),
        },
        {
            "url": "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Legislative/MapServer/8/query",
            "where": "STATE='17'",
            "district_field": "BASENAME",
            "parse": lambda v: int(str(v).strip()),
        },
    ],
    "il_house": [
        {
            "url": "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Legislative/MapServer/10/query",
            "where": "STATE='17'",
            "district_field": "BASENAME",
            "parse": lambda v: int(str(v).strip()),
        },
    ],
    "il_senate": [
        {
            "url": "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Legislative/MapServer/9/query",
            "where": "STATE='17'",
            "district_field": "BASENAME",
            "parse": lambda v: int(str(v).strip()),
        },
    ],
}


def download_and_extract_shapefile(url, tmpdir):
    """Download a zip file from Census and extract shapefile."""
    print(f"  📥 Downloading: {url.split('/')[-1]}")
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()

    zip_path = os.path.join(tmpdir, "download.zip")
    with open(zip_path, "wb") as f:
        f.write(resp.content)
    print(f"  📦 Downloaded {len(resp.content) / 1024:.0f} KB")

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(tmpdir)

    # Find the .shp file
    shp_files = [f for f in os.listdir(tmpdir) if f.endswith(".shp")]
    if not shp_files:
        raise FileNotFoundError("No .shp file found in archive")

    return os.path.join(tmpdir, shp_files[0])


def simplify_geometry(gdf, tolerance=0.002):
    """Simplify geometries to reduce file size while keeping good detail."""
    gdf = gdf.copy()
    gdf["geometry"] = gdf["geometry"].simplify(tolerance, preserve_topology=True)
    return gdf


def fetch_via_shapefile(geo_type, config):
    """Primary method: download Census shapefile and convert to GeoJSON."""
    print(f"\n{'='*50}")
    print(f"📍 Fetching {geo_type} boundaries via Census shapefiles...")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            shp_path = download_and_extract_shapefile(config["url"], tmpdir)
            gdf = gpd.read_file(shp_path)

            # Ensure WGS84
            if gdf.crs and gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs(epsg=4326)

            # Simplify to reduce size
            gdf = simplify_geometry(gdf)

            count = 0
            field = config["district_field"]

            for _, row in gdf.iterrows():
                raw_val = row.get(field, "")
                if raw_val in (None, "", "ZZ"):
                    continue

                try:
                    dist_int = int(str(raw_val).strip().lstrip("0") or "0")
                except ValueError:
                    continue

                if dist_int < 1 or dist_int > config["max_district"]:
                    continue

                key = config["key_format"].format(dist_int)
                out_path = os.path.join(OUT_DIR, f"{key}.geojson")

                geojson_feature = {
                    "type": "Feature",
                    "properties": {
                        "district_key": key,
                        "district_num": dist_int,
                        "name": config["name_format"].format(dist_int),
                        "geography": config["geography"],
                    },
                    "geometry": json.loads(gpd.GeoSeries([row.geometry]).to_json())["features"][0]["geometry"],
                }

                with open(out_path, "w") as f:
                    json.dump(geojson_feature, f)
                count += 1

            print(f"  ✅ {count}/{config['max_district']} {geo_type} districts saved")
            return count

    except Exception as e:
        print(f"  ❌ Shapefile method failed: {e}")
        return 0


def fetch_via_arcgis(geo_type, config, fallbacks):
    """Fallback: query ArcGIS REST API."""
    print(f"  🔄 Trying ArcGIS fallback for {geo_type}...")

    for endpoint in fallbacks:
        try:
            params = {
                "where": endpoint["where"],
                "outFields": "*",
                "outSR": "4326",
                "f": "geojson",
                "resultRecordCount": 200,
                "returnGeometry": "true",
            }
            resp = requests.get(endpoint["url"], params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            features = data.get("features", [])

            if not features:
                continue

            count = 0
            for feat in features:
                props = feat.get("properties", {})
                raw_val = props.get(endpoint["district_field"], "")

                try:
                    dist_int = endpoint["parse"](raw_val)
                except (ValueError, TypeError):
                    continue

                if dist_int < 1 or dist_int > config["max_district"]:
                    continue

                key = config["key_format"].format(dist_int)
                out_path = os.path.join(OUT_DIR, f"{key}.geojson")

                geojson_feature = {
                    "type": "Feature",
                    "properties": {
                        "district_key": key,
                        "district_num": dist_int,
                        "name": config["name_format"].format(dist_int),
                        "geography": config["geography"],
                    },
                    "geometry": feat.get("geometry"),
                }

                with open(out_path, "w") as f:
                    json.dump(geojson_feature, f)
                count += 1

            if count > 0:
                print(f"  ✅ {count}/{config['max_district']} {geo_type} districts saved (ArcGIS)")
                return count

        except Exception as e:
            print(f"  ⚠️  ArcGIS endpoint failed: {e}")
            continue

    print(f"  ❌ All ArcGIS fallbacks failed for {geo_type}")
    return 0


def build_app_boundaries_file():
    """
    Build il_congressional_boundaries.json that app.py can load directly.
    This replaces the rectangular boundaries in the DISTRICTS dict.
    """
    print("\n📦 Building il_congressional_boundaries.json for app.py...")

    boundaries = {}
    for i in range(1, 18):
        key = f"IL-{i:02d}"
        geojson_path = os.path.join(OUT_DIR, f"{key}.geojson")

        if os.path.exists(geojson_path):
            with open(geojson_path) as f:
                feat = json.load(f)
            boundaries[key] = {
                "type": "Feature",
                "geometry": feat["geometry"],
                "properties": feat["properties"],
            }
            print(f"  ✅ {key}")
        else:
            print(f"  ⚠️  {key} — not found, will use rectangle fallback")

    out_path = "il_congressional_boundaries.json"
    with open(out_path, "w") as f:
        json.dump(boundaries, f)
    print(f"\n  📄 Wrote {out_path} ({len(boundaries)} districts)")

    return len(boundaries)


def main():
    print("=" * 60)
    print("IDOT Dashboard — Boundary Fetcher v2")
    print("=" * 60)
    print("Uses Census Bureau shapefiles (primary) + ArcGIS (fallback)")

    results = {}

    for geo_type, config in SOURCES.items():
        # Try shapefile first
        count = fetch_via_shapefile(geo_type, config)

        # If shapefile failed, try ArcGIS
        if count == 0 and geo_type in ARCGIS_FALLBACKS:
            count = fetch_via_arcgis(geo_type, config, ARCGIS_FALLBACKS[geo_type])

        results[geo_type] = count

    # Build the app-ready boundaries file for congressional districts
    app_count = build_app_boundaries_file()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"  Congressional: {results.get('congressional', 0)}/17")
    print(f"  IL House:      {results.get('il_house', 0)}/118")
    print(f"  IL Senate:     {results.get('il_senate', 0)}/59")
    total = sum(results.values())
    print(f"  Total files:   {total}")
    print(f"  Output dir:    {OUT_DIR}/")
    print(f"  App file:      il_congressional_boundaries.json ({app_count} districts)")
    print("=" * 60)

    if total == 0:
        print("\n⚠️  No boundaries fetched!")
        print("Check your internet connection and try again.")
        print("The Census Bureau servers may be temporarily unavailable.")
        sys.exit(1)


if __name__ == "__main__":
    main()
