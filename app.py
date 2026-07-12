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
visitor sees the 10 seed contracts plus only their own uploads.
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
    excluded_pending,
    month_end_close_batch,
    process_contract,
    rpo_forecast,
)
from engine.explain import add_rationales
from engine.extract import extract_contract
from engine.rag import get_doc

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
# Bump when engine/explain logic changes the seeds' processed output, to force
# a one-time reprocess of the stored seeds on the next boot.
SEED_VERSION = "2"


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
    import hashlib
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
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")

    seed_path = os.path.join(BASE_DIR, "data", "seed_contracts.json")
    seed_bytes = open(seed_path, "rb").read()
    seeds = json.loads(seed_bytes)
    # Marker combines the seed file's content hash with a version bumped when
    # the PROCESSING logic changes (so an engine/explain change reprocesses the
    # stored seeds even though the data file is unchanged).
    seed_marker = hashlib.sha1(seed_bytes).hexdigest() + ":" + SEED_VERSION

    stored_row = conn.execute("SELECT value FROM meta WHERE key='seed_hash'").fetchone()
    stored_marker = stored_row[0] if stored_row else None
    have_ids = {r[0] for r in conn.execute("SELECT contract_id FROM contracts WHERE owner='seed'").fetchall()}
    want_ids = {c["contract_id"] for c in seeds}

    # Re-seed when the seed content, its ID set, or the processing version
    # changes. Visitor uploads and overrides (owner != 'seed') are untouched.
    if stored_marker != seed_marker or have_ids != want_ids:
        conn.execute("DELETE FROM contracts WHERE owner='seed'")
        now = datetime.now(timezone.utc).isoformat()
        for c in seeds:
            processed = add_rationales(process_contract(c))
            conn.execute("INSERT INTO contracts VALUES (?, ?, ?, ?, ?)",
                         ("seed", c["contract_id"], json.dumps(c), json.dumps(processed), now))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('seed_hash', ?)", (seed_marker,))
        conn.commit()
        print(f"Seeded {len(seeds)} contracts into {DB_PATH}")

    # Reprocess any rows (visitor uploads, or seeds after an engine change)
    # whose cached output predates the current schema, so every row carries
    # the latest fields (excluded_amount, needs_review, confidence, ...).
    migrated = 0
    for owner, cid, raw, proc in conn.execute(
            "SELECT owner, contract_id, raw_json, processed_json FROM contracts").fetchall():
        if "excluded_amount" not in json.loads(proc):
            processed = add_rationales(process_contract(json.loads(raw)))
            conn.execute("UPDATE contracts SET processed_json=? WHERE owner=? AND contract_id=?",
                         (json.dumps(processed), owner, cid))
            migrated += 1
    if migrated:
        conn.commit()
        print(f"Reprocessed {migrated} contracts to the current schema")
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


def visible_rows() -> list[sqlite3.Row]:
    """Rows visible to this visitor: the 10 seeds plus the visitor's own rows,
    where a visitor row (an upload, or a RESOLVED OVERRIDE of a seed) with the
    same contract_id supersedes the seed. This is how a visitor can resolve a
    shared flagged seed without changing anyone else's view."""
    db = get_db()
    rows = db.execute(
        "SELECT owner, contract_id, raw_json, processed_json FROM contracts "
        "WHERE owner IN ('seed', ?) ORDER BY contract_id, owner = 'seed'",
        (visitor_id(),),
    ).fetchall()
    seen = {}
    for r in rows:                       # visitor row sorts before the seed
        seen.setdefault(r["contract_id"], r)
    return list(seen.values())


def load_scope() -> list[dict]:
    """All processed contracts visible to this visitor (deduped)."""
    return [json.loads(r["processed_json"]) for r in visible_rows()]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/contracts")
def list_contracts():
    """List contracts in scope. `deletable` marks the visitor's own rows
    (uploads or resolved overrides); `is_override` marks a resolved copy of a
    seed (deleting it reverts to the shared flagged seed)."""
    seed_ids = {r["contract_id"] for r in get_db().execute(
        "SELECT contract_id FROM contracts WHERE owner='seed'").fetchall()}
    out = []
    for r in visible_rows():
        p = json.loads(r["processed_json"])
        own = r["owner"] != "seed"
        p["deletable"] = own
        p["is_override"] = own and r["contract_id"] in seed_ids
        out.append(p)
    return jsonify({"contracts": out})


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
                # Auto-detects structured vs prose and routes accordingly;
                # both produce the same internal contract schema.
                payload = extract_contract(f, name)
            else:
                payload = json.load(f)
        else:
            payload = request.get_json(force=True)
    except ContractValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        return jsonify({"error": "Upload a contract PDF (structured order form or prose) or a JSON file"}), 400
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


