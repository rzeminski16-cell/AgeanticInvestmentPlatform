# The design system

*What already exists, what it is worth, and where it is measurably broken. The tokens are
good; the coverage is not, and three of the colour pairings do not pass contrast.*

---

## What exists

**Tailwind v4, configured in CSS rather than a JavaScript config file.** The stylesheet source
is `src/aer/web/styles/app.css`; the compiled output is committed so a checkout runs with no
Node toolchain.

**A complete semantic palette, correct in both schemes**, driven by custom properties that
flip. **A small set of layout constants.** **Six component macros.** **One typeface.**

**And a migration that reached 13 of 54 templates.** The tokens were added *beside* Tailwind's
stock ramps rather than over them — deliberately, so that `text-sky-700` still renders sky
rather than becoming a lie — which means the research tool's 41 templates are still on
`slate`, `sky`, `amber`, `red`, `emerald` and `rose`, at 1,837 occurrences.

**You are designing onto the tokens.** Treat the ramps as the thing being removed.

---

## The colour tokens

Ten families. Every one flips between schemes; the utility is emitted as a variable reference,
never as the hex the light block happens to hold.

### Structure

| Token | Light | Dark | Used for |
|---|---|---|---|
| `canvas` | `#f4f7fb` | `#071e33` | The page behind everything |
| `surface` | `#ffffff` | `#0b2945` | Cards, panels, the header |
| `surface-sunken` | `#eef3f9` | `#061a2c` | Recessed areas, hover, cash rows |
| `line` | `#d9e2ef` | `#1c3a5c` | Ordinary borders and dividers |
| `line-strong` | `#b8c7da` | `#2b4a6e` | Emphasised borders |

### Type

| Token | Light | Dark | Used for |
|---|---|---|---|
| `ink` | `#152033` | `#eaf1ff` | Body and headings |
| `ink-muted` | `#60718a` | `#9db0c9` | Secondary text |
| `ink-faint` | `#8291a7` | `#7286a1` | Hints, notes, timestamps |

### Brand — one accent, used once or twice per screen

| Token | Light | Dark |
|---|---|---|
| `brand` | `#246bfd` | `#4d86ff` |
| `brand-strong` | `#1858dd` | `#6d9bff` |
| `brand-wash` | `#eaf1ff` | `#10305a` |
| `navy` | `#0b2945` | `#eaf1ff` |

*`brand-strong` is darker than `brand` in light and lighter in dark — correct for a hover
state in each.*

### Meaning — five pairs, each a wash and an ink

| Pair | Light ink / wash | Dark ink / wash | Means |
|---|---|---|---|
| `good` | `#087a5b` / `#e3f6ef` | `#6ee7b7` / `#06301f` | Confirmed, succeeded, documented |
| `warn` | `#9a6200` / `#fff3d7` | `#fcd34d` / `#3a2a05` | Blocked, attested, needs attention |
| `bad` | `#b42333` / `#fde7e9` | `#fda4af` / `#3d1119` | Failed, broken, refused |
| `info` | `#175cd3` / `#e8f1ff` | `#93c5fd` / `#10305a` | Calculated, neutral notice |
| `mute` | `#506176` / `#edf1f6` | `#9db0c9` / `#12283f` | Idle, assumed, inactive |

**Never carried by colour alone.** Every use pairs with a word or an icon.

---

## Contrast: what actually passes

*Measured from the token values above. WCAG 2.2 AA needs **4.5:1** for normal text, **3:1** for
text at 18.66px bold or 24px regular, and **3:1** for the boundary of any user-interface
component.*

### Passing comfortably

`ink` on `surface` (16.3 light / 13.1 dark) · `ink` on `canvas` (15.2 / 14.9) · `ink-muted` on
`surface` (4.97 / 6.69) · all five meaning pairs (4.6–5.6 light, 6.8–9.6 dark).

### Failing, and where

**Fix these in the redesign. They are not close calls.**

| Pairing | Light | Dark | Verdict |
|---|---|---|---|
| **`ink-faint` on `canvas`** | **2.98** | 4.54 | **Fails outright in light.** Below even the large-text threshold |
| **`ink-faint` on `surface-sunken`** | **2.87** | 4.73 | **Fails outright in light** |
| `ink-faint` on `surface` | 3.20 | 3.98 | Large text only. Every current use is small text |
| `ink-muted` on `surface-sunken` | 4.45 | 7.96 | Marginally under in light |
| `brand` on `brand-wash` | 4.03 | 3.87 | Large only — **and this is the badge pairing**, used on the count badge, the guidance chips and the active nav item |
| `brand` on `canvas` | 4.25 | 4.97 | Large only in light |
| **`line-strong` on `surface`** | **1.72** | **1.63** | **Fails WCAG 1.4.11.** It is the border of text inputs and buttons, and a control's boundary needs 3:1 |
| `line` on `surface` | 1.31 | 1.28 | Fine as a decorative divider; **not** fine as a control boundary |

**`ink-faint` is the headline problem**: seventeen uses across the token-clean templates, all
small text, and its two commonest backgrounds both fail in light mode. The build identity, the
form hints, the "Since 1 August" note, the exchange beside a ticker — all of it.

**The border problem is the sneakier one.** A text input whose only boundary is a 1.7:1 line
is a control a low-vision user cannot locate. Every form in the product uses it.

