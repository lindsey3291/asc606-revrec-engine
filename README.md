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
