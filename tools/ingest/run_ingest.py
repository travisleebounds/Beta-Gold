from __future__ import annotations
import os, json, shutil, hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from tools.ingest.extractors import extract_docx, extract_xlsx, extract_csv, extract_pdf_text
from tools.ingest.memo_writer import write_updated_memo

# OpenAI is optional; imported lazily in AI path.

INBOX_DEFAULT = Path("ingest_inbox")
ARCHIVE_DEFAULT = Path("ingest_archive")
OUT_FACTS = Path("data/ingest/facts.json")
OUT_INDEX = Path("data/ingest/index.json")
OUT_MEMO = Path("memos/UPDATED_memo.docx")

SUPPORTED = {".docx", ".xlsx", ".csv", ".pdf", ".txt", ".md"}

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def save_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2))

def extract_file(p: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    suffix = p.suffix.lower()
    meta = {
        "file_name": p.name,
        "file_path": str(p),
        "sha256": sha256_file(p),
        "modified_time": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
        "extracted_at": now_iso(),
        "kind": suffix.lstrip("."),
    }

    if suffix == ".docx":
        chunks = extract_docx(p)
    elif suffix == ".xlsx":
        chunks = extract_xlsx(p)
    elif suffix == ".csv":
        chunks = extract_csv(p)
    elif suffix == ".pdf":
        chunks = extract_pdf_text(p)
    elif suffix in (".txt", ".md"):
        txt = p.read_text(errors="ignore")
        chunks = [{"kind":"text","text":txt,"provenance":{"file":p.name,"type":suffix.lstrip("."),"locator":"full_text"}}]
    else:
        chunks = []
    return meta, chunks

def naive_facts(meta: Dict[str, Any], compact: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministic fallback when OpenAI quota/billing is unavailable."""
    date = (meta.get("modified_time") or "")[:10] or "1970-01-01"
    stamp = now_iso()
    facts: List[Dict[str, Any]] = [{
        "fact_id": f"ingested|{meta.get('sha256','')[:16]}",
        "metric": "document_ingested",
        "value": 1,
        "unit": "count",
        "date": date,
        "jurisdiction": "unknown",
        "state": None,
        "city": None,
        "description": f"Ingested {meta.get('file_name','')}",
        "source": {"file": meta.get("file_name",""), "locator": "meta", "kind": meta.get("kind","")},
        "confidence": 0.05,
        "extracted_at": stamp,
    }]

    for c in compact[:3]:
        prov = c.get("provenance", {})
        loc = prov.get("locator", "unknown")
        if c.get("kind") == "table":
            hdr = ", ".join((c.get("table", {}).get("headers") or [])[:10])
            facts.append({
                "fact_id": f"table_headers|{meta.get('sha256','')[:10]}|{loc}",
                "metric": "table_headers",
                "value": hdr[:240],
                "unit": "text",
                "date": date,
                "jurisdiction": "unknown",
                "state": None,
                "city": None,
                "description": "Extracted table headers (no-ai mode).",
                "source": {"file": meta.get("file_name",""), "locator": loc, "kind": "table"},
                "confidence": 0.05,
                "extracted_at": stamp,
            })
        else:
            t = (c.get("text","") or "").strip().replace("\n"," ")
            facts.append({
                "fact_id": f"text_snippet|{meta.get('sha256','')[:10]}|{loc}",
                "metric": "text_snippet",
                "value": t[:240],
                "unit": "text",
                "date": date,
                "jurisdiction": "unknown",
                "state": None,
                "city": None,
                "description": "Extracted text snippet (no-ai mode).",
                "source": {"file": meta.get("file_name",""), "locator": loc, "kind": "text"},
                "confidence": 0.05,
                "extracted_at": stamp,
            })
    return facts

def compact_chunks(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compact = []
    for c in chunks:
        if c.get("kind") == "table":
            tbl = c.get("table", {})
            compact.append({
                "kind": "table",
                "table": {"headers": (tbl.get("headers") or [])[:80], "rows": (tbl.get("rows") or [])[:25]},
                "provenance": c.get("provenance", {}),
            })
        else:
            t = c.get("text", "") or ""
            compact.append({"kind": "text", "text": t[:8000], "provenance": c.get("provenance", {})})
    return compact

def dedup_facts(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for f in facts:
        key = f.get("fact_id") or json.dumps({
            "metric": f.get("metric"),
            "jurisdiction": f.get("jurisdiction"),
            "state": f.get("state"),
            "city": f.get("city"),
            "date": (f.get("date") or "")[:10],
            "value": f.get("value"),
            "unit": f.get("unit"),
        }, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out

def latest_per_bucket(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for f in facts:
        bkey = json.dumps({
            "metric": f.get("metric"),
            "jurisdiction": f.get("jurisdiction"),
            "state": f.get("state"),
            "city": f.get("city"),
        }, sort_keys=True)
        buckets.setdefault(bkey, []).append(f)

    latest = []
    for _, items in buckets.items():
        items_sorted = sorted(items, key=lambda x: ((x.get("date") or "")[:10], x.get("extracted_at") or ""), reverse=True)
        latest.append(items_sorted[0])
    return latest

def main(inbox: Path, no_ai: bool):
    inbox = inbox.resolve()
    archive = ARCHIVE_DEFAULT.resolve()
    inbox.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)

    files = [p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED]
    if not files:
        print(f"⚠️ No supported files found in {inbox}")
        return

    docs_out = []
    facts_all: List[Dict[str, Any]] = []

    for p in sorted(files):
        print(f"==> Extracting: {p.name}")
        meta, chunks = extract_file(p)
        comp = compact_chunks(chunks)

        if no_ai:
            facts = naive_facts(meta, comp)
        else:
            try:
                from tools.ingest.openai_map import ai_extract_facts_structured
                facts = ai_extract_facts_structured(meta, comp)
            except Exception as e:
                # Auto-fallback on quota/ratelimit or schema errors, so the pipeline never blocks demos
                msg = str(e)
                print(f"⚠️ AI ingest failed ({type(e).__name__}): {msg}")
                print("➡️ Falling back to --no-ai mode for this file.")
                facts = naive_facts(meta, comp)

        docs_out.append({"meta": meta, "chunks_used": len(comp)})
        facts_all.extend(facts)

        # archive after processing (even in fallback mode)
        dest = archive / f"{meta['sha256']}_{p.name}"
        shutil.move(str(p), str(dest))

    facts_all = dedup_facts(facts_all)
    facts_latest = latest_per_bucket(facts_all)

    save_json(OUT_FACTS, {"generated_at": now_iso(), "facts_all": facts_all, "facts_latest": facts_latest})
    save_json(OUT_INDEX, {"generated_at": now_iso(), "documents": docs_out})

    write_updated_memo(OUT_MEMO, facts_latest)

    print(f"✅ Wrote {OUT_FACTS}")
    print(f"✅ Wrote {OUT_INDEX}")
    print(f"✅ Wrote {OUT_MEMO}")
    print("✅ Done. (Processed files moved to ingest_archive/)")

if __name__ == "__main__":
    import sys
    args = [a for a in sys.argv[1:] if a]
    no_ai = False
    if "--no-ai" in args:
        no_ai = True
        args = [a for a in args if a != "--no-ai"]
    inbox = Path(args[0]) if args else INBOX_DEFAULT
    main(inbox, no_ai)
