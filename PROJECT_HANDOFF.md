# PROJECT HANDOFF — ASC 606 Revenue Recognition Engine

> This document is a complete, self-contained handoff. A brand-new Claude Code
> session with zero prior context should be able to read this file and fully
> understand, run, redeploy, and continue building the project — including
> recreating it from scratch if the repo were lost. Nothing here is summarized
> for brevity; Section 9 contains the full current source of every file.

---

## 1. What this project is

This is a simplified **ASC 606 revenue recognition engine**, built as a portfolio
project by Lindsey Trinh (a finance analyst — FP&A/treasury background — targeting
GTM / strategic-finance and AI-solutions roles at fintech companies, specifically a
**Ramp AI Solutions Strategist** application). It reads B2B software/services
contracts (as realistic prose PDFs, structured PDFs, or JSON), splits each into
performance obligations, allocates the transaction price across them, classifies
each obligation as recognized **point-in-time** or **over-time** under the ASC 606
five-step model, and produces the full downstream accounting: month-by-month
recognition schedules, journal entries (cash → deferred revenue → revenue),
per-contract and aggregate deferred-revenue balances, an automated month-end
close batch, an SAP-style journal-upload CSV, and a forward-looking known-revenue
(Remaining Performance Obligations / RPO) forecast. A retrieval-grounded Claude
layer *explains* each classification and powers a scoped chat agent, and a
confidence-scoring layer routes genuinely ambiguous contracts to a human review
queue. Its whole design thesis — the thing that makes it credible for a
finance-plus-AI role — is **"rules decide, AI reads and explains":** every number
in the output comes from deterministic, auditable rules; the AI is used only to
read messy prose contracts into structured data and to explain decisions the rules
already made, never to decide revenue treatment itself.

---

## 2. Current live deployment

- **Live URL:** https://asc606-revrec-engine-production.up.railway.app
- **Platform:** Railway (free/hobby tier), single web service.
- **GitHub repo:** https://github.com/lindsey3291/asc606-revrec-engine (owner GitHub username: `lindsey3291`; default branch `main`).
- **Deployment method:** Railway is connected to the GitHub repo and auto-deploys on every push to `main`. The start command lives in `railway.json` (`gunicorn app:app --bind 0.0.0.0:$PORT`); Railway injects `$PORT`.
- **Persistence:** a Railway **Volume** is mounted at `/data`, and the env var `DATABASE_URL=sqlite:////data/revrec.db` points SQLite at that volume so the database (seed contracts + visitor uploads + resolved overrides) survives restarts and redeploys.
- **API key:** the env var `ANTHROPIC_API_KEY` is set on Railway, which enables (a) live prose-contract extraction, (b) Claude-generated rationales on seeds/uploads, and (c) the chat agent. Without it the app still runs: structured/JSON upload works, rationales fall back to deterministic templates, and the chat endpoint returns a clear 503.

### How to redeploy after a code change (exact steps)

1. Make the code change locally in the project directory
   (`~/GTM-Finance-SQL/asc606-revrec-engine`).
2. Stage and commit:
   ```bash
   git add -A
   git commit -m "your message"
   ```
3. Push to `main`:
   ```bash
   git push
   ```
4. Railway detects the push and automatically builds and redeploys (~30–60s).
   No dashboard action is needed for a normal code change.
5. **If you changed seed data or the engine logic that affects seed output:**
   the seeds are cached in the database. `app.py` re-seeds automatically when
   either the SHA-1 hash of `data/seed_contracts.json` changes **or** the
   `SEED_VERSION` constant in `app.py` changes. So after an engine/`explain`
   change that alters seed output, **bump `SEED_VERSION`** (e.g. `"2"` → `"3"`)
   in `app.py` and push — that forces a one-time reprocess of the stored seeds
   on the next boot. Visitor uploads/overrides are never touched by re-seeding.
6. **To watch the deploy / debug:** Railway dashboard → the service →
   **Deployments** tab → **View logs** (build + runtime logs). A healthy boot
   prints `Seeded 10 contracts into ...` (only when re-seeding) and gunicorn
   `Booting worker`.

---

## 3. Full architecture overview

### Tech stack
- **Language:** Python 3 (deployed on Python 3.13 on Railway).
- **Web framework:** Flask, served by gunicorn in production.
- **Database:** SQLite, config-driven via a `DATABASE_URL` setting (`sqlite:///...`). The connection layer is deliberately isolated so it could be swapped for hosted Postgres.
- **Frontend:** a single self-contained `static/index.html` (vanilla HTML/CSS/JS, no build step), using Chart.js via CDN for the charts.
- **AI:** the official `anthropic` Python SDK, model `claude-opus-4-8`, using the Messages API with `output_config.format` JSON-schema structured outputs for extraction and rationales.
- **PDF:** `pypdf` for reading uploaded/seed PDFs; `fpdf2` (dev only) for generating the sample/seed PDFs.
- **Hosting:** Railway with a persistent volume.

### The five-stage data flow

**Stage 1 — Ingestion (PDF upload and extraction).** A contract enters as a PDF
(prose or structured) or a JSON file. The upload endpoint routes it: `engine/extract.py`
detects the format. A **structured** `Label: value` PDF goes to the deterministic
parser `engine/pdf_contract.py`. A **prose** PDF goes to the RAG-grounded Claude
extractor in `engine/extract.py`, which reads plain business English and infers the
customer, dates, total price, and per-obligation `type`, `delivery_type`, and
`standalone_price_estimate` — flagging (rather than guessing) when a bundle has no
pricing basis. Both paths emit the **exact same internal contract dict**, so nothing
downstream knows or cares which format it came from.

**Stage 2 — Classification (deterministic rules engine).** `engine/core.py` takes
the internal contract dict and does all the accounting with pure integer-cent math,
no AI: it identifies performance obligations from the deliverables, allocates the
transaction price across them in proportion to standalone selling price (ASC 606
Step 4), classifies each obligation `point_in_time` vs `over_time` from its
`delivery_type` (Step 5), applies the prospective-reallocation modification logic
when present, and marks flagged/unpriced obligations as excluded (no allocation, no
schedule).

**Stage 3 — AI-grounded explanation and confidence scoring (RAG).** `engine/explain.py`
runs strictly *after* the numbers exist. For each obligation it (a) assigns a
deterministic confidence (high/medium/low) via a keyword heuristic + tied-SSP and
out-of-scope checks, then (b) generates a 1–2 sentence rationale that cites the
specific ASC 606 rule applied — grounded in retrieved sections of the reference
guide via `engine/rag.py` — using Claude when a key is present, or a deterministic
template (which cites the same rules) otherwise. Flagged obligations carry their
extraction reason straight into the review queue at low confidence.

**Stage 4 — Calculation and storage (schedules, journal entries, database).**
Still in `engine/core.py`: point-in-time obligations recognize a lump sum on the
delivery month; over-time obligations spread evenly by month across the term.
Journal entries are generated for cash receipt (Dr Cash / Cr Deferred Revenue) and
each recognition event (Dr Deferred Revenue / Cr Revenue); per-contract month-by-month
deferred-revenue tables are built. `app.py` caches each contract's full processed
result as JSON in SQLite, so aggregates never reprocess existing contracts and adding
one contract only processes that one.

**Stage 5 — Reporting and interaction (dashboard, forecast, close batch, SAP export,
chat).** `app.py` exposes a JSON API and serves `static/index.html`. The dashboard
shows the aggregate deferred-revenue time series, a point-in-time-vs-over-time
recognized-by-month chart, the RPO forecast line, a filterable contract table, a
per-contract detail view (obligations, rationales with rule citations and confidence
chips, deferred-revenue table, journal entries), a "Needs Review" queue, an
"$X excluded pending review" banner, the month-end close batch with control flags,
an SAP-style CSV download, a scoped chat panel, contract upload, contract delete,
and a human "provide the missing data" resolve flow for flagged contracts.

### How the stages map to files

| Stage | File(s) | Responsibility |
|---|---|---|
| 1. Ingestion / routing | `engine/extract.py` | Detect structured vs prose PDF; route to the right extractor; translate the result into the internal contract dict. Prose path calls Claude (RAG-grounded). |
| 1. Structured parsing | `engine/pdf_contract.py` | Deterministic `Label: value` PDF parser: robust currency parsing, negative-price rejection, modification-line detection (never silently dropped). |
| 1. Retrieval | `engine/rag.py` | Load and section the reference guide; keyword-score retrieval used by both the extractor and the explain/chat layers. |
| 2 + 4. Rules engine | `engine/core.py` | Validation, obligation identification, price allocation, classification, schedules, modification reallocation, journal entries, deferred-revenue tables, excluded-obligation handling, and all aggregate functions (deferred-revenue series, recognized-by-method, close batch, RPO forecast, excluded-pending). |
| 3. Explanation + confidence | `engine/explain.py` | Deterministic confidence heuristic (+ tied-SSP / out-of-scope / sparse checks), RAG-grounded Claude rationales with rule citations, template fallback, needs-review assembly. |
| 5. API + storage + serving | `app.py` | Flask routes, SQLite persistence + config-driven DB, visitor-cookie scoping, seeding/migration, upload/delete/resolve endpoints, aggregates, close batch, SAP CSV, forecast, chat agent, static file serving. |
| 5. UI | `static/index.html` | The entire dashboard (HTML/CSS/JS + Chart.js). |
| Reference data | `data/asc606_reference_doc.md` | The condensed ASC 606 guide that grounds all AI reasoning. |
| Seed data | `data/seed_contracts.json` | The 10 seed contracts as ground-truth extracted internal representations. |
| Seed documents | `data/seed_pdfs/*.pdf` | The 10 human-facing prose contract PDFs. |
| Offline report | `scripts/generate_report.py` | Generates a JSON/CSV/markdown report bundle from the seeds into `output/`. |
| PDF generators (dev) | `scripts/make_seed_pdfs.py`, `scripts/make_sample_pdf.py` | Generate the prose seed PDFs and the structured sample PDF. |
| Tests | `scripts/test_bugfixes.py` | 33 checks covering the bug fixes, flagging/exclusion, modification, backward compatibility. |

---

## 4. Complete file structure

```
asc606-revrec-engine/
├── app.py                          Flask backend: API routes, SQLite persistence, seeding/migration,
│                                   visitor scoping, upload/delete/resolve, aggregates, close batch,
│                                   SAP CSV export, RPO forecast, scoped chat agent, static serving.
├── railway.json                    Railway deploy config (start command: gunicorn app:app --bind 0.0.0.0:$PORT).
├── requirements.txt                Python dependencies (flask, gunicorn, anthropic, pypdf).
├── .gitignore                      Ignores __pycache__, *.pyc, revrec.db, .env, venvs, output/.
├── README.md                       Human-readable project readme (ASC 606 model, deployment, limitations, tests).
├── engine/
│   ├── __init__.py                 Empty package marker.
│   ├── core.py                     Deterministic ASC 606 engine: validation, allocation, classification,
│   │                               schedules, modification reallocation, journal entries, deferred-revenue
│   │                               tables, excluded-obligation handling; aggregate functions
│   │                               (deferred-revenue series, recognized-by-method, month-end close batch,
│   │                               RPO forecast, excluded-pending). PURE RULES, no AI.
│   ├── extract.py                  Contract extraction entry point: format detection (structured vs prose),
│   │                               RAG-grounded Claude prose extractor, translation into the internal schema.
│   ├── pdf_contract.py             Deterministic structured `Label: value` PDF parser: robust currency parsing,
│   │                               negative rejection, modification-line detection (flag not drop).
│   ├── explain.py                  AI explanation + confidence layer: deterministic confidence heuristic,
│   │                               tied-SSP / out-of-scope / sparse checks, RAG-grounded Claude rationales
│   │                               with rule citations, template fallback, needs-review assembly.
│   └── rag.py                      Reference-doc loader + keyword-scoring retrieval (no embeddings).
├── data/
│   ├── asc606_reference_doc.md     Condensed ASC 606 reference guide (grounding source for all AI reasoning).
│   ├── seed_contracts.json         The 10 seed contracts as ground-truth extracted internal representations.
│   └── seed_pdfs/
│       ├── 01_northstar_saas.pdf   #1 Simple SaaS subscription (clean over-time). Also the dashboard sample.
│       ├── 02_ironclad_hardware.pdf #2 One-time hardware sale (clean point-in-time).
│       ├── 03_meridian_bundle.pdf  #3 Bundle, single aggregate fee, no per-component price (FLAGGED).
│       ├── 04_blueridge_support.pdf #4 24-month support + mid-term modification (prospective reallocation).
│       ├── 05_vertex_partial.pdf   #5 Bundle with partial standalone pricing (back-into allocation).
│       ├── 06_sterling_report.pdf  #6 One-time professional-services report (point-in-time).
│       ├── 07_quantum_platform.pdf #7 Ambiguous annual platform fee (reason to over-time, weaker signal).
│       ├── 08_nimbus_bundle.pdf    #8 Fully-priced two-obligation bundle (clean allocation, no flag).
│       ├── 09_apex_three.pdf       #9 Three-component bundle, single total, no pricing (FLAGGED, hardest).
│       └── 10_sparrow_install.pdf  #10 Simple delivery/installation project (clean point-in-time baseline).
├── scripts/
│   ├── generate_report.py          Offline report bundle generator (JSON/CSV/markdown → output/).
│   ├── make_seed_pdfs.py           Generates the 10 prose seed PDFs; copies #1 to static/sample_contract.pdf.
│   ├── make_sample_pdf.py          Generates a STRUCTURED-format sample PDF (legacy/back-compat testing).
│   └── test_bugfixes.py            33-check regression suite (bug fixes, flagging, modification, back-compat).
├── static/
│   ├── index.html                  The entire dashboard (HTML/CSS/JS, Chart.js via CDN).
│   └── sample_contract.pdf         Downloadable sample contract (a copy of seed #1's prose PDF).
└── output/                         Generated report artifacts (gitignored; produced by generate_report.py).
    ├── report.json                 Full processed results for all seeds + aggregates.
    ├── journal_entries.csv         Every journal entry across all seeds.
    ├── deferred_revenue.csv        Per-contract monthly deferred-revenue tables.
    ├── aggregate_deferred.csv      Total deferred-revenue liability by month.
    └── summary.md                  Readable markdown summary of all seeds + close batch + RPO.
```

Note: `revrec.db` (the SQLite database) is created at runtime and is gitignored; on
Railway it lives on the mounted volume at `/data/revrec.db`.

---

## 5. Data model / schema

### Database schema (SQLite)

Two tables, created in `app.py`'s `init_db()`:

```sql
CREATE TABLE IF NOT EXISTS contracts (
    owner          TEXT NOT NULL,      -- 'seed' or a visitor id (cookie hex)
    contract_id    TEXT NOT NULL,      -- e.g. 'ORD-2601'
    raw_json       TEXT NOT NULL,      -- the contract as ingested (internal input schema)
    processed_json TEXT NOT NULL,      -- cached full engine output (internal output schema)
    created_at     TEXT NOT NULL,      -- ISO 8601 timestamp
    PRIMARY KEY (owner, contract_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,            -- currently only 'seed_hash'
    value TEXT                         -- '<sha1 of seed_contracts.json>:<SEED_VERSION>'
);
```

**Scoping model:** every visitor gets a cookie (`revrec_visitor`, a random hex id).
`load_scope()` / `visible_rows()` return rows where `owner IN ('seed', <visitor_id>)`,
**deduped by `contract_id` preferring the visitor's row over the seed.** This is how a
visitor can *resolve* a shared flagged seed: resolving writes a visitor-owned row
(`owner=<visitor_id>`, same `contract_id`) that supersedes the seed in that visitor's
view only. Deleting that override reverts to the seed. Pure seeds cannot be deleted.

### Internal INPUT schema (a contract's `raw_json`)

This is the single internal representation that both the structured parser and the
prose extractor emit, and that JSON uploads must match:

```jsonc
{
  "contract_id": "ORD-2601",          // string; generated for prose uploads (e.g. "CEDA-1A2B")
  "customer": "Cedar Grove Financial",// string
  "start_date": "2026-01-01",         // "YYYY-MM-DD"
  "end_date": "2026-12-31",           // "YYYY-MM-DD" (== start_date for a single point-in-time deliverable)
  "total_price": 72000,               // number (fixed consideration; excludes purely variable/usage estimates)
  "deliverables": [
    {
      "type": "subscription",          // string, free-form lower_snake label
      "description": "Access to the Platform for a period of twelve (12) months",
      "standalone_price_estimate": 72000, // number OR null (null only when a "review" marker is present)
      "delivery_type": "over_time"     // "one_time" | "over_time" | "unknown" ("unknown" only with a review marker)
      // OPTIONAL — present only on flagged/excluded obligations:
      // "review": {
      //   "kind": "unpriced_bundle" | "variable_consideration" | "ambiguous_timing",
      //   "reason": "human-readable explanation",
      //   "excluded_amount": 8000     // number OR null (dollar amount to show as excluded, if known)
      // }
    }
  ],
  // OPTIONAL — mid-term modification (triggers prospective reallocation):
  "modification": {
    "date": "2026-12-01",             // "YYYY-MM-DD"; must fall inside the contract term
    "description": "…",
    "added_price": 18000,             // number
    "added_deliverable": {
      "type": "support_addon",
      "description": "…",
      "standalone_price_estimate": 18000, // number
      "delivery_type": "over_time"    // must be over_time in this engine
    }
  },
  // OPTIONAL — legacy variable-consideration form (supported by core.py; not used by current seeds):
  "variable_consideration": {
    "description": "…",
    "monthly_actuals": { "2026-01": 1850, "2026-02": 2340 }  // month -> usage $ recognized as billed
  }
}
```

