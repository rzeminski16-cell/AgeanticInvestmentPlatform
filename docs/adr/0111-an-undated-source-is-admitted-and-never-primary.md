# ADR 0111 — An undated source is admitted, and never primary

**Status.** Accepted
**Date.** 2026-09-09
**Amends.** ADR 0021 (look-ahead is checked twice, on the latest date), whose two checks are
untouched: what changes is that a second rule stops travelling on their flag. ADR 0010
(point-in-time is selection, not filtering) stands unchanged. ADR 0044 (an aggregate is dated
by its newest component) is not reopened — see below.
**Required by.** The first full manual acceptance pass (`docs/plan/acceptance-2026-09-08.md`
§3, §5), where the operator wrote that *"the publishing date should only matter for context
but it should be used when needed"* and where a live run's plan named news sources and its
evidence table held none.

## Context

`decide_quarantine` began with this:

```python
if point_in_time and publication_date is None:
    return QuarantineDecision(quarantined=True, reason=NO_PUBLICATION_DATE)
```

and continued to the look-ahead test proper — a source published after the as-of date is
refused — gated on the same boolean. **Two policies wore one flag.**

They are not the same question. "Is this demonstrably newer than the as-of date?" is a
finding about a document. "Can this be shown to predate the as-of date?" is a finding about
what we know about a document, and everything undatable fails it: most news reporting, an
issuer's press-release index, a regulator's aggregate endpoint, any page whose publisher
does not stamp a date a machine can read. Under the old rule the only way to read any of
them was to switch off `point_in_time`, which also switched off the check that refuses a
source published after the as-of date. Nobody would choose that trade on its merits, and
the acceptance run shows what happened instead: the operator left point-in-time on, the
research steps proposed news sources, and every one of them was quarantined before it
reached the look-ahead test. The evidence table's undated rows were the run working exactly
as written.

The measurement in the acceptance document is the reason this is one ADR and not a rewrite:
`as_of_date` appears 527 times across 86 source files, and the places where it *changes
behaviour* number about fifteen. This is one of them, and it is the one the operator noticed.

## Decision

**The datability rule gets its own policy, and an undated document that policy admits is
capped at tier 5.**

Three parts, and the third is what makes the first safe.

### 1. `work_orders.undated_sources_admissible`

A column on the run root, defaulting to `true`, editable on the request form as a second
pair of radios beside the point-in-time pair. `decide_quarantine` reads it in place of
`point_in_time` for the first branch and nothing else changes: the look-ahead branch keeps
`point_in_time`, in the same order, refusing a demonstrably post-dated source whatever the
new policy says. All four corners are asserted in `test_the_two_rules_are_independent`.

It is a column rather than a constant because **a run has to record which rule it ran
under**. An evaluation reading a finished run, a replay two years later, and an operator
asking "why is this here?" all need the answer from the record rather than from the code
version that happened to be deployed. `_RUN_ROOT_FIELDS` gains it for the same reason the
other three are there.

Existing rows are backfilled to `true` and **nothing that already happened changes**: the
quarantine decision is written onto each `source_documents` row at acquisition, so a
document already refused for `no_publication_date` stays refused, with its reason, in every
run that fetched it. The policy governs what the next acquisition decides.

### 2. `SourceTier.as_evidence(dated=...)`

An undated document counts as `T5_SECONDARY` however authoritative its publisher. Tiers 5
and 6 are returned unchanged — the cap is a floor, and a blog nobody can date does not
become citable by acquiring a second problem.

The argument is the one `aer/sources/tiering.py` already makes: a tier records *what compels
the publisher to be accurate*. A page that cannot be dated is not the regulator's record of
a filing; it is a page asserting one, and an undatable copy carries none of what made the
original tier 1. `T5_SECONDARY` already means "usable, corroborating, never the sole support
for a number", which is exactly the standing this deserves, so the rule is said in the
vocabulary the platform has rather than in a seventh tier nothing else knows how to read.

`source_tier` is **not** overwritten. What the provider is, and what kind of thing it
published, is a fact about the document; the cap is a verdict about it. Both are stored,
both are shown, and the evidence table prints the recorded tier beside the cap whenever they
differ — the cap alone reads as a mis-tiered document, and the record alone hides the rule.

Every judgement of *worth* reads `evidence_tier`: the tier told to the writer, the tier index
the primary-source shortfall check walks, the badge on the evidence table and the order that
table sorts in. Telling a writer a document is tier 1 while the shortfall check counts it as
tier 5 is the mismatch that produced *"a single primary filing, the Form 10-Q"* in the live
run (gap R12).

**A section's tier ceiling is the one place that keeps reading the recorded tier**, and the
line is worth stating because it was drawn the other way first. `SectionPolicy.max_tier_rank`
is a statement about publishers — *do not show me secondary reporting* — where the cap is a
statement about worth. Filtering the listing on the cap put this ADR's own blanket refusal
back through a different door: a section with a ceiling of 4 stopped seeing an undated filing
at all, instead of seeing it and being told it had no primary source. Six custom-section tests
found it. `test_a_tier_ceiling_still_shows_it` is the boundary, stated.

