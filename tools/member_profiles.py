"""
Member Profiles — IDOT Dashboard
==================================
Unified profile pages for all federal and state members.

Features:
  - Profile photo (local file or placeholder)
  - Interactive Folium district map with layer selector:
    • District boundary
    • Road closures (from IDOT/pipeline data)
    • Construction projects
    • Federal discretionary grants
  - Transportation legislation authored/co-authored
  - Federal funding summary
  - Document Master report generator integration

Works for:
  - 17 Congressional districts (from DISTRICTS dict in app.py)
  - 118 IL House districts (from members.json)
  - 59 IL Senate districts (from members.json)
"""

from __future__ import annotations
import json
import math
import os
import glob
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium


# ═══════════════════════════════════════════════════════════════
# Data Loading Helpers
# ═══════════════════════════════════════════════════════════════

def _load_json(path: str, default=None):
    """Safely load a JSON file."""
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return default if default is not None else {}


def _load_members():
    """Load the full member roster."""
    return _load_json("members.json", {})


def _load_discretionary_grants():
    """Load discretionary grants data."""
    return _load_json("discretionary_grants.json", {})


def _load_formula_allocations():
    """Load district formula allocation data."""
    return _load_json("district_formula_allocations.json", {})


def _load_road_events(district_key: str):
    """Load road event cache for a district."""
    path = f"data/road/{district_key}.json"
    return _load_json(path)


def _load_boundary(district_key: str):
    """Load GeoJSON boundary for a district."""
    path = f"data/boundaries/{district_key}.geojson"
    return _load_json(path)


def _load_real_bills():
    """Load bill data if available."""
    files = glob.glob("bills_*.json")
    if files:
        latest = sorted(files)[-1]
        return _load_json(latest, {})
    return {}


def _load_ilga_data():
    """Load IL General Assembly data."""
    return _load_json("illinois_general_assembly.json", {})


def _get_districts_from_app():
    """
    Import the DISTRICTS dict from the main app module.
    Falls back to loading from a cache file if import fails.
    """
    try:
        # Try importing from the app's global namespace
        import app
        return getattr(app, "DISTRICTS", {})
    except Exception:
        pass
    
    # Fallback: build a minimal version from members.json
    return {}


def _get_district_boundaries():
    """Load all available district boundaries."""
    boundaries = {}
    
    # Congressional boundaries
    if os.path.exists("il_congressional_boundaries.json"):
        raw = _load_json("il_congressional_boundaries.json", {})
        for key, val in raw.items():
            num = int(key.split("-")[1])
            boundaries[f"IL-{num:02d}"] = val
    
    # Individual boundary files
    boundary_dir = "data/boundaries"
    if os.path.isdir(boundary_dir):
        for f in glob.glob(os.path.join(boundary_dir, "*.geojson")):
            key = Path(f).stem
            boundaries[key] = _load_json(f)
    
    return boundaries


# ═══════════════════════════════════════════════════════════════
# Photo Helpers
# ═══════════════════════════════════════════════════════════════

def _find_photo(member_id: str, name: str = "", geo_type: str = "federal") -> str | None:
    """
    Find a profile photo for a member.
    
    Checks multiple locations:
    1. data/members/{geo_type}/{member_id}/photo.*
    2. district_images/{member_id}.png
    3. data/member_photos/{member_id}.*
    """
    # data/members structure
    for folder in [geo_type, "federal", "il_house", "il_senate"]:
        base = Path("data/members") / folder / member_id
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            for fname in [f"photo{ext}", f"{member_id}{ext}"]:
                cand = base / fname
                if cand.exists():
                    return str(cand)
    
    # district_images (congressional reference maps)
    img_path = f"district_images/{member_id}.png"
    if os.path.exists(img_path):
        return img_path
    
    # Flat photo directory
    photo_dir = Path("data/member_photos")
    if photo_dir.is_dir():
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            cand = photo_dir / f"{member_id}{ext}"
            if cand.exists():
                return str(cand)
            # Try by name
            if name:
                safe_name = name.replace(" ", "_").replace(".", "")
                cand = photo_dir / f"{safe_name}{ext}"
                if cand.exists():
                    return str(cand)
    
    return None


