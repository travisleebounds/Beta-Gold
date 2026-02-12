import os
import streamlit as st

def _have_openai_key() -> bool:
    # Works for local env var or Streamlit secrets
    if os.getenv("OPENAI_API_KEY"):
        return True
    try:
        return bool(st.secrets.get("OPENAI_API_KEY"))
    except Exception:
        return False

def render_policy_goblin():
    st.header("🧌 Policy Goblin")
    st.caption("Optional AI assistant. Non-blocking. If no key/quota, the dashboard still works.")

    # Feature toggle (default OFF)
    enabled = st.toggle("Enable Policy Goblin (OpenAI)", value=False)
    if not enabled:
        st.info("Policy Goblin is disabled. Toggle it on to chat.")
        return

    if not _have_openai_key():
        st.warning("OPENAI_API_KEY not set. Add it to your shell env or Streamlit secrets to enable AI.")
        st.code("export OPENAI_API_KEY='sk-...'", language="bash")
        return

    # Lazy import so missing package doesn't break the rest of the app
    try:
        from openai import OpenAI
    except Exception as e:
        st.error("OpenAI python package is not installed in this environment.")
        st.code("python3 -m pip install -U openai", language="bash")
        st.text(str(e))
        return

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    if "goblin_msgs" not in st.session_state:
        st.session_state["goblin_msgs"] = [
            {"role": "assistant", "content": "I’m the Policy Goblin. Give me an EO, memo, or question and I’ll summarize it like staff notes."}
        ]

    # Render history
    for m in st.session_state["goblin_msgs"]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    prompt = st.chat_input("Ask about AV policy, EOs, agency guidance, grants, etc…")
    if not prompt:
        return

    st.session_state["goblin_msgs"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call OpenAI Responses API
    client = OpenAI()

    with st.chat_message("assistant"):
        with st.spinner("Goblin thinking…"):
            try:
                resp = client.responses.create(
                    model=model,
                    input=[
                        {
                            "role": "system",
                            "content": (
                                "You are Policy Goblin: a transportation policy staff assistant. "
                                "Be concise, factual, and cite uncertainty. "
                                "When given a document excerpt, produce: (1) 5-bullet summary, "
                                "(2) key dates/deadlines, (3) who is affected, (4) recommended actions."
                            ),
                        },
                        *[
                            {"role": x["role"], "content": x["content"]}
                            for x in st.session_state["goblin_msgs"]
                            if x["role"] in ("user", "assistant")
                        ],
                    ],
                    max_output_tokens=900,
                )
                answer = getattr(resp, "output_text", None) or "(No text returned.)"
            except Exception as e:
                # Quota/billing errors show up here; keep it non-blocking
                answer = f"⚠️ OpenAI request failed: {e}\n\nIf this is `insufficient_quota`, add API billing/credits for the key’s project."
            st.markdown(answer)

    st.session_state["goblin_msgs"].append({"role": "assistant", "content": answer})
