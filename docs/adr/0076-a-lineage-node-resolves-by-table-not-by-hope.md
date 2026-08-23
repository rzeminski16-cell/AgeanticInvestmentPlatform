# ADR 0076 — A lineage node resolves by table, not by hope

**Status.** Accepted
**Date.** 2026-08-22
**Required by.** `docs/archive/investment-os.md` §6.1 — first of the four prerequisites, and the one
that note describes as a defect "written down but not yet firing" — and by ADR 0073, whose
attestation needs a table to resolve against before it can be the leaf of anything.
**Extends.** ADR 0011. A traced calculation refuses a bare `Decimal`; it should also refuse a
source reference nobody can follow.

## Context

**The defect is loaded rather than firing, and that is the case for fixing it now rather than
a reason to leave it alone.**

`SourceKind.FACT` (`src/aer/calc/units.py:145`) is documented generically — "a reported
figure, traced to a filing and a hashed artefact" — and implemented as one table.
`_load_fact` (`src/aer/services/calculations.py:409`) is `await session.get(FinancialFact,
parsed)`, and nothing anywhere says that is what `FACT` means.

`aer/services/macro.py:201` mints `SourceRef.fact(observation.id)` over a
`macro_observations` row, and its docstring argues correctly for doing so: "a published
statistic is an observation somebody made". Where the two meet, the `get` returns `None`,
`_resolve_fact` (line 324) returns `stored.missing("fact")`, and the node renders as
`missing` — which the calculation page labels *"this input points at something no longer
here"*, wrong twice over: the row is not gone, and it was never in the table the resolver
looked in.

**They have not met.** `grep -rn 'services.macro' src/ tests/` returns a single line, and it
is macro's own logger name. Nothing under `src/` and nothing under `tests/` imports the
module: it is written, and it is unwired. No dangling macro node is rendering, and none has
been. An earlier draft of this record said otherwise, and the correction belongs in the record
rather than in a quiet deletion — a note that overstates its own evidence is this ADR's own
subject one level up.

**The wrong assumption is nonetheless written down twice, by readers who never consulted each
other.** `_load_fact` is one. `_fact_input_ids` (`src/aer/services/exhibits.py:266`) is the
other: it reads `source.get("kind") == "fact"`, collects the ids, and `_margin_series` looks
them up in `financial_facts` and nowhere else. A calculation whose fact inputs resolve to no
period, or to several, has its margin point dropped — "this margin cannot be placed on a year
honestly, so it is not placed at all". That comment is right about the case it was written
for and blind to the case it will meet: an input from another table resolves to no period at
all, so a margin computed partly from a published statistic disappears from the chart
silently, with no amber row anywhere to be seen. Two readers, one unstated guess, two
*different* degradations — a missing lineage node at one end, an absent chart point at the
other. A guess made twice independently is the evidence that the discriminator belongs on the
reference rather than in each reader's head, and that there will be a third reader.

**Nor is the generic reading of `FACT` one author's idiosyncrasy.** Six further sites mint
`SourceRef.fact` over a `securities` row — `services/price_acquisition.py` lines 343, 495,
510, 542 and 543, and `services/comps_run.py:329` — feeding `market_capitalisation_for` and
`beta_against`, whose records the `acquire_prices` step persists
(`workflow/workflows/vertical_slice_v1.py:325`). That step is wired, and everything beneath it
is conditional on a market-data subscription the platform is deliberately built to run
without, so whether a `securities` id has already reached a stored `calculations.inputs` row
is a question about one operator's licence key rather than about this code. The *reading* is
conditional on nothing: three modules took the docstring at its word, and the resolver took it
at `financial_facts`.

**Nobody would notice, because the viewer degrades quietly and does so deliberately.**
`_missing` (line 383) surfaces an unresolvable reference rather than dropping it, because "an
input pointing at a deleted fact is a real problem with the report that cites it".
`_uuid_or_none` (line 423) treats an unparseable id as missing rather than raising, because "a
provenance viewer that 500s on one bad id is less useful than one that shows the rest of the
tree". Both are right. The cost of being right is that a *systematic* resolver defect would be
indistinguishable from an ordinary deleted row, one amber line at a time — and at the exhibit
end there would be no line at all.