# ═══════════════════════════════════════════════════════════════
# Map Builder
# ═══════════════════════════════════════════════════════════════

def _geojson_to_folium_coords(geometry):
    """Convert GeoJSON geometry to folium-compatible coordinate rings."""
    if geometry is None:
        return []
    
    geo_type = geometry.get("type", "")
    coords = geometry.get("coordinates", [])
    rings = []
    
    if geo_type == "Polygon":
        for ring in coords:
            rings.append([[lat, lon] for lon, lat in ring])
    elif geo_type == "MultiPolygon":
        for polygon in coords:
            for ring in polygon:
                rings.append([[lat, lon] for lon, lat in ring])
    
    return rings


def _get_boundary_center(boundary_data):
    """Calculate center point from boundary geometry."""
    if not boundary_data or "geometry" not in boundary_data:
        return None, None
    
    geo = boundary_data["geometry"]
    geo_type = geo.get("type", "")
    all_lons, all_lats = [], []
    
    if geo_type == "Polygon":
        for lon, lat in geo["coordinates"][0]:
            all_lons.append(lon)
            all_lats.append(lat)
    elif geo_type == "MultiPolygon":
        for polygon in geo["coordinates"]:
            for lon, lat in polygon[0]:
                all_lons.append(lon)
                all_lats.append(lat)
    
    if all_lats and all_lons:
        return sum(all_lats) / len(all_lats), sum(all_lons) / len(all_lons)
    return None, None


