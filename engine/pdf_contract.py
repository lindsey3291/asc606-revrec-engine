"""Parse a contract PDF (LedgerPay order-form template) into the contract dict.

The dashboard accepts contract uploads as PDFs that follow the sample order
form (static/sample_contract.pdf). Parsing is deterministic: the PDF text is
extracted with pypdf and matched against labeled fields — no AI is involved.
A PDF that doesn't follow the template is rejected with a clear error that
points the user at the sample.

Two robustness rules this parser enforces:
  * A recognized field is never silently dropped. If an optional block
    (e.g. a "Modification:" line) is present but can't be fully parsed, the
    upload is rejected with a review message rather than processed as if the
    field weren't there.
  * Price fields distinguish "present but unparseable / invalid" from
    "genuinely missing", and negative prices are rejected with a specific
    message rather than silently accepted or reported as a missing field.
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

# A currency token tolerant of common formatting: optional parentheses
# (negative), a minus before or after the dollar sign, thousands separators,
# and optional decimals. Examples matched: $5,000.00  $5000  -$5,000  $-5000
# ($5,000.00)  5,000
_PRICE_TOKEN = r"\(?\s*-?\s*\$?\s*-?\s*[\d,]+(?:\.\d{1,2})?\s*-?\s*\)?"


def _parse_money(token: str) -> float:
    """Parse a currency token to a float, preserving sign. Raises ValueError
    if there are no digits to parse."""
    t = token.strip()
    negative = False
    if t.startswith("(") and t.rstrip().endswith(")"):
        negative = True
        t = t[1:t.rindex(")")]
    t = t.replace("$", "").replace(",", "").replace(" ", "")
    if t.startswith("-"):
        negative = True
        t = t[1:]
    if t.endswith("-"):
        negative = True
        t = t[:-1]
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", t):
        raise ValueError(f"unparseable amount: {token!r}")
    val = float(t)
    return -val if negative else val


def _fmt_money(v: float) -> str:
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


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

    def price_field(label: str) -> float:
        """Extract a labeled price, distinguishing missing / unparseable /
        negative and rejecting negatives with a specific message."""
        if not re.search(re.escape(label) + r"\s*:", text, re.IGNORECASE):
            raise ContractValidationError(f"Could not find '{label}' in the PDF. " + TEMPLATE_HINT)
        m = re.search(re.escape(label) + r"\s*:\s*(" + _PRICE_TOKEN + r")", text, re.IGNORECASE)
        if not m:
            raise ContractValidationError(
                f"Found '{label}' in the PDF but could not read its value as an amount. " + TEMPLATE_HINT)
        try:
            val = _parse_money(m.group(1))
        except ValueError:
            raise ContractValidationError(
                f"Found '{label}' in the PDF but could not read its value as an amount. " + TEMPLATE_HINT)
        if val < 0:
            raise ContractValidationError(f"{label} cannot be negative — found {_fmt_money(val)}.")
        if val == 0:
            raise ContractValidationError(f"{label} must be greater than zero — found $0.00.")
        return val

    contract_id = field(r"Contract ID:\s*(.+?)\s+Customer:", "Contract ID")
    customer = field(r"Customer:\s*(.+?)\s+Start Date:", "Customer")
    start_date = field(r"Start Date:\s*(\d{4}-\d{2}-\d{2})", "Start Date")
    end_date = field(r"End Date:\s*(\d{4}-\d{2}-\d{2})", "End Date")
    total_price = price_field("Total Price")

    deliverables = []
    for i, m in enumerate(re.finditer(
        r"Type:\s*([A-Za-z_]+)\s*\|\s*Description:\s*(.+?)\s*\|\s*"
        r"Standalone Price:\s*(" + _PRICE_TOKEN + r")\s*\|\s*"
        r"Delivery:\s*(one_time|over_time)",
        text, re.IGNORECASE), start=1):
        try:
            ssp = _parse_money(m.group(3))
        except ValueError:
            raise ContractValidationError(
                f"Standalone Price for deliverable {i} could not be read as an amount. " + TEMPLATE_HINT)
        if ssp < 0:
            raise ContractValidationError(
                f"Standalone Price for deliverable {i} cannot be negative — found {_fmt_money(ssp)}.")
        deliverables.append({
            "type": m.group(1).lower(),
            "description": m.group(2).strip(),
            "standalone_price_estimate": ssp,
            "delivery_type": m.group(4).lower(),
        })

    if not deliverables:
        raise ContractValidationError("No deliverable lines found in the PDF. " + TEMPLATE_HINT)

    contract = {
        "contract_id": contract_id,
        "customer": customer,
        "start_date": start_date,
        "end_date": end_date,
        "total_price": total_price,
        "deliverables": deliverables,
    }

    # Optional modification block. If a 'Modification:' label is present it
    # must NEVER be silently dropped: either every field parses and we attach
    # the modification (so prospective reallocation runs downstream), or we
    # reject the upload with a review message.
    mod = _parse_modification(text)
    if mod is not None:
        contract["modification"] = mod

    return contract


_MOD_REVIEW_MSG = (
    "This contract includes a modification that requires review — automatic "
    "modification processing needs all modification fields present. Provide "
    "'Modification:' with 'Mod Date', 'Mod Description', 'Added Price', "
    "'Added Type', 'Added Description', 'Added Standalone Price', and "
    "'Added Delivery' (must be over_time), or upload the contract as JSON. "
    "The modification was NOT silently ignored — it is flagged here for review."
)


def _parse_modification(text: str):
    """Return a modification dict, None if no 'Modification:' block is present,
    or raise a review error if the block is present but incomplete."""
    if not re.search(r"Modification\s*:", text, re.IGNORECASE):
        return None

    def sub(pattern):
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    mod_date = sub(r"Mod Date:\s*(\d{4}-\d{2}-\d{2})")
    mod_desc = sub(r"Mod Description:\s*(.+?)\s*\|")
    added_price_tok = sub(r"Added Price:\s*(" + _PRICE_TOKEN + r")")
    added_type = sub(r"Added Type:\s*([A-Za-z_]+)")
    added_desc = sub(r"Added Description:\s*(.+?)\s*\|")
    added_ssp_tok = sub(r"Added Standalone Price:\s*(" + _PRICE_TOKEN + r")")
    added_delivery = sub(r"Added Delivery:\s*(one_time|over_time)")

    if not all([mod_date, mod_desc, added_price_tok, added_type,
                added_desc, added_ssp_tok, added_delivery]):
        raise ContractValidationError(_MOD_REVIEW_MSG)

    try:
        added_price = _parse_money(added_price_tok)
        added_ssp = _parse_money(added_ssp_tok)
    except ValueError:
        raise ContractValidationError(_MOD_REVIEW_MSG)
    if added_price < 0 or added_ssp < 0:
        raise ContractValidationError("Modification prices cannot be negative.")
    if added_delivery.lower() != "over_time":
        raise ContractValidationError(
            "Automatic modification processing currently supports over_time additions only "
            "(prospective reallocation). This modification is flagged for human review.")

    return {
        "date": mod_date,
        "description": mod_desc,
        "added_price": added_price,
        "added_deliverable": {
            "type": added_type.lower(),
            "description": added_desc,
            "standalone_price_estimate": added_ssp,
            "delivery_type": "over_time",
        },
    }
