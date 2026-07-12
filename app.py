"""ASC 606 revenue recognition engine — Flask backend.

Serves the JSON API + the static dashboard. Contracts are persisted to
SQLite (config-driven via DATABASE_URL, so the storage can be swapped for
hosted Postgres on a platform where local disk is not durable).

Incremental design: each contract is processed exactly once (at seed time
or at upload time) and its full result — schedules, journal entries,
deferred revenue table, rationales — is cached in the database. Aggregate
views (deferred revenue series, close batch, RPO forecast) are computed by
summing the cached per-contract series, so adding contract #21 never
reprocesses contracts #1-20.

Multi-user: uploaded contracts are scoped to a visitor cookie. Every
visitor sees the 20 seed contracts plus only their own uploads.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from flask import Flask, Response, g, jsonify, request, send_from_directory

from engine.core import (
    ContractValidationError,
    aggregate_deferred_revenue,
    aggregate_recognized_by_method,
    month_end_close_batch,
    process_contract,
    rpo_forecast,
)
from engine.explain import add_rationales
from engine.pdf_contract import parse_contract_pdf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Config-driven database location. SQLite by default; the URL indirection is
# the seam where a hosted Postgres (Neon/Supabase) would slot in if the
# deploy target's disk isn't persistent.
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'revrec.db')}")

if not DATABASE_URL.startswith("sqlite:///"):
    raise RuntimeError(
        "This demo ships with SQLite support. To use another database, swap the "
        "connection layer in app.py (the DATABASE_URL seam is here for that purpose)."
    )
DB_PATH = DATABASE_URL[len("sqlite:///"):]

app = Flask(__name__, static_folder="static")

VISITOR_COOKIE = "revrec_visitor"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contracts (
            owner        TEXT NOT NULL,      -- 'seed' or a visitor id
            contract_id  TEXT NOT NULL,
            raw_json     TEXT NOT NULL,      -- the contract as uploaded
            processed_json TEXT NOT NULL,    -- cached engine output
            created_at   TEXT NOT NULL,
            PRIMARY KEY (owner, contract_id)
        )
    """)
    seeded = conn.execute("SELECT COUNT(*) FROM contracts WHERE owner = 'seed'").fetchone()[0]
    if seeded == 0:
        with open(os.path.join(BASE_DIR, "data", "seed_contracts.json")) as f:
            seeds = json.load(f)
        now = datetime.now(timezone.utc).isoformat()
        for c in seeds:
            processed = add_rationales(process_contract(c))
            conn.execute(
                "INSERT INTO contracts VALUES (?, ?, ?, ?, ?)",
                ("seed", c["contract_id"], json.dumps(c), json.dumps(processed), now),
            )
        conn.commit()
        print(f"Seeded {len(seeds)} contracts into {DB_PATH}")
    conn.close()


# ---------------------------------------------------------------------------
# Visitor scoping
# ---------------------------------------------------------------------------

def visitor_id() -> str:
    return request.cookies.get(VISITOR_COOKIE) or g.get("new_visitor_id") or ""


@app.before_request
def ensure_visitor():
    if not request.cookies.get(VISITOR_COOKIE):
        g.new_visitor_id = uuid.uuid4().hex


@app.after_request
def set_visitor_cookie(resp):
    if g.get("new_visitor_id"):
        resp.set_cookie(VISITOR_COOKIE, g.new_visitor_id, max_age=60 * 60 * 24 * 365,
                        httponly=True, samesite="Lax")
    return resp


def load_scope() -> list[dict]:
    """All processed contracts visible to this visitor: seeds + own uploads."""
    db = get_db()
    rows = db.execute(
        "SELECT processed_json FROM contracts WHERE owner IN ('seed', ?) "
        "ORDER BY owner != 'seed', contract_id",
        (visitor_id(),),
    ).fetchall()
    return [json.loads(r["processed_json"]) for r in rows]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/contracts")
def list_contracts():
    return jsonify({"contracts": load_scope()})


@app.route("/api/contracts/<contract_id>")
def get_contract(contract_id):
    for p in load_scope():
        if p["contract_id"] == contract_id:
            return jsonify(p)
    return jsonify({"error": f"Contract {contract_id} not found"}), 404