def build_member_map(
    member_id: str,
    party: str,
    boundary_data: dict = None,
    fallback_boundary: list = None,
    center_lat: float = None,
    center_lon: float = None,
    closures: list = None,
    construction: list = None,
    grants: list = None,
    disc_grants: list = None,
    road_events: dict = None,
    active_layers: list = None,
    zoom: int = 10,
):
    """
    Build an interactive Folium map for a member's district.
    
    Supports layer toggling for:
    - District boundary
    - Road closures
    - Construction projects
    - Federal grants (from DISTRICTS data)
    - Discretionary grants (from discretionary_grants.json)
    - Pipeline road events (from data/road/)
    """
    if active_layers is None:
        active_layers = ["boundary", "closures", "construction", "grants"]
    
    # Determine center
    if boundary_data:
        b_lat, b_lon = _get_boundary_center(boundary_data)
        center_lat = b_lat or center_lat or 40.0
        center_lon = b_lon or center_lon or -89.0
    
    center_lat = center_lat or 40.0
    center_lon = center_lon or -89.0
    
    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom, tiles="CartoDB positron")
    
    color = "#4A90E2" if party == "D" else "#E24A4A" if party == "R" else "#888888"
    
    # ─── Layer: District Boundary ───────────────────────────
    if "boundary" in active_layers:
        if boundary_data and "geometry" in boundary_data:
            rings = _geojson_to_folium_coords(boundary_data["geometry"])
            for ring in rings:
                folium.Polygon(
                    locations=ring,
                    color=color,
                    fill=True,
                    fillColor=color,
                    fillOpacity=0.12,
                    weight=3,
                    popup=f"<b>{member_id} Boundary</b>",
                ).add_to(m)
        elif fallback_boundary:
            folium.Polygon(
                locations=fallback_boundary,
                color=color,
                fill=True,
                fillColor=color,
                fillOpacity=0.12,
                weight=3,
                popup=f"<b>{member_id} Boundary</b>",
            ).add_to(m)
    
    # ─── Layer: Road Closures ───────────────────────────────
    if "closures" in active_layers and closures:
        for c in closures:
            if c.get("lat") and c.get("lon"):
                folium.Marker(
                    [c["lat"], c["lon"]],
                    icon=folium.Icon(color="orange", icon="road", prefix="fa"),
                    popup=folium.Popup(
                        f"<b>🚧 {c.get('route', 'N/A')}</b><br>"
                        f"{c.get('type', '')}<br>"
                        f"Status: {c.get('status', 'Unknown')}<br>"
                        f"{c.get('description', '')[:120]}",
                        max_width=300,
                    ),
                    tooltip=f"🚧 {c.get('route', '')} - {c.get('type', '')}",
                ).add_to(m)
    
    # ─── Layer: Construction ────────────────────────────────
    if "construction" in active_layers and construction:
        for c in construction:
            if c.get("lat") and c.get("lon"):
                folium.Marker(
                    [c["lat"], c["lon"]],
                    icon=folium.Icon(color="red", icon="wrench", prefix="fa"),
                    popup=folium.Popup(
                        f"<b>🏗️ {c.get('route', 'N/A')}</b><br>"
                        f"{c.get('type', '')}<br>"
                        f"Status: {c.get('status', 'Unknown')}<br>"
                        f"Budget: {c.get('budget', 'N/A')}<br>"
                        f"{c.get('description', '')[:120]}",
                        max_width=300,
                    ),
                    tooltip=f"🏗️ {c.get('route', '')} - {c.get('type', '')}",
                ).add_to(m)
    
    # ─── Layer: Federal Grants (from DISTRICTS) ─────────────
    if "grants" in active_layers and grants:
        for g in grants:
            if g.get("lat") and g.get("lon"):
                folium.Marker(
                    [g["lat"], g["lon"]],
                    icon=folium.Icon(color="green", icon="dollar", prefix="fa"),
                    popup=folium.Popup(
                        f"<b>💰 {g.get('program', 'N/A')}</b><br>"
                        f"${g.get('amount', 0):,.0f}<br>"
                        f"{g.get('project', '')}<br>"
                        f"{g.get('description', '')[:120]}",
                        max_width=300,
                    ),
                    tooltip=f"💰 {g.get('program', '')} - ${g.get('amount', 0):,.0f}",
                ).add_to(m)
    
    # ─── Layer: Discretionary Grants ────────────────────────
    if "disc_grants" in active_layers and disc_grants:
        for g in disc_grants:
            # Discretionary grants may not have lat/lon — place at district center
            lat = g.get("lat", center_lat)
            lon = g.get("lon", center_lon)
            # Offset slightly if multiple grants at same point
            import random
            lat += random.uniform(-0.01, 0.01)
            lon += random.uniform(-0.01, 0.01)
            
            folium.CircleMarker(
                [lat, lon],
                radius=max(6, min(15, g.get("amount", 0) / 5_000_000)),
                color="#9b59b6",
                fill=True,
                fillColor="#9b59b6",
                fillOpacity=0.6,
                weight=2,
                popup=folium.Popup(
                    f"<b>💎 {g.get('program', 'N/A')}</b><br>"
                    f"<b>${g.get('amount', 0):,.0f}</b><br>"
                    f"Year: {g.get('year', 'N/A')}<br>"
                    f"{g.get('project', '')}<br>"
                    f"{g.get('recipient', '')}",
                    max_width=300,
                ),
                tooltip=f"💎 {g.get('program', '')} ({g.get('year', '')}) - ${g.get('amount', 0):,.0f}",
            ).add_to(m)
    
    # ─── Layer: Pipeline Road Events ────────────────────────
    if "road_events" in active_layers and road_events:
        items = road_events.get("items", [])
        for e in items[:100]:
            if e.get("lat") and e.get("lon"):
                etype = e.get("type", "")
                if etype == "closure":
                    ecolor = "red"
                elif etype == "restriction":
                    ecolor = "orange"
                else:
                    ecolor = "blue"
                
                folium.CircleMarker(
                    [e["lat"], e["lon"]],
                    radius=5,
                    color=ecolor,
                    fill=True,
                    fillOpacity=0.7,
                    popup=folium.Popup(
                        f"<b>{e.get('road', 'N/A')}</b><br>"
                        f"{etype.title()}<br>"
                        f"{(e.get('description') or '')[:100]}",
                        max_width=250,
                    ),
                    tooltip=f"{e.get('road', '')} — {etype}",
                ).add_to(m)
    
    return m


