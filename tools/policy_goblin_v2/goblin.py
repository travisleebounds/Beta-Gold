"""
Policy Goblin v2 — Claude-Powered Policy Advisor
==================================================
The "smart brain" of the IDOT Dashboard.

Uses:
  - ChromaDB (local) for retrieving relevant document context
  - Anthropic Claude API for strategic policy analysis & reasoning
  - Dashboard data context for grounding in real numbers

The Goblin knows Illinois transportation policy inside and out.
"""

import os
import json
import streamlit as st
from datetime import datetime

try:
    import anthropic
except ImportError:
    anthropic = None


# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

CLAUDE_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096

SYSTEM_PROMPT = """You are the IDOT Policy Goblin 🧌 — a senior transportation policy advisor embedded in the Illinois Department of Transportation's internal dashboard.

Your personality:
- Sharp, direct, no-nonsense policy analyst
- Deep expertise in federal transportation funding (IIJA, FHWA formulas, STBG, NHPP, etc.)
- Knows Illinois politics — the General Assembly, congressional delegation, state agencies
- Speaks with authority but flags uncertainty honestly
- Uses occasional goblin humor ("I've been lurking in these policy docs...")
- Gives ACTIONABLE advice, not vague platitudes

Your knowledge base includes:
- Federal highway formula programs and how Illinois's share is calculated
- IIJA (Infrastructure Investment and Jobs Act) provisions
- Illinois congressional delegation and their committee assignments
- State legislative transportation bills
- Autonomous vehicle policy across 50 states
- IDOT construction, closures, and road event data
- Discretionary grant programs (RAISE, INFRA, MEGA, SS4A, etc.)
- FY27 reauthorization scenarios

When answering:
1. Ground your answers in the DASHBOARD DATA and DOCUMENT CONTEXT provided
2. Cite specific numbers, bills, members, or programs when possible
3. If asked about something outside your context, say so — don't hallucinate
4. For policy recommendations, always note political feasibility
5. Format responses for readability — use headers for long answers
6. When relevant, suggest which dashboard view the user should check

You are the goblin that lives in the policy docs. Act like it."""


# ═══════════════════════════════════════════════════════════════
# Policy Goblin Engine
# ═══════════════════════════════════════════════════════════════

