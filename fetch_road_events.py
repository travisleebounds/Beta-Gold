#!/usr/bin/env python3
"""
fetch_road_events.py — Query IDOT ArcGIS layers, intersect with district
boundaries, normalize into RoadEvent schema, score, and save per-district JSON.

IDOT ArcGIS Open Data layers:
  - Road Construction (points)
  - Road Closures (points)
  - Road Restrictions (points — obstructions)

For each district boundary in data/boundaries/, we query each layer using
esriSpatialRelIntersects, normalize results, and write:
  data/road/{district_key}.json

Also builds: data/road/US-IL-SEN.json (statewide top-5 aggregate)

Usage:
  python fetch_road_events.py                  # all districts
  python fetch_road_events.py US-IL-CD-05      # single district
  python fetch_road_events.py --statewide-only # just the senator aggregate
"""

import json
import os
import sys
import glob
import time
import hashlib
from datetime import datetime, timezone
from collections import Counter

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

# ─── Configuration ────────────────────────────────────────────────────

BOUNDARY_DIR = "data/boundaries"
ROAD_DIR = "data/road"
os.makedirs(ROAD_DIR, exist_ok=True)

# IDOT ArcGIS layer endpoints (public, no auth required)
# These are the Hub/OpenData FeatureServer endpoints
LAYERS = {
    "construction": {
        "url": "https://services2.arcgis.com/aIrBD8yn1TDTEXoz/arcgis/rest/services/Road_Construction_Public/FeatureServer/0/query",
        "type": "construction",
        "fallback_url": "https://gis-idot.opendata.arcgis.com/datasets/road-construction-public.geojson"
    },
    "closures": {
        "url": "https://services2.arcgis.com/aIrBD8yn1TDTEXoz/arcgis/rest/services/Road_Closures/FeatureServer/0/query",
        "type": "closure",
        "fallback_url": "https://gis-idot.opendata.arcgis.com/datasets/d692355520e94d39a028f79248b75ef7_0.geojson"
    },
    "restrictions": {
        "url": "https://services2.arcgis.com/aIrBD8yn1TDTEXoz/arcgis/rest/services/Road_Restrictions/FeatureServer/0/query",
        "type": "restriction",
        "fallback_url": None
    }
}

# If the FeatureServer endpoints don't work, try these alternate patterns
ALT_LAYER_PATTERNS = [
    "https://services2.arcgis.com/aIrBD8yn1TDTEXoz/ArcGIS/rest/services/{name}/FeatureServer/0/query",
    "https://services2.arcgis.com/aIrBD8yn1TDTEXoz/arcgis/rest/services/{name}_View/FeatureServer/0/query",
]

PAGE_SIZE = 1000
MAX_PAGES = 20


# ─── ArcGIS Query Helpers ─────────────────────────────────────────────

def arcgis_query_paged(url, geometry_json=None, where="1=1", out_fields="*",
                       spatial_rel="esriSpatialRelIntersects"):
    """
    Paged ArcGIS FeatureServer query. Returns list of features (GeoJSON).
    Uses resultOffset/resultRecordCount for paging.
    """
    all_features = []
    offset = 0

    for page in range(MAX_PAGES):
        params = {
            "where": where,
            "outFields": out_fields,
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "returnGeometry": "true",
        }

        if geometry_json:
            params["geometry"] = json.dumps(geometry_json)
            params["geometryType"] = "esriGeometryPolygon"
            params["spatialRel"] = spatial_rel
            params["inSR"] = "4326"

        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    ⚠️  Query error at offset {offset}: {e}")
            break

        if "error" in data:
            print(f"    ⚠️  ArcGIS error: {data['error'].get('message', 'unknown')}")
            break

        features = data.get("features", [])
        if not features:
            break

        all_features.extend(features)
        offset += PAGE_SIZE

        # Check if we got fewer than page size (last page)
        if len(features) < PAGE_SIZE:
            break

        time.sleep(0.3)  # Rate limit courtesy

    return all_features


