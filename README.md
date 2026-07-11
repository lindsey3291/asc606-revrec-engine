# ASC 606 Revenue Recognition Engine

A simplified, end-to-end revenue recognition engine demonstrating order-to-cash /
rev rec accounting logic: contract → performance obligations → price allocation →
recognition schedules → journal entries → deferred revenue → automated month-end
close → known-revenue (RPO) forecast — with a web dashboard, dynamic contract
upload, and an AI layer that *explains* (but never *makes*) the accounting decisions.

Built as a portfolio piece. 20 mock B2B contracts covering one-time sales,
subscriptions (6/12/24-month terms), bundles requiring price allocation,
mid-term modifications, and variable consideration.

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

## Dynamic contract upload + incremental aggregation

The dashboard's **Upload contract** button accepts a JSON file in the same
structure as `data/seed_contracts.json`. The upload runs through the *same*
`process_contract()` pipeline as the seeds — classification, allocation,
scheduling, journal entries, AI rationale — then persists to SQLite.

**Incremental by design:** each contract is processed exactly once and its full
result is cached. Aggregate views (deferred revenue series, close batch, RPO
forecast) sum the cached per-contract series, so adding contract #21 never
reprocesses contracts #1–20. A real system with thousands of contracts can't
afford full reprocessing on every insert; this mirrors that constraint.

Uploads survive restarts and page refreshes (SQLite on disk, not session state).
Uploads are scoped to a browser cookie, so one visitor's test contracts don't
pollute another's view — everyone sees the 20 seed contracts by default.

## AI explanation layer

`engine/explain.py` calls the Claude API (model `claude-opus-4-8`) once per
contract to generate a 1–2 sentence rationale per obligation: why point-in-time
vs over-time, and why the allocation split is what it is. This is strictly
separated from `engine/core.py` — **the AI explains output that deterministic
rules already produced; it never makes a classification or allocation decision.**
Every rationale is tagged with its source (`claude` or `template`) in the UI.

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
| `POST /api/contracts` | Upload a new contract (JSON body, seed structure) |
| `GET /api/aggregates` | Deferred revenue series + recognized-by-method series |
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

These cuts keep the core mechanics — obligations, allocation, deferral,
automated close, RPO — legible and demo-able.
