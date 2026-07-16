# ASC 606 Rev Rec Engine — Demo Script & Talk Track

A front-to-end guide for demoing and narrating this project (e.g. Ramp AI Solutions
Strategist). Study the **Thesis** and **Q&A bank** cold; the walkthrough is your click path.

- **Live demo URL (use this — chat + live upload work):** https://asc606-revrec-engine-production.up.railway.app
- **Local (chat/upload disabled without API key):** `python3 app.py` → http://localhost:5001

---

## 0. The one thing to memorize (your thesis)

> **"Rules decide, AI reads and explains."** Every number in this app comes from
> deterministic, auditable accounting rules. The AI is used in exactly two lanes:
> (1) *reading* messy prose contracts into structured data, and (2) *explaining*
> the decisions the rules already made — grounded in a reference guide. The AI
> never decides revenue treatment itself.

Why this framing wins for a finance-plus-AI role: accounting has to be **auditable and
reproducible** — the same contract must always produce the same journal entries and each
result must trace to a rule (e.g. ASC 606-10-25-27), not to a model that could answer
differently next run. This is the credibility line. Lead with it, and return to it.

30-second pitch: *"This reads a B2B software contract as a PDF, splits it into performance
obligations under the ASC 606 five-step model, allocates the price, classifies each
obligation as point-in-time or over-time, and produces the full downstream accounting —
recognition schedules, journal entries, deferred-revenue balances, an automated month-end
close, an SAP-style upload file, and a known-revenue forecast. The rules do all the math;
Claude reads the prose and explains each call."*

---

## 1. Recommended demo arc (~6–8 min)

Don't just scroll top-to-bottom. Tell a story: **the numbers → how one number is built →
the judgment call → how it's operationalized → the AI layer.**

1. Top KPIs + the **excluded banner** (opens on judgment, not padding)
2. The three charts (deferred revenue, recognized-by-method, RPO forecast)
3. Drill into **ORD-2601** (clean SaaS) — the full audit trail for one contract
4. Drill into **ORD-2604** (the modification) — the sophisticated accounting
5. The **Needs Review** queue + resolve flow — "won't fabricate a number"
6. **Month-End Close Batch** + SAP CSV — operationalized for a real finance team
7. The **chat agent** — scoped, grounded RAG
8. (Optional) **Upload** a prose PDF live — extraction in action
9. Close: restate the thesis + what you'd build next

---

## 2. Section-by-section talk track

### A. Header + KPI tiles
On screen: title, three tiles — **10 Contracts**, **$112,000 Deferred Revenue — 2026-07**,
**$117,692 Known Revenue, Next 12 Months (RPO)**.

Say: *"Ten contracts loaded. Two headline numbers a finance team lives on: the deferred-revenue
liability on the balance sheet today, and RPO — remaining performance obligations, i.e. revenue
already contracted but not yet recognized, over the next 12 months."*

Know your terms:
- **Deferred revenue** = a contract liability. We took cash but still owe the customer future
  service. Settled by *delivering*, not by paying cash (that's the distinction from accounts
  payable — never blur them).
- **RPO** = contracted, not-yet-recognized revenue. **Known** revenue only — it deliberately
  excludes new sales, renewals, pipeline, and usage fees. A total forecast would need separate
  assumptions and must not be blended in.

### B. The excluded banner (⚑ $190,000 excluded pending review) — YOUR BEST MOMENT
On screen: *"$190,000.00 excluded from the totals pending review… ORD-2603, ORD-2609 … totals are
deliberately incomplete rather than padded with a guess."*

Say: *"This is the design decision I'm most proud of. Two contracts are bundles where the price
can't be split across obligations from the contract text alone. A naive tool would guess an
allocation to keep the totals looking complete. This one refuses — it holds $190K out of every
aggregate and says so on the front page. For an accounting tool, a wrong number that looks right
is the worst outcome. Silence-and-guess fails an audit; flag-and-exclude passes it."*

This single behavior signals finance maturity. Dwell on it.

### C. The three charts
- **Total Deferred Revenue Balance** — the aggregate liability over time. Rises as cash comes in,
  burns down as revenue is recognized. *"Summed from each contract's cached schedule, not
  recomputed — I'll come back to why that matters."*
