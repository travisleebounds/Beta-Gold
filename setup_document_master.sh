#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# IDOT Dashboard — Document Master + Policy Goblin Setup
# Run this once on your ThinkPad to get everything stood up.
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  IDOT Document Master — Setup Script${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
echo ""

# ─── 1. Install Ollama ─────────────────────────────────────────
echo -e "${YELLOW}[1/5] Checking Ollama...${NC}"
if command -v ollama &> /dev/null; then
    echo -e "  ✅ Ollama already installed: $(ollama --version 2>/dev/null || echo 'unknown')"
else
    echo -e "  📦 Installing Ollama..."
    if pacman -Qi ollama &> /dev/null; then
        echo -e "  ✅ Ollama package found"
    else
        # Try AUR first (yay or paru), fall back to official script
        if command -v yay &> /dev/null; then
            yay -S --noconfirm ollama
        elif command -v paru &> /dev/null; then
            paru -S --noconfirm ollama
        else
            echo -e "  Using official install script..."
            curl -fsSL https://ollama.com/install.sh | sh
        fi
    fi
fi

# ─── 2. Start Ollama service ───────────────────────────────────
echo ""
echo -e "${YELLOW}[2/5] Starting Ollama service...${NC}"
if systemctl is-active --quiet ollama 2>/dev/null; then
    echo -e "  ✅ Ollama service already running"
else
    echo -e "  🔄 Starting ollama..."
    sudo systemctl enable --now ollama 2>/dev/null || ollama serve &
    sleep 3
    echo -e "  ✅ Ollama started"
fi

# ─── 3. Pull the model ─────────────────────────────────────────
echo ""
echo -e "${YELLOW}[3/5] Pulling AI models...${NC}"

# Primary: qwen2.5-coder:7b for document work (fits in 12GB VRAM)
echo -e "  📥 Pulling qwen2.5-coder:7b (document engine)..."
ollama pull qwen2.5-coder:7b

# Secondary: llama3.1:8b as general-purpose fallback
echo -e "  📥 Pulling llama3.1:8b (general assistant)..."
ollama pull llama3.1:8b

echo -e "  ✅ Models ready"

# ─── 4. Install Python dependencies ────────────────────────────
echo ""
echo -e "${YELLOW}[4/5] Installing Python dependencies...${NC}"

pip install --break-system-packages --quiet \
    chromadb \
    ollama \
    anthropic \
    langchain \
    langchain-community \
    langchain-text-splitters \
    sentence-transformers \
    python-docx \
    PyPDF2 \
    openpyxl \
    tiktoken \
    2>/dev/null

echo -e "  ✅ Python packages installed"

# ─── 5. Create data directories ────────────────────────────────
echo ""
echo -e "${YELLOW}[5/5] Setting up data directories...${NC}"

mkdir -p data/vectorstore
mkdir -p data/ingest
mkdir -p ingest_inbox
mkdir -p logs

echo -e "  ✅ Directories created"

# ─── Verify ────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ Setup Complete!${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Ollama:    $(ollama --version 2>/dev/null || echo 'installed')"
echo -e "  Models:    qwen2.5-coder:7b, llama3.1:8b"
echo -e "  ChromaDB:  $(python3 -c 'import chromadb; print(chromadb.__version__)' 2>/dev/null || echo 'installed')"
echo -e "  Anthropic: $(python3 -c 'import anthropic; print(anthropic.__version__)' 2>/dev/null || echo 'installed')"
echo ""
echo -e "  ${YELLOW}Make sure your API key is set:${NC}"
echo -e "  export ANTHROPIC_API_KEY=\"sk-ant-...\""
echo ""
echo -e "  ${YELLOW}To test Ollama:${NC}"
echo -e "  ollama run qwen2.5-coder:7b \"Hello, are you working?\""
echo ""
echo -e "  ${YELLOW}To start the dashboard:${NC}"
echo -e "  streamlit run app.py"
echo ""
