#!/usr/bin/env python3
"""
setup_pipeline.py — One-command setup for the IDOT dashboard data pipeline.

Usage:
  python setup_pipeline.py          # Full setup: boundaries + road events
  python setup_pipeline.py refresh  # Just refresh road events (daily cron)

Requires: pip install requests
"""

import subprocess
import sys
import os


def run(cmd, desc):
    print(f"\n{'─'*50}")
    print(f"▶ {desc}")
    print(f"  $ {cmd}")
    print(f"{'─'*50}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"  ⚠️  {desc} returned non-zero exit code: {result.returncode}")
    return result.returncode


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"

    print("=" * 60)
    print("IDOT Dashboard — Pipeline Setup")
    print("=" * 60)

    # Check dependencies
    try:
        import requests
    except ImportError:
        print("\n📦 Installing requests...")
        subprocess.run([sys.executable, "-m", "pip", "install", "requests", "--break-system-packages"])

    if mode == "full":
        # Step 1: Fetch boundaries (only need to do this once)
        boundary_dir = "data/boundaries"
        existing = len([f for f in os.listdir(boundary_dir) if f.endswith('.geojson')]) if os.path.exists(boundary_dir) else 0

        if existing < 10:
            run(f"{sys.executable} fetch_boundaries.py", "Fetching district boundaries")
        else:
            print(f"\n✅ {existing} boundary files already exist, skipping download")
            print("   (delete data/boundaries/ to re-fetch)")

        # Step 2: Fetch road events
        run(f"{sys.executable} fetch_road_events.py", "Fetching road events from IDOT ArcGIS")

    elif mode == "refresh":
        # Just refresh road events (for daily cron)
        run(f"{sys.executable} fetch_road_events.py", "Refreshing road events")

    elif mode == "boundaries":
        run(f"{sys.executable} fetch_boundaries.py", "Fetching boundaries only")

    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python setup_pipeline.py [full|refresh|boundaries]")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("✅ Pipeline complete!")
    print("")
    print("Next steps:")
    print("  1. Check data/boundaries/ for .geojson files")
    print("  2. Check data/road/ for district event .json files")
    print("  3. Run the dashboard: streamlit run app.py")
    print("")
    print("For daily refresh, add to cron:")
    print(f"  0 6 * * * cd {os.getcwd()} && {sys.executable} setup_pipeline.py refresh")
    print("=" * 60)


if __name__ == "__main__":
    main()