- **Revenue Recognized by Month — Point-in-Time vs. Over-Time vs. Usage** — the classification
  split. Point-in-time = lump on delivery; over-time = spread across the term.
- **Known Revenue Forecast (RPO)** — forward view of already-contracted revenue burning off.

### D. Drill into ORD-2601 — Cedar Grove Financial (the clean SaaS trace)
Click the row. Four stacked panels appear:

1. **Performance Obligations & Price Allocation** — one obligation, `subscription`, `over_time`,
   $72,000 standalone → $72,000 allocated.
2. **The rationale** — *"high confidence — Recognized over time under Step 5, criterion 1
   (ASC 606-10-25-27): the customer simultaneously receives and consumes the benefit as the
   company performs…"* with **Rule applied** and **Rationale source** ("template" locally,
   "claude (RAG-grounded)" live).
3. **Deferred Revenue by Month** — $72K in, $6K recognized/month, burning $72K → $0 over 12 months.
4. **Journal Entries** — one `cash_receipt` (Dr Cash / Cr Deferred Revenue $72K) + twelve
   `recognition` entries (Dr Deferred Revenue / Cr Revenue $6K each).

Say: *"Here's the whole audit trail for one contract. The AI wrote that plain-English rationale
and cited the specific criterion — but notice everything below it is deterministic: the $6K/month,
the balance roll-forward, the journal entries. If I reran this a hundred times the rationale wording
might vary slightly, but not a single number would move. That's the separation — the AI explains,
the rules decide."*

The confidence chip (high/medium/low) is a **separate deterministic layer** (`explain.py`) that runs
*after* the numbers exist and can never change them. The AI can nuance a displayed confidence down
at most one notch (floored at "medium" for priced obligations) but can never route a priced
obligation into the review queue.

### E. Drill into ORD-2604 — Pinnacle Manufacturing (the modification)
On screen: category **⚠ Modified**, term 2026-01-01 → 2027-12-31, 2 obligations, method OT.

Say: *"This is a mid-term contract modification. ASC 606 gives you three treatments: separate
contract, prospective reallocation, or cumulative catch-up — and picking the wrong one misstates
revenue. This engine implements prospective reallocation: when a module is added mid-term but not
priced at standalone value, the remaining unrecognized consideration is combined with the new
consideration and re-spread across the remaining term — going forward only, past revenue untouched.
The other two treatments are the judgment calls a real accountant confirms, and I document them as a
known limitation rather than pretending to auto-resolve them."*

### F. Needs Review queue + resolve flow
On screen: five line items across ORD-2603 (2 obligations) and ORD-2609 (3 obligations), each with a
plain-English reason (no pricing basis to split the bundle).

Say: *"These are the $190K held out. Each flag names its real trigger — here, a single aggregate fee
with no basis to allocate it across the components. A human can resolve it: supply the missing
standalone price and timing, and the obligation flows into the aggregates normally. On the live site
that resolution is private to your session — it writes a per-visitor override so you don't change the
shared demo for the next person."*

### G. Month-End Close Batch + SAP CSV
On screen: month picker, **Generate Batch**, **Download SAP CSV**; *"4 entries for 2026-07 —
$14,000.00 recognized… ✓ Control check passed."*

Say: *"This is what makes it operational, not just analytical. Pick a month, it assembles every
recognition entry due that month with a control check for missed or incomplete recognition, and
exports an SAP-style journal-upload CSV — posting keys, G/L accounts, cost center. This is the file a
finance team actually posts. And it's generated from the stored schedules — no per-contract
reprocessing."*

Tie-in to incremental design: *"Each contract is processed once and its full result cached in SQLite.
Aggregates sum the cached series, so adding contract #1,001 only processes that one — a real system
with thousands of contracts can't reprocess everything on every insert."*

### H. The chat agent (live site only)
Say: *"A scoped assistant, grounded via retrieval in the ASC 606 reference guide plus the loaded
contracts. It's forbidden from answering from general model knowledge and declines anything out of
scope — tax law, ASC 842 leases, investment advice. I ran five out-of-scope questions against it live
and it correctly declined all five."* Ask it something real: *"Why is ORD-2601 recognized over time?"*

