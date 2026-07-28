# 7. Where request validation lives, and why the core never reads the clock

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

The research request is the root of everything downstream. Its rules are ordinary — a
ticker pattern, a date that is not in the future, a cost ceiling, a supported exchange —
and every one of them has to hold for both the JSON API and the HTML form.

Two constraints pull in opposite directions:

1. `CLAUDE.md` requires `aer.core` to be **pure**: no I/O, no globals, no clock reads. It
   is the part of the codebase that must be trivially testable.
2. Two of the required rules need outside knowledge. "`as_of_date` must not be in the
   future" needs today's date. "`max_cost_gbp` must not exceed the per-run budget" needs
   the configuration.

The obvious implementation — `date.today()` inside a validator — satisfies the rule and
breaks the purity requirement, and it makes the rule untestable at any date but the one
the test happens to run on.

## Decision

**Split the rules by what they need to know, and pass outside facts in as arguments.**

### Three layers, one path

| Layer | Holds | Reads the clock? |
|---|---|---|
| `aer.core.schemas.request.ResearchRequestCreate` | Every rule expressible from the payload alone: ticker pattern, currency, ISIN check digit, weight ordering, horizon range | No |
| `aer.core.schemas.request.check_limits(payload, limits)` | The two contextual rules, with `RequestLimits(today, per_run_budget_gbp)` supplied by the caller | No |
| `aer.services.requests.limits_from(settings)` | Reads `datetime.now(UTC)` and the settings, and builds the `RequestLimits` | Yes |

The impurity exists in exactly one function, at the edge, where it is visible. And
"reject a future as-of date" becomes a function you can test at any date without freezing
a clock — `check_limits(payload, RequestLimits(today=date(2100, 1, 1), ...))` is a
one-line test of the boundary.

### One validation path, two front doors

The JSON API and the HTML form both call `aer.services.requests.create_request`. Neither
validates anything itself.

This is the decision that matters most. A form that checks its own rules eventually
checks slightly different ones, and the drift always goes the same direction: the form
becomes more permissive than the API, and something invalid reaches the database through
the door nobody was testing. `tests/test_request_form.py` asserts that both paths reject
the same submission.

### Universe rules are heuristics, and say so

`aer.core.universe` refuses OTC venues, funds, investment trusts and unsupported
exchanges. It works from what the operator typed, because **no external call is made
while a request is being written** — writing one is offline, instant and free, and
`resolved` stays false until something confirms the identity.

Working from typed values makes these heuristics. Two consequences are accepted
deliberately:

- **Every refusal names its rule and explains itself.** "SPY appears to be a fund rather
  than an operating company" plus the reason, not "rejected".
- **The micro-cap rule cannot fire at request time.** Market capitalisation is genuinely
  unknown before ticker resolution, and inferring one from a ticker would be exactly the
  invented number this codebase exists to prevent. `is_micro_cap(None)` returns `False`,
  the function takes the figure as an argument, and the rule fires once resolution
  supplies a real one.

The false-positive direction is the dangerous one. A rule that refuses to research
*Trustpilot* because its name contains "trust" is worse than no rule: it is wrong in a
direction the operator cannot work around. `tests/test_universe.py` therefore tests the
accepting direction as carefully as the refusing one — and it caught a real one during
implementation, where a bare "vanguard" in the fund-name pattern would have blocked
Vanguard Natural Resources, a genuine NASDAQ-listed oil and gas producer.

### Errors arrive in two rounds, not one

Every *schema* problem is reported together, and every *service* problem is reported
together. A submission with both kinds shows the schema problems first and the rest on
resubmission, because the service rules cannot run on a payload that failed to construct.

Closing that gap would mean evaluating domain rules over half-parsed input — trading a
rare second round trip for a permanent source of rules applied to values that were never
valid. Not worth it. The limit is documented in `aer.web.forms` rather than hidden.

## Consequences

### The browser tests earn their cost

Two bugs were found by Playwright that the in-process HTTP tests structurally could not
see, because both are about what a browser does with a correct response:

1. **HTMX discards non-2xx responses by default.** The server returned a perfectly good
   422 with the rendered error list; HTMX dropped it, and the operator saw nothing happen
   at all. Fixed by configuring `htmx.config.responseHandling` in
   `web/static/js/app.js` — the status stays honest and the client is told to render the
   body.
2. **Rotating the CSRF cookie without rotating the form's hidden input.** HTMX swaps only
   the error container, so the form kept its original token while the cookie moved on.
   The form then looked entirely normal and could never be submitted again. Fixed with an
   out-of-band swap of the hidden input in the error fragment.

Neither is visible to a test that asserts on a response body. Both would have been found
by the first person to use the form, which is a worse place to find them.

### The landing page degrades; nothing else does

`GET /` renders whether or not the database is up, and says what is wrong when it is not.
The most likely reason an operator is looking at it is that something is not working, and
a blank 500 tells them nothing.

That leniency stops there. Every page that displays data fails loudly without a database,
because rendering "no requests yet" while the database is unreachable would be stating
something false — and a system whose premise is that the record can be trusted cannot
afford a page that quietly lies about what the record contains.

### Percentages in, fractions stored

The form asks for portfolio weights in per cent because that is how people say them; the
schema and database store fractions. The conversion happens in `aer.web.forms`, uses
`Decimal`, and is tested. Asking an operator to type `0.025` invites someone to type
`2.5` and silently commission research against a 250% position.
