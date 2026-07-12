"""Regression tests for the three stress-test bugs + delete consistency.

Run from the project root:  python3 scripts/test_bugfixes.py
Exercises pure engine logic (no server needed). Prints PASS/FAIL per case.
"""
from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpdf import FPDF

from engine.core import ContractValidationError, process_contract
from engine.explain import add_rationales, assess_obligation
from engine.pdf_contract import _parse_money, parse_contract_pdf

_results = []


def check(name, cond, detail=""):
    _results.append(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def _pdf(lines: list[str]) -> io.BytesIO:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "", 10)
    for ln in lines:
        pdf.multi_cell(0, 6, ln, new_x="LMARGIN", new_y="NEXT")
    buf = io.BytesIO(pdf.output())
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
print("\nBug 3 — price parsing robustness")
# ---------------------------------------------------------------------------
for raw, expected in [("$5,000.00", 5000.0), ("$5000", 5000.0), ("$5,000", 5000.0),
                      ("5,000", 5000.0), ("$-5,000.00", -5000.0), ("-$5,000.00", -5000.0),
                      ("($5,000.00)", -5000.0), ("$1,234.56", 1234.56)]:
    got = _parse_money(raw)
    check(f"parse {raw!r} -> {expected}", abs(got - expected) < 0.005, f"got {got}")

# Negative total price rejected with a SPECIFIC message (not "field not found").
for fmt in ["Total Price: $-5,000.00", "Total Price: ($5,000.00)"]:
    lines = ["Contract ID: C-NEG", "Customer: Neg Co", "Start Date: 2026-01-01",
             "End Date: 2026-12-31", fmt,
             "Type: subscription | Description: 12-month platform subscription | "
             "Standalone Price: $5,000.00 | Delivery: over_time"]
    try:
        parse_contract_pdf(_pdf(lines))
        check(f"negative total ({fmt}) rejected", False, "no error raised")
    except ContractValidationError as e:
        msg = str(e)
        check(f"negative total ({fmt}) rejected with accurate message",
              "cannot be negative" in msg and "could not find" not in msg.lower(), msg)

# Genuinely missing total price still says "could not find".
lines = ["Contract ID: C-MISS", "Customer: Miss Co", "Start Date: 2026-01-01",
         "End Date: 2026-12-31",
         "Type: subscription | Description: 12-month subscription | "
         "Standalone Price: $5,000.00 | Delivery: over_time"]
try:
    parse_contract_pdf(_pdf(lines))
    check("missing total price rejected", False, "no error")
except ContractValidationError as e:
    check("missing total price says 'could not find'", "Could not find 'Total Price'" in str(e), str(e))

# Standard positive PDF still parses (regression).
lines = ["Contract ID: C-POS", "Customer: Pos Co", "Start Date: 2026-01-01",
         "End Date: 2026-12-31", "Total Price: $12,000",
         "Type: subscription | Description: 12-month platform subscription | "
         "Standalone Price: $12,000 | Delivery: over_time"]
c = parse_contract_pdf(_pdf(lines))
check("standard positive PDF parses", c["total_price"] == 12000.0 and len(c["deliverables"]) == 1)

# ---------------------------------------------------------------------------
print("\nBug 1 — modification field never silently dropped")
# ---------------------------------------------------------------------------
# Full, parseable modification -> applied (prospective reallocation runs).
full_mod = ["Contract ID: C-MOD", "Customer: Mod Co", "Start Date: 2025-11-01",
            "End Date: 2026-10-31", "Total Price: $96,000.00",
            "Type: subscription | Description: 12-month claims platform subscription | "
            "Standalone Price: $96,000.00 | Delivery: over_time",
            "Modification: Mod Date: 2026-05-01 | Mod Description: Added fraud module | "
            "Added Price: $24,000.00 | Added Type: subscription_addon | "
            "Added Description: Fraud analytics for the remaining term | "
            "Added Standalone Price: $24,000.00 | Added Delivery: over_time"]
c = parse_contract_pdf(_pdf(full_mod))
check("modification extracted from PDF", "modification" in c, "no modification key")
p = process_contract(c)
check("modification applied (2 obligations, note present)",
      len(p["obligations"]) == 2 and p["modification_note"] is not None)

# Modification line present but incomplete -> flagged for review, NOT ignored.
partial_mod = ["Contract ID: C-MOD2", "Customer: Mod2 Co", "Start Date: 2025-11-01",
               "End Date: 2026-10-31", "Total Price: $96,000.00",
               "Type: subscription | Description: 12-month subscription | "
               "Standalone Price: $96,000.00 | Delivery: over_time",
               "Modification: Mod Date: 2026-05-01 | Mod Description: Added a module"]
try:
    parse_contract_pdf(_pdf(partial_mod))
    check("incomplete modification flagged", False, "processed silently!")
except ContractValidationError as e:
    check("incomplete modification flagged for review (not silent)",
          "requires review" in str(e) and "NOT silently ignored" in str(e), str(e))

# ---------------------------------------------------------------------------
print("\nBug 2 — confidence reasons accurate & consistent")
# ---------------------------------------------------------------------------
DESC = "Perpetual software license"

# Same description, DISTINCT sibling prices -> high, no sparse/tie flag.
distinct = process_contract({
    "contract_id": "C-D", "customer": "D", "start_date": "2026-01-01", "end_date": "2026-12-31",
    "total_price": 60000, "deliverables": [
        {"type": "license", "description": DESC, "standalone_price_estimate": 40000, "delivery_type": "one_time"},
        {"type": "support", "description": "12 months of premium support", "standalone_price_estimate": 20000, "delivery_type": "over_time"},
    ]})
a_distinct = assess_obligation(distinct["obligations"][0], distinct["obligations"])

