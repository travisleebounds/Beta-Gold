"""
Document Master Engine — IDOT Dashboard
========================================
Local AI document engine powered by Ollama + ChromaDB.

Handles:
  - Document ingestion (PDF, DOCX, XLSX, CSV, TXT, MD)
  - Vector embedding & storage in ChromaDB
  - Semantic search over all ingested documents
  - Report generation (Policy Brief / Data Nuke) via Ollama streaming

Usage:
  from tools.document_master.engine import DocumentMaster
  dm = DocumentMaster()
  dm.ingest_file("path/to/doc.pdf")
  results = dm.search("federal funding allocations")
  report = dm.generate_report(member_id="IL-01", report_type="brief")
"""

import os
import json
import hashlib
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Generator

# Document parsing
try:
    from PyPDF2 import PdfReader
except ImportError:
    PdfReader = None

try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None

try:
    import openpyxl
except ImportError:
    openpyxl = None

import csv

# Vector store
import chromadb
from chromadb.config import Settings as ChromaSettings

# Text splitting
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Ollama client
import ollama as ollama_client

logger = logging.getLogger("document_master")
logging.basicConfig(level=logging.INFO)

# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

DEFAULT_MODEL = os.environ.get("DOCMASTER_MODEL", "qwen2.5-coder:7b")
FALLBACK_MODEL = "llama3.1:8b"
CHROMA_DIR = os.environ.get("DOCMASTER_CHROMA_DIR", "data/vectorstore")
COLLECTION_NAME = "idot_documents"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
TOP_K_RESULTS = 15  # How many chunks to retrieve for context


# ═══════════════════════════════════════════════════════════════
# Document Parsers
# ═══════════════════════════════════════════════════════════════

def parse_pdf(filepath: str) -> str:
    """Extract text from a PDF file."""
    if PdfReader is None:
        raise ImportError("PyPDF2 not installed. Run: pip install PyPDF2")
    reader = PdfReader(filepath)
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text.strip())
    return "\n\n".join(pages)


def parse_docx(filepath: str) -> str:
    """Extract text from a DOCX file."""
    if DocxDocument is None:
        raise ImportError("python-docx not installed. Run: pip install python-docx")
    doc = DocxDocument(filepath)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    
    # Also extract tables
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                paragraphs.append(" | ".join(cells))
    
    return "\n\n".join(paragraphs)


def parse_xlsx(filepath: str) -> str:
    """Extract text from an Excel file."""
    if openpyxl is None:
        raise ImportError("openpyxl not installed. Run: pip install openpyxl")
    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    content = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        content.append(f"=== Sheet: {sheet_name} ===")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            if any(c.strip() for c in cells):
                content.append(" | ".join(cells))
    wb.close()
    return "\n".join(content)


def parse_csv_file(filepath: str) -> str:
    """Extract text from a CSV file."""
    content = []
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            if any(cell.strip() for cell in row):
                content.append(" | ".join(row))
    return "\n".join(content)


def parse_text(filepath: str) -> str:
    """Read a plain text or markdown file."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".xlsx": parse_xlsx,
    ".xls": parse_xlsx,
    ".csv": parse_csv_file,
    ".tsv": parse_csv_file,
    ".txt": parse_text,
    ".md": parse_text,
    ".json": parse_text,
}


def parse_file(filepath: str) -> str:
    """Parse any supported file type and return its text content."""
    ext = Path(filepath).suffix.lower()
    parser = PARSERS.get(ext)
    if parser is None:
        raise ValueError(f"Unsupported file type: {ext}. Supported: {list(PARSERS.keys())}")
    return parser(filepath)


def file_hash(filepath: str) -> str:
    """Compute SHA-256 hash of a file for dedup."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ═══════════════════════════════════════════════════════════════
# Document Master Engine
# ═══════════════════════════════════════════════════════════════

