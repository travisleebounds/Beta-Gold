#!/usr/bin/env python3
"""
Fetch Official Profile Photos — IDOT Dashboard
=================================================
Downloads official headshots for all members:
  - 17 Congressional reps (from house.gov / senate.gov / bioguide)
  - 2 US Senators
  - 118 IL House members (from ilga.gov)
  - 59 IL Senate members (from ilga.gov)

Photos are saved to:
  data/members/federal/{member_id}/photo.jpg
  data/members/il_house/{member_id}/photo.jpg
  data/members/il_senate/{member_id}/photo.jpg

Usage:
  python3 fetch_member_photos.py
"""

import json
import os
import time
import re
import urllib.request
import urllib.error
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

MEMBERS_JSON = "members.json"
PHOTO_BASE = Path("data/members")
DELAY = 1.0  # Seconds between requests (be polite)

# Congressional members — bioguide IDs for official photos
# These map to https://bioguide.congress.gov/bioguide/photo/{LETTER}/{bioguide_id}.jpg
# or https://www.congress.gov/img/member/{bioguide_id}_200.jpg
CONGRESSIONAL_PHOTOS = {
    "IL-01": {
        "name": "Jonathan Jackson",
        "bioguide": "J000308",
    },
    "IL-02": {
        "name": "Robin Kelly",
        "bioguide": "K000385",
    },
    "IL-03": {
        "name": "Delia Ramirez",
        "bioguide": "R000617",
    },
    "IL-04": {
        "name": "Jesús García",
        "bioguide": "G000586",
    },
    "IL-05": {
        "name": "Mike Quigley",
        "bioguide": "Q000023",
    },
    "IL-06": {
        "name": "Sean Casten",
        "bioguide": "C001117",
    },
    "IL-07": {
        "name": "Danny Davis",
        "bioguide": "D000096",
    },
    "IL-08": {
        "name": "Raja Krishnamoorthi",
        "bioguide": "K000391",
    },
    "IL-09": {
        "name": "Jan Schakowsky",
        "bioguide": "S001145",
    },
    "IL-10": {
        "name": "Brad Schneider",
        "bioguide": "S001190",
    },
    "IL-11": {
        "name": "Bill Foster",
        "bioguide": "F000454",
    },
    "IL-12": {
        "name": "Mike Bost",
        "bioguide": "B001295",
    },
    "IL-13": {
        "name": "Nikki Budzinski",
        "bioguide": "B001316",
    },
    "IL-14": {
        "name": "Lauren Underwood",
        "bioguide": "U000040",
    },
    "IL-15": {
        "name": "Mary Miller",
        "bioguide": "M001211",
    },
    "IL-16": {
        "name": "Darin LaHood",
        "bioguide": "L000585",
    },
    "IL-17": {
        "name": "Eric Sorensen",
        "bioguide": "S001221",
    },
}

SENATOR_PHOTOS = {
    "IL-SEN-Durbin": {
        "name": "Dick Durbin",
        "bioguide": "D000563",
    },
    "IL-SEN-Duckworth": {
        "name": "Tammy Duckworth",
        "bioguide": "D000622",
    },
}


def download_file(url: str, dest: str, headers: dict = None) -> bool:
    """Download a file from URL to destination."""
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) IDOT-Dashboard/1.0")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        
        with urllib.request.urlopen(req, timeout=15) as response:
            if response.status == 200:
                data = response.read()
                if len(data) > 1000:  # Sanity check — real photo should be > 1KB
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "wb") as f:
                        f.write(data)
                    return True
                else:
                    print(f"    ⚠️ Too small ({len(data)} bytes), skipping")
                    return False
    except urllib.error.HTTPError as e:
        print(f"    ❌ HTTP {e.code}: {url}")
    except Exception as e:
        print(f"    ❌ Error: {e}")
    return False


# ═══════════════════════════════════════════════════════════════
# Federal Members
# ═══════════════════════════════════════════════════════════════

def fetch_congressional_photos():
    """Download photos for all 17 IL Congressional members + 2 Senators."""
    print("\n🏛️  Fetching Congressional member photos...")
    
    all_members = {**CONGRESSIONAL_PHOTOS, **SENATOR_PHOTOS}
    success = 0
    
    for member_id, info in all_members.items():
        bioguide = info["bioguide"]
        name = info["name"]
        dest_dir = PHOTO_BASE / "federal" / member_id
        dest = dest_dir / "photo.jpg"
        
        if dest.exists():
            print(f"  ✅ {member_id} ({name}) — already exists")
            success += 1
            continue
        
        print(f"  📥 {member_id} ({name})...")
        
        # Try multiple sources
        urls = [
            # Congress.gov official 200px
            f"https://www.congress.gov/img/member/{bioguide}_200.jpg",
            # Bioguide high-res
            f"https://bioguide.congress.gov/bioguide/photo/{bioguide[0]}/{bioguide}.jpg",
            # theunitedstates.io (open source project)
            f"https://theunitedstates.io/images/congress/450x550/{bioguide}.jpg",
            f"https://theunitedstates.io/images/congress/225x275/{bioguide}.jpg",
        ]
        
        downloaded = False
        for url in urls:
            if download_file(url, str(dest)):
                print(f"    ✅ Downloaded from {url.split('/')[2]}")
                success += 1
                downloaded = True
                break
            time.sleep(0.5)
        
        if not downloaded:
            print(f"    ⚠️ Could not download photo for {name}")
        
        time.sleep(DELAY)
    
    print(f"\n  Congressional: {success}/{len(all_members)} photos downloaded")
    return success