# ═══════════════════════════════════════════════════════════════
# Profile Renderers
# ═══════════════════════════════════════════════════════════════

def _render_profile_header(member_id, name, party, area, committees=None, 
                            photo_path=None, geo_type="federal"):
    """Render the top section of a member profile."""
    col_photo, col_info = st.columns([1, 3])
    
    with col_photo:
        if photo_path and os.path.exists(photo_path):
            from PIL import Image
            img = Image.open(photo_path)
            st.image(img, width=200)
        else:
            # Placeholder
            party_color = "#4A90E2" if party == "D" else "#E24A4A" if party == "R" else "#888"
            party_label = "Democrat" if party == "D" else "Republican" if party == "R" else "Unknown"
            st.markdown(
                f"""<div style="width:180px;height:220px;background:{party_color}22;
                border:2px solid {party_color};border-radius:12px;display:flex;
                align-items:center;justify-content:center;flex-direction:column;margin:auto;">
                <span style="font-size:48px;">{'🔵' if party == 'D' else '🔴' if party == 'R' else '⚪'}</span>
                <span style="color:{party_color};font-weight:bold;margin-top:8px;">{party_label}</span>
                </div>""",
                unsafe_allow_html=True,
            )
    
    with col_info:
        st.subheader(f"{member_id} — {name}")
        
        party_full = "🔵 Democrat" if party == "D" else "🔴 Republican" if party == "R" else "⚪ Unknown"
        
        info_cols = st.columns(3)
        info_cols[0].metric("Party", party_full)
        info_cols[1].metric("District", member_id)
        if area:
            info_cols[2].metric("Area", area[:30])
        
        if committees:
            st.caption(f"**Committees:** {', '.join(committees)}")


def _render_map_section(member_id, party, boundary_data, fallback_boundary,
                         center_lat, center_lon, closures, construction,
                         grants_inline, disc_grants, road_events, zoom=10):
    """Render the interactive map with layer selector."""
    
    st.markdown("### 🗺️ District Map")
    
    # Layer selector
    all_layers = {
        "boundary": "🔲 District Boundary",
        "closures": "🚧 Road Closures",
        "construction": "🏗️ Construction",
        "grants": "💰 Federal Grants",
        "disc_grants": "💎 Discretionary Grants",
        "road_events": "🛣️ Pipeline Road Events",
    }
    
    # Only show layers that have data
    available = ["boundary"]
    if closures:
        available.append("closures")
    if construction:
        available.append("construction")
    if grants_inline:
        available.append("grants")
    if disc_grants:
        available.append("disc_grants")
    if road_events and road_events.get("items"):
        available.append("road_events")
    
    layer_labels = [all_layers[k] for k in available]
    layer_keys = available
    
    selected_labels = st.multiselect(
        "Map Layers:",
        layer_labels,
        default=layer_labels,  # All on by default
        key=f"map_layers_{member_id}",
    )
    
    # Convert labels back to keys
    active_layers = []
    for label in selected_labels:
        for k, v in all_layers.items():
            if v == label:
                active_layers.append(k)
    
    # Legend
    legend_items = []
    if "boundary" in active_layers:
        legend_items.append("🔲 Boundary")
    if "closures" in active_layers and closures:
        legend_items.append(f"🚧 Closures ({len(closures)})")
    if "construction" in active_layers and construction:
        legend_items.append(f"🏗️ Construction ({len(construction)})")
    if "grants" in active_layers and grants_inline:
        legend_items.append(f"💰 Grants ({len(grants_inline)})")
    if "disc_grants" in active_layers and disc_grants:
        total_disc = sum(g.get("amount", 0) for g in disc_grants)
        legend_items.append(f"💎 Disc. Grants ({len(disc_grants)}, ${total_disc/1e6:.1f}M)")
    if "road_events" in active_layers and road_events:
        legend_items.append(f"🛣️ Road Events ({road_events.get('total', 0)})")
    
    if legend_items:
        st.caption(" · ".join(legend_items))
    
    # Build and render map
    m = build_member_map(
        member_id=member_id,
        party=party,
        boundary_data=boundary_data,
        fallback_boundary=fallback_boundary,
        center_lat=center_lat,
        center_lon=center_lon,
        closures=closures if "closures" in active_layers else None,
        construction=construction if "construction" in active_layers else None,
        grants=grants_inline if "grants" in active_layers else None,
        disc_grants=disc_grants if "disc_grants" in active_layers else None,
        road_events=road_events if "road_events" in active_layers else None,
        active_layers=active_layers,
        zoom=zoom,
    )
    
    st_folium(m, width=1400, height=500, returned_objects=[])


