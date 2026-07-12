"""Generate the 10 realistic prose contract PDFs for the seed set.

Each is written as a real order form / SOW / short MSA excerpt: a letterhead
line, a title, numbered sections (Term, Scope/Services, Fees), and a signature
block — with the actual deal terms living inside the prose, NOT as label:value
fields. These are the human-facing artifacts; the matching machine-extracted
representation is authored in data/seed_contracts.json (ground truth for the
deploy, which has no API key at seed time). When ANTHROPIC_API_KEY is set, any
of these same PDFs can be re-uploaded and the live prose extractor reproduces
the extraction.

Run:  python3 scripts/make_seed_pdfs.py
Writes data/seed_pdfs/*.pdf and copies contract 1 to static/sample_contract.pdf.
"""
import os
import shutil

from fpdf import FPDF

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, "data", "seed_pdfs")
os.makedirs(OUT_DIR, exist_ok=True)


def make(filename, vendor, title, sections, vendor_sig, customer_sig):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, vendor, new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(200, 200, 195)
    pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 12)
    pdf.multi_cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 10.5)
    for heading, body in sections:
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.multi_cell(0, 6, heading, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10.5)
        pdf.multi_cell(0, 5.5, body, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 10.5)
    pdf.multi_cell(0, 5.5, "IN WITNESS WHEREOF, the parties have executed this agreement as of the "
                   "dates set forth below.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.cell(0, 6, f"Vendor: {vendor_sig}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "By: _______________________    Date: ____________", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.cell(0, 6, f"Customer: {customer_sig}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "By: _______________________    Date: ____________", new_x="LMARGIN", new_y="NEXT")
    pdf.output(os.path.join(OUT_DIR, filename))


# 1 — Simple SaaS subscription (clean over-time). Also the dashboard sample.
make("01_northstar_saas.pdf", "Northstar Analytics, Inc.",
     "Order Form - Platform Subscription",
     [("1. Term", "This Order Form is effective January 1, 2026 and continues for a period of "
       "twelve (12) months, ending December 31, 2026."),
      ("2. Services", "Vendor shall provide Customer with access to the Northstar Analytics Platform "
       "for the duration of the Term. Customer's authorized users may access the hosted Platform on a "
       "continuous basis throughout the Term."),
      ("3. Fees", "The subscription fee is seventy-two thousand dollars ($72,000), payable annually "
       "in advance.")],
     "Northstar Analytics, Inc.", "Cedar Grove Financial")

# 2 — One-time hardware sale (clean point-in-time).
make("02_ironclad_hardware.pdf", "Ironclad Devices Co.",
     "Sales Order - Payment Terminals",
     [("1. Goods", "Vendor shall deliver to Customer thirty (30) Ironclad payment terminal units. "
       "Title and risk of loss pass to Customer upon delivery, which shall occur upon execution of "
       "this order on February 10, 2026."),
      ("2. Price", "The total purchase price for the units is twenty-four thousand dollars ($24,000), "
       "due upon delivery.")],
     "Ironclad Devices Co.", "Harborview Clinics")

# 3 — Bundle, single aggregate fee, no per-component pricing (flag for review).
make("03_meridian_bundle.pdf", "Meridian Software Group",
     "Order Form - Practice Management Suite",
     [("1. Scope", "Vendor will provide Customer a perpetual license to the Meridian practice-management "
       "software, together with onboarding and configuration services to stand the software up in "
       "Customer's environment."),
      ("2. Fees", "Customer shall pay a single all-inclusive fee of forty thousand dollars ($40,000) for "
       "the software license and the onboarding and configuration services described above. The parties "
       "have not separately stated a price for the license or for the services."),
      ("3. Effective Date", "This Order Form is executed on March 3, 2026.")],
     "Meridian Software Group", "Talbot & Reyes LLP")

# 4 — Support/maintenance over 24 months + mid-term modification.
make("04_blueridge_support.pdf", "Blue Ridge Systems, LLC",
     "Master Support Agreement - Excerpt",
     [("1. Term", "The initial Term of this Agreement is twenty-four (24) months, commencing "
       "January 1, 2026 and ending December 31, 2027."),
      ("2. Services", "Vendor shall provide ongoing maintenance and support for Customer's Blue Ridge "
       "control systems throughout the Term, including monitoring, updates, and issue resolution as "
       "needed."),
      ("3. Fees", "Support is billed at a recurring rate of three thousand dollars ($3,000) per month, "
       "for a total of seventy-two thousand dollars ($72,000) over the initial Term."),
      ("4. Modification", "In month 12 of the Term, Customer and Vendor agreed to expand coverage to add "
       "Premium Priority Support for the remainder of the Term, for additional consideration of eighteen "
       "thousand dollars ($18,000). This change is effective December 1, 2026.")],
     "Blue Ridge Systems, LLC", "Pinnacle Manufacturing")

# 5 — Bundle with partial standalone pricing (back into allocation).
make("05_vertex_partial.pdf", "Vertex Software Corp.",
     "Order Form - Point-of-Sale Suite",
     [("1. Scope", "Vendor will provide (a) a perpetual software license to the Vertex point-of-sale "
       "suite, separately priced at thirty thousand dollars ($30,000), and (b) implementation and store "
       "configuration services."),
      ("2. Total Fee", "The total contract value is fifty thousand dollars ($50,000). The implementation "
       "and configuration services are priced at the remainder of the total after the separately stated "
       "license price."),
      ("3. Delivery", "Both the license and the implementation services are delivered and completed at "
       "go-live upon execution on April 15, 2026.")],
     "Vertex Software Corp.", "Coastline Retail")

# 6 — One-time professional services (report, point-in-time).
make("06_sterling_report.pdf", "Sterling Advisory Partners",
     "Statement of Work - Market Assessment",
     [("1. Deliverable", "Vendor shall prepare and deliver to Customer a single written market-assessment "
       "report. The report is due within thirty (30) days of execution and constitutes the entire "
       "deliverable under this engagement."),
      ("2. Fee", "The fee for the engagement is a flat twenty-two thousand five hundred dollars "
       "($22,500), due upon delivery of the report."),
      ("3. Effective Date", "This Statement of Work is executed on May 5, 2026; the report is delivered "
       "on or about June 4, 2026.")],
     "Sterling Advisory Partners", "Fenwick Holdings")

# 7 — Ambiguous platform fee, weak delivery language (reason to over-time, lower confidence).
make("07_quantum_platform.pdf", "Quantum Ledger Systems",
     "Order Form - Annual Platform Fee",
     [("1. Term", "The Term of this Order Form is twelve (12) months, beginning February 1, 2026."),
      ("2. Fee", "Customer shall pay an annual platform fee of forty-eight thousand dollars ($48,000) "
       "for use of the Quantum Ledger Services during the Term."),
      ("3. General", "Use of the Services is subject to Vendor's standard terms. The fee is non-refundable "
       "once the Term begins.")],
     "Quantum Ledger Systems", "Ashford Group")

# 8 — Fully-priced two-obligation bundle (clean allocation, no flag).
make("08_nimbus_bundle.pdf", "Nimbus Payments, Inc.",
     "Order Form - License and Support",
     [("1. Scope", "Vendor shall provide Customer (a) a perpetual license to the Nimbus Payments "
       "software, and (b) twelve (12) months of support and platform maintenance."),
      ("2. Term", "The support term is twelve (12) months, commencing March 1, 2026. The license is "
       "delivered at commencement."),
      ("3. Fees", "The perpetual software license is separately priced at twenty-four thousand dollars "
       "($24,000). The twelve months of support and maintenance are separately priced at twelve "
       "thousand dollars ($12,000), for a total contract value of thirty-six thousand dollars ($36,000).")],
     "Nimbus Payments, Inc.", "Riverstone Markets")

# 9 — Three-component bundle, single total, no per-component pricing (hardest; flag).
make("09_apex_three.pdf", "Apex Systems, Inc.",
     "Master Agreement - Clinical Platform",
     [("1. Scope", "Vendor shall provide Customer: (a) a perpetual license to the Apex clinical platform; "
       "(b) a one-time migration of Customer's existing clinical records; and (c) ongoing support and "
       "maintenance for a period of twenty-four (24) months."),
      ("2. Total Consideration", "The total consideration for all of the foregoing is one hundred fifty "
       "thousand dollars ($150,000). No separate price is stated for the license, the migration, or the "
       "ongoing support."),
      ("3. Effective Date", "This Agreement is executed on June 1, 2026.")],
     "Apex Systems, Inc.", "Grandview Health")

# 10 — Simple delivery/installation project (clean point-in-time baseline).
make("10_sparrow_install.pdf", "Sparrow Equipment Co.",
     "Purchase Order - Equipment and Installation",
     [("1. Goods and Installation", "Vendor shall deliver and install refrigeration equipment at "
       "Customer's facility. Delivery and installation shall be completed within fifteen (15) days of "
       "this purchase order, on or about February 4, 2026."),
      ("2. Price", "The total price for the equipment and installation is fifteen thousand dollars "
       "($15,000), due upon completion of installation.")],
     "Sparrow Equipment Co.", "Lakeside Foods")

# The dashboard's downloadable sample is the simple, unambiguous SaaS contract.
shutil.copyfile(os.path.join(OUT_DIR, "01_northstar_saas.pdf"),
                os.path.join(BASE, "static", "sample_contract.pdf"))

print(f"Wrote 10 prose seed PDFs to {OUT_DIR}/ and refreshed static/sample_contract.pdf")