def arcgis_count(url, geometry_json=None, where="1=1"):
    """Get feature count for a layer (fast sanity check)."""
    params = {
        "where": where,
        "returnCountOnly": "true",
        "f": "json",
    }
    if geometry_json:
        params["geometry"] = json.dumps(geometry_json)
        params["geometryType"] = "esriGeometryPolygon"
        params["spatialRel"] = "esriSpatialRelIntersects"
        params["inSR"] = "4326"

    try:
        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()
        return data.get("count", -1)
    except:
        return -1


# ─── GeoJSON helpers ──────────────────────────────────────────────────

def load_boundary(district_key):
    """Load a district boundary GeoJSON file, return Esri-compatible geometry."""
    path = os.path.join(BOUNDARY_DIR, f"{district_key}.geojson")
    if not os.path.exists(path):
        return None

    with open(path) as f:
        feat = json.load(f)

    geom = feat.get("geometry", {})
    if geom.get("type") == "Polygon":
        return {
            "rings": geom["coordinates"],
            "spatialReference": {"wkid": 4326}
        }
    elif geom.get("type") == "MultiPolygon":
        # Flatten MultiPolygon to rings
        rings = []
        for polygon in geom["coordinates"]:
            rings.extend(polygon)
        return {
            "rings": rings,
            "spatialReference": {"wkid": 4326}
        }
    return None


def bbox_from_geojson(district_key):
    """Get bounding box from a district boundary (fallback for spatial query)."""
    path = os.path.join(BOUNDARY_DIR, f"{district_key}.geojson")
    if not os.path.exists(path):
        return None

    with open(path) as f:
        feat = json.load(f)

    geom = feat.get("geometry", {})
    coords = geom.get("coordinates", [])

    # Flatten all coordinates
    all_coords = []
    def _flatten(c):
        if isinstance(c[0], (int, float)):
            all_coords.append(c)
        else:
            for item in c:
                _flatten(item)
    _flatten(coords)

    if not all_coords:
        return None

    lons = [c[0] for c in all_coords]
    lats = [c[1] for c in all_coords]
    return {
        "xmin": min(lons), "ymin": min(lats),
        "xmax": max(lons), "ymax": max(lats),
        "spatialReference": {"wkid": 4326}
    }


# ─── Normalization ────────────────────────────────────────────────────

