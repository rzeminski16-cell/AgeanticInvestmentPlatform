# `plan/`

*What is in scope, what the evidence for it is, and where the work goes next. Three files and
a results folder; nothing else belongs here.*

---

| | What it is | Read it when |
|---|---|---|
| [`ROADMAP.md`](ROADMAP.md) | **The authority on scope.** Three buckets — fixes, new additions, archived. An item does not exist as scope until it is in here, and it moves between buckets by being worked, never by being tidied away | Before proposing anything. Start at *What to do next* |
| [`readiness-audit-2026-09.md`](readiness-audit-2026-09.md) | **The evidence.** Six live runs, three console baselines, eighteen blind judge reads, 28 findings, the spend ledger, and the decisions only the operator can take | Whenever a plan asserts a present-tense fact about the platform. Every one of them traces here |
| [`readiness-audit-2026-09/`](readiness-audit-2026-09/) | The audit's raw results — exports, scores, baseline texts, gate screenshots | When you doubt a number in the audit |

**The next phase lives elsewhere.** [`../V1.0_Alpha/`](../V1.0_Alpha/README.md) holds the
design, the specifications, the mechanisms, the migrations, the testing strategy and the
delivery plan for V1.0. The roadmap carries it as **§3.16**, which is what makes it scope; the
V1.0 folder is the authority on how it is sequenced and built.

## The order of authority, which matters more than it sounds

1. **The ADRs** ([`../adr/`](../adr/)) — decisions, not plans. They outrank everything below.
   Immutable once accepted: a change needs a superseding record. 0113–0120 are **Proposed**,
   which is the one state in which an ADR can still be argued with.
2. **`ROADMAP.md`** — scope. Where it and any other document disagree about *whether* something
   is being built, this wins.
3. **`../V1.0_Alpha/`** — how §3.16 is built, in what order, and how each part is known to be
   done.
4. **`CLAUDE.md`** at the repository root — the conventions, and the eight invariants. It still
   says eight, and it should: ADR 0113 *proposes* removing the fourth, and an invariant goes
   when the ADR that removes it is accepted and the code lands — not when a plan anticipates
   it.

## What used to be here

The design requirements, the redesign delivery record, the interface overhaul, the first
manual acceptance pass and `remaining-work.md` were archived on 14 September 2026 into
[`../archive/superseded-2026-09/`](../archive/superseded-2026-09/), with a README mapping every
old path to the document that replaced it. They are history, not scope. The five phase plans
and the original `PLAN.md` went to [`../archive/`](../archive/README.md) earlier, for the same
reason and with the same care: a diff records what changed, and these records are why.

## The rule this folder runs on

**A plan is not evidence.** Everything in `ROADMAP.md` that claims the platform does or does
not do something should be checkable against the audit, the code, or a run's own record — and
where it is not, it should say so. The most expensive mistakes in this repository's history
have been documents that were confidently out of date rather than documents that were wrong.