def _render_closures_construction(closures, construction):
    """Render closures and construction detail tables."""
    if not closures and not construction:
        st.info("No active closures or construction projects in this district")
        return
    
    tab_c, tab_con = st.tabs(["🚧 Closures", "🏗️ Construction"])
    
    with tab_c:
        if closures:
            data = []
            for c in closures:
                data.append({
                    "Route": c.get("route", "N/A"),
                    "Location": c.get("location", c.get("location_text", "N/A")),
                    "Type": c.get("type", "N/A"),
                    "Status": c.get("status", "Unknown"),
                    "Description": (c.get("description") or "")[:80],
                })
            st.dataframe(pd.DataFrame(data), width="stretch", hide_index=True)
        else:
            st.info("No active closures")
    
    with tab_con:
        if construction:
            data = []
            for c in construction:
                data.append({
                    "Route": c.get("route", "N/A"),
                    "Location": c.get("location", c.get("location_text", "N/A")),
                    "Type": c.get("type", "N/A"),
                    "Status": c.get("status", "Unknown"),
                    "Budget": c.get("budget", "N/A"),
                    "Timeline": c.get("timeline", "N/A"),
                    "Description": (c.get("description") or "")[:80],
                })
            st.dataframe(pd.DataFrame(data), width="stretch", hide_index=True)
        else:
            st.info("No active construction")


def _render_grants_section(grants_inline, disc_grants, member_id):
    """Render grants summary and detail."""
    st.markdown("### 💰 Federal Grants")
    
    all_grants = []
    
    if grants_inline:
        for g in grants_inline:
            all_grants.append({
                "Source": "District Data",
                "Program": g.get("program", "N/A"),
                "Amount": g.get("amount", 0),
                "Project": g.get("project", "N/A"),
                "Description": (g.get("description") or "")[:80],
            })
    
    if disc_grants:
        for g in disc_grants:
            all_grants.append({
                "Source": "Discretionary",
                "Program": g.get("program", "N/A"),
                "Amount": g.get("amount", 0),
                "Year": g.get("year", "N/A"),
                "Project": g.get("project", "N/A"),
                "Recipient": g.get("recipient", "N/A"),
                "Status": g.get("status", "N/A"),
            })
    
    if all_grants:
        total = sum(g.get("Amount", g.get("amount", 0)) for g in all_grants)
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Grant Funding", f"${total/1e6:.1f}M")
        col2.metric("Number of Awards", len(all_grants))
        if disc_grants:
            years = [g.get("year", 0) for g in disc_grants if g.get("year")]
            if years:
                col3.metric("Year Range", f"{min(years)}-{max(years)}")
        
        # Table
        display_data = []
        for g in sorted(all_grants, key=lambda x: x.get("Amount", x.get("amount", 0)), reverse=True):
            display_data.append({
                "Program": g.get("Program", g.get("program", "N/A")),
                "Amount": f"${g.get('Amount', g.get('amount', 0)):,.0f}",
                "Project": g.get("Project", g.get("project", "N/A")),
                "Year": g.get("Year", "—"),
                "Status": g.get("Status", "—"),
            })
        
        st.dataframe(pd.DataFrame(display_data), width="stretch", hide_index=True)
    else:
        st.info(f"No grant data found for {member_id}")


