"""AI explanation layer — RAG-grounded against the ASC 606 reference doc.

Three responsibilities, all strictly AFTER engine/core.py has produced the
numbers (the AI explains output; it never makes classification decisions):

1. RATIONALES — a 1-2 sentence explanation per obligation, grounded in the
   most relevant section of data/asc606_reference_doc.md and citing the
   specific rule applied (e.g. "over time under criterion 1"). When the
   Claude API is available the model writes the prose from retrieved
   sections; otherwise a deterministic template — which cites the same
   sections — is used. Every rationale is tagged with its source.

2. CONFIDENCE (high/medium/low) — how cleanly each deliverable's description
   maps to the reference doc's distinctness (Step 2) and over-time (Step 5)
   criteria. A deterministic keyword heuristic always runs (so behavior is
   identical with or without an API key); low-confidence items surface in
   the dashboard's "Needs Review" section instead of being silently
   auto-processed.

3. SCOPE FLAGS — if a deliverable touches an area the reference doc lists
   as explicitly out of scope (financing components, principal vs. agent,
   multi-currency, leases, ...), it is flagged for human review rather than
   confidently classified.
"""
from __future__ import annotations

import json
import os
import re

from engine.rag import get_doc

MODEL = "claude-opus-4-8"

# ---------------------------------------------------------------------------
# Deterministic confidence heuristic + out-of-scope detection
# ---------------------------------------------------------------------------

# Vocabulary that cleanly matches the reference doc's Step 5 discussion.
_OVER_TIME_WORDS = {"subscription", "support", "maintenance", "hosting", "access",
                    "saas", "platform", "monthly", "service", "sla", "module", "addon"}
_POINT_IN_TIME_WORDS = {"hardware", "terminal", "device", "license", "perpetual",
                        "delivered", "delivery", "migration", "setup", "installation",
                        "integration", "onboarding", "workshop", "milestone", "shipment",
                        "completed", "go-live", "acceptance"}
# Signals that CONTRADICT the declared delivery type (per the Step 5
# misconception paragraph) — these drop confidence.
_ONGOING_WORDS = {"ongoing", "continuous", "updates", "evolving", "recurring"}
_ONE_SHOT_WORDS = {"perpetual", "one-time", "single"}

# Areas the reference doc lists under "What This Project Deliberately Does
# Not Handle" — anything matching these is flagged for human review.
_OUT_OF_SCOPE_PATTERNS = [
    (r"financ(e|ing)|loan|interest", "significant financing components"),
    (r"principal|agent|resell|marketplace|third[- ]party seller", "principal vs. agent determination"),
    (r"multi[- ]currency|foreign currency|eur\b|gbp\b", "multi-currency contract rules"),
    (r"lease|leasing", "leases (ASC 842, outside ASC 606)"),
    (r"combined contract|contract combination", "contract combination rules"),
]


def _words(text: str) -> set[str]:
    # Drop negated phrases ("no ongoing service", "without updates") before
    # matching, so a negation isn't miscounted as a contradicting signal.
    cleaned = re.sub(r"\b(?:no|without|not)\s+\w+(?:\s+\w+)?", " ", text.lower())
    return set(re.findall(r"[a-z']+", cleaned))


