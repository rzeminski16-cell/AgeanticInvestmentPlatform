# The constraints

*What the design must satisfy. Each one has a reason, and the reason is the part worth
reading — a constraint you understand is one you can design elegantly around, and a
constraint you have only been told is one you will resent.*

**The challenge appendix is at the bottom.** It lists what a designer might reasonably want
that these constraints forbid, and what changing each would cost. If your design needs one
changed, that is a legitimate conversation — it needs an architecture decision record, not a
diff.

---

## The two that decide everything else

### 1. The server renders every page. There is no client-side view layer.

Jinja templates on the server, styled with Tailwind, progressively enhanced with htmx. No
React, no Vue, no build-time component framework, no client-side router, no client-held
store.

**Why.** The argument was never about interactivity — it is about where the truth lives.
Approval status, validation results, run progress and cost totals are authoritative on the
server, so rendering them there means there is exactly one copy of each. A client-side
application would hold a second copy and need reconciliation logic whose failure mode is
showing a stale approval. On a platform whose entire premise is that the record can be
trusted, that is the worst class of bug available.

**What it means for you.** Every state change is a page or a fragment from the server. Design
in *pages and fragments*, not in components with local state. A pattern that needs the client
to remember something between interactions is a pattern that needs rethinking or a URL.

### 2. Every form works with JavaScript switched off.

Not "degrades acceptably". Works. A plain `POST` followed by a redirect is the real path;
htmx only changes where the response is rendered.

**Why.** A form whose validation depends on a script is a form that silently accepts anything
the moment the script fails to load — and these forms commission spending and record
financial positions.

**What it means for you.** Every interactive control must have a no-script answer:

| Instead of | Use |
|---|---|
| A scripted dropdown | `<details>`/`<summary>` — focusable, Enter/Space toggles, Escape closes, all free |
| A scripted combobox | `<input list>` over a `<datalist>` — native, typeable, no script |
| A modal wizard holding answers | A URL per step, each posting and redirecting |
| A toggle that saves on change | A form with a submit button |
| A confirm() dialogue | A confirmation *page* that can show what will be destroyed |

Every one of those is already in use in the product. The pattern is: **render the enhanced
control hidden, and let the script reveal it.** Scripting off gets the complete table and no
search box, rather than a search box that does nothing.

---

## The line: JavaScript may own chrome, never a figure

There *is* JavaScript — 606 lines of it, in four files, and it is allowed to do specific
things.

**Chrome, which it may own.** Focus management, scroll locking, keyboard dismissal, an
overlay's open-and-closed lifecycle, a drawer's open state, a long table's scroll position,
filtering rows that are already on the page.

**The test for what counts as chrome:** *if a reload would lose it and something would then be
wrong, it is state and it belongs on the server. If a reload would lose it and nobody would
notice, it is chrome.* A drawer that reopens closed is a mild irritation. An approval that
survives only in a browser tab is a lie about a hash.

**Three things it may never do.** Each forbids something a designer will want.

**No client-side arithmetic.** JavaScript has no decimal type. A weight that changes in the
third decimal place because it passed through a float is a number nobody can reconcile
against the database. So: no summing a column in the browser, no "total selected", no live
recalculation as a slider moves.

**No client-side formatting of a figure.** There is one renderer for money, scalars, dates
and percentages, resolved against a configured house style. A JavaScript formatter is a
second house style nobody configured. So: no `toLocaleString`, no client-side rounding, no
"£" prepended to a number the client shaped.

**No optimistic UI, and therefore no client-held approval state.** A gate approval carries a
hash of exactly the payload that was on screen; a stale page's approval is *supposed* to
fail. Painting "Approved" before the server has checked the hash is a lie about a hash, told
at the moment it matters most.

**The underlying fact:** every guard this platform owns — citation verification, the numeral
rule, plausibility checking — runs on the server over recorded rows. **A figure computed in
the browser is a figure no guard has ever seen.**

### What the existing scripts do

Useful as a calibration of how much client behaviour is normal here.

| File | Lines | What it owns |
|---|---|---|
| `app.js` | 37 | One htmx setting: swap the body of a 422 so validation errors are not discarded |
| `console.js` | 316 | The run console's live updates: a dot colour, an elapsed clock; reloads rather than inventing a step row |
| `drawer.js` | 185 | The drawer: focus trap, Escape, scroll lock. Written once, never per page |
| `tables.js` | 68 | Filtering rows already on the page. Fetches nothing, computes nothing |
| `branches.js` | 60 | A form whose radio choice leads to the fields it needs: hides the branch not chosen, shows every branch with scripting off |

`console.js` marks the honest edge. It takes exactly one liberty — prepending a "£" glyph to
digits the server produced — and that is the outer limit, not a licence.

**A new script is a named component with a declared contract**, admitted in its own commit,
holding no domain state, with its dependency vendored and hashed. It is not a line in
`package.json`. If your design needs one, say so explicitly in the handover so it can be
recorded rather than absorbed.

---