Validation rules (in `core.py`'s `validate_contract`): required contract fields
present; `end_date >= start_date`; `total_price > 0`; deliverables non-empty; each
deliverable has the required fields; a **non-flagged** deliverable must have
`delivery_type in (one_time, over_time)` and `standalone_price_estimate > 0`; a
**flagged** deliverable (has a `review` marker) may have `standalone_price_estimate`
null and `delivery_type == "unknown"`, and its price if present must be ≥ 0. A
`modification` must have all sub-fields, its date inside the term, and at least one
over-time deliverable in the contract.

### Internal OUTPUT schema (a contract's `processed_json`)

Produced by `core.process_contract(...)` then enriched by `explain.add_rationales(...)`:

```jsonc
{
  "contract_id": "ORD-2601",
  "customer": "Cedar Grove Financial",
  "start_date": "2026-01-01",
  "end_date": "2026-12-31",
  "total_price": 72000.00,            // stated total (dollars, 2dp)
  "recognized_amount": 72000.00,      // allocable/recognizable consideration (included obligations only)
  "excluded_amount": 0.00,            // amount held out pending review (whole total if nothing is allocable)
  "category": "subscription",         // "one_time" | "subscription" | "bundled" | "modification" | "variable"
  "term_months": 12,
  "obligations": [
    {
      "obligation_id": "ORD-2601-OB1",
      "type": "subscription",
      "description": "…",
      "standalone_price_estimate": 72000.00, // number OR null (null for excluded)
      "allocated_price": 72000.00,           // number OR null (null for excluded)
      "method": "over_time",                 // "point_in_time" | "over_time" | "pending_review"
      "added_by_modification": false,
      "excluded": false,                     // true for flagged obligations
      "review_kind": null,                   // "unpriced_bundle" | "variable_consideration" | "ambiguous_timing" | null
      "review_reason": null,                 // string | null
      "review_excluded_amount": null,        // number | null
      "confidence": "high"                   // "high" | "medium" | "low" (added by explain layer)
      // "needs_review": bool                // also set on the obligation by the explain layer
    }
  ],
  "schedule": [
    { "month": "2026-01", "obligation_id": "ORD-2601-OB1", "amount": 6000.00, "method": "over_time" }
    // method is "point_in_time" | "over_time" | "usage"; flagged obligations produce NO schedule rows
  ],
  "journal_entries": [
    {
      "date": "2026-01-01", "month": "2026-01", "entry_type": "cash_receipt",
      "debit_account": "Cash", "credit_account": "Deferred Revenue",
      "amount": 72000.00, "memo": "…", "obligation_id": null
    }
    // entry_type is "cash_receipt" | "recognition" | "usage_billing"
    // recognition: Dr Deferred Revenue / Cr Revenue; usage_billing: Dr Cash / Cr Revenue
  ],
  "deferred_revenue": [
    { "month": "2026-01", "beginning_balance": 0.00, "cash_received": 72000.00,
      "revenue_recognized": 6000.00, "ending_balance": 66000.00 }
  ],
  "modification_note": null,           // string when a modification was applied, else null
  "variable_note": null,               // string for legacy variable_consideration, else null
  "rationales": {                      // keyed by obligation_id (added by explain layer)
    "ORD-2601-OB1": {
      "text": "Recognized over time under Step 5, criterion 1 …",
      "source": "claude (RAG-grounded)" | "template (reference-doc grounded)" | "extraction flag",
      "rule_citation": "Step 5, over-time criterion 1 (ASC 606-10-25-27): …",
      "confidence": "high",
      "confidence_reason": "…"
    }
  },
  "needs_review": [                    // added by explain layer; one entry per accurate reason
    { "obligation_id": "…", "type": "…", "description": "…", "reason": "…" }
  ]
}
```

The API's `/api/contracts` list adds two transient fields per contract not stored in
the DB: `deletable` (true for the visitor's own rows) and `is_override` (true for a
visitor's resolved copy of a seed).

---

## 6. The reference guide and RAG setup

The complete current content of `data/asc606_reference_doc.md` is reproduced in full
immediately below (this is the exact grounding source used by the extractor, the
rationale layer, and the chat agent):

```markdown
# ASC 606 Reference Guide (Internal Knowledge Base)

Compiled from Deloitte's Roadmap: Revenue Recognition, KPMG's Handbook: Revenue
Recognition, BDO's ASC 606 guidance, PwC Viewpoint, and FASB ASC 606-10. This is an
original summary written for this project, not reproduced from any single source. Where a
specific accounting citation is included (e.g., ASC 606-10-25-27), it references the
codification paragraph number, which is a factual reference, not copyrighted text.

## The Core Principle

A company recognizes revenue to reflect the transfer of promised goods or services to a
customer, in an amount that reflects what the company expects to be entitled to in exchange.
Revenue is recognized when — or as — control transfers, not when cash is received.

## Step 1: Identify the Contract

A contract must have commercial substance, create enforceable rights and obligations for both
parties, have identifiable payment terms, and it must be probable the company will collect what
it's owed. Informal or incomplete agreements may not qualify until formalized.

## Step 2: Identify the Performance Obligations

A performance obligation is a promise to transfer a distinct good or service. "Distinct" means the
customer can benefit from it on its own (or with readily available resources) AND it's separately
identifiable from other promises in the contract. If two items are highly interdependent or one
significantly modifies the other, they may need to be combined into a single obligation rather
than split.

## Step 3: Determine the Transaction Price

The total consideration the company expects to receive, adjusted for variable consideration
(discounts, rebates, usage-based fees, bonuses), any significant financing component, noncash
consideration, and amounts payable to the customer. Variable consideration must be estimated,
but the estimate is constrained — a company can't recognize an amount if it's probable a
significant reversal will occur later.

## Step 4: Allocate the Transaction Price

The transaction price is allocated across performance obligations based on their relative
standalone selling price (SSP) — what each item would sell for on its own to a similar customer.
If the sum of standalone prices differs from the actual contract price (e.g., a bundle discount),
the difference is typically spread proportionally across all obligations, not assigned to just one.

## Step 5: Recognize Revenue When (or As) Obligations Are Satisfied

This is the step our engine automates. The critical test, per ASC 606-10-25-27, is control
transfer — not effort, not the passage of time by itself, and not merely "the customer keeps
using it afterward."

An obligation is recognized OVER TIME if any ONE of these three criteria is met:

1. The customer simultaneously receives and consumes the benefit as the company performs
   (e.g., a monthly support contract, a SaaS subscription providing continuous access).
2. The company's performance creates or enhances an asset that the customer controls as
   it's being built (e.g., construction on a customer's property).
3. The asset created has no alternative use to the company, AND the company has an
   enforceable right to payment for work completed to date (e.g., a highly customized build
   the company couldn't resell to anyone else).

If none of the three criteria are met, the obligation is recognized at a POINT IN TIME —
when control transfers, typically evidenced by physical possession, legal title transfer, the
customer accepting the asset, or the company having a present right to payment.

Common misconception to explicitly avoid: recognizing "over time" is not the same as "the
customer will use this slowly." A perpetual software license or a physical product is typically
point-in-time even though the customer's own usage is gradual, because the company's
obligation to deliver was satisfied in a single moment — nothing further is owed by the company
after delivery. Support, hosting, and subscription access are typically over-time because the
company must keep performing continuously for the obligation to be considered satisfied.

## Licensing (Relevant for Software/IP Deals)

ASC 606 distinguishes between:

- Functional IP license (a right to use IP "as it exists" at a point in time, e.g., a static
  software version) — generally point-in-time.
- Symbolic IP license (a right to access IP that the company will continue to update/support,
  e.g., a brand or evolving platform) — generally over-time, since the customer is relying on
  the company's continued activity.

## Contract Modifications

A modification is a change in scope or price approved by both parties. It's treated one of three
ways, and the trigger for each is specific:

1. Separate contract — triggers when the added goods/services are distinct AND priced at
   their standalone selling price. Treated as an entirely new, separate contract; the original
   contract's obligations are untouched.
2. Prospective reallocation — triggers when the added goods/services are distinct but NOT
   priced at standalone value (e.g., a bundled discount on the addition). The remaining
   unrecognized consideration from the original contract is combined with the new
   consideration and reallocated across the remaining obligations, going forward only —
   past recognized revenue is not adjusted.
3. Cumulative catch-up — triggers when the added goods/services are NOT distinct from
   what's already being delivered (they're effectively part of an obligation already in
   progress). Revenue recognized to date is adjusted immediately to reflect the full modified
   contract, rather than spread forward.

For our modification contracts specifically: this project uses approach 2 (prospective
reallocation of remaining, unrecognized value across the revised remaining term) as the
simplifying assumption, since the added modules are treated as distinct but not separately
priced at standalone value. Approaches 1 and 3 are flagged as the judgment call a real
accountant would need to confirm — whether the addition is priced at standalone value
(→ approach 1) or is not distinct from existing obligations (→ approach 3).

## Deferred Revenue vs. Accounts Payable — the Distinction to Never Blur

Both sit on the liability side of the balance sheet, but they are fundamentally different kinds of
obligations:

- Deferred revenue (a.k.a. contract liability): the company owes the customer future
  performance (a good or service), not cash. It's settled by delivering, not by paying money out.
- Accounts payable: the company owes a vendor cash for something already received.
  It's settled by paying cash out.

## What This Project Deliberately Does Not Handle (Scope Boundaries)

- Variable consideration constraint estimation (beyond a single simple example)
- Principal vs. agent determination
- Significant financing components
- Multi-currency or multi-entity contract combination rules
- Full disclosure requirements (this is a recognition/scheduling tool, not a
  disclosure-drafting tool)
- Contracts that fail the Step 1 enforceability test entirely

When a contract or question falls into one of these areas, the correct behavior is to flag it for
human review rather than attempt to resolve it automatically.
```

### How retrieval works in this codebase

Retrieval is deliberately **simple, deterministic keyword scoring — no embeddings,
no vector store.** The reasoning (a design decision, see Section 7): the reference
doc is ~9 KB of text, so transparent, auditable, reproducible keyword retrieval
reliably returns the right section and is far easier to reason about than an
embedding index. Implementation is in `engine/rag.py`:

1. **Load + section.** On first use, `data/asc606_reference_doc.md` is read and split
   into sections at each `##` markdown heading. Each section stores its title, its
   body text, the set of tokens in its title, and the list of tokens in its body.
   Tokenizing lowercases, splits on non-alphanumeric characters, and drops a small
   stopword list. The parsed doc is cached in a module-level singleton (`get_doc()`).

2. **Score a query.** `ReferenceDoc.retrieve(query, k=3)` tokenizes the query the
   same way, then scores every section: **+3 for each query token that appears in the
   section's title, +1 for each query token occurrence in the section body.** Sections
   with score > 0 are sorted descending and the top `k` are returned as
   `{title, text}` dicts.

3. **Inject into the prompt.** Two consumers use this:
   - **Prose extraction** (`engine/extract.py`): before calling Claude, it retrieves
     the sections most relevant to the extraction task (queries like "identify
     performance obligations distinct", "recognize revenue over time point in time
     three criteria control", "allocate transaction price standalone selling price",
     "contract modifications", "variable consideration constraint"), dedupes by title,
     and concatenates them under a `# Reference guide excerpts` header appended to the
     system prompt. The model is instructed to ground its reading in those excerpts
     and reason via the three-criteria test, not keyword-match.
   - **Rationale generation** (`engine/explain.py`): for each contract it retrieves
     sections relevant to that contract's features (obligations/allocation/over-time
     criteria, plus modification/variable sections if applicable) and appends them to
     the rationale system prompt; the model must name the specific rule/criterion it
     applied.
   - **Chat agent** (`app.py`): for each user message it retrieves the top 3 sections
     for that message and includes them, plus a compact summary of the currently
     loaded contracts, as grounding in the chat system prompt.

There is no separate embedding step and nothing is persisted for retrieval — the doc
is re-scored per query in-process, which at this size is instantaneous.

---

## 7. Key design decisions and why

- **Classification is rule-based, not AI-generated.** Every number and every
  point-in-time-vs-over-time decision comes from deterministic code in `engine/core.py`,
  not from a model. Accounting must be **auditable and reproducible**: the same
  contract must always yield the same journal entries, and each result must trace to a
  rule (e.g. ASC 606-10-25-27), not to a model that could answer differently next run.
  The AI is confined to two lanes — *reading* messy prose into structured fields, and
  *explaining* decisions the rules already made. For a prose upload the AI does propose
  the `delivery_type` the rules then execute, so it influences the outcome; but the
  execution and all math are deterministic and inspectable, and for structured/JSON
  input even the proposal is rule-based.

- **Flagged / needs-review obligations are excluded from aggregate totals until
  resolved.** When a bundle has no basis to split its price, or an obligation is
  genuinely ambiguous, the engine does **not** fabricate an allocation to keep totals
  looking complete. A flagged obligation gets an obligation record but **no allocation
  and no schedule**, so it is automatically absent from deferred revenue, the RPO
  forecast, and the close batch. A visible "**$X excluded from the totals pending
  review**" banner names the affected contracts so the incomplete totals read as
  intentional, not as a bug. A human can then supply the missing standalone price +
  timing (the resolve flow), and the obligation flows into the aggregates normally.
  Silently guessing would be the worst outcome for an accounting tool — a wrong number
  that looks right.

- **Confidence scoring and RAG explanation are separate layers from classification.**
  `engine/core.py` (rules) knows nothing about confidence or prose rationales;
  `engine/explain.py` runs strictly *after* the numbers exist and cannot change them.
  This keeps the audit trail clean (the classification is deterministic and testable in
  isolation) and lets the explanation layer degrade gracefully — with no API key it
  falls back to deterministic template rationales that cite the same rules, and the app
  is fully functional. It also means the AI's confidence opinion can nuance a *displayed*
  confidence but can never silently re-route a priced obligation into review (a bug that
  surfaced once the live key was set; see Section 8-style note below).

- **The chat agent is strictly scoped and low-variance.** Its system prompt forbids
  answering from general model knowledge — every answer must be grounded in the
  retrieved reference-doc excerpts plus the loaded contract data — and instructs it to
  decline anything outside ASC 606 revenue recognition and the loaded contracts (tax
  law, other standards like ASC 842 leases, investment advice, general chat), and to
  defer reference-doc scope-boundary topics (financing components, principal-vs-agent,
  multi-currency) to human review. **On temperature:** the original intent was a low
  temperature (0.1–0.2) for consistency, but the current model generation
  (`claude-opus-4-8`) removed sampling parameters entirely (they are rejected by the
  API), so determinism/consistency is enforced instead through the strict grounding
  rules in the system prompt — which is the stronger control for accuracy anyway. The
  endpoint is also rate-limited (20 messages per visitor per hour) since it is public,
  and returns a clear 503 when no API key is configured. The five out-of-scope test
  questions were run against the live agent and all correctly declined (transcript in
  README).

- **Modification treatment: three approaches exist; this project implements approach 2.**
  Per the reference guide, a contract modification is treated one of three ways:
  **(1) separate contract** — the added goods/services are distinct AND priced at their
  standalone selling price, so it's treated as a new, independent contract and the
  original is untouched; **(2) prospective reallocation** — the added goods/services are
  distinct but NOT priced at standalone value, so the remaining unrecognized
  consideration from the original is combined with the new consideration and reallocated
  across the remaining obligations, going forward only (past recognized revenue is not
  adjusted); **(3) cumulative catch-up** — the added goods/services are NOT distinct from
  what's already being delivered, so revenue recognized to date is adjusted immediately.
  This engine implements **approach 2 (prospective reallocation)** as the simplifying
  assumption, applied in `core._apply_modification`. Approaches 1 and 3 are the judgment
  calls a real accountant would confirm and are documented as a known limitation.

- **Seed contracts were changed from 20 structured contracts to 10 realistic
  unstructured (prose) ones.** The original 20 seeds used rigid `Label: value` fields
  (e.g. "Delivery: over_time"), which handed the classifier the answer and understated
  what the tool demonstrates. They were replaced with 10 contracts written as realistic
  order forms / SOWs / MSA excerpts (letterhead, numbered sections, signature blocks),
  with the deal terms living in the prose — so the extraction step must actually reason
  about control transfer, distinctness, and pricing from context. The 10 span the full
  range: clean SaaS, clean hardware, unpriced bundle (flagged), long-term support + a
  mid-term modification, partial-pricing bundle (back-into allocation), one-time
  professional services, ambiguous platform fee, fully-priced bundle, three-component
  unpriced bundle (flagged, hardest), and a simple delivery/installation. **Exactly two
  require human review** (the two unpriced bundles: ORD-2603 and ORD-2609, $190,000 held
  out). Because the deploy has no API key at seed time, the seeds are shipped as
  **ground-truth extracted JSON** (`data/seed_contracts.json`) authored to match what a
  correct extraction produces; the human-facing prose PDFs live in `data/seed_pdfs/`,
  and re-uploading any of them with the key set reproduces the extraction live.

- **Backward compatibility with the structured format is a hard requirement, not
  optional.** Earlier bug-testing used structured `Label: value` PDFs. `engine/extract.py`
  detects format and routes structured PDFs to the original deterministic parser
  (`engine/pdf_contract.py`) unchanged, while prose PDFs go to the AI extractor. Both
  produce the identical internal schema, so downstream code is format-agnostic.

- **Extraction is a pure translation step, not a parallel data path.** Whatever format a
  contract arrives in, it is translated into the one internal contract dict before any
  downstream code runs. There is no separate representation for prose-extracted
  contracts; journal entries, schedules, the SAP export, and the forecast never know the
  origin.

- **Incremental aggregation, not full reprocessing.** Each contract is processed exactly
  once and its full result cached in SQLite; aggregate views sum the cached per-contract
  series. Adding contract #N only processes that one — a real system with thousands of
  contracts can't reprocess everything on every insert.

- **Per-visitor scoping and overrides for a public multi-user demo.** Uploads and
  resolutions are scoped to a browser cookie so one visitor's test data never affects
  another's, while the 10 seeds stay visible to everyone by default. Resolving a shared
  flagged seed writes a **private per-visitor override** (a copy that supersedes the seed
  in that visitor's view only), with a revert control; other visitors still see it
  flagged.

- **Retrieval is keyword-based, not embeddings.** At ~9 KB of reference text,
  transparent keyword scoring reliably retrieves the right section and is auditable and
  dependency-light; a vector store would add opacity and infrastructure for no accuracy
  gain at this scale.

- **Config-driven database via a single `DATABASE_URL` setting.** SQLite by default; the
  URL indirection is the seam where a hosted Postgres would slot in if the host's disk
  weren't persistent. Documented as a deliberate design choice.

- **Delete is a hard delete with no accounting-period awareness (known limitation).**
  Deleting fully removes a contract and its figures. A real system must distinguish
  deleting an *un-posted* test contract (a true delete) from reversing one whose entries
  posted in a *closed* period — which is done with a reversing journal entry in the
  current period, never a silent rewrite of history. That period-close distinction is
  deliberately out of scope; noted in the README.

- **Deliberate scope limitations (left unaddressed on purpose).** Full unstructured
  contract parsing is scoped to structured extraction plus realistic prose (not arbitrary
  legal documents). Variable-consideration handling is minimal (flag-and-exclude, or the
  legacy recognized-as-billed path; no expected-value/most-likely estimation with the
  constraint and true-ups). No contract combination rules, no significant financing
  component, no principal-vs-agent, no multi-currency, no full disclosure requirements,
  and not full double-entry bookkeeping (single-entry-style cash → deferred → revenue
  flow; no general ledger, trial balance, or A/R — all contracts assume upfront cash).
  Implementation-type obligations are treated as point-in-time at go-live for simplicity.
  These cuts keep the core mechanics legible and demoable.

---

## 8. Bugs found and fixed, with details

These were found through stress-testing and each has a regression test in
`scripts/test_bugfixes.py` (33 checks total, all passing).

### Bug 1 — Silent modification data loss
- **Test case:** a contract PDF included a labeled "Modification:" line (date,
  description, added price) in addition to the standard fields.
- **What went wrong:** the extractor read the base fields correctly but **completely
  ignored** the Modification line — no error, no flag, no schedule adjustment. The
  journal entries were a clean 12-month schedule for the original price only, with zero
  trace that a modification existed. A recognized field was silently dropped.
- **The fix:** `engine/pdf_contract.py` now explicitly detects a `Modification:` block.
  If every modification sub-field is present (`Mod Date`, `Mod Description`,
  `Added Price`, `Added Type`, `Added Description`, `Added Standalone Price`,
  `Added Delivery` = over_time), it parses them into the modification dict so the
  prospective-reallocation logic runs (approach 2). If a `Modification:` label is present
  but any field is missing/unparseable, the upload is **rejected with a review message**
  ("This contract includes a modification that requires review … it is flagged here for
  review") rather than processed as if the modification weren't there. Net rule: a
  recognized field is never silently dropped — it is either fully applied or the upload
  is stopped with a clear, visible reason. The prose extractor has the analogous
  instruction (populate the modification object or flag; never drop).

### Bug 2 — Inconsistent / mislabeled confidence scoring
- **Test case:** the description "Perpetual software license" — identical text that
  processed at high confidence in several existing contracts — was flagged **low**
  confidence in a new contract with the stated reason "description too sparse to verify
  the classification." The actual trigger was unrelated to the description: the contract
  had two obligations with **identical/tied standalone selling prices**.
- **What went wrong:** two problems. (a) The sparse-description check ran *before* the
  supporting-vocabulary check, so a short-but-clear description ("Perpetual software
  license", 3 words) was mislabeled "too sparse" even though "perpetual"/"license" are
  clear point-in-time signals. (b) There was no separate check for tied standalone
  selling prices, so that real condition was misattributed to the sparse-description
  reason.
- **The fix (in `engine/explain.py`):** recognition-method confidence is now driven
  **only** by the deliverable's own description (so the same text yields the same
  confidence everywhere), and the supporting-vocabulary check runs **before** the
  sparse-length heuristic (so "Perpetual software license" scores high, not sparse). A
  **separate, explicitly-labeled tied-SSP check** was added: "Shares an identical
  standalone selling price ($X) with another obligation — the relative-SSP allocation
  (Step 4) may need manual confirmation." Review reasons are now a list, so one
  obligation can carry multiple accurate reasons without conflating them, and each names
  its true trigger. (A related follow-on: once the live API key was set, the AI rationale
  layer was over-aggressively downgrading a *priced, included* obligation into the review
  queue; that was fixed so the AI may nuance the displayed confidence down at most one
  notch, floored at "medium" for priced obligations, but never adds an item to the review
  queue on that basis — the queue is reserved for excluded/unpriced obligations and hard
  deterministic flags.)

### Bug 3 — Fragile Total Price / currency parsing
- **Test case:** a "Total Price" (or "Standalone Price") value formatted with a leading
  minus before the dollar sign (`$-5,000.00`) or parentheses-negative notation
  (`($5,000.00)`).
- **What went wrong:** the parser failed to extract the field and returned a generic
  "could not find 'Total Price' in the PDF" — indistinguishable from a genuinely missing
  field, giving no signal that the field was present but unparseable.
- **The fix (in `engine/pdf_contract.py`):** a broadened currency token/parser now
  handles standard variants — `$5,000.00`, `$5000`, `$5,000`, `5,000`, a leading minus
  before or after the `$`, and parentheses notation — preserving sign. A price that
  parses as **negative** is rejected with a **specific** message ("Total Price cannot be
  negative — found -$5,000.00"), distinct from the "could not find" error for a genuinely
  missing field, and distinct again from a present-but-unreadable value ("Found 'Total
  Price' in the PDF but could not read its value as an amount"). Zero is also rejected
  ("must be greater than zero"). Regression tests confirm standard positive formats still
  parse correctly, so the fix didn't merely special-case negatives.

---

## 9. Full current source code

Every source file's complete current content follows, in labeled sections. This is
sufficient to recreate the entire working app.

### `app.py`

```python
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
```

### `engine/__init__.py`

```python
```

### `engine/core.py`

```python
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
    months = month_span(start_m, end_m)

    # Step 2: identify performance obligations. Step 4: allocate the price.
    # An obligation carrying a "review" marker (missing/ambiguous standalone
    # price, variable consideration, ambiguous timing) is EXCLUDED: it gets an
    # obligation record but no allocation and no schedule, so it contributes
    # nothing to deferred revenue, the forecast, or the close batch until a
    # person resolves it. Nothing is ever allocated to it by a silent guess.
    included_idx = [i for i, d in enumerate(delivs) if not d.get("review")]
    flagged_idx = [i for i, d in enumerate(delivs) if d.get("review")]

    if flagged_idx:
        # Mixed/flagged contract: do NOT divide the stated total across
        # obligations (part of that total belongs to the excluded items). Each
        # included obligation must carry its own explicitly-stated price.
        alloc_by_idx = {i: _cents(delivs[i]["standalone_price_estimate"]) for i in included_idx}
    else:
        # Clean contract: allocate the transaction price across all obligations
        # in proportion to standalone selling prices (handles bundle discounts).
        ssp_all = [_cents(d["standalone_price_estimate"]) for d in delivs]
        alloc_list = _allocate(total_cents, ssp_all)
        alloc_by_idx = {i: alloc_list[i] for i in range(len(delivs))}

    obligations, schedule = [], []
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
            schedule.append({"month": start_m, "obligation_id": ob_id,
                             "amount_cents": alloc, "method": "point_in_time"})
        else:
            for m, amt in zip(months, _spread(alloc, len(months))):
                schedule.append({"month": m, "obligation_id": ob_id,
                                 "amount_cents": amt, "method": "over_time"})

    # Recognizable (allocable) consideration — only the included obligations.
    recognized_total_cents = sum(alloc_by_idx.get(i, 0) for i in included_idx)
    # Amount deliberately excluded from every total pending human review.
    if not included_idx:
        excluded_cents = total_cents          # whole contract un-allocatable
    else:
        excluded_cents = sum((ob.get("review_excluded_cents") or 0)
                             for ob in obligations if ob.get("excluded"))

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
            entry_date = contract["start_date"] if e["method"] == "point_in_time" else month_end(e["month"])
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

    return {
        "contract_id": cid,
        "customer": contract["customer"],
        "start_date": contract["start_date"],
        "end_date": contract["end_date"],
        "total_price": _dollars(total_cents),
        "recognized_amount": _dollars(recognized_total_cents),
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
```

### `engine/extract.py`

```python
"""Contract extraction — the single entry point for turning an uploaded PDF
into the internal contract dict the rest of the app depends on.

Two input formats are supported and auto-detected; BOTH produce the exact
same internal schema (contract_id, customer, start_date, end_date,
total_price, deliverables[...], optional modification), so nothing
downstream — journal entries, deferred revenue, SAP export, forecast —
needs to know where a contract came from:

  * STRUCTURED  — the legacy label:value order form (Contract ID: / Type: /
    Delivery: ...). Parsed deterministically by engine.pdf_contract. Kept for
    full backward compatibility with earlier test PDFs.

  * PROSE       — a realistic order form / SOW / MSA excerpt written in plain
    business English. Extraction here does real work: it reads the prose and
    reasons — grounded in the ASC 606 reference guide's three-criteria test —
    about what is being sold, whether each obligation transfers at a point in
    time or continuously, the dates, price, and customer. Where a standalone
    selling price is not stated for a bundled deal, it does NOT invent a
    number; it flags the obligation for review. Prose extraction uses the
    Claude API (reasoning, not keyword matching); without a configured key it
    returns a clear message rather than a bad guess.
"""
from __future__ import annotations

import json
import os
import re

from pypdf import PdfReader

from engine.core import ContractValidationError
from engine.pdf_contract import parse_contract_pdf
from engine.rag import get_doc

MODEL = "claude-opus-4-8"


def _pdf_text(fileobj) -> str:
    try:
        fileobj.seek(0)
    except Exception:
        pass
    reader = PdfReader(fileobj)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def is_structured(text: str) -> bool:
    """The legacy structured order form uses explicit field labels. Detect it
    by the label pattern so those PDFs keep routing to the deterministic
    parser exactly as before."""
    t = re.sub(r"\s+", " ", text)
    has_labels = bool(re.search(r"Contract ID:", t, re.I)) and (
        bool(re.search(r"Delivery:\s*(one_time|over_time)", t, re.I))
        or bool(re.search(r"Standalone Price:", t, re.I)))
    return has_labels


def extract_contract(fileobj, filename: str = "") -> dict:
    """Route an uploaded PDF to the right extractor. Returns the internal
    contract dict. Raises ContractValidationError with a clear message on
    failure (including 'prose extraction needs an API key')."""
    text = _pdf_text(fileobj)
    if is_structured(text):
        try:
            fileobj.seek(0)
        except Exception:
            pass
        return parse_contract_pdf(fileobj)     # legacy path, unchanged
    return _extract_prose(text)


# ---------------------------------------------------------------------------
# Prose extraction (AI, RAG-grounded)
# ---------------------------------------------------------------------------

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "customer": {"type": "string"},
        "start_date": {"type": "string", "description": "YYYY-MM-DD"},
        "end_date": {"type": "string", "description": "YYYY-MM-DD"},
        "total_price": {"type": "number"},
        "deliverables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                    "delivery_type": {"type": "string", "enum": ["one_time", "over_time", "unknown"]},
                    "reasoning": {"type": "string",
                                  "description": "Which of the three over-time criteria was applied, or why point-in-time."},
                    "standalone_price_estimate": {"type": ["number", "null"]},
                    "review": {
                        "type": ["object", "null"],
                        "properties": {
                            "kind": {"type": "string",
                                     "enum": ["unpriced_bundle", "variable_consideration", "ambiguous_timing"]},
                            "reason": {"type": "string"},
                            "excluded_amount": {"type": ["number", "null"]},
                        },
                        "required": ["kind", "reason", "excluded_amount"],
                        "additionalProperties": False,
                    },
                },
                "required": ["type", "description", "delivery_type", "reasoning",
                             "standalone_price_estimate", "review"],
                "additionalProperties": False,
            },
        },
        "modification": {
            "type": ["object", "null"],
            "properties": {
                "date": {"type": "string"},
                "description": {"type": "string"},
                "added_price": {"type": "number"},
                "added_deliverable": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "description": {"type": "string"},
                        "standalone_price_estimate": {"type": "number"},
                        "delivery_type": {"type": "string", "enum": ["over_time"]},
                    },
                    "required": ["type", "description", "standalone_price_estimate", "delivery_type"],
                    "additionalProperties": False,
                },
            },
            "required": ["date", "description", "added_price", "added_deliverable"],
            "additionalProperties": False,
        },
    },
    "required": ["customer", "start_date", "end_date", "total_price", "deliverables", "modification"],
    "additionalProperties": False,
}

_EXTRACT_SYSTEM = """You extract structured performance-obligation data from a contract written in plain business prose, for a deterministic ASC 606 revenue recognition engine that runs downstream. You do the READING and REASONING; the engine does the math.

Ground every judgment in the reference guide excerpts provided — especially Step 2 (distinct obligations), Step 4 (allocation by relative standalone selling price), and Step 5 (the three over-time criteria and the control-transfer test). Do NOT keyword-match (e.g. do not assume the word "months" means over_time); reason about whether control transfers continuously or at a point in time.

Rules:
- delivery_type = "over_time" ONLY if one of the three Step-5 criteria is met (typically criterion 1: the customer simultaneously receives and consumes the benefit as the company performs — subscriptions, hosting, support). "one_time" (point in time) when the company's obligation is satisfied at a single moment (delivered goods, a perpetual license, a one-off report), even if the customer's own use is gradual. Use "unknown" only if the text genuinely doesn't let you decide, and then attach a review with kind "ambiguous_timing".
- standalone_price_estimate: use an explicitly stated per-item price when given. If one component of a bundle is priced and the rest is the remainder of a stated total, you MAY infer the remainder. If a bundle gives only ONE aggregate price for multiple distinct components with no basis to split it, set standalone_price_estimate to null for those components and attach a review with kind "unpriced_bundle" — do NOT invent an allocation.
- Variable / usage-based consideration: set standalone_price_estimate to null and attach a review with kind "variable_consideration" and excluded_amount set to any stated estimate (else null). Do not confidently classify it.
- total_price is the fixed consideration stated in the contract (exclude purely variable/usage estimates from it).
- For a single point-in-time deliverable, set start_date and end_date to the delivery/execution date.
- modification: if the text describes a mid-term change in scope/price approved by both parties, populate the modification object (added_deliverable must be over_time in this engine). If you detect modification language but cannot fully structure it, still return the contract and attach a review of kind "ambiguous_timing" on the affected obligation noting it — never silently drop it.
- reasoning: one short phrase naming the specific criterion applied.

Return ONLY the JSON matching the provided schema."""


def _extract_prose(text: str) -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ContractValidationError(
            "This looks like a prose contract (not the structured order form). "
            "Reading prose contracts requires the Claude API — set ANTHROPIC_API_KEY on "
            "the server to enable it. You can still upload a structured-format PDF "
            "(the sample order form) or a JSON contract without a key.")
    try:
        import anthropic
    except ImportError:
        raise ContractValidationError("The anthropic package is not installed on the server.")

    doc = get_doc()
    sections = []
    seen = set()
    for q in ["identify performance obligations distinct",
              "recognize revenue over time point in time three criteria control",
              "allocate transaction price standalone selling price",
              "contract modifications", "variable consideration constraint"]:
        for s in doc.retrieve(q, k=2):
            if s["title"] not in seen:
                seen.add(s["title"])
                sections.append(s)
    grounding = "\n\n".join(f"### {s['title']}\n{s['text']}" for s in sections)

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=3000,
            system=_EXTRACT_SYSTEM + "\n\n# Reference guide excerpts\n\n" + grounding,
            messages=[{"role": "user", "content": "Extract this contract:\n\n" + text[:12000]}],
            output_config={"format": {"type": "json_schema", "schema": _EXTRACT_SCHEMA}},
        )
        if response.stop_reason == "refusal":
            raise ContractValidationError("The extractor declined to process this document.")
        data = json.loads(next(b.text for b in response.content if b.type == "text"))
    except ContractValidationError:
        raise
    except Exception as e:
        raise ContractValidationError(f"Prose extraction failed: {e}")

    return _to_internal(data)


def _to_internal(data: dict) -> dict:
    """Translate the extractor's output into the exact internal contract dict
    (identical shape to a structured/JSON contract). This is a pure
    translation step — no parallel data model."""
    delivs = []
    for d in data["deliverables"]:
        item = {
            "type": (d.get("type") or "item").strip().lower().replace(" ", "_"),
            "description": d["description"].strip(),
            "standalone_price_estimate": d.get("standalone_price_estimate"),
            "delivery_type": d.get("delivery_type", "unknown"),
        }
        if d.get("review"):
            item["review"] = {
                "kind": d["review"]["kind"],
                "reason": d["review"]["reason"],
                "excluded_amount": d["review"].get("excluded_amount"),
            }
        delivs.append(item)

    contract = {
        "contract_id": _gen_id(data["customer"]),
        "customer": data["customer"].strip(),
        "start_date": data["start_date"].strip(),
        "end_date": data["end_date"].strip(),
        "total_price": float(data["total_price"]),
        "deliverables": delivs,
    }
    if data.get("modification"):
        contract["modification"] = data["modification"]
    return contract


def _gen_id(customer: str) -> str:
    import hashlib
    import time
    slug = re.sub(r"[^A-Za-z]", "", customer)[:4].upper() or "CTRT"
    tail = hashlib.sha1(f"{customer}{time.time()}".encode()).hexdigest()[:4].upper()
    return f"{slug}-{tail}"
```

### `engine/pdf_contract.py`

```python
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
```

### `engine/explain.py`

```python
"""AI explanation layer — RAG-grounded against the ASC 606 reference doc.

Three responsibilities, all strictly AFTER engine/core.py has produced the
numbers (the AI explains output; it never makes classification decisions):

1. RATIONALES — a 1-2 sentence explanation per obligation, grounded in the
   most relevant section of data/asc606_reference_doc.md and citing the
   specific rule applied (e.g. "over time under criterion 1"). When the
   Claude API is available the model writes the prose from retrieved
   sections; otherwise a deterministic template — which cites the same
   sections — is used. Every rationale is tagged with its source.

2. CONFIDENCE (high/medium/low) — how cleanly each deliverable's description
   maps to the reference doc's distinctness (Step 2) and over-time (Step 5)
   criteria. A deterministic keyword heuristic always runs (so behavior is
   identical with or without an API key); low-confidence items surface in
   the dashboard's "Needs Review" section instead of being silently
   auto-processed.

3. SCOPE FLAGS — if a deliverable touches an area the reference doc lists
   as explicitly out of scope (financing components, principal vs. agent,
   multi-currency, leases, ...), it is flagged for human review rather than
   confidently classified.
"""
from __future__ import annotations

import json
import os
import re

from engine.rag import get_doc

MODEL = "claude-opus-4-8"

# ---------------------------------------------------------------------------
# Deterministic confidence heuristic + out-of-scope detection
# ---------------------------------------------------------------------------

# Vocabulary that cleanly matches the reference doc's Step 5 discussion.
_OVER_TIME_WORDS = {"subscription", "support", "maintenance", "hosting", "access",
                    "saas", "platform", "monthly", "service", "sla", "module", "addon"}
_POINT_IN_TIME_WORDS = {"hardware", "terminal", "device", "license", "perpetual",
                        "delivered", "delivery", "migration", "setup", "installation",
                        "integration", "onboarding", "workshop", "milestone", "shipment",
                        "completed", "go-live", "acceptance"}
# Signals that CONTRADICT the declared delivery type (per the Step 5
# misconception paragraph) — these drop confidence.
_ONGOING_WORDS = {"ongoing", "continuous", "updates", "evolving", "recurring"}
_ONE_SHOT_WORDS = {"perpetual", "one-time", "single"}

# Areas the reference doc lists under "What This Project Deliberately Does
# Not Handle" — anything matching these is flagged for human review.
_OUT_OF_SCOPE_PATTERNS = [
    (r"financ(e|ing)|loan|interest", "significant financing components"),
    (r"principal|agent|resell|marketplace|third[- ]party seller", "principal vs. agent determination"),
    (r"multi[- ]currency|foreign currency|eur\b|gbp\b", "multi-currency contract rules"),
    (r"lease|leasing", "leases (ASC 842, outside ASC 606)"),
    (r"combined contract|contract combination", "contract combination rules"),
]


def _words(text: str) -> set[str]:
    # Drop negated phrases ("no ongoing service", "without updates") before
    # matching, so a negation isn't miscounted as a contradicting signal.
    cleaned = re.sub(r"\b(?:no|without|not)\s+\w+(?:\s+\w+)?", " ", text.lower())
    return set(re.findall(r"[a-z']+", cleaned))


def assess_obligation(ob: dict, siblings: list[dict] | None = None) -> dict:
    """Deterministic confidence + review check for one obligation.

    Two concerns are assessed INDEPENDENTLY so the reasons stay accurate:

    * ``confidence`` (high/medium/low) is about the RECOGNITION METHOD and is
      driven ONLY by the deliverable's own description/type. The same
      description therefore always yields the same confidence, regardless of
      the rest of the contract.
    * ``reviews`` is a list of accurate, separately-attributed reasons the
      obligation should get a human look — out-of-scope area, description that
      conflicts with the declared delivery type, a genuinely sparse
      description, or an allocation concern such as a tied standalone selling
      price. Each reason names its true trigger; they are never conflated.

    Returns {"confidence", "confidence_reason", "needs_review", "reviews"}.
    """
    blob = f"{ob['type']} {ob['description']}"
    w = _words(blob)
    reviews: list[str] = []

    # (A) Confidence in the recognition method — description-driven only.
    is_over_time = ob["method"] == "over_time"
    supporting = w & (_OVER_TIME_WORDS if is_over_time else _POINT_IN_TIME_WORDS)
    contradicting = w & (_ONE_SHOT_WORDS if is_over_time else _ONGOING_WORDS)
    out_of_scope = next((area for pat, area in _OUT_OF_SCOPE_PATTERNS
                         if re.search(pat, blob, re.IGNORECASE)), None)

    if out_of_scope:
        confidence = "low"
        confidence_reason = f"Touches an out-of-scope area: {out_of_scope}."
        reviews.append(
            f"The reference doc lists '{out_of_scope}' under its scope boundaries — "
            "flagged for human review rather than auto-classified.")
    elif contradicting:
        confidence = "low"
        confidence_reason = (
            f"Description mentions {', '.join(sorted(contradicting))!s}, which cuts against a "
            f"{'over-time' if is_over_time else 'point-in-time'} classification — the Step 5 "
            "criteria may not map cleanly.")
        reviews.append("Description conflicts with the declared delivery type — confirm the Step 5 criteria.")
    elif supporting:
        # Clear classifying vocabulary present — high confidence regardless of
        # word count. (This check MUST precede the sparse-length heuristic so a
        # short-but-clear description like "Perpetual software license" is not
        # mislabeled as sparse.)
        confidence = "high"
        confidence_reason = (
            "Description clearly maps to the reference doc's "
            f"{'over-time criterion 1 (continuous benefit)' if is_over_time else 'point-in-time control-transfer indicators'}.")
    elif len(ob["description"].split()) < 4:
        confidence = "low"
        confidence_reason = "Description is too sparse to verify distinctness (Step 2) or the Step 5 criteria."
        reviews.append("Deliverable description is too sparse to verify the classification.")
    else:
        confidence = "medium"
        confidence_reason = (
            "Delivery type is stated but the description doesn't use vocabulary that maps "
            "directly onto the Step 5 criteria — classification follows the declared type.")

    # (B) Allocation concern — tied standalone selling prices. This is its own
    # explicit check with its own message; it is NOT conflated with the
    # description checks above, and it does not change the method confidence.
    # (Only meaningful for priced obligations — excluded/unpriced ones are
    # handled separately in add_rationales.)
    if siblings and len(siblings) > 1 and ob.get("standalone_price_estimate") is not None:
        ties = [o for o in siblings
                if o is not ob
                and o.get("standalone_price_estimate") is not None
                and abs(o["standalone_price_estimate"] - ob["standalone_price_estimate"]) < 0.005]
        if ties:
            reviews.append(
                f"Shares an identical standalone selling price "
                f"(${ob['standalone_price_estimate']:,.2f}) with "
                f"{'another obligation' if len(ties) == 1 else f'{len(ties)} other obligations'} — "
                "the relative-SSP allocation (Step 4) may need manual confirmation.")

    return {
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "needs_review": bool(reviews),
        "reviews": reviews,
    }


# ---------------------------------------------------------------------------
# Grounded rationales
# ---------------------------------------------------------------------------

def _citation_for(ob: dict, processed: dict) -> str:
    if ob["method"] == "over_time":
        return ("Step 5, over-time criterion 1 (ASC 606-10-25-27): the customer "
                "simultaneously receives and consumes the benefit as the company performs")
    return ("Step 5, point-in-time recognition: none of the three over-time criteria are met; "
            "control transfers at delivery/acceptance")


def _template_rationale(ob: dict, processed: dict) -> str:
    total = processed["total_price"]
    # Only priced (non-excluded) obligations participate in the allocation.
    ssp_sum = sum(o["standalone_price_estimate"] for o in processed["obligations"]
                  if o.get("standalone_price_estimate") is not None)
    own_ssp = ob.get("standalone_price_estimate") or 0
    pct = own_ssp / ssp_sum * 100 if ssp_sum else 0
    if ob["method"] == "point_in_time":
        method_part = (
            "Recognized at a point in time per the reference guide's Step 5: none of the three "
            "over-time criteria apply — the company's obligation is satisfied in a single moment "
            "when control transfers (delivery/acceptance), even if the customer's own use is gradual."
        )
    else:
        method_part = (
            "Recognized over time under Step 5, criterion 1 (ASC 606-10-25-27): the customer "
            "simultaneously receives and consumes the benefit as the company performs, so revenue "
            "is spread monthly across the service period."
        )
    if len(processed["obligations"]) > 1 and not ob.get("added_by_modification"):
        method_part += (
            f" Per Step 4, it carries {pct:.0f}% of the bundle's standalone selling prices, so it is "
            f"allocated ${ob['allocated_price']:,.2f} of the ${total:,.2f} transaction price — the "
            "bundle discount is spread proportionally, not assigned to one item."
        )
    elif ob.get("added_by_modification"):
        method_part += (
            " Added by a mid-term modification treated as a prospective reallocation (the reference "
            "guide's modification approach 2): remaining unrecognized consideration plus the added "
            "fee were combined and reallocated over the remaining term."
        )
    return method_part


def _claude_rationales(processed: dict) -> dict[str, dict] | None:
    """One grounded API call per contract → {ob_id: {rationale, confidence}} or None."""
    try:
        import anthropic
    except ImportError:
        return None

    doc = get_doc()
    # Retrieve the sections relevant to this contract's features.
    queries = ["performance obligations distinct", "recognize revenue over time point in time criteria",
               "allocate transaction price standalone selling price"]
    if processed.get("modification_note"):
        queries.append("contract modifications prospective reallocation")
    if processed.get("variable_note"):
        queries.append("variable consideration transaction price constraint")
    seen, sections = set(), []
    for q in queries:
        for s in doc.retrieve(q, k=2):
            if s["title"] not in seen:
                seen.add(s["title"])
                sections.append(s)
    grounding = "\n\n".join(f"### {s['title']}\n{s['text']}" for s in sections)

    facts = {
        "contract_id": processed["contract_id"],
        "customer": processed["customer"],
        "term": f"{processed['start_date']} to {processed['end_date']}",
        "total_price": processed["total_price"],
        "obligations": [{k: o[k] for k in ("obligation_id", "type", "description",
                                           "standalone_price_estimate", "allocated_price",
                                           "method", "added_by_modification")}
                        for o in processed["obligations"]],
        "modification_note": processed.get("modification_note"),
        "variable_note": processed.get("variable_note"),
    }
    schema = {
        "type": "object",
        "properties": {"rationales": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "obligation_id": {"type": "string"},
                "rationale": {"type": "string"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            },
            "required": ["obligation_id", "rationale", "confidence"],
            "additionalProperties": False,
        }}},
        "required": ["rationales"],
        "additionalProperties": False,
    }
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=2500,
            system=(
                "You explain the output of a deterministic ASC 606 revenue recognition engine "
                "to a finance reviewer. Ground every statement in the reference guide excerpts "
                "provided — do not rely on general knowledge, and never contradict the excerpts. "
                "For each performance obligation write a 1-2 sentence rationale that names the "
                "specific rule applied (e.g. 'over time under Step 5 criterion 1 (ASC 606-10-25-27)', "
                "'Step 4 relative standalone selling price allocation', 'modification approach 2 — "
                "prospective reallocation'). Also assess confidence: high if the deliverable "
                "description maps cleanly onto the cited criteria, medium if the classification "
                "follows the declared delivery type without clear supporting language, low if the "
                "description is ambiguous or conflicts with the criteria. You are explaining "
                "decisions already made by deterministic rules — never second-guess or change them."
                f"\n\n# Reference guide excerpts\n\n{grounding}"
            ),
            messages=[{"role": "user", "content": json.dumps(facts, indent=2)}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if response.stop_reason == "refusal":
            return None
        text = next(b.text for b in response.content if b.type == "text")
        return {it["obligation_id"]: {"rationale": it["rationale"], "confidence": it["confidence"]}
                for it in json.loads(text)["rationales"]}
    except Exception:
        return None


def add_rationales(processed: dict) -> dict:
    """Attach grounded rationale + confidence + review flags per obligation."""
    ai = _claude_rationales(processed) if os.environ.get("ANTHROPIC_API_KEY") else None
    obligations = processed["obligations"]
    rationales, review_items = {}, []
    for ob in obligations:
        ob_id = ob["obligation_id"]

        # Excluded obligations (flagged during extraction: unpriced bundle,
        # variable consideration, ambiguous timing) are not classified or
        # allocated — they carry the extraction's own review reason straight
        # into the queue at low confidence, and are excluded from all totals.
        if ob.get("excluded"):
            reason = ob.get("review_reason") or "Flagged for human review."
            ob["confidence"] = "low"
            rationales[ob_id] = {
                "text": ("Flagged for review, not auto-classified or allocated: " + reason),
                "source": "extraction flag",
                "rule_citation": "Held out of Steps 4–5 pending human review (no reliable allocation/timing).",
                "confidence": "low",
                "confidence_reason": reason,
            }
            review_items.append({"obligation_id": ob_id, "type": ob["type"],
                                 "description": ob["description"], "reason": reason})
            continue

        assessment = assess_obligation(ob, obligations)

        if ai and ob_id in ai:
            text, source = ai[ob_id]["rationale"], "claude (RAG-grounded)"
            # The AI may nuance the DISPLAYED confidence down one notch, but a
            # priced/included obligation is never dropped to "low" and never
            # added to the review queue on an AI nuance alone. The Needs Review
            # queue is reserved for obligations that genuinely can't be
            # processed (excluded/unpriced) or hit a deterministic red flag
            # (out-of-scope, tied SSP, contradictory or too-sparse text) — so a
            # confidently-priced item doesn't clutter it just because the prose
            # reads a little generic.
            order = {"high": 2, "medium": 1, "low": 0}
            if order[ai[ob_id]["confidence"]] < order[assessment["confidence"]]:
                assessment["confidence"] = "medium" if order[ai[ob_id]["confidence"]] < 1 \
                    else ai[ob_id]["confidence"]
                assessment["confidence_reason"] += " (Adjusted down by AI review of the description.)"
        else:
            text, source = _template_rationale(ob, processed), "template (reference-doc grounded)"

        ob["confidence"] = assessment["confidence"]
        ob["needs_review"] = assessment["needs_review"]
        rationales[ob_id] = {
            "text": text,
            "source": source,
            "rule_citation": _citation_for(ob, processed),
            "confidence": assessment["confidence"],
            "confidence_reason": assessment["confidence_reason"],
        }
        # One Needs-Review entry per accurate reason, so a single obligation
        # can appear for both (say) a tied price and a sparse description
        # without either reason being mislabeled as the other.
        for reason in assessment["reviews"]:
            review_items.append({
                "obligation_id": ob_id,
                "type": ob["type"],
                "description": ob["description"],
                "reason": reason,
            })
    processed["rationales"] = rationales
    processed["needs_review"] = review_items
    return processed
```

### `engine/rag.py`

```python
"""Retrieval over the ASC 606 reference doc (data/asc606_reference_doc.md).

Deliberately simple, deterministic retrieval: the doc is split into sections
by markdown heading, and a query is matched by keyword overlap (with a boost
for heading matches). No embeddings, no external services — at ~9KB of
reference text, transparent keyword scoring is easier to audit than a vector
store and retrieves the right section reliably.

Used by two consumers:
  - engine/explain.py — grounds each obligation's classification rationale
  - the /api/chat endpoint — grounds every chat answer
"""
from __future__ import annotations

import os
import re

_DOC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "asc606_reference_doc.md")

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "be", "to", "of", "and", "or", "in",
    "on", "for", "it", "its", "this", "that", "with", "as", "by", "at", "from",
    "what", "how", "when", "which", "not", "but", "if", "can", "do", "does",
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9']+", text.lower()) if t not in _STOPWORDS]


class ReferenceDoc:
    def __init__(self, path: str = _DOC_PATH):
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        self.sections: list[dict] = []
        title, buf = "Preamble", []
        for line in raw.splitlines():
            if line.startswith("## "):
                if buf:
                    self._add(title, buf)
                title, buf = line[3:].strip(), []
            else:
                buf.append(line)
        self._add(title, buf)

    def _add(self, title: str, buf: list[str]):
        text = "\n".join(buf).strip()
        if text:
            self.sections.append({
                "title": title,
                "text": text,
                "_title_tokens": set(_tokens(title)),
                "_text_tokens": _tokens(text),
            })

    def retrieve(self, query: str, k: int = 3) -> list[dict]:
        """Top-k sections by keyword overlap; heading hits count 3x."""
        q = _tokens(query)
        scored = []
        for s in self.sections:
            score = sum(3 for t in set(q) if t in s["_title_tokens"])
            score += sum(1 for t in q if t in s["_text_tokens"])
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: -x[0])
        return [{"title": s["title"], "text": s["text"]} for _, s in scored[:k]]

    def section(self, title_startswith: str) -> dict | None:
        for s in self.sections:
            if s["title"].lower().startswith(title_startswith.lower()):
                return {"title": s["title"], "text": s["text"]}
        return None

    def full_text(self) -> str:
        return "\n\n".join(f"## {s['title']}\n{s['text']}" for s in self.sections)


_doc: ReferenceDoc | None = None


def get_doc() -> ReferenceDoc:
    global _doc
    if _doc is None:
        _doc = ReferenceDoc()
    return _doc
```

### `data/seed_contracts.json`

```json
[
  {
    "contract_id": "ORD-2601",
    "customer": "Cedar Grove Financial",
    "start_date": "2026-01-01",
    "end_date": "2026-12-31",
    "total_price": 72000,
    "deliverables": [
      {
        "type": "subscription",
        "description": "Access to the Northstar Analytics Platform for a period of twelve (12) months",
        "standalone_price_estimate": 72000,
        "delivery_type": "over_time"
      }
    ]
  },
  {
    "contract_id": "ORD-2602",
    "customer": "Harborview Clinics",
    "start_date": "2026-02-10",
    "end_date": "2026-02-10",
    "total_price": 24000,
    "deliverables": [
      {
        "type": "hardware",
        "description": "Thirty (30) Ironclad payment terminal units delivered upon execution of this order",
        "standalone_price_estimate": 24000,
        "delivery_type": "one_time"
      }
    ]
  },
  {
    "contract_id": "ORD-2603",
    "customer": "Talbot & Reyes LLP",
    "start_date": "2026-03-03",
    "end_date": "2026-03-03",
    "total_price": 40000,
    "deliverables": [
      {
        "type": "license",
        "description": "Perpetual license to the Meridian practice-management software",
        "standalone_price_estimate": null,
        "delivery_type": "unknown",
        "review": {
          "kind": "unpriced_bundle",
          "reason": "Standalone selling price not stated in the contract text; the bundle's single aggregate fee ($40,000) cannot be split between the license and the onboarding/configuration services without a pricing basis from a source outside this document.",
          "excluded_amount": null
        }
      },
      {
        "type": "implementation",
        "description": "Onboarding and configuration services provided together with the license",
        "standalone_price_estimate": null,
        "delivery_type": "unknown",
        "review": {
          "kind": "unpriced_bundle",
          "reason": "Standalone selling price not stated in the contract text; the bundle's single aggregate fee ($40,000) cannot be split between the license and the onboarding/configuration services without a pricing basis from a source outside this document.",
          "excluded_amount": null
        }
      }
    ]
  },
  {
    "contract_id": "ORD-2604",
    "customer": "Pinnacle Manufacturing",
    "start_date": "2026-01-01",
    "end_date": "2027-12-31",
    "total_price": 72000,
    "deliverables": [
      {
        "type": "support",
        "description": "Ongoing maintenance and support of the Blue Ridge control systems for a term of twenty-four (24) months, billed at a recurring monthly rate",
        "standalone_price_estimate": 72000,
        "delivery_type": "over_time"
      }
    ],
    "modification": {
      "date": "2026-12-01",
      "description": "In month 12 of the Term, Customer expanded coverage to add Premium Priority Support for the remainder of the Term",
      "added_price": 18000,
      "added_deliverable": {
        "type": "support_addon",
        "description": "Premium Priority Support coverage for the remaining twelve (12) months of the Term",
        "standalone_price_estimate": 18000,
        "delivery_type": "over_time"
      }
    }
  },
  {
    "contract_id": "ORD-2605",
    "customer": "Coastline Retail",
    "start_date": "2026-04-15",
    "end_date": "2026-04-15",
    "total_price": 50000,
    "deliverables": [
      {
        "type": "license",
        "description": "Perpetual software license to the Vertex point-of-sale suite, separately priced at $30,000",
        "standalone_price_estimate": 30000,
        "delivery_type": "one_time"
      },
      {
        "type": "implementation",
        "description": "Implementation and store configuration services (remainder of the total contract value)",
        "standalone_price_estimate": 20000,
        "delivery_type": "one_time"
      }
    ]
  },
  {
    "contract_id": "ORD-2606",
    "customer": "Fenwick Holdings",
    "start_date": "2026-06-04",
    "end_date": "2026-06-04",
    "total_price": 22500,
    "deliverables": [
      {
        "type": "professional_services",
        "description": "A written market-assessment report delivered within thirty (30) days of execution, for a flat fee",
        "standalone_price_estimate": 22500,
        "delivery_type": "one_time"
      }
    ]
  },
  {
    "contract_id": "ORD-2607",
    "customer": "Ashford Group",
    "start_date": "2026-02-01",
    "end_date": "2027-01-31",
    "total_price": 48000,
    "deliverables": [
      {
        "type": "platform_fee",
        "description": "Annual platform fee for use of the Quantum Ledger Services during the twelve (12) month Term",
        "standalone_price_estimate": 48000,
        "delivery_type": "over_time"
      }
    ]
  },
  {
    "contract_id": "ORD-2608",
    "customer": "Riverstone Markets",
    "start_date": "2026-03-01",
    "end_date": "2027-02-28",
    "total_price": 36000,
    "deliverables": [
      {
        "type": "license",
        "description": "Perpetual license to the Nimbus Payments software, separately priced at $24,000",
        "standalone_price_estimate": 24000,
        "delivery_type": "one_time"
      },
      {
        "type": "support",
        "description": "Twelve (12) months of support and platform maintenance, separately priced at $12,000",
        "standalone_price_estimate": 12000,
        "delivery_type": "over_time"
      }
    ]
  },
  {
    "contract_id": "ORD-2609",
    "customer": "Grandview Health",
    "start_date": "2026-06-01",
    "end_date": "2026-06-01",
    "total_price": 150000,
    "deliverables": [
      {
        "type": "license",
        "description": "Perpetual license to the Apex clinical platform",
        "standalone_price_estimate": null,
        "delivery_type": "unknown",
        "review": {
          "kind": "unpriced_bundle",
          "reason": "Three distinct components (license, one-time data migration, and ongoing support) are sold under a single total contract value of $150,000 with no per-component pricing. There is no reasonable basis in the document to allocate the price across the three obligations — flagged for human review.",
          "excluded_amount": null
        }
      },
      {
        "type": "professional_services",
        "description": "One-time data migration of existing clinical records",
        "standalone_price_estimate": null,
        "delivery_type": "unknown",
        "review": {
          "kind": "unpriced_bundle",
          "reason": "Three distinct components (license, one-time data migration, and ongoing support) are sold under a single total contract value of $150,000 with no per-component pricing. There is no reasonable basis in the document to allocate the price across the three obligations — flagged for human review.",
          "excluded_amount": null
        }
      },
      {
        "type": "support",
        "description": "Ongoing support and maintenance over a twenty-four (24) month term",
        "standalone_price_estimate": null,
        "delivery_type": "unknown",
        "review": {
          "kind": "unpriced_bundle",
          "reason": "Three distinct components (license, one-time data migration, and ongoing support) are sold under a single total contract value of $150,000 with no per-component pricing. There is no reasonable basis in the document to allocate the price across the three obligations — flagged for human review.",
          "excluded_amount": null
        }
      }
    ]
  },
  {
    "contract_id": "ORD-2610",
    "customer": "Lakeside Foods",
    "start_date": "2026-02-04",
    "end_date": "2026-02-04",
    "total_price": 15000,
    "deliverables": [
      {
        "type": "equipment",
        "description": "Refrigeration equipment delivered and installed within fifteen (15) days of the purchase order",
        "standalone_price_estimate": 15000,
        "delivery_type": "one_time"
      }
    ]
  }
]
```

### `static/index.html`

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ASC 606 Revenue Recognition Engine</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  :root {
    /* Clean, light palette in the spirit of ramp.com: warm off-white page,
       white cards, near-black ink, lime accent used sparingly. */
    --bg: #faf9f6; --panel: #ffffff; --panel2: #f4f3ee; --border: #e6e4dd;
    --text: #1a1a1a; --muted: #6f6d66; --ink: #1a1a1a;
    --lime: #d3f26a; --lime-soft: #eef9d3; --green: #1e7a4d;
    --amber: #b45309; --amber-soft: #fdf0d9; --red: #b3372f; --red-soft: #fbe4e1;
    --violet: #5b4bb5; --violet-soft: #eceafa; --blue: #2563eb; --blue-soft: #e3ecfc;
  }
  * { box-sizing: border-box; margin: 0; }
  body { background: var(--bg); color: var(--text); font: 14px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; padding: 24px; }
  h1 { font-size: 22px; letter-spacing: -0.01em; } h2 { font-size: 16px; margin-bottom: 12px; }
  .sub { color: var(--muted); margin: 4px 0 20px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  @media (max-width: 1000px) { .grid { grid-template-columns: 1fr; } }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 16px;
          box-shadow: 0 1px 2px rgba(26,26,26,.04); }
  .card.full { grid-column: 1 / -1; }
  .kpis { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
  .kpi { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 12px 18px;
         min-width: 170px; box-shadow: 0 1px 2px rgba(26,26,26,.04); }
  .kpi .v { font-size: 20px; font-weight: 700; color: var(--ink); }
  .kpi .l { color: var(--muted); font-size: 12px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  tr.clickable { cursor: pointer; }
  tr.clickable:hover { background: var(--panel2); }
  tr.selected { background: var(--lime-soft); outline: 1px solid var(--ink); }
  .badge { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
  .badge.one_time { background: var(--blue-soft); color: var(--blue); }
  .badge.subscription { background: var(--lime-soft); color: var(--green); }
  .badge.bundled { background: var(--violet-soft); color: var(--violet); }
  .badge.modification { background: var(--amber-soft); color: var(--amber); }
  .badge.variable { background: var(--red-soft); color: var(--red); }
  .filters button, .controls button, .controls select, .controls input {
    background: var(--panel); color: var(--text); border: 1px solid var(--border);
    border-radius: 8px; padding: 5px 12px; font-size: 13px; cursor: pointer;
  }
  .filters button.active { background: var(--ink); border-color: var(--ink); color: var(--lime); }
  #uploadBtn { background: var(--ink); color: var(--lime); border-color: var(--ink); font-weight: 600; }
  #runClose { background: var(--ink); color: #fff; border-color: var(--ink); }
  .controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
  #detail { display: none; }
  #detail.open { display: block; }
  .note { background: var(--amber-soft); border-left: 3px solid var(--amber); padding: 8px 12px; border-radius: 0 8px 8px 0; margin: 8px 0; font-size: 13px; }
  .rationale { background: var(--panel2); border-left: 3px solid var(--ink); padding: 8px 12px; border-radius: 0 8px 8px 0; margin: 6px 0; font-size: 13px; }
  .rationale .src { color: var(--muted); font-size: 11px; }
  .flag { background: var(--red-soft); border-left: 3px solid var(--red); padding: 8px 12px; border-radius: 0 8px 8px 0; margin: 6px 0; font-size: 13px; }
  .ok { color: var(--green); font-size: 13px; }
  .disclaimer { color: var(--muted); font-size: 12px; margin-top: 8px; font-style: italic; }
  #uploadStatus { font-size: 13px; }
  #uploadStatus.err { color: var(--red); } #uploadStatus.ok { color: var(--green); }
  .scroll { max-height: 420px; overflow-y: auto; }
  .chartbox { position: relative; height: 300px; }
  button.del { background: none; border: none; cursor: pointer; font-size: 15px; padding: 2px 6px; border-radius: 6px; opacity: .55; }
  button.del:hover { opacity: 1; background: var(--red-soft); }
  .chip { display: inline-block; padding: 0 7px; border-radius: 999px; font-size: 11px; font-weight: 600; margin-left: 6px; }
  .chip.high { background: var(--lime-soft); color: var(--green); }
  .chip.medium { background: var(--amber-soft); color: var(--amber); }
  .chip.low { background: var(--red-soft); color: var(--red); }
  .cite { color: var(--muted); font-size: 12px; margin-top: 3px; }
  #chatLog { max-height: 320px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; }
  .msg { max-width: 85%; padding: 8px 12px; border-radius: 10px; font-size: 13px; white-space: pre-wrap; }
  .msg.user { align-self: flex-end; background: var(--ink); color: #fff; }
  .msg.bot { align-self: flex-start; background: var(--panel2); border: 1px solid var(--border); }
  .msg.bot .cite { margin-top: 6px; }
  #chatForm { display: flex; gap: 8px; }
  #chatInput { flex: 1; background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; font-size: 13px; }
  #chatSend { background: var(--ink); color: var(--lime); border: 1px solid var(--ink); border-radius: 8px; padding: 8px 16px; font-weight: 600; cursor: pointer; }
  #excludedNote { background: var(--amber-soft); border: 1px solid #e7c48a; color: #7a4a08; border-radius: 12px; padding: 12px 16px; margin-bottom: 16px; font-size: 13px; display: none; }
  #excludedNote.show { display: block; }
  .chip.pending { background: var(--amber-soft); color: var(--amber); }
  .resolvebox { background: var(--panel2); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin: 8px 0; }
  .resolvebox .row { display: flex; gap: 8px; align-items: center; margin: 6px 0; flex-wrap: wrap; }
  .resolvebox input, .resolvebox select { background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 5px 8px; font-size: 13px; }
  .resolvebox button { background: var(--ink); color: var(--lime); border: none; border-radius: 6px; padding: 6px 14px; font-weight: 600; cursor: pointer; }
</style>
</head>
<body>

<h1>ASC 606 Revenue Recognition Engine</h1>
<p class="sub">Revenue recognition for B2B contracts read from realistic contract PDFs — the extractor reasons about
each obligation (point-in-time vs. over-time) from the prose, grounded in the ASC 606 three-criteria test. Performance
obligations, price allocation, journal entries, deferred revenue, automated month-end close, and known-revenue forecasting.</p>

<div class="kpis" id="kpis"></div>

<div id="excludedNote"></div>

<div class="grid">
  <div class="card full">
    <h2>Total Deferred Revenue Balance (Balance Sheet Liability, All Contracts)</h2>
    <div class="chartbox"><canvas id="drChart"></canvas></div>
  </div>
  <div class="card">
    <h2>Revenue Recognized by Month — Point-in-Time vs. Over-Time vs. Usage</h2>
    <div class="chartbox"><canvas id="methodChart"></canvas></div>
  </div>
  <div class="card">
    <h2>Known Revenue Forecast — Contracted Revenue Only (RPO)</h2>
    <div class="chartbox"><canvas id="rpoChart"></canvas></div>
    <p class="disclaimer" id="rpoDisclaimer"></p>
  </div>
</div>

<div class="grid">
  <div class="card full">
    <div class="controls">
      <h2 style="margin:0; flex:1;">Month-End Close Batch</h2>
      <label for="closeMonth" style="color:var(--muted)">Close Month:</label>
      <input type="month" id="closeMonth">
      <button id="runClose">Generate Batch</button>
      <button id="downloadCsv" title="SAP-style journal entry upload file: posting keys, G/L accounts, cost center, profit center">⬇ Download SAP CSV</button>
    </div>
    <div id="closeSummary" class="sub"></div>
    <div id="closeFlags"></div>
    <div class="scroll"><table id="closeTable">
      <thead><tr><th>Contract</th><th>Customer</th><th>Date</th><th>Debit</th><th>Credit</th><th class="num">Amount</th><th>Memo</th></tr></thead>
      <tbody></tbody>
    </table></div>
  </div>
</div>

<div class="grid">
  <div class="card full">
    <h2>Needs Review</h2>
    <p class="sub" style="margin-bottom:8px">Obligations whose descriptions don't map cleanly to the reference guide's criteria — or that touch an out-of-scope area — are flagged here for human review instead of being silently auto-processed.</p>
    <div id="reviewList"></div>
  </div>
</div>

<div class="grid">
  <div class="card full">
    <div class="controls">
      <h2 style="margin:0; flex:1;">Contracts</h2>
      <div class="filters" id="filters">
        <button data-f="all" class="active">All</button>
        <button data-f="one_time">One-Time</button>
        <button data-f="subscription">Subscription</button>
        <button data-f="bundled">Bundled</button>
        <button data-f="modification">⚠ Modified</button>
        <button data-f="variable">≈ Variable</button>
      </div>
      <a href="/static/sample_contract.pdf" download style="color:var(--muted); font-size:13px;">Sample Contract (PDF)</a>
      <input type="file" id="uploadFile" accept=".pdf,.json" style="display:none">
      <button id="uploadBtn">⬆ Upload Contract (PDF)</button>
      <span id="uploadStatus"></span>
    </div>
    <div class="scroll"><table id="contractTable">
      <thead><tr>
        <th>ID</th><th>Customer</th><th>Category</th><th>Term</th>
        <th class="num">Total Price</th><th class="num">Obligations</th><th>Methods</th><th></th>
      </tr></thead>
      <tbody></tbody>
    </table></div>
  </div>

  <div class="card full" id="detail">
    <h2 id="detailTitle"></h2>
    <div id="detailBody"></div>
  </div>
</div>

<div class="grid">
  <div class="card full">
    <h2>Ask About ASC 606 or This Data</h2>
    <p class="sub" style="margin-bottom:8px">Answers are grounded in the ASC 606 reference guide and the contracts loaded above — questions outside that scope are declined by design.</p>
    <div id="chatLog"></div>
    <form id="chatForm">
      <input id="chatInput" type="text" maxlength="2000" placeholder="e.g. Why is C-013's license point-in-time but its support over-time?" autocomplete="off">
      <button id="chatSend" type="submit">Send</button>
    </form>
  </div>
</div>

<script>
const fmt = n => "$" + n.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const fmt0 = n => "$" + Math.round(n).toLocaleString("en-US");
let contracts = [], filter = "all", selectedId = null;
let drChart, methodChart, rpoChart;

Chart.defaults.color = "#6f6d66";
Chart.defaults.borderColor = "#e6e4dd";

async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error(await r.text()); return r.json(); }

async function loadAll() {
  const [c, agg, rpo] = await Promise.all([
    getJSON("/api/contracts"), getJSON("/api/aggregates"), getJSON("/api/forecast?months=12"),
  ]);
  contracts = c.contracts;
  renderKPIs(agg, rpo);
  renderExcludedNote(agg.excluded_pending);
  renderDR(agg.deferred_revenue);
  renderMethod(agg.recognized_by_method);
  renderRPO(rpo);
  renderTable();
  renderReview();
  if (selectedId) { const p = contracts.find(x => x.contract_id === selectedId); if (p) renderDetail(p); }
}

function renderExcludedNote(ep) {
  const el = document.getElementById("excludedNote");
  if (!ep || ep.total_excluded <= 0) { el.classList.remove("show"); el.innerHTML = ""; return; }
  const names = ep.items.map(i => i.contract_id).join(", ");
  el.innerHTML = `<b>⚑ ${fmt(ep.total_excluded)} excluded from the totals pending review.</b>
    The deferred revenue, forecast, and close figures above intentionally leave out ${ep.items.length}
    contract(s) — ${names} — whose obligations couldn't be priced or classified with confidence from the
    contract text. They stay out of the numbers until a person resolves them, so the totals are
    deliberately incomplete rather than padded with a guess.`;
  el.classList.add("show");
}

function renderKPIs(agg, rpo) {
  const now = new Date().toISOString().slice(0, 7);
  const cur = agg.deferred_revenue.find(r => r.month === now) || agg.deferred_revenue.at(-1);
  document.getElementById("kpis").innerHTML = `
    <div class="kpi"><div class="v">${agg.contract_count}</div><div class="l">Contracts</div></div>
    <div class="kpi"><div class="v">${fmt0(cur ? cur.deferred_revenue : 0)}</div><div class="l">Deferred Revenue — ${cur ? cur.month : "n/a"}</div></div>
    <div class="kpi"><div class="v">${fmt0(rpo.total_known_revenue)}</div><div class="l">Known Revenue, Next 12 Months (RPO)</div></div>`;
}

function renderDR(series) {
  if (drChart) drChart.destroy();
  drChart = new Chart(document.getElementById("drChart"), {
    type: "line",
    data: { labels: series.map(r => r.month), datasets: [{
      label: "Deferred revenue", data: series.map(r => r.deferred_revenue),
      borderColor: "#1a1a1a", backgroundColor: "rgba(211,242,106,.45)", fill: true, tension: .25, pointRadius: 2,
    }]},
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { y: { ticks: { callback: v => fmt0(v) } } } },
  });
}

function renderMethod(series) {
  if (methodChart) methodChart.destroy();
  methodChart = new Chart(document.getElementById("methodChart"), {
    type: "bar",
    data: { labels: series.map(r => r.month), datasets: [
      { label: "Point-in-time", data: series.map(r => r.point_in_time), backgroundColor: "#1a1a1a" },
      { label: "Over-time", data: series.map(r => r.over_time), backgroundColor: "#c9e85c" },
      { label: "Usage (Variable)", data: series.map(r => r.usage), backgroundColor: "#e0907f" },
    ]},
    options: { maintainAspectRatio: false,
      scales: { x: { stacked: true }, y: { stacked: true, ticks: { callback: v => fmt0(v) } } } },
  });
}

function renderRPO(rpo) {
  document.getElementById("rpoDisclaimer").textContent = rpo.disclaimer;
  if (rpoChart) rpoChart.destroy();
  rpoChart = new Chart(document.getElementById("rpoChart"), {
    type: "line",
    data: { labels: rpo.monthly_totals.map(r => r.month), datasets: [{
      label: "Known contracted revenue", data: rpo.monthly_totals.map(r => r.known_revenue),
      borderColor: "#1e7a4d", backgroundColor: "rgba(30,122,77,.12)", fill: true, tension: .25, pointRadius: 2,
    }]},
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { y: { ticks: { callback: v => fmt0(v) } } } },
  });
}

const BADGE_LABEL = { one_time: "One-Time", subscription: "Subscription", bundled: "Bundled",
                      modification: "⚠ Modified", variable: "≈ Variable" };

function methodsCell(p) {
  const methods = new Set();
  let pending = false;
  p.obligations.forEach(o => {
    if (o.excluded) pending = true;
    else methods.add(o.method === "point_in_time" ? "PIT" : "OT");
  });
  let s = [...methods].join(" + ");
  if (pending) s += (s ? " · " : "") + '<span class="chip pending">⚑ review</span>';
  return s || "—";
}

function renderTable() {
  const tbody = document.querySelector("#contractTable tbody");
  const rows = contracts.filter(p => filter === "all" || p.category === filter);
  tbody.innerHTML = rows.map(p => `
    <tr class="clickable ${p.contract_id === selectedId ? "selected" : ""}" data-id="${p.contract_id}">
      <td>${p.contract_id}</td><td>${p.customer}</td>
      <td><span class="badge ${p.category}">${BADGE_LABEL[p.category]}</span></td>
      <td>${p.start_date} → ${p.end_date}</td>
      <td class="num">${fmt(p.total_price)}</td>
      <td class="num">${p.obligations.length}</td>
      <td>${methodsCell(p)}</td>
      <td>${p.deletable ? `<button class="del" data-del="${p.contract_id}" title="${p.is_override ? "Revert to the flagged sample" : "Delete this contract"}">${p.is_override ? "↺" : "🗑"}</button>` : ""}</td>
    </tr>`).join("");
  tbody.querySelectorAll("tr").forEach(tr => tr.addEventListener("click", e => {
    if (e.target.classList.contains("del")) return;  // delete handled separately
    selectedId = tr.dataset.id;
    renderDetail(contracts.find(p => p.contract_id === selectedId));
    renderTable();
  }));
  tbody.querySelectorAll("button.del").forEach(btn => btn.addEventListener("click", async e => {
    e.stopPropagation();
    await deleteContract(btn.dataset.del);
  }));
}

async function deleteContract(id) {
  const c = contracts.find(x => x.contract_id === id);
  const isOverride = c && c.is_override;
  const prompt = isOverride
    ? `Revert ${id} to the flagged sample contract? Your resolved values will be discarded.`
    : `Are you sure you want to delete contract ${id}? This cannot be undone.`;
  if (!confirm(prompt)) return;
  const status = document.getElementById("uploadStatus");
  status.className = ""; status.textContent = `${isOverride ? "Reverting" : "Deleting"} ${id}…`;
  try {
    const r = await fetch(`/api/contracts/${encodeURIComponent(id)}`, { method: "DELETE" });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || r.statusText);
    if (selectedId === id) { selectedId = null; document.getElementById("detail").classList.remove("open"); }
    status.className = "ok";
    status.textContent = data.reverted
      ? `✓ ${id} reverted to the flagged sample — totals updated.`
      : `✓ ${id} deleted — charts, forecast, close batch, and review queue updated.`;
    await loadAll();       // re-pulls contracts + aggregates + RPO
    await runClose();      // re-pulls the close batch for the selected month
  } catch (err) {
    status.className = "err"; status.textContent = "✗ " + err.message;
  }
}

function renderReview() {
  const items = contracts.flatMap(p =>
    (p.needs_review || []).map(r => ({ ...r, contract_id: p.contract_id, customer: p.customer })));
  document.getElementById("reviewList").innerHTML = items.length
    ? items.map(r => `<div class="flag"><b>${r.contract_id}</b> (${r.customer}) — ${r.obligation_id}, ${r.type}: ${r.reason}</div>`).join("")
    : `<div class="ok">✓ All obligations classified with sufficient confidence — nothing is awaiting review.</div>`;
}

function renderDetail(p) {
  const el = document.getElementById("detail");
  el.classList.add("open");
  document.getElementById("detailTitle").textContent =
    `${p.contract_id} — ${p.customer} (${p.start_date} → ${p.end_date}, ${fmt(p.total_price)})`;
  const money = v => (v === null || v === undefined) ? "—" : fmt(v);
  let html = `
    <h3 style="margin:8px 0">Performance Obligations & Price Allocation</h3>
    <table><thead><tr><th>Obligation</th><th>Type</th><th>Method</th>
      <th class="num">Standalone Price</th><th class="num">Allocated Price</th></tr></thead><tbody>
    ${p.obligations.map(o => `<tr>
      <td>${o.obligation_id}${o.added_by_modification ? ' <span class="badge modification">added by mod</span>' : ""}</td>
      <td>${o.type}</td><td>${o.excluded ? '<span class="chip pending">pending review</span>' : o.method}</td>
      <td class="num">${money(o.standalone_price_estimate)}</td>
      <td class="num">${money(o.allocated_price)}</td></tr>`).join("")}
    </tbody></table>`;
  html += p.obligations.map(o => {
    const r = p.rationales[o.obligation_id];
    if (!r) return "";
    const conf = r.confidence ? `<span class="chip ${r.confidence}">${r.confidence} confidence</span>` : "";
    const cite = r.rule_citation ? `<div class="cite">Rule applied: ${r.rule_citation}</div>` : "";
    const cls = o.excluded ? "flag" : "rationale";
    return `<div class="${cls}"><b>${o.obligation_id}</b>${conf} — ${r.text}${cite}
      <div class="src">Rationale source: ${r.source}</div></div>`;
  }).join("");
  if (p.modification_note) html += `<div class="note">⚠ ${p.modification_note}</div>`;
  if (p.variable_note) html += `<div class="note">≈ ${p.variable_note}</div>`;

  // Resolve form — shown for ANY contract with flagged obligations, seeds
  // included. Resolving a shared seed creates a private override (your fix,
  // your view); other visitors still see it flagged.
  const flagged = p.obligations.filter(o => o.excluded);
  if (flagged.length) {
    html += `<div class="resolvebox"><b>Provide the missing data (human review)</b> — supply the standalone
      price and delivery timing for each flagged obligation, and it will flow into the deferred revenue,
      forecast, and close totals.${p.deletable ? "" : " Resolving this sample contract saves a private copy for your session only."}
      ${flagged.map(o => {
        const n = o.obligation_id.split("-OB")[1];
        return `<div class="row" data-ob="${n}">
          <span style="min-width:180px">${o.obligation_id} — ${o.type}</span>
          <input type="number" min="0" step="0.01" placeholder="Standalone price" class="rprice">
          <select class="rtype"><option value="over_time">over_time</option><option value="one_time">one_time</option></select>
        </div>`;
      }).join("")}
      <div class="row"><button id="resolveBtn" data-id="${p.contract_id}">Resolve & include in totals</button></div>
    </div>`;
  }
  html += `
    <h3 style="margin:14px 0 8px">Deferred Revenue by Month</h3>
    <div class="scroll"><table><thead><tr><th>Month</th><th class="num">Beginning</th>
      <th class="num">Cash Received</th><th class="num">Recognized</th><th class="num">Ending</th></tr></thead><tbody>
    ${p.deferred_revenue.map(r => `<tr><td>${r.month}</td>
      <td class="num">${fmt(r.beginning_balance)}</td><td class="num">${fmt(r.cash_received)}</td>
      <td class="num">${fmt(r.revenue_recognized)}</td><td class="num">${fmt(r.ending_balance)}</td></tr>`).join("")}
    </tbody></table></div>
    <h3 style="margin:14px 0 8px">Journal Entries</h3>
    <div class="scroll"><table><thead><tr><th>Date</th><th>Type</th><th>Debit</th><th>Credit</th>
      <th class="num">Amount</th><th>Memo</th></tr></thead><tbody>
    ${p.journal_entries.map(j => `<tr><td>${j.date}</td><td>${j.entry_type}</td>
      <td>${j.debit_account}</td><td>${j.credit_account}</td>
      <td class="num">${fmt(j.amount)}</td><td>${j.memo}</td></tr>`).join("")}
    </tbody></table></div>`;
  document.getElementById("detailBody").innerHTML = html;
  const rb = document.getElementById("resolveBtn");
  if (rb) rb.addEventListener("click", () => resolveContract(rb.dataset.id));
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function resolveContract(id) {
  const resolutions = {};
  document.querySelectorAll("#detailBody .resolvebox .row[data-ob]").forEach(row => {
    const price = row.querySelector(".rprice").value;
    const dtype = row.querySelector(".rtype").value;
    if (price) resolutions[row.dataset.ob] = { standalone_price: parseFloat(price), delivery_type: dtype };
  });
  if (!Object.keys(resolutions).length) { alert("Enter at least one standalone price."); return; }
  const status = document.getElementById("uploadStatus");
  status.className = ""; status.textContent = `Resolving ${id}…`;
  try {
    const r = await fetch(`/api/contracts/${encodeURIComponent(id)}/resolve`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resolutions }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || r.statusText);
    status.className = "ok";
    status.textContent = `✓ ${id} resolved — now included in the deferred revenue, forecast, and close totals.`;
    await loadAll(); await runClose();
  } catch (err) {
    status.className = "err"; status.textContent = "✗ " + err.message;
  }
}

async function runClose() {
  const month = document.getElementById("closeMonth").value;
  const batch = await getJSON(`/api/close-batch?month=${month}`);
  document.getElementById("closeSummary").textContent =
    `${batch.entry_count} entries for ${batch.close_month} — ${fmt(batch.total_recognized)} recognized from deferred revenue` +
    (batch.total_usage_billed ? `, ${fmt(batch.total_usage_billed)} usage billed` : "") +
    ". Generated from stored schedules — no per-contract lookup required.";
  document.getElementById("closeFlags").innerHTML = batch.flags.length
    ? batch.flags.map(f => `<div class="flag"><b>${f.contract_id}</b> — ${f.message}</div>`).join("")
    : `<div class="ok">✓ Control check passed — no contracts with missed or incomplete recognition.</div>`;
  document.querySelector("#closeTable tbody").innerHTML = batch.entries.map(e => `
    <tr><td>${e.contract_id}</td><td>${e.customer}</td><td>${e.date}</td>
      <td>${e.debit_account}</td><td>${e.credit_account}</td>
      <td class="num">${fmt(e.amount)}</td><td>${e.memo}</td></tr>`).join("");
}

document.getElementById("filters").addEventListener("click", e => {
  if (e.target.tagName !== "BUTTON") return;
  filter = e.target.dataset.f;
  document.querySelectorAll("#filters button").forEach(b => b.classList.toggle("active", b === e.target));
  renderTable();
});

document.getElementById("uploadBtn").addEventListener("click", () => document.getElementById("uploadFile").click());
document.getElementById("uploadFile").addEventListener("change", async e => {
  const file = e.target.files[0];
  if (!file) return;
  const status = document.getElementById("uploadStatus");
  status.className = ""; status.textContent = "Processing…";
  try {
    const fd = new FormData();
    fd.append("file", file);
    const r = await fetch("/api/contracts", { method: "POST", body: fd });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || r.statusText);
    status.className = "ok";
    status.textContent = `✓ ${data.contract_id} added — charts, close batch, and forecast updated.`;
    selectedId = data.contract_id;
    await loadAll();
    await runClose();
  } catch (err) {
    status.className = "err"; status.textContent = "✗ " + err.message;
  }
  e.target.value = "";
});
document.getElementById("runClose").addEventListener("click", runClose);
document.getElementById("downloadCsv").addEventListener("click", () => {
  const month = document.getElementById("closeMonth").value;
  window.location.href = `/api/close-batch.csv?month=${month}`;
});

// --- Scoped chat agent ---
const chatHistory = [];
function addMsg(role, text, sections) {
  const log = document.getElementById("chatLog");
  const div = document.createElement("div");
  div.className = `msg ${role === "user" ? "user" : "bot"}`;
  div.textContent = text;
  if (sections && sections.length) {
    const cite = document.createElement("div");
    cite.className = "cite";
    cite.textContent = "Grounded in: " + sections.join(" · ");
    div.appendChild(cite);
  }
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}
document.getElementById("chatForm").addEventListener("submit", async e => {
  e.preventDefault();
  const input = document.getElementById("chatInput");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  addMsg("user", message);
  const thinking = document.createElement("div");
  thinking.className = "msg bot"; thinking.textContent = "…";
  document.getElementById("chatLog").appendChild(thinking);
  try {
    const r = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history: chatHistory.slice(-8) }),
    });
    const data = await r.json();
    thinking.remove();
    if (!r.ok) throw new Error(data.error || r.statusText);
    addMsg("assistant", data.reply, data.retrieved_sections);
    chatHistory.push({ role: "user", content: message }, { role: "assistant", content: data.reply });
  } catch (err) {
    thinking.remove();
    addMsg("assistant", "⚠ " + err.message);
  }
});

