# The ranked backlog — what the judges actually want

*Phase 0.1 of the delivery plan, executed 14 September 2026 for £3.96. The eighteen judges who
read the audit's documents in September were handed their own recorded read and the document it
was about, and asked the one question the original rubric did not: **what would have to be
different?** They proposed changes and ranked them; the counting is deterministic and no model
was asked to aggregate anything.*

**Raw output, committed:** [`rereads.json`](../plan/readiness-audit-2026-09/judges/rereads.json)
· **Weights:** [`backlog.json`](../plan/readiness-audit-2026-09/judges/backlog.json)
· **Runner:** `audit/judges/rereads.py`, `audit/judges/backlog.py`

*Committed beside the audit's own judge reads rather than left in `audit/out/`, which is
git-ignored. Evidence that cost money and exists on one machine is not evidence.*

---

## The three findings that change the plan

### 1. Self-contradiction is the whole game, and it is small work

**Weight 9.42, named first by seven of nine judges, and twelve of thirteen mentions are
tagged *small*.** Not depth, not breadth, not a stated view — the single thing standing
between these judges and acting on the platform's documents is that **the documents contradict
themselves**, and the fix is deleting and reconciling rather than building.

Three judges independently asked for the same deletion: the Executive Summary bullet asserting
that no discounted cash flow, cost of capital or per-share value sits on the record, thirty
lines below a front page printing WACC 9.3% and a value per share.

This confirms the delivery plan's ordering rather than disturbing it — **Phase 3 is already
self-contradiction and Phase 4 is already print-what-exists** — but it changes their weight. They
are not tranches A and B of ISSUE 2; between them they are most of it.

### 2. "State a view" was not asked for once

**`states_a_view` has a weight of zero across all nine platform reads.** F13 — the stated view,
its own feature, its own ADR (0117), its own phase — was not named by a single judge as a change
that would move them.

Read carefully, it has not disappeared; it has been **absorbed**. What the judges ask for is *one*
reconciled base case with its method named and a share price beside it — which is F13's composed
half, arriving as the resolution of a contradiction rather than as a new capability. The authored
half, which ADR 0117 already sequences second and behind a redaction round, is asked for by nobody.

**What this changes:** Phase 6's view work is re-scoped to the composed half, shipped as part of
the reconciliation rather than as a feature in its own right. ADR 0117 stands as written — it
already ships the halves in that order and for that reason — but F13's cost estimate and its
place in the plan should fall.

### 3. Fixing everything they asked for still leaves eight of nine at "partly"

Asked whether they would act on the document if **every change they named** were made:
**one said yes, eight said partly.** Not one said no, and none said yes-with-reservations.

This is the most important number in the exercise and it belongs in the Phase 5
pre-registration. The delivery plan's target is *at least three of six comparisons no longer
choose the console*. The judges are saying, in advance and unprompted, that document changes
alone move them from "no" to "partly" — which is movement, and is not the same as preference.
A round that produces exactly that and no more is the middle row of the kill-gate table, not the
top one.

## And from the other side: what the console note is missing is provenance

The nine baseline reads rank **provenance first** (4.43), and the asks are specific:
*replace Yahoo, btw.media and a Substack with the 10-K balance sheet and lease note*; *resolve
the $62bn versus $16.53bn finance-lease contradiction the note admits it cannot reconcile*.

Five of nine said they would act on the console note if their changes were made, against one of
nine for the platform — so the console is closer to acceptable today. But the thing its judges
want most is the thing the platform already does best and the console structurally cannot: a
figure that resolves to a filing rather than to a URL that may or may not still say what was
quoted.