class DocumentMaster:
    """
    Local AI document engine for the IDOT Dashboard.
    
    Uses ChromaDB for vector storage and Ollama for inference.
    """

    def __init__(self, chroma_dir: str = CHROMA_DIR, model: str = DEFAULT_MODEL):
        self.model = model
        self.chroma_dir = chroma_dir
        
        # Initialize ChromaDB
        os.makedirs(chroma_dir, exist_ok=True)
        self.chroma_client = chromadb.PersistentClient(path=chroma_dir)
        self.collection = self.chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )
        
        # Text splitter for chunking
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        
        # Track ingested files
        self.index_path = Path("data/ingest/docmaster_index.json")
        self.index = self._load_index()
        
        logger.info(f"DocumentMaster initialized: model={model}, docs={self.collection.count()}")

    def _load_index(self) -> dict:
        """Load the document index tracking what's been ingested."""
        if self.index_path.exists():
            with open(self.index_path) as f:
                return json.load(f)
        return {"documents": {}, "last_updated": None}

    def _save_index(self):
        """Persist the document index."""
        self.index["last_updated"] = datetime.now().isoformat()
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.index_path, "w") as f:
            json.dump(self.index, f, indent=2)

    def _check_ollama(self) -> bool:
        """Verify Ollama is running and model is available."""
        try:
            models = ollama_client.list()
            available = [m.get("name", m.get("model", "")) for m in models.get("models", [])]
            # Check for model (handle tag variations)
            for m in available:
                if self.model.split(":")[0] in m:
                    return True
            logger.warning(f"Model {self.model} not found. Available: {available}")
            return False
        except Exception as e:
            logger.error(f"Ollama not reachable: {e}")
            return False

    # ─── Ingestion ──────────────────────────────────────────────

    def ingest_file(self, filepath: str, force: bool = False) -> dict:
        """
        Ingest a single file into the vector store.
        
        Returns dict with status info.
        """
        filepath = str(Path(filepath).resolve())
        fname = Path(filepath).name
        fhash = file_hash(filepath)
        
        # Skip if already ingested (same hash)
        if not force and fname in self.index["documents"]:
            if self.index["documents"][fname].get("sha256") == fhash:
                logger.info(f"Skipping {fname} (already ingested, same hash)")
                return {"file": fname, "status": "skipped", "reason": "already ingested"}
        
        # Parse the file
        logger.info(f"Parsing {fname}...")
        try:
            text = parse_file(filepath)
        except Exception as e:
            logger.error(f"Failed to parse {fname}: {e}")
            return {"file": fname, "status": "error", "reason": str(e)}
        
        if not text.strip():
            return {"file": fname, "status": "error", "reason": "empty content"}
        
        # Chunk the text
        chunks = self.splitter.split_text(text)
        logger.info(f"  {fname}: {len(text)} chars → {len(chunks)} chunks")
        
        # Remove old entries for this file if re-ingesting
        try:
            existing = self.collection.get(where={"source_file": fname})
            if existing and existing["ids"]:
                self.collection.delete(ids=existing["ids"])
                logger.info(f"  Removed {len(existing['ids'])} old chunks for {fname}")
        except Exception:
            pass  # Collection might not support this query yet
        
        # Add chunks to ChromaDB
        ids = [f"{fname}__chunk_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "source_file": fname,
                "source_path": filepath,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "sha256": fhash,
                "ingested_at": datetime.now().isoformat(),
            }
            for i in range(len(chunks))
        ]
        
        # Batch add (ChromaDB handles embedding internally)
        batch_size = 100
        for start in range(0, len(chunks), batch_size):
            end = min(start + batch_size, len(chunks))
            self.collection.add(
                ids=ids[start:end],
                documents=chunks[start:end],
                metadatas=metadatas[start:end],
            )
        
        # Update index
        self.index["documents"][fname] = {
            "sha256": fhash,
            "path": filepath,
            "chunks": len(chunks),
            "chars": len(text),
            "ingested_at": datetime.now().isoformat(),
        }
        self._save_index()
        
        logger.info(f"  ✅ {fname}: {len(chunks)} chunks indexed")
        return {
            "file": fname,
            "status": "ingested",
            "chunks": len(chunks),
            "chars": len(text),
        }

    def ingest_directory(self, directory: str, force: bool = False) -> list:
        """Ingest all supported files from a directory."""
        results = []
        directory = Path(directory)
        
        if not directory.is_dir():
            return [{"error": f"{directory} is not a directory"}]
        
        for filepath in sorted(directory.iterdir()):
            if filepath.is_file() and filepath.suffix.lower() in PARSERS:
                result = self.ingest_file(str(filepath), force=force)
                results.append(result)
        
        return results

    # ─── Search ─────────────────────────────────────────────────

    def search(self, query: str, n_results: int = TOP_K_RESULTS, 
               filter_file: str = None) -> list:
        """
        Semantic search over all ingested documents.
        
        Returns list of {text, source_file, score, chunk_index}.
        """
        if self.collection.count() == 0:
            return []
        
        where = {"source_file": filter_file} if filter_file else None
        
        results = self.collection.query(
            query_texts=[query],
            n_results=min(n_results, self.collection.count()),
            where=where,
        )
        
        hits = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                distance = results["distances"][0][i] if results["distances"] else 0
                hits.append({
                    "text": doc,
                    "source_file": meta.get("source_file", "unknown"),
                    "chunk_index": meta.get("chunk_index", 0),
                    "score": 1 - distance,  # Convert distance to similarity
                })
        
        return hits

    # ─── Report Generation ──────────────────────────────────────

    def _build_report_prompt(self, member_data: dict, report_type: str,
                              context_chunks: list, dashboard_context: str = "") -> str:
        """Build the prompt for Ollama report generation."""
        
        member_id = member_data.get("id", "Unknown")
        member_name = member_data.get("name", "Unknown Member")
        party = member_data.get("party", "?")
        area = member_data.get("area", "Illinois")
        
        # Assemble retrieved context
        doc_context = "\n\n---\n\n".join(
            f"[Source: {c['source_file']}]\n{c['text']}" 
            for c in context_chunks
        )
        
        if report_type == "brief":
            return f"""You are Document Master, an AI report generator for the Illinois Department of Transportation Dashboard.

Generate a POLICY BRIEF (1-2 pages) for the following member.

MEMBER INFORMATION:
- ID: {member_id}
- Name: {member_name}
- Party: {party}
- Area: {area}

DASHBOARD DATA:
{dashboard_context[:3000]}

RELEVANT DOCUMENTS:
{doc_context[:4000]}

INSTRUCTIONS:
Generate a concise policy brief with these sections:
1. EXECUTIVE SUMMARY — 2-3 sentence overview
2. KEY FINDINGS — bullet points of most important facts
3. POLICY REFERENCES — relevant policies, bills, and compliance status
4. RECOMMENDATION — 1-2 sentence action item

Format the output as a clean text report with clear section headers.
Use ═ and ─ characters for borders. Include the member name and date at the top.
Be specific — cite actual data from the context provided.
If data is missing, note "Data pending" rather than making things up.
"""
        
        else:  # "nuke"
            return f"""You are Document Master, an AI report generator for the Illinois Department of Transportation Dashboard.

Generate a COMPREHENSIVE DATA NUKE REPORT (10+ pages) for the following member.

MEMBER INFORMATION:
- ID: {member_id}
- Name: {member_name}
- Party: {party}
- Area: {area}

DASHBOARD DATA:
{dashboard_context[:5000]}

RELEVANT DOCUMENTS:
{doc_context[:8000]}

INSTRUCTIONS:
Generate an exhaustive comprehensive report with ALL of these sections:

1. EXECUTIVE SUMMARY — Full overview with confidence scores
2. MEMBER PROFILE & HISTORY — Complete background
3. POLICY COMPLIANCE AUDIT — Every relevant policy checked
4. FEDERAL FUNDING ANALYSIS — Formula allocations, grants, per-capita comparisons
5. TRANSPORTATION INFRASTRUCTURE — Road events, construction, closures in district
6. LEGISLATIVE ACTIVITY — Bills sponsored, committee work, voting record
7. RISK ASSESSMENT MATRIX — Rate each area: LOW / MEDIUM / HIGH
8. COMPARATIVE ANALYSIS — How this district/member compares to peers
9. HISTORICAL TIMELINE — Key events chronologically
10. DOCUMENT CROSS-REFERENCE — Which source docs informed each section
11. RECOMMENDATIONS & ACTION ITEMS — Specific next steps with priorities
12. APPENDIX — Source document list with dates

Format as a professional report with:
- ══ double borders for major sections
- ── single borders for subsections
- [■] for completed/verified items, [□] for pending items
- Include a TABLE OF CONTENTS at the top
- Include page estimates for each section
- Be exhaustive. Use ALL available data. This is the "nuke everything" option.
- If data is missing for a section, note "DATA PENDING — requires [specific source]"
"""

    def generate_report_stream(self, member_data: dict, report_type: str = "brief",
                                dashboard_context: str = "") -> Generator[dict, None, None]:
        """
        Generate a report with streaming output.
        
        Yields dicts: {"stage": str, "progress": float} for stages,
                      {"token": str} for streamed text,
                      {"done": True, "full_text": str} at completion.
        """
        stages = [
            "Connecting to Document Master",
            "Loading member profile data",
            "Querying policy database",
            "Searching document archive",
        ]
        
        if report_type == "nuke":
            stages.extend([
                "Cross-referencing compliance records",
                "Analyzing historical data",
                "Processing all related documents",
                "Building risk assessment matrix",
            ])
        
        stages.append("Generating report")
        
        # Stage 1-N: Search and prepare
        member_id = member_data.get("id", "")
        member_name = member_data.get("name", "Unknown")
        
        for i, stage in enumerate(stages[:-1]):
            yield {"stage": stage, "progress": (i / len(stages)) * 100}
            time.sleep(0.3)  # Brief pause for UX
        
        # Search for relevant context
        search_queries = [
            f"{member_name} {member_data.get('area', '')}",
            f"{member_id} transportation funding",
            f"{member_id} policy compliance",
            "Illinois transportation federal funding",
        ]
        
        if report_type == "nuke":
            search_queries.extend([
                f"{member_id} grants discretionary",
                f"{member_id} road construction closures",
                f"{member_data.get('area', '')} infrastructure",
                "IIJA formula allocations Illinois",
            ])
        
        all_chunks = []
        seen_texts = set()
        for query in search_queries:
            results = self.search(query, n_results=5)
            for r in results:
                if r["text"] not in seen_texts:
                    all_chunks.append(r)
                    seen_texts.add(r["text"])
        
        # Build prompt
        prompt = self._build_report_prompt(
            member_data, report_type, all_chunks, dashboard_context
        )
        
        # Final stage: generate
        yield {"stage": "Generating report", "progress": 90}
        
        # Check Ollama availability
        if not self._check_ollama():
            yield {"error": f"Ollama not available or model {self.model} not pulled. Run: ollama pull {self.model}"}
            return
        
        # Stream from Ollama
        full_text = ""
        try:
            stream = ollama_client.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
                options={
                    "temperature": 0.3,
                    "num_predict": 4096 if report_type == "brief" else 8192,
                    "top_p": 0.9,
                },
            )
            
            for chunk in stream:
                token = chunk.get("message", {}).get("content", "")
                if token:
                    full_text += token
                    yield {"token": token}
            
            yield {"done": True, "full_text": full_text, "sources": len(all_chunks)}
        
        except Exception as e:
            logger.error(f"Ollama generation failed: {e}")
            yield {"error": f"Generation failed: {e}"}

    def generate_report(self, member_data: dict, report_type: str = "brief",
                         dashboard_context: str = "") -> str:
        """Generate a report (non-streaming). Returns full text."""
        full_text = ""
        for event in self.generate_report_stream(member_data, report_type, dashboard_context):
            if "token" in event:
                full_text += event["token"]
            elif "error" in event:
                raise RuntimeError(event["error"])
        return full_text

    # ─── Status ─────────────────────────────────────────────────

    def status(self) -> dict:
        """Get Document Master status."""
        ollama_ok = self._check_ollama()
        return {
            "ollama_running": ollama_ok,
            "model": self.model,
            "documents_indexed": len(self.index.get("documents", {})),
            "total_chunks": self.collection.count(),
            "chroma_dir": self.chroma_dir,
            "last_updated": self.index.get("last_updated"),
        }