(async () => {
  document.getElementById("closeMonth").value = new Date().toISOString().slice(0, 7);
  await loadAll();
  await runClose();
})();
</script>
</body>
</html>
```

### `scripts/generate_report.py`

```python
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
```

### `scripts/make_seed_pdfs.py`

```python
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
```

### `scripts/make_sample_pdf.py`

```python
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
```

### `scripts/test_bugfixes.py`

```python
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
print("\nProse seed set — flagging, exclusion & modification")
# ---------------------------------------------------------------------------
import json
from engine.core import excluded_pending, rpo_forecast
seeds = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "data", "seed_contracts.json")))
sp = [add_rationales(process_contract(s)) for s in seeds]
by_id = {p["contract_id"]: p for p in sp}

check("exactly 10 seed contracts", len(sp) == 10, str(len(sp)))
# Exactly two contracts require human review; both are unpriced bundles that
# must NOT come out with a confident, silent classification.
for cid in ("ORD-2603", "ORD-2609"):
    p = by_id[cid]
    check(f"{cid} fully flagged (no recognized amount)",
          p["recognized_amount"] == 0 and all(o["excluded"] for o in p["obligations"]),
          f"recognized={p['recognized_amount']}")
# The fully-priced bundle allocates cleanly (no flag).
c8 = by_id["ORD-2608"]
check("ORD-2608 fully-priced bundle allocates cleanly (no flag)",
      c8["recognized_amount"] == 36000 and not any(o["excluded"] for o in c8["obligations"]))