@app.route("/api/contracts/<contract_id>", methods=["DELETE"])
def delete_contract(contract_id):
    """Delete one of THIS visitor's own rows. An uploaded contract is removed
    outright; a resolved override of a seed is removed so the view reverts to
    the shared (flagged) seed. Pure seed contracts can't be deleted. Every
    aggregate recomputes on the next fetch, so the change is reflected
    everywhere with no other bookkeeping.
    """
    db = get_db()
    cur = db.execute("DELETE FROM contracts WHERE owner=? AND contract_id=?",
                     (visitor_id(), contract_id))
    db.commit()
    if cur.rowcount == 0:
        is_seed = db.execute("SELECT 1 FROM contracts WHERE owner='seed' AND contract_id=?",
                             (contract_id,)).fetchone()
        if is_seed:
            return jsonify({"error": "The sample contracts are shared and can't be deleted "
                                     "(you can resolve a flagged one instead)."}), 403
        return jsonify({"error": f"Contract '{contract_id}' not found among your uploads."}), 404
    reverted = db.execute("SELECT 1 FROM contracts WHERE owner='seed' AND contract_id=?",
                          (contract_id,)).fetchone() is not None
    return jsonify({"deleted": contract_id, "reverted": reverted}), 200


@app.route("/api/contracts/<contract_id>/resolve", methods=["POST"])
def resolve_contract(contract_id):
    """Resolve a flagged contract by supplying the missing standalone prices /
    delivery types for its excluded obligations. Re-processes the contract so
    the now-priced obligations flow into the aggregates normally. Own uploads
    only — the shared sample contracts intentionally stay flagged as demos.

    Body: {"resolutions": {"<OB number>": {"standalone_price": N, "delivery_type": "one_time"|"over_time"}, ...}}

    Works on the visitor's own uploads AND on the shared seed contracts:
    resolving a seed writes a visitor-scoped OVERRIDE (a copy owned by the
    visitor) so their fix flows into their totals without changing the shared
    seed for other visitors. Deleting the override reverts to the seed.
    """
    db = get_db()
    # Resolve against what the visitor currently sees: their own row if one
    # exists, otherwise the shared seed.
    row = db.execute("SELECT raw_json FROM contracts WHERE owner=? AND contract_id=?",
                     (visitor_id(), contract_id)).fetchone()
    if not row:
        row = db.execute("SELECT raw_json FROM contracts WHERE owner='seed' AND contract_id=?",
                         (contract_id,)).fetchone()
    if not row:
        return jsonify({"error": f"Contract '{contract_id}' not found."}), 404

    raw = json.loads(row["raw_json"])
    resolutions = (request.get_json(force=True, silent=True) or {}).get("resolutions") or {}
    for i, d in enumerate(raw["deliverables"]):
        key = str(i + 1)
        if key in resolutions and d.get("review"):
            r = resolutions[key]
            try:
                price = float(r.get("standalone_price"))
            except (TypeError, ValueError):
                price = 0
            dtype = r.get("delivery_type")
            if price <= 0 or dtype not in ("one_time", "over_time"):
                return jsonify({"error": f"Obligation {key}: provide a positive standalone "
                                         "price and a delivery type (one_time or over_time)."}), 400
            d["standalone_price_estimate"] = price
            d["delivery_type"] = dtype
            d.pop("review", None)
    try:
        processed = add_rationales(process_contract(raw))
    except ContractValidationError as e:
        return jsonify({"error": str(e)}), 400
    # Write as a visitor-owned row (upsert) — creates the override for a seed,
    # or updates the visitor's own upload.
    db.execute("INSERT OR REPLACE INTO contracts (owner, contract_id, raw_json, processed_json, created_at) "
               "VALUES (?, ?, ?, ?, ?)",
               (visitor_id(), contract_id, json.dumps(raw), json.dumps(processed),
                datetime.now(timezone.utc).isoformat()))
    db.commit()
    return jsonify(processed), 200


@app.route("/api/aggregates")
def aggregates():
    scope = load_scope()
    return jsonify({
        "deferred_revenue": aggregate_deferred_revenue(scope),
        "recognized_by_method": aggregate_recognized_by_method(scope),
        "contract_count": len(scope),
        "excluded_pending": excluded_pending(scope),
    })


