# ADR 0034 — A withheld figure is a type with no field for it

**Status.** Accepted
**Date.** 2026-08-05
**Implements.** ADR 0030 route 2 — the operator keeps the EODHD personal-use plan and builds
for internal use, so nothing derived from market data may be published.
**Follows.** ADR 0029, which made the sector block a type rather than a check.

## Context

Every multiple in a comparables table is computed from a price. The price arrives under a
personal-use subscription whose terms prohibit selling, retransmitting, redistributing or
**displaying** the information in "original or repackaged form", and contain no derived-data
exemption anywhere. ADR 0030 read those terms and recorded the consequence: a figure computed
from this data is internal, and whether a computed multiple may be published is *unresolved*
rather than permitted.

That is a rule about a rendering surface, and rules about rendering surfaces are the ones
most easily lost. The obvious implementations are all fragile in the same way:

- **A boolean on the table** — `internal_only=True` — which every renderer must remember to
  read. A template that forgets prints the figures, and nothing fails.
- **A check in the exporter.** Correct until somebody adds a second export path, and the
  second one is always added under time pressure.
- **A convention in the documentation.** The weakest form of all: it protects nothing the
  first time somebody who has not read it writes a template.

Each of those puts the licence obligation in the same category as a coding standard, and the
consequence of breaching it is not a lint failure — it is publishing licensed data.

## Decision

### `for_audience` returns a different type, and the shareable one has no rows

`CompsTable.for_audience(Audience.SHAREABLE)` returns a `WithheldComps`. That object carries a
peer count, an excluded count, an as-of date and the licence note. It has **no** `peers`, no
`subject`, no `median_of` — not fields set to `None`, no fields at all.

A renderer handed one cannot print a multiple from it, because there is no multiple in it. The
restriction is enforced by what the object contains rather than by what a template remembers
to check, which is the same move ADR 0029 made when it turned "does this sector permit a DCF?"
from a question into a `ValuationMandate` a caller must be handed.

### The Markdown renderer's signature accepts only the withheld form

`render_markdown(..., comps: WithheldComps | None)` and `_comps_block(comps: WithheldComps |
None)`. A rendered Markdown report is the *shareable* artefact — it gets exported, attached
and sent — so the type it accepts is the one with no figures in it.

Putting the numbers into a report is therefore not a matter of passing a different argument.
There is no argument that would carry them; it requires changing the renderer, which is a
change somebody reviews. The internal view is the valuation page (task 31), which is not
exported.

### The counts are disclosed, because they are not the vendor's data

`WithheldComps` says *that* a comparison was performed, against how many peers, with how many
excluded. Those counts describe work a person chose to do — the peer set is proposed and then
confirmed by a human — and disclose nothing the subscription covers.

Silence would have been the safer-looking choice and the wrong one. A report that says nothing
about comparables reads as "no comparison was performed", which is a different and false
claim. The reader is entitled to know that an analysis exists and that they are not seeing it.

### `Audience` is an enum with two members, and neither is a default

A caller states which surface they are rendering for. There is no implicit "current audience"
and no default parameter, because a default is a decision somebody makes once and everybody
else inherits without noticing.

## Consequences

**The comps work is real and mostly invisible in the deliverable.** Accepted, and it is the
whole point of ADR 0030 route 2: the analysis informs the operator's own judgement, and the
licence does not permit more than that.

**Two rendering paths for one analysis**, which is duplication in the presentation layer. The
alternative was one path with a conditional, and a conditional is exactly what gets inverted
by a later edit.

**If the subscription ever moves to a commercial tier, this is what changes.** One method and
one signature, both in files named for the thing they do. That is deliberate — the restriction
was built to be removable by a decision rather than by an archaeology exercise.

**A `WithheldComps` is trivially constructible with wrong counts.** It carries no figures, so
the failure mode is a misleading count rather than a licence breach, and the count comes from
the same confirmed peer set the table does.

## Alternatives rejected

**A boolean flag on the table.** The default failure is printing, and a flag nobody reads
looks exactly like a flag everybody reads.

**Redaction at export time**, replacing figures with asterisks in a finished document. Puts
the licensed data into the document first and relies on a text pass to take it out —
correct until a second export path exists, or until a figure appears in prose rather than in
a table cell.

**Not computing the comparables at all.** Considered seriously, because the safest licensed
figure is the one that never exists. Rejected: the analysis is legitimate internal use, it is
what the subscription is *for*, and a valuation with no relative view is a worse valuation.
The constraint is on publication, not on knowing.

**One `render_markdown` that takes either type and branches.** The branch is the thing that
gets inverted. A signature that cannot express the wrong call is stronger than a branch that
currently makes the right one.
