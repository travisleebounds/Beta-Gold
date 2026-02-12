import json
from datetime import datetime

OUT = "data/av_guidance.json"

records = []

def add(**kw):
    records.append(kw)

# =====================
# STATE EXECUTIVE ORDERS
# =====================

add(
    jurisdiction="state",
    state="MA",
    city=None,
    type="executive_order",
    issuer="Office of the Governor of Massachusetts",
    title="Executive Order 572 — Testing of Highly Automated Driving Technologies",
    date="2016-08-10",
    url="https://www.mass.gov/executive-order-572-testing-of-highly-automated-driving-technologies",
    summary="Authorizes and coordinates testing of highly automated vehicles on public ways in Massachusetts.",
    tags=["testing"]
)

add(
    jurisdiction="state",
    state="OH",
    city=None,
    type="executive_order",
    issuer="Office of the Governor of Ohio",
    title="Executive Order 2018-04K — DriveOhio",
    date="2018-05-09",
    url="https://drive.ohio.gov/",
    summary="Establishes DriveOhio to coordinate AV testing, smart mobility corridors, and public-private partnerships.",
    tags=["testing", "smart_mobility"]
)

add(
    jurisdiction="state",
    state="MI",
    city=None,
    type="agency_guidance",
    issuer="Michigan Department of Transportation",
    title="Connected and Automated Vehicle Policy Framework",
    date="2019-01-01",
    url="https://www.michigan.gov/mdot/programs/connected-and-automated-vehicles",
    summary="MDOT guidance and coordination framework for AV testing and deployment.",
    tags=["testing", "coordination"]
)

# =====================
# CITY / METRO PROGRAMS
# =====================

add(
    jurisdiction="city",
    state="AZ",
    city="Phoenix",
    type="pilot_program",
    issuer="City of Phoenix / Waymo",
    title="Autonomous Vehicle Pilot Program",
    date="2017-11-07",
    url="https://www.phoenix.gov/streets/transportation/av",
    summary="City-supported AV pilot and robotaxi testing partnership with Waymo.",
    tags=["robotaxi", "pilot"]
)

add(
    jurisdiction="city",
    state="CA",
    city="San Francisco",
    type="permit_program",
    issuer="San Francisco Municipal Transportation Agency",
    title="Autonomous Vehicle Passenger Service Permitting",
    date="2021-06-01",
    url="https://www.sfmta.com/projects/autonomous-vehicles",
    summary="Local permitting and coordination framework for AV passenger service operations.",
    tags=["robotaxi", "permitting"]
)

add(
    jurisdiction="city",
    state="PA",
    city="Pittsburgh",
    type="pilot_program",
    issuer="City of Pittsburgh",
    title="Autonomous Vehicle Testing Program",
    date="2016-09-01",
    url="https://pittsburghpa.gov/innovation-performance/autonomous-vehicles",
    summary="Early AV testing framework coordinating universities, companies, and public safety.",
    tags=["testing", "research"]
)

# =====================
# WRITE OUTPUT
# =====================

records.sort(key=lambda x: x["date"], reverse=True)

with open(OUT, "w") as f:
    json.dump(records, f, indent=2)

print(f"✅ Wrote {len(records)} AV guidance records to {OUT}")