### I. Upload a contract (live site only)
Say: *"Upload a prose PDF and the extractor — Claude, grounded in the same reference guide — reads the
customer, dates, price, and per-obligation type and timing out of business English, then hands the
structured result to the exact same rules pipeline the seeds use. Structured PDFs route to a
deterministic parser instead. Both produce the identical internal representation, so nothing
downstream knows or cares which format it came from."*

---

## 3. Architecture in three sentences (if asked "how's it built?")

Python/Flask backend, SQLite (config-driven via `DATABASE_URL` so it could swap to Postgres), a
single self-contained `static/index.html` frontend with Chart.js. Five-stage flow: **ingest** (detect
structured vs prose PDF, route to deterministic parser or Claude extractor) → **classify** (pure-rules
engine: obligations, price allocation, point-in-time vs over-time) → **explain** (deterministic
confidence + RAG-grounded rationales, runs after the numbers, can't change them) → **calculate/store**
(schedules, journal entries, cached in SQLite) → **report** (dashboard, close batch, SAP export,
forecast, chat). Retrieval is deliberately simple keyword scoring over a ~9KB reference doc — no
embeddings — because it's transparent, auditable, and reliable at that size.

---

## 4. Q&A bank (rehearse these)

**"Why not let the AI just do the accounting?"**
Because accounting must be auditable and reproducible. A model can answer differently next run and
can't cite a rule. I confine AI to reading prose and explaining decisions; the classification and all
math are deterministic and traceable to a codification paragraph.

**"What happens with no API key?"**
The app still fully runs. Structured/JSON upload works, rationales fall back to deterministic templates
that cite the same rules, and chat returns a clear 503. The AI layer degrades gracefully — it's an
enhancement, not a dependency, which is exactly what you want in a control environment.

**"How do you know the extraction is right?"**
Two ways. The seeds ship as ground-truth extracted JSON authored to match a correct extraction, with
the human-facing prose PDFs alongside — re-uploading a PDF with the key set reproduces it live. And
extraction only proposes structured fields; the deterministic rules and the reference-doc grounding
constrain it, and genuinely ambiguous cases are flagged for review, not guessed.

**"Why keyword retrieval instead of a vector DB?"**
The reference doc is ~9KB. Transparent keyword scoring reliably returns the right section, is
auditable and reproducible, and adds no infra or opacity. A vector store would add cost for no accuracy
gain at this scale. I'd revisit that at a much larger corpus.

**"What are the limitations?"** (naming these builds trust)
Delete is a hard delete with no accounting-period awareness — a real system reverses posted entries in
the current period rather than rewriting history. Variable consideration is minimal (flag-and-exclude).
No contract-combination rules, significant financing components, principal-vs-agent, or multi-currency.
Modification handling implements one of three ASC 606 treatments; the other two are flagged as
judgment calls. These are deliberate scope cuts to keep the core mechanics legible.

**"What would you build next?"**
Postgres for durable multi-tenant storage; period-close awareness so deletes become reversing entries;
expand variable-consideration handling with the constraint and true-ups; and a batch-upload path for
processing a folder of contracts at once.

**"Walk me through the three bugs you fixed."**
(1) Silent modification data loss — a labeled modification line was read but silently dropped; now it's
either fully applied or the upload is stopped with a visible reason. (2) Mislabeled confidence — a
clear short description ("Perpetual software license") was mis-flagged "too sparse" because the
sparse-length check ran before the vocabulary check, and a tied-standalone-price condition was
misattributed; fixed the ordering and added a separate, explicitly-labeled tied-SSP check. (3) Fragile
currency parsing — negative/parenthetical amounts failed indistinguishably from a missing field; now
distinct, specific error messages and preserved sign. All three have regression tests (33 checks).

---

## 5. Pre-demo checklist
- [ ] Open the **live Railway URL** (chat + upload work there; local has no API key)
- [ ] Confirm the page loads and the excluded banner shows $190,000
- [ ] Have ORD-2601 (clean), ORD-2604 (modification), and the review queue ready to click
- [ ] Rehearse the thesis line until it's automatic
- [ ] Have one chat question ready ("Why is ORD-2601 recognized over time?")
- [ ] Know your terms cold: deferred revenue, RPO, point-in-time vs over-time, the three modification treatments
