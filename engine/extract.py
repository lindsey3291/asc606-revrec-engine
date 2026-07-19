"""Contract extraction — the single entry point for turning an uploaded PDF
into the internal contract dict the rest of the app depends on.

Two input formats are supported and auto-detected; BOTH produce the exact
same internal schema (contract_id, customer, start_date, end_date,
total_price, deliverables[...], optional modification), so nothing
downstream — journal entries, deferred revenue, SAP export, forecast —
needs to know where a contract came from:

  * STRUCTURED  — the legacy label:value order form (Contract ID: / Type: /
    Delivery: ...). Parsed deterministically by engine.pdf_contract. Kept for
    full backward compatibility with earlier test PDFs.

  * PROSE       — a realistic order form / SOW / MSA excerpt written in plain
    business English. Extraction here does real work: it reads the prose and
    reasons — grounded in the ASC 606 reference guide's three-criteria test —
    about what is being sold, whether each obligation transfers at a point in
    time or continuously, the dates, price, and customer. Where a standalone
    selling price is not stated for a bundled deal, it does NOT invent a
    number; it flags the obligation for review. Prose extraction uses the
    Claude API (reasoning, not keyword matching); without a configured key it
    returns a clear message rather than a bad guess.
"""
from __future__ import annotations

import json
import os
import re

from pypdf import PdfReader

from engine.core import ContractValidationError
from engine.pdf_contract import parse_contract_pdf
from engine.rag import get_doc

MODEL = "claude-opus-4-8"


def _pdf_text(fileobj) -> str:
    try:
        fileobj.seek(0)
    except Exception:
        pass
    reader = PdfReader(fileobj)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def is_structured(text: str) -> bool:
    """The legacy structured order form uses explicit field labels. Detect it
    by the label pattern so those PDFs keep routing to the deterministic
    parser exactly as before."""
    t = re.sub(r"\s+", " ", text)
    has_labels = bool(re.search(r"Contract ID:", t, re.I)) and (
        bool(re.search(r"Delivery:\s*(one_time|over_time)", t, re.I))
        or bool(re.search(r"Standalone Price:", t, re.I)))
    return has_labels


def extract_contract(fileobj, filename: str = "") -> dict:
    """Route an uploaded PDF to the right extractor. Returns the internal
    contract dict. Raises ContractValidationError with a clear message on
    failure (including 'prose extraction needs an API key')."""
    text = _pdf_text(fileobj)
    if is_structured(text):
        try:
            fileobj.seek(0)
        except Exception:
            pass
        return parse_contract_pdf(fileobj)     # legacy path, unchanged
    return _extract_prose(text)


# ---------------------------------------------------------------------------
# Prose extraction (AI, RAG-grounded)
# ---------------------------------------------------------------------------

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "customer": {"type": "string"},
        "start_date": {"type": "string", "description": "YYYY-MM-DD"},
        "end_date": {"type": "string", "description": "YYYY-MM-DD"},
        "total_price": {"type": "number"},
        "deliverables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                    "delivery_type": {"type": "string", "enum": ["one_time", "over_time", "unknown"]},
                    "reasoning": {"type": "string",
                                  "description": "Which of the three over-time criteria was applied, or why point-in-time."},
                    "standalone_price_estimate": {"type": ["number", "null"]},
                    "review": {
                        "type": ["object", "null"],
                        "properties": {
                            "kind": {"type": "string",
                                     "enum": ["unpriced_bundle", "variable_consideration", "ambiguous_timing"]},
                            "reason": {"type": "string"},
                            "excluded_amount": {"type": ["number", "null"]},
                        },
                        "required": ["kind", "reason", "excluded_amount"],
                        "additionalProperties": False,
                    },
                },
                "required": ["type", "description", "delivery_type", "reasoning",
                             "standalone_price_estimate", "review"],
                "additionalProperties": False,
            },
        },
        "modification": {
            "type": ["object", "null"],
            "properties": {
                "date": {"type": "string"},
                "description": {"type": "string"},
                "added_price": {"type": "number"},
                "added_deliverable": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "description": {"type": "string"},
                        "standalone_price_estimate": {"type": "number"},
                        "delivery_type": {"type": "string", "enum": ["over_time"]},
                    },
                    "required": ["type", "description", "standalone_price_estimate", "delivery_type"],
                    "additionalProperties": False,
                },
            },
            "required": ["date", "description", "added_price", "added_deliverable"],
            "additionalProperties": False,
        },
    },
    "required": ["customer", "start_date", "end_date", "total_price", "deliverables", "modification"],
    "additionalProperties": False,
}