# Flagged obligations are excluded from every aggregate.
ep = excluded_pending(sp)
check("excluded-pending total = $190,000 across 2 contracts",
      ep["total_excluded"] == 190000 and len(ep["items"]) == 2, str(ep["total_excluded"]))
rpo = rpo_forecast(sp, "2026-06", 12)
check("flagged contracts absent from RPO forecast",
      "ORD-2603" not in rpo["by_contract"] and "ORD-2609" not in rpo["by_contract"])

# Modification on seed #4 is applied (prospective reallocation), not dropped.
c4 = by_id["ORD-2604"]
check("ORD-2604 modification applied (2 obligations + note)",
      len(c4["obligations"]) == 2 and c4["modification_note"] is not None)

# Every contract's deferred revenue drains to zero (included portions tie out).
check("all seed contracts: final deferred revenue = 0",
      all(abs(p["deferred_revenue"][-1]["ending_balance"] if p["deferred_revenue"] else 0) < 0.01 for p in sp))

# Clean (non-flagged) obligations are confidently classified (high or medium,
# never low); flagged obligations are low. Unambiguous ones (#1 SaaS, #2/#10
# hardware) should be high specifically.
clean_not_low = all(o["confidence"] in ("high", "medium")
                    for p in sp for o in p["obligations"] if not o.get("excluded"))
