"""Generate the sample contract PDF that documents the upload template.

Run locally (requires fpdf2: pip install fpdf2):
    python3 scripts/make_sample_pdf.py

The output (static/sample_contract.pdf) is committed to the repo so the
deployed app can serve it without a PDF-generation dependency.
"""
import os
import sys

from fpdf import FPDF

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "static", "sample_contract.pdf")

pdf = FPDF()
pdf.add_page()
pdf.set_font("Helvetica", "B", 16)
pdf.cell(0, 10, "LedgerPay - Customer Order Form", new_x="LMARGIN", new_y="NEXT")
pdf.set_font("Helvetica", "", 9)
pdf.set_text_color(110, 110, 105)
pdf.cell(0, 6, "Sample contract for the ASC 606 Revenue Recognition Engine. "
              "Keep the field labels exactly as shown.", new_x="LMARGIN", new_y="NEXT")
pdf.ln(4)
pdf.set_text_color(0, 0, 0)

pdf.set_font("Helvetica", "", 11)
for line in [
    "Contract ID: C-200",
    "Customer: Sample Customer Inc",
    "Start Date: 2026-09-01",
    "End Date: 2027-08-31",
    "Total Price: $60,000.00",
]:
    pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")

pdf.ln(4)
pdf.set_font("Helvetica", "B", 12)
pdf.cell(0, 8, "Deliverables", new_x="LMARGIN", new_y="NEXT")
pdf.set_font("Helvetica", "", 9)
for line in [
    "1. Type: subscription | Description: 12-month platform subscription | "
    "Standalone Price: $48,000.00 | Delivery: over_time",
    "2. Type: implementation | Description: Onboarding and data setup | "
    "Standalone Price: $12,000.00 | Delivery: one_time",
]:
    pdf.multi_cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

pdf.ln(4)
pdf.set_font("Helvetica", "I", 8)
pdf.set_text_color(110, 110, 105)
pdf.multi_cell(0, 5,
    "Delivery must be one_time (point-in-time recognition) or over_time (spread "
    "monthly across the term). Dates must be YYYY-MM-DD. Add or remove numbered "
    "deliverable lines as needed - the total price is allocated across them in "
    "proportion to their standalone prices. Prices may be written $12,000, "
    "$12,000.00, or 12000; negative prices are rejected.")
pdf.ln(2)
pdf.multi_cell(0, 5,
    "OPTIONAL - mid-term modification. To add one, include a line exactly like: "
    "Modification: Mod Date: 2026-05-01 | Mod Description: Added a module | "
    "Added Price: $24,000.00 | Added Type: subscription_addon | Added Description: "
    "Fraud analytics for remaining term | Added Standalone Price: $24,000.00 | "
    "Added Delivery: over_time . If a Modification line is present but any field "
    "is missing, the upload is flagged for review rather than silently ignored.")

pdf.output(OUT)
print(f"Wrote {OUT}")