**That is ADR 0066's lesson, met before the fact instead of after it.** A 172.1% net margin
shipped with every guard green, because each guard answered a narrower question than a reader
assumes it does. A guard that renders a missing node instead of raising looks green in exactly
the same way: the tree draws, the page returns 200, and the only signal is a phrase a reader
has no reason to disbelieve. The single difference here is that the report has not been
written yet.

**A loaded gun is worth unloading while the drawer is still empty.** Three things are true at
once. The wrong assumption is already recorded in two independent readers, so a fix applied to
one of them is not a fix. The module that fires it is complete and waiting on an import line,
which nobody will weigh as a decision because it will not look like one. And ADR 0073's
fourth source kind turns the three-way `if`-chain in `_resolve_input` (line 282) into a
four-way one that rots identically, at portfolio volume: every position, fill, cost basis and
NAV component is an attestation, and a resolver that guesses would render each of them
unresolvable until the amber line stopped being an oddity and became the page. A branch is
exactly what failed here — `macro.py` was written to mint a `fact` over a second table and
`_resolve_input` was never touched, because nothing about an `if`-chain requires that it be.
Fixing this now costs a migration over an empty problem. Fixing it after the portfolio domain
lands costs one over rows.

## Decision

**A `SourceRef` names the table it resolves against, and leaf resolution is a registered
mapping from that name to a loader.**

**The discriminator and the kind answer different questions.** `SourceKind` says what
*guarantee* a number carries — somebody published it, somebody chose it, or code derived it.
The discriminator says which *relation* holds the row. A yield in `macro_observations` and a
revenue line in `financial_facts` carry the same guarantee and live in different tables;
treating those as one thing was never a modelling insight, only an unstated default that
`_load_fact` made on everyone's behalf.

**A registry, not a chain.** One mapping from discriminator to a loader returning a
`LineageNode`, so adding a source table is a registry entry with a test rather than an edit to
a branch somebody must remember to extend. A discriminator with no registered loader fails
loudly rather than rendering amber, which is the difference that matters: the failure mode
being fixed is silence. `_resolve_calculation` keeps its own path, because a calculation is
the one kind with children and therefore the only one the walk continues through.

This is the shape ADR 0035 gave agent capability — registry data, refusing rather than
trusting — applied to the other end of the same audit chain.

**The stored JSON already argued for this.** `CalculationInput.as_dict`
(`src/aer/calc/engine.py:95`) writes the unit as a rendered symbol rather than as a `Unit`,
because "a database row that needs the application to interpret it is a database row that
stops being readable the moment the application changes". A bare `"kind": "fact"` is precisely
such a row. It needs `_load_fact` to know what it meant, and `_load_fact` stopped agreeing
with `macro.py` about what it meant the day that module was written.

## What the migration costs

**This changes the shape of every persisted `calculations.inputs` row.** The column is JSONB
under a GIN index (`ix_calculations_inputs`, migration 0005), and that index exists for one
question: "what depends on this fact?" — asked, as the migration says, "the moment a fact
turns out to be wrong". A containment query's argument shape changes with the row shape, so
the index and every future query over it move together.

**Old rows need a compatibility read path, and it is a guess.** A bare `fact` discriminator
resolves against `financial_facts` first and `macro_observations` second. That ordering is
defensible — the candidate tables are disjoint sets of UUIDs, so at most one can match — but a
lookup with two candidates is still a guess, and a guess belongs in a fallback with an end
date, not in the resolver's steady state. It exists to serve rows a backfill has not yet
reached. A bare `fact` id matching nothing stays `missing`, which is both honest and exactly
what the present resolver does.

**Backfilling is adding a statement, not rewriting one.** The objection to touching
`calculations` is that it is an audit record, and rewriting an audit record to make it look
correct is the opposite of this platform's posture. Stamping a table name changes no value, no
formula, no code version and no identifier; it records where a row already known by id
actually lives. Replay is unaffected — `aer/eval/replay.py` matches on
`SourceKind.ASSUMPTION.value` at lines 277 and 306, and that name does not move. The reader
that must narrow is `exhibits.py::_fact_input_ids`, and narrowing it to the `financial_facts`
discriminator is the fix to its own silent drop. There is, for now, nothing to backfill: the
macro sites have never run and the price sites only run where a licence key does, so the
compatibility path is being written for rows that may not exist. That is the cheapest moment
this work will ever have.

