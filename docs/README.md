# Documentation

Everything written down about this platform, arranged by who is reading it.

The platform produces **one institutional-style equity research report at a time** for a UK
or US listed company, under explicit human approval, with every number traceable to a
formula and every fact traceable to a hashed source document.

> **This is a personal research tool. It is not regulated investment advice.** Nothing it
> produces is a recommendation to buy, sell or hold any security. Ratings are non-binding
> personal views, and every generated report carries this disclaimer.

---

## Start here

| You are… | Read |
|---|---|
| Curious what this is and why it is built this way | [`product/what-it-is.md`](product/what-it-is.md) |
| Explaining it to somebody else | [`product/what-it-is.md`](product/what-it-is.md), then [`product/anatomy-of-a-research-run.html`](product/anatomy-of-a-research-run.html) in a browser |
| About to use it | [`users/getting-started.md`](users/getting-started.md) |
| About to change the code | [`developers/knowledge-map.md`](developers/knowledge-map.md) |
| Deciding what to build next | [`plan/ROADMAP.md`](plan/ROADMAP.md) |
| About to design a screen | [`design/README.md`](archive/superseded-2026-09/design-requirements/README.md) |
| Wondering why something was built that way | [`adr/`](adr/) — 100 decision records |

---

## The five audiences

### `product/` — what it is

For anyone who needs to understand or explain the platform without reading code.

- [`what-it-is.md`](product/what-it-is.md) — what it does, what makes it different from
  asking a chatbot to research a company, what it deliberately refuses to do, and where it
  is honestly incomplete.
- [`anatomy-of-a-research-run.html`](product/anatomy-of-a-research-run.html) — a
  self-contained diagram of the whole pipeline. Open it in a browser; it needs no server.
- [`investor-deck/`](product/investor-deck/) — twelve slides arguing the case to somebody
  outside the project, built only from figures the readiness audit measured;
  [`sources.md`](product/investor-deck/sources.md) maps every one of them back to its
  section, including the six blind comparisons the platform lost.

### `users/` — how to run it

For the operator: the person who installs it, commissions research and approves it.

- [`getting-started.md`](users/getting-started.md) — install, configure, first run.
- [`running-a-report.md`](users/running-a-report.md) — the run, gate by gate, and what each
  approval commits you to.
- [`the-confirmation-run.md`](users/the-confirmation-run.md) — testing the tool step by
  step, with the expected result beside every step: the ceiling that holds the run before
  the expensive part, what to check at each gate, and how to stop cheaply.
- [`reading-a-report.md`](users/reading-a-report.md) — how to read the output, walk any
  figure back to its source, and interpret a refusal.
- [`portfolio.md`](users/portfolio.md) — recording what you hold, and why no position is
  ever stored as a number.
- [`theses.md`](users/theses.md) — writing down what you believe about a company, and what
  would defeat it
- [`monitor.md`](users/monitor.md) — what has been filed since, read against each premise;
  a finding is a question, and the gate it can open.
- [`decisions.md`](users/decisions.md) — what you decided to do about a thesis, written
  before the outcome, and the trade that carries it out.
- [`review.md`](users/review.md) — a closed position scored for the process behind the
  decision, not the result; and what the reviews have in common, with an `n` on everything.
- [`risk.md`](users/risk.md) — what the book is exposed to and how it would have moved as it
  stands, the scenarios you state, and an analyst's reading that writes no number.
- [`watchlist.md`](users/watchlist.md) — the companies you follow and why, the queue of
  what to research next, and the standing budget it spends.
- [`skills.md`](users/skills.md) — the methodology library: your method, house view and
  preferences, versioned, pinned at gate 1, and composed into the roles that plan and write.
- [`troubleshooting.md`](users/troubleshooting.md) — when a run stalls, fails, or refuses.

### `developers/` — how to change it

- [`knowledge-map.md`](developers/knowledge-map.md) — **the orientation layer. Read this
  first.** The one rule, the anatomy of a run, the trust zones, the invariants and what
  enforces each, and the five extension recipes. Pinned to the code by
  `tests/test_knowledge_map.py`, so it fails the suite rather than going stale.
- [`architecture.md`](developers/architecture.md) — the kernel/tool boundary, and how a
  second tool exists beside the first without either reinventing runs, evidence or budgets.
- [`repository-layout.md`](developers/repository-layout.md) — every module, annotated.
- [`testing.md`](developers/testing.md) — the layers of the suite, what each buys, and the
  two rules that stop it lying to you.
