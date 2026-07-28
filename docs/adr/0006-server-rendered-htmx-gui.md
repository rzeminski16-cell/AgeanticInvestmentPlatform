# 6. The GUI is server-rendered HTML, progressively enhanced with HTMX

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

The platform needs a browser interface for a single local user. It has to collect a
structured research request, present a plan for approval, stream progress during a run,
show a draft alongside its validation results, and take a final approval decision.

Three properties constrain the choice, in this order:

1. **The report is the product.** Time spent on the interface is time not spent on the
   research pipeline, which is where the whole value of the project sits.
2. **It must be presentable.** One stated goal is to show this to employers. A GUI that
   looks like a debugging tool undersells work that is not.
3. **It must not become a second application.** Every piece of state that has to be
   mirrored between a server and a client is a place where the two can disagree, and a
   disagreement about *which figures are approved* is not a cosmetic bug.

The realistic options were a React (or similar) single-page application against a JSON
API, server-rendered HTML with a small enhancement layer, or a Python-native GUI
framework such as Streamlit, Gradio or NiceGUI.

## Decision

**Server-rendered Jinja2 templates, styled with Tailwind, progressively enhanced with
HTMX. A typed JSON API sits underneath and is the contract.**

### Why not a single-page application

An SPA would mean a second toolchain, a second dependency tree, a second set of types
describing the same objects, and a build step in the way of every change. It buys
client-side interactivity this application barely needs: the interactions here are a
form, a table, an approval button and a progress feed. None of them wants a client-side
state machine.

The decisive argument is duplication of domain state. Approval status, validation
results, run progress and cost totals are all authoritative on the server. Rendering them
on the server means there is exactly one copy. An SPA would hold a second one and would
need reconciliation logic whose failure mode is showing a stale approval — which, in a
system whose premise is that the record can be trusted, is the worst class of bug it
could have.

### Why not Streamlit, Gradio or NiceGUI

They are faster to a first screen and worse everywhere after. The interaction model is
a whole-script rerun, which fights against long-running jobs and multi-step approval
flows. Layout control is limited in ways that matter for property 2. Most importantly
they own the HTTP layer, so the same code cannot serve both a browser and an API — and
this application needs an API regardless, for its own tests.

### Why HTMX rather than plain forms

The pipeline is long-running and the GUI has to show progress, inline validation and
partial refreshes. HTMX gets those by swapping server-rendered fragments, so the server
stays the only renderer and there is no client-side view layer to keep in step.

**Every form must work with JavaScript disabled.** HTMX improves the experience; it is
never load-bearing. A normal `POST` followed by a redirect is always the fallback path,
which also happens to make the flows straightforward to test without a browser.

### Why the JSON API exists from the start

The pages call services; the API calls the same services. Neither calls the other. If
this decision is ever reversed, the replacement consumes an API that already exists and
is already tested, so reversing it is a swap rather than a rewrite. It also means the
integration tests exercise the same code path the GUI does.

## Consequences

### Accepted costs

- **A Node toolchain is needed to change the styling.** Tailwind compiles the stylesheet.
  This is mitigated by **committing the compiled output** to
  `src/aer/web/static/css/app.css`, so a checkout is immediately runnable and CI needs no
  Node. The cost of that mitigation is that the stylesheet can drift from the templates
  if someone adds a class and forgets `just css`; `tests/test_web_pages.py` checks a
  sample of classes to catch the common case.
- **No rich client-side interactivity.** Anything genuinely needing it — a live chart
  with client-side filtering, say — would need a small islands-style component. Nothing
  in the planned scope does.
- **Server round-trips for interactions an SPA would handle locally.** On loopback this
  is not perceptible.

### Assets are vendored, never fetched

Every stylesheet, script and font is served from `/static`. Nothing loads from a CDN.
Two reasons, both load-bearing: the application is local-first and must work with no
internet connection, and a third-party script tag on a page that can reach the database,
the artefact store and the provider credentials is a supply-chain risk taken purely for
convenience. `htmx.min.js` is copied from the npm package and committed, with its version
and SHA-256 recorded in the commit that adds or updates it.

### The disclaimer is part of the shell

"This is not investment advice" lives in `base.html`, not in individual pages, so a page
cannot ship without it. That is a structural guarantee rather than a convention, and
`tests/test_web_pages.py` asserts it.

## Alternatives reconsidered later

If a future version becomes multi-user and gains genuinely interactive analysis screens,
an SPA becomes defensible — the API contract this decision preserves is exactly what
would make that migration incremental rather than a rewrite. Revisit then, not before.