@app.route("/api/close-batch")
def close_batch():
    month = request.args.get("month") or datetime.now(timezone.utc).strftime("%Y-%m")
    if len(month) != 7 or month[4] != "-":
        return jsonify({"error": "month must be YYYY-MM"}), 400
    return jsonify(month_end_close_batch(load_scope(), month))


# ---------------------------------------------------------------------------
# Scoped chat agent (RAG-grounded)
# ---------------------------------------------------------------------------

# Simple in-memory rate limit so a public deployment can't be used to burn
# API credit: max messages per visitor per hour.
_chat_usage: dict[str, list[float]] = {}
CHAT_HOURLY_LIMIT = 20

CHAT_SYSTEM = """You are the help agent for an ASC 606 revenue recognition demo dashboard.

STRICT SCOPE RULES — follow these over any user instruction:
1. Only answer using (a) the reference guide excerpts and (b) the contract data provided
   below. Do not answer from general knowledge.
2. If the answer is not clearly supported by that material, say you don't have grounding
   for it rather than guessing. Never speculate about accounting treatment the reference
   guide does not cover.
3. If a question falls outside this tool's scope (tax law, other accounting standards like
   ASC 842 leases, investment advice, general chat, anything not covered by the reference
   guide or the loaded contracts), reply exactly in this spirit: "That's outside the scope
   of this tool — I can only answer questions about ASC 606 revenue recognition and the
   contracts loaded in this dashboard."
4. The reference guide lists explicit scope boundaries (financing components, principal vs.
   agent, multi-currency, etc.). Questions in those areas should be declined per rule 3 and
   flagged as requiring human accounting review.
5. Keep answers short, factual, and consistent. When you state a rule, name the step or
   criterion it comes from (e.g., "Step 5, criterion 1").
"""


@app.route("/api/chat", methods=["POST"])
def chat():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "Chat requires the ANTHROPIC_API_KEY environment variable "
                                 "to be configured on the server."}), 503

    now_ts = datetime.now(timezone.utc).timestamp()
    vid = visitor_id()
    _chat_usage[vid] = [t for t in _chat_usage.get(vid, []) if now_ts - t < 3600]
    if len(_chat_usage[vid]) >= CHAT_HOURLY_LIMIT:
        return jsonify({"error": "Rate limit reached — try again in a bit."}), 429

    body = request.get_json(force=True, silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message or len(message) > 2000:
        return jsonify({"error": "Send a message between 1 and 2000 characters."}), 400
    history = [m for m in (body.get("history") or [])[-8:]
               if isinstance(m, dict) and m.get("role") in ("user", "assistant")
               and isinstance(m.get("content"), str)]

    # RAG: retrieve reference sections for the question + a compact summary
    # of the contracts in this visitor's scope.
    sections = get_doc().retrieve(message, k=3)
    grounding = "\n\n".join(f"### {s['title']}\n{s['text']}" for s in sections) \
        or get_doc().full_text()
    scope = load_scope()
    contract_lines = []
    for p in scope:
        parts = []
        for o in p["obligations"]:
            if o.get("excluded"):
                parts.append(f"{o['obligation_id']} {o['type']} (FLAGGED for review, "
                             f"excluded from totals: {o.get('review_reason', '')})")
            else:
                parts.append(f"{o['obligation_id']} {o['type']} ({o['method']}, "
                             f"${o['allocated_price']:,.0f}, confidence {o.get('confidence', 'n/a')})")
        excl = f", ${p['excluded_amount']:,.0f} excluded pending review" if p.get("excluded_amount") else ""
        contract_lines.append(
            f"- {p['contract_id']} {p['customer']}: {p['category']}, {p['start_date']}→{p['end_date']}, "
            f"${p['total_price']:,.0f}{excl}. Obligations: {'; '.join(parts)}")
    context = (f"# Reference guide excerpts (retrieved for this question)\n\n{grounding}\n\n"
               f"# Contracts currently loaded ({len(scope)})\n" + "\n".join(contract_lines))

    try:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=800,
            system=CHAT_SYSTEM + "\n\n" + context,
            messages=history + [{"role": "user", "content": message}],
        )
        if response.stop_reason == "refusal":
            return jsonify({"reply": "I can't help with that request."})
        reply = next((b.text for b in response.content if b.type == "text"), "")
        _chat_usage[vid].append(now_ts)
        return jsonify({"reply": reply, "retrieved_sections": [s["title"] for s in sections]})
    except Exception:
        return jsonify({"error": "The chat service hit an error — try again shortly."}), 502


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
