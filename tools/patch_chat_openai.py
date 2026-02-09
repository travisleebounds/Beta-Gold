import re
from pathlib import Path

p = Path("app.py")
s = p.read_text(encoding="utf-8")

# Remove the existing Anthropic chat block (anchored on your exact comment headers)
chat_block = re.compile(
    r"""
^\s*#\s*Initialize\schat\shistory\s*\n
(?:.*\n)*?
^\s*#\s*Clear\schat\s*button\s*\n
(?:.*\n)*?
^\s*st\.session_state\.chat_messages\s*=\s*\[\]\s*\n
""",
    re.M | re.X
)

s2, n = chat_block.subn("", s, count=1)
if n != 1:
    raise SystemExit("ERROR: Could not find the old chat block. Run: sed -n '280,410p' app.py")

s = s2

# Ensure OpenAI import
if "from openai import OpenAI" not in s:
    lines = s.splitlines(True)
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or lines[i].strip() == ""):
        i += 1
    while i < len(lines) and (lines[i].startswith("import ") or lines[i].startswith("from ")):
        i += 1
    lines.insert(i, "from openai import OpenAI\n")
    s = "".join(lines)

# Inject chatbot helpers once
MARK = "### === OPENAI SIDEBAR CHATBOT ==="
if MARK not in s:
    inject = f"""
{MARK}
import os
import json
from datetime import datetime

def _ai_context_min():
    return json.dumps({{
        "selection": {{
            "congressional_district": st.session_state.get("selected_cd"),
            "il_house_district": st.session_state.get("selected_house"),
            "il_senate_district": st.session_state.get("selected_senate"),
            "active_tab": st.session_state.get("active_tab"),
        }},
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }}, indent=2)

def render_ai_chatbot():
    st.subheader("🤖 IDOT AI Assistant")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        st.error("OPENAI_API_KEY is not set.")
        st.caption("In terminal: export OPENAI_API_KEY='sk-...'; then restart streamlit.")
        return

    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []

    if st.button("🧹 Clear chat", use_container_width=True):
        st.session_state.chat_messages = []
        st.rerun()

    chat_box = st.container(height=420)
    with chat_box:
        for m in st.session_state.chat_messages:
            with st.chat_message(m["role"]):
                st.markdown(m["content"])

    prompt = st.chat_input("Ask about IL transportation…", key="idot_sidebar_chat")
    if not prompt:
        return

    st.session_state.chat_messages.append({{"role": "user", "content": prompt}})
    with chat_box:
        with st.chat_message("user"):
            st.markdown(prompt)

    ctx = _ai_context_min()
    system = (
        "You are the IDOT AI Assistant embedded in a Streamlit dashboard. "
        "Use ONLY the provided JSON context as ground truth. "
        "If the answer is not in context, say so and ask what tab/data to load.\\n\\n"
        f"DASHBOARD_CONTEXT_JSON:\\n{ctx}"
    )

    client = OpenAI(api_key=api_key)
    resp = client.responses.create(
        model="gpt-5",
        input=[
            {{"role": "system", "content": system}},
            *st.session_state.chat_messages[-10:],
        ],
        max_output_tokens=650,
    )

    reply = resp.output_text
    st.session_state.chat_messages.append({{"role": "assistant", "content": reply}})
    with chat_box:
        with st.chat_message("assistant"):
            st.markdown(reply)
### === END OPENAI SIDEBAR CHATBOT ===
"""
    # insert after imports
    lines = s.splitlines(True)
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or lines[i].strip() == ""):
        i += 1
    while i < len(lines) and (lines[i].startswith("import ") or lines[i].startswith("from ")):
        i += 1
    s = "".join(lines[:i]) + inject + "".join(lines[i:])

# Add sidebar call once
CALL = "### === OPENAI SIDEBAR CHATBOT CALL ==="
if CALL not in s:
    call_block = f"""
{CALL}
with st.sidebar:
    render_ai_chatbot()
### === END OPENAI SIDEBAR CHATBOT CALL ===
"""
    m = re.search(r"^st\\.set_page_config\\([^\\n]*\\)\\s*$", s, flags=re.M)
    if m:
        s = s[:m.end()] + call_block + s[m.end():]
    else:
        lines = s.splitlines(True)
        i = 0
        while i < len(lines) and (lines[i].startswith("#") or lines[i].strip() == ""):
            i += 1
        while i < len(lines) and (lines[i].startswith("import ") or lines[i].startswith("from ")):
            i += 1
        s = "".join(lines[:i]) + call_block + "".join(lines[i:])

# Write back
bak = Path(f"app.py.BAK.chat-openai")
Path("app.py").replace(bak)
p.write_text(s, encoding="utf-8")
print("✅ Patched app.py (backup: app.py.BAK.chat-openai).")
