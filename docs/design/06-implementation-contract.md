# The implementation contract

*For turning a design into templates. Read it before you finalise, not after — three or four
things here will change a layout decision, and finding them at build time is expensive.*

---

## The mechanism, in one page

**Jinja2 templates rendered by FastAPI, styled with Tailwind v4, enhanced with htmx.**

A page is a Jinja template extending `base.html`, rendered by a handler that fetches its data
and calls `render()`. `render()` is the only door: it injects the shell, the disclaimer, the
theme attribute, the guidance flag and a CSRF token, so a handler cannot forget any of them.

**Everything is server-rendered.** There is no build step for the templates and no client
framework. The only build step is Tailwind compiling the stylesheet, and its output is
committed so a checkout runs without Node.

---

## How little htmx is actually used

Across 54 templates, **sixteen htmx attributes in total**:

| Attribute | Uses | Where |
|---|---|---|
| `hx-target` | 4 | Form errors, the drawer |
| `hx-swap` | 4 | " |
| `hx-swap-oob` | 2 | The badge counts |
| `hx-get` | 2 | The drawer, the badges |
| `hx-post` | 2 | The request form |
| `hx-trigger` | 1 | Badges, on load |
| `hx-disabled-elt` | 1 | The submit button during a post |

**Four patterns, and that is the whole vocabulary.** If your design needs a fifth, that is
fine — but be explicit about it, because each one is a place the no-script path has to be
designed too.

### The four patterns

**1. Inline form errors.** The form has both `action` and `hx-post` pointing at the same URL.
With script, the 422's rendered error fragment is swapped into a live region. Without it, the
identical POST returns a full page. **The validation is the same code either way.**

```html
<div id="form-errors" aria-live="polite">…</div>
<form method="post" action="/requests/new"
      hx-post="/requests/new" hx-target="#form-errors" hx-swap="innerHTML"
      hx-disabled-elt="#submit" novalidate>
```

**The live region node must survive the swap.** Swap `innerHTML`, never the region itself — a
swap that replaced the region announces nothing, silently.

**2. The drawer.** A link that is a real link first.

```html
<a href="/runs/{id}"                          <!-- no script: a navigation -->
   hx-get="/research/runs/{id}/preview"       <!-- with script: a fragment -->
   hx-target="#aer-drawer-body" hx-swap="innerHTML"
   data-drawer-title="Contoso plc is waiting for you">Preview</a>
```

`drawer.js` opens the panel *because content arrived*, not because anything told it to. Those
four attributes are the entire contract.

**3. Out-of-band counts.** One hidden element fires one request on load; the response contains
several fragments each targeting its own id. `hx-swap="none"` on the trigger, because the
element is only a trigger.

**4. Progressive reveal.** The enhanced control is rendered `hidden`; the script removes the
attribute. Scripting off gets the complete table and no search box.

---

## What `render()` gives every template

A `shell` object, always present, **constructible with no database**:

| Field | What it is |
|---|---|
| `shell.nav` | The navigation sections and items |
| `shell.active` | The key of the item the current path is inside |
| `shell.path` | The current path, for the preference forms' `next` field |
| `shell.theme` | `light` · `dark` · `system` |
| `shell.theme_attr` | What goes on `<html data-theme>`; **empty for `system`** |
| `shell.guidance` | Whether callouts are shown |
| `shell.guidance_attr` | What goes on `<body data-guidance>` |

Plus `disclaimer`, `app_version`, `csrf_field` and `csrf_token`.

**`StrictUndefined` is on.** A template naming something the handler did not supply raises
rather than rendering blank. Good — but it means every field a design introduces must be
supplied by every handler that renders that template.

---

## Twelve things that will change a design decision

**1. The badge slot may appear exactly once per page.** Out-of-band swaps target an id. Two
nav elements — a sidebar plus a mobile menu, say — would put two elements with the same id on
the page, and the first would fill while the second showed nothing for ever. **A responsive
design with two nav renderings must put the badge in exactly one of them.**

