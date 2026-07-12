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
print("\nSeed regression — all seeds still classify high, no spurious reviews")
# ---------------------------------------------------------------------------
import json
seeds = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "data", "seed_contracts.json")))
counts = {"high": 0, "medium": 0, "low": 0}
reviews = 0
for s in seeds:
    p = add_rationales(process_contract(s))
    for ob in p["obligations"]:
        counts[ob["confidence"]] += 1
    reviews += len(p["needs_review"])
check("all 30 seed obligations high confidence", counts["high"] == 30, str(counts))
check("no seed contracts flagged for review", reviews == 0, f"{reviews} flagged")

# ---------------------------------------------------------------------------
print(f"\n{'='*50}")
print(f"{sum(_results)}/{len(_results)} checks passed")
sys.exit(0 if all(_results) else 1)