# Same description, TIED sibling prices -> still high confidence, but a tied-SSP review (accurate).
tied = process_contract({
    "contract_id": "C-T", "customer": "T", "start_date": "2026-01-01", "end_date": "2026-12-31",
    "total_price": 60000, "deliverables": [
        {"type": "license", "description": DESC, "standalone_price_estimate": 30000, "delivery_type": "one_time"},
        {"type": "license", "description": "Perpetual analytics module license", "standalone_price_estimate": 30000, "delivery_type": "one_time"},
    ]})
a_tied = assess_obligation(tied["obligations"][0], tied["obligations"])

check("same description -> consistent confidence across contracts",
      a_distinct["confidence"] == a_tied["confidence"] == "high",
      f"distinct={a_distinct['confidence']} tied={a_tied['confidence']}")
check("distinct-price version has no review flag", not a_distinct["needs_review"], str(a_distinct["reviews"]))
check("tied-price version flagged with ACCURATE tied-SSP reason (not 'sparse')",
      a_tied["needs_review"]
      and any("identical standalone selling price" in r for r in a_tied["reviews"])
      and not any("too sparse" in r for r in a_tied["reviews"]),
      str(a_tied["reviews"]))
check("clear short description not mislabeled sparse",
      "too sparse" not in a_distinct["confidence_reason"].lower(), a_distinct["confidence_reason"])

# ---------------------------------------------------------------------------
print("\nProse seed set — flagging, exclusion & modification")
# ---------------------------------------------------------------------------
import json
from engine.core import excluded_pending, rpo_forecast
seeds = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "data", "seed_contracts.json")))
sp = [add_rationales(process_contract(s)) for s in seeds]
by_id = {p["contract_id"]: p for p in sp}

check("exactly 10 seed contracts", len(sp) == 10, str(len(sp)))
# Contracts 3, 8, 9 must NOT come out with a confident, silent classification.
for cid in ("ORD-2603", "ORD-2609"):
    p = by_id[cid]
    check(f"{cid} fully flagged (no recognized amount)",
          p["recognized_amount"] == 0 and all(o["excluded"] for o in p["obligations"]),
          f"recognized={p['recognized_amount']}")
c8 = by_id["ORD-2608"]
check("ORD-2608 base recognized, variable flagged",
      c8["recognized_amount"] == 36000 and any(o["excluded"] for o in c8["obligations"])
      and c8["category"] == "variable")

# Flagged obligations are excluded from every aggregate.
ep = excluded_pending(sp)
check("excluded-pending total = $198,000 across 3 contracts",
      ep["total_excluded"] == 198000 and len(ep["items"]) == 3, str(ep["total_excluded"]))
rpo = rpo_forecast(sp, "2026-06", 12)
check("flagged contracts absent from RPO forecast",
      "ORD-2603" not in rpo["by_contract"] and "ORD-2609" not in rpo["by_contract"])

# Modification on seed #4 is applied (prospective reallocation), not dropped.
c4 = by_id["ORD-2604"]
check("ORD-2604 modification applied (2 obligations + note)",
      len(c4["obligations"]) == 2 and c4["modification_note"] is not None)

# Every contract's deferred revenue drains to zero (included portions tie out).
check("all seed contracts: final deferred revenue = 0",
      all(abs(p["deferred_revenue"][-1]["ending_balance"] if p["deferred_revenue"] else 0) < 0.01 for p in sp))

# Clean (non-flagged) obligations are confidently classified (high or medium,
# never low); flagged obligations are low. Unambiguous ones (#1 SaaS, #2/#10
# hardware) should be high specifically.
clean_not_low = all(o["confidence"] in ("high", "medium")
                    for p in sp for o in p["obligations"] if not o.get("excluded"))
check("clean obligations classified confidently (not low)", clean_not_low)
unambiguous_high = all(by_id[cid]["obligations"][0]["confidence"] == "high"
                       for cid in ("ORD-2601", "ORD-2602", "ORD-2610"))
check("unambiguous SaaS/hardware obligations are high confidence", unambiguous_high)
flagged_low = all(o["confidence"] == "low"
                  for p in sp for o in p["obligations"] if o.get("excluded"))
check("all flagged obligations are low confidence", flagged_low)

# ---------------------------------------------------------------------------
print("\nBackward compatibility — structured label:value PDF still parses")
# ---------------------------------------------------------------------------
from engine.extract import extract_contract, is_structured, _pdf_text
struct = _pdf([
    "Contract ID: C-LEGACY", "Customer: Legacy Structured Co", "Start Date: 2026-01-01",
    "End Date: 2026-12-31", "Total Price: $60,000.00",
    "Type: subscription | Description: 12-month platform subscription | "
    "Standalone Price: $60,000.00 | Delivery: over_time"])
struct.seek(0)
check("legacy structured PDF detected as structured", is_structured(_pdf_text(struct)))
struct.seek(0)
lc = extract_contract(struct, "legacy.pdf")
check("legacy structured PDF still extracts correctly",
      lc["contract_id"] == "C-LEGACY" and lc["total_price"] == 60000.0
      and lc["deliverables"][0]["delivery_type"] == "over_time")
prose = _pdf(["Northstar Analytics, Inc.", "Order Form - Platform Subscription",
              "Vendor shall provide Customer access to the Platform for a period of twelve (12) months.",
              "The subscription fee is seventy-two thousand dollars ($72,000)."])
check("prose PDF detected as prose (routes to AI extractor)", not is_structured(_pdf_text(prose)))

# ---------------------------------------------------------------------------
print(f"\n{'='*50}")
print(f"{sum(_results)}/{len(_results)} checks passed")
sys.exit(0 if all(_results) else 1)
