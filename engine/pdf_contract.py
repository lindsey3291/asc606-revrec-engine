"""Parse a contract PDF (LedgerPay order-form template) into the contract dict.

The dashboard accepts contract uploads as PDFs that follow the sample order
form (static/sample_contract.pdf). Parsing is deterministic: the PDF text is
extracted with pypdf and matched against labeled fields — no AI is involved.
A PDF that doesn't follow the template is rejected with a clear error that
points the user at the sample.
"""
from __future__ import annotations

import re

from pypdf import PdfReader

from engine.core import ContractValidationError

TEMPLATE_HINT = (
    "The PDF must follow the sample order form (download it from the dashboard): "
    "labeled fields 'Contract ID:', 'Customer:', 'Start Date:' (YYYY-MM-DD), "
    "'End Date:', 'Total Price:', then one deliverable per line as "
    "'Type: ... | Description: ... | Standalone Price: ... | Delivery: one_time or over_time'."
)


def _money(s: str) -> float:
    return float(s.replace(",", "").replace("$", ""))


def parse_contract_pdf(fileobj) -> dict:
    try:
        reader = PdfReader(fileobj)
        raw = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        raise ContractValidationError("Could not read the PDF file. " + TEMPLATE_HINT)

    # Collapse all whitespace so PDF line-wrapping can't break field matching.
    text = re.sub(r"\s+", " ", raw)

    def field(pattern: str, label: str) -> str:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            raise ContractValidationError(f"Could not find '{label}' in the PDF. " + TEMPLATE_HINT)
        return m.group(1).strip()

    contract_id = field(r"Contract ID:\s*(.+?)\s+Customer:", "Contract ID")
    customer = field(r"Customer:\s*(.+?)\s+Start Date:", "Customer")
    start_date = field(r"Start Date:\s*(\d{4}-\d{2}-\d{2})", "Start Date")
    end_date = field(r"End Date:\s*(\d{4}-\d{2}-\d{2})", "End Date")
    total_price = _money(field(r"Total Price:\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", "Total Price"))

    deliverables = []
    for m in re.finditer(
        r"Type:\s*([A-Za-z_]+)\s*\|\s*Description:\s*(.+?)\s*\|\s*"
        r"Standalone Price:\s*\$?\s*([\d,]+(?:\.\d{1,2})?)\s*\|\s*"
        r"Delivery:\s*(one_time|over_time)",
        text, re.IGNORECASE,
    ):
        deliverables.append({
            "type": m.group(1).lower(),
            "description": m.group(2).strip(),
            "standalone_price_estimate": _money(m.group(3)),
            "delivery_type": m.group(4).lower(),
        })

    if not deliverables:
        raise ContractValidationError("No deliverable lines found in the PDF. " + TEMPLATE_HINT)

    return {
        "contract_id": contract_id,
        "customer": customer,
        "start_date": start_date,
        "end_date": end_date,
        "total_price": total_price,
        "deliverables": deliverables,
    }
