# The remaining work — the running order

*Written 2026-08-28, and trimmed the same day to hold only what no other document holds: the
order, the gating, and the moves only the operator can make. The items themselves live in
[`ROADMAP.md`](ROADMAP.md) — the authority on scope, now carrying each open item's verified
state — and the overhaul's live status is the top section of
[`interface-overhaul.md`](interface-overhaul.md). Where this and either disagree, they win.*

---

## The phases

| Phase | What | Roadmap items | Gated on |
|---|---|---|---|
| **0** | Back to green — **done 2026-08-28** | §3.12's red suite | — |
| **1A** | The drafting failure | §2.1, §2.2 | Fixed 2026-09-01; the operator's confirmation run |
| **1B** | The portfolio's third door — **done 2026-08-29**, ADR 0093 | §3.1 | — |
| **2** | The overhaul — **done 2026-08-30**, all ten tranches | §3.12, closing §2.5 | — |
| **3** | The document and the data fixes — **done 2026-08-30** | §2.4, §2.6, §2.7, §2.8 | Nothing — surfaces phase 2 does not touch |
| **4** | Portfolio depth — **done 2026-09-02** | §3.2, §3.3, §3.4 | — |
| **5** | The judgement layer | §3.5–§3.11 | Strict order; §3.3 before §3.6 |
| **6** | Before it leaves one machine | A5, A7, A8 | Intent to run anywhere else |

The roadmap's order is priority, not serialisation: 1A waits on the operator, so 0 and 1B run
meanwhile without skipping ahead — §3.1 is second on the operator's own list, and phase 0
restores a baseline rather than building anything.

## What the order turns on

**Phase 0 is done (2026-08-28); 2026-08-29 closed tranches 6, 7 and 8 and phase 1B; and
2026-08-30 closed tranche 9 and with it the overhaul** — the console, the seven gates on one
frame, the `verdict` role (ADR 0087), the evidence and report surfaces, the portfolio's
third door (ADR 0093), the portfolio, skills and knowledge families, then settings and
costs, the ramp ledger to zero with the ratchet made a hard assertion, and the §8.3
hardening sweep — each tranche with both suites seen green before its closing commit. The
record is the overhaul plan's status section. What stays true for every later phase: in a
remote session a local PostgreSQL and Redis come first — without a database the default
suite silently skips 1,849 tests.

**1A is diagnosed and mostly fixed; 1B is done.** The operator's export landed on
2026-09-01 and §2.1 was read from the record rather than from a hypothesis. It refuted the
standing one: nothing starved, every failed section used both attempts, and all eight
failures were contract refusals in five causes — and six failed at `draft` while **two
drafted cleanly and were destroyed by `revise`**. Four causes are closed: ADR 0096 (a
malformed claim costs the claim), ADR 0097 (a numeral is checked against the figure, not
its spelling), ADR 0098 (a refused revision leaves the approved draft standing) and the two
eraser gaps; the fifth, the one-"missing evidence"-sentence rule, closes under ADR 0100 —
it refused a whole draft over a count of its own hedging and now loses the surplus remarks
instead. §2.2 is closed the same day under ADR 0099: the 0.30 was a cap firing on edits
that say nothing about the evidence, and each degradation now carries its own ceiling.
**Phase 1A is finished in code. What is left is the confirmation** — one operator-approved
live run, which also exercises the critique-and-revise loop (ADR 0091) against the same
sections for the first time. §3.1 landed 2026-08-29 in the roadmap's own
order, ADR 0093 first, before tranche 8 rewrites the portfolio form it adds a door to.

**Phase 2 is done (2026-08-30).** Tranche 9 closed §2.5 and §3.12 together and ended with
the manual pass — recorded, with its instrument (`tests/e2e/sweep.py`), its findings and
its residuals, in the overhaul plan's status section.

**Phase 3 is done (2026-08-30), and it interleaved with phase 2 exactly as planned** —
`src/aer/render/` and the extraction layer are surfaces the overhaul deliberately did not
touch, so nothing collided. Verified at **5,987 in-process · 175 browser**, each suite in
its own process against the shared `aer_test`.

