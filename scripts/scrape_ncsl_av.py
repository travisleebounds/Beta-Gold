#!/usr/bin/env python3
"""Simple scraper to fetch the NCSL Autonomous Vehicles legislation page and
save a JSON snapshot at `ncsl_av_complete.json`.

It will also attempt a lightweight parse of state sections (headings) into a
`states` dictionary where possible. This is intentionally forgiving and saves
raw HTML so a more robust parser can be written later.
"""
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
from pathlib import Path
import argparse

URL = "https://www.ncsl.org/transportation/autonomous-vehicles-legislation-database"
OUT_PATH = Path.cwd() / "ncsl_av_complete.json"

# List of US states + District of Columbia for simple matching
STATES = [
    'Alabama','Alaska','Arizona','Arkansas','California','Colorado','Connecticut','Delaware',
    'Florida','Georgia','Hawaii','Idaho','Illinois','Indiana','Iowa','Kansas','Kentucky',
    'Louisiana','Maine','Maryland','Massachusetts','Michigan','Minnesota','Mississippi',
    'Missouri','Montana','Nebraska','Nevada','New Hampshire','New Jersey','New Mexico',
    'New York','North Carolina','North Dakota','Ohio','Oklahoma','Oregon','Pennsylvania',
    'Rhode Island','South Carolina','South Dakota','Tennessee','Texas','Utah','Vermont',
    'Virginia','Washington','West Virginia','Wisconsin','Wyoming','District of Columbia'
]


def fetch_page(url):
    resp = requests.get(url, timeout=30, headers={"User-Agent": "IDOT-Dashboard-Scraper/1.0"})
    resp.raise_for_status()
    return resp.text


def lightweight_parse(html):
    soup = BeautifulSoup(html, "html.parser")
    results = {}

    # Strategy: find headings (h2/h3) whose text equals a state name, then collect
    # the text of the following paragraph(s) until the next heading.
    for header_tag in soup.find_all(['h2', 'h3']):
        title = header_tag.get_text(separator=" ", strip=True)
        if title in STATES:
            # Collect sibling text until next heading
            content_parts = []
            for sib in header_tag.find_next_siblings():
                if sib.name in ('h2', 'h3'):
                    break
                text = sib.get_text(separator=' ', strip=True)
                if text:
                    content_parts.append(text)
            results[title] = {
                'summary': ' '.join(content_parts)[:5000]
            }
    return results


def load_existing(path: Path):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def save_output(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default=URL)
    parser.add_argument('--out', default=str(OUT_PATH))
    args = parser.parse_args()

    out_path = Path(args.out)

    print(f"Fetching {args.url}...")
    html = fetch_page(args.url)
    print("Fetched page — parsing...")

    parsed_states = lightweight_parse(html)
    existing = load_existing(out_path)

    out = {
        'metadata': {
            'source': 'NCSL Autonomous Vehicles Legislation Database',
            'source_url': args.url,
            'scraped_date': datetime.utcnow().isoformat(),
            'note': 'Lightweight scrape — also saved raw_html. For authoritative parsing, run a full scraper.'
        },
        'states': parsed_states if parsed_states else existing.get('states', {}),
        'raw_html': html
    }

    save_output(out_path, out)
    print(f"Saved snapshot to {out_path}")


if __name__ == '__main__':
    main()