# ═══════════════════════════════════════════════════════════════
# CLI for testing
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    dm = DocumentMaster()
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m tools.document_master.engine status")
        print("  python -m tools.document_master.engine ingest <path>")
        print("  python -m tools.document_master.engine search <query>")
        print("  python -m tools.document_master.engine report <member_id> [brief|nuke]")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "status":
        status = dm.status()
        print(json.dumps(status, indent=2))
    
    elif cmd == "ingest":
        path = sys.argv[2] if len(sys.argv) > 2 else "ingest_inbox"
        p = Path(path)
        if p.is_dir():
            results = dm.ingest_directory(str(p))
        else:
            results = [dm.ingest_file(str(p))]
        for r in results:
            print(f"  {r['file']}: {r['status']} ({r.get('chunks', 0)} chunks)")
    
    elif cmd == "search":
        query = " ".join(sys.argv[2:])
        results = dm.search(query)
        for r in results:
            print(f"  [{r['source_file']}] (score: {r['score']:.3f})")
            print(f"    {r['text'][:120]}...")
            print()
    
    elif cmd == "report":
        member_id = sys.argv[2] if len(sys.argv) > 2 else "IL-01"
        report_type = sys.argv[3] if len(sys.argv) > 3 else "brief"
        
        member_data = {"id": member_id, "name": "Test Member", "party": "D", "area": "Illinois"}
        
        print(f"Generating {report_type} for {member_id}...")
        for event in dm.generate_report_stream(member_data, report_type):
            if "stage" in event:
                print(f"  [{event['progress']:.0f}%] {event['stage']}...")
            elif "token" in event:
                print(event["token"], end="", flush=True)
            elif "done" in event:
                print(f"\n\n--- Report complete ({event['sources']} sources) ---")
            elif "error" in event:
                print(f"\n  ❌ {event['error']}")