- **§2.4** — the disagreement appendix reads as prose (contract v4, migration 0061); the
  stacked label/value pairs did not reproduce under the pinned WeasyPrint ≥ 69 and are
  guarded so they cannot return. The layout check became a test rather than a pass:
  `tests/test_report_layout.py` walks the paged box tree of the golden document *and* of a
  full-pipeline run whose red team argues at the length that broke the live document. What
  it cannot hold — a live-provider document on the machine the operator actually runs, which
  is commercial check 5 — stays named in the roadmap entry rather than claimed.
- **§2.6** — ADR 0094 first, as this document asked. The shape question turned on the
  quantity: a share *delta* goes stale the moment an earlier trade is backfilled, so the row
  carries the *ratio* and the walk multiplies at the split's place in trade-date order.
  Units multiply, the cost pool is untouched (ADR 0085), and the kind is refused on the form.
- **§2.7** — `NEVER_MAP` with a reason beside each entry, refused in `canonical_concept`
  itself so an alias added in good faith cannot take effect; the gate reports refused tags
  apart from unplaced ones and no longer stops a run for a decision already taken.
- **§2.8** — the mechanism only, which is all a session can honestly build:
  `aer curation-worksheet` prepares the ranked sitting. **The curation itself remains the
  operator's work**, in batched sittings, and is the one part of this phase that is not
  finished by anybody but them.

**Phase 4:** §3.2 landed 2026-09-01 — `calc/performance.py` and `services/performance.py`,
two returns that disagree on purpose and four exposure bands that name what they cannot
classify. §3.3 landed the same day in two halves: `services/mandate.py` first, so every
`session.get(ResearchRequest, …)` became an optional mandate read, then migration `0064`
dropping the three pointers and the six duplicated columns. It was a prerequisite for §3.6
rather than tidying — a monitor run has no research request, and until the reads were optional
a monitor could not exist. §3.4 landed 2026-09-02: the DCF's real grid shape, mirrored onto
the bank model's own axes with the property tests `calc/` demands. ADR 0101 settles the axis
question that had kept it out — a driver path may be an axis when it is flat, and is refused
by name when it fades — and closes phase 4.

**Phase 5 in the roadmap's forced order, nothing folded forward.** §3.5 landed 2026-09-02
(ADR 0102): the judgement supertype, premises as its first subtype, theses as the container,
and the theses tool working — the record §3.6's monitor reads and had nothing to read. §3.6
landed the same day (ADR 0103): the `thesis_monitor` role, a pass that measures the crossing in
code before the model is asked anything, `findings` kept apart from approvals with the tier as a
column, the one gate a contradicted premise opens, and the monitor as the fourth working tool.
§3.7 is next. §3.11
is quietly the missing half of ADR 0091's memory: an operator-authored methodology skill is
the only route a recorded lesson has into a future run.

**Phase 6 stays unscheduled** until leaving one machine is intended, and then becomes the
whole of the next phase — the three parts stand or fall together.

## What only the operator can move

In leverage order. Everything else above is a session's work.

1. ~~**Export the run diagnosis.**~~ **Done 2026-09-01** — run
   `7b05643a-4f95-4e08-ba5c-9d14d772f7c9`, stranded at gate 2 and diagnosed all the same.
   It unblocked §2.1 and §2.2 and settled both.
2. **Approve one confirmation run's spend** when the last §2.1 fix lands.
3. **The peer-discovery decision** (§4.15's remnant): `propose_peers` buys a reasoned peer
   list that can contribute no figure. Skip it when no price client is configured, or amend
   ADR 0059 and acquire peer data so comps compute — the second multiplies the data
   subscription across the peer set. Either is fine; it should be chosen, not inherited.
4. **Concept-map curation sittings** (§2.8) over the prepared worksheet.
5. **Commercial checks** (roadmap list): EODHD's licence terms **in writing**; WeasyPrint's
   native dependencies on the target Windows machine, alongside §2.4. The Companies House
   rate limit and Langfuse's self-host licence are verifiable from primary sources by a
   session with web access — delegable on request.
6. **One Firefox run** for D7's second engine, and **tranche 9's manual pass** when it comes.

Nothing here reopens the decided-against list, and nothing needs to.

---

**See also:** [ROADMAP](ROADMAP.md) · [the overhaul plan](interface-overhaul.md) ·
[the testing plan](interface-overhaul-testing.md) · [the decision records](../adr/)