def _get_client():
    """Get Anthropic client with API key."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    if anthropic is None:
        return None
    return anthropic.Anthropic(api_key=api_key)


def _get_document_context(query: str, n_results: int = 10) -> str:
    """Pull relevant document chunks from ChromaDB via Document Master."""
    try:
        from tools.document_master.engine import DocumentMaster
        dm = DocumentMaster()
        
        if dm.collection.count() == 0:
            return ""
        
        results = dm.search(query, n_results=n_results)
        if not results:
            return ""
        
        chunks = []
        for r in results:
            chunks.append(f"[{r['source_file']}] {r['text']}")
        
        return "\n\n---\n\n".join(chunks)
    
    except Exception as e:
        return f"(Document search unavailable: {e})"


def _build_messages(user_query: str, chat_history: list,
                     dashboard_context: str = "", doc_context: str = "") -> list:
    """Build the messages array for the Claude API call."""
    
    # Inject context into the first user message
    context_block = ""
    
    if dashboard_context:
        context_block += f"\n\n<dashboard_data>\n{dashboard_context[:6000]}\n</dashboard_data>\n"
    
    if doc_context:
        context_block += f"\n<document_context>\n{doc_context[:4000]}\n</document_context>\n"
    
    messages = []
    
    # Add chat history
    for msg in chat_history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    
    # Add current query with context
    if context_block and len(messages) == 0:
        # First message — include full context
        messages.append({
            "role": "user",
            "content": f"{context_block}\n\nUser question: {user_query}"
        })
    elif context_block and len(messages) <= 2:
        # Early in conversation — include context
        messages.append({
            "role": "user",
            "content": f"{context_block}\n\nUser question: {user_query}"
        })
    else:
        # Later in conversation — just the question (context already established)
        messages.append({
            "role": "user",
            "content": user_query
        })
    
    return messages


def ask_goblin(query: str, chat_history: list = None,
                dashboard_context: str = "") -> str:
    """
    Ask the Policy Goblin a question.
    
    Returns the full response text.
    """
    client = _get_client()
    if client is None:
        return "❌ Policy Goblin needs an Anthropic API key. Set ANTHROPIC_API_KEY in your environment."
    
    chat_history = chat_history or []
    
    # Get relevant document context
    doc_context = _get_document_context(query)
    
    messages = _build_messages(query, chat_history, dashboard_context, doc_context)
    
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        return response.content[0].text
    
    except anthropic.AuthenticationError:
        return "❌ Invalid API key. Check your ANTHROPIC_API_KEY."
    except anthropic.RateLimitError:
        return "⚠️ Rate limited. Try again in a moment."
    except Exception as e:
        return f"❌ Policy Goblin error: {e}"


def ask_goblin_stream(query: str, chat_history: list = None,
                       dashboard_context: str = ""):
    """
    Ask the Policy Goblin with streaming response.
    
    Yields text chunks.
    """
    client = _get_client()
    if client is None:
        yield "❌ Policy Goblin needs an Anthropic API key. Set ANTHROPIC_API_KEY in your environment."
        return
    
    chat_history = chat_history or []
    doc_context = _get_document_context(query)
    messages = _build_messages(query, chat_history, dashboard_context, doc_context)
    
    try:
        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text
    
    except anthropic.AuthenticationError:
        yield "❌ Invalid API key. Check your ANTHROPIC_API_KEY."
    except anthropic.RateLimitError:
        yield "⚠️ Rate limited. Try again in a moment."
    except Exception as e:
        yield f"❌ Policy Goblin error: {e}"


# ═══════════════════════════════════════════════════════════════
# Streamlit UI
# ═══════════════════════════════════════════════════════════════

def render_policy_goblin(dashboard_context: str = ""):
    """Render the full Policy Goblin chat interface."""
    
    st.header("🧌 Policy Goblin — AI Policy Advisor")
    
    # Status indicators
    col1, col2, col3 = st.columns(3)
    
    # Check Anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        col1.success(f"🧠 Claude API: Connected")
    else:
        col1.error("🧠 Claude API: No key found")
        st.warning("Set your API key: `export ANTHROPIC_API_KEY='sk-ant-...'`")
        st.info("Add it to `~/.bashrc` to make it persistent, then restart the terminal.")
        return
    
    # Check Document Master
    try:
        from tools.document_master.engine import DocumentMaster
        dm = DocumentMaster()
        status = dm.status()
        col2.metric("📚 Docs Indexed", status["documents_indexed"])
        col3.metric("🧩 Chunks", status["total_chunks"])
    except Exception:
        col2.caption("📚 No docs indexed")
        col3.caption("🧩 ChromaDB offline")
    
    st.markdown("---")
    
    st.caption(
        "The Policy Goblin uses Claude for strategic analysis and your local document "
        "archive for context. Ask about funding, policy, legislation, districts, or strategy."
    )
    
    # Suggested prompts
    st.markdown("**Quick prompts:**")
    prompt_cols = st.columns(3)
    
    suggested = [
        "How does Illinois's STBG formula share compare to peer states?",
        "What's our best strategy for FY27 reauthorization?",
        "Which districts are most at risk from IIJA expiration?",
        "Summarize the AV policy landscape — what should IL do?",
        "What discretionary grants should we prioritize next cycle?",
        "Compare IL-01 vs IL-12 federal funding per capita",
    ]
    
    for i, prompt in enumerate(suggested):
        col = prompt_cols[i % 3]
        if col.button(prompt[:50] + "..." if len(prompt) > 50 else prompt, 
                      key=f"suggest_{i}", use_container_width=True):
            st.session_state["goblin_input"] = prompt
    
    st.markdown("---")
    
    # Chat history
    if "goblin_history" not in st.session_state:
        st.session_state["goblin_history"] = []
    
    # Display chat history
    for msg in st.session_state["goblin_history"]:
        if msg["role"] == "user":
            st.chat_message("user").write(msg["content"])
        else:
            st.chat_message("assistant", avatar="🧌").write(msg["content"])
    
    # Chat input
    user_input = st.chat_input(
        "Ask the Policy Goblin...",
        key="goblin_chat_input",
    )
    
    # Handle suggested prompt click
    if "goblin_input" in st.session_state and st.session_state["goblin_input"]:
        user_input = st.session_state.pop("goblin_input")
    
    if user_input:
        # Show user message
        st.chat_message("user").write(user_input)
        st.session_state["goblin_history"].append({"role": "user", "content": user_input})
        
        # Stream goblin response
        with st.chat_message("assistant", avatar="🧌"):
            response_placeholder = st.empty()
            full_response = ""
            
            for chunk in ask_goblin_stream(
                user_input,
                chat_history=st.session_state["goblin_history"][:-1],  # Exclude current
                dashboard_context=dashboard_context,
            ):
                full_response += chunk
                response_placeholder.markdown(full_response + "▌")
            
            response_placeholder.markdown(full_response)
        
        st.session_state["goblin_history"].append({"role": "assistant", "content": full_response})
    
    # Sidebar controls
    with st.sidebar:
        st.markdown("### 🧌 Goblin Controls")
        
        if st.button("🗑️ Clear Chat", key="clear_goblin"):
            st.session_state["goblin_history"] = []
            st.rerun()
        
        st.caption(f"Model: {CLAUDE_MODEL}")
        st.caption(f"Messages: {len(st.session_state.get('goblin_history', []))}")