- [`testing-by-hand.md`](developers/testing-by-hand.md) — **the full acceptance pass**, run
  on your own machine and **ending in a readiness verdict**: setup, the gates, a run stepped
  through under developer mode, both tools by eye, every guard provoked deliberately,
  failure and recovery, and the one section that spends money.
- [`notebooks/how-a-run-works.ipynb`](developers/notebooks/how-a-run-works.ipynb) — the same
  walkthrough as the product diagram, but every table generated by importing the live code.
  Re-run it to find out whether the diagram has gone stale.

### `V1.0_Alpha/` — what happens next

**The next phase, and the first place to look if you want to know where this is going.** The
platform becomes a loop — research, decide, hold, review — in which each stage writes a record
the next stage reads.

- [`V1.0_Alpha/HANDOVER.md`](V1.0_Alpha/HANDOVER.md) — **start here if you are about to do
  the work.** What is settled, what is left of Phase 0, how to bring the environment up, and
  the three things the plan has already got wrong.
- [`V1.0_Alpha/README.md`](V1.0_Alpha/README.md) — what the phase is, what is decided, what is
  not, and how to work in the folder.
- [`V1.0_Alpha/12-the-ranked-backlog.md`](V1.0_Alpha/12-the-ranked-backlog.md) — the only
  measured document in the folder: what eighteen judges said would have to be different.
- [`V1.0_Alpha/03-page-specifications.md`](V1.0_Alpha/03-page-specifications.md) — every one of
  nineteen surfaces: layout, components, states, data contract, constraints. **What you build
  from.**
- [`V1.0_Alpha/04-feature-specifications.md`](V1.0_Alpha/04-feature-specifications.md) —
  eighteen features: the mechanism, what each touches, the ADR it needs, and how we know it is
  done.
- [`V1.0_Alpha/07-data-model.md`](V1.0_Alpha/07-data-model.md) — the schema as it actually is,
  read from the live database, and the six real gaps. Read it before proposing a table.
- [`V1.0_Alpha/08-mechanisms.md`](V1.0_Alpha/08-mechanisms.md) — the four mechanisms nothing
  specified: materiality in a refresh, Ask's tier resolution, the monitor's metric resolution,
  and the scheduler's daily pass.
- [`V1.0_Alpha/11-testing-strategy.md`](V1.0_Alpha/11-testing-strategy.md) — the journey
  harness, what asserts the invariants once three of them change, and the nine things that must
  be green before a measurement round may spend.
- [`V1.0_Alpha/design/`](V1.0_Alpha/design/README.md) — the nineteen drawn screens, how to use
  them, and the intent behind each.

### `design-system.md` — the visual contract

- [`design-system.md`](design-system.md) — **Tracework.** Colour, type, spacing, shape,
  elevation and layout, as normative tokens. Every shipped surface is built to it and every V1.0
  screen is drawn in it. It is the one document from the August redesign that is still live.

### `plan/` — what happens next

The record of scope, and the evidence that shaped it. [`plan/README.md`](plan/README.md) is
the index, and states the order of authority: ADRs, then the roadmap, then `V1.0_Alpha/`.

- [`ROADMAP.md`](plan/ROADMAP.md) — **the authority on scope.** What is built, what is not, what
  is deliberately not being built. An item does not exist as scope until it is in here.
- [`readiness-audit-2026-09.md`](plan/readiness-audit-2026-09.md) — **is the research tool ready
  for use?** Six live runs, three console baselines, eighteen blind judge reads, 28 findings, the
  spend ledger, and the decisions only the operator can take. Every present-tense figure in
  `V1.0_Alpha/` traces to it. Results in
  [`readiness-audit-2026-09/`](plan/readiness-audit-2026-09/).

---

## Reference

- [`adr/`](adr/) — 123 architecture decision records, chronological. Each is a claim, not a
  topic. They are immutable once accepted: a change needs a superseding record.
  `developers/knowledge-map.md` §6 indexes them by theme. **0113–0123 are marked Proposed** —
  the ten V1.0 features that change a recorded decision or an invariant, and Phase 1.2's
  approvals model, argued before the code lands rather than after.
- [`data-sources/`](data-sources/) — one dossier per publisher: what it offers, its terms,
  its rate limits, and whether we may use it. Two sources were **declined** at this step
  and stayed declined.
- [`archive/`](archive/) — superseded documents, kept whole rather than deleted, with
  [an index](archive/README.md) saying what each was and what replaced it.

## Conventions

- **UK English** throughout, in documentation, comments and user-facing text.
- Comments and documents explain *why*, not *what*.
- `CLAUDE.md` at the repository root is the short version of the working conventions;
  `CONTRIBUTING.md` covers the mechanics of a change.