### 3. Marked wherever it is read

- The evidence table prints **Undated** rather than an em dash, and names the cap.
- The evidence pack tells the writer `"dated": false` alongside the capped tier, rather than
  leaving it to be inferred from a null date by a model that will otherwise describe an
  undated page in the words it would use for a filing.
- The rendered report already carried the C3 marker and its legend for a cited source with
  no publication date; it now derives that from the same `is_dated` rule rather than from
  `publication_date` alone, so the conservative bound decides it in both places.
- `/api/runs/{id}/sources` carries `evidence_tier` and `dated` beside `source_tier`.

## The half that was already true

The acceptance document proposed "never as the sole support for a number" as a rule to
build. Reading the code settles it: it is already structural. Since ADR 0109 a numeric claim
names exactly one figure — a stored financial fact, a recorded calculation, or an
attestation — and `record_claim` refuses one that does not. A number never comes from a
citation, so an undated page cannot be the sole support for a number no matter what tier it
sits at. What the cap actually decides is the *primary*-source floor, and that is worth
being precise about: **admitting an undated source buys a section the page and does not buy
it a primary source.**

## Consequences

**ADR 0044 is not reopened, and its loose end changes hands.** That decision dates a
generated aggregate — SEC `companyfacts`, which is assembled on request and was never
published — by the newest thing inside it, because *"the document cannot have existed before
its newest component was filed"* is a true claim and the right bound. It stands: 0111 does
not invent dates, it changes what an absent one costs. What changes is the case 0044 left
open, *"an aggregate carrying no facts keeps no date and stays quarantined"*, and the generic
recording path that supplies no date at all. Both are now admitted at evidence tier 5, which
is a fair description of a document asserting facts it does not contain: no section's
primary-source floor can be met by it, nothing can rest a figure on it, the facts extracted
from it carry the filings' own `filed_date` as they always did, and the evidence table says
it is undated. The rule that was implicit in a missing date is now explicit in a tier.

**The temporal-compliance metric measures the run again.** `SourceObservation` carries both
policies. A run that admitted an undated document on purpose is not a run that let one slip
through, and scoring it as one is what put a temporal-compliance failure on page 1 of the
live AAPL report for seven documents the acquisition layer had deliberately admitted.

**What is given up.** A run that admits undated sources cannot claim to be a clean
historical replay of what a reader could have known on a given date: an undated page might
be from after it. That is why the policy is recorded per run rather than removed, why the
form names the consequence beside each choice, and why the cap exists — the page can inform
a sentence and cannot carry a figure or satisfy a floor.

**What is not given up.** The look-ahead check, at acquisition and again at claim time. The
quarantine record and its override. `publication_date_latest` as the bound admissibility
turns on. None of these were the thing standing between the operator and a news source.

## Two defects found on the way, and fixed here

**The web form could not turn point-in-time off.** The control became a pair of radios (so
that the state it is *not* in gets named, per `ui.choice_group`), and the parser was still
the checkbox's: `values["point_in_time"] != ""`. The string `"false"` is not empty, so an
operator who chose "allow later-published sources" got a point-in-time run and nothing said
otherwise. `_chosen` reads the word; `form_values_from` renders it; an absent key still
means the guard stays on, because nothing selected is not a decision to relax a rule.

**A quarantine reason reached the screen as an identifier.** `Quarantined:
no_publication_date`, in three templates. The raw-identifier ratchet exempts `reason` on the
argument that a refusal's reason is a sentence the platform wrote — true everywhere except
here, where the three reasons are module constants. They now resolve through
`vocabulary.QUARANTINE_REASONS`, with a completeness test walking the constants where they
are declared.

## Alternatives considered

**Leave it inadmissible.** The status quo, and the reason no news source appeared in the
plan. It also makes `T5_SECONDARY` mostly unreachable in practice, since a secondary source
is exactly the kind least likely to carry a machine-readable date — a tier the platform
defines, tiers into, and then refuses.

**Admit it and change nothing else.** Then an undated page claiming to be a 10-K satisfies a
section's primary-source requirement, and the platform's own account of how authoritative
its evidence was becomes a statement about what a publisher asserted. The floor is the only
thing that makes "requires primary" mean anything.

**A seventh tier for "undated".** It would need a migration on a shared Postgres enum, a
place in the conflict-resolution ordering, an entry in every tier mapping, and an answer to
"is an undated tier-1 filing better or worse evidence than a dated newspaper?" that nobody
needs to have. Tier 5 already answers the question the platform actually asks of it.

**Derive datability at read time instead of storing the policy.** The rule would then be
whatever the current build thinks, applied retroactively to every run ever recorded. A
platform whose premise is that the stored record can be trusted does not get to re-decide
old runs by deploying.
