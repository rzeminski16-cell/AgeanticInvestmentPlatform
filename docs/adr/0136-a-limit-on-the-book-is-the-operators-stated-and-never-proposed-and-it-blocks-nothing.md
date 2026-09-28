# ADR 0136 — A limit on the book is the operator's, stated and never proposed, and it blocks nothing

- **Status:** Accepted (28 September 2026)
- **Date:** 2026-09-28
- **Amends:** the page specification's correction of 20 September 2026 (§11.1, repeated in §1,
  §2, §3 and §5), which removed ceilings from every page because none was stored anywhere.
  ADR 0080 (the risk analyst comments on numbers it cannot write) and ADR 0104 (a decision's
  size is a sentence) stand unchanged.
- **Decided by:** the operator, 28 September 2026, approving the V1.0 page specification: a
  concentration ceiling is a limit they set themselves.

## Context

Seven of the nineteen drawings assume limits. Platform has *Your book's limits*. Portfolio
filters *over ceiling*. Risk states each figure against its ceiling. Position and Company say
*over the 10% ceiling you set*. Today suggests *your concentration is close to its ceiling*.
The decision page warns before recording. The information architecture says the same: *top-five
weight within 2 points of the operator's own limit*.

The 20 September correction took them out. Its reason was sound for the thing it described: a
ceiling the platform invented would be the platform stating the operator's risk policy for
them, which ADR 0080 refuses one layer along. It also removed the thing it did not describe, a
limit the operator states, which is the operator's policy written down. The platform already
keeps that kind of statement. The stated shock on the risk page (ADR 0106) is the operator's,
the platform proposes neither the set nor the fall, and nothing about it has been a problem.

## Decision

### 1. A limit is a statement the operator makes, and nothing else makes one

Three kinds, each a fraction of the book:

- **A single position**: no one holding above this share.
- **The five largest**: the five largest holdings together.
- **A sector**: one sector's share, named by the same cut the exposure bands use.

A limit is stated on Platform, under *Book*, one kind at a time. Until the operator states one,
there is none. The platform offers no default, no suggestion and no example value, and neither
a model nor a skill file writes one: the field names are the operator's form's alone, and no
agent's output schema has them.

### 2. A limit blocks nothing

Not a trade, not a decision, not a run. It changes what pages say about the book. A holding over
a limit is labelled *over the {n}% ceiling you set*, in `warning`, with the word beside the
colour. The portfolio's *over ceiling* filter and its *positions over their ceiling* row count
against it. Today earns its fifth suggestion when the five largest are within two percentage
points of the operator's limit. The decision page's check says whether the what-if crosses one
(ADR 0137).

### 3. A limit is superseded, never edited

Each statement is a row with the book it limits, the operator who stated it (ADR 0120's
constraint: `user_id` on every new table), the kind, the sector where the kind needs one, the
fraction and when it was stated. Changing a limit writes a new row that supersedes the old one,
and withdrawing a limit records when and why. The history of what the operator allowed
themselves is part of the record a post-trade review reads.

### 4. The comparison is code, over the figures the risk page already strikes

A weight, the five largest and a sector's share are the risk page's recorded calculations (F12:
one implementation, surfaced twice). Whether one exceeds a limit is a comparison made in Python
when the page is built, not a figure: it prints no new number, only the word and the limit's
own value beside the recorded one.

### 5. Limits stay on the operator's own pages

They appear on Today, Portfolio, Position, Company, Risk, Decision and Platform. They never
reach a report or anything that leaves the machine: a report's closing section is the
operator's copy alone already (ADR 0129), and it does not print them.

## What was rejected

**A default limit, such as 10% for a single position.** A default is the platform stating a
policy, which is exactly what the 20 September correction refused.

**A limit that blocks.** The operator's book is their own, and a tool that refuses a trade has
confused advice with control (page specification §11).

**Editing a limit in place.** It would lose what the operator allowed themselves when they made
a decision, which a review of that decision needs.

## Consequences

A migration adds the limits table. Platform gains the *Book* section's form. Portfolio,
Position, Company and Risk draw each limit beside the figure it limits. Today can earn the
fifth suggestion. The page specification's 20 September correction is itself corrected, in
place and dated, where it said no ceiling is stored.
