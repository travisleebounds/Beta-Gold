from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

UA = "idot-dashboard-betagold/1.0 (ilga photo fetch)"

MEMBERS_JSON = Path("members.json")
OUT_BASE = Path("data/members")

def http_get(url: str) -> str:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=45) as r:
        return r.read().decode("utf-8", errors="replace")

def http_get_bytes(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=60) as r:
        return r.read()

def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def build_district_to_profile(list_url: str) -> dict[int, str]:
    """
    ILGA member list pages generally contain links to individual member pages.
    We'll extract all hrefs that look like /House/Members/Details? or /Senate/Members/Details?
    and also capture nearby district numbers when present.
    """
    html = http_get(list_url)

    # Grab all candidate profile links from the page
    hrefs = re.findall(r'href="([^"]+)"', html, flags=re.IGNORECASE)
    links = []
    for h in hrefs:
        if "/House/Members/" in h or "/Senate/Members/" in h:
            # Filter out list pages and obvious non-profile pages
            if "rptMemberList" in h:
                continue
            if "Members" in h:
                links.append(urljoin(list_url, h))

    links = sorted(set(links))

    # If list doesn't contain direct profile links, we can't proceed.
    # (But empirically, ILGA pages do.)
    if not links:
        return {}

    # Build mapping by visiting each profile link and reading district number from profile page
    mapping: dict[int, str] = {}
    for url in links:
        try:
            prof = http_get(url)
        except Exception:
            continue

        # Try to find "District:" or "District" marker
        m = re.search(r"District\s*[:#]?\s*</?[^>]*>\s*(\d{1,3})", prof, flags=re.IGNORECASE)
        if not m:
            # alternate: plain text "District 12"
            m = re.search(r"\bDistrict\s+(\d{1,3})\b", prof, flags=re.IGNORECASE)
        if not m:
            continue

        d = int(m.group(1))
        # Prefer first seen; later ones are usually duplicates
        mapping.setdefault(d, url)

    return mapping

def extract_headshot_url(profile_url: str) -> str | None:
    html = http_get(profile_url)

    # Heuristic: look for first jpg/png under /images/ or containing "Members" in path
    # ILGA has historically used /images/house/ or similar patterns.
    candidates = re.findall(r'<img[^>]+src="([^"]+)"', html, flags=re.IGNORECASE)
    scored = []
    for src in candidates:
        absu = urljoin(profile_url, src)
        s = absu.lower()
        score = 0
        if "member" in s: score += 3
        if "head" in s or "photo" in s or "portrait" in s: score += 3
        if "/images/" in s: score += 2
        if s.endswith(".jpg") or s.endswith(".jpeg"): score += 2
        if s.endswith(".png"): score += 1
        if "seal" in s or "logo" in s: score -= 5
        scored.append((score, absu))

    if not scored:
        return None

    scored.sort(key=lambda t: t[0], reverse=True)
    best = scored[0][1]
    return best

def save_photo(bucket_folder: str, member_id: str, img_url: str) -> bool:
    out_dir = OUT_BASE / bucket_folder / member_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Save as photo.jpg by default; if PNG, keep png
    lower = img_url.lower()
    ext = ".jpg"
    if lower.endswith(".png"):
        ext = ".png"
    elif lower.endswith(".jpeg"):
        ext = ".jpeg"
    elif lower.endswith(".jpg"):
        ext = ".jpg"

    out_file = out_dir / ("photo" + ext)

    if out_file.exists() and out_file.stat().st_size > 10_000:
        return True

    data = http_get_bytes(img_url)
    out_file.write_bytes(data)
    return True

def main():
    if not MEMBERS_JSON.exists():
        raise SystemExit("❌ members.json not found at repo root")

    members = json.load(open(MEMBERS_JSON, "r"))
    if not isinstance(members, dict):
        raise SystemExit("❌ members.json top-level is not a dict")

    # Get list URLs from the first record "source" fields
    house_any = next(iter(members.get("il_house", {}).values()), {})
    sen_any = next(iter(members.get("il_senate", {}).values()), {})

    house_list_url = house_any.get("source")
    senate_list_url = sen_any.get("source")

    if not house_list_url or not senate_list_url:
        raise SystemExit("❌ Missing 'source' URLs in members.json records")

    print("✅ House list:", house_list_url)
    print("✅ Senate list:", senate_list_url)

    OUT_BASE.mkdir(parents=True, exist_ok=True)

    # Build district -> profile maps
    print("\nBuilding House district -> profile map…")
    house_map = build_district_to_profile(house_list_url)
    print("House profiles found:", len(house_map))

    print("\nBuilding Senate district -> profile map…")
    senate_map = build_district_to_profile(senate_list_url)
    print("Senate profiles found:", len(senate_map))

    # Fetch photos
    ok = 0
    miss = 0

    # House
    for member_id, info in sorted(members.get("il_house", {}).items()):
        d = int(info.get("district"))
        prof = house_map.get(d)
        if not prof:
            miss += 1
            continue
        try:
            img = extract_headshot_url(prof)
            if not img:
                miss += 1
                continue
            save_photo("il_house", member_id, img)
            ok += 1
            print(f"✅ House {member_id} (D{d}): photo saved")
        except Exception as e:
            miss += 1
            print(f"⚠️ House {member_id} (D{d}): {e}")

    # Senate
    for member_id, info in sorted(members.get("il_senate", {}).items()):
        d = int(info.get("district"))
        prof = senate_map.get(d)
        if not prof:
            miss += 1
            continue
        try:
            img = extract_headshot_url(prof)
            if not img:
                miss += 1
                continue
            save_photo("il_senate", member_id, img)
            ok += 1
            print(f"✅ Senate {member_id} (D{d}): photo saved")
        except Exception as e:
            miss += 1
            print(f"⚠️ Senate {member_id} (D{d}): {e}")

    print("\n==== SUMMARY ====")
    print("Saved photos:", ok)
    print("Missing/failed:", miss)
    print("\nPhotos are now under:")
    print("  data/members/il_house/IL-H-###/photo.*")
    print("  data/members/il_senate/IL-S-###/photo.*")
    print("\nRestart Streamlit to see them in Member Profiles.")
