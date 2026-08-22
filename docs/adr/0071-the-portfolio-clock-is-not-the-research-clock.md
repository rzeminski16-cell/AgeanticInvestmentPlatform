# ADR 0071 — The portfolio clock is not the research clock

**Status.** Proposed
**Date.** 2026-08-22
**Required by.** ADR 0069, which puts two times on every attestation and defers to this
record for why there must be two.
**Extends.** ADR 0010, ADR 0021 and ADR 0044 — the three decisions that settled
point-in-time, each of them for a document somebody else published.

## Context

**The platform has been bitemporal since ADR 0010 and has never had to say so.**

A financial fact carries the period it describes and the date it was filed, and the
uniqueness constraint takes both: `uq_financial_facts_observation`
(`src/aer/db/models/financial_fact.py:132`) keys on `period_end` *and* `filed_date`, because
two rows can report different values for one company's one period in one unit and both be
correct. The model states the reason as a rule — *"`filed_date` is not metadata. It is part
of the identity of the fact."* ADR 0010 selects across that pair; `visible_facts` spends one
line on it (`src/aer/services/facts.py:94`). `macro_observations` says the same in fewer
columns, keyed `(series_id, observed_on, vintage)` (`src/aer/db/models/macro.py:146`), and
`observation_as_at` (`src/aer/services/macro.py:131`) opens with the sentence this record is
about: *"Two filters, on two different dates."*

**Both of those clocks are read off somebody else's publication.** A filer decides which
period a figure describes; a regulator stamps the day it became public. The platform decides
neither — it reads two dates and enforces a rule over them. That is what ADR 0021 leans on
in judging admissibility on `publication_date_latest` rather than on a best estimate, and
what ADR 0044 leans on in dating a generated aggregate by its newest component. All three
assume there is a publication event to point at.

**A trade has the same two clocks and no publisher.** A purchase entered on Friday for a
Wednesday settlement is knowable on Friday and true of the book on Wednesday. A contract
note found in a drawer on Tuesday, for a fill that happened the previous Thursday, is true
of the book from Thursday and knowable only from Tuesday. One column cannot carry both, and
nothing outside stamps either: the operator supplies both dates, from a document or from
memory.

Which date is the right one depends on the question, and the portfolio asks two questions
that sound like one:

* *What did we hold on 3 March?* — effective time. This is what a NAV chart plots.
* *What did we believe on 3 March that we held on 3 March?* — transaction time. This is what
  a report dated 3 March was entitled to use.

Store one date and the schema can answer whichever question it was designed for. The other
returns a confident number instead of an error.

## Decision

**Every record the portfolio tools write about the operator's own affairs carries two
timestamps: `effective_at`, when it became true of the book, and `recorded_at`, when the
platform came to know it. Neither is derivable from the other.**

The rule that decides which one a query reads:

> **Research point-in-time reads `recorded_at`. A portfolio "as at" reads `effective_at`.**

This generalises `macro_observations` rather than inventing a shape. That table has held a
value's period beside its vintage since migration 0016, and records in `is_archived` how
strong the vintage claim is, "because a UK figure that silently inherited a US figure's
point-in-time guarantee would be the whole problem this table exists to prevent". The
portfolio needs the same pair for the same reason, and ADR 0069's `documented` and
`attested` grades are that column's descendants.

**`recorded_at` is the database's clock and cannot be typed.** It is `created_at_column()`
(`src/aer/db/base.py:43`), server-defaulted, chosen there because "the database clock is the
single authority for when a row appeared" — and `catalyst_resolutions.recorded_at`
(`src/aer/db/models/catalyst_resolution.py:63`) already spells the name that way.
`effective_at` is supplied, because a fill time is a fact about the world and there is
nowhere else to get it. **The asymmetry is the containment.** The clock that gates look-ahead
has to be the one nobody can set: an operator able to back-date what they knew can defeat
every point-in-time guarantee in the platform by typing.

### This is not a rename of `created_at`

Mechanically it is the same column, and that is the objection worth answering. What changes
is that the column becomes load-bearing and says so. A `created_at` nobody queries is
bookkeeping — a backfill re-stamps it, a restore re-stamps it, a tidy-up script re-stamps
it, and nothing fails, because nothing ever declared it a property. A `recorded_at` that
decides admissibility is part of the record's identity in the way `filed_date` is part of a
fact's, and every operation on the table then has to answer for what it does to it.

### Neither ordering is a bug, so no constraint can rescue a wrong query

`macro_observations` carries `CHECK (vintage >= observed_on)`
(`src/aer/db/models/macro.py:149`): a figure published before the period it measures is "not
a revision, an import error". **No equivalent constraint exists for a ledger, in either
direction.** Late entry gives `recorded_at > effective_at`; a trade booked ahead of
settlement gives `recorded_at < effective_at`. Both are ordinary weeks.

