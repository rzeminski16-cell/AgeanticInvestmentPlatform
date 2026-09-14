# ADR 0113 — A run reads the filings as they stand, and point-in-time is retired

**Status.** Proposed — V1.0_Alpha. Accepted when the change it argues lands.
**Date.** 2026-09-14
**Supersedes.** ADR 0010 (point-in-time is selection, not filtering), ADR 0021 (look-ahead is
checked twice), ADR 0110 (a run is dated by the platform, not by the operator) and ADR 0111
(an undated source is admitted, and never primary) **in their enforcement**, and only there.
Each remains the readable record of why the machinery existed and what it cost; none of them
is reopened, because there is nothing left to argue about once the input they governed is
gone.
**Removes.** Invariant 4 of `CLAUDE.md` — *point-in-time is enforced at acquisition, in code*.
**Required by.** `docs/V1.0_Alpha/04-feature-specifications.md` F1, and the readiness audit's
measurement that the machinery never fired.

## Context

The platform carries a complete temporal apparatus. Acquisition selects the latest filing on
or before a date; every adapter bounds its own window; a quarantine policy refuses a source
demonstrably published after the date; a second look-ahead check runs at citation time; two
evaluation metrics score the result and one of them blocks the final gate.

**On all five stored runs, `look_ahead_recall` read *"not exercised"*.** Nothing published
after the as-of date was ever offered to a claim, because the as-of date was always the day
the run was commissioned — ADR 0110 made it a stamp precisely so nobody could set it to
anything else. The apparatus is a guard on a door that only opens onto today.

That is not an argument that the capability was worthless. It is an argument that **the
capability was already removed, one ADR at a time, and what remains is its scaffolding.**
ADR 0110 took away the ability to commission a run about a past quarter and said plainly that
if the capability were ever wanted back it should return "as a deliberate mode with its own
name and its own warnings, not as a date field that is wrong by default". This ADR takes the
scaffolding down.

The cost of keeping it is not abstract. `as_of_date` appears 527 times across 86 source files.
It is a clause in the planner's prompt, in the section writer's prompt and in the plan
critic's prompt — three places where a frontier model is told to reason about a constraint
that cannot bind. It is a required field on the request form that ADR 0110 already turned
into a read-only statement. It is a mode on a radio pair whose two options now differ only in
whether a mis-dated document is admitted. And it is one of ten blocking metrics, which means
a run can be held at its final gate by a measurement of a rule that never applied.

## Decision

**A run reads the filings as they stand when it runs. There is no as-of date to select
against, no point-in-time mode, and no look-ahead check.**

- **Acquisition stops filtering by date.** `select_point_in_time` and the per-adapter
  bounding go. "The latest filing on or before today" is "the latest filing", which is what
  every adapter already returned.
- **The request form loses the as-of field and the point-in-time pair.** ADR 0110 already
  made the date a statement rather than a question; it now stops being printed as a
  constraint, because it constrains nothing.
- **`temporal_compliance` and `look_ahead_recall` stop being computed.** The blocking set
  shrinks from ten to nine.
- **`aer.verify.citations` loses its look-ahead branch.** ADR 0021's second check was
  justified by acquisition not knowing what a claim would later rest on. With no date to
  compare against, there is nothing for it to find.
- **The three prompts lose the clause.** This is the part that is easy to overlook and is
  half the benefit: a model told to observe a rule that never binds spends tokens and
  attention on it, and may invent an observation of it in prose.

**Two things stay, and they are the reason this is a removal rather than a loss.**

**The retrieval timestamp on every source document.** That is provenance — *when did we
fetch these bytes* — and it is the answer to a different question. F14's post-trade review
asks whether a decision was sound *given what was knowable at the time*, and that question is
unanswerable without it. It already exists, it is already hashed with the artefact, and
nothing here touches it.

**The tier-5 cap on an undated source.** ADR 0111 separated two policies that had worn one
flag: *"is this demonstrably newer than the as-of date?"* and *"can this be shown to have a
date at all?"*. The first dies with the as-of date. The second is a statement about evidence
quality and survives intact — a document whose publisher stamps no machine-readable date is
still a weaker source than one that does, and it is still never primary.
`work_orders.undated_sources_admissible` keeps its column and its meaning; what it loses is
the branch that quarantined on it in point-in-time mode.

**And no data is deleted.** `reports.as_of_date` stays: it records the date a finished run
was made as-at, which is history and remains true. Existing evaluation rows for both retired
metrics stay, because they are the record of measurements that were taken, and deleting them
would rewrite the audit's own evidence.

## What is given up, named rather than discovered

**The ability to reconstruct a research position as it stood on a past date.** Nothing else
in the system provides it. After this change there is no way to ask *"what would this report
have said in March?"* — not from the interface, not from the API, not from a flag. A stored
run dated to March can still be read, replayed and re-rendered, because a replay reads that
run's own stamp; what cannot be **created** is a new run about a past date.

Three things make that acceptable, and the third is the one that matters.

1. The capability has not existed since ADR 0110, and nobody asked for it back.
2. The product is one institutional-style research report at a time about a company as it
   stands, for somebody deciding whether to buy today.
3. **A rule that never fires is not protection; it is a claim of protection.** The worse
   outcome than not having point-in-time is having a report that says it was produced under
   point-in-time discipline when no document was ever excluded by it.

If backtesting is ever wanted, it returns as its own feature with its own ADR, its own mode
name and its own warnings — and it will need the selection logic rebuilt, which this ADR
acknowledges as a real future cost rather than pretending the code will be missed.

## Consequences

**The invariant count goes from eight to seven, and `CLAUDE.md` says why.** An invariant
removed silently is an invariant that grows back as folklore. The table in `CLAUDE.md` loses
row 4 and gains a pointer here.

**A structural absence test replaces the invariant.** A deletion nobody asserts is a deletion
that returns: a text scan over `src/` and `docs/` for `point.in.time`, `look_ahead`,
`temporal_compliance` and `as_of` must return only this ADR, the surviving
`reports.as_of_date` reader, and the retrieval-timestamp code. It is the first thing F1 lands
and it is how F1 is known to be done.

**Every archived run still replays.** That is the acceptance condition, not a hope: the five
stored runs re-derive from their own records with the new code, and the replay reads each
run's own stamp exactly as before.

**The final gate gets simpler to explain.** One fewer blocking metric, and one fewer way for a
run to stop at a gate for a reason the operator cannot act on.

## Alternatives considered

**Keep the machinery and fix the metrics.** The metrics are not broken; they correctly report
that nothing was excluded. Fixing a correct measurement means changing what is measured, and
there is nothing to measure.

**Keep acquisition's date selection and drop only the mode, the metrics and the prompts.**
This is the tempting half-measure: the selection code is harmless, it already works, and
leaving it costs nothing today. It costs something tomorrow — 527 references and a parameter
threaded through 86 files is a permanent tax on every change to acquisition, and a dead
parameter is the kind of thing a later change quietly starts depending on. Remove it while
its removal is provable.

**Keep point-in-time as a hidden developer flag.** A capability that exists but is not
offered is the worst of both: the code is maintained, the tests must cover it, and no
operator benefits. Either it is a feature with a surface or it is gone.

**Do it late, after the features that pay for themselves.** Rejected on sequencing. This
change touches the drafting prompts, and a prompt change after the judgement layer is built
would require re-proving everything drafted under the old prompts. It goes first, alone, with
the suite green before anything else lands.
