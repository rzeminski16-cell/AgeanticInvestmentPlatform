# Accessibility

*The floor, not a later pass. Most of it is already free, because the interface is built out of
real HTML — and the three places it is currently broken are measurable and named.*

---

## The target

**WCAG 2.2 Level AA.** For a single-user local tool that is a choice rather than an obligation,
and it is the right choice: this is a tool for reading dense financial information carefully,
and almost everything AA asks for — contrast, focus, keyboard reach, text that survives zoom —
is what makes dense information readable by anybody at all.

---

## What is already right, and why it was nearly free

**The interface is built out of real HTML elements**, which is a consequence of the
no-JavaScript rule rather than an accessibility effort — and it means a great deal comes for
nothing.

- The menu is `<details>`/`<summary>`: focusable, Enter and Space toggle it, Escape closes it,
  and it announces its expanded state. **All from the browser.**
- Forms are forms. Labels are labels. Buttons are buttons.
- The typeable security field is `<input list>` over a `<datalist>` — a native combobox with
  the whole interaction contract already implemented.
- Tables are tables, with `<th scope="col">`.
- The drawer's semantics are **markup, not script**: `role="dialog"`, `aria-modal`,
  `aria-labelledby` are in the HTML, so the panel is a dialogue in the DOM a reader inspects
  and not only in the one a script got round to editing.
- The drawer's *behaviour* is script — focus trap, Escape, scroll lock — and it is written
  once, in one file. **Escape returns focus to the row it came from, not to the top of the
  document**, and that is tested by tabbing rather than asserted about a template.
- The overlay is not focusable and not a button. It is a convenience for a mouse; every reader
  without one has Escape and a real Close button.
- Status is never carried by colour alone. Every chip pairs its colour with a word.
- `prefers-reduced-motion` collapses all animation globally.
- The badge slot is `aria-live="polite"` and carries a spoken label — *"3 runs waiting for
  your approval"* — because a bare numeral beside a word is read as "Requests 3" and means
  nothing.
- The live region survives its own swap: `innerHTML` is replaced, the region node is not,
  which is the mutation a screen reader announces. **A swap that replaced a live region rather
  than its contents would announce nothing, silently.**

**Keep every one of these.** They are cheap to preserve and expensive to rebuild.

---

## What is broken

### 1. Contrast — three failures, measured

From [`03-design-system.md`](03-design-system.md), where the full table is:

| Pairing | Light | Verdict |
|---|---|---|
| `ink-faint` on `canvas` | **2.98** | Fails outright. Below even the large-text threshold |
| `ink-faint` on `surface-sunken` | **2.87** | Fails outright |
| `ink-faint` on `surface` | 3.20 | Large text only; every current use is small |
| `line-strong` on `surface` | **1.72** | Fails **1.4.11** — it is the boundary of text inputs |
| `brand` on `brand-wash` | 4.03 | Large only — the badge, the guidance chip, the active nav item |

**`ink-faint` and the input border are the two to fix**, and both are single token changes with
product-wide effect.

### 2. Focus is the browser default

There is no designed focus state anywhere. The browser's own ring is inconsistent between
engines and effectively invisible against several of the surfaces here — particularly
`brand`-coloured buttons and anything on `surface-sunken`.

**WCAG 2.2 added focus-appearance requirements**, and this is the single largest accessibility
gap in the product. **Specify a focus state for every interactive element, in both schemes**,
with at least 3:1 against both the component and what is behind it.

### 3. Guidance callouts are `title` attributes

The explanation lives in a `title`, which is not reliably reachable by keyboard and not
reliably announced. Screen-reader users get it via `sr-only` text; **sighted keyboard users get
nothing at all**. Redesign as visible inline content.

### 4. Nothing is verified

There are 33 `aria-` attributes and 31 `role=` attributes across the templates and **no test
checks a single one of them**. The drawer's keyboard behaviour is the honourable exception.
See [`../developers/testing.md`](../developers/testing.md).

---

## What the design must deliver

**Focus states**, for every interactive element, in both schemes, meeting the contrast
requirement against both adjacent surfaces. Not the browser default.

**Contrast for everything you introduce.** State the ratio. Small text 4.5:1, large text 3:1,
control boundaries and meaningful graphics 3:1.

**A visible focus order that follows the reading order.** Particularly on the gate pages, where
the decision buttons must come after the evidence in the DOM as well as on the screen.

**Target sizes.** WCAG 2.2 asks for 24×24 CSS pixels minimum for pointer targets. The current
per-row Archive and Remove buttons in the requests table are below it.

**Reflow at 320px and at 200% zoom** with no horizontal page scroll. The run console, the
assumptions gate and the review gate are the three most likely to fail.

**A heading structure that is a structure.** One `h1` per page, no skipped levels. Several
pages currently have empty `h1` and `h2` elements where a heading was made from a variable that
can be blank.

**Table semantics for real tables, and not for layout.** Row headers where a row has one,
captions where a table needs explaining.

**An accessible name for every control**, including the icon-only ones. There is one today —
the drawer's Close button — and it does it correctly with a visually hidden extension: "Close"
followed by `sr-only` "this panel".

**Error messages associated with their fields**, not only rendered near them. The request form
renders a summary and inline messages; the association must survive.

**Nothing that depends on hover to be discoverable.**

---

## Two patterns worth copying rather than reinventing

**The revealed-control pattern.** Every enhanced control is rendered *hidden* and revealed by
script. A browser with scripting off gets the complete table and no search box, rather than a
search box that does nothing. This is also an accessibility pattern: it means the enhanced path
never leaves a control present-but-dead.

**The link-first trigger.** Every drawer trigger is an ordinary link whose `href` goes to the
full page; the script intercepts it. So the keyboard path, the no-script path and the
right-click-open-in-new-tab path all work without anybody designing them separately.

---

## What to test, and how

In [`../developers/testing.md`](../developers/testing.md) §The interface, with the by-hand
passes in [`../developers/testing-by-hand.md`](../developers/testing-by-hand.md) §8.3. In
short:

- **Automated** — axe-core per surface, in both schemes, as a build failure rather than a
  report. Catches roughly half.
- **Contrast** — assert computed colours and compute the ratio. Never assert a class string; a
  class string is what is already wrong.
- **Keyboard** — drive it, do not assert about it. The drawer tests are the model.
- **By hand** — the keyboard-only pass, the narrow window, 200% zoom.

---

**Next:** [the implementation contract](06-implementation-contract.md)
