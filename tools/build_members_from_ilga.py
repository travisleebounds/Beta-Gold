import json
import re
from pathlib import Path
from io import StringIO

import pandas as pd
import requests

HOUSE_URL = "https://www.ilga.gov/House/Members/rptMemberList"
SENATE_URL = "https://www.ilga.gov/Senate/Members/rptMemberList"

REP_RE = re.compile(
    r"^\s*(?P<name>.+?)\s*\((?P<party>[DRI])\)\s+(?P<district>\d{1,3})(?:st|nd|rd|th)\s+District\s*$"
)

def fetch_table(url: str) -> pd.DataFrame:
    html = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).text
    tables = pd.read_html(StringIO(html))
    if not tables:
        raise RuntimeError(f"No tables found at {url}")
    # pick the largest table
    return max(tables, key=lambda df: df.shape[0]).copy()

def parse_member_cell(cell: str):
    s = str(cell).strip()
    m = REP_RE.match(s)
    if not m:
        return None
    return m.group("name").strip(), m.group("party").strip(), int(m.group("district"))

def parse_house():
    df = fetch_table(HOUSE_URL)
    if "Representative" not in df.columns:
        raise RuntimeError(f"Expected 'Representative' column, got: {list(df.columns)}")

    out = {}
    for cell in df["Representative"].tolist():
        parsed = parse_member_cell(cell)
        if not parsed:
            continue
        name, party, dist = parsed
        key = f"IL-H-{dist:03d}"
        out[key] = {
            "name": name,
            "party": party,
            "district": dist,
            "source": HOUSE_URL,
        }
    return out

def parse_senate():
    df = fetch_table(SENATE_URL)

    # Column name is usually "Senator" but let's be tolerant
    col = None
    for c in df.columns:
        if str(c).strip().lower() in ("senator", "senators", "member"):
            col = c
            break
    if col is None:
        # fallback: first column
        col = df.columns[0]

    out = {}
    for cell in df[col].tolist():
        parsed = parse_member_cell(cell)
        if not parsed:
            continue
        name, party, dist = parsed
        key = f"IL-S-{dist:03d}"
        out[key] = {
            "name": name,
            "party": party,
            "district": dist,
            "source": SENATE_URL,
        }
    return out

def main():
    il_house = parse_house()
    il_senate = parse_senate()

    out = {
        "generated_from": {"house": HOUSE_URL, "senate": SENATE_URL},
        "il_house": il_house,
        "il_senate": il_senate,
    }

    Path("members.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print("✅ Wrote members.json")
    print(f"House: {len(il_house)} /118")
    print(f"Senate: {len(il_senate)} /59")

    missing_house = [f"IL-H-{i:03d}" for i in range(1, 119) if f"IL-H-{i:03d}" not in il_house]
    missing_senate = [f"IL-S-{i:03d}" for i in range(1, 60) if f"IL-S-{i:03d}" not in il_senate]

    if missing_house:
        print("Missing House keys:", ", ".join(missing_house[:25]), "..." if len(missing_house) > 25 else "")
    if missing_senate:
        print("Missing Senate keys:", ", ".join(missing_senate[:25]), "..." if len(missing_senate) > 25 else "")

    # Helpful debug if something didn't match
    if len(il_house) < 118:
        print("\nDEBUG: Example House cells that did not match regex:")
        df = fetch_table(HOUSE_URL)
        bad = []
        for cell in df["Representative"].tolist():
            if not REP_RE.match(str(cell).strip()):
                bad.append(str(cell))
            if len(bad) >= 5:
                break
        for b in bad:
            print("  ", b)

if __name__ == "__main__":
    main()