## The rest

**Nothing loads from a CDN.** Every stylesheet, script and font is served from the
application's own origin. The platform is local-first and must work with no internet
connection, and a third-party script tag on a page that can reach the database and the
provider credentials is a supply-chain risk taken for convenience. **Practically: you may
specify any typeface, but it will be vendored and hashed.** Inter is what ships today, as a
variable file, latin and latin-ext, no italic.

**The disclaimer is part of the shell, not of any page.** "Not investment advice" cannot be
designed off a screen, because no screen owns it. It currently appears twice — a chip beside
the product name in the header and a line in the footer. Where it goes is yours; *that it
appears on every page* is not.

**A status is never carried by colour alone.** Every chip in this product pairs its colour
with a word, deliberately. A status a colour-blind reader cannot read is a status that is not
there.

**A provenance badge is a link, and it is always a link.** The reference is a required
argument — a badge cannot be constructed without somewhere to point. A badge reading
"Calculated" that links nowhere asserts a lineage while offering no way to read it, which is
precisely the confidently-wrong surface this platform exists to prevent.

**Two provenance chips, not one.** Class (`Source fact` · `Calculated` · `Attested` ·
`Assumed` · `Judged`) and confirmation (`Suggested` · `Unconfirmed` · `Confirmed by <name> at
<time>`) are orthogonal axes. One control cannot carry both: the ordinary state of half an
assumptions page is "a calculation nobody has confirmed", which a single chip cannot say. Do
not merge them to save space.

**Navigation is data.** Sections and items are frozen rows in Python; the template is a loop.
A test asserts every page is either in the navigation or explicitly named as reachable only
from inside another page. **You may restructure the navigation freely** — that is data — but
a design that needs per-item bespoke markup fights the mechanism.

**The theme is stamped by the server from a cookie.** Not a `<head>` script. The cookie is
already in hand when the page is built, so the palette is correct in the first byte and there
is no flash to beat. Three states: light, dark, and system — where "system" is the *absence*
of the attribute, which is what leaves `prefers-color-scheme` in charge.

**Reduced motion is respected globally.** `prefers-reduced-motion` already collapses every
animation and transition. Design animation as an enhancement that can vanish entirely.

**UK English**, in every user-facing string. Colour, not color. Organisation, not
organization.

---

## Challenge appendix

*What a good designer will want, that the above forbids. Each entry says what it would cost
so the conversation can be had properly. **Any of these is arguable — none is arguable in a
pull request.** They need an ADR.*

### Worth arguing for

**A richer knowledge-graph canvas — pan, zoom, drag, live filtering.** Currently drawn as
static SVG with coordinates computed in Python, which is what lets a test hold the drawing.
This is *already* identified as the one legitimate candidate for a JavaScript island: the
layout stays server-computed, the component receives placed nodes as JSON and owns only the
viewport transform, and no figure passes through it. **If you want this, ask for it** — the
path is open and the record for it is half-written.

**Client-side sorting of a long table.** Filtering is already permitted, because hiding a row
computes nothing. Sorting is the same class of operation — it reorders rows that are already
on the page — and the line is only crossed if sorting implies a recomputed total or a
re-fetched page. A sortable table with a server-rendered total is defensible. Ask.

**A different typeface, or more of them.** No constraint at all beyond vendoring. Say what
you want and it gets committed with its hash.

**Restructuring the navigation entirely** — different grouping, different depth, a persistent
sidebar instead of a menu panel. This is *data*, and the current shape was itself a
deliberate trade recorded in the code: the sidebar was replaced by a menu panel to buy a page
that is the same shape at every width. If you want the sidebar back, or something else
entirely, that is a design decision and it is yours to make. Note the one mechanical
constraint in [`06-implementation-contract.md`](06-implementation-contract.md): the badge
slot must appear exactly once per page.

### Costly, but possible with a decision

**A single-page application.** Explicitly reconsidered and explicitly deferred: it becomes
defensible *if* the platform ever becomes multi-user and gains genuinely interactive analysis
screens. The typed JSON API that sits under every page exists partly so that migration would
be a swap rather than a rewrite. It is not on the roadmap and nothing in this brief should
assume it.

**A component library with a JavaScript runtime** — shadcn, Radix, Headless UI. Same
territory. The interaction primitives these give you (dialog, popover, combobox, disclosure)
all have native HTML answers that are already in use here, and those answers work with
scripting off, which the libraries do not.

### Not arguable

These are not stylistic preferences and no design need is going to outweigh them.

- **Any arithmetic in the browser.** Including a sum, an average, a percentage, or a
  currency conversion.
- **Any formatting of a figure in the browser.** Including thousands separators and rounding.
- **Optimistic rendering of an approval, an approval count, or a gate decision.**
- **Removing the disclaimer from any page.**
- **A provenance badge that is not a link.**
- **Loading anything from a third-party origin at runtime.**
- **A status conveyed by colour alone.**

---

**Next:** [the information architecture](02-information-architecture.md)
