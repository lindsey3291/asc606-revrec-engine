"""AI explanation layer.

Generates a 1-2 sentence plain-English rationale for each performance
obligation: why it was classified point-in-time vs over-time and why the
price allocation came out the way it did.

IMPORTANT SEPARATION OF CONCERNS: this module runs strictly AFTER
engine/core.py has produced the numbers. The AI explains the output — it
never makes or influences a classification or allocation decision. If the
Claude API is unavailable (no ANTHROPIC_API_KEY, no network), a
deterministic template rationale is used instead so the tool still runs
end-to-end; each rationale is tagged with its source.
"""
from __future__ import annotations

import json
import os

MODEL = "claude-opus-4-8"


def _template_rationale(ob: dict, processed: dict) -> str:
    """Deterministic fallback rationale built from the classification facts."""
    total = processed["total_price"]
    ssp_sum = sum(o["standalone_price_estimate"] for o in processed["obligations"])
    pct = ob["standalone_price_estimate"] / ssp_sum * 100 if ssp_sum else 0
    if ob["method"] == "point_in_time":
        method_part = (
            "Classified point-in-time because the customer obtains control of this "
            "deliverable at a single moment (delivery/acceptance), so revenue is "
            "recognized in full on that date."
        )
    else:
        method_part = (
            "Classified over-time because the customer receives and consumes the "
            "benefit continuously across the contract term, so revenue is spread "
            "evenly by month."
        )
    if len(processed["obligations"]) > 1 and not ob.get("added_by_modification"):
        alloc_part = (
            f" It represents {pct:.0f}% of the bundle's standalone selling prices, so it "
            f"was allocated ${ob['allocated_price']:,.2f} of the ${total:,.2f} transaction price."
        )
    elif ob.get("added_by_modification"):
        alloc_part = (
            " It was added by a mid-term modification, so its price comes from the "
            "blended reallocation of remaining deferred revenue plus the added fee."
        )
    else:
        alloc_part = ""
    return method_part + alloc_part


def _claude_rationales(processed: dict) -> dict[str, str] | None:
    """One API call per contract; returns {obligation_id: rationale} or None on failure."""
    try:
        import anthropic
    except ImportError:
        return None

    facts = {
        "contract_id": processed["contract_id"],
        "customer": processed["customer"],
        "term": f"{processed['start_date']} to {processed['end_date']}",
        "total_price": processed["total_price"],
        "obligations": processed["obligations"],
        "modification_note": processed.get("modification_note"),
        "variable_note": processed.get("variable_note"),
    }
    schema = {
        "type": "object",
        "properties": {
            "rationales": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "obligation_id": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["obligation_id", "rationale"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["rationales"],
        "additionalProperties": False,
    }
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=(
                "You are explaining the output of a deterministic ASC 606 revenue "
                "recognition engine to a finance reviewer. For each performance "
                "obligation, write a 1-2 sentence plain-English rationale covering "
                "(a) why its recognition method (point_in_time vs over_time) fits the "
                "nature of the deliverable and (b) why the allocated price is what it "
                "is (proportional to standalone selling prices, or resulting from a "
                "modification reallocation). You are explaining decisions already "
                "made by rules — do not second-guess or change them."
            ),
            messages=[{"role": "user", "content": json.dumps(facts, indent=2)}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if response.stop_reason == "refusal":
            return None
        text = next(b.text for b in response.content if b.type == "text")
        items = json.loads(text)["rationales"]
        return {it["obligation_id"]: it["rationale"] for it in items}
    except Exception:
        return None


def add_rationales(processed: dict) -> dict:
    """Attach a rationale (AI or template) to every obligation. Mutates + returns."""
    ai = _claude_rationales(processed) if os.environ.get("ANTHROPIC_API_KEY") else None
    rationales = {}
    for ob in processed["obligations"]:
        ob_id = ob["obligation_id"]
        if ai and ob_id in ai:
            rationales[ob_id] = {"text": ai[ob_id], "source": "claude"}
        else:
            rationales[ob_id] = {
                "text": _template_rationale(ob, processed),
                "source": "template",
            }
    processed["rationales"] = rationales
    return processed