def _render_legislation_section(member_id, name, bills_data, ilga_data):
    """Render transportation legislation authored/co-authored."""
    st.markdown("### 📜 Transportation Legislation")
    
    found_bills = []
    
    # Check district-indexed bills (federal)
    district_bills = bills_data.get(member_id, {}).get("bills", [])
    if district_bills:
        for b in district_bills:
            found_bills.append({
                "Number": b.get("number", "N/A"),
                "Title": (b.get("title") or "No title")[:80],
                "Relationship": b.get("relationship", "Related"),
                "Source": "Congress.gov",
            })
    
    # Check ILGA transport bills for state legislators
    if ilga_data and "transport_bills" in ilga_data:
        name_lower = name.lower() if name else ""
        for bill_id, bill in ilga_data.get("transport_bills", {}).items():
            sponsor = (bill.get("sponsor") or "").lower()
            if name_lower and name_lower in sponsor:
                found_bills.append({
                    "Number": bill.get("number", bill_id),
                    "Title": (bill.get("title") or "No title")[:80],
                    "Relationship": "Sponsor",
                    "Status": bill.get("status", "Unknown"),
                    "Source": "IL General Assembly",
                })
    
    if found_bills:
        st.success(f"📜 {len(found_bills)} transportation-related bills found")
        
        tab_s, tab_all = st.tabs(["✍️ Sponsored/Authored", "📋 All Related"])
        
        with tab_s:
            sponsored = [b for b in found_bills if b.get("Relationship") in ("Sponsor", "Author", "Primary")]
            if sponsored:
                st.dataframe(pd.DataFrame(sponsored), width="stretch", hide_index=True)
            else:
                st.info("No directly sponsored transportation bills found")
        
        with tab_all:
            st.dataframe(pd.DataFrame(found_bills), width="stretch", hide_index=True)
    else:
        st.info("No transportation legislation found for this member. Bill data may need to be fetched.")


def _render_report_generator(member_id, name, party, area):
    """Render the Document Master report generator buttons."""
    try:
        from tools.document_master.ui_report import render_report_generator
        
        # Build dashboard context inline
        try:
            from app import build_dashboard_context
            ctx = build_dashboard_context()
        except Exception:
            ctx = ""
        
        render_report_generator(
            member_data={
                "id": member_id,
                "name": name,
                "party": party,
                "area": area,
            },
            dashboard_context=ctx,
        )
    except ImportError:
        st.caption("📋 Report generator available after running setup_document_master.sh")


# ═══════════════════════════════════════════════════════════════
# Main Render Function
# ═══════════════════════════════════════════════════════════════

