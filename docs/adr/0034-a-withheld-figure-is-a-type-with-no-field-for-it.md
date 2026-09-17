# ADR 0034 — A withheld figure is a type with no field for it

**Status.** Accepted — **amended 2026-09-17**, see the amendment at the foot. The decision
stands; its second half, the renderer's signature, is superseded by ADR 0030's amendment of
2026-08-09.
**Date.** 2026-08-05
**Implements.** ADR 0030 route 2 — the operator keeps the EODHD personal-use plan and builds
for internal use, so nothing derived from market data may be published. *Route 2 was amended
on 2026-08-09: a computed figure may now be published; the series may not.*
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

## Amendment, 2026-09-17 — the renderer takes the union, and the branch is safe

**The second decision above — "the Markdown renderer's signature accepts only the withheld
form" — and the alternative rejected immediately above it are superseded.** ADR 0030's
amendment of 2026-08-09 determined that figures *computed from* the licensed feed may be
published, and stated the consequence in as many words: "the comps section of an exported
report now shows the multiples where it previously showed a withholding paragraph". This
ADR anticipated that day and named its own price — "one method and one signature, both in
files named for the thing they do". The method changed in August. The signature did not, so
for six weeks the determination was encoded everywhere except in the one place that decides
what a reader sees.

**What that cost is on the record, and it is not the withholding paragraph.** Every audited
run printed the *other* branch, because ADR 0059 acquires no peer's prices and so every
confirmed peer is excluded: "every one of the eight proposed peers was excluded … **No
comparable figure was computed**, and there is no fuller version elsewhere." Measured across
the eight committed run exports, every one of them had computed at least one of the
subject's own multiples behind that sentence — MSFT #2 four (EV/EBITDA 19.0×, EV/Sales
11.1×, P/E 27.6×, P/B 8.3×), AZN and M&T the P/E each — traced and replayed on every run.
The sentence is defensible as a statement about
*peer* comparables and is not how a reader takes it; the new wording says "There is no peer
comparison", states the basis, and then prints the figures where they exist. The lesson is
narrower than the licence: a disclosure written for the withheld case was doing duty for a
case it was not written about.

`assemble_document` now takes `CompsTable | WithheldComps | None`, which is exactly what
`for_audience` returns, and the Markdown and HTML notations walk whichever arrived.

**Why the branch this ADR rejected is now the right shape.** The rejected alternative was a
renderer holding the figures and deciding not to print them; inverting that branch publishes
licensed data. This branch holds whatever `for_audience` handed over, and the withheld arm
*has no figures in it* — so a renderer that took the wrong branch prints nothing at all. The
containment moved one call upstream rather than away, which is the same argument this ADR
made for `for_audience` in the first place. `web/pages.py` has rendered the internal
valuation surface through precisely this union since the page was written.

**What still cannot cross.** The series and any chart of it, which ADR 0030's amendment
scopes out explicitly. That is held where it always was: `Chart.exportable` is false for
`price_relative` and `assemble_document` refuses a non-exportable chart outright (ADR 0043).
A `CompsTable` contains no price, no market capitalisation and no series — only multiples,
peer identities and exclusions — so admitting it to the assembler admits nothing the
determination withholds.

**A figure prints only with a footnote that resolves.** Each of the subject's multiples is a
traced calculation, and the comps step now records the calculation id beside the figure, so
the marker resolves to the formula, the inputs and the code version that struck it. A record
written before that id was stored sources its multiples to the step, and citing the step
would print the report's own broken-citation notice against a figure that is perfectly
sound — so those records render as they did before: the disclosure, and no table.

**`WithheldComps` keeps its job**, for the reason ADR 0030's amendment gives: the
determination is dated and about one executed agreement, and a provider added tomorrow
starts closed. The type is what the closed path returns, and it is still a type with no
field for a figure.

**One cost, recorded.** `WithheldComps.as_paragraph` carried Markdown emphasis on its
withholding sentence, which the HTML notation converted by a special case. A fragment may
not carry one notation's syntax, so the emphasis is gone and the sentence stands on its own
words.