**This is the durable advantage, confirmed from the comparator's side rather than asserted from
ours.** It is also the argument for F8 printing the verified excerpt (behind F16's boundary):
the platform has the provenance and does not show it.

---
## The platform's own documents (9 reads)

If every change they asked for were made, they would act on it: partly 8, yes 1

| # | Category | Weight | Named first by | Effort | Answered by |
|---|---|---|---|---|---|
| 1 | internal contradiction | 9.42 | 7 | small 12, medium 1 | F4 recompute-everything · F8 print what exists |
| 2 | segment and breakdown | 3.98 | 2 | medium 8, large 1 | F7 primary-source depth · F8 dimensioned carve-out (ADR 0118) |
| 3 | price and multiples | 1.65 | 0 | small 5 | F8 the subject's own multiples (open question 2, answered) |
| 4 | valuation shown | 1.42 | 0 | medium 3, small 1 | F8 the valuation rows that fall off the 40-row cap |
| 5 | bear case with a number | 1.38 | 0 | medium 5, small 1 | F2 the adversary argues the other side |
| 6 | provenance | 1.08 | 0 | medium 2, small 1 | — already the platform's strongest dimension |
| 7 | adversary quality | 1.03 | 0 | medium 2, small 1 | F2 the adversary, and the seeded-false-challenge test |
| 8 | guidance and recent | 0.58 | 0 | medium 1, small 1 | F7 exhibits inside accessions already opened |

**What they actually asked for, in the top two ranks:**

- **internal contradiction**
  - msft1/sceptic: Add a headline reconciliation table tying $139,225m FCF and $485.29/share to their source calculation, period and inputs, and delete the Executive Summary bullet asserting no valuation, discount rate or per-share value exists.
  - msft1/operator: Reconcile the three conflicting per-share figures into one base case with a named method, state the current share price beside it, and delete the Executive Summary bullet claiming no valuation exists.
  - msft1/reader: Delete or correct the three mutually exclusive valuation statements — the $485.29 headline, the two DCF per-share outputs, and the executive-summary/red-team claims that no valuation exists — leaving one stated base case with its method named.
- **segment and breakdown**
  - azn/operator: Add a product-by-product FY2025 revenue table with US patent/exclusivity expiry dates and the percentage of revenue exposed inside the horizon.
  - azn/sceptic: Fetch the 20-F revenue note and therapy-area review and publish a product-level revenue table for FY2025 with the patent expiry / regulatory exclusivity date for each product above, say, $1bn, plus the share of FY2025 revenue falling due inside the horizon.
  - azn2/operator: Add a product-level FY2025 revenue table with each product's share of group revenue and its US/EU exclusivity expiry date, and total the revenue expiring inside the horizon.
- **price and multiples**
  - azn2/operator: Print the current share price and market capitalisation, state explicitly whether the 1,562,000,000 divisor is ordinary shares or ADRs and what the ADR ratio is, and show implied upside/downside for both the $169.80 and $333.48 outputs.
  - azn2/reader: Print the share price and market capitalisation as at 12 September 2026 in "At a glance" and repeat it beside the $169.80 and $333.48 outputs in "### The Two Terminal Methods", with the implied upside or downside stated as a percentage.
- **valuation shown**
  - msft1/operator: Show the five-year forecast FCF path, enterprise value, net-debt bridge and equity value in the DCF section, plus a second column holding capex intensity at FY2026 levels.
- **bear case with a number**

## The console baselines (9 reads)

If every change they asked for were made, they would act on it: yes 5, partly 4

| # | Category | Weight | Named first by | Effort | Answered by |
|---|---|---|---|---|---|
| 1 | provenance | 4.43 | 1 | small 7, medium 4 | — already the platform's strongest dimension |
| 2 | internal contradiction | 4.03 | 3 | small 6 | F4 recompute-everything · F8 print what exists |
| 3 | valuation shown | 4.00 | 2 | medium 5, small 3 | F8 the valuation rows that fall off the 40-row cap |
| 4 | excerpt and evidence | 2.93 | 1 | medium 4, small 3 | F8 print the verified excerpt · F16 the boundary first |
| 5 | segment and breakdown | 2.08 | 1 | medium 4 | F7 primary-source depth · F8 dimensioned carve-out (ADR 0118) |
| 6 | price and multiples | 2.03 | 1 | medium 3, small 2 | F8 the subject's own multiples (open question 2, answered) |
| 7 | bear case with a number | 0.58 | 0 | small 1, medium 1 | F2 the adversary argues the other side |
| 8 | named competitors | 0.25 | 0 | medium 1 | F8 name the operator-confirmed peers |
| 9 | adversary quality | 0.20 | 0 | small 1 | F2 the adversary, and the seeded-false-challenge test |

**What they actually asked for, in the top two ranks:**

- **provenance**
  - msft1/operator: Source FY26 D&A directly from the 10-K cash flow statement and rebuild the §8 deferral arithmetic and the §15 risk-6 depreciation-catch-up mechanism from it.
  - msft1/reader: Rebuild §9 from the as-filed 30 June 2026 balance sheet and lease note, giving finance lease liabilities and the $329.1bn uncommenced-lease figure with filing citations instead of Yahoo, btw.media and a Substack.
  - msft1/sceptic: Source the 30 June 2026 balance sheet, finance lease liabilities and the $329.1bn uncommenced-lease disclosure from the FY26 10-K balance sheet and lease note, replacing Yahoo/btw.media/Substack and resolving the $62bn vs $16.53bn finance-lease contradiction the note admits it cannot reconcile.
- **internal contradiction**
  - msft1/operator: Replace the aggregator-sourced FY26 balance sheet in §9 with the as-filed 10-K balance sheet and lease note, resolving the $62bn-vs-$16.53bn finance-lease discrepancy and re-sourcing the $329.1bn of uncommenced leases to the filing rather than btw.media and a Substack.
  - msft1/reader: State the actual FY26 D&A from the 10-K cash flow statement, recompute the capitalisation gap and fix the '155.237 − (115.95 − 44.1) = $63bn / 19%' mismatch.
  - msft1/sceptic: Read FY26 'Depreciation, amortization, and other' from the 10-K cash flow statement, cite it, and re-print the §8 deferral calculation so the stated result matches the printed formula.
- **valuation shown**
  - azn/operator: Build a CAPM or peer-implied cost of equity in Section 13.3 — a named risk-free rate, ERP and beta with sources — and show the DCF at both that figure and the market-implied 7.2%, stating which rating each supports.
  - azn/reader: Derive the cost of equity from a dated risk-free rate, a sourced beta and a stated ERP, and restate the base-case per-share value at that derived rate in Section 1.
  - azn2/sceptic: Build the cost of equity from AZ bond yields to maturity, a sourced beta, a dated risk-free rate and a cited ERP, replacing the 3.6pp spread-over-coupons assumption.
- **excerpt and evidence**
  - azn/reader: Extract the patent-expiry table and add a short table in Section 12 listing every key product whose exclusivity or expiry falls between Sep 2026 and Sep 2027, with the jurisdiction, the date, the FY2025 sales for that product and that jurisdiction, and the page reference in the expiry PDF.
  - azn/sceptic: A horizon-specific patent-expiry table sourced to the company's own expiry schedule, with FY2025 sales and US/ex-US split per product, replacing the global-Farxiga proxy.
  - azn2/sceptic: Read the Patent Expiries of Key Marketed Products schedule and state, product by product with jurisdiction, every expiry falling between Sept 2026 and Sept 2027, plus the Farxiga US-only FY2025 revenue and H1 2026 run-rate, and restate the "15% exposed" headline on that base.
- **segment and breakdown**
  - azn/operator: Add a sourced per-product expiry table for Sep 2026–Sep 2027 with FY2025 US-only revenue for each affected product, replacing the current 'global figure for a US event' row.
  - azn2/operator: Replace the global Farxiga figure with a sourced US/ex-US split from the 20-F Note 2 regional table, and add a per-product expiry column (year and jurisdiction) for Farxiga, Brilinta, Soliris, Lynparza, Calquence, Imfinzi, Tagrisso and Ultomiris from the 10 Feb 2026 patent-expiry schedule.
