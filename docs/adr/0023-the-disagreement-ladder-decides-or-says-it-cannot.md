# 23. The disagreement ladder decides, or says it cannot

- **Status:** Accepted
- **Date:** 2026-07-30

## Context

Two admissible sources report different values for the same thing. That is not an edge case:
a 10-Q and a 10-K state the same quarter on different dates, an issuer's annual report and the
regulator's copy of it are parsed by different extractors, and a vendor's standardised figure
is a recast of a filing nobody standardised.

The failure to design against is not "the wrong figure was chosen". It is that a figure was
chosen and **nobody was told**. A report whose revenue came from the second of two conflicting
filings, with no record that the first existed, reads exactly like one where no conflict ever
arose — and the reader has no way to find the single decision that most affected the number.

`docs/PLAN.md` §2.9 states the ladder. This records the decisions taken in implementing it,
which are mostly about *order*, and one about what the ladder refuses to do.

## Decision

### The ladder is total, and raises rather than defaulting

Seven rungs, each with an explicit guard. There is no `else`. A pair of positions matching no
rung raises `UnresolvableDisagreementError`, and a hypothesis property over the whole input
space — every tier, basis, unit, date and value — asserts it never happens.

The alternative shape, `if ... elif ... else: pick_one()`, would satisfy the same tests and be
wrong in the way that matters: a "no rule applied, so we took the first" outcome is
indistinguishable in the database from a rule having fired. The acceptance criterion for this
task is that no rung resolves by falling through to a default, and the only way to hold that
under later edits is for the fall-through to be an exception rather than a branch.

### The order is not the order §2.9 lists

§2.9 numbers the rules; it does not fix an evaluation order, and two of them have to move.

**Units are checked before anything.** Everything below rung 0 compares numbers, and a
mismatch is not a disagreement about a quantity — it is two different quantities. Nothing is
converted: invariant 5 says a unit mismatch raises rather than coercing, and a resolver that
turned GBP into USD to get a comparison would be that failure with a different name.

**Scale detection comes before the tier rule**, which is the one placement worth arguing
about. A tier-1 figure of 245,122 against a tier-2 figure of 245,122,000,000 in the same unit
is not evidence about which publisher is more reliable — it is a parsing bug in one of them.
Resolving it by tier would take a defect and give it a provenance record saying the regulator
said so, and every check downstream would pass. It escalates, with the exponent named in the
rationale so a reviewer knows to look for a lost multiplier rather than for a restatement.

Detection is deliberately narrow: the ratio must sit within 0.5% of a power of ten, opposite
signs are not a scale error (that is a sign-convention bug and saying otherwise misdirects the
reviewer), and nothing is a power of ten away from zero.

**Basis mismatch is checked before filing date.** An as-reported figure and a restatement of
the same period are both true, of different questions. §2.9 folds this into the date rung
("later filed_date IF as-reported basis matches"); pulling it out makes it fire when the dates
match too, which the folded version misses.

### The result is a function of the pair, not of the call

Two facts arrive from a query in whatever order the planner chose. `resolve` puts them into a
canonical order — better tier, then earlier filing, then every remaining field as a tie-break
— before deciding anything. The tier and date rungs then read that ordering directly, which is
why they can name a winner without re-comparing.

Without it, `resolve(a, b)` and `resolve(b, a)` could name different winners, and a re-run over
the same evidence would silently change the reported figure. The tie-break covers **every**
field, including the raw unit string, because the rationale prints the positions in order:
anything that can appear in the prose has to appear in the key, or the wording is decided by
argument order.

### Agreement is not recorded, and the schema enforces it

Rung 1 is the ordinary case. A row per agreeing pair would bury the rows that mean something
under the rows that do not, so `record_resolution` returns `None` and a check constraint
refuses `resolution = 'agreed'`. The rule lives in the schema rather than only in the one
service that currently obeys it.

### Recording is idempotent on a fingerprint over identities, not values

A retried step or a re-run must produce one row. The fingerprint covers the topic, the kind and
the two references — **not** the values. A fingerprint that moved when a figure was re-extracted
a hair differently would let the same conflict be recorded twice, and the duplicates would look
like independent conflicts in the appendix.

### Escalations are inside the gate-2 payload, not beside it

`final_gate_payload` carries them, so they are inside the hash the approval records. "Approved
with these three conflicts outstanding" is then a verifiable statement about what the operator
saw, rather than a claim about what a page happened to render. It also means settling one
invalidates a stale approval of the older draft, which is correct: the evidence changed.

### `material` is half of §2.4's trigger, and says so

§2.4 escalates on "two Tier ≤4 sources disagreeing by > 2% **on a material figure**". The first
half is computed here. The second is not: whether a figure matters depends on what the report
leans on, which the ladder cannot see. The flag raises the banner; a person decides what it
means. Naming the column `material` and quietly meaning "credible-source conflict" would have
been the kind of half-truth that survives review because it reads correctly.

## Consequences

**A human decision does not overwrite the rule that escalated.** `settle_by_hand` appends to
the rationale and sets `resolved_by = 'human'`, leaving `rule` intact. Replacing it would erase
the reason a person was asked in the first place.

**A rule decision cannot be settled by hand.** Overriding a rule is a different act from
settling an escalation and needs its own record; reusing this one would make the two
indistinguishable afterwards.

**Escalating on any clean power of ten will occasionally be conservative.** A genuine 10x
disagreement between a filing and a blog escalates rather than resolving by tier. That is the
intended trade: the cost is an operator glance, and the alternative cost is a mis-parsed figure
carrying a tier-1 provenance record.

**The ladder does not do point-in-time selection**, and must not be mistaken for it.
`select_point_in_time` decides which facts are admissible as at a date; this decides between
the ones that already are. Rung 4 catches a restatement that reaches it, but that is a safety
net rather than the intended input.

**`sector_profiles` ships seeded in the same migration.** Not part of the ladder, and included
because Phase 3 should open on data rather than on a migration. The seed is written out
literally in migration 0014 rather than imported from `aer.core.sectors` — a migration that
imports application code stops describing the schema as it was and starts describing the code
as it is now — and a test asserts the two still agree.

## Alternatives considered

**Let a model settle conflicts the ladder cannot.** Rejected for figures, and the `ResolvedBy`
enum keeps `AGENT` as a member with a docstring saying so: never for a number. A model choosing
between two filings is exactly the arithmetic-in-prose failure `CLAUDE.md` opens with.

**Store only the winner, with a note.** Rejected. The requirement is that losing evidence is
retained for the report's disagreement appendix, and a note is not evidence — the rejected
figure, its tier, its filing date and its source are what a reader needs to disagree with the
decision.

**A tolerance band per concept.** Attractive — revenue and share count deserve different
tolerances — and deferred. One tolerance that is honest about being one tolerance is better
than a table of thresholds nobody has calibrated, and the relative difference is stored on
every row so the calibration data is being collected.
