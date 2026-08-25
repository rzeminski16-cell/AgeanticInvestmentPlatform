# ADR 0089 — The run you are watching has an address

**Status.** Accepted
**Date.** 2026-08-25
**Extends.** The nav-as-data decision in `web/nav.py` and the drift test that guards it.
**Required by.** Roadmap §3.12.

## Context

The redesign puts **Active run** in the navigation, one action from anywhere. That is the
right instinct: a run takes tens of minutes, an operator returns to it repeatedly, and today
reaching it means going through Requests to a request to its run.

It is also not expressible. `NavItem.href` is a `str` on a frozen dataclass, and
`tests/test_shell_nav.py` asserts that every `href` resolves to a real registered route.
A run console lives at `/runs/{job_id}`, which is not a literal, and an item whose
destination depends on state is an item that sometimes has no destination at all.

Two bad answers were available. Making `href` a callable would retire the guarantee that
every navigation item goes somewhere — the drift test is what stops a page shipping with no
way to reach it, and it can only compare literals. Rendering the item conditionally, present
when a run is active and absent otherwise, would make the navigation change shape as work
progresses, which is the one thing a persistent index must not do.

## Decision

**`GET /runs/active` is a real route that redirects.**

- **303** to the operator's current run when there is one.
- **303 to `/requests`** when there is not, because the honest next action for somebody with
  no run in flight is to look at their requests.
- It resolves the run **in the same service the console uses**, so the navigation and the
  page cannot disagree about which run is current.
- It is **a navigation item like any other**: a literal `href`, matched by the same prefix
  logic, guarded by the same drift test.

**"Current" is defined once, in code, and never guessed by a template.** The most recently
started run that has not reached a terminal state; failing that, the most recently touched
run. Whatever it is, it is one function, and it is the same one the item and the redirect
both call.

### What it is not

**Not a page.** It renders nothing and holds no state. Opening it in a browser lands the
operator somewhere real, which is the whole of its behaviour.

**Not conditional.** The item is always present and always goes somewhere. An operator with
no runs reaches their requests, which is a better answer than a missing item and a much
better one than a dead link.

## Consequences

- **Navigation stays literal data**, and the drift test keeps its meaning: every item's
  destination is a route the application registers, checked at build time rather than hoped
  for at run time.
- **One more redirect on a common path.** On loopback this is not perceptible, and it buys a
  destination that cannot be stale — a computed `href` baked into a rendered page would point
  at whichever run was current when the page was built.
- **The pattern generalises.** Any future "the thing I am working on" item — an open thesis, a
  watchlist in progress — takes this shape rather than inventing a second one: a literal
  address that resolves, not a navigation entry that computes.
