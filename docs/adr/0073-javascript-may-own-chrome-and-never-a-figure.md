# ADR 0073 — JavaScript may own chrome and never a figure

**Status.** Proposed
**Date.** 2026-08-22
**Required by.** `docs/investment-os.md` §8, which asks that the two things 0006 left implicit
be named before the shell is built.
**Amends.** ADR 0006 — the decision stands; this record narrows the islands permission it left
open.

## Context

The Investment OS shell wants drawers, split-screen review, sticky wizards, a knowledge-graph
canvas, a guidance overlay and badge counts in the navigation — a list that reads like the
specification of a single-page application, and the obvious reading is that ADR 0006 has been
outgrown. It has not been, and the argument matters more than the conclusion, because it will be
made again at the next screen.

**0006's decisive argument was never about interactivity.** It was about where the truth lives:
approval status, validation results, run progress and cost totals are authoritative on the
server, so rendering them there means "there is exactly one copy". **Multi-tool makes that
argument stronger**: five tools each holding a mirror is five reconciliation layers that must
agree with each other and with the server, over records whose whole value is that they say what
the database says.

### Where htmx actually strains

Most of the list does not strain it: a drawer is `hx-get` into a mount, split-screen review is
two targets and two URLs, and a sticky wizard is what `web/pages.py` calls a gate — a URL per
step, a 303, and a hidden `payload_hash` proving what was on screen when the operator clicked,
which a wizard holding its answers in browser memory would destroy. Two gaps are genuine.

**Overlay semantics.** A panel that traps focus, locks the background scroll and announces
itself as a modal is browser behaviour, not markup a server can send: there is no `<dialog>`,
`role="dialog"`, `aria-modal` or sticky positioning in the 42 templates today. New ground, not a
pattern to extend.

**Several independently updating live regions on one page.** The repository's one htmx page gets
this right: `requests/_form.html` declares `<div id="form-errors" aria-live="polite">` and swaps
`innerHTML` into it, so the region node survives while its contents change — the mutation a
screen reader announces. An attention feed, a badge count and a drawer make three such regions,
and the failure is silent: a swap replacing a live region rather than its contents announces
nothing.

## Decision

**ADR 0006 stands.** Server-rendered Jinja, progressively enhanced with htmx, remains the
interface, and every form still works with scripting off. What 0006 left implicit is now named:
"a small islands-style component" for "anything genuinely needing it" is a pre-authorisation
with no shape, and an unshaped one is how a second application arrives one diff at a time.

### A JS island is a named component with a declared contract

An island is admitted by name, in its own commit, carrying the screen it serves, a declared JSON
contract for what the server hands it, and **no domain state**: it renders a view of what the
server sent and holds the authoritative copy of nothing, so there is nothing to reconcile and
nothing to go stale. Its dependency is vendored with its version and SHA-256 recorded in that
commit, exactly as `htmx.min.js` is, and its class names are scanned by the stylesheet build, or
the build fails — `test_loads_no_third_party_asset` fails any page naming a CDN or font host,
and an island whose classes `app.css` never scans renders its failure state with no colour, as
`console.js` would without its own `@source` line. This is ADR 0067's rule one storey down: a
capability not registered by name does not exist.

### A client-owned chrome layer, and chrome is not state

Focus management, scroll locking, keyboard dismissal, an overlay's open-and-closed lifecycle, a
drawer's open state and a long table's scroll position are the browser's to own: none is
mirrored on the server, none is persisted, no record depends on any. That is the test. **If a
reload would lose it and something would then be wrong, it is state and it belongs on the
server. If a reload would lose it and nobody would notice, it is chrome.** A drawer that reopens
closed is a mild irritation; an approval that survives only in a browser tab is a lie about a
hash. **Guidance mode fails that test, so the flag is server state**, carried in the `shell`
object `templating.render()` injects on every page and remembered for the operator; only the
instant visual toggle of `data-guidance` is the client's.

### And never a figure

> **JavaScript may own chrome, never a figure.**

ADR 0054 spent a record defining "a figure": a numeral denoting a quantity, which invariant 3
requires to be a stored fact or a recorded calculation. Three rules follow, and each forbids
something somebody will want.