@app.route("/api/contracts", methods=["POST"])
def upload_contract():
    """Upload a new contract as a PDF (order-form template) or JSON.

    The single shared pipeline runs once for the new contract; aggregates
    pick it up automatically because they sum the cached per-contract
    results — no reprocessing of existing contracts.
    """
    try:
        if "file" in request.files:
            f = request.files["file"]
            name = (f.filename or "").lower()
            if name.endswith(".pdf"):
                payload = parse_contract_pdf(f)
            else:
                payload = json.load(f)
        else:
            payload = request.get_json(force=True)
    except ContractValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        return jsonify({"error": "Upload a contract PDF (see the sample order form) or a JSON file"}), 400
    if isinstance(payload, list):
        if len(payload) != 1:
            return jsonify({"error": "Upload one contract at a time"}), 400
        payload = payload[0]

    try:
        processed = process_contract(payload)   # same code path as the seeds
    except ContractValidationError as e:
        return jsonify({"error": str(e)}), 400

    cid = processed["contract_id"]
    existing = {p["contract_id"] for p in load_scope()}
    if cid in existing:
        return jsonify({"error": f"Contract id '{cid}' already exists in your view"}), 409

    add_rationales(processed)

    db = get_db()
    db.execute(
        "INSERT INTO contracts VALUES (?, ?, ?, ?, ?)",
        (visitor_id(), cid, json.dumps(payload), json.dumps(processed),
         datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    return jsonify(processed), 201


@app.route("/api/aggregates")
def aggregates():
    scope = load_scope()
    return jsonify({
        "deferred_revenue": aggregate_deferred_revenue(scope),
        "recognized_by_method": aggregate_recognized_by_method(scope),
        "contract_count": len(scope),
    })


@app.route("/api/close-batch")
def close_batch():
    month = request.args.get("month") or datetime.now(timezone.utc).strftime("%Y-%m")
    if len(month) != 7 or month[4] != "-":
        return jsonify({"error": "month must be YYYY-MM"}), 400
    return jsonify(month_end_close_batch(load_scope(), month))


# Demo G/L mapping for the SAP-style export. In a real implementation this
# would come from the company's chart of accounts / posting configuration.
GL_ACCOUNTS = {
    "Cash": ("1000000", "Cash and Cash Equivalents"),
    "Deferred Revenue": ("2300000", "Deferred Revenue"),
    "Revenue": ("4000000", "Subscription Revenue"),
    "Revenue (usage)": ("4100000", "Usage Fee Revenue"),
}
COST_CENTER = "CC1000"
PROFIT_CENTER = "PC1000"


@app.route("/api/close-batch.csv")
def close_batch_csv():
    """Month-end close batch as an SAP-upload-style CSV.

    One document per journal entry, two line items each (debit + credit),
    with posting keys 40/50, G/L accounts, document header text, cost
    center, and profit center — the shape a journal-entry upload template
    (e.g. for SAP) expects.
    """
    month = request.args.get("month") or datetime.now(timezone.utc).strftime("%Y-%m")
    if len(month) != 7 or month[4] != "-":
        return jsonify({"error": "month must be YYYY-MM"}), 400
    batch = month_end_close_batch(load_scope(), month)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Posting Date", "Document Header Text", "Posting Key", "Debit/Credit",
        "G/L Account", "Account Name", "Amount", "Currency", "Line Item Text",
        "Cost Center", "Profit Center", "Contract ID",
    ])

    def account_for(name, entry):
        if name == "Revenue" and entry["entry_type"] == "usage_billing":
            return GL_ACCOUNTS["Revenue (usage)"]
        return GL_ACCOUNTS[name]

    # The batch is one balanced posting: every debit row is immediately
    # followed by its matching credit row, and total debits == total credits.
    header = f"RevRec close {month}"
    for e in batch["entries"]:
        debit_gl, debit_name = account_for(e["debit_account"], e)
        credit_gl, credit_name = account_for(e["credit_account"], e)
        amount = f"{e['amount']:.2f}"
        w.writerow([e["date"], header, "40", "Debit", debit_gl, debit_name,
                    amount, "USD", e["memo"], COST_CENTER, PROFIT_CENTER, e["contract_id"]])
        w.writerow([e["date"], header, "50", "Credit", credit_gl, credit_name,
                    amount, "USD", e["memo"], COST_CENTER, PROFIT_CENTER, e["contract_id"]])

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=close_batch_{month}_sap.csv"},
    )


@app.route("/api/forecast")
def forecast():
    from_month = request.args.get("from") or datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        num = min(max(int(request.args.get("months", 12)), 1), 36)
    except ValueError:
        return jsonify({"error": "months must be an integer"}), 400
    return jsonify(rpo_forecast(load_scope(), from_month, num))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)), debug=False)
