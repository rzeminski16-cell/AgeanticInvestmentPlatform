# ADR 0102 — A thesis is premises, and a premise is the judgement

**Status.** Accepted
**Date.** 2026-09-02
**Required by.** Roadmap §3.5, and by ADR 0079, whose monitor reads premises and has had
none to read.
**Extends.** ADR 0074, which decided that a judgement exists and may never be a source
reference, and left its shape to the change that first stored one. ADR 0079's thesis model —
a free-text statement plus an *optional* predicate — is taken as written.

## Context

ADR 0074 settled the rule and deliberately not the table. It named the fifth record class —
*a named person held this view at this time on this stated basis* — and enforced the one
thing that mattered structurally: `SourceKind` gains no fifth member, `SourceRef` no fifth
constructor, and `claims` no `judgement_id`. What it did not decide is what a judgement row
holds, what a thesis is in relation to one, and what happens to a view its holder gives up.

Three shapes were on the table.

**One table, `theses`, with a JSON list of premises.** The obvious first draft. Rejected
because ADR 0079's monitor reads *one premise* against new evidence and returns a
`premise_id` "chosen by code and not by the model" — a premise has to be a row with an id, or
the monitor's output names an index into a document that may have been edited since.

**Premises as rows, with no judgement supertype.** A `premises` table carrying holder, time
and basis directly. Rejected because the Decisions tool and ADR 0081's reviewer are both
described in terms of judgements too — "a decision to trim on valuation rather than on
evidence", "a post-trade classification of *good process, bad outcome*" — and three tables
each carrying their own copy of *who held this, when, on what basis* is the drift ADR 0073's
supertype exists to prevent for attestations.

**A `judgements` supertype with `premises` as its first subtype**, in exactly the shape
`attestations` and `transactions` take. This is the decision.

## Decision

**`judgements` holds what every judgement has: a holder, two times, a basis, a supersession
link, and a withdrawal with a reason.** `held_at` is when the view was held as the holder
states it; `recorded_at` is when the platform was told. Two clocks for the reason
`attestations` keeps two — "what did you believe before the results came out" is answered
by the first. `basis` is `NOT NULL` for the reason `assumptions.justification` is: a view
with no stated grounds is a guess wearing a label, and there is no `note` column that could
stand in for it.

**`premises` is the subtype, keyed on the judgement's own id.** A premise *is* a judgement
seen from its thesis; a separate key would allow a premise with no holder, no time and no
basis. `JudgementKind` has one value, `premise`, and adding one is a value *and* a detail
table — visibly a schema change rather than a string.

**`theses` is the container and not a judgement.** A thesis names a subject, a title and
the report it was written against, and belongs to a person. What it *asserts* is its
premises. The subject is a kind and an id with no foreign key, in `work_orders`' shape:
a thesis outlives the company row it was about, as an audit record outlives the thing it
describes. The one foreign key it carries is to the report, `SET NULL`, because "the
evidence it rests on" is a reader's first question and a report id is an honest answer.

**A premise without a predicate must name the date a person reviews it by.** ADR 0079's
optionality is deliberate — a mandatory predicate would mint fake measurements — but an
unpredicated premise with nothing scheduled is a view the platform stores and silently stops
asking about, which is the failure `STALE_AFTER_DAYS` measures without addressing. So the
check constraint is: a predicate is all four of metric, comparator, threshold and unit or none
of them, and `metric IS NOT NULL OR review_by IS NOT NULL`. The form asks the question in
those words — *a threshold code can test, or a person will look again* — and styles the two
answers identically, because a surface that greyed the second out would teach the operator to
fabricate the first.

**What the metric names is free text, not a closed vocabulary.** Closing it here would put
the platform's current reach in the operator's mouth: a premise about a line the platform
cannot yet read is still a premise. Resolution is the monitor's business, and
`unobservable` is in its enum precisely for a metric no filing answers.

**Nothing is deleted, and a change of mind is a later fact.** A premise is withdrawn with a
reason, on the row that was withdrawn; a thesis is retired with a reason; both constraints
are *both-or-neither*, so a withdrawal without a reason cannot be stored. ADR 0078 argued this
for attention items — "I saw this and chose to do nothing" is decision data — and it holds
with more force here: a premise quietly rewritten after it failed is the row ADR 0081's
reviewer exists to read. A retired thesis accepts no new premises.

**Every write is on the audit chain, with the thesis as its subject.** `AuditEvent.create_linked`
gains `subject_kind` and `subject_id` — the columns ADR 0072 added and nothing had yet
filled — and `services/theses.py` is the only writer of the three tables. The correlation
sits outside the digest, as `job_id` and `request_id` do, so every chain written before this
verifies unchanged.

**The rule is proved by walking the metadata.** `tests/test_theses.py` asserts that no table
but `premises` carries a foreign key to `judgements`, that `claims` has no column naming a
judgement, that `SourceKind` still has four members and `SourceRef` no `judgement`
constructor, and that no column on the three new tables could hold a conviction. The last
is not a rule ADR 0074 states — it permits a stored confidence for calibration, computed
outside the traced engine — but none exists yet, and a column added later should arrive
with the surface that reads it and a test that pins where it may not go.

**`conviction` is reserved in the same change**, as ADR 0074 required: the seventh name in
`RESERVED_OUTPUT_FIELDS`, with its own attack file and its own refusal clause, because the
reason is not ownership — a view somebody holds is not a figure at all.

## Consequences

* **The theses tool works.** A list, a detail, and four forms: write a thesis, add a
  premise, withdraw one, retire the thesis. It is the third working tool and the first whose
  every row is prose.

* **The monitor has something to monitor.** §3.6 reads `premises` rows with a predicate
  against facts that arrive after `held_at`, and schedules `review_by` for the rest.

* **The Decisions tool and the reviewer are additive.** Each is a `JudgementKind` value and
  a detail table on the same supertype, with the holder, the two clocks and the basis
  already decided.

* **A thesis about a company nobody has researched cannot be written.** The form offers
  the companies the platform can resolve, and a fresh install offers none: a thesis about a
  ticker somebody typed would be a view about a string. The empty state says to commission
  research first, which is the platform's order of work rather than an inconvenience.