# ═══════════════════════════════════════════════════════════════
# IL General Assembly Members
# ═══════════════════════════════════════════════════════════════

def fetch_ilga_photos():
    """
    Download photos for IL House and Senate members.
    
    ILGA member photos are available at:
    https://www.ilga.gov/house/Rep.asp?MemberID={id}
    https://www.ilga.gov/senate/Senator.asp?MemberID={id}
    
    But we need to scrape the member pages to find the photo URLs.
    Alternative: use the member list pages to get photo links.
    
    Simpler approach: ILGA photos follow pattern:
    https://www.ilga.gov/images/members/{MemberID}.jpg
    But MemberID != district number, it's an internal ID.
    
    We'll try the predictable patterns first.
    """
    print("\n🏠 Fetching IL General Assembly member photos...")
    
    members_data = {}
    if os.path.exists(MEMBERS_JSON):
        with open(MEMBERS_JSON) as f:
            members_data = json.load(f)
    
    il_house = members_data.get("il_house", {})
    il_senate = members_data.get("il_senate", {})
    
    house_success = 0
    senate_success = 0
    
    # ─── IL House ───────────────────────────────────────────
    print(f"\n  🏠 IL House ({len(il_house)} members)...")
    
    for member_id, info in sorted(il_house.items()):
        name = info.get("name", "Unknown")
        district = info.get("district", 0)
        dest_dir = PHOTO_BASE / "il_house" / member_id
        dest = dest_dir / "photo.jpg"
        
        if dest.exists():
            house_success += 1
            continue
        
        # Build search-friendly name parts
        name_parts = name.strip().split()
        if len(name_parts) < 2:
            continue
        
        last_name = name_parts[-1]
        first_name = name_parts[0]
        
        # Try ILGA photo patterns
        # Pattern 1: direct member photo by name
        # Pattern 2: ilga.gov uses internal IDs, but we can try common patterns
        urls = [
            # Illinois General Assembly member photos (common patterns)
            f"https://www.ilga.gov/images/members/HousePhotos/{district:03d}.jpg",
            f"https://www.ilga.gov/images/members/{last_name}{first_name[0]}.jpg",
            # Illinois House Democrats/Republicans photo pages
            f"https://www.ilhousedems.com/wp-content/uploads/member-photos/{last_name.lower()}.jpg",
            f"https://www.ilhouserepublicans.com/wp-content/uploads/member-photos/{last_name.lower()}.jpg",
        ]
        
        downloaded = False
        for url in urls:
            if download_file(url, str(dest)):
                print(f"  ✅ {member_id}: {name}")
                house_success += 1
                downloaded = True
                break
            time.sleep(0.3)
        
        if not downloaded:
            # Create placeholder marker so we know we tried
            os.makedirs(str(dest_dir), exist_ok=True)
        
        time.sleep(DELAY * 0.5)
    
    print(f"  IL House: {house_success}/{len(il_house)} photos")
    
    # ─── IL Senate ──────────────────────────────────────────
    print(f"\n  🏛️ IL Senate ({len(il_senate)} members)...")
    
    for member_id, info in sorted(il_senate.items()):
        name = info.get("name", "Unknown")
        district = info.get("district", 0)
        dest_dir = PHOTO_BASE / "il_senate" / member_id
        dest = dest_dir / "photo.jpg"
        
        if dest.exists():
            senate_success += 1
            continue
        
        name_parts = name.strip().split()
        if len(name_parts) < 2:
            continue
        
        last_name = name_parts[-1]
        first_name = name_parts[0]
        
        urls = [
            f"https://www.ilga.gov/images/members/SenatePhotos/{district:03d}.jpg",
            f"https://www.ilga.gov/images/members/{last_name}{first_name[0]}.jpg",
            f"https://www.ilsenatedemocrats.com/wp-content/uploads/member-photos/{last_name.lower()}.jpg",
            f"https://www.ilsenategop.org/wp-content/uploads/member-photos/{last_name.lower()}.jpg",
        ]
        
        downloaded = False
        for url in urls:
            if download_file(url, str(dest)):
                print(f"  ✅ {member_id}: {name}")
                senate_success += 1
                downloaded = True
                break
            time.sleep(0.3)
        
        if not downloaded:
            os.makedirs(str(dest_dir), exist_ok=True)
        
        time.sleep(DELAY * 0.5)
    
    print(f"  IL Senate: {senate_success}/{len(il_senate)} photos")
    
    return house_success, senate_success


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("═" * 55)
    print("  IDOT Dashboard — Member Photo Fetcher")
    print("═" * 55)
    
    # Federal
    fed_count = fetch_congressional_photos()
    
    # State
    house_count, senate_count = fetch_ilga_photos()
    
    # Summary
    print("\n" + "═" * 55)
    print("  Summary")
    print("═" * 55)
    print(f"  🏛️ Congressional:  {fed_count}/19 photos")
    print(f"  🏠 IL House:       {house_count}/118 photos")
    print(f"  🏛️ IL Senate:      {senate_count}/59 photos")
    print()
    
    # Count total on disk
    total = 0
    for root, dirs, files in os.walk(str(PHOTO_BASE)):
        for f in files:
            if f.startswith("photo."):
                total += 1
    
    print(f"  📸 Total photos on disk: {total}")
    print()
    print("  Photos saved to: data/members/{chamber}/{member_id}/photo.jpg")
    print()
    print("  For missing state legislator photos, you can manually download from:")
    print("  https://www.ilga.gov/house/ and https://www.ilga.gov/senate/")
    print()
