# ADR 0072 — A lineage node resolves by table, not by hope

**Status.** Proposed
**Date.** 2026-08-22
**Required by.** `docs/investment-os.md` §6.1, which names this the one live defect among the
four prerequisites, and by ADR 0069 — an attestation needs a table to resolve against before
it can be the leaf of anything.
**Extends.** ADR 0011. A traced calculation refuses a bare `Decimal`; it should also refuse a
source reference nobody can follow.

## Context

**The provenance viewer is showing dangling nodes today, and has been for months.**

`SourceKind.FACT` (`src/aer/calc/units.py:136`) is documented generically — "a reported
figure, traced to a filing and a hashed artefact" — and implemented as one table.
`_load_fact` (`src/aer/services/calculations.py:409`) is `await session.get(FinancialFact,
parsed)`, and nothing anywhere says that is what `FACT` means.

`aer/services/macro.py:201` mints `SourceRef.fact(observation.id)` over a
`macro_observations` row, and its docstring argues correctly for doing so: "a published
statistic is an observation somebody made". The `get` returns `None`, `_resolve_fact` (line
324) returns `stored.missing("fact")`, and the node renders as `missing`.

**It is worse than the design note recorded.** Macro has no production caller of `as_rate`
yet, so that instance is loaded and not yet fired. The one already firing is prices.
`services/price_acquisition.py` lines 343, 495, 510, 542 and 543, and `services/comps_run.py`
line 329, all mint `SourceRef.fact(security.id, …)` over a `securities` row. Those quantities
feed `market_capitalisation_for` and `beta_against`, and the `acquire_prices` step persists
their records on every run with a market-data subscription — its own docstring says so. So
the market capitalisation on an already-published report walks down to a node the calculation
page labels *"this input points at something no longer here"*, which is wrong twice over: the
row is not gone, and it was never in the table the resolver looked in.

**Nobody noticed because the viewer degrades quietly, and does so deliberately.** `_missing`
(line 383) surfaces an unresolvable reference rather than dropping it, because "an input
pointing at a deleted fact is a real problem with the report that cites it". `_uuid_or_none`
(line 423) treats an unparseable id as missing rather than raising, because "a provenance
viewer that 500s on one bad id is less useful than one that shows the rest of the tree". Both
are right. The cost of being right is that a *systematic* resolver defect is indistinguishable
from an ordinary deleted row, one amber line at a time.

**That is ADR 0066's lesson in a second setting.** A 172.1% net margin shipped with every
guard green, because each guard answered a narrower question than a reader assumes it does.
A guard that renders a missing node instead of raising looks green in exactly the same way:
the tree draws, the page returns 200, and the only signal is a phrase a reader has no reason
to disbelieve.

**And the assumption is duplicated rather than centralised.** `services/exhibits.py:266`
independently reads `source.kind == "fact"` and looks the ids up in `financial_facts`; when
the lookup yields no single period it drops the margin point from the chart entirely, with a
comment explaining that a margin from several periods "cannot be placed on a year honestly".
Two readers, the same unstated guess, two different silent degradations. There will be a
third.

**The portfolio domain makes this urgent rather than merely wrong.** Every position, fill,
cost basis and NAV component is an attestation under ADR 0069, and the same resolver would
render every one of them as a dangling node — at a volume where the amber line stops being an
oddity and becomes the page. Meanwhile the fourth kind turns the three-way `if`-chain in
`_resolve_input` (line 282) into a four-way one. A branch is what already failed here: macro
was wired, and this function was not touched.

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
such a row. It needs `_load_fact` to know what it meant, and `_load_fact` changed its mind
about what it meant the day macro shipped.

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
what happens today.

**Backfilling is adding a statement, not rewriting one.** The objection to touching
`calculations` is that it is an audit record, and rewriting an audit record to make it look
correct is the opposite of this platform's posture. Stamping a table name changes no value, no
formula, no code version and no identifier; it records where a row already known by id
actually lives. Replay is unaffected — `aer/eval/replay.py` matches on
`SourceKind.ASSUMPTION.value` at lines 276 and 304, and that name does not move. The reader
that must narrow is `exhibits.py::_fact_input_ids`, and narrowing it to the `financial_facts`
discriminator is the fix to its own silent drop.

## Alternatives considered

**(a) Add the fourth enum value and a fourth branch.** One diff, no migration, ships in an
afternoon. It also reproduces the defect at portfolio volume: `ATTESTATION` would be
implemented as "a row in `attestations`" by exactly the mechanism that implemented `FACT` as
"a row in `financial_facts`", and that holds until the second table wants to mint one. The
design note already names two attestation shapes — a documented one behind a `USER_SUPPLIED`
artefact and an attested one typed by the operator. The second table always arrives. Rejected
because it fixes nothing and adds a branch to the structure that failed.

**(b) A shared supertype table, so `FACT` genuinely means what its docstring says.** One
`sources` table with every published figure in it; `financial_facts` and `macro_observations`
become detail tables joined on its id. Conceptually the cleanest answer, and the largest
migration this repository has ever run: two live tables re-keyed, both carrying report
citations that point at them. ADR 0018 puts a single function in charge of writing
`excerpt_verified` under `VERIFICATION_METHOD = 'excerpt_match_v1'`, and re-keying under live
citations means either rewriting verified citations or carrying a mapping table for ever — a
verification somebody has to trust twice is weaker than one nobody had to touch. The
correctness gain over a discriminator is nil: both make the leaf resolve. The cost differs by
an order of magnitude, and the discriminator does not foreclose the supertype, since a table
name is the column a future `sources` migration would route on anyway.

## Consequences

**The provenance viewer stops lying.** "This input points at something no longer here" starts
meaning a deleted row rather than being the platform's default rendering of price and macro
provenance, and a market capitalisation becomes walkable to the security and the bar behind
it — which it has never been.

**ADR 0069's attestation has somewhere to resolve to.** It arrives as a registry entry and a
loader rather than as a fourth arm of a function that has already proven it does not get
extended.

**The closed list survives.** Adding a *table* becomes cheap; adding a *kind* stays expensive —
an ADR and an `ALTER TYPE`, with migration 0048 as the worked precedent. That asymmetry is the
right one. The docstring's fear is "a number with no story", and a second table of published
statistics tells the same story as the first; a fifth kind would be telling a new one.

**The registry is checkable from both ends.** A leaf resolves through a registered loader, and
a minting site that names an unregistered table can be caught where it is written rather than
where it fails to render — which is the whole distance this defect travelled unnoticed.