- **No client-side arithmetic.** JavaScript has no decimal type, and `templating.percent` states
  the cost: a weight that changes in the third decimal place because it passed through a float
  is a number nobody can reconcile against the database.
- **No client-side formatting of a figure.** `aer/render/display.py` is the one door — `money`,
  `scalar`, `cell`, `date_text`, `prose` — and ADR 0056 makes presentation a `HouseStyle`
  resolved once and applied at render. A JavaScript formatter is a second house style nobody
  configured, on a platform that has paid for having one too few: the report that prompted 0056
  printed a raw `11729000000 USD` into prose.
- **No optimistic UI, and so no client-held approval state**, which is the sentence 0006 was
  written to prevent. An optimistic approval paints a decision the server has not recorded and
  may refuse, because the payload hash exists so that a stale page's approval fails. Painting
  "approved" before the hash is checked is a lie about a hash, told at the moment it matters
  most.

Underneath them is one fact: every guard this platform owns — citation verification, the numeral
rule, ADR 0066's `figure_plausibility` — runs on the server, over recorded rows. **A figure
computed in the browser is a figure no guard has ever seen.** `static/js/console.js` marks the
honest edge: 316 lines that own a dot colour and an elapsed clock, reload rather than invent a
step row, and take one liberty — `spend.textContent = "£" + state.spend_gbp`, where the server
produced the digits and the client added a glyph. That is the outer limit, not a licence.

## A provenance badge is a link, and one chip cannot carry two axes

The shell renders provenance, so this record decides what a badge is; nothing else does, though
`docs/investment-os.md` §8 asks for one and ADR 0077 already grounds its sample-size rule in it.
**`ProvenanceRef(kind, identifier, href)` is a required argument of the provenance macro** — not
optional, not defaulted, so every badge is a link to its own drill-down. A badge reading
"Calculated" that links nowhere is the confidently-wrong surface this platform exists to
prevent: it asserts a lineage while offering no way to read it.

**Two independent chips, not the specification's five-way one.** A provenance chip — `Source
fact` · `Calculated` · `Attested` (ADR 0069) · `Assumed` · `Judged` (ADR 0070) — and a
confirmation chip — `Suggested` · `Unconfirmed` · `Confirmed by <name> at <time>`. One control
cannot carry both. Suggested and Approved are lifecycle states orthogonal to provenance class,
so a single chip cannot say "a Calculation nobody has confirmed", the ordinary state of half an
assumptions page. And "Approved" would collapse `Assumption.approved` — a person agreed to a
value — with an `approvals` row carrying a `payload_hash` — a person agreed to *exactly this
page*. The second is far the stronger guarantee, and hiding it behind the same word
overstates what the platform knows.

**Enforced the way ADR 0013 enforces section keys.** There, a test reads every module under
`src/` and fails if a seeded section key appears in code; here, a test greps every template for
the label strings and fails if any appears outside the provenance macro. The required argument
means a badge cannot be built without a ref; the grep means one cannot be typed around the
macro.

## The knowledge graph is the one legitimate island

`web/templates/knowledge/graph.html` draws the whole graph as static SVG with no script at all,
every coordinate from `services/graph_view.py::place`, where "the same nodes and edges always
produce the same picture" — which is what lets a test hold the drawing. **If it needs only to be
bigger and clickable, it stays server-drawn**: those are a viewBox and an anchor. Pan, zoom,
drag and live filtering are the island — the layout stays server-computed, the component
receives placed nodes as JSON and owns only the viewport transform, and no figure passes through
it. If built, it is built under this ADR: by name, not as a line in `package.json`.

## Consequences

- **The drawer JavaScript is written once, in one file, and never per page.** A second
  implementation of focus trapping is how a chrome layer becomes a framework, and the copy that
  drifts is always the newer one.
- **A page that needs a figure needs a server round trip, and that is the intended cost.** What
  it buys is that every number on every screen of every tool came from the same renderer, under
  the same guards, that put it in the report.
- **The permission 0006 granted is now spendable only deliberately.** An island needs a record
  naming it; a chrome layer needs none; and the ambiguous middle — a script that totals a
  column, holds a wizard's answers, or paints an approval ahead of the server — is closed by a
  rule rather than argued screen by screen.
