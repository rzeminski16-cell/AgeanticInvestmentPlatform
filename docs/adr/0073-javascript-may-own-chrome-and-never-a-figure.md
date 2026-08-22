# ADR 0073 — JavaScript may own chrome and never a figure

**Status.** Proposed
**Date.** 2026-08-22
**Successor to.** ADR 0006. This record **narrows** that decision; it does not reverse it.
**Required by.** `docs/investment-os.md` §8, which asks that the two things 0006 left
implicit be named before the shell is built.

## Context

The Investment OS shell wants right-side drawers, split-screen review workspaces, sticky
multi-step wizards, a knowledge-graph canvas, a guidance overlay and badge counts in the
navigation. Written as a list, that reads like the specification of a single-page
application, and the obvious reading is that ADR 0006 has been outgrown.

It has not been, and the argument matters more than the conclusion, because it is the
argument that will be made again at the next screen.

**0006's decisive argument was never about interactivity.** It was about where the truth
lives:

> The decisive argument is duplication of domain state. Approval status, validation
> results, run progress and cost totals are all authoritative on the server. Rendering
> them on the server means there is exactly one copy. An SPA would hold a second one and
> would need reconciliation logic whose failure mode is showing a stale approval.

**Multi-tool makes that argument stronger, not weaker.** One tool holding a stale mirror
of approval state is one bug. Five tools sharing one server-rendered truth is still one
renderer; five tools each holding their own mirror is five reconciliation layers that must
agree with the server and with each other — over records whose entire value is that they
say what the database says. The screen count grew. The reason for the original decision
grew with it.

### Where htmx actually strains

Taken one item at a time, honestly, because a survey of only the easy cases is not a
decision.

**Drawers are `hx-get` into a mount.** A drawer is a fragment fetched on demand and put
somewhere. The server already renders every fragment one would contain.

**Split-screen review is two independently refreshable panes.** Two targets, two URLs.
Nothing about a pane sitting beside another rather than above it involves the client
owning state.

**A sticky wizard is what this codebase already calls a gate.** `web/pages.py` gives each
step its own URL — `/runs/{job_id}/plan`, `/sector`, `/peers`, `/themes`, `/review` — the
decision is one `POST /runs/{job_id}/gates/{gate}`, the answer is a 303, and a hidden
`payload_hash` proves what was on screen when the operator clicked. A client-held wizard
would keep the operator's answers in memory until a final submit, which is exactly the
arrangement the hash exists to forbid: what is displayed and what is approved must be the
same object. **Wizard state must not live in the browser anyway**, so here htmx is not
merely adequate — it is the correct shape.

That leaves two genuine gaps, and they are the only two.

**Overlay semantics.** A panel that traps focus, closes on Escape, locks the background
scroll and announces itself as a modal is browser behaviour, not markup a server can send.
There is no `<dialog>`, no `role="dialog"`, no `aria-modal` and no sticky positioning
anywhere in the 42 templates today. This is new ground, not a pattern to extend.

**Several independent live regions on one page.** The one htmx page in the repository gets
this right: `requests/_form.html` declares `<div id="form-errors" aria-live="polite">` and
the form swaps `innerHTML` into it, so the region node survives while its contents change —
which is the mutation a screen reader announces. A page carrying an attention feed, a badge
count and a drawer at once has three such regions, and the failure mode is silent: an
out-of-band swap that replaces a live-region element rather than its contents announces
nothing at all. Holding that discipline across a dozen fragments is a client-side concern
with no server-side equivalent.

## Decision

**ADR 0006 stands.** Server-rendered Jinja, progressively enhanced with htmx, remains the
interface, and every form still works with scripting off.

Two things 0006 left implicit are now named, because "a small islands-style component" for
"anything genuinely needing it" is a pre-authorisation with no shape — and an unshaped
permission is how a second application arrives one reasonable diff at a time.

### A JS island is a named component with a declared contract

An island is admitted by name, in its own commit, carrying: the screen it serves; a
declared JSON contract for what the server hands it; a dependency vendored and committed
exactly as `htmx.min.js` is, with its version and SHA-256 recorded in the commit that adds
or updates it; and **no domain state**. It renders a view of what the server sent. It never
holds the authoritative copy of anything, so there is nothing to reconcile and nothing to
go stale.

This is ADR 0067's rule one storey down: a capability that is not registered by name does
not exist. An island arriving as an ordinary dependency bump has skipped every part of it.

### A client-owned chrome layer, and chrome is not state

Focus management, scroll locking, the open-and-closed lifecycle of an overlay, keyboard
dismissal and the `data-guidance` attribute are the browser's to own. None is mirrored on
the server, none is persisted, and no record depends on any of them.

That is also the test. **If a reload would lose it and something would then be wrong, it is
state and it belongs on the server. If a reload would lose it and nobody would notice, it is
chrome.** A drawer that reopens closed after a refresh is a mild irritation; an approval
that survives only in a browser tab is a lie about a hash.