check("clean obligations classified confidently (not low)", clean_not_low)
unambiguous_high = all(by_id[cid]["obligations"][0]["confidence"] == "high"
                       for cid in ("ORD-2601", "ORD-2602", "ORD-2610"))
check("unambiguous SaaS/hardware obligations are high confidence", unambiguous_high)
flagged_low = all(o["confidence"] == "low"
                  for p in sp for o in p["obligations"] if o.get("excluded"))
check("all flagged obligations are low confidence", flagged_low)

# ---------------------------------------------------------------------------
print("\nBackward compatibility — structured label:value PDF still parses")
# ---------------------------------------------------------------------------
from engine.extract import extract_contract, is_structured, _pdf_text
struct = _pdf([
    "Contract ID: C-LEGACY", "Customer: Legacy Structured Co", "Start Date: 2026-01-01",
    "End Date: 2026-12-31", "Total Price: $60,000.00",
    "Type: subscription | Description: 12-month platform subscription | "
    "Standalone Price: $60,000.00 | Delivery: over_time"])
struct.seek(0)
check("legacy structured PDF detected as structured", is_structured(_pdf_text(struct)))
struct.seek(0)
lc = extract_contract(struct, "legacy.pdf")
check("legacy structured PDF still extracts correctly",
      lc["contract_id"] == "C-LEGACY" and lc["total_price"] == 60000.0
      and lc["deliverables"][0]["delivery_type"] == "over_time")