def normalize_event(raw_props, layer_type, geometry=None):
    """
    Normalize raw ArcGIS feature properties into a standard RoadEvent.

    Fields vary across layers; this handles known IDOT field names.
    """
    p = raw_props or {}

    # Extract coordinates
    lat, lon = None, None
    if geometry and geometry.get("type") == "Point":
        coords = geometry.get("coordinates", [])
        if len(coords) >= 2:
            lon, lat = coords[0], coords[1]

    # Common field extraction (IDOT uses many different field names)
    road = (
        p.get("Route") or p.get("Route1") or p.get("ROUTE") or
        p.get("RoadName") or p.get("ROAD_NAME") or
        p.get("road") or ""
    )
    direction = p.get("Direction") or p.get("DIRECTION") or ""
    location_text = (
        p.get("NearTown") or p.get("NEAR_TOWN") or
        p.get("Location") or p.get("LOCATION") or
        p.get("LocationDescription") or ""
    )
    county = p.get("County") or p.get("COUNTY") or ""
    description = (
        p.get("Description") or p.get("DESCRIPTION") or
        p.get("ConstructionType") or p.get("CONSTRUCTION_TYPE") or
        p.get("ImpactOnTravel") or p.get("IMPACT_ON_TRAVEL") or ""
    )

    # Status normalization
    raw_status = (p.get("Status") or p.get("STATUS") or "").lower()
    if any(w in raw_status for w in ["active", "in progress", "current", "open"]):
        status = "active"
    elif any(w in raw_status for w in ["planned", "upcoming", "scheduled"]):
        status = "planned"
    elif any(w in raw_status for w in ["ended", "completed", "closed"]):
        status = "ended"
    else:
        status = "unknown"

    # Date parsing (IDOT uses epoch milliseconds or date strings)
    def parse_date(val):
        if val is None:
            return None
        if isinstance(val, (int, float)) and val > 1e12:
            return datetime.fromtimestamp(val / 1000, tz=timezone.utc).isoformat()
        if isinstance(val, str):
            return val.strip()
        return None

    start = parse_date(p.get("StartDate") or p.get("START_DATE") or p.get("start"))
    end = parse_date(p.get("EndDate") or p.get("END_DATE") or p.get("end"))
    updated = parse_date(p.get("LastUpdated") or p.get("LAST_UPDATED") or p.get("EditDate"))

    # Lanes info
    lanes = (
        p.get("LanesAffected") or p.get("LANES_AFFECTED") or
        p.get("ImpactOnTravel") or p.get("TrafficAlert") or ""
    )

    # Source URL
    source_url = (
        p.get("WebAddress") or p.get("WEB_ADDRESS") or
        p.get("url") or p.get("URL") or
        "https://www.gettingaroundillinois.com/"
    )

    # Unique ID for dedup
    obj_id = p.get("OBJECTID") or p.get("ObjectId") or p.get("FID") or ""
    unique_id = f"{layer_type}:{obj_id}" if obj_id else hashlib.md5(
        json.dumps(p, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]

    return {
        "id": unique_id,
        "type": layer_type,
        "status": status,
        "road": road,
        "direction": direction,
        "location_text": location_text,
        "county": county,
        "description": description,
        "lanes": lanes,
        "start": start,
        "end": end,
        "last_updated": updated,
        "lat": lat,
        "lon": lon,
        "source_url": source_url,
        "severity": 0,  # calculated next
        "raw_status": raw_status,
        "source_layer": layer_type,
    }


# ─── Scoring ──────────────────────────────────────────────────────────

def score_event(event):
    """
    Deterministic severity score for ranking. Higher = more important.

    Base by type: closure +60, restriction +40, construction +25
    If status=active: +20
    Interstate: +15 (major US routes +10, state routes +5)
    If "all lanes closed" / "road closed": +20
    If "one lane" / "shoulder": +5
    """
    score = 0
    t = event.get("type", "")
    if t == "closure":
        score += 60
    elif t == "restriction":
        score += 40
    elif t == "construction":
        score += 25

    if event.get("status") == "active":
        score += 20

    road = (event.get("road") or "").upper()
    if road.startswith("I-") or road.startswith("I "):
        score += 15
    elif road.startswith("US-") or road.startswith("US "):
        score += 10
    elif road.startswith("IL-") or road.startswith("IL "):
        score += 5

    lanes = (event.get("lanes") or "").lower()
    desc = (event.get("description") or "").lower()
    combined = lanes + " " + desc

    if "road closed" in combined or "all lanes" in combined:
        score += 20
    elif "closed" in combined:
        score += 10
    elif "one lane" in combined or "shoulder" in combined:
        score += 5

    # Imminent end date bonus
    if event.get("end"):
        try:
            end_str = event["end"]
            if "T" in str(end_str):
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                hours_left = (end_dt - now).total_seconds() / 3600
                if 0 < hours_left < 48:
                    score += 10
        except:
            pass

    event["severity"] = score
    return event


# ─── District Builder ─────────────────────────────────────────────────

def build_district(district_key, verbose=True):
    """
    Build road event cache for a single district.
    Returns the district data dict.
    """
    if verbose:
        print(f"\n🔍 Building: {district_key}")

    # Load boundary
    boundary = load_boundary(district_key)
    if not boundary:
        bbox = bbox_from_geojson(district_key)
        if not bbox:
            if verbose:
                print(f"  ⚠️  No boundary file for {district_key}")
            return None
        geometry_param = bbox
        geom_type = "esriGeometryEnvelope"
    else:
        geometry_param = boundary
        geom_type = "esriGeometryPolygon"

    all_events = []
    counts = {"closures": 0, "restrictions": 0, "construction": 0}

    for layer_name, layer_info in LAYERS.items():
        url = layer_info["url"]
        layer_type = layer_info["type"]

        if verbose:
            print(f"  📡 Querying {layer_name}...", end=" ")

        # First try spatial intersect
        params = {
            "where": "1=1",
            "outFields": "*",
            "outSR": "4326",
            "f": "geojson",
            "geometry": json.dumps(geometry_param),
            "geometryType": geom_type,
            "spatialRel": "esriSpatialRelIntersects",
            "inSR": "4326",
            "resultRecordCount": PAGE_SIZE,
            "returnGeometry": "true",
        }

        features = []
        try:
            resp = requests.get(url, params=params, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                if "error" not in data:
                    features = data.get("features", [])
        except Exception as e:
            if verbose:
                print(f"error: {e}")

        # Try alternate URLs if primary failed
        if not features:
            for alt_pattern in ALT_LAYER_PATTERNS:
                alt_url = alt_pattern.format(name=layer_name.title().replace("s", ""))
                try:
                    resp = requests.get(alt_url, params=params, timeout=30)
                    if resp.status_code == 200:
                        data = resp.json()
                        if "error" not in data:
                            features = data.get("features", [])
                            if features:
                                break
                except:
                    pass

        if verbose:
            print(f"{len(features)} features")

        counts[layer_name] = len(features)

        for feat in features:
            event = normalize_event(
                feat.get("properties", {}),
                layer_type,
                feat.get("geometry")
            )
            event = score_event(event)
            all_events.append(event)

        time.sleep(0.5)  # Rate limit courtesy

    # Sort by severity (descending)
    all_events.sort(key=lambda e: e["severity"], reverse=True)

    result = {
        "district_key": district_key,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "total": sum(counts.values()),
        "top": all_events[:10],  # Top 10 by severity
        "items": all_events,      # Full list
    }

    out_path = os.path.join(ROAD_DIR, f"{district_key}.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    if verbose:
        print(f"  ✅ Saved {len(all_events)} events → {out_path}")

    return result


def build_statewide_senators():
    """
    Build statewide aggregate for US Senators.
    Unions all district items, deduplicates, ranks top 5.
    """
    print("\n🏛️  Building statewide senator aggregate (US-IL-SEN)...")

    all_events = []
    seen_ids = set()

    # Load all existing district caches
    for path in sorted(glob.glob(os.path.join(ROAD_DIR, "*.json"))):
        key = os.path.basename(path).replace(".json", "")
        if key == "US-IL-SEN":
            continue
        try:
            with open(path) as f:
                data = json.load(f)
            for event in data.get("items", []):
                eid = event.get("id", "")
                if eid and eid not in seen_ids:
                    seen_ids.add(eid)
                    event["_source_district"] = key
                    all_events.append(event)
        except:
            pass

    # Sort by severity
    all_events.sort(key=lambda e: e.get("severity", 0), reverse=True)

    # Count by type
    type_counts = Counter(e.get("type", "unknown") for e in all_events)

    result = {
        "district_key": "US-IL-SEN",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "closures": type_counts.get("closure", 0),
            "restrictions": type_counts.get("restriction", 0),
            "construction": type_counts.get("construction", 0),
        },
        "total": len(all_events),
        "top": all_events[:5],  # Top 5 statewide
        "items": all_events[:50],  # Keep top 50 for browsing
    }

    out_path = os.path.join(ROAD_DIR, "US-IL-SEN.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    print(f"  ✅ {len(all_events)} total events, top 5 saved → {out_path}")
    return result


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("IDOT Dashboard — Road Events Fetcher")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 60)

    # Check for boundary files
    boundary_files = glob.glob(os.path.join(BOUNDARY_DIR, "*.geojson"))
    if not boundary_files:
        print("\n❌ No boundary files found in data/boundaries/")
        print("   Run: python fetch_boundaries.py first")
        sys.exit(1)

    print(f"\n📂 Found {len(boundary_files)} boundary files")

    # Parse args
    target = sys.argv[1] if len(sys.argv) > 1 else None

    if target == "--statewide-only":
        build_statewide_senators()
        return

    if target:
        # Build single district
        build_district(target)
    else:
        # Build all districts
        for bf in sorted(boundary_files):
            key = os.path.basename(bf).replace(".geojson", "")
            build_district(key)
            time.sleep(1)  # Rate limit between districts

    # Always rebuild senator aggregate after district builds
    build_statewide_senators()

    print("\n" + "=" * 60)
    print("DONE — Road event caches written to data/road/")
    print("=" * 60)


if __name__ == "__main__":
    main()
