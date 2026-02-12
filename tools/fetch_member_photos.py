from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen

DATA_DIR = Path("data")
OUT_BASE = Path("data/members")
BIOGUIDE_PROFILE = "https://bioguide.congress.gov/search/bio/{bioguide}"
BIOGUIDE_BASE = "https://bioguide.congress.gov"

BIOGUIDE_ID_RE = re.compile(r"^[A-Z]\d{6}$")

UA = "idot-dashboard-betagold/1.0 (photo fetch)"

def load_members_json() -> Path:
    # Prefer repo-root members.json if present (your output shows it exists)
    candidates = [Path("members.json"), DATA_DIR / "members.json", DATA_DIR / "members_data.json", Path("members_data.json")]
    for p in candidates:
        if p.exists():
            return p
    raise SystemExit("❌ Could not find members.json / members_data.json")

def http_get_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")

def http_get_bytes(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=60) as r:
        return r.read()

def extract_bioguide(member_id: str, info: dict) -> str | None:
    for key in ["bioguide", "bioguide_id", "bioguideId", "bioguideID", "id_bioguide"]:
        val = info.get(key)
        if isinstance(val, str) and BIOGUIDE_ID_RE.match(val.strip()):
            return val.strip()

    if isinstance(member_id, str) and BIOGUIDE_ID_RE.match(member_id):
        return member_id

    for _, val in info.items():
        if isinstance(val, str):
            m = re.search(r"\b([A-Z]\d{6})\b", val)
            if m:
                return m.group(1)

    return None

def bioguide_photo_url(bioguide: str) -> str | None:
    html = http_get_text(BIOGUIDE_PROFILE.format(bioguide=bioguide))
    m = re.search(r'"/photo/([^"]+?\.jpg)"', html, flags=re.IGNORECASE)
    if not m:
        return None
    return BIOGUIDE_BASE + "/photo/" + m.group(1)

def bucket_to_folder(bucket_key: str) -> str:
    lk = bucket_key.lower()
    if "congress" in lk or "federal" in lk:
        return "federal"
    if "house" in lk:
        return "il_house"
    if "senate" in lk:
        return "il_senate"
    return bucket_key

def main():
    src = load_members_json()
    members_data = json.loads(src.read_text())
    if not isinstance(members_data, dict):
        raise SystemExit(f"❌ {src} is not a dict")

    # Args:
    #   python3 tools/fetch_member_photos.py <bucket_key>
    bucket_key = sys.argv[1] if len(sys.argv) > 1 else None

    dict_keys = [k for k, v in members_data.items() if isinstance(v, dict)]
    if not bucket_key:
        print(f"✅ Using members file: {src}")
        print("Pick a bucket key and rerun:")
        for k in sorted(dict_keys):
            v = members_data.get(k)
            print(f"  - {k} ({len(v) if isinstance(v, dict) else 0})")
        print("\nExample:")
        print("  python3 tools/fetch_member_photos.py congressional")
        sys.exit(0)

    if bucket_key not in members_data or not isinstance(members_data.get(bucket_key), dict):
        raise SystemExit(f"❌ Bucket '{bucket_key}' not found or not a dict. Run without args to list keys.")

    bucket = members_data[bucket_key]
    out_bucket = bucket_to_folder(bucket_key)
    out_base = OUT_BASE / out_bucket
    out_base.mkdir(parents=True, exist_ok=True)

    print(f"✅ Using members file: {src}")
    print(f"✅ Using bucket: {bucket_key} -> data/members/{out_bucket}/ ({len(bucket)} records)")

    ok = 0
    skipped = 0
    missing = 0

    # Only fetch bioguide portraits if we can find bioguide ids
    for member_id, info_any in sorted(bucket.items()):
        info = info_any if isinstance(info_any, dict) else {}
        bioguide = extract_bioguide(member_id, info)
        if not bioguide:
            missing += 1
            continue

        url = bioguide_photo_url(bioguide)
        if not url:
            missing += 1
            continue

        out_dir = out_base / member_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "photo.jpg"

        if out_file.exists() and out_file.stat().st_size > 10_000:
            skipped += 1
            continue

        try:
            data = http_get_bytes(url)
            out_file.write_bytes(data)
            ok += 1
            print(f"✅ {member_id}: {bioguide} -> photo.jpg")
        except Exception as e:
            missing += 1
            print(f"⚠️  {member_id}: download failed: {e}")

    print("\n==== SUMMARY ====")
    print(f"Downloaded: {ok}")
    print(f"Already had: {skipped}")
    print(f"Missing/failed: {missing}")
    print("\nNOTE: Bioguide portraits only work for FEDERAL members. ILGA needs a different source.")
