# The shell and the menu system

*The frame every page sits in. One chrome, many tools, and no tool owning the frame.*

---

## At a glance

| | |
|---|---|
| **Where** | Every page. `base.html`, `_nav.html`, `_shell/drawer.html`, `_shell/badges.html` |
| **Who sees it** | Everybody, on every screen, all the time |
| **What it must do** | Say where you are, get you anywhere, carry the disclaimer, and hold the one drawer |
| **Token state** | **Clean.** All four templates are on the design tokens. This is the reference |

---

## The job

**Make the product navigable at any width, from any page, without a script — and carry the
three things no page may ship without: the disclaimer, the navigation, and the drawer.**

---

## What is in it

The shell has five parts, and one of them is invisible.

### 1. The header

A single row, currently containing, left to right:

| Element | Notes |
|---|---|
| **Menu** button | A `<details>`/`<summary>` disclosure. **No JavaScript at all** |
| **Product name** | "Ageiantic Equity Research", linking to `/` |
| **"Not investment advice"** chip | Deliberately beside the product name — the first thing read on every page, not something found at the bottom |
| A hidden trigger | Fetches the badge counts once the page is on screen. Invisible, zero-sized |

The header is bounded to the same width as the page beneath it, so its contents line up with
the content rather than running to the window edge.

### 2. The menu panel

Opens from the button, absolutely positioned so opening it does not push the page down.
Currently 16rem wide, capped at 70% of viewport height, scrolling internally.

**Contents: the navigation, then the preferences.**

Navigation is four sections, each a group of items:

| Section | Items |
|---|---|
| **Overview** | Overview → `/` |
| **Research** | Requests → `/requests` *(carries the count badge)* · Reports · Skills · Knowledge |
| **Portfolio** | Portfolio → `/portfolio` |
| **Platform** | Settings · Costs · Health · API |

**Sections are contributed by tools, not written into a template.** A tool ships a section and
adds one line to a registry; nothing in the shell changes. A test fails when a tool is
registered and forgotten. **The navigation is data — you may restructure it freely.**

The active item is marked with `aria-current="page"` and a visual treatment. **The longest
matching prefix wins**, so `/requests/new` lights *Requests* rather than whichever item was
declared first, and a run console keeps *Requests* lit while the operator is three levels
down.

**Presence in the menu is not permission.** The route's own dependencies decide access. A
navigation that could grant it would be a second, weaker place for a rule kept in exactly one.

### 3. The preference controls

At the bottom of the menu panel, under an "Appearance" heading. Both are plain forms with
submit buttons — no scripting, no save-on-change.

**Colour scheme** — a three-way group: Light · Dark · Auto. Remembered in a cookie and
stamped on the `<html>` element by the server, so the palette is correct in the very first
byte and there is no flash to beat. "Auto" is the *absence* of the attribute, which is what
leaves the operating system in charge.

**Show / hide explanations** — guidance mode. Reveals numbered callout chips beside things
worth explaining. Off by default. It is server state rather than a browser preference,
because a reload that lost it would be noticed — which is the test for what the client may
own.

Both controls return the operator to the page they were on. The destination is checked rather
than trusted: only a same-site absolute path is honoured, because a redirect that followed a
form field anywhere would be an open redirect.

### 4. The badges

The Requests item carries a count. **It arrives after the page does.**

The navigation ships an empty slot; a single request fills every registered slot at once,
out of band. The reason is composition: computed inline, one tool's slow query would be paid
for on every other tool's first paint, invisibly.

Each badge carries a `count`, a short `label` and a `title`. The label is not decoration — a
bare numeral beside a word is read aloud as "Requests 3" and means nothing. The registered
label is *"runs waiting for your approval"*, so the slot speaks as "3 runs waiting for your
approval". The `title` — "Waiting for you" — is the same count named rather than spoken, for
the tile on the main menu, so the menu and the dashboard cannot disagree about a number.

**Zero renders nothing, not "0".** The slot is empty and CSS hides an empty slot. A badge is a
hint; an absence is the honest rendering of nothing to hint at.

**Redis being down must not cost you the navigation.** Counts are cached briefly and
best-effort in both directions.

### 5. The drawer

One drawer, in the shell, so no page grows a second focus trap. Empty and `hidden` until
something is swapped into it.

**The semantics are markup; the behaviour is script.** `role="dialog"`, `aria-modal`,
`aria-labelledby` are in the HTML — so the panel is a dialogue in the DOM a reader inspects,
not only in the one a script got round to editing. Focus trapping, Escape, and the scroll
lock are the script's, because a server cannot send behaviour.

Currently: full-height, right-hand side, up to 28rem wide, with a title bar and a Close
button, over a translucent overlay.

**Every trigger is an ordinary link first.** The `href` goes to the full page; htmx
intercepts the click and swaps a fragment into the drawer instead. With scripting off, the
same click is a navigation. **This is the pattern for every enhancement in the product.**

