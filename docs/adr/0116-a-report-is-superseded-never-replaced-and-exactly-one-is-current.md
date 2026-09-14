# ADR 0116 — A report is superseded, never replaced, and exactly one is current

**Status.** Proposed — V1.0_Alpha. Accepted when the change it argues lands.
**Date.** 2026-09-14
**Extends.** ADR 0102 (a thesis is premises) and ADR 0104 (a decision is written before the
outcome), whose supersede-and-withdraw-with-a-reason shape this copies rather than invents.
**Does not touch.** Immutability. An approved report stays immutable, at its own address, for
good.
**Required by.** `docs/V1.0_Alpha/04-feature-specifications.md` F4 (the refresh), and
`docs/V1.0_Alpha/07-data-model.md`, which found the gap while reading the live schema.

## Context

The platform can produce two reports about the same company. It has no way to say which one
is true now.

`reports` carries `immutable` and `approved_at`, and an approved report is frozen — that part
works and is not in question. What is missing is any relation between two frozen reports.
Ask *"what does the platform currently think about Microsoft?"* and the honest answer is a
list, sorted by date, with the reader left to assume the newest one wins. Four separate plans
have assumed a *current report* exists; none of them owned it.

This becomes load-bearing the moment the refresh exists. A refresh is defined as *update what
changed, keep what did not* — which produces a second report that shares most of its prose
with the first and supersedes all of it. Without a recorded relation there is no way to render
*"this replaced the report of 11 September, because two quarters had been filed since"*, no
way for the monitor to know which thesis the current document supports, and no way for a
reader arriving at an old link to be told there is a newer one.

There is also a second case, and it is the one that makes this more than bookkeeping: a report
that turns out to be **wrong**. The bank-revenue defect (ADR 0114) produced an approved,
immutable report carrying a 172.1% net margin. The correct response is neither to edit it —
that destroys the evidence — nor to leave it standing as the platform's current view of M&T
Bank. It is to supersede it, with the reason recorded.

## Decision

**A report is superseded by another report, with a reason and a timestamp, and at most one
report per company is current.**

### The shape

```sql
ALTER TABLE reports
  ADD COLUMN superseded_by uuid REFERENCES reports(id),
  ADD COLUMN superseded_at timestamptz,
  ADD COLUMN supersession_reason text;

CREATE UNIQUE INDEX reports_one_current_per_company
  ON reports (company_id)
  WHERE immutable AND superseded_by IS NULL AND superseded_at IS NULL;
```

Three columns and one partial unique index, and the index is the decision. **"Exactly one is
current" is enforced by the database, not by a service that remembers to check.** A second
approval for a company whose current report has not been superseded fails at commit, in a
transaction, rather than producing two current reports and a question about which won.

`superseded_at` is separate from the superseding report's `approved_at` because they are
different events: a report can be superseded by one approved earlier (a correction of an
ordering mistake), and the record should say when the supersession was decided.

### Three ways a report is superseded, and all three record why

| Reason | Written by | Example |
|---|---|---|
| `refreshed` | The refresh orchestration | Two quarters filed since the last run |
| `rerun` | A full new run on the same company | The operator commissioned fresh research |
| `withdrawn` | The operator, by hand | A defect found after approval — the 172.1% margin |

`supersession_reason` is free text on top of that vocabulary and **cannot be blank**. This is
the same rule `settle_by_hand` and the thesis withdrawal already enforce, for the same
reason: an action that changes what the platform asserts must say why, in the operator's own
words, at the moment they take it.

### Withdrawal without a successor

`withdrawn` is the one case where there may be no superseding report — the operator wants the
wrong report to stop being current and has nothing to put in its place. The columns allow it:
`superseded_at` and `supersession_reason` are set and `superseded_by` stays null — which is
why the index tests `superseded_at IS NULL` as well as `superseded_by IS NULL`, rather than
gaining a fourth column to mean "withdrawn". **Current means approved, not superseded by
anything, and not withdrawn**, and those are three readings of two columns.

### What a superseded report keeps

Everything. It stays immutable, readable at its own address, with its own footnotes resolving
to its own hashed artefacts, forever. Its calculations, facts, citations and evaluation rows
are untouched. What changes is that every surface which shows it says, in one line, that it
was superseded — when, by what, and why — with a link.

**A superseded report reaches no current surface.** It does not answer *"what do we think
about this company"*, it is not what the monitor validates against, and it is not what a
thesis points at. That is invariant 3's territory: a figure from a superseded report is not a
figure the platform is currently asserting, and the type that carries a figure to a current
surface reads only from the current report.

## What is given up, named rather than discovered

**A branching history.** This models a chain, not a tree: one current report, each superseded
one pointing at its successor. Two analysts holding different current views of the same
company is not expressible, and will not be until ADR 0120's accounts exist — at which point
the index becomes `(user_id, company_id)` and this decision is extended rather than reversed.
That migration is one line and is named here so nobody plans around the single-user shape.

**The operator can no longer keep two reports and decide later.** Approving a second report
supersedes the first, at commit. That is the point, and it costs the ability to hold a
deliberate pair.

## Consequences

**The report library groups by company**, showing the current report with its history beneath
it, rather than a flat list sorted by date where the reader infers precedence.

**The migration is the one that can fail.** Creating the partial unique index on existing data
fails if any company already has two approved reports. The stored corpus has three approved
reports across three companies, so it succeeds today — but the migration checks first and
stops with a readable message naming the company rather than failing on the index, because a
migration that aborts halfway through is worse than one that refuses to start.

**Nothing is backfilled.** Existing reports are current, because they are, and the reason
columns stay null. A supersession that never happened is not recorded as having happened.

**The refresh gains its acceptance criterion**: a refresh leaves the prior report readable at
its own address, marked superseded, with the reason naming what moved.

## Alternatives considered

**A `is_current` boolean.** One column, easy to read, and impossible to constrain — two rows
with `is_current = true` is a valid table state, and the only thing stopping it is code that
remembers. The partial unique index makes the invalid state unrepresentable, which is the
whole difference.

**Version numbers on reports.** `reports.version` and "the highest wins" is the same problem
with extra arithmetic: nothing stops two rows at version 3, and a withdrawal has no
representation at all.

**Edit the report in place on a refresh.** Cheapest, and it destroys the record. The audit's
whole value came from being able to read what the platform said in September; a platform that
rewrites its own conclusions cannot be audited, and invariant 1 is meaningless if the document
the hashes support keeps changing.

**Let the newest approved report win implicitly.** This is today's behaviour, unwritten. It
cannot express a withdrawal, it cannot say why, and it silently promotes a report approved by
mistake at 23:59 over the right one approved at 23:58.