def render_member_profiles(members_data=None, road_events=None, grants=None, legislation=None):
    """
    Main entry point — renders the full Member Profiles page.
    
    Called from app.py:
        render_member_profiles(
            members_data=members_data,
            road_events=...,
            grants=...,
            legislation=...,
        )
    """
    st.header("🧑 Member Profiles")
    
    if not isinstance(members_data, dict):
        members_data = _load_members()
    
    if not members_data:
        st.warning("No member data loaded. Check members.json.")
        return
    
    # Load supplementary data
    disc_grants_data = _load_discretionary_grants()
    formula_data = _load_formula_allocations()
    bills_data = _load_real_bills()
    ilga_data = _load_ilga_data()
    boundaries = _get_district_boundaries()
    
    # Load DISTRICTS from app.py for congressional data
    # We need to import it carefully to avoid circular imports
    districts_dict = {}
    try:
        # Read DISTRICTS directly from app context
        import __main__
        districts_dict = getattr(__main__, "DISTRICTS", {})
    except Exception:
        pass
    
    # ─── Geography Selector ─────────────────────────────────
    geo = st.radio(
        "Select Chamber:",
        ["🏛️ Congressional (17)", "🏠 IL House (118)", "🏛️ IL Senate (59)"],
        horizontal=True,
        key="mp_geo_select",
    )
    
    st.markdown("---")
    
    # ─── Congressional Profiles ─────────────────────────────
    if geo.startswith("🏛️ Congressional"):
        if not districts_dict:
            st.warning("Congressional district data not available. DISTRICTS dict not loaded.")
            return
        
        # District selector
        district_ids = sorted(districts_dict.keys())
        labels = [f"{d}: {districts_dict[d]['rep']} ({districts_dict[d]['party']})" for d in district_ids]
        
        selected_label = st.selectbox("Select Member:", labels, key="mp_cong_select")
        selected_id = district_ids[labels.index(selected_label)]
        info = districts_dict[selected_id]
        
        name = info["rep"]
        party = info["party"]
        area = info.get("area", "")
        
        # Photo
        photo = _find_photo(selected_id, name, "federal")
        
        # Header
        _render_profile_header(
            selected_id, name, party, area,
            committees=info.get("committees"),
            photo_path=photo,
            geo_type="federal",
        )
        
        st.markdown("---")
        
        # Gather data for map
        closures = info.get("closures", [])
        construction = info.get("construction", [])
        grants_inline = info.get("grants", [])
        
        # Get discretionary grants for this district
        disc_grants = [
            g for g in disc_grants_data.get("grants", [])
            if g.get("district") == selected_id
        ]
        
        # Get road events from pipeline
        road_ev = _load_road_events(f"US-IL-CD-{int(selected_id.split('-')[1]):02d}")
        
        # Boundary
        boundary = boundaries.get(selected_id)
        fallback = info.get("boundary")
        
        # Map with layers
        _render_map_section(
            selected_id, party, boundary, fallback,
            info.get("lat"), info.get("lon"),
            closures, construction, grants_inline,
            disc_grants, road_ev, zoom=10,
        )
        
        st.markdown("---")
        
        # Closures & Construction detail
        _render_closures_construction(closures, construction)
        
        st.markdown("---")
        
        # Grants
        _render_grants_section(grants_inline, disc_grants, selected_id)
        
        st.markdown("---")
        
        # Federal Funding
        try:
            if selected_id in formula_data.get("district_allocations", {}):
                alloc = formula_data["district_allocations"][selected_id]
                st.markdown("### 💰 Formula Allocations (FY26)")
                fc1, fc2, fc3, fc4 = st.columns(4)
                fc1.metric("Total Formula", f"${alloc['total_formula_est']/1e6:.1f}M")
                fc2.metric("STBG", f"${alloc['stbg_formula']/1e6:.1f}M")
                fc3.metric("NHPP", f"${alloc['nhpp_est']/1e6:.1f}M")
                fc4.metric("Per Capita", f"${alloc['per_capita']:.0f}")
                st.markdown("---")
        except Exception:
            pass
        
        # Legislation
        _render_legislation_section(selected_id, name, bills_data, ilga_data)
        
        st.markdown("---")
        
        # Report Generator
        _render_report_generator(selected_id, name, party, area)
    
    # ─── IL House / Senate Profiles ─────────────────────────
    else:
        if geo.startswith("🏠"):
            chamber_key = "il_house"
            prefix = "IL-H-"
            max_num = 118
            fmt = 3
            chamber_label = "IL House"
        else:
            chamber_key = "il_senate"
            prefix = "IL-S-"
            max_num = 59
            fmt = 3
            chamber_label = "IL Senate"
        
        roster = members_data.get(chamber_key, {})
        
        if not roster:
            st.warning(f"No {chamber_label} data in members.json")
            return
        
        # Build selection list
        member_ids = sorted(roster.keys())
        labels = []
        for mid in member_ids:
            m = roster[mid]
            labels.append(f"{mid}: {m.get('name', 'Unknown')} ({m.get('party', '?')})")
        
        selected_label = st.selectbox(f"Select {chamber_label} Member:", labels, key=f"mp_{chamber_key}_select")
        selected_id = member_ids[labels.index(selected_label)]
        info = roster[selected_id]
        
        name = info.get("name", f"District {info.get('district', '?')}")
        party = info.get("party", "?")
        area = info.get("area", "")
        district_num = info.get("district", 0)
        
        # Photo
        photo = _find_photo(selected_id, name, chamber_key)
        
        # Header
        _render_profile_header(
            selected_id, name, party, area,
            photo_path=photo,
            geo_type=chamber_key,
        )
        
        st.markdown("---")
        
        # Map congressional district for this state district
        if prefix.startswith("IL-H"):
            total_state = 118
        else:
            total_state = 59
        congress_idx = math.ceil(district_num / (total_state / 17.0)) if district_num else 1
        congress_idx = max(1, min(17, int(congress_idx)))
        cong_key = f"IL-{congress_idx:02d}"
        
        st.caption(f"*Mapped to Congressional District: {cong_key}*")
        
        # Get data from mapped congressional district
        cong_info = districts_dict.get(cong_key, {}) if districts_dict else {}
        closures = cong_info.get("closures", [])
        construction = cong_info.get("construction", [])
        grants_inline = cong_info.get("grants", [])
        
        disc_grants = [
            g for g in disc_grants_data.get("grants", [])
            if g.get("district") == cong_key
        ]
        
        # Road events from pipeline (try state district key first, then congressional)
        road_ev = _load_road_events(selected_id) or _load_road_events(
            f"US-IL-CD-{congress_idx:02d}"
        )
        
        # Boundary (try state district, fall back to congressional)
        boundary = _load_boundary(selected_id) or boundaries.get(cong_key)
        fallback = cong_info.get("boundary")
        
        center_lat = cong_info.get("lat", 40.0)
        center_lon = cong_info.get("lon", -89.0)
        
        # Map
        _render_map_section(
            selected_id, party, boundary, fallback,
            center_lat, center_lon,
            closures, construction, grants_inline,
            disc_grants, road_ev, zoom=9,
        )
        
        st.markdown("---")
        
        # Closures & Construction
        _render_closures_construction(closures, construction)
        
        st.markdown("---")
        
        # Grants
        _render_grants_section(grants_inline, disc_grants, selected_id)
        
        st.markdown("---")
        
        # Federal Funding from mapped congressional district
        try:
            if cong_key in formula_data.get("district_allocations", {}):
                alloc = formula_data["district_allocations"][cong_key]
                st.markdown(f"### 💰 Formula Allocations — via {cong_key} (FY26)")
                fc1, fc2, fc3, fc4 = st.columns(4)
                fc1.metric("Total Formula", f"${alloc['total_formula_est']/1e6:.1f}M")
                fc2.metric("STBG", f"${alloc['stbg_formula']/1e6:.1f}M")
                fc3.metric("NHPP", f"${alloc['nhpp_est']/1e6:.1f}M")
                fc4.metric("Per Capita", f"${alloc['per_capita']:.0f}")
                st.markdown("---")
        except Exception:
            pass
        
        # Legislation
        _render_legislation_section(selected_id, name, bills_data, ilga_data)
        
        st.markdown("---")
        
        # Report Generator
        _render_report_generator(selected_id, name, party, area)