**2. Buttons must be inside a real `<form>`.** A sticky decision bar, a floating action, a
two-pane gate layout — all fine, provided the buttons are in the form element that carries the
CSRF token and the payload hash. HTML permits `<button form="gate-form">` outside the form,
which solves most layouts; a browser test caught a submit button outside its form once, so it
is a real failure mode.

**3. Anything destructive is a POST.** Which means a button, not a link. It cannot be styled as
a link and be a link.

**4. A view worth returning to is a GET with the state in the URL.** The portfolio date is the
model. A control whose state lives only in a form is a view that cannot be bookmarked.

**5. A figure arrives already rendered.** Templates never format numbers. If a design needs a
figure abbreviated, aligned, coloured by sign or split into parts, the *server* must produce
those parts — so say so, because it changes the data contract.

**6. Money has two renderings that must not mix.** Report house style is millions; the
portfolio is exact to the penny. A shared component that formatted money would be a third.

**7. Empty `h1`s exist today** because several are built from a variable that can be blank. If
your design puts a title in the header, specify the fallback.

**8. The theme attribute is absent for `system`.** Design your CSS so the media query decides
when no attribute is present, and an explicit choice wins in both directions. The existing
`dark:` variant does this correctly and is worth reading before you write any theme CSS.

**9. Tailwind cannot see inside Jinja by default.** The stylesheet's `@source` lines tell it
where to scan — templates, Python files and `static/js/*.js`. **A class name composed at
runtime exists in no scanned file and gets no CSS.** The run console composes step-status
colours in JavaScript, which is why the JS directory is scanned at all. If a design needs
runtime-composed classes, they must be scannable or the element renders with no colour.

**10. The compiled stylesheet is committed.** Changing styles means running the Tailwind build
and committing the output. A test samples classes to catch the common case of forgetting.

**11. Everything is vendored.** Any typeface, icon set or library becomes a committed file with
its SHA-256 recorded. No runtime request leaves the machine. Icons are best as inline SVG.

**12. There is no icon set.** The whole interface uses three glyphs — `☰` on the menu button,
`←` on twenty-one back links and `→` on three forward links — plus punctuation. The only `<svg>`
in the product is the knowledge graph, which is a drawing rather than an icon. If your design
uses icons, it is introducing the first icon system, and it should be specified as one: which
icons, at what sizes, with what accessible names, inline rather than as a font.

---

## What a handover should contain

So that a developer can build it without a second round of questions:

1. **Screens, in both colour schemes**, at a narrow and a wide width.
2. **The token set**, revised, with contrast stated for every text pairing.
3. **A type scale and a spacing scale.**
4. **Every component with every state** — default, hover, focus, active, disabled, error,
   loading, empty — and its no-script appearance where that differs.
5. **Focus states**, explicitly, in both schemes.
6. **Table specification**: density, alignment, tabular numerals, header behaviour, and what
   happens at a narrow width. *(The page body must never scroll horizontally; a wide table
   scrolls inside its own container.)*
7. **Empty, loading, partial, error and refused states for every surface**, not just the happy
   path. The state lists in each page specification are the checklist.
8. **Anything that needs new server data**, called out. A design that shows "waiting since
   Tuesday" needs a field that is not currently passed to that template.
9. **Anything that needs JavaScript**, called out by name, with what happens without it. A new
   script is a named component with a declared contract, admitted in its own commit — not an
   assumption.

---

## Where to look in the code

Not required reading, but the best examples of each pattern:

| For | Read |
|---|---|
| The shell and what every page gets | `src/aer/web/templates/base.html`, `web/shell/context.py` |
| Navigation as data | `web/nav.py`, `web/shell/registry.py` |
| The component macros | `web/templates/_ui/` |
| The tokens and the `dark:` variant | `web/styles/app.css` |
| A well-built form | `web/templates/requests/_form.html`, `_field.html` |
| The drawer contract | `web/templates/_shell/drawer.html`, `static/js/drawer.js` |
| The honest edge of what a script may do | `static/js/console.js` |
| Progressive reveal | `static/js/tables.js` |
| The best-designed page in the product | `web/templates/runs/financials.html` |
| The least-designed one | `web/templates/runs/review.html` |

---

**Back to:** [the index](README.md) · [the page specifications](pages/)
