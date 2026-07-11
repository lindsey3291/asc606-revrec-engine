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

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from flask import Flask, g, jsonify, request, send_from_directory

from engine.core import (
    ContractValidationError,
    aggregate_deferred_revenue,
    aggregate_recognized_by_method,
    month_end_close_batch,
    process_contract,
    rpo_forecast,
)
from engine.explain import add_rationales

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
    """Upload a new contract (same JSON structure as the seed contracts).

    The single shared pipeline runs once for the new contract; aggregates
    pick it up automatically because they sum the cached per-contract
    results — no reprocessing of existing contracts.
    """
    try:
        payload = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Body must be valid JSON"}), 400
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
