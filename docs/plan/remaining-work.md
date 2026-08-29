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
| **1A** | The drafting failure | §2.1, §2.2 | `run-diagnosis.json` from the operator |
| **1B** | The portfolio's third door — **done 2026-08-29**, ADR 0093 | §3.1 | — |
| **2** | The overhaul, tranches 8–9 — 6 and 7 done 2026-08-29 | §3.12, closing §2.5 | None left — §3.1 landed 2026-08-29 |
| **3** | The document and the data fixes | §2.4, §2.6, §2.7, §2.8 | Nothing — surfaces phase 2 does not touch |
| **4** | Portfolio depth | §3.2, §3.3, §3.4 | §3.2 wants tranche 8's layout |
| **5** | The judgement layer | §3.5–§3.11 | Strict order; §3.3 before §3.6 |
| **6** | Before it leaves one machine | A5, A7, A8 | Intent to run anywhere else |

The roadmap's order is priority, not serialisation: 1A waits on the operator, so 0 and 1B run
meanwhile without skipping ahead — §3.1 is second on the operator's own list, and phase 0
restores a baseline rather than building anything.

## What the order turns on

**Phase 0 is done (2026-08-28), and 2026-08-29 closed tranches 6 and 7 and phase 1B** — the
console, the seven gates on one frame, the `verdict` role (ADR 0087), the evidence and
report surfaces, and the portfolio's third door (ADR 0093), each with both suites seen green
before its closing commit. The record is the overhaul plan's status section; tranche 8 is
next and nothing blocks it. What stays true for every later phase: in a remote session a
local PostgreSQL and Redis come first — without a database the default suite silently skips
1,849 tests.

**1A waits on the operator; 1B is done.** §2.1 and §2.2 diagnose from the export before
anything is changed — the hypotheses the code already admits are recorded on the items — and
the confirmation is one operator-approved live run, which also exercises the
critique-and-revise loop (ADR 0091) against the same sections for the first time. §3.1
landed 2026-08-29 in the roadmap's own order, ADR 0093 first, before tranche 8 rewrites the
portfolio form it adds a door to.

**Phase 2 resumes exactly where the overhaul plan's status section says.** Tranches 6 and 7
are done — the workflow engine's `verdict` role (ADR 0087) and the `RenderedFigure`-from-
`LineageNode` gap both closed with them; tranche 8's prerequisite, §3.1, is landed;
tranche 9 closes §2.5 and §3.12 together and ends with the manual pass.

**Phase 3 interleaves with phase 2 freely** — `src/aer/render/` and the extraction layer are
surfaces the overhaul deliberately does not touch. §2.4 pairs with commercial check 5
(WeasyPrint's native dependencies) since both want a real document generated where the
operator actually runs. §2.6 wants a short ADR before the code: a split transaction kind is a
schema decision with a shape question, the more so given a currency-exchange kind was refused
until its row shape was safe — and the derivation multiplies quantity while leaving the cost
pool alone (ADR 0085; a split is not a purchase). §2.8 is batched curation sittings over a
worksheet a session prepares from the gate's own ranked rows, not one heroic pass.

**Phase 4:** §3.3 is a prerequisite for §3.6, not tidying — a monitor run has no research
request, so the mandate reads must become optional before a monitor can exist. §3.4 mirrors
the DCF's real grid shape onto the bank model's own axes, with the property tests `calc/`
demands.

**Phase 5 in the roadmap's forced order, nothing folded forward.** §3.11 is quietly the
missing half of ADR 0091's memory: an operator-authored methodology skill is the only route a
recorded lesson has into a future run.

**Phase 6 stays unscheduled** until leaving one machine is intended, and then becomes the
whole of the next phase — the three parts stand or fall together.

## What only the operator can move

In leverage order. Everything else above is a session's work.

1. **Export the run diagnosis** — two read-only `psql` commands from `scripts/README.md`,
   then hand over `run-diagnosis.json` (read it first). Unblocks §2.1 and §2.2, the top of
   the roadmap.
2. **Approve one confirmation run's spend** when the §2.1 fix lands.
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
