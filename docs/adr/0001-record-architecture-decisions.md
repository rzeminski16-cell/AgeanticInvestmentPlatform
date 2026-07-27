# 1. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-07-27

## Context

This project has three goals that pull against each other: it must be a genuinely useful
personal research tool, a credible portfolio artefact for investment-management employers,
and a codebase that could later become sellable software. Most of its architecture is a
resolution of that tension, and those resolutions are not self-evident from the code.

It is also built by one person over months, in evenings and weekends, with long gaps. The
reasoning behind a decision is reliably forgotten in far less time than that. Worse, a
decision whose rationale has been forgotten tends to get quietly reversed by a later
change that looked locally sensible.

## Decision

We record architecture decisions as Architecture Decision Records, using the format
described by Michael Nygard, in `docs/adr/`.

- One file per decision, numbered sequentially: `NNNN-short-title.md`.
- Sections: Context, Decision, Consequences, and where useful, Alternatives considered.
- **Accepted ADRs are immutable.** To change a decision, write a new ADR that supersedes
  the old one and mark the old one `Superseded by NNNN`. Editing history to make past
  reasoning look better destroys the only thing that makes these useful.

Write an ADR when a decision is hard to reverse, when it constrains future work, when a
reasonable engineer would ask "why on earth is it done this way", or when an obvious
alternative was rejected for a non-obvious reason.

Do **not** write one for reversible, local choices. An ADR per library import would bury
the ones that matter.

## Consequences

- Someone joining the project — including the author after a break — can read `docs/adr/`
  in order and understand the shape of the system before reading any code.
- Reviewing a change that contradicts an ADR becomes a specific conversation about a
  specific documented trade-off, rather than a vague argument about taste.
- There is ongoing overhead. It is small, and it is paid at the moment the reasoning is
  freshest, which is the cheapest possible time to pay it.

## Alternatives considered

**Comments in code.** They travel with the code, which is good, but they cannot capture a
decision spanning several modules and they are invisible when deciding whether to *add* a
module.

**A design document.** Documents describing the whole system go stale as a unit and nobody
trusts them. A ledger of dated, immutable decisions ages honestly: an old ADR is still a
true record of what was decided and why, even after it is superseded.

**Commit messages.** Right granularity, wrong discoverability. Nobody archaeologises git
log to find out why the calculation engine refuses to accept a bare number.