prose = _pdf(["Northstar Analytics, Inc.", "Order Form - Platform Subscription",
              "Vendor shall provide Customer access to the Platform for a period of twelve (12) months.",
              "The subscription fee is seventy-two thousand dollars ($72,000)."])
check("prose PDF detected as prose (routes to AI extractor)", not is_structured(_pdf_text(prose)))

# ---------------------------------------------------------------------------
print(f"\n{'='*50}")
print(f"{sum(_results)}/{len(_results)} checks passed")
sys.exit(0 if all(_results) else 1)
```

### `requirements.txt`

```text
flask>=3.0
gunicorn>=21.2
anthropic>=0.50
pypdf>=4.0
```

### `railway.json`

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "deploy": {
    "startCommand": "gunicorn app:app --bind 0.0.0.0:$PORT"
  }
}
```

### `.gitignore`

```text
__pycache__/
*.pyc
revrec.db
.env
venv/
.venv/
output/
```

### `README.md`

```markdown
# ASC 606 Revenue Recognition Engine

A simplified, end-to-end revenue recognition engine demonstrating order-to-cash /
rev rec accounting logic: contract → performance obligations → price allocation →
recognition schedules → journal entries → deferred revenue → automated month-end
close → known-revenue (RPO) forecast — with a web dashboard, dynamic contract
upload, and an AI layer that *explains* (but never *makes*) the accounting decisions.

Built as a portfolio piece. The 10 seed contracts are written as **realistic
prose contract documents** (order forms, SOWs, an MSA excerpt — letterhead,
numbered sections, signature blocks), not label:value forms. The extractor
reads the prose and reasons — grounded in the ASC 606 three-criteria test —
about what's being sold, whether each obligation is point-in-time or
over-time, the dates, price, and customer. Where a bundle gives no basis to
split a price, it flags for review instead of fabricating an allocation.

## Quick start

```bash
pip install -r requirements.txt
python3 app.py                       # serves API + dashboard on http://localhost:5001
python3 scripts/generate_report.py   # writes the offline report bundle to output/
```

Optional: `export ANTHROPIC_API_KEY=...` before starting to get Claude-generated
rationales; without a key the app uses deterministic template rationales (tagged
`source: template`) so everything still runs.

## The ASC 606 five-step model, as applied here

| Step | ASC 606 | In this project |
|---|---|---|
| 1. Identify the contract | A signed agreement with a customer | Each JSON contract (`contract_id`, customer, term, `total_price`) |
| 2. Identify performance obligations | Distinct promises to deliver goods/services | Each entry in `deliverables` becomes an obligation (`C-013-OB1`, …) |
| 3. Determine the transaction price | Fixed + variable consideration | `total_price` is the fixed consideration; usage fees (C-020) are variable and handled separately |
| 4. Allocate the price | Proportional to standalone selling prices | `total_price` split across obligations by `standalone_price_estimate` ratio (rounding residual to the last obligation so allocations always tie out) |
| 5. Recognize revenue | When/as control transfers | `one_time` → point-in-time lump sum on the delivery date; `over_time` → even monthly amounts across the term |

## Balance sheet mechanics

Cash received up front creates a **liability** (the company owes service, not money):

```
On signing:        Dr Cash                 / Cr Deferred Revenue      (full contract price)
Each period:       Dr Deferred Revenue     / Cr Revenue               (per obligation, per schedule)
Usage (variable):  Dr Cash                 / Cr Revenue               (billed & recognized as incurred)
```

**Deferred revenue vs accounts payable** — both are liabilities, but they settle
differently. Accounts payable is an obligation to *pay cash* to a supplier.
Deferred revenue is an obligation to *deliver a product or service* already paid
for; it converts to revenue through performance, never through a cash payment.
The dashboard's aggregate deferred revenue chart is the balance-sheet liability
line a company would report, month by month.

## Automated month-end close (the automation story)

Without automation, someone in accounting must remember — every month, for every
active contract — to look up the schedule and post `Dr Deferred Revenue / Cr
Revenue`. That's repetitive, error-prone, and doesn't scale past a handful of
contracts.

`month_end_close_batch(contracts, "2026-07")` generates the complete batch for a
given month from the stored schedules — no manual lookup — plus two control
checks:

1. **Ended-but-not-drained** — a contract past its end date still carrying a
   deferred revenue balance (recognition incomplete).
2. **Active-but-silent** — an active over-time contract with no recognition
   entry generated for the month (a missed posting).

The interview soundbite is not "I can calculate revenue recognition" — it's
"I can generate the recurring monthly entries a real close process needs, on a
schedule, with a built-in check for anything that was missed."

## Known revenue forecast (Remaining Performance Obligations)

Because every over-time obligation has a contractually fixed schedule, future
months roll up into a forward-looking series — the standard SaaS **RPO** metric.
The `/api/forecast` endpoint (and dashboard chart) shows known contracted revenue
by future month, traceable back to source contracts.

This is deliberately labeled **known revenue from existing contracts only**. It
excludes new sales, renewals, pipeline, and variable/usage fees. A total revenue
forecast would require separate assumptions (e.g., average new contract value ×
expected close rate) and is intentionally not blended into this number.

## Contract extraction — prose and structured, one internal schema

`engine/extract.py` is the single entry point for reading a contract PDF. It
detects the format and routes:

- **Prose** (realistic order form / SOW / MSA excerpt): the extractor calls
  the Claude API with the ASC 606 reference guide in context and reasons about
  each obligation — control-transfer timing via the three-criteria test (not a
  keyword match on "months"), the dates, price, customer, and allocation. If a
  bundle states no basis to split a price, it returns the obligation with a
  null price and a review flag rather than inventing a number. Requires
  `ANTHROPIC_API_KEY`; without it, prose upload returns a clear message.
- **Structured** (the legacy `Label: value` order form): parsed
  deterministically by `engine/pdf_contract.py`, exactly as before — **full
  backward compatibility** with earlier test PDFs, and works with no API key.

Both paths are pure translation into the **same internal contract dict**
(`contract_id, customer, start_date, end_date, total_price, deliverables[...],
optional modification`), so nothing downstream — journal entries, deferred
revenue, SAP export, forecast — knows or cares where a contract came from.

The seed set ships as 10 prose PDFs in `data/seed_pdfs/` plus their
machine-extracted representation in `data/seed_contracts.json` (authored
ground truth, since the deployed instance seeds at startup without an API
key). With a key configured, re-uploading any of those same PDFs exercises the
live extractor. The dashboard's **Upload Contract** button also accepts a JSON
file in the same internal shape.

### Flagged obligations are excluded from the totals (not guessed)

An obligation the extractor can't price or classify with confidence — an
unpriced bundle component, a variable/usage fee, ambiguous timing — is carried
but **excluded** from every calculation: it produces no schedule, journal
entry, or deferred-revenue row, so it's absent from the deferred revenue
chart, the RPO forecast, and the month-end close batch. The dashboard shows a
visible **"$X excluded from the totals pending review"** banner naming the
affected contracts, so the incomplete totals read as intentional rather than a
bug.

**Human-in-the-loop resolution.** A **"Provide the missing data (human
review)"** control on any flagged contract lets a person supply the standalone
price and delivery timing for each flagged obligation; it then flows into the
deferred revenue, forecast, and close totals normally. This works on both
uploaded contracts and the **shared seed contracts** — resolving a seed writes
a **per-visitor override** (a private copy that supersedes the seed in that
visitor's view only), so one person's fix never changes what another visitor
sees. Deleting the override (a ↺ revert control) restores the flagged seed.

Among the 10 seeds, two require human review — **ORD-2603** (bundle, one
aggregate fee) and **ORD-2609** (three-component bundle, single total) — with
$190,000 held out of the totals rather than silently allocated. Both are
resolvable in-app by supplying the missing standalone prices.

**Incremental by design:** each contract is processed exactly once and its full
result is cached. Aggregate views (deferred revenue series, close batch, RPO
forecast) sum the cached per-contract series, so adding contract #21 never
reprocesses contracts #1–20. A real system with thousands of contracts can't
afford full reprocessing on every insert; this mirrors that constraint.

Uploads survive restarts and page refreshes (SQLite on disk, not session state).
Uploads are scoped to a browser cookie, so one visitor's test contracts don't
pollute another's view — everyone sees the 20 seed contracts by default.

## AI explanation layer (RAG-grounded)

All AI reasoning is grounded in `data/asc606_reference_doc.md` — a condensed
ASC 606 guide (five-step model, the three over-time criteria, licensing,
modification treatments, scope boundaries). `engine/rag.py` splits it into
sections and retrieves by transparent keyword scoring (no embeddings — at 9KB
of reference text, auditable retrieval beats a vector store).

`engine/explain.py` calls the Claude API (model `claude-opus-4-8`) once per
contract with the retrieved sections in context, and every rationale must name
the specific rule applied (e.g. *"over time under Step 5, criterion 1
(ASC 606-10-25-27)"*). Without an API key, deterministic template rationales
cite the same sections. This is strictly separated from `engine/core.py` —
**the AI explains output that deterministic rules already produced; it never
makes a classification or allocation decision.** Every rationale is tagged
with its source in the UI.

### Ambiguity detection and confidence scoring

Each deliverable's description is assessed against the reference doc's
distinctness (Step 2) and over-time (Step 5) criteria and scored
**high / medium / low** confidence. The floor is a deterministic keyword
heuristic (runs identically with or without an API key); when the API is
available, Claude can *lower* — never raise — a confidence grade. Low-confidence
obligations and anything touching the reference doc's **explicit out-of-scope
areas** (financing components, principal vs. agent, multi-currency, leases,
contract combination) land in the dashboard's **Needs Review** section instead
of being silently auto-processed.

### Scoped chat agent

The dashboard includes a chat panel (`POST /api/chat`) for questions about
ASC 606, this dataset, or how the tool works. Guardrails:

- Every answer is grounded in retrieved reference-doc sections plus a summary
  of the currently loaded contracts — the system prompt forbids answering from
  general model knowledge, and requires "I don't have grounding for that"
  over guessing.
- Out-of-scope questions (tax law, ASC 842 leases, anything not in the
  reference doc or contract data) are declined by design.
- Rate-limited per visitor (20 messages/hour) since the endpoint is public.
- **Note on temperature:** the spec called for temperature 0.1–0.2; the current
  Claude model generation (`claude-opus-4-8`) removed sampling parameters
  entirely (they are rejected by the API), so consistency is enforced instead
  through the strict grounding rules in the system prompt — which is the
  stronger control for accuracy anyway.
- Requires `ANTHROPIC_API_KEY` (configured on the live deployment); without it
  the endpoint returns a clear 503 and the dashboard chat shows the
  configuration message.

### Out-of-scope test transcript (chat agent)

Verified against the live deployment — five deliberately out-of-scope
questions, all correctly declined (verbatim replies):

> **Q: What's the capital gains tax rate?**
> A: That's outside the scope of this tool — I can only answer questions about
> ASC 606 revenue recognition and the contracts loaded in this dashboard.
>
> **Q: How does ASC 842 lease accounting work?**
> A: That's outside the scope of this tool — I can only answer questions about
> ASC 606 revenue recognition and the contracts loaded in this dashboard.
>
> **Q: Should I invest in SaaS stocks?**
> A: That's outside the scope of this tool — I can only answer questions about
> ASC 606 revenue recognition and the contracts loaded in this dashboard.
>
> **Q: How do I account for a significant financing component in a 3-year deal?**
> A: Significant financing components are explicitly outside the scope of this
> tool — the reference guide lists them as a scope boundary. Per that guidance,
> this should be flagged for human accounting review rather than resolved here.
>
> **Q: What's a good chocolate chip cookie recipe?**
> A: That's outside the scope of this tool — I can only answer questions about
> ASC 606 revenue recognition and the contracts loaded in this dashboard.

(Note #4: the agent recognizes a financing component as an ASC 606 *scope
boundary* and defers to human review, rather than a generic decline — the RAG
grounding working as intended.) An in-scope control question ("Why is ORD-2601
recognized over time?") is answered correctly, grounded in Step 5 criterion 1
and the loaded contract data.

## Project layout

```
engine/core.py       deterministic engine (validation, allocation, schedules,
                     modifications, journal entries, DR tables, close batch, RPO)
