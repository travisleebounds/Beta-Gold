#!/usr/bin/env python3
"""
fetch_road_events.py — Query IDOT ArcGIS layers for live road events.

IDOT ArcGIS Live Layers (verified Feb 2026):
  - Illinois_Roadway_Incidents (421+ real-time incidents — main feed)
  - ClosureIncidents (IDOT-posted closures)
  - ClosureIncidentExtents (closure line extents)
  - Annual_Highway_Improvement_Program (1100+ construction projects)
  - RoadConstructionTest_Waze (Waze-reported construction)
  - Flooding_Road_Closures (flood events)
  - Travel_Midwest_Unplanned_Events (unplanned events)

For each district boundary in data/boundaries/, performs spatial intersect,
normalizes into RoadEvent schema, scores by severity, saves per-district JSON.

Usage:
  python fetch_road_events.py                  # all districts
  python fetch_road_events.py IL-01            # single district
  python fetch_road_events.py --statewide-only # just senator aggregate
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

BASE = "https://services2.arcgis.com/aIrBD8yn1TDTEXoz/arcgis/rest/services"

# Verified working IDOT ArcGIS layers (Feb 2026)
LAYERS = {
    "incidents": {
        "url": f"{BASE}/Illinois_Roadway_Incidents/FeatureServer/0/query",
        "type": "closure",  # Most are closures/incidents
        "description": "Real-time roadway incidents (main feed)",
    },
    "closure_incidents": {
        "url": f"{BASE}/ClosureIncidents/FeatureServer/0/query",
        "type": "closure",
        "description": "IDOT-posted closure incidents",
    },
    "closure_extents": {
        "url": f"{BASE}/ClosureIncidentExtents/FeatureServer/0/query",
        "type": "closure",
        "description": "Closure line extents",
    },
    "construction": {
        "url": f"{BASE}/Annual_Highway_Improvement_Program/FeatureServer/0/query",
        "type": "construction",
        "description": "Annual highway improvement program projects",
    },
    "waze_construction": {
        "url": f"{BASE}/RoadConstructionTest_Waze/FeatureServer/0/query",
        "type": "construction",
        "description": "Waze-reported construction",
    },
    "flooding": {
        "url": f"{BASE}/Flooding_Road_Closures/FeatureServer/0/query",
        "type": "closure",
        "description": "Flood-related road closures",
    },
    "unplanned": {
        "url": f"{BASE}/Travel_Midwest_Unplanned_Events/FeatureServer/0/query",
        "type": "closure",
        "description": "Unplanned travel events",
    },
}

PAGE_SIZE = 1000
MAX_PAGES = 20
REQUEST_TIMEOUT = 60

# ─── ArcGIS Query Helpers ─────────────────────────────────────────────

def arcgis_spatial_query(url, geometry_json, geom_type="esriGeometryPolygon"):
    """Query an ArcGIS layer with spatial intersect. Returns list of GeoJSON features."""
    all_features = []
    offset = 0

    for page in range(MAX_PAGES):
        params = {
            "where": "1=1",
            "outFields": "*",
            "outSR": "4326",
            "f": "geojson",
            "geometry": json.dumps(geometry_json),
            "geometryType": geom_type,
            "spatialRel": "esriSpatialRelIntersects",
            "inSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "returnGeometry": "true",
        }

        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    ⚠  Query error: {e}")
            break

        if "error" in data:
            # Don't spam — just skip this layer for this district
            break

        features = data.get("features", [])
        if not features:
            break

        all_features.extend(features)
        offset += PAGE_SIZE

        if len(features) < PAGE_SIZE:
            break

        time.sleep(0.3)

    return all_features


def arcgis_count(url):
    """Quick count check for a layer."""
    try:
        r = requests.get(url, params={"where": "1=1", "returnCountOnly": "true", "f": "json"}, timeout=15)
        return r.json().get("count", 0)
    except:
        return 0


# ─── GeoJSON / Boundary Helpers ───────────────────────────────────────

def load_boundary_esri(district_key):
    """Load boundary as Esri-compatible geometry for spatial queries."""
    path = os.path.join(BOUNDARY_DIR, f"{district_key}.geojson")
    if not os.path.exists(path):
        return None, None

    with open(path) as f:
        feat = json.load(f)

    geom = feat.get("geometry", {})

    if geom.get("type") == "Polygon":
        return {
            "rings": geom["coordinates"],
            "spatialReference": {"wkid": 4326},
        }, "esriGeometryPolygon"

    elif geom.get("type") == "MultiPolygon":
        rings = []
        for polygon in geom["coordinates"]:
            rings.extend(polygon)
        return {
            "rings": rings,
            "spatialReference": {"wkid": 4326},
        }, "esriGeometryPolygon"

    return None, None


def bbox_from_boundary(district_key):
    """Get bounding box envelope as fallback for spatial query."""
    path = os.path.join(BOUNDARY_DIR, f"{district_key}.geojson")
    if not os.path.exists(path):
        return None

    with open(path) as f:
        feat = json.load(f)

    all_coords = []

    def flatten(c):
        if isinstance(c[0], (int, float)):
            all_coords.append(c)
        else:
            for item in c:
                flatten(item)

    flatten(feat.get("geometry", {}).get("coordinates", []))

    if not all_coords:
        return None

    lons = [c[0] for c in all_coords]
    lats = [c[1] for c in all_coords]

    return {
        "xmin": min(lons), "ymin": min(lats),
        "xmax": max(lons), "ymax": max(lats),
        "spatialReference": {"wkid": 4326},
    }


# ─── Event Normalization ──────────────────────────────────────────────

def normalize_incident(props, geometry=None):
    """Normalize Illinois_Roadway_Incidents fields."""
    p = props or {}

    lat, lon = None, None
    if geometry and geometry.get("type") == "Point":
        coords = geometry.get("coordinates", [])
        if len(coords) >= 2:
            lon, lat = coords[0], coords[1]

    # Parse ORIGIN field as fallback coords (format: "-87.79434 41.88346")
    if (lat is None or lon is None) and p.get("ORIGIN"):
        try:
            parts = p["ORIGIN"].split()
            if len(parts) == 2:
                lon, lat = float(parts[0]), float(parts[1])
        except:
            pass

    # Type mapping
    type_desc = (p.get("TRAFFIC_ITEM_TYPE_DESC") or "").upper()
    if "CLOSURE" in type_desc or p.get("ROAD_CLOSED"):
        event_type = "closure"
    elif "CONSTRUCTION" in type_desc:
        event_type = "construction"
    elif "RESTRICTION" in type_desc:
        event_type = "restriction"
    else:
        event_type = "closure"  # Default for incidents

    # Status
    criticality = (p.get("CRITICALITY_DESC") or "").lower()
    if criticality in ("critical", "major"):
        status = "active"
    elif p.get("VERIFIED"):
        status = "active"
    else:
        status = "unknown"

    # Dates (epoch ms)
    def parse_epoch(val):
        if val and isinstance(val, (int, float)) and val > 1e12:
            return datetime.fromtimestamp(val / 1000, tz=timezone.utc).isoformat()
        return None

    road = p.get("LOCATION_DEFINED_ORIGIN_RDWY") or ""
    description = p.get("TRAFFIC_ITEM_DESCRIPTION") or p.get("TRAFFIC_ITEM_DESCRIPTION_NO_EX") or ""

    return {
        "id": f"incident:{p.get('OBJECTID', '')}",
        "type": event_type,
        "status": status,
        "road": road,
        "direction": "",
        "location_text": description,
        "county": "",
        "description": description,
        "lanes": "",
        "start": parse_epoch(p.get("START_TIME")),
        "end": parse_epoch(p.get("END_TIME")),
        "last_updated": None,
        "lat": lat,
        "lon": lon,
        "source_url": "https://www.gettingaroundillinois.com/",
        "severity": 0,
        "source_layer": "Illinois_Roadway_Incidents",
    }


def normalize_closure(props, geometry=None):
    """Normalize ClosureIncidents fields."""
    p = props or {}

    lat, lon = None, None
    if geometry and geometry.get("type") == "Point":
        coords = geometry.get("coordinates", [])
        if len(coords) >= 2:
            lon, lat = coords[0], coords[1]

    ctype = (p.get("ConstructionType") or "").upper()
    if "CLOSED" in ctype:
        event_type = "closure"
    else:
        event_type = "construction"

    def parse_epoch(val):
        if val and isinstance(val, (int, float)) and val > 1e12:
            return datetime.fromtimestamp(val / 1000, tz=timezone.utc).isoformat()
        return None

    route = p.get("Route1") or p.get("Route2") or ""
    location = p.get("Location") or p.get("NearTown") or ""
    county = p.get("County") or ""

    return {
        "id": f"closure:{p.get('OBJECTID', '')}",
        "type": event_type,
        "status": "active",
        "road": route,
        "direction": p.get("Route1Direction") or "",
        "location_text": location,
        "county": county,
        "description": location,
        "lanes": p.get("TrafficAlert") or "",
        "start": parse_epoch(p.get("StartDate")),
        "end": parse_epoch(p.get("EndDate")),
        "last_updated": None,
        "lat": lat,
        "lon": lon,
        "source_url": p.get("WebAddress") or "https://www.gettingaroundillinois.com/",
        "severity": 0,
        "source_layer": "ClosureIncidents",
    }


def normalize_construction(props, geometry=None):
    """Normalize Annual_Highway_Improvement_Program fields."""
    p = props or {}

    lat, lon = None, None
    if geometry and geometry.get("type") == "Point":
        coords = geometry.get("coordinates", [])
        if len(coords) >= 2:
            lon, lat = coords[0], coords[1]

    # Try common field names for road/route
    road = (
        p.get("ROUTE") or p.get("Route") or p.get("ROAD_NAME") or
        p.get("RoadName") or p.get("INVENTORY") or ""
    )
    location = (
        p.get("LOCATION") or p.get("Location") or p.get("DESCRIPTION") or
        p.get("Description") or p.get("WORK_DESCRIPTION") or ""
    )
    county = p.get("COUNTY") or p.get("County") or ""

    return {
        "id": f"construction:{p.get('OBJECTID', p.get('FID', ''))}",
        "type": "construction",
        "status": "active",
        "road": str(road),
        "direction": "",
        "location_text": str(location)[:200],
        "county": str(county),
        "description": str(location)[:200],
        "lanes": "",
        "start": None,
        "end": None,
        "last_updated": None,
        "lat": lat,
        "lon": lon,
        "source_url": "https://www.gettingaroundillinois.com/",
        "severity": 0,
        "source_layer": "Annual_Highway_Improvement_Program",
    }


def normalize_generic(props, layer_type, layer_name, geometry=None):
    """Generic normalizer for other layers."""
    p = props or {}

    lat, lon = None, None
    if geometry:
        if geometry.get("type") == "Point":
            coords = geometry.get("coordinates", [])
            if len(coords) >= 2:
                lon, lat = coords[0], coords[1]
        elif geometry.get("type") == "LineString":
            coords = geometry.get("coordinates", [])
            if coords:
                mid = coords[len(coords) // 2]
                lon, lat = mid[0], mid[1]

    # Pull whatever fields exist
    road = ""
    desc = ""
    for key in ["Route", "ROUTE", "Route1", "RoadName", "ROAD_NAME",
                 "LOCATION_DEFINED_ORIGIN_RDWY", "road"]:
        if p.get(key):
            road = str(p[key])
            break

    for key in ["Description", "DESCRIPTION", "Location", "LOCATION",
                 "TRAFFIC_ITEM_DESCRIPTION", "ConstructionType", "WORK_DESCRIPTION"]:
        if p.get(key):
            desc = str(p[key])[:200]
            break

    return {
        "id": f"{layer_type}:{p.get('OBJECTID', p.get('FID', hashlib.md5(json.dumps(p, default=str).encode()).hexdigest()[:8]))}",
        "type": layer_type,
        "status": "active",
        "road": road,
        "direction": "",
        "location_text": desc,
        "county": str(p.get("County", p.get("COUNTY", ""))),
        "description": desc,
        "lanes": "",
        "start": None,
        "end": None,
        "last_updated": None,
        "lat": lat,
        "lon": lon,
        "source_url": "https://www.gettingaroundillinois.com/",
        "severity": 0,
        "source_layer": layer_name,
    }


# Layer-specific normalizer dispatch
NORMALIZERS = {
    "incidents": normalize_incident,
    "closure_incidents": normalize_closure,
    "closure_extents": lambda p, g=None: normalize_generic(p, "closure", "ClosureIncidentExtents", g),
    "construction": normalize_construction,
    "waze_construction": lambda p, g=None: normalize_generic(p, "construction", "RoadConstructionTest_Waze", g),
    "flooding": lambda p, g=None: normalize_generic(p, "closure", "Flooding_Road_Closures", g),
    "unplanned": lambda p, g=None: normalize_generic(p, "closure", "Travel_Midwest_Unplanned_Events", g),
}


# ─── Scoring ──────────────────────────────────────────────────────────

def score_event(event):
    """Severity score. Higher = more important."""
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

    desc = ((event.get("description") or "") + " " + (event.get("lanes") or "")).lower()
    if "road closed" in desc or "all lanes" in desc:
        score += 20
    elif "closed" in desc:
        score += 10

    # Imminent end date
    if event.get("end"):
        try:
            end_dt = datetime.fromisoformat(event["end"].replace("Z", "+00:00"))
            hours_left = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
            if 0 < hours_left < 48:
                score += 10
        except:
            pass

    event["severity"] = score
    return event


# ─── District Builder ─────────────────────────────────────────────────

def build_district(district_key, verbose=True):
    """Build road event cache for a single district."""
    if verbose:
        print(f"\n🔍 {district_key}")

    # Load boundary
    geometry, geom_type = load_boundary_esri(district_key)
    if not geometry:
        bbox = bbox_from_boundary(district_key)
        if not bbox:
            if verbose:
                print(f"  ⚠  No boundary for {district_key}")
            return None
        geometry = bbox
        geom_type = "esriGeometryEnvelope"

    all_events = []
    seen_ids = set()
    layer_counts = {}

    for layer_name, layer_info in LAYERS.items():
        url = layer_info["url"]
        normalizer = NORMALIZERS.get(layer_name, lambda p, g=None: normalize_generic(p, layer_info["type"], layer_name, g))

        if verbose:
            print(f"  📡 {layer_name}...", end=" ", flush=True)

        features = arcgis_spatial_query(url, geometry, geom_type)
        layer_counts[layer_name] = len(features)

        if verbose:
            print(f"{len(features)} events")

        for feat in features:
            event = normalizer(feat.get("properties", {}), feat.get("geometry"))
            event = score_event(event)

            # Dedup by ID
            eid = event.get("id", "")
            if eid not in seen_ids:
                seen_ids.add(eid)
                all_events.append(event)

        time.sleep(0.3)

    # Sort by severity
    all_events.sort(key=lambda e: e["severity"], reverse=True)

    # Count by type
    type_counts = Counter(e.get("type", "unknown") for e in all_events)

    result = {
        "district_key": district_key,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "closures": type_counts.get("closure", 0),
            "construction": type_counts.get("construction", 0),
            "restrictions": type_counts.get("restriction", 0),
        },
        "layer_counts": layer_counts,
        "total": len(all_events),
        "top": all_events[:10],
        "items": all_events,
    }

    out_path = os.path.join(ROAD_DIR, f"{district_key}.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    if verbose:
        print(f"  ✅ {len(all_events)} events → {out_path}")

    return result


def build_statewide_senators():
    """Build statewide aggregate for US Senators."""
    print("\n🏛  Building statewide senator aggregate (US-IL-SEN)...")

    all_events = []
    seen_ids = set()

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

    all_events.sort(key=lambda e: e.get("severity", 0), reverse=True)
    type_counts = Counter(e.get("type", "unknown") for e in all_events)

    result = {
        "district_key": "US-IL-SEN",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "closures": type_counts.get("closure", 0),
            "construction": type_counts.get("construction", 0),
            "restrictions": type_counts.get("restriction", 0),
        },
        "total": len(all_events),
        "top": all_events[:10],
        "items": all_events[:100],
    }

    out_path = os.path.join(ROAD_DIR, "US-IL-SEN.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    print(f"  ✅ {len(all_events)} total statewide events → {out_path}")
    return result


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("IDOT Dashboard — Road Events Fetcher v2")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 60)

    # Quick layer health check
    print("\n📡 Layer status:")
    for name, info in LAYERS.items():
        count = arcgis_count(info["url"])
        status = f"✅ {count} features" if count > 0 else "⚠️  0 features" if count == 0 else "❌ unreachable"
        print(f"  {name}: {status}")

    # Check boundaries
    boundary_files = sorted(glob.glob(os.path.join(BOUNDARY_DIR, "*.geojson")))
    if not boundary_files:
        print("\n❌ No boundary files in data/boundaries/")
        print("   Run: python fetch_boundaries.py")
        sys.exit(1)

    print(f"\n📂 {len(boundary_files)} boundary files")

    # Parse args
    target = sys.argv[1] if len(sys.argv) > 1 else None

    if target == "--statewide-only":
        build_statewide_senators()
        return

    if target:
        build_district(target)
    else:
        for bf in sorted(boundary_files):
            key = os.path.basename(bf).replace(".geojson", "")
            build_district(key)
            time.sleep(0.5)

    build_statewide_senators()

    # Summary
    total_events = 0
    district_files = glob.glob(os.path.join(ROAD_DIR, "*.json"))
    for df in district_files:
        try:
            with open(df) as f:
                total_events += json.load(f).get("total", 0)
        except:
            pass

    print("\n" + "=" * 60)
    print(f"DONE — {len(district_files)} district files, {total_events} total events")
    print(f"Data written to {ROAD_DIR}/")
    print("=" * 60)


if __name__ == "__main__":
    main()
