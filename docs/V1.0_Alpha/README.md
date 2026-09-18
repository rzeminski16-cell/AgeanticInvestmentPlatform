# V1.0 Alpha

> **Starting work?** Read [`HANDOVER.md`](HANDOVER.md) first. It is what a session
> that has never seen the design conversation needs: what is settled, what is left of Phase 0,
> how to bring the environment up, and the three things this plan has already got wrong.

*The next phase. Everything that says what the system becomes, why, how it looks and how it gets
built. Opened 14 September 2026, after the readiness audit measured what exists today and the
design conversation settled what it should be.*

---

## What this phase is

The platform today produces **one auditable research report at a time**, and does it well: the
audit found 0 contradicted figures of 779 checkable ones, 259 of 259 citations verified, 3,335
calculation rows replayed with zero divergence, at £7.19 a report. It also found that nine of
nine blind comparisons preferred a Claude console note, and that six of six judges would not act
on what it produced.

V1.0 is the answer to both halves of that. It turns a report generator into **a loop** —
research, decide, hold, review — in which each stage writes a record the next stage reads. That
is the thing a chat session structurally cannot do, and it is the whole of the product's claim.

**The sentence this phase is organised around:** *are the reasons I bought still standing?*

## Read in this order

| # | Document | What it settles |
|---|---|---|
| 0 | [`00-the-product.md`](00-the-product.md) | The loop, and the decisions that shape it — point-in-time removed, an adversary that argues the opposite case, a recommendation that reads your own book, depth in free primary sources |
| 1 | [`01-tools-and-success-criteria.md`](01-tools-and-success-criteria.md) | Every tool and sub-tool, what each must make possible, and the measurable bar it has to clear |
| 2 | [`02-information-architecture.md`](02-information-architecture.md) | How nineteen surfaces become six destinations, and the three that deliberately have no menu item |
| 3 | [`03-page-specifications.md`](03-page-specifications.md) | Every surface: layout, components, states, data contract, actions, constraints. **This is what you build from** |
| 4 | [`04-feature-specifications.md`](04-feature-specifications.md) | Twenty features: what, why, the mechanism, what it touches, the ADR it needs, and how we know it is done |
| 5 | [`05-delivery-plan.md`](05-delivery-plan.md) | Phases, sessions, pounds, operator hours, the targets stated before the work, and the result that would mean stopping |
| 6 | [`06-open-questions.md`](06-open-questions.md) | What is not decided, who decides it, and what it blocks |
| — | [`design/`](design/README.md) | The nineteen drawn screens, how to use them, and the intent behind each |

**Then the deep-design pass**, written 14 September 2026 after the first six documents were
read back and found thin in eight places. These answer *how*, where the first six answer *what*.

| # | Document | What it settles |
|---|---|---|
| 7 | [`07-data-model.md`](07-data-model.md) | The schema as it actually is, read from the live database — and the **six real gaps**, which is far fewer than any plan assumed. The judgement layer is already built and unused |
| 8 | [`08-mechanisms.md`](08-mechanisms.md) | The four mechanisms nothing specified: what makes a change material in a refresh, how Ask resolves a tier, how the monitor resolves a premise to a number, and what the scheduler does each day |
| 9 | [`09-the-workbook.md`](09-the-workbook.md) | The spreadsheet, sheet by sheet: named ranges, the blue-and-black convention, the provenance tab, and the recompute test that proves it is a model rather than a picture |
| 10 | [`10-migration.md`](10-migration.md) | Nine additive migrations, the one that can fail and how it is checked first, and what is deliberately **not** backfilled |
| 11 | [`11-testing-strategy.md`](11-testing-strategy.md) | The journey harness, what asserts the invariants once three of them change, how the judgement layer is tested with no live run, and the nine things that must be green before a measurement round may spend |
| 12 | [`12-the-ranked-backlog.md`](12-the-ranked-backlog.md) | **Measured, not planned.** Phase 0.1, run for £3.96: the eighteen judges asked what would have to be different. Self-contradiction outranks everything and is small work; "state a view" was not asked for once; and fixing everything they named still leaves eight of nine at *partly* |

## The twelve ADRs

An ADR before the code, wherever a feature says it needs one. Ten were drafted with this
folder; the eleventh (0123) was written on 16 September for Phase 1.2, which the delivery
plan had called ADR-shaped without numbering, and the twelfth (0124) the same day for the
second half of Phase 1.6, the standing equity risk premium. All sit in
[`../adr/`](../adr/), marked **Proposed** until the change each argues lands — which 0116,
0123 and 0124 have, each Accepted with its code on 16 September.