def assess_obligation(ob: dict, siblings: list[dict] | None = None) -> dict:
    """Deterministic confidence + review check for one obligation.

    Two concerns are assessed INDEPENDENTLY so the reasons stay accurate:

    * ``confidence`` (high/medium/low) is about the RECOGNITION METHOD and is
      driven ONLY by the deliverable's own description/type. The same
      description therefore always yields the same confidence, regardless of
      the rest of the contract.
    * ``reviews`` is a list of accurate, separately-attributed reasons the
      obligation should get a human look — out-of-scope area, description that
      conflicts with the declared delivery type, a genuinely sparse
      description, or an allocation concern such as a tied standalone selling
      price. Each reason names its true trigger; they are never conflated.

    Returns {"confidence", "confidence_reason", "needs_review", "reviews"}.
    """
    blob = f"{ob['type']} {ob['description']}"
    w = _words(blob)
    reviews: list[str] = []

    # (A) Confidence in the recognition method — description-driven only.
    is_over_time = ob["method"] == "over_time"
    supporting = w & (_OVER_TIME_WORDS if is_over_time else _POINT_IN_TIME_WORDS)
    contradicting = w & (_ONE_SHOT_WORDS if is_over_time else _ONGOING_WORDS)
    out_of_scope = next((area for pat, area in _OUT_OF_SCOPE_PATTERNS
                         if re.search(pat, blob, re.IGNORECASE)), None)

    if out_of_scope:
        confidence = "low"
        confidence_reason = f"Touches an out-of-scope area: {out_of_scope}."
        reviews.append(
            f"The reference doc lists '{out_of_scope}' under its scope boundaries — "
            "flagged for human review rather than auto-classified.")
    elif contradicting:
        confidence = "low"
        confidence_reason = (
            f"Description mentions {', '.join(sorted(contradicting))!s}, which cuts against a "
            f"{'over-time' if is_over_time else 'point-in-time'} classification — the Step 5 "
            "criteria may not map cleanly.")
        reviews.append("Description conflicts with the declared delivery type — confirm the Step 5 criteria.")
    elif supporting:
        # Clear classifying vocabulary present — high confidence regardless of
        # word count. (This check MUST precede the sparse-length heuristic so a
        # short-but-clear description like "Perpetual software license" is not
        # mislabeled as sparse.)
        confidence = "high"
        confidence_reason = (
            "Description clearly maps to the reference doc's "
            f"{'over-time criterion 1 (continuous benefit)' if is_over_time else 'point-in-time control-transfer indicators'}.")
    elif len(ob["description"].split()) < 4:
        confidence = "low"
        confidence_reason = "Description is too sparse to verify distinctness (Step 2) or the Step 5 criteria."
        reviews.append("Deliverable description is too sparse to verify the classification.")
    else:
        confidence = "medium"
        confidence_reason = (
            "Delivery type is stated but the description doesn't use vocabulary that maps "
            "directly onto the Step 5 criteria — classification follows the declared type.")

    # (B) Allocation concern — tied standalone selling prices. This is its own
    # explicit check with its own message; it is NOT conflated with the
    # description checks above, and it does not change the method confidence.
    # (Only meaningful for priced obligations — excluded/unpriced ones are
    # handled separately in add_rationales.)
    if siblings and len(siblings) > 1 and ob.get("standalone_price_estimate") is not None:
        ties = [o for o in siblings
                if o is not ob
                and o.get("standalone_price_estimate") is not None
                and abs(o["standalone_price_estimate"] - ob["standalone_price_estimate"]) < 0.005]
        if ties:
            reviews.append(
                f"Shares an identical standalone selling price "
                f"(${ob['standalone_price_estimate']:,.2f}) with "
                f"{'another obligation' if len(ties) == 1 else f'{len(ties)} other obligations'} — "
                "the relative-SSP allocation (Step 4) may need manual confirmation.")

    return {
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "needs_review": bool(reviews),
        "reviews": reviews,
    }


# ---------------------------------------------------------------------------
# Grounded rationales
# ---------------------------------------------------------------------------

def _citation_for(ob: dict, processed: dict) -> str:
    if ob["method"] == "over_time":
        return ("Step 5, over-time criterion 1 (ASC 606-10-25-27): the customer "
                "simultaneously receives and consumes the benefit as the company performs")
    return ("Step 5, point-in-time recognition: none of the three over-time criteria are met; "
            "control transfers at delivery/acceptance")


def _template_rationale(ob: dict, processed: dict) -> str:
    total = processed["total_price"]
    # Only priced (non-excluded) obligations participate in the allocation.
    ssp_sum = sum(o["standalone_price_estimate"] for o in processed["obligations"]
                  if o.get("standalone_price_estimate") is not None)
    own_ssp = ob.get("standalone_price_estimate") or 0
    pct = own_ssp / ssp_sum * 100 if ssp_sum else 0
    if ob["method"] == "point_in_time":
        method_part = (
            "Recognized at a point in time per the reference guide's Step 5: none of the three "
            "over-time criteria apply — the company's obligation is satisfied in a single moment "
            "when control transfers (delivery/acceptance), even if the customer's own use is gradual."
        )
    else:
        method_part = (
            "Recognized over time under Step 5, criterion 1 (ASC 606-10-25-27): the customer "
            "simultaneously receives and consumes the benefit as the company performs, so revenue "
            "is spread monthly across the service period."
        )
    if len(processed["obligations"]) > 1 and not ob.get("added_by_modification"):
        method_part += (
            f" Per Step 4, it carries {pct:.0f}% of the bundle's standalone selling prices, so it is "
            f"allocated ${ob['allocated_price']:,.2f} of the ${total:,.2f} transaction price — the "
            "bundle discount is spread proportionally, not assigned to one item."
        )
    elif ob.get("added_by_modification"):
        method_part += (
            " Added by a mid-term modification treated as a prospective reallocation (the reference "
            "guide's modification approach 2): remaining unrecognized consideration plus the added "
            "fee were combined and reallocated over the remaining term."
        )
    return method_part


