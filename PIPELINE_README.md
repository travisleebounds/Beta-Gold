# IDOT Dashboard v2 — Data Pipeline

## Architecture

```
fetch_boundaries.py          fetch_road_events.py
      │                              │
      ▼                              ▼
data/boundaries/             data/road/
  US-IL-CD-01.geojson          US-IL-CD-01.json
  US-IL-CD-02.geojson          US-IL-CD-02.json
  ...                          ...
  IL-H-001.geojson             IL-H-001.json
  IL-H-002.geojson             IL-H-002.json
  ...                          ...
  IL-S-001.geojson             IL-S-001.json
  ...                          ...
                               US-IL-SEN.json  ← statewide top-5
      └──────────┬──────────────┘
                 ▼
              app.py (Streamlit)
              members.json (roster)
```

## District Key Format

| Geography     | Pattern         | Example        | Count |
|---------------|-----------------|----------------|-------|
| Congressional | `US-IL-CD-##`   | `US-IL-CD-05`  | 17    |
| IL House      | `IL-H-###`      | `IL-H-042`     | 118   |
| IL Senate     | `IL-S-###`      | `IL-S-021`     | 59    |
| US Senators   | `US-IL-SEN`     | `US-IL-SEN`    | 1     |

## Quick Start

```bash
# 1. Install deps
pip install streamlit pandas folium streamlit-folium altair pillow requests

# 2. Run the full pipeline (boundaries + road events)
python setup_pipeline.py

# 3. Launch dashboard
streamlit run app.py
```

## Scripts

### `fetch_boundaries.py`
Downloads GeoJSON district boundaries from Census Bureau ArcGIS services.
- Congressional: Census TIGER 119th Congress
- IL House: Census TIGER SLDL (State Legislative District Lower)
- IL Senate: Census TIGER SLDU (State Legislative District Upper)

Run once. Re-run only if district boundaries change.

### `fetch_road_events.py`
Queries IDOT ArcGIS open data layers:
- **Road Construction** — active construction projects
- **Road Closures** — lane closures, road closures
- **Road Restrictions** — weight/height restrictions, obstructions

For each district boundary, performs spatial intersect query, normalizes results
into a standard `RoadEvent` schema, scores by severity, and saves per-district JSON.

Also builds `US-IL-SEN.json` — a statewide aggregate with the top 5 issues
(for US Senator briefings).

### `setup_pipeline.py`
Convenience wrapper:
- `python setup_pipeline.py` — full setup (boundaries + events)
- `python setup_pipeline.py refresh` — just refresh road events (daily)
- `python setup_pipeline.py boundaries` — just fetch boundaries

## RoadEvent Schema

```json
{
  "id": "construction:12345",
  "type": "construction|closure|restriction",
  "status": "active|planned|ended|unknown",
  "road": "I-90",
  "direction": "Eastbound",
  "location_text": "Near Rockford",
  "county": "Winnebago",
  "description": "Bridge deck replacement",
  "lanes": "Right lane closed",
  "start": "2026-01-15T00:00:00Z",
  "end": "2026-06-30T00:00:00Z",
  "last_updated": "2026-02-05T12:00:00Z",
  "lat": 42.2711,
  "lon": -89.0940,
  "source_url": "https://www.gettingaroundillinois.com/",
  "severity": 85,
  "source_layer": "construction"
}
```

## Scoring Algorithm

| Factor                          | Points |
|---------------------------------|--------|
| Type: closure                   | +60    |
| Type: restriction               | +40    |
| Type: construction              | +25    |
| Status: active                  | +20    |
| Interstate (I-*)                | +15    |
| US route (US-*)                 | +10    |
| State route (IL-*)              | +5     |
| "road closed" / "all lanes"    | +20    |
| "closed" (partial)             | +10    |
| End date within 48 hours       | +10    |

## Refresh Schedule

- **Boundaries**: Once (or when redistricting happens)
- **Road events**: Daily recommended, or 2x/day for closures
- **Cron example**: `0 6 * * * cd /path/to/repo && python setup_pipeline.py refresh`

## Troubleshooting

**"No boundary files found"**
→ Run `python fetch_boundaries.py`. If ArcGIS endpoints fail, download
  shapefiles manually from Census Bureau and convert with geopandas.

**"0 features returned for a district"**
→ The IDOT layer might be empty for that area, or the spatial query failed.
  Check the boundary file exists and has valid geometry.

**ArcGIS rate limiting**
→ The scripts include rate limit delays (0.3-1s between requests).
  If you still get throttled, increase the sleep values.
