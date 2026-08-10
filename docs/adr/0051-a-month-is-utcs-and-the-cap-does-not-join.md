# 0051 — A month is UTC's, and the monthly cap does not join

Date: 2026-08-10
Status: Accepted

## Context

Invariant 6 says cost is metered and capped **in code**, and that "caps that only warn are
caps that do not work". `BudgetGuard` was written with two ceilings — a per-run cap from the
request and a monthly cap from settings — and its docstring said both mattered: "a run that
respects its own budget while blowing the month's is still a run nobody agreed to."

`check` never read the monthly one. `aer.services.runs` passed `monthly_cap_gbp` in on every
run, `aer.services.skill_dry_run` did too, `costs` carried an index commented "the
monthly-budget query", and nothing anywhere compared a month's spend to anything. The field
was dead for the whole life of the engine. This is a worse shape than a cap that only warns:
it did not warn either, so an £80 month would hold a £2.50 request thirty-two times over
without a single log line.

It was found by reading, not by a failing test, and no test could have found it. Every
statement of the intent lived in prose.

Implementing it needs three decisions that a monthly cap does not obviously come with.

## Decision

**The month is UTC's.** `occurred_at` is stored timezone-aware in UTC, and the window opens
at the first instant of the UTC month. The alternative — the operator's local month — makes
the cap reset at a different moment depending on where they are standing, and makes a
January cap and a July cap different caps in a country with summer time. `check` takes an
injectable `now` so the boundary is testable without waiting for one.

**The query does not join.** `costs.job_id` is `SET NULL` rather than `CASCADE` (migration
0009) for exactly one reason: a cap you can get under by deleting the thing you spent it on
is not a cap. A month's total reached through a join to `jobs` would hand that escape
straight back, so `spend_this_month` filters on `occurred_at` alone and counts rows whose
job is gone.

**Both ceilings are checked, per-run first, and the refusal names which one fired.** The
narrower ceiling is the one the operator set on this request, so it is reported when both are
breached. `BudgetExceededError` carries `scope` as `per_run` or `monthly`, `RunState` reads
it back off the step that recorded the refusal, and the console changes what it says: raising
a request's own cap releases a per-run stop and does **nothing whatever** to a monthly one.
An operator sent to the wrong number by a generic message loses a minute and some trust.

**The run's own spend is counted once.** Its cost rows are already inside the month's window,
so the month's projection is `this_month + projected`, not `this_month + already +
projected`. Getting this wrong refuses runs that fit, which is the failure nobody reports
because it looks exactly like the cap working.

## Consequences

A run can now stop for a reason that has nothing to do with the request in front of the
operator, which is new and is the point. The pause is the ordinary `BUDGET_EXCEEDED` one — it
does not lose the work — but the remedy is a settings change or the turn of the month rather
than anything on the request, so the console had to learn to say so.

The monthly cap is enforced against the *effective* settings, which an operator can change
from a web page with no authentication in front of it. That is the caveat ADR 0050 already
records, unchanged: on loopback it is the same trust boundary as editing `.env`, and it stops
being so the moment A5 stops being optional.

Every existing budget test set a per-run cap below the first step's projected cost, so the
guard's *accumulation* — that a step is measured against what the run has already spent, not
in isolation — was never exercised. That was a separate hole, found by mutation in the same
pass, and `tests/test_budget.py` now covers both ceilings and the window's four edges.
