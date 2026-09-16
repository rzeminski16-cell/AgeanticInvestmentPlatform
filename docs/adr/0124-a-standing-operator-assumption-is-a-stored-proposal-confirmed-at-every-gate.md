# ADR 0124 — A standing operator assumption is a stored proposal, re-proposed into every run and confirmed at the gate

**Status.** Accepted — 16 September 2026, with the code, as the second half of Phase 1.6.
Written Proposed earlier the same day, before the code.
**Date.** 2026-09-16
**Extends.** ADR 0046 (a role proposes only what no filing can answer, every proposal states
its value, unit, proposer and justification, and a person confirms it at the gate) and ADR
0050 (an operator's standing decisions about cost and method live in the settings table,
recorded with who changed them and when, and apply to runs that start after the change).
**Does not touch.** ADR 0082's rule that `macro_observations` holds sourced, dated, published
observations and nothing else; ADR 0073's attestation grade and where it propagates; ADR
0046's rule that nothing is confirmed in advance.
**Required by.** `docs/V1.0_Alpha/05-delivery-plan.md` Phase 1.6, and the readiness audit's
finding that the assumptions gate asked for the same values on every run — the risk-free
rate and the equity risk premium on MSFT, four names on AZN, three on the bank — each with a
value, a unit and a written justification the service refuses to do without.

## Context

The discount rate decomposes into three assumptions, and after the first half of Phase 1.6
two of them have a source on an ordinary US run: the risk-free rate is fetched from the
documented series at the run's own vintage, and the beta is regressed from the price history
where a subscription exists. The third, the equity risk premium, is a judgement. No series
publishes it; it is a survey figure, a house view or a practitioner's estimate, and the gate
says so: *"The equity risk premium is a judgement with no series behind it, and no role in
this platform proposes one. Enter the premium you are using and cite where it comes from."*

So the operator types it — the same number, with the same justification — into every run,
at about £7 a run, and nothing carries the answer forward. The audit counted this as one of
the two values typed on every MSFT run and one of the three on the bank's.

There are three places a standing value could live, and two of them are wrong.

**Not `macro_observations`.** ADR 0082 reserves that table for a sourced, dated, published
observation: a row there names a series, a period and a vintage, and a typed premium wearing
those columns would be a number impersonating a publication. The first implementer will reach
for it because it is the table the risk-free rate went into; this record exists partly to say
no.

**Not an attestation.** ADR 0073's `attested` grade is for a value backed by nothing but the
operator's word *where a sourced value could have existed* — an FX rate typed in place of a
fetchable one — and its cost is that the grade propagates into a return type with no field
for the figure, so nothing resting on it reaches a shareable surface. That is the right price
for a shortcut and the wrong one for a judgement that has no sourced form: a discounted cash
flow that could never be shared because its premium is a premium would be a valuation nobody
could publish.

**The settings table.** ADR 0050 already gives the operator's standing decisions a home — the
model routing, the budgets, the house style — with who changed each and when in the audit
trail, and with the rule that a change applies to runs that start after it rather than to a
run halfway through. A standing premium is a decision of exactly that kind.

What the standing value must not do is skip the gate. ADR 0046's whole argument is that a
person agrees to each number in each run; a value confirmed once in settings and never again
would be a house view presented as the operator's judgement about this company today.

## Decision

### 1. A standing operator assumption is a settings override

Held under a key naming the assumption — `standing_equity_risk_premium` — with its
justification beside it under `standing_equity_risk_premium_justification`, editable on the
settings page and by environment variable like every other override, and recorded in the
audit trail as `settings.changed` with the value, the justification, who set it and when.

**The set of names that may stand is explicit and short**: judgements about the market rather
than about the company. The equity risk premium is the first and, today, the only one. A
company-specific number — a beta, a growth rate, a margin — is never a standing value,
because the same number for every company is the wrong number for most of them; and a
number a source can supply — the risk-free rate — is never one either, because a standing
value would then be the typed rate ADR 0082 already calls an attestation.

### 2. It is proposed into every run, and never confirmed in advance

The assumptions step proposes it as an ordinary `assumptions` row: the value, the `pure`
unit, the stored justification with the date it was set and who set it appended, and the
proposer `operator:standing` — through the same `propose` every other source uses, which
writes an unconfirmed row whatever its caller says. The gate lists it with the rest. The
operator confirms it, amends it, or types another over it; the run records what was proposed
and what was confirmed. A standing value changed next month never rewrites this month's run:
the row is the run's own.

### 3. It is never a macro observation and never an attestation

Nothing here writes `macro_observations`, and nothing here grades evidence. A confirmed
standing premium is the same kind of input a typed premium has always been — an assumption a
person agreed to, with its justification on the row — and every figure computed from it
carries the assumption as its source, exactly as before.

### 4. Validated at entry, as it would be at the gate

The settings form refuses a value outside the plausible band for the name — the same
`PLAUSIBLE_RANGE` the assumptions service applies at proposal — and refuses a blank
justification, so a standing value cannot be a number nobody explained. A justification is
required *with* the value: a premium set with no reason is refused rather than stored with a
placeholder.

### 5. Absence is ordinary

With no standing value the gate asks, as it does today, with the reason that the premium is
a judgement no series carries — and the reason now says where a standing one can be set.

## Consequences

**A US run with a price subscription reaches the assumptions gate with nothing outstanding**
— the rate from the series, the beta from the prices, the premium standing — and the
operator still reads and confirms the list. That is the whole of what changes on the gate:
one fewer thing to type, nothing fewer to agree to.

**Every run says what it stood on.** The proposed row's justification names the standing
value's date and author, so a premium set in 2025 and confirmed unread in 2027 says so on the
gate and in the report's assumptions table. `proposer_words("operator:standing")` reads as
*you*, which is who it is.

**The audit trail carries the value.** A `settings.changed` event records each standing
value and justification, so the month a report was approved can be read against the premium
in force that month.

**What this does not do.** It does not source the premium: a judgement stays a judgement,
and the report's assumptions table says so. It does not carry per-request assumptions between
requests: a value typed at one run's gate is that run's, and only a value set in settings
stands. And it does not admit a second standing name without a decision here — the list is a
short one on purpose, and a candidate (a country risk premium for a non-US listing, say)
joins it by amending this record.