The overlay is not focusable and is not a button — it is a convenience for a mouse. Every
reader without one has Escape and the Close button, both real controls.

### 6. The footer

The disclaimer in full, and the version.

---

## Inputs

| Control | Type | Where it goes |
|---|---|---|
| Colour scheme | Three submit buttons in one form | `POST /_shell/theme` → cookie → redirect back |
| Guidance | One submit button, toggling | `POST /_shell/guidance` → cookie → redirect back |

Both are CSRF-protected. Nothing else in the shell collects anything.

---

## States

**Default** — menu shut. It is shut until you open it; nothing about the page suggests
otherwise.

**Menu open** — the panel over the page, the current item marked.

**Badges pending** — the first paint. Slots are empty, and the page is complete without them.

**Badges failed** — Redis or the database unreachable. Slots stay empty. **The menu must
still work.**

**Drawer open** — focus inside, background not scrolling, Escape returns focus to the row it
came from. Not to the top of the document — to the row.

**Scripting off** — the menu opens and closes, every link navigates, both preference forms
submit, and drawer triggers become page loads. Everything works.

**Dark, light, auto** — three states, and an explicit light choice must beat a dark machine.

**Guidance on** — numbered chips appear inline beside the things they explain.

---

## What is wrong today

**The menu costs a click on a wide screen that a sidebar gave for nothing.** This was a
deliberate trade, made knowingly and recorded: what it buys is a page that is the same shape
at every width, one nav element instead of a responsive pair, and a header that does not
compete with the content for the left edge. What it costs is that on a 27-inch monitor —
which is where this tool is actually used — every navigation is two actions instead of one.
**This is the single biggest open question in the shell, and it is genuinely open.**

**Nothing shows where you are except inside the closed menu.** With the panel shut, a run
console three levels deep looks exactly like the front page. There is no breadcrumb, no
section label in the header, nothing. The active state is computed correctly and then hidden
behind a click.

**The header is four unrelated things in a row** — a menu button, a wordmark, a legal chip
and an invisible element — with no hierarchy and nothing else. It is the most-seen 56 pixels
in the product and it does the least work of any part of it.

**"Not investment advice" is the second-most prominent thing on every page.** It must appear
on every page; it does not have to compete with the product name for attention on every page.
It also appears twice, header and footer.

**The drawer is used in exactly one place** — the work-list preview — despite being general
machinery. Several surfaces would benefit and none of them use it.

**No visible affordance says the menu contains the appearance controls.** A reader looking
for dark mode has no reason to open something labelled "Menu".

---

## What to improve

**1. Settle the menu-versus-sidebar question, and settle it with the width in mind.** The
options are a persistent sidebar at wide widths collapsing to the current panel at narrow
ones; a horizontal top-level nav with the tool sections inline; or keeping the panel and
solving orientation another way. The recorded reason for the current choice was to avoid a
responsive pair of nav elements — that is a real cost, and it is a cost worth paying if the
gain is orientation on every page. **Note the mechanical constraint:** the badge slot may
appear only once per page, so a nav rendered twice needs the badge in exactly one of them.

**2. Give every page a location.** Whatever the nav becomes, the answer to "where am I"
should be visible without opening anything. A run console is inside *Research* → a request →
a run, and none of that context is on the screen today.

**3. Rebalance the header.** It has room for orientation, the current tool, and a primary
action, none of which it currently carries.

**4. Decide how the disclaimer earns its place.** Once per page, unmissable when it matters,
not competing with navigation. It cannot be removed; it can be placed better.

**5. Use the drawer, or justify its narrowness.** Peer rationales, a source excerpt, a
calculation's inputs, an assumption's justification — every one of those is something an
operator wants to glance at without losing their place, and every one currently costs a
navigation. The machinery is built, tested, accessible and used once.

**6. Design the badge slot for more than one badge.** One is registered; the mechanism exists
for several, and several will arrive.

---

## What must not change

**The disclaimer is in the shell.** No page may ship without it, and that is structural, not
a convention.

**The menu works with no JavaScript.** `<details>`/`<summary>` gives focus, Enter, Space and
Escape for free. Whatever replaces it must give the same for free or must degrade to
something that does.

**One drawer, one focus trap, in the shell.** A second implementation of focus trapping is
how a chrome layer becomes a framework, and the copy that drifts is always the newer one.

**Every enhanced trigger is a working link first.**

**The theme is server-stamped from a cookie.** Not a `<head>` script.

**The badge slot appears exactly once per page.** Two elements with the same id would mean
the first fills and the second shows nothing for ever.

**Navigation describes navigation, not authorisation.**

---

## Done when

- The operator can tell which tool and which page they are on without opening anything.
- Reaching any top-level destination from any page takes one action on a wide screen.
- The whole shell works with scripting off — menu, both preference forms, every link.
- The disclaimer is on every page, once, and is not the loudest thing on it.
- The shell holds four navigation sections and eight, without redesign.
- Nothing in the shell breaks when Redis is down, when the database is down, or when the
  badge request never returns.
