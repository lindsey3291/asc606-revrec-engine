"""Generate the offline report bundle from the seed contracts.

Writes to output/:
  report.json            — full processed results for every contract
  journal_entries.csv    — every journal entry across all contracts
  deferred_revenue.csv   — per-contract monthly deferred revenue tables
  aggregate_deferred.csv — total deferred revenue liability by month
  summary.md             — readable markdown summary

Run from the project root:  python3 scripts/generate_report.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.core import (
    aggregate_deferred_revenue,
    aggregate_recognized_by_method,
    month_end_close_batch,
    process_contract,
    rpo_forecast,
)
from engine.explain import add_rationales

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "output")


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(BASE, "data", "seed_contracts.json")) as f:
        contracts = json.load(f)

    processed = [add_rationales(process_contract(c)) for c in contracts]
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    dr_series = aggregate_deferred_revenue(processed)
    by_method = aggregate_recognized_by_method(processed)
    batch = month_end_close_batch(processed, current_month)
    forecast = rpo_forecast(processed, current_month, 12)

    # --- report.json --------------------------------------------------------
    with open(os.path.join(OUT, "report.json"), "w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "contracts": processed,
            "aggregate_deferred_revenue": dr_series,
            "recognized_by_method": by_method,
            "close_batch_current_month": batch,
            "known_revenue_forecast": forecast,
        }, f, indent=2)

    # --- journal_entries.csv ------------------------------------------------
    with open(os.path.join(OUT, "journal_entries.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["contract_id", "customer", "date", "entry_type",
                    "debit_account", "credit_account", "amount", "memo"])
        for p in processed:
            for j in p["journal_entries"]:
                w.writerow([p["contract_id"], p["customer"], j["date"], j["entry_type"],
                            j["debit_account"], j["credit_account"], f"{j['amount']:.2f}", j["memo"]])

    # --- deferred_revenue.csv -----------------------------------------------
    with open(os.path.join(OUT, "deferred_revenue.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["contract_id", "month", "beginning_balance", "cash_received",
                    "revenue_recognized", "ending_balance"])
        for p in processed:
            for r in p["deferred_revenue"]:
                w.writerow([p["contract_id"], r["month"], f"{r['beginning_balance']:.2f}",
                            f"{r['cash_received']:.2f}", f"{r['revenue_recognized']:.2f}",
                            f"{r['ending_balance']:.2f}"])

    # --- aggregate_deferred.csv ---------------------------------------------
    with open(os.path.join(OUT, "aggregate_deferred.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["month", "total_deferred_revenue"])
        for r in dr_series:
            w.writerow([r["month"], f"{r['deferred_revenue']:.2f}"])

    # --- summary.md ---------------------------------------------------------
    lines = [
        "# ASC 606 Revenue Recognition — Summary Report",
        f"\nGenerated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"{len(processed)} contracts\n",
    ]
    for p in processed:
        lines.append(f"\n## {p['contract_id']} — {p['customer']}  `{p['category']}`")
        lines.append(f"Term {p['start_date']} → {p['end_date']} · total price ${p['total_price']:,.2f}\n")
        lines.append("| Obligation | Type | Method | SSP | Allocated |")
        lines.append("|---|---|---|---:|---:|")
        for ob in p["obligations"]:
            lines.append(
                f"| {ob['obligation_id']} | {ob['type']} | {ob['method']} | "
                f"${ob['standalone_price_estimate']:,.2f} | ${ob['allocated_price']:,.2f} |")
        for ob in p["obligations"]:
            r = p["rationales"].get(ob["obligation_id"], {})
            lines.append(f"\n> **{ob['obligation_id']}** ({r.get('source', '?')}): {r.get('text', '')}")
        if p.get("modification_note"):
            lines.append(f"\n> ⚠️ {p['modification_note']}")
        if p.get("variable_note"):
            lines.append(f"\n> ℹ️ {p['variable_note']}")

    lines.append(f"\n\n## Month-end close batch — {current_month}")
    lines.append(f"\n{batch['entry_count']} entries · recognition total "
                 f"${batch['total_recognized']:,.2f} · usage billed ${batch['total_usage_billed']:,.2f}")
    if batch["flags"]:
        lines.append("\n**Control flags:**")
        for fl in batch["flags"]:
            lines.append(f"- [{fl['severity']}] {fl['contract_id']}: {fl['message']}")
    else:
        lines.append("\nNo control flags — all active contracts recognized on schedule.")

    lines.append(f"\n\n## Known revenue forecast (RPO) — next 12 months from {current_month}")
    lines.append(f"\n> {forecast['disclaimer']}\n")
    lines.append("| Month | Known revenue |")
    lines.append("|---|---:|")
    for r in forecast["monthly_totals"]:
        lines.append(f"| {r['month']} | ${r['known_revenue']:,.2f} |")
    lines.append(f"| **Total** | **${forecast['total_known_revenue']:,.2f}** |")

    with open(os.path.join(OUT, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Wrote report bundle for {len(processed)} contracts to {OUT}/")
    print(f"Close batch {current_month}: {batch['entry_count']} entries, "
          f"${batch['total_recognized']:,.2f} recognized, {len(batch['flags'])} flags")
    print(f"12-month known revenue (RPO): ${forecast['total_known_revenue']:,.2f}")


if __name__ == "__main__":
    main()