| ADR | Decides | For |
|---|---|---|
| [0113](../adr/0113-a-run-reads-the-filings-as-they-stand.md) | Point-in-time is retired; invariant 4 is removed | F1 |
| [0114](../adr/0114-a-banks-revenue-is-derived-and-only-for-a-bank.md) | A bank's revenue is derived at the fact layer, and only for a confirmed bank | F7 |
| [0115](../adr/0115-the-adversary-argues-the-other-side-and-every-challenge-is-resolved-before-the-document-freezes.md) | The adversary argues the opposing case, and no challenge reaches a report unresolved | F2 |
| [0116](../adr/0116-a-report-is-superseded-never-replaced-and-exactly-one-is-current.md) | Supersession, with a reason, and exactly one current report per company | F4 |
| [0117](../adr/0117-the-report-states-a-view-in-two-halves-and-the-model-writes-neither.md) | The stated view: composed and authored, shipped in that order | F13 |
| [0118](../adr/0118-the-section-about-segments-may-read-segments.md) | The dimensioned-facts carve-out, for one section only | F8 |
| [0119](../adr/0119-a-printed-excerpt-re-enters-as-data-and-the-prior-research-type-stays-narrow.md) | The evidence boundary, decided before an excerpt is printed | F16 |
| [0120](../adr/0120-an-account-owns-a-book-and-a-share-is-a-sealed-pack.md) | Accounts and the sealed evidence pack — **deferred**, drafted so V1.0's schema does not foreclose it | F17 |
| [0121](../adr/0121-a-uk-filer-is-acquired-from-its-own-accounts.md) | A UK filer is acquired from its own accounts — there is no companyfacts equivalent | F19 |
| [0122](../adr/0122-the-knowledge-map-learns-what-you-decided.md) | The knowledge map learns what you decided, and still becomes evidence for nothing | F20 |
| [0123](../adr/0123-a-decision-at-a-gate-is-superseded-and-a-rejection-ends-the-run.md) | A decision at a gate is superseded, never re-asserted; a rejection ends the run; re-seal and re-measure are controls | Phase 1.2 (F-16, F-22) |
| [0124](../adr/0124-a-standing-operator-assumption-is-a-stored-proposal-confirmed-at-every-gate.md) | A standing operator assumption is a settings override, proposed into every run and confirmed at the gate — never a macro observation, never an attestation | Phase 1.6 (the equity risk premium) |

## The evidence underneath it

Nothing in this folder is asserted where it could be measured. Two documents outside it carry
the measurements, and both stay where they are:

- [`../plan/readiness-audit-2026-09.md`](../plan/readiness-audit-2026-09.md) — six live runs,
  three console baselines, eighteen blind judge reads, 28 findings. Every present-tense number in
  this folder traces to it.
- [`../design-system.md`](../design-system.md) — **Tracework**, the normative contract every
  surface is built to. The screens use it; they do not extend it.

## What is decided, and what is not

**Decided and written down**: the loop; the four stages; the portfolio as a validity dashboard;
Today as a briefing rather than an inbox; Ask's three tiers; the monitor watching both premises
and price; point-in-time removed; the adversary arguing the opposite case; consequences rather
than recommendations; depth in primary sources rather than licensed commentary.

**Not decided**: eighteen questions in [`06-open-questions.md`](06-open-questions.md), four of
which block a feature and two of which need a solicitor rather than an engineer.

## How to work in this folder

1. **The page specifications and the feature specifications are normative.** Where they disagree,
   the page spec wins on layout, data and states; the feature spec wins on mechanism.
2. **The screens are illustrations**, not a source of truth. No figure in them is real.
3. **`../plan/ROADMAP.md` still outranks everything here**, and the ADRs outrank it. A feature in
   this folder does not exist as scope until it has a roadmap number.
4. **An ADR before the code**, wherever a feature says it needs one. Ten do, and all ten
   are drafted — see the table above. They are **Proposed**, not Accepted: an ADR becomes
   Accepted when the change it argues lands, and until then it can still be argued with.
5. **A question leaves `06-open-questions.md` by being answered in a document**, not by being
   forgotten.

## Status

**Design complete, deep-design complete, eight ADRs drafted; not started.** Nothing in this
folder is built. The mechanisms, the data model, the migrations, the workbook and the testing
strategy have all had a second pass, and the eight ADRs the features asked for are written.

**All three blocking questions were answered on 14 September 2026.** The price subscription is
treated as permitting publication of derived figures, with ADR 0034's withheld type as the
fallback if that is wrong; "UK or US" is **built, not narrowed** — ADR 0121, F19, Phase 4a; and
the abandonment criterion is signed off, which is what makes every other gate able to fail.

> **Corrected 18 September 2026, against the live registers.** "UK or US" turned out to mean
> something narrower than the claim, and the difference is about how a company *files* rather
> than where it is listed. Companies House holds tagged accounts only for companies that file
> through accounting software; nine London-listed companies were checked and every one files a
> scanned PDF, which carries no figures and no text. So the platform researches US listings,
> UK companies with a US listing through their 20-F, and UK companies that file tagged
> accounts — and a subject it cannot read is now refused **when the run is commissioned**,
> with the reason, before anything is spent. See ADR 0128, ADR 0121's appended section, and
> `../plan/ROADMAP.md` §3.19 items 21–25.

What remains before code is Phase 0: the ranked judge backlog, the source map, the operator's
own blind read of two documents, and one QUICK-mode run. Three sessions and about £7.