## Alternatives considered

**(a) Add the fourth enum value and a fourth branch.** One diff, no migration, ships in an
afternoon. It also reproduces the defect one table further on: `ATTESTATION` would be
implemented as "a row in `attestations`" by precisely the mechanism that implemented `FACT` as
"a row in `financial_facts`" — an undocumented `session.get` inside an arm of an `if`-chain,
correct until somebody mints the kind over a second relation. The evidence that a kind
outgrows its table is `FACT` itself: the oldest value in the enum, and already minted over
three tables — `financial_facts`, `macro_observations` and `securities` — by five modules that
each read the same docstring and were each entitled to. Rejected because it fixes nothing and
adds an arm to the structure that failed.

**(b) A shared supertype table, so `FACT` genuinely means what its docstring says.** One
`sources` table with every published figure in it; `financial_facts` and `macro_observations`
become detail tables joined on its id. Conceptually the cleanest answer, and the largest
migration this repository has ever run, because `financial_facts.id` is not an internal
identifier. `claims.financial_fact_id` is a `RESTRICT` foreign key to it
(`db/models/claim.py:63`), and the model says why: "the figure a claim asserts cannot be
deleted out from under it. That would leave a published number with nothing behind it." Every
persisted `calculations.inputs` fact leaf names one too. Re-keying therefore moves the
identifier by which an approved report asserts *which figure it meant*, which leaves two
options: rewrite the claim rows, or carry a mapping table for ever. The first is an edit to an
audit record, which the migration section above refuses on far smaller grounds; the second is
the guess this record removes, made permanent. The correctness gain over a discriminator is
nil: both make the leaf resolve. The cost differs by an order of magnitude,
and the discriminator does not foreclose the supertype, since a table name is the column a
future `sources` migration would route on anyway.

## Consequences

**The provenance viewer never gets its chance to lie.** "This input points at something no
longer here" keeps meaning a deleted row, rather than becoming the platform's standing
rendering of price and macro provenance the first time either is wired; and a market
capitalisation becomes walkable to the security and the bar behind it, which it has never
been. A sentence that stays true is worth more before a reader has learnt to discount it than
after.

**ADR 0073's attestation and the macro seam are two callers of one fix.** An attestation
arrives as a registry entry and a loader rather than as a fourth arm of a function that has
already shown it does not get extended, and a `macro_observations` row stops being a `fact`
the resolver cannot find. ADR 0073 finds the same assumption in its own half of the seam —
`claims.financial_fact_id` is a foreign key to `financial_facts` for exactly the reason
`_load_fact` is a `session.get(FinancialFact, …)` — and sends the resolver half here: "ADR
0076 is where that assumption is dug out and named."

**It sends only that half.** Whether a published statistic may be the figure a `NUMERIC` claim
names — a fourth arm on the `claims` constraint, or a settled position that macro reaches a
report only wrapped in a `Calculation` — is left open by that record and is not closed by this
one. A leaf that resolves is a precondition for answering the question, not the answer;
asserting otherwise here would be the same overreach this record has just corrected in its own
first paragraph.

**The closed list survives, and it was never the schema that closed it.** `SourceKind` is a
plain `StrEnum` serialised into `calculations.inputs` as a string; there is no Postgres type
to alter, so adding a value costs one line — which is exactly how ADR 0073 prices the arrival
of `ATTESTATION`, and this record must not price it differently. What makes the list closed is
the docstring, the review and the ADR the value arrives with, and that is the only thing that
was ever holding it. So the asymmetry this decision creates is between *cheap and invisible*
and *cheap and loud*: adding a table becomes a registry entry a test can see, while adding a
kind stays a decision somebody has to write down. The docstring's fear is "a number with no
story", and a second table of published statistics tells the same story as the first; a fifth
kind would be telling a new one.

**The registry is checkable from both ends.** A leaf resolves through a registered loader, and
a minting site that names an unregistered table can be caught where it is written rather than
where it fails to render — which is the whole distance this defect would otherwise travel
before anybody saw it, and the reason it is being closed while nothing has yet made the trip.
