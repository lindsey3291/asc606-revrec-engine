"""Deterministic ASC 606 revenue recognition engine.

All classification, allocation, scheduling, journal-entry, and forecast
logic lives here. It is pure rule-based Python — no AI is involved in any
number that this module produces. The AI layer (engine/explain.py) only
explains the output after the fact.

Money is handled internally in integer cents to avoid float drift; all
public output is in dollars rounded to 2 decimals. Recognition schedules
are bucketed by calendar month ("YYYY-MM" keys).
"""
from __future__ import annotations

import calendar
from datetime import date

REQUIRED_CONTRACT_FIELDS = (
    "contract_id", "customer", "start_date", "end_date", "total_price", "deliverables",
)
REQUIRED_DELIVERABLE_FIELDS = (
    "type", "description", "standalone_price_estimate", "delivery_type",
)
VALID_DELIVERY_TYPES = ("one_time", "over_time")


class ContractValidationError(ValueError):
    """Raised when an input contract does not match the expected structure."""


# ---------------------------------------------------------------------------
# Month helpers
# ---------------------------------------------------------------------------

def month_key(date_str: str) -> str:
    """'2026-03-15' -> '2026-03'."""
    return date_str[:7]


def _month_index(key: str) -> int:
    y, m = key.split("-")
    return int(y) * 12 + int(m) - 1


