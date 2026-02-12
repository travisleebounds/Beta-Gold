from __future__ import annotations
import os, json, hashlib
from typing import Any, Dict, List
from datetime import datetime, timezone

from openai import OpenAI

MODEL_DEFAULT = os.getenv("OPENAI_INGEST_MODEL", "gpt-4o-mini")

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def make_fact_id(metric: str, jurisdiction: str, state: str | None, city: str | None, date: str, value: Any) -> str:
    s = f"{metric}|{jurisdiction}|{state or ''}|{city or ''}|{date[:10]}|{value}"
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

# Strict Structured Outputs JSON Schema (validator-friendly)
FACTS_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "fact_id": {"type": "string"},
                    "metric": {"type": "string"},
                    "value": {"type": ["number", "string", "boolean", "null"]},
                    "unit": {"type": "string"},
                    "date": {"type": "string"},
                    "jurisdiction": {"type": "string", "enum": ["state", "city", "federal", "unknown"]},
                    "state": {"type": ["string", "null"]},
                    "city": {"type": ["string", "null"]},
                    "description": {"type": "string"},
                    "source": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "file": {"type": "string"},
                            "locator": {"type": "string"},
                            "kind": {"type": "string"},
                        },
                        "required": ["file", "locator", "kind"],
                    },
                    "confidence": {"type": "number"},
                    "extracted_at": {"type": "string"},
                },
                "required": [
                    "fact_id",
                    "metric",
                    "value",
                    "unit",
                    "date",
                    "jurisdiction",
                    "state",
                    "city",
                    "description",
                    "source",
                    "confidence",
                    "extracted_at",
                ],
            },
        }
    },
    "required": ["facts"],
}

SYSTEM = """You are a data-ingestion analyst for a transportation intelligence dashboard.
Extract normalized facts from provided text/tables and return JSON matching the schema.

Rules:
- Only extract facts supported by the provided content.
- Prefer explicit dates in content; otherwise use doc_meta.modified_time (YYYY-MM-DD).
- fact_id must be stable: metric|jurisdiction|state|city|date|value (hash ok).
- Use ISO dates (YYYY-MM-DD) when possible.
- Always include provenance (file + locator + kind).
"""

def ai_extract_facts_structured(doc_meta: Dict[str, Any], chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI()
    payload = {"doc_meta": doc_meta, "chunks": chunks}

    resp = client.responses.create(
        model=MODEL_DEFAULT,
        input=[
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Extract normalized facts as JSON."},
                    {"type": "input_text", "text": json.dumps(payload)[:180000]},
                ],
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "extracted_facts",
                "schema": FACTS_JSON_SCHEMA,
            }
        },
        store=False,
        max_output_tokens=1600,
    )

    obj = json.loads(resp.output_text)
    facts = obj.get("facts", [])
    stamp = now_iso()

    # Belt + suspenders: fill extracted_at and fact_id if model misses them
    for f in facts:
        f.setdefault("extracted_at", stamp)
        if not f.get("fact_id"):
            f["fact_id"] = make_fact_id(
                f.get("metric",""),
                f.get("jurisdiction","unknown"),
                f.get("state"),
                f.get("city"),
                f.get("date",""),
                f.get("value"),
            )
    return facts