_EXTRACT_SYSTEM = """You extract structured performance-obligation data from a contract written in plain business prose, for a deterministic ASC 606 revenue recognition engine that runs downstream. You do the READING and REASONING; the engine does the math.

Ground every judgment in the reference guide excerpts provided — especially Step 2 (distinct obligations), Step 4 (allocation by relative standalone selling price), and Step 5 (the three over-time criteria and the control-transfer test). Do NOT keyword-match (e.g. do not assume the word "months" means over_time); reason about whether control transfers continuously or at a point in time.

Rules:
- delivery_type = "over_time" ONLY if one of the three Step-5 criteria is met (typically criterion 1: the customer simultaneously receives and consumes the benefit as the company performs — subscriptions, hosting, support). "one_time" (point in time) when the company's obligation is satisfied at a single moment (delivered goods, a perpetual license, a one-off report), even if the customer's own use is gradual. Use "unknown" only if the text genuinely doesn't let you decide, and then attach a review with kind "ambiguous_timing".
- standalone_price_estimate: use an explicitly stated per-item price when given. If one component of a bundle is priced and the rest is the remainder of a stated total, you MAY infer the remainder. If a bundle gives only ONE aggregate price for multiple distinct components with no basis to split it, set standalone_price_estimate to null for those components and attach a review with kind "unpriced_bundle" — do NOT invent an allocation.
- Variable / usage-based consideration: set standalone_price_estimate to null and attach a review with kind "variable_consideration" and excluded_amount set to any stated estimate (else null). Do not confidently classify it.
- total_price is the fixed consideration stated in the contract (exclude purely variable/usage estimates from it).
- For a single point-in-time deliverable, set start_date and end_date to the delivery/execution date.
- modification: if the text describes a mid-term change in scope/price approved by both parties, populate the modification object (added_deliverable must be over_time in this engine). If you detect modification language but cannot fully structure it, still return the contract and attach a review of kind "ambiguous_timing" on the affected obligation noting it — never silently drop it.
- reasoning: one short phrase naming the specific criterion applied.

Return ONLY the JSON matching the provided schema."""


def _extract_prose(text: str) -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ContractValidationError(
            "This looks like a prose contract (not the structured order form). "
            "Reading prose contracts requires the Claude API — set ANTHROPIC_API_KEY on "
            "the server to enable it. You can still upload a structured-format PDF "
            "(the sample order form) or a JSON contract without a key.")
    try:
        import anthropic
    except ImportError:
        raise ContractValidationError("The anthropic package is not installed on the server.")

    doc = get_doc()
    sections = []
    seen = set()
    for q in ["identify performance obligations distinct",
              "recognize revenue over time point in time three criteria control",
              "allocate transaction price standalone selling price",
              "contract modifications", "variable consideration constraint"]:
        for s in doc.retrieve(q, k=2):
            if s["title"] not in seen:
                seen.add(s["title"])
                sections.append(s)
    grounding = "\n\n".join(f"### {s['title']}\n{s['text']}" for s in sections)

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=3000,
            system=_EXTRACT_SYSTEM + "\n\n# Reference guide excerpts\n\n" + grounding,
            messages=[{"role": "user", "content": "Extract this contract:\n\n" + text[:12000]}],
            output_config={"format": {"type": "json_schema", "schema": _EXTRACT_SCHEMA}},
        )
        if response.stop_reason == "refusal":
            raise ContractValidationError("The extractor declined to process this document.")
        data = json.loads(next(b.text for b in response.content if b.type == "text"))
    except ContractValidationError:
        raise
    except Exception as e:
        raise ContractValidationError(f"Prose extraction failed: {e}")

    return _to_internal(data)


def _to_internal(data: dict) -> dict:
    """Translate the extractor's output into the exact internal contract dict
    (identical shape to a structured/JSON contract). This is a pure
    translation step — no parallel data model."""
    # If the file isn't actually a contract, the reader comes back with empty
    # core fields. Surface one clear message instead of a raw date-parse error
    # ("Invalid date '' — expected YYYY-MM-DD") from validation downstream.
    try:
        price = float(data.get("total_price") or 0)
    except (TypeError, ValueError):
        price = 0
    if (not (data.get("customer") or "").strip()
            or not (data.get("start_date") or "").strip()
            or not (data.get("end_date") or "").strip()
            or price <= 0
            or not data.get("deliverables")):
        raise ContractValidationError(
            "This file doesn't look like a contract — the reader couldn't find a customer, "
            "contract dates, and a positive total price. Please upload a contract PDF "
            "(order form, SOW, or MSA excerpt) or a JSON contract.")
    delivs = []
    for d in data["deliverables"]:
        item = {
            "type": (d.get("type") or "item").strip().lower().replace(" ", "_"),
            "description": d["description"].strip(),
            "standalone_price_estimate": d.get("standalone_price_estimate"),
            "delivery_type": d.get("delivery_type", "unknown"),
        }
        if d.get("review"):
            item["review"] = {
                "kind": d["review"]["kind"],
                "reason": d["review"]["reason"],
                "excluded_amount": d["review"].get("excluded_amount"),
            }
        delivs.append(item)

    contract = {
        "contract_id": _gen_id(data["customer"]),
        "customer": data["customer"].strip(),
        "start_date": data["start_date"].strip(),
        "end_date": data["end_date"].strip(),
        "total_price": float(data["total_price"]),
        "deliverables": delivs,
    }
    if data.get("modification"):
        contract["modification"] = data["modification"]
    return contract


def _gen_id(customer: str) -> str:
    import hashlib
    import time
    slug = re.sub(r"[^A-Za-z]", "", customer)[:4].upper() or "CTRT"
    tail = hashlib.sha1(f"{customer}{time.time()}".encode()).hexdigest()[:4].upper()
    return f"{slug}-{tail}"