def _claude_rationales(processed: dict) -> dict[str, dict] | None:
    """One grounded API call per contract → {ob_id: {rationale, confidence}} or None."""
    try:
        import anthropic
    except ImportError:
        return None

    doc = get_doc()
    # Retrieve the sections relevant to this contract's features.
    queries = ["performance obligations distinct", "recognize revenue over time point in time criteria",
               "allocate transaction price standalone selling price"]
    if processed.get("modification_note"):
        queries.append("contract modifications prospective reallocation")
    if processed.get("variable_note"):
        queries.append("variable consideration transaction price constraint")
    seen, sections = set(), []
    for q in queries:
        for s in doc.retrieve(q, k=2):
            if s["title"] not in seen:
                seen.add(s["title"])
                sections.append(s)
    grounding = "\n\n".join(f"### {s['title']}\n{s['text']}" for s in sections)

    facts = {
        "contract_id": processed["contract_id"],
        "customer": processed["customer"],
        "term": f"{processed['start_date']} to {processed['end_date']}",
        "total_price": processed["total_price"],
        "obligations": [{k: o[k] for k in ("obligation_id", "type", "description",
                                           "standalone_price_estimate", "allocated_price",
                                           "method", "added_by_modification")}
                        for o in processed["obligations"]],
        "modification_note": processed.get("modification_note"),
        "variable_note": processed.get("variable_note"),
    }
    schema = {
        "type": "object",
        "properties": {"rationales": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "obligation_id": {"type": "string"},
                "rationale": {"type": "string"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            },
            "required": ["obligation_id", "rationale", "confidence"],
            "additionalProperties": False,
        }}},
        "required": ["rationales"],
        "additionalProperties": False,
    }
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=2500,
            system=(
                "You explain the output of a deterministic ASC 606 revenue recognition engine "
                "to a finance reviewer. Ground every statement in the reference guide excerpts "
                "provided — do not rely on general knowledge, and never contradict the excerpts. "
                "For each performance obligation write a 1-2 sentence rationale that names the "
                "specific rule applied (e.g. 'over time under Step 5 criterion 1 (ASC 606-10-25-27)', "
                "'Step 4 relative standalone selling price allocation', 'modification approach 2 — "
                "prospective reallocation'). Also assess confidence: high if the deliverable "
                "description maps cleanly onto the cited criteria, medium if the classification "
                "follows the declared delivery type without clear supporting language, low if the "
                "description is ambiguous or conflicts with the criteria. You are explaining "
                "decisions already made by deterministic rules — never second-guess or change them."
                f"\n\n# Reference guide excerpts\n\n{grounding}"
            ),
            messages=[{"role": "user", "content": json.dumps(facts, indent=2)}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if response.stop_reason == "refusal":
            return None
        text = next(b.text for b in response.content if b.type == "text")
        return {it["obligation_id"]: {"rationale": it["rationale"], "confidence": it["confidence"]}
                for it in json.loads(text)["rationales"]}
    except Exception:
        return None


def add_rationales(processed: dict) -> dict:
    """Attach grounded rationale + confidence + review flags per obligation."""
    ai = _claude_rationales(processed) if os.environ.get("ANTHROPIC_API_KEY") else None
    obligations = processed["obligations"]
    rationales, review_items = {}, []
    for ob in obligations:
        ob_id = ob["obligation_id"]

        # Excluded obligations (flagged during extraction: unpriced bundle,
        # variable consideration, ambiguous timing) are not classified or
        # allocated — they carry the extraction's own review reason straight
        # into the queue at low confidence, and are excluded from all totals.
        if ob.get("excluded"):
            reason = ob.get("review_reason") or "Flagged for human review."
            ob["confidence"] = "low"
            rationales[ob_id] = {
                "text": ("Flagged for review, not auto-classified or allocated: " + reason),
                "source": "extraction flag",
                "rule_citation": "Held out of Steps 4–5 pending human review (no reliable allocation/timing).",
                "confidence": "low",
                "confidence_reason": reason,
            }
            review_items.append({"obligation_id": ob_id, "type": ob["type"],
                                 "description": ob["description"], "reason": reason})
            continue

        assessment = assess_obligation(ob, obligations)

        if ai and ob_id in ai:
            text, source = ai[ob_id]["rationale"], "claude (RAG-grounded)"
            # The AI can only lower confidence relative to the deterministic
            # heuristic, never raise it above the rule-based floor checks.
            order = {"high": 2, "medium": 1, "low": 0}
            if order[ai[ob_id]["confidence"]] < order[assessment["confidence"]]:
                assessment["confidence"] = ai[ob_id]["confidence"]
                assessment["confidence_reason"] += " (Downgraded by AI review of the description.)"
                if assessment["confidence"] == "low":
                    assessment["reviews"].append(
                        "AI review judged the description ambiguous against the criteria.")
                    assessment["needs_review"] = True
        else:
            text, source = _template_rationale(ob, processed), "template (reference-doc grounded)"

        ob["confidence"] = assessment["confidence"]
        ob["needs_review"] = assessment["needs_review"]
        rationales[ob_id] = {
            "text": text,
            "source": source,
            "rule_citation": _citation_for(ob, processed),
            "confidence": assessment["confidence"],
            "confidence_reason": assessment["confidence_reason"],
        }
        # One Needs-Review entry per accurate reason, so a single obligation
        # can appear for both (say) a tied price and a sparse description
        # without either reason being mislabeled as the other.
        for reason in assessment["reviews"]:
            review_items.append({
                "obligation_id": ob_id,
                "type": ob["type"],
                "description": ob["description"],
                "reason": reason,
            })
    processed["rationales"] = rationales
    processed["needs_review"] = review_items
    return processed