def _key_from_index(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def month_span(start_key: str, end_key: str) -> list[str]:
    """Inclusive list of month keys from start to end."""
    return [_key_from_index(i) for i in range(_month_index(start_key), _month_index(end_key) + 1)]


def add_months(key: str, n: int) -> str:
    return _key_from_index(_month_index(key) + n)


def month_end(key: str) -> str:
    """Last calendar day of a month key, as a full date string."""
    y, m = int(key[:4]), int(key[5:7])
    return f"{key}-{calendar.monthrange(y, m)[1]:02d}"


def _cents(x) -> int:
    return int(round(float(x) * 100))


def _dollars(c: int) -> float:
    return round(c / 100.0, 2)


def _spread(total_cents: int, n: int) -> list[int]:
    """Split an amount evenly over n periods; rounding residual goes to the last period."""
    base = total_cents // n
    out = [base] * n
    out[-1] += total_cents - base * n
    return out


def _parse_date(s: str) -> date:
    try:
        y, m, d = s.split("-")
        return date(int(y), int(m), int(d))
    except Exception:
        raise ContractValidationError(f"Invalid date '{s}' — expected YYYY-MM-DD")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_contract(contract: dict) -> None:
    if not isinstance(contract, dict):
        raise ContractValidationError("Contract must be a JSON object")
    for f in REQUIRED_CONTRACT_FIELDS:
        if f not in contract:
            raise ContractValidationError(f"Missing required field '{f}'")
    start = _parse_date(contract["start_date"])
    end = _parse_date(contract["end_date"])
    if end < start:
        raise ContractValidationError("end_date is before start_date")
    if float(contract["total_price"]) <= 0:
        raise ContractValidationError("total_price must be positive")
    delivs = contract["deliverables"]
    if not isinstance(delivs, list) or not delivs:
        raise ContractValidationError("deliverables must be a non-empty list")
    for i, d in enumerate(delivs):
        for f in REQUIRED_DELIVERABLE_FIELDS:
            if f not in d:
                raise ContractValidationError(f"Deliverable {i + 1} missing field '{f}'")
        flagged = bool(d.get("review"))
        # A flagged obligation (missing/ambiguous SSP, variable consideration,
        # ambiguous timing) is carried but excluded from every calculation, so
        # it is allowed to have a null price and an "unknown" delivery type.
        if d["delivery_type"] not in VALID_DELIVERY_TYPES and not (flagged and d["delivery_type"] == "unknown"):
            raise ContractValidationError(
                f"Deliverable {i + 1} has invalid delivery_type '{d['delivery_type']}'"
            )
        ssp = d["standalone_price_estimate"]
        if flagged:
            if ssp is not None and float(ssp) < 0:
                raise ContractValidationError(f"Deliverable {i + 1} standalone_price_estimate cannot be negative")
        elif ssp is None or float(ssp) <= 0:
            raise ContractValidationError(f"Deliverable {i + 1} standalone_price_estimate must be positive")
        # Optional per-obligation service window (over_time obligations only).
        # May extend past the contract end_date — that's the point (e.g. a
        # 24-month support tail). Only the ordering is constrained.
        if d.get("service_start") is not None or d.get("service_end") is not None:
            ss = _parse_date(d["service_start"]) if d.get("service_start") else start
            se = _parse_date(d["service_end"]) if d.get("service_end") else end
            if se < ss:
                raise ContractValidationError(
                    f"Deliverable {i + 1} service_end is before service_start")
        # Optional point-in-time delivery date (control transfers on completion,
        # e.g. a data migration recognized when it finishes — which can be after
        # the signing date). Must not precede the contract start.
        if d.get("delivery_date") is not None:
            dd = _parse_date(d["delivery_date"])
            if dd < start:
                raise ContractValidationError(
                    f"Deliverable {i + 1} delivery_date is before the contract start_date")
    mod = contract.get("modification")
    if mod is not None:
        for f in ("date", "description", "added_price", "added_deliverable"):
            if f not in mod:
                raise ContractValidationError(f"modification missing field '{f}'")
        mod_d = _parse_date(mod["date"])
        if not (start < mod_d <= end):
            raise ContractValidationError("modification date must fall inside the contract term")
        if not any(d["delivery_type"] == "over_time" for d in delivs):
            raise ContractValidationError("modification requires at least one over_time deliverable")


# ---------------------------------------------------------------------------
# Classification + allocation + scheduling (ASC 606 steps 2-5, simplified)
# ---------------------------------------------------------------------------

def _classify_category(contract: dict) -> str:
    if contract.get("modification"):
        return "modification"
    delivs = contract["deliverables"]
    if contract.get("variable_consideration") or \
       any((d.get("review") or {}).get("kind") == "variable_consideration" for d in delivs):
        return "variable"
    if len(delivs) > 1:
        return "bundled"
    return "subscription" if delivs[0]["delivery_type"] == "over_time" else "one_time"


def _allocate(total_cents: int, ssp_cents: list[int]) -> list[int]:
    """Allocate total price across obligations proportional to standalone prices.

    Rounding residual lands on the last obligation so the allocation always
    sums exactly to the transaction price.
    """
    ssp_total = sum(ssp_cents)
    allocated, running = [], 0
    for i, s in enumerate(ssp_cents):
        if i == len(ssp_cents) - 1:
            share = total_cents - running
        else:
            share = int(round(total_cents * s / ssp_total))
            running += share
        allocated.append(share)
    return allocated


def _apply_modification(contract, obligations, schedule, months):
    """Prospective modification treatment.

    At the modification date, the unrecognized remainder of every over-time
    obligation plus the added consideration form a blended pool, which is
    reallocated across the surviving over-time obligations + the new one
    (weighted by remaining standalone value) and spread evenly over the
    remaining months. Amounts recognized before the modification are frozen.
    """
    mod = contract["modification"]
    mod_m = month_key(mod["date"])
    added_cents = _cents(mod["added_price"])
    remaining_months = [m for m in months if m >= mod_m]

    # Freeze pre-mod entries; pull out the over-time amounts scheduled from
    # the modification month onward (string compare works for YYYY-MM keys).
    kept, removed_by_ob = [], {}
    for e in schedule:
        if e["method"] == "over_time" and e["month"] >= mod_m:
            removed_by_ob[e["obligation_id"]] = removed_by_ob.get(e["obligation_id"], 0) + e["amount_cents"]
        else:
            kept.append(e)

    recognized_before = {
        ob["obligation_id"]: ob["allocated_cents"] - removed_by_ob.get(ob["obligation_id"], 0)
        for ob in obligations if ob["method"] == "over_time"
    }

    new_d = mod["added_deliverable"]
    new_ob = {
        "obligation_id": f"{contract['contract_id']}-OB{len(obligations) + 1}",
        "type": new_d["type"],
        "description": new_d["description"],
        "ssp_cents": _cents(new_d["standalone_price_estimate"]),
        "method": "point_in_time" if new_d["delivery_type"] == "one_time" else "over_time",
        "allocated_cents": 0,
        "added_by_modification": True,
    }
    if new_ob["method"] != "over_time":
        raise ContractValidationError("added_deliverable in a modification must be over_time in this model")
    obligations.append(new_ob)

    # Blended pool = unrecognized remainder + added price, reallocated by
    # remaining standalone value (existing obligations prorated for the
    # portion of the term they have left).
    pool = sum(removed_by_ob.values()) + added_cents
    weight_obs = [ob for ob in obligations if ob["method"] == "over_time"]
    weights = []
    for ob in weight_obs:
        if ob.get("added_by_modification"):
            weights.append(float(ob["ssp_cents"]))
        else:
            weights.append(ob["ssp_cents"] * len(remaining_months) / len(months))
    wsum = sum(weights)

    shares, running = [], 0
    for i, w in enumerate(weights):
        if i == len(weights) - 1:
            share = pool - running
        else:
            share = int(round(pool * w / wsum))
            running += share
        shares.append(share)

    for ob, share in zip(weight_obs, shares):
        for m, amt in zip(remaining_months, _spread(share, len(remaining_months))):
            kept.append({
                "month": m,
                "obligation_id": ob["obligation_id"],
                "amount_cents": amt,
                "method": "over_time",
            })
        if ob.get("added_by_modification"):
            ob["allocated_cents"] = share
        else:
            ob["allocated_cents"] = recognized_before[ob["obligation_id"]] + share

    note = (
        f"Modified on {mod['date']}: {mod['description']}. Added consideration of "
        f"${_dollars(added_cents):,.2f} was combined with ${_dollars(pool - added_cents):,.2f} "
        f"of not-yet-recognized revenue and the blended total of ${_dollars(pool):,.2f} is "
        f"recognized evenly over the remaining {len(remaining_months)} months "
        f"({remaining_months[0]} to {remaining_months[-1]}). Revenue recognized before the "
        f"modification is unchanged (prospective treatment)."
    )
    schedule[:] = kept
    return note


def process_contract(contract: dict) -> dict:
    """Run the full pipeline for one contract. Returns a self-contained dict.

    This function is the single code path for both seed contracts and
    contracts uploaded later through the dashboard.
    """
    validate_contract(contract)
    cid = contract["contract_id"]
    total_cents = _cents(contract["total_price"])
    delivs = contract["deliverables"]

    start_m = month_key(contract["start_date"])
    end_m = month_key(contract["end_date"])

    def _ob_window(d):
        """(start_month, end_month) an over_time obligation recognizes across —
        its own service window when supplied, else the contract term. Lets a
        multi-year support tail spread beyond the base term (e.g. 24-month
        maintenance on a contract signed on a single date)."""
        ss = month_key(d["service_start"]) if d.get("service_start") else start_m
        se = month_key(d["service_end"]) if d.get("service_end") else end_m
        return ss, se

    def _deliver_date(d):
        """Exact date a point_in_time obligation transfers control — its own
        delivery_date when supplied (e.g. a data migration recognized on
        completion), else the contract start date."""
        return d["delivery_date"] if d.get("delivery_date") else contract["start_date"]

    # The recognition horizon must cover the contract term, any over_time
    # obligation whose service window extends past end_date, and any
    # point_in_time obligation delivered after end_date.
    span_end = end_m
    for d in delivs:
        if d.get("review"):
            continue
        if d["delivery_type"] == "over_time":
            _, se = _ob_window(d)
        else:
            se = month_key(_deliver_date(d))
        if _month_index(se) > _month_index(span_end):
            span_end = se
    months = month_span(start_m, span_end)

    # Step 2: identify performance obligations. Step 4: allocate the price.
    # An obligation carrying a "review" marker (missing/ambiguous standalone
    # price, variable consideration, ambiguous timing) is EXCLUDED: it gets an
    # obligation record but no allocation and no schedule, so it contributes
    # nothing to deferred revenue, the forecast, or the close batch until a
    # person resolves it. Nothing is ever allocated to it by a silent guess.
    included_idx = [i for i, d in enumerate(delivs) if not d.get("review")]
    flagged_idx = [i for i, d in enumerate(delivs) if d.get("review")]

    clean_ssp_sum_cents = None   # set on the clean path, for the allocation note
    if flagged_idx:
        # Mixed/flagged contract: do NOT divide the stated total across
        # obligations (part of that total belongs to the excluded items). Each
        # included obligation must carry its own explicitly-stated price.
        alloc_by_idx = {i: _cents(delivs[i]["standalone_price_estimate"]) for i in included_idx}
    else:
        # Clean contract: allocate the transaction price across all obligations
        # in proportion to standalone selling prices (handles bundle discounts).
        ssp_all = [_cents(d["standalone_price_estimate"]) for d in delivs]
        clean_ssp_sum_cents = sum(ssp_all)
        alloc_list = _allocate(total_cents, ssp_all)
        alloc_by_idx = {i: alloc_list[i] for i in range(len(delivs))}

    obligations, schedule = [], []
    pit_dates: dict[str, str] = {}   # obligation_id -> exact point-in-time delivery date
    for i, d in enumerate(delivs):
        ob_id = f"{cid}-OB{i + 1}"
        if d.get("review"):
            rv = d["review"]
            obligations.append({
                "obligation_id": ob_id, "type": d["type"], "description": d["description"],
                "ssp_cents": None, "allocated_cents": None, "method": "pending_review",
                "excluded": True, "review_kind": rv.get("kind"), "review_reason": rv.get("reason"),
                "review_excluded_cents": _cents(rv["excluded_amount"]) if rv.get("excluded_amount") is not None else None,
            })
            continue
        method = "point_in_time" if d["delivery_type"] == "one_time" else "over_time"
        alloc = alloc_by_idx[i]
        obligations.append({
            "obligation_id": ob_id, "type": d["type"], "description": d["description"],
            "ssp_cents": _cents(d["standalone_price_estimate"]), "allocated_cents": alloc, "method": method,
        })
        # Step 5: recognition schedule (included obligations only).
        if method == "point_in_time":
            deliver_dt = _deliver_date(d)
            pit_dates[ob_id] = deliver_dt
            schedule.append({"month": month_key(deliver_dt), "obligation_id": ob_id,
                             "amount_cents": alloc, "method": "point_in_time"})
        else:
            ob_months = month_span(*_ob_window(d))
            for m, amt in zip(ob_months, _spread(alloc, len(ob_months))):
                schedule.append({"month": m, "obligation_id": ob_id,
                                 "amount_cents": amt, "method": "over_time"})

    # Recognizable (allocable) consideration — only the included obligations.
    recognized_total_cents = sum(alloc_by_idx.get(i, 0) for i in included_idx)
    # Amount deliberately excluded from every total pending human review. The
    # invariant recognized + excluded == total must always hold, including when
    # a multi-obligation bundle is only PARTIALLY resolved (some obligations
    # priced, others still flagged) — otherwise the still-flagged portion would
    # silently vanish from both the recognized totals and the excluded banner.
    excluded_cents = max(0, total_cents - recognized_total_cents)

    modification_note = None
    if contract.get("modification"):
        modification_note = _apply_modification(contract, obligations, schedule, months)

    # Variable consideration: usage fees are billed and recognized as incurred
    # (they never sit in deferred revenue in this simplified model).
    variable_note = None
    if contract.get("variable_consideration"):
        vc = contract["variable_consideration"]
        for m in sorted(vc.get("monthly_actuals", {})):
            schedule.append({
                "month": m, "obligation_id": f"{cid}-USAGE",
                "amount_cents": _cents(vc["monthly_actuals"][m]), "method": "usage",
            })
        variable_note = (
            f"Variable consideration: {vc.get('description', 'usage-based fees')}. "
            "Usage fees are billed monthly as incurred and recognized in the month of usage "
            "— they are excluded from deferred revenue and from the known-revenue (RPO) "
            "forecast because future usage is not contractually fixed."
        )

    schedule.sort(key=lambda e: (e["month"], e["obligation_id"]))

    # Journal entries -------------------------------------------------------
    # Cash received is the recognizable (allocable) consideration only —
    # excluded/flagged amounts never post until resolved.
    journal = []
    if recognized_total_cents > 0:
        journal.append({
            "date": contract["start_date"], "month": start_m, "entry_type": "cash_receipt",
            "debit_account": "Cash", "credit_account": "Deferred Revenue",
            "amount_cents": recognized_total_cents,
            "memo": f"{cid} — cash received on contract signing ({contract['customer']})",
        })
    if contract.get("modification"):
        mod = contract["modification"]
        journal.append({
            "date": mod["date"], "month": month_key(mod["date"]), "entry_type": "cash_receipt",
            "debit_account": "Cash", "credit_account": "Deferred Revenue",
            "amount_cents": _cents(mod["added_price"]),
            "memo": f"{cid} — additional cash for contract modification",
        })
    for e in schedule:
        if e["method"] == "usage":
            journal.append({
                "date": month_end(e["month"]), "month": e["month"], "entry_type": "usage_billing",
                "debit_account": "Cash", "credit_account": "Revenue",
                "amount_cents": e["amount_cents"],
                "memo": f"{cid} — usage fees billed and recognized for {e['month']}",
                "obligation_id": e["obligation_id"],
            })
        else:
            entry_date = pit_dates.get(e["obligation_id"], contract["start_date"]) \
                if e["method"] == "point_in_time" else month_end(e["month"])
            journal.append({
                "date": entry_date, "month": e["month"], "entry_type": "recognition",
                "debit_account": "Deferred Revenue", "credit_account": "Revenue",
                "amount_cents": e["amount_cents"],
                "memo": f"{cid} — revenue recognized for {e['obligation_id']} ({e['month']})",
                "obligation_id": e["obligation_id"],
            })
    journal.sort(key=lambda j: (j["date"], j["entry_type"] != "cash_receipt"))

    # Deferred revenue table -------------------------------------------------
    cash_in = {start_m: recognized_total_cents} if recognized_total_cents > 0 else {}
    if contract.get("modification"):
        mm = month_key(contract["modification"]["date"])
        cash_in[mm] = cash_in.get(mm, 0) + _cents(contract["modification"]["added_price"])
    recognized_by_month: dict[str, int] = {}
    for e in schedule:
        if e["method"] != "usage":
            recognized_by_month[e["month"]] = recognized_by_month.get(e["month"], 0) + e["amount_cents"]

    dr_table, balance = [], 0
    for m in months:
        begin = balance
        received = cash_in.get(m, 0)
        recognized = recognized_by_month.get(m, 0)
        balance = begin + received - recognized
        dr_table.append({
            "month": m,
            "beginning_balance": _dollars(begin),
            "cash_received": _dollars(received),
            "revenue_recognized": _dollars(recognized),
            "ending_balance": _dollars(balance),
        })

    # A prospective modification adds consideration that flows through the
    # schedule, journal, and deferred-revenue tables. Reflect it in the
    # displayed total and recognized amounts too, so the contract header ties
    # to what actually recognizes ($90k here, not the pre-mod $72k) instead of
    # showing a total smaller than the revenue it books.
    mod_added_cents = _cents(contract["modification"]["added_price"]) if contract.get("modification") else 0

    # Bundle discount/premium transparency: when a multi-obligation contract's
    # standalone selling prices don't sum to the contract price, Step 4 spreads
    # the difference proportionally. Surface it so the allocation is never a
    # silent scaling (and a fat-finger price is visible rather than absorbed).
    allocation_note = None
    if clean_ssp_sum_cents is not None and len(delivs) >= 2 and clean_ssp_sum_cents != total_cents:
        diff = clean_ssp_sum_cents - total_cents
        pct = abs(diff) / total_cents * 100
        kind = "discount" if diff > 0 else "premium"
        allocation_note = (
            f"Standalone selling prices sum to ${_dollars(clean_ssp_sum_cents):,.2f} vs. the "
            f"${_dollars(total_cents):,.2f} contract price — a ${_dollars(abs(diff)):,.2f} "
            f"({pct:.0f}%) bundle {kind}, allocated proportionally across all obligations "
            f"(ASC 606 Step 4). No obligation recognizes more than its allocated share."
        )

    # Displayed term end: when an obligation's service window (or a point-in-time
    # delivery date) recognizes past the contract's stated end month, show the
    # term through the actual recognition horizon so the term can't read as a
    # single day while 12 months of revenue spread beneath it. Contracts that
    # don't extend keep their exact stated end date (day preserved).
    display_end_date = contract["end_date"]
    if _month_index(months[-1]) > _month_index(end_m):
        display_end_date = month_end(months[-1])

    return {
        "contract_id": cid,
        "customer": contract["customer"],
        "start_date": contract["start_date"],
        "end_date": display_end_date,
        "total_price": _dollars(total_cents + mod_added_cents),
        "recognized_amount": _dollars(recognized_total_cents + mod_added_cents),
        "excluded_amount": _dollars(excluded_cents),
        "category": _classify_category(contract),
        "term_months": len(months),
        "obligations": [{
            "obligation_id": ob["obligation_id"],
            "type": ob["type"],
            "description": ob["description"],
            "standalone_price_estimate": None if ob["ssp_cents"] is None else _dollars(ob["ssp_cents"]),
            "allocated_price": None if ob["allocated_cents"] is None else _dollars(ob["allocated_cents"]),
            "method": ob["method"],
            "added_by_modification": bool(ob.get("added_by_modification")),
            "excluded": bool(ob.get("excluded")),
            "review_kind": ob.get("review_kind"),
            "review_reason": ob.get("review_reason"),
            "review_excluded_amount": None if ob.get("review_excluded_cents") is None
                                      else _dollars(ob["review_excluded_cents"]),
        } for ob in obligations],
        "schedule": [{
            "month": e["month"], "obligation_id": e["obligation_id"],
            "amount": _dollars(e["amount_cents"]), "method": e["method"],
        } for e in schedule],
        "journal_entries": [{
            "date": j["date"], "month": j["month"], "entry_type": j["entry_type"],
            "debit_account": j["debit_account"], "credit_account": j["credit_account"],
            "amount": _dollars(j["amount_cents"]), "memo": j["memo"],
            "obligation_id": j.get("obligation_id"),
        } for j in journal],
        "deferred_revenue": dr_table,
        "modification_note": modification_note,
        "allocation_note": allocation_note,
        "variable_note": variable_note,
        "rationales": {},  # filled by engine.explain
    }


# ---------------------------------------------------------------------------
# Aggregations — these operate on already-processed contracts, so adding a
# new contract never requires reprocessing the existing ones (the per-contract
# results are cached and the roll-ups below just sum the cached series).
# ---------------------------------------------------------------------------

def _all_months(processed_list) -> list[str]:
    keys = set()
    for p in processed_list:
        for row in p["deferred_revenue"]:
            keys.add(row["month"])
        for e in p["schedule"]:
            keys.add(e["month"])
    if not keys:
        return []
    return month_span(min(keys), max(keys))


def excluded_pending(processed_list) -> dict:
    """Amounts deliberately excluded from the deferred-revenue, forecast, and
    close totals because an obligation is flagged for human review. Flagged
    obligations never produce schedule/journal/DR rows, so they are already
    absent from every other aggregate; this just surfaces how much is being
    held back and why, so the incomplete totals read as intentional."""
    items, total = [], 0.0
    for p in processed_list:
        amt = p.get("excluded_amount", 0) or 0
        flagged = [o for o in p["obligations"] if o.get("excluded")]
        if flagged:
            total += amt
            items.append({
                "contract_id": p["contract_id"],
                "customer": p["customer"],
                "excluded_amount": round(amt, 2),
                "obligations": [{"obligation_id": o["obligation_id"], "type": o["type"],
                                 "reason": o.get("review_reason")} for o in flagged],
            })
    return {"total_excluded": round(total, 2), "items": items}


def aggregate_deferred_revenue(processed_list) -> list[dict]:
    """Total deferred revenue liability by month across all contracts."""
    months = _all_months(processed_list)
    out = []
    for m in months:
        total = 0.0
        for p in processed_list:
            rows = p["deferred_revenue"]
            if not rows or m < rows[0]["month"]:
                continue  # contract not signed yet
            if m > rows[-1]["month"]:
                total += rows[-1]["ending_balance"]  # 0 once fully recognized
                continue
            for row in rows:
                if row["month"] == m:
                    total += row["ending_balance"]
                    break
        out.append({"month": m, "deferred_revenue": round(total, 2)})
    return out


def aggregate_recognized_by_method(processed_list) -> list[dict]:
    """Revenue recognized per month, split point-in-time vs over-time vs usage."""
    months = _all_months(processed_list)
    buckets = {m: {"point_in_time": 0.0, "over_time": 0.0, "usage": 0.0} for m in months}
    for p in processed_list:
        for e in p["schedule"]:
            buckets[e["month"]][e["method"]] += e["amount"]
    return [{
        "month": m,
        "point_in_time": round(buckets[m]["point_in_time"], 2),
        "over_time": round(buckets[m]["over_time"], 2),
        "usage": round(buckets[m]["usage"], 2),
    } for m in months]


def month_end_close_batch(processed_list, close_month: str) -> dict:
    """Generate the full batch of entries that should post in a given month,
    plus control flags for contracts where recognition looks wrong.

    This is the automation story: nobody looks up per-contract amounts by
    hand — every active contract's entry for the month is generated from its
    stored schedule, and the control check flags anything that should have
    recognized but didn't (or should be done but still carries a balance).
    """
    entries, flags = [], []
    for p in processed_list:
        for j in p["journal_entries"]:
            if j["month"] == close_month and j["entry_type"] in ("recognition", "usage_billing"):
                entries.append({**j, "contract_id": p["contract_id"], "customer": p["customer"]})

        start_m, end_m = month_key(p["start_date"]), month_key(p["end_date"])
        final_balance = p["deferred_revenue"][-1]["ending_balance"] if p["deferred_revenue"] else 0.0

        # Control 1: contract term is over but deferred revenue remains.
        if close_month > end_m and final_balance > 0.005:
            flags.append({
                "contract_id": p["contract_id"], "severity": "error",
                "message": (
                    f"Contract ended {p['end_date']} but still carries a deferred revenue "
                    f"balance of ${final_balance:,.2f} — recognition appears incomplete."
                ),
            })

        # Control 2: contract is active with over-time obligations but no
        # recognition entry was generated for this month.
        has_over_time = any(ob["method"] == "over_time" for ob in p["obligations"])
        if has_over_time and start_m <= close_month <= end_m:
            if not any(e["contract_id"] == p["contract_id"] and e["entry_type"] == "recognition"
                       for e in entries):
                flags.append({
                    "contract_id": p["contract_id"], "severity": "error",
                    "message": (
                        f"Active over-time contract has no recognition entry for {close_month} — "
                        "the monthly entry may have been missed."
                    ),
                })

    entries.sort(key=lambda e: (e["contract_id"], e["obligation_id"] or ""))
    return {
        "close_month": close_month,
        "entries": entries,
        "entry_count": len(entries),
        "total_recognized": round(sum(e["amount"] for e in entries if e["entry_type"] == "recognition"), 2),
        "total_usage_billed": round(sum(e["amount"] for e in entries if e["entry_type"] == "usage_billing"), 2),
        "flags": flags,
    }


def rpo_forecast(processed_list, from_month: str, num_months: int = 12) -> dict:
    """Known contracted revenue by future month (Remaining Performance Obligations).

    Derived entirely from existing recognition schedules — zero new
    assumptions. Explicitly EXCLUDES new sales, renewals, pipeline, and
    variable/usage revenue (future usage is not contractually fixed).
    """
    window = [add_months(from_month, i) for i in range(num_months)]
    totals = {m: 0.0 for m in window}
    by_contract: dict[str, dict] = {}
    for p in processed_list:
        contrib = {m: 0.0 for m in window}
        for e in p["schedule"]:
            if e["method"] == "usage":
                continue
            if e["month"] in totals:
                totals[e["month"]] += e["amount"]
                contrib[e["month"]] += e["amount"]
        if any(v > 0 for v in contrib.values()):
            by_contract[p["contract_id"]] = {
                "customer": p["customer"],
                "monthly": {m: round(v, 2) for m, v in contrib.items() if v > 0},
                "total": round(sum(contrib.values()), 2),
            }
    return {
        "from_month": from_month,
        "months": window,
        "monthly_totals": [{"month": m, "known_revenue": round(totals[m], 2)} for m in window],
        "total_known_revenue": round(sum(totals.values()), 2),
        "by_contract": by_contract,
        "disclaimer": (
            "Known revenue from existing contracts only (Remaining Performance Obligations). "
            "Excludes new sales, renewals, pipeline, and variable/usage fees. A total revenue "
            "forecast would require separate assumptions (e.g. new bookings x close rate) and "
            "must not be blended into this number."
        ),
    }
