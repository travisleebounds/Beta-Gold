import json
from pathlib import Path
import streamlit as st

DATA_PATH = Path("data/av_guidance.json")

@st.cache_data(show_spinner=False)
def load_av_guidance():
    if not DATA_PATH.exists():
        return []
    try:
        return json.loads(DATA_PATH.read_text())
    except Exception:
        return []

def render_av_guidance_section():
    st.markdown("## Executive Orders & Agency Guidance (AV)")

    items = load_av_guidance()
    if not items:
        st.info("No EO / guidance entries loaded yet.")
        return

    states = sorted({x.get("state") for x in items if x.get("state")})
    types = sorted({x.get("type") for x in items if x.get("type")})

    c1, c2 = st.columns(2)
    state = c1.selectbox("State", ["All"] + states, index=0)
    typ = c2.selectbox("Type", ["All"] + types, index=0)

    def ok(x):
        return (state == "All" or x.get("state") == state) and (typ == "All" or x.get("type") == typ)

    filtered = [x for x in items if ok(x)]
    filtered.sort(key=lambda x: x.get("date", ""), reverse=True)

    for x in filtered:
        st.markdown(f"**{x.get('date','')} — {x.get('title','(untitled)')}**")
        if x.get("issuer"):
            st.caption(x["issuer"])
        if x.get("url"):
            st.markdown(x["url"])
        if x.get("summary"):
            st.write(x["summary"])
        if x.get("tags"):
            st.caption("Tags: " + ", ".join(x["tags"]))
        st.divider()