engine/explain.py    AI explanation layer (Claude API, template fallback)
data/seed_contracts.json   20 mock contracts
app.py               Flask API + static dashboard, SQLite persistence
static/index.html    self-contained dashboard (Chart.js via CDN)
scripts/generate_report.py  offline report bundle (JSON/CSV/markdown → output/)
```

## API

| Endpoint | Description |
|---|---|
| `GET /api/contracts` | All contracts in scope (seeds + your uploads), fully processed |
| `GET /api/contracts/<id>` | One contract's full detail |
| `POST /api/contracts` | Upload a new contract (prose PDF, structured PDF, or JSON; multipart `file` or raw JSON body) |
| `DELETE /api/contracts/<id>` | Delete one of your own uploaded contracts (seeds protected) |
| `POST /api/contracts/<id>/resolve` | Supply missing prices/timing for a flagged upload so it flows into the totals |
| `GET /api/close-batch.csv?month=YYYY-MM` | Close batch as an SAP-upload-style CSV (posting keys, G/L accounts, cost/profit centers) |
| `GET /api/aggregates` | Deferred revenue series, recognized-by-method series, and excluded-pending totals |
| `GET /api/close-batch?month=YYYY-MM` | Month-end close batch + control flags |
| `GET /api/forecast?from=YYYY-MM&months=12` | Known revenue (RPO) forecast |

## Deployment (Render or Railway)

The app is a single Flask service that serves both the API and the dashboard.

### Render

1. Push this folder to its own GitHub repo.
2. Render → **New → Web Service** → connect the repo.
3. Settings: Runtime **Python 3**, Build command `pip install -r requirements.txt`,
   Start command `gunicorn app:app`.
4. Add a **Disk** (e.g., 1 GB mounted at `/var/data`) so SQLite survives deploys,
   and set env var `DATABASE_URL=sqlite:////var/data/revrec.db`.