**What to do:** darken `ink-faint` in light mode until it clears 4.5:1 on `canvas` and
`surface-sunken`; introduce a distinct control-boundary token at 3:1 or better against
`surface`, separate from the decorative divider. Both are small changes with wide effect, and
both are yours to specify.

---

## Layout constants

| Token | Value | Purpose |
|---|---|---|
| `--container-shell` | `77.5rem` (1240px) | Maximum content width. The header carries the same constraint so its contents line up with the page |
| `--radius-card` | `14px` | Cards, tiles, table containers |
| `--radius-panel` | `16px` | The menu panel, larger surfaces |
| `--spacing-drawer` | `28rem` | The drawer's maximum width |
| `--spacing-sidebar` | `15rem` | From the previous sidebar. Currently unused — **available if you bring a sidebar back** |

**There is no spacing scale of the platform's own** — Tailwind's default is used directly.
Worth defining if the redesign wants rhythm rather than ad-hoc values.

---

## Typography

**Inter Variable**, weights 100–900, self-hosted, latin and latin-ext. **No italic** — nothing
in the product is italic, and a face nobody renders is 52 kB committed for the look of
completeness.

`latin-ext` exists because issuer names come out of filings and an LSE listing is routinely a
European domicile: a report about Škoda or Société Générale would otherwise set those letters
in whatever the system supplies, mid-word. It is fetched only on pages that contain those
characters.

`font-display: swap` — text is readable in the fallback immediately and reflows when Inter
arrives. A research page that is blank while a font loads is worse than one that changes shape.

**There is no type scale.** Sizes are picked per template from Tailwind's defaults. Headings
land on `text-2xl`/`text-3xl` for `h1` and `text-lg` for `h2`, mostly by convention.
**Defining a real scale is one of the clearest wins available.**

**Tabular numerals are used in the portfolio table and should be used in every table of
figures.** Columns of money that do not align are columns nobody can scan.

---

## The component macros

Six, callable as `ui.card(...)` and so on. **The rule: a macro takes data and never classes.**
A component that accepted a class string would let a caller build a card that is not a card,
and the reason to have components is that there is one answer to what a card looks like.

| Macro | Signature | Notes |
|---|---|---|
| `card(title, subtitle)` | Wraps content | Bordered surface, rounded, subtle shadow |
| `kpi(label, value, note)` | A figure and its name | **`value` arrives already rendered.** A macro that formatted a figure would be a second house style nobody configured |
| `empty(title, explanation, action_label, action_href)` | An empty state | Dashed border, centred, optional action |
| `guide(number, explanation)` | A guidance callout | Hidden unless guidance mode is on. **The number is explicit**, because it must match a written walkthrough and a CSS counter silently renumbers when a conditional block is hidden |
| `provenance(ref)` | A provenance badge | **`ref` is required.** Renders as a link to the drill-down |
| `confirmation(ref)` | A confirmation chip | "Confirmed by <name> at <time>", built by the server so no template composes the sentence |

Plus one page-local macro: the portfolio's **grade chip** (`Typed` / `Documented`).

### What the component set is missing

Everything the research tool needs, which is why the research tool has none of it:

- **A table.** Fifteen templates build one from scratch.
- **A page header** — title, subtitle, actions, status. Every page invents its own.
- **A status chip** with the five meanings, so an uppercase enum stops reaching the screen.
- **A banner / callout** for the four severities. The console alone has three, hand-built.
- **Form controls.** Only the research request form has a field macro; every other form types
  its classes inline.
- **A definition list** for the mandate-style label/value blocks that appear on six pages.
- **A section disclosure** — `<details>` styled consistently, used for "closed by default"
  tables on three gates.

**Specifying these is the highest-leverage part of this redesign.** 1,837 raw colour classes
exist largely because there was no component to reach for.

---

## Guidance mode

Callouts are markup that is always present and revealed by an attribute on `<body>`, so a page
not using guidance pays no JavaScript for it and the explanation sits beside the thing it
explains. A tour driven by a list of selectors breaks the moment somebody renames a `div`, and
nothing says so.

Currently a numbered chip with the explanation in a `title` attribute — which is **not
accessible**: `title` is not reliably reachable by keyboard or announced by screen readers. The
`sr-only` text carries it for assistive technology, but a sighted keyboard user gets nothing.
**Worth redesigning as visible inline content.**

---

## Motion

`prefers-reduced-motion: reduce` collapses every animation and transition globally. There is
almost no motion today — one pulsing dot on the running step in the console. **Design motion as
an enhancement that can vanish entirely.**

---

## What to deliver

For the redesign to be buildable without a second round of guessing:

1. **The revised token values**, both schemes, with contrast stated for every text pairing you
   introduce. Fix the failures above.
2. **A type scale** — sizes, weights, line heights, and which is used where.
3. **A spacing scale.**
4. **The component set**, including the missing ones listed above, each with its states:
   default, hover, focus, active, disabled, error, loading, empty.
5. **Focus states**, explicitly. Every interactive element, in both schemes. This is currently
   the browser default and it is invisible against several of the surfaces.
6. **Table specification** — density, alignment, numerals, zebra or not, sticky headers, and
   what happens at a narrow width.
7. **How a component behaves with scripting off**, wherever that differs.

---

**Next:** [content and voice](04-content-and-voice.md) ·
[accessibility](05-accessibility.md) · [the implementation
contract](06-implementation-contract.md)
