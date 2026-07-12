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