## JavaScript may own chrome, never a figure

The rule, in one line, meant to be quotable at a review:

> **JavaScript may own chrome, never a figure.**

"A figure" already has a definition here — ADR 0054 spent a whole record on it: a numeral
denoting a quantity, which invariant 3 requires to be a stored fact or a recorded
calculation. Four things follow, and each forbids something somebody will want.

**No client-side arithmetic.** JavaScript has no decimal type. `CLAUDE.md` requires
`Decimal` for money and ratios, and `templating.percent` states the reason plainly: a weight
that changes in the third decimal place because it passed through a float is a number nobody
can reconcile against the database. A total summed in the browser is unreconcilable by
construction.

**No client-side formatting of a figure.** `aer/render/display.py` is the one door —
`money`, `scalar`, `cell`, `date_text`, `prose` — and ADR 0056 makes formatting
configuration applied at render, from a `HouseStyle` the caller resolves once. A JavaScript
formatter is a second house style nobody configured, on a platform that already paid for
having one formatter too few: gap R1 printed `11729000000 USD` mid-sentence in a live report.

**No optimistic UI.** Not merely disallowed here — actively wrong. An optimistic approval
paints a decision the server has not recorded, and the server may refuse it, because the
payload hash exists precisely so that a stale page's approval fails. Painting "approved"
before the hash has been checked is a lie about a hash, told most convincingly at the moment
it matters most.

**No client-held approval state.** The corollary, and the sentence 0006 was written to
prevent.

Underneath all four is one fact: every guard this platform owns — citation verification, the
numeral rule, ADR 0066's `figure_plausibility` — runs on the server, over recorded rows. **A
figure computed in the browser is a figure no guard has ever seen.**

The run console is the honest edge of the rule. `static/js/console.js` is 316 lines that own
chrome — a dot colour, an elapsed clock, a summary sentence — and refuse to own anything
else: a step the page has never seen triggers `window.location.reload()`, because "inventing
a row here would mean two places rendering a step, and the copy that drifts is always the one
in JavaScript". It also does `spend.textContent = "£" + state.spend_gbp`, where the server
produced every digit and the client added a glyph. That is the outer limit, not a licence to
move further: new work sends the symbol from the server with the digits.

## The knowledge graph is the one legitimate island

`web/templates/knowledge/graph.html` draws the entire node-and-edge graph as static SVG with
no script whatever. Every coordinate comes from `services/graph_view.py::place`, pure
arithmetic where "the same nodes and edges always produce the same picture" — which is what
lets a test hold the drawing rather than eyeball it. It carries `role="img"` with an
`aria-label`, a `<title>` per node, and `currentColor` so it follows the page's own theme.
It prints, and it needs nothing an inline-script policy would forbid.

**If the graph needs only to be bigger and clickable, it stays server-drawn.** Those are a
viewBox and an anchor, and an accessible, printable, script-free picture is worth more than
the effort saved.

Pan, zoom, drag and live filtering are a different request, and that is the island. It
qualifies: the layout stays server-computed, the component receives placed nodes as JSON and
owns only the viewport transform, and no figure passes through it. If it is built, it is
built under this ADR — admitted by name with its contract stated, not arriving as a line in
`package.json`.

## Two constraints that are already tests

Both will eventually be met by somebody who has forgotten they exist.

**No third-party asset may appear in a rendered page.**
`tests/test_web_pages.py::test_loads_no_third_party_asset` scans the page for `https://cdn`,
`http://cdn`, `unpkg.com`, `jsdelivr` and `googleapis`. Inter is therefore vendored under
`static/fonts/` with its version and SHA-256 in the commit, exactly as `htmx.min.js` is, or
it is not used at all. A `<link>` to Google Fonts is a failed build, which is the intended
answer.

**The sampled-class assertion means a forgotten `just css` fails loudly.**
`test_the_stylesheet_contains_classes_the_templates_use` requires `antialiased`,
`max-w-5xl`, `tracking-tight` and `border-red-300` in the compiled stylesheet. Widening
`max-w-5xl` for a sidebar is a knowing change that updates that list in the same commit. And
`styles/app.css` already carries `@source "../static/js/*.js"` because `console.js` composes
Tailwind class names: an island that does the same must be scanned too, or its classes exist
in no stylesheet and its failure state renders with no colour.

## Consequences

**The drawer JavaScript is written once, in one file, and never per page.** A second
implementation of focus trapping is how a chrome layer becomes a framework, and the copy
that drifts is always the newer one.

**A page that needs a figure needs a server round trip, and that is the intended cost.** On
loopback it is imperceptible. What it buys is that every number on every screen of every
tool came from the same renderer, under the same guards, that put it in the report.

**The permission 0006 granted is now spendable only deliberately.** An island needs a record
naming it; a chrome layer needs no permission at all; and the ambiguous middle — a helpful
script that totals a column, holds a wizard's answers, or paints an approval ahead of the
server — is closed by a rule rather than argued screen by screen.