`observation_as_at` calls its second predicate redundant while that check holds, and keeps
it anyway, "because the day that constraint is relaxed for a forecast series this query
would start reaching forward silently". On a portfolio ledger that day has already arrived.
The second predicate is never redundant here, and a query that omits it reaches forward on
the first future-dated trade it meets.

## The failure this prevents

A contract note surfaces on Tuesday for a fill that happened the previous Thursday. The book
now holds 4,000 shares from Thursday where it held 3,000, so the NAV for Thursday, Friday
and Monday is not the NAV the platform displayed on Thursday, Friday and Monday. With one
date column those earlier figures are not wrong. They never existed. History has restated
itself and left no record that it did.

Then the second half. A report dated as at Monday makes a numeric claim resting on the book
— a holding, a cost basis, a portfolio exposure — and under ADR 0069 that claim names an
attestation. **ADR 0021's twice-checked look-ahead guard sails straight past it.**
`_refuse_if_out_of_time` (`src/aer/verify/citations.py:192`) resolves a citation to a
`SourceDocument` through its extraction, then compares
`source.publication_date_latest or source.publication_date` against the request's as-of date
(line 222). An internal ledger row has neither column and no document behind it. There is
nothing for the guard to read, so it reads nothing and refuses nothing.

**The `documented` grade does not save it either, and that is the part worth sitting with.**
A contract note ingested as a `USER_SUPPLIED` artefact would be dated by ADR 0021's
extractor from the best evidence it can find, which for a contract note is the trade date
printed on its face. That is the effective clock wearing a publication date's name. Judged
against it, a note opened on Tuesday and dated Thursday is admissible evidence for a report
as at Monday — checked twice, correctly both times, against the wrong date both times.

This is exactly the failure the platform exists to prevent, arriving through the one door it
does not watch. ADR 0010 described its shape already: "The failure is silent. Nothing raises,
no figure looks implausible, and the backtest simply looks better than reality."

**Store one date, call it `date`, and this is not hard to fix. It is unrecoverable.** A
filing can be re-fetched and a contract note re-read, because somebody else published them
and their dates survive independently of this platform. Nothing publishes the moment an
operator learned their own book. If that moment is not stamped as it passes there is no
archive to recover it from, no vendor to re-query and no second copy anywhere — and every
NAV in the history becomes a number nobody can show was the number that was shown.

## What it costs

**Every portfolio query grows a second predicate**, on tables that NAV, exposure, realised
P&L, the attention queue and every tool built after this one will read. `observation_as_at`
is as long as it is because of that second predicate, and macro data is one series at a time.

**And the two questions are a few words apart in English.** "What did we hold on 3 March"
and "what did we believe on 3 March we held on 3 March" become different queries that both
have to be written correctly, and swapping them yields a plausible figure rather than an
exception. The mitigation is the one ADR 0061 used for evidence scope: each question gets
exactly one function, in a service, and nothing else selects over the ledger — because three
copies of a predicate is how the first two diverged.

## Alternatives considered

**One date, with belief reconstructed from the audit log.** `audit_events` correlates only
by `job_id` and `request_id` (`src/aer/db/models/audit_event.py:46`), both deliberately not
foreign keys and both about runs. A trade the operator types has neither, so the entries
that would have to carry the reconstruction are precisely the ones that never reach the
chain. A guarantee reconstructed from a log that does not reliably contain the events is not
a design.

**A shadow history table beside each ledger table.** It puts the belief question in a
different table from the truth question, so the naive query — the one against the table with
the obvious name — silently returns the current view and looks right. Two columns fail the
other way: the naive query returns every version of every row, which is wrong on sight.
Where one design fails silently and the other fails loudly, this repository has consistently
taken the loud one.

**Valid time only, on the grounds that the corrected book is the true book.** True, and the
wrong axis to keep. The restated NAV is the better answer to "what is it worth"; only
`recorded_at` answers "what did this platform tell me", and only the second can be checked
against what a report said.

## Consequences

**NAV history becomes reproducible.** "The NAV shown on 3 March" stops being a memory and
becomes a query, and it can be set beside today's answer for the same day — which is how a
restatement becomes visible at all rather than becoming the past.

**A correction reads as a correction.** With ADR 0069's superseding row, the earlier figure
goes on standing and goes on saying what it said: the posture ADR 0021 took in refusing to
let an override clear a quarantine, and the instinct the schema already shows where
`uq_price_bars_day` (`src/aer/db/models/security.py:182`) makes a vendor's revised bar
collide rather than overwrite.

**The look-ahead guard gains an internal-ledger branch**, keyed on `recorded_at` against the
as-of date carried by ADR 0068's `EvidenceScope`. That is a widening of the control's
surface, not a relaxation of it: the guard acquires a second thing it knows how to refuse,
in the one place where it currently refuses nothing.

**What this does not settle.** Whether the book may inform research at all remains ADR
0064's question, untouched here. Two clocks make a portfolio figure datable. They do not
make it admissible.
