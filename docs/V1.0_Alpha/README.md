# V1.0 Alpha

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
| 4 | [`04-feature-specifications.md`](04-feature-specifications.md) | Eighteen features: what, why, the mechanism, what it touches, the ADR it needs, and how we know it is done |
| 5 | [`05-delivery-plan.md`](05-delivery-plan.md) | Phases, sessions, pounds, operator hours, the targets stated before the work, and the result that would mean stopping |
| 6 | [`06-open-questions.md`](06-open-questions.md) | What is not decided, who decides it, and what it blocks |
| — | [`design/`](design/README.md) | The nineteen drawn screens, how to use them, and the intent behind each |

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
4. **An ADR before the code**, wherever a feature says it needs one. Eight do.
5. **A question leaves `06-open-questions.md` by being answered in a document**, not by being
   forgotten.

## Status

**Design complete; not started.** Nothing in this folder is built. The next decision is whether
this plan is ready to act on, or wants another pass — which is the conversation this folder
exists to make possible.