5. Optional env var: `ANTHROPIC_API_KEY` for Claude-generated rationales.
6. Deploy — the public URL appears at the top of the service page.

### Railway

1. Railway → **New Project → Deploy from GitHub repo**.
2. Railway auto-detects Python. Set the start command to `gunicorn app:app`
   (Settings → Deploy). Railway injects `PORT`; gunicorn binds it via
   `web: gunicorn app:app` semantics — if needed set start command to
   `gunicorn app:app --bind 0.0.0.0:$PORT`.
3. Add a **Volume** mounted at `/data` and set `DATABASE_URL=sqlite:////data/revrec.db`.
4. Optional: `ANTHROPIC_API_KEY`.
5. Settings → Networking → **Generate Domain** for the public URL.

If the chosen host has no persistent disk, the `DATABASE_URL` seam in `app.py`
is where a hosted Postgres (Neon/Supabase free tier) would slot in — the demo
ships SQLite-only to stay dependency-light.

## Known limitations & deliberate scope cuts

- **Modification treatment is simplified.** The mid-term modification (C-018,
  C-019) uses prospective treatment: remaining unrecognized revenue + added fee
  are blended and spread over the remaining term. Real ASC 606 requires judging
  whether a modification is (a) a separate contract, (b) a termination + new
  contract, or (c) a cumulative catch-up — this engine always applies (b)-style
  prospective blending. Worth discussing as a known edge case.
- **Variable consideration is minimal.** Usage fees (C-020) are recognized as
  billed. Real ASC 606 requires estimating variable consideration (expected
  value / most-likely amount) subject to the constraint — not modeled.
- **Not full double-entry bookkeeping.** Entries show the cash → deferred
  revenue → revenue flow clearly, but there's no general ledger, trial balance,
  A/R (all contracts assume upfront cash), or multi-currency.
- **Implementation obligations are treated as point-in-time** at go-live for
  simplicity; many would be over-time in practice.
- **No contract combination rules**, no financing components, no commissions
  (ASC 340-40), no disclosures.
- **SQLite + cookie scoping is demo-grade persistence/auth.** A production
  version would use a real database and real authentication.
- **Delete is a hard delete — no accounting-period awareness.** The delete
  action fully removes a contract from storage and every aggregate. A real
  system must distinguish deleting an *un-posted* test contract (a true
  delete, as here) from reversing a contract whose entries have already
  posted in a *closed* accounting period — the latter is never a silent
  rewrite of history but a **reversing journal entry** in the current period,
  preserving the audit trail. That period-close distinction is out of scope
  for this demo; here, delete always fully rewrites the figures.

These cuts keep the core mechanics — obligations, allocation, deferral,
automated close, RPO — legible and demo-able.

## Robustness & tests

`scripts/test_bugfixes.py` covers the parsing/classification edge cases found
through stress testing (run `python3 scripts/test_bugfixes.py`):

- **PDF modification lines are never silently dropped.** A `Modification:`
  block is either fully parsed and applied (prospective reallocation runs) or,
  if incomplete, the upload is rejected with a review message — never processed
  as if the modification weren't there.
- **Confidence reasons are accurately attributed.** Recognition-method
  confidence is driven only by the deliverable description (so the same text
  scores the same everywhere), while allocation concerns like a **tied
  standalone selling price** are a separate, explicitly-labeled review reason —
  not conflated with "description too sparse."
- **Currency parsing is robust.** `$5,000.00` / `$5000` / `$5,000` all parse;
  negative formats (`$-5,000`, `-$5,000`, `($5,000)`) parse and are then
  rejected with a specific "cannot be negative" message, distinct from the
  "field not found" error for a genuinely missing price.
- **Prose seeds flag, exclude, and reallocate correctly.** ORD-2603 / ORD-2609
  are fully flagged (no silent allocation), $190,000 held out of the totals;
  ORD-2608 is a fully-priced bundle that allocates cleanly; the seed-#4
  modification is applied; and every included contract's deferred revenue
  drains to zero. Resolving a flagged seed via a per-visitor override brings it
  into the totals.
- **Backward compatibility holds.** A structured `Label: value` PDF is still
  detected and parsed exactly as before; a prose PDF is routed to the AI
  extractor. Both yield the identical internal schema.

Regenerate the prose seed PDFs with `python3 scripts/make_seed_pdfs.py`
(requires `fpdf2`, a dev-only dependency — the runtime loads the seeds from
`data/seed_contracts.json`).
```

---

## 10. Environment and setup instructions

### Prerequisites
- Python 3 (3.10+; deployed on 3.13). Git. (For deploying: a GitHub account and a
  Railway account.)

### Dependencies (`requirements.txt`)
```
flask>=3.0
gunicorn>=21.2
anthropic>=0.50
pypdf>=4.0
```
Dev-only (for regenerating PDFs / running the test suite's PDF generation): `fpdf2`.
Install it separately with `pip install fpdf2` — it is intentionally **not** in
`requirements.txt` because the deployed app never generates PDFs (the seed/sample PDFs
are committed to the repo).

### Environment variables
| Variable | Required? | Purpose | Example / value |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Optional (enables AI) | Enables live prose extraction, Claude-generated rationales, and the chat agent. Without it: structured/JSON upload still works, rationales fall back to deterministic templates, chat returns 503. | `sk-ant-...` (never commit; set in Railway → Variables) |
| `DATABASE_URL` | Optional (defaults to local file) | Config-driven SQLite path. Must start with `sqlite:///`. On Railway, points at the mounted volume. | `sqlite:////data/revrec.db` (note four slashes: `sqlite:///` + absolute `/data/...`) |
| `PORT` | Set by host | Port gunicorn binds. Railway injects it; the start command uses `$PORT`. | (auto) |

**Never expose or commit the actual API key.** On Railway it lives in the service's
Variables. Locally, export it in your shell (`export ANTHROPIC_API_KEY=...`) or leave it
unset to use the template-rationale path.

### Run locally from scratch
```bash
# 1. Get the code
git clone https://github.com/lindsey3291/asc606-revrec-engine.git
cd asc606-revrec-engine

# 2. (recommended) virtual env
python3 -m venv .venv && source .venv/bin/activate

# 3. Install deps
pip install -r requirements.txt

# 4. (optional) enable the AI layer
export ANTHROPIC_API_KEY=sk-ant-...        # omit to use template rationales + no chat

# 5. Run — serves API + dashboard on http://localhost:5001
python3 app.py
```
On first run the app creates `revrec.db` locally and seeds the 10 contracts. Open
http://localhost:5001.

### Generate the offline report bundle (optional)
```bash
python3 scripts/generate_report.py     # writes JSON/CSV/markdown into output/
```

### Regenerate the seed / sample PDFs (dev only, needs fpdf2)
```bash
pip install fpdf2
python3 scripts/make_seed_pdfs.py      # 10 prose PDFs -> data/seed_pdfs/, and static/sample_contract.pdf
python3 scripts/make_sample_pdf.py     # a STRUCTURED-format sample PDF (back-compat testing)
```

### Run the test suite
```bash
pip install fpdf2                       # the suite builds test PDFs in-memory
python3 scripts/test_bugfixes.py        # expect: 33/33 checks passed
```

### Deploy from scratch (Railway)
1. Push the repo to GitHub.
2. Railway → New Project → Deploy from GitHub repo → select the repo.
3. Service → Settings → the start command comes from `railway.json`
   (`gunicorn app:app --bind 0.0.0.0:$PORT`) — no manual entry needed.
4. Service → Variables → add `DATABASE_URL=sqlite:////data/revrec.db` and (optional)
   `ANTHROPIC_API_KEY`.
5. Service → attach a **Volume** mounted at `/data`.
6. Service → Settings → Networking → Generate Domain for the public URL.
7. Verify: open the URL (10 contracts, charts render); upload a contract, refresh, and
   confirm it persists; optionally redeploy and confirm it still persists (volume works).

---

## 11. What's built vs. what's still planned

### Fully working right now
- [x] Deterministic ASC 606 engine: obligations, relative-SSP allocation, point-in-time
      vs over-time classification, even-monthly / lump-sum schedules — pure integer-cent
      math, all invariants tie out (every schedule sums to the recognized total; every
      final deferred-revenue balance is zero).
- [x] Journal entries (cash receipt, recognition, usage billing) and per-contract
      month-by-month deferred-revenue tables.
- [x] Aggregate deferred-revenue time series, recognized-by-method series, and the
      forward-looking known-revenue (RPO) forecast — with flagged/excluded amounts held
      out and clearly labeled.
- [x] Automated month-end close batch with two control checks (ended-but-not-drained;
      active-but-no-recognition-entry).
- [x] SAP-style journal-upload CSV export (posting keys 40/50, G/L accounts, cost/profit
      centers, header text, contract id; balances to the penny; no doc/line numbers by
      design, since SAP assigns document numbers at posting).
- [x] Contract modification handling (prospective reallocation, approach 2), detected and
      applied — never silently dropped.
- [x] Ingestion in three formats, all producing the same internal schema: **prose PDF**
      (AI extraction, RAG-grounded, reasons about control transfer), **structured
      `Label: value` PDF** (deterministic parser, backward-compatible), and **JSON**.
- [x] RAG-grounded AI rationales that cite the specific ASC 606 rule/criterion, with a
      deterministic template fallback when no API key is present; every rationale tagged
      with its source.
- [x] Confidence scoring (high/medium/low) with a deterministic floor + separate tied-SSP
      / out-of-scope / sparse checks; genuinely ambiguous items routed to a **Needs
      Review** queue.
- [x] Exclusion of flagged obligations from all totals with a visible "$X excluded
      pending review" banner; exactly two seeds flagged (ORD-2603, ORD-2609; $190,000).
- [x] **Human resolution** of flagged contracts — including shared seeds — via a
      "provide the missing data" form; resolving a seed writes a **private per-visitor
      override** with a revert control.
- [x] Scoped, RAG-grounded chat agent that declines out-of-scope questions (verified live
      against 5 out-of-scope questions), rate-limited, graceful without a key.
- [x] Dynamic contract upload with incremental aggregation (adding one contract processes
      only that one), durable SQLite persistence, and per-visitor cookie scoping.
- [x] Delete contract (own uploads and overrides; seeds protected; delete of an override
      reverts to the flagged seed) with live aggregate updates.
- [x] Deployed live on Railway with a persistent volume and the API key configured.
- [x] 33-check regression suite; README with the ASC 606 five-step mapping, deferred-
      revenue-vs-accounts-payable explanation, deployment steps, limitations, and the
      chat out-of-scope transcript.

### Scoped but intentionally not built (documented limitations)
- [ ] Full modification treatment (only approach 2 / prospective reallocation is
      implemented; approaches 1 "separate contract" and 3 "cumulative catch-up" are not).
- [ ] Variable-consideration estimation with the constraint and true-ups (currently
      flag-and-exclude, or a legacy recognized-as-billed path).
- [ ] Full double-entry bookkeeping / general ledger / trial balance / accounts
      receivable (single-entry-style; all contracts assume upfront cash).
- [ ] Contract combination rules, significant financing component, principal-vs-agent,
      multi-currency, full disclosure requirements.
- [ ] Accounting-period awareness on delete (no closed-period lock or reversing entries;
      delete is a hard delete).
- [ ] Arbitrary/legal-document parsing (extraction targets realistic order-form/SOW prose,
      not any legal contract).

### Next planned steps (discussed, not yet built)
These were brainstormed toward turning the tool from backward-looking accounting into a
forward-looking "close the loop" order-to-cash product (the Ramp-relevant direction):

- [ ] **An extraction evaluation harness** — a labeled test set of contracts with
      human-verified correct extractions and a scorer (precision/recall on
      classification, correct-flag vs hallucination rate). Rationale: you cannot put an
      LLM near revenue without measuring how often it's right and catching regressions.
      Considered the single most impressive next addition and the most self-contained.
- [ ] **Confidence-based routing / straight-through processing** — auto-post
      high-confidence extractions; only route ambiguous ones to the review queue, with a
      tunable threshold defended by the eval numbers.
- [ ] **Period-lock + audit trail** — immutable log of who resolved/changed what and when,
      a closed-period lock, and reversing entries instead of silent rewrites (closes the
      known delete limitation).
- [ ] **Payment tracking + AR aging + dunning ("close the loop")** — add an actuals layer
      (billed vs paid) alongside the contracted schedule; flag overdue invoices into an
      aging view (Current / 1–30 / 31–60 / 61–90 / 90+); **draft** escalating follow-up
      emails via Claude and queue them for one-click human approval (not autonomous send).
- [ ] **Renewal / expiry outreach** — flag contracts whose term ends soon (RPO running
      off) and draft renewal emails.
- [ ] **Missed-billing control** — flag revenue the schedule says should have been
      invoiced but wasn't (unbilled revenue).
- [ ] **Cash-application exceptions** — match incoming payments to invoices; flag
      short-pay / overpay / unmatched for human reconciliation.
- [ ] **At-risk / churn signal** — combine overdue + upcoming renewal into a churn-risk
      escalation (the LedgerPay dataset has a planted SMB-churn wave that could feed this).
- [ ] **"Why did revenue change" agent** — natural-language querying over the ledger that
      traces a deferred-revenue or recognized-revenue change to the driving
      contracts/modifications.
- [ ] Deeper accounting (full modification test, variable-consideration estimation,
      double-entry GL), a hosted Postgres + auth + preparer/reviewer roles, and
      bidirectional ERP integration (post entries and read back real document numbers).

### Outstanding housekeeping (non-code)
- A GitHub personal access token (`mac git`) was pasted into an earlier chat; it should
  be deleted in GitHub → Settings → Developer settings → Personal access tokens, and a
  fresh one minted if needed for future pushes.

---

*End of handoff. See Section 9 above for the complete source of every file.*
