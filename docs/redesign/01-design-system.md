# Tracework — design system

> Implementation contract for the redesigned interface. This document is normative: where a
> page specification and an improvised utility class disagree, this system wins.

## 1. System intent

Tracework is an analyst's working paper, not a consumer-finance dashboard. The system therefore
uses sheets, ledger rules, margin notes, human verdicts and inspectable proof. It does not use
gradients, glass effects, decorative charts, oversized KPI cards or a rainbow of status pills.

The system has four non-negotiable behaviours:

1. **Verdict first, evidence beside it, proof on demand.** The current answer is visually
   primary; its lineage is always reachable.
2. **A refusal and a failure are different.** A refusal is a trustworthy rule working; a
   failure is a fault. They never share a label or colour.
3. **The server owns meaning.** Human status labels, formatted figures, action consequences,
   provenance and decision state arrive from the server. Templates do not infer them.
4. **HTML works before enhancement.** Links navigate, forms submit, disclosures disclose and
   tables remain complete without scripting. JavaScript may shorten a path, never create the
   only path.

All user-facing text uses UK English. One visible `This is not investment advice.` disclaimer
appears once per page through the shared shell.

---

## 2. Colour tokens

### 2.1 Token rules

- Use semantic custom properties, exposed to Tailwind v4 through `@theme inline`. Do not put a
  stock Tailwind colour ramp in a redesigned template.
- Light mode is the default. `[data-theme="dark"]` is the explicit dark override. When the
  attribute is absent, `@media (prefers-color-scheme: dark)` supplies dark values. An explicit
  light or dark choice always wins over the media query.
- Set `color-scheme: light` or `dark` with the active scheme so native fields match it.
- Do not create class names at runtime. Every component variant has a complete, literal class
  name visible to Tailwind's scanner.
- `line` and `line-strong` are decorative rules. They are deliberately quieter than 3:1 and
  must never be the only boundary of a control or meaningful graphic. Use `control-boundary`
  for those.
- Colour is never the only carrier of meaning. Every semantic colour is paired with a word;
  meaningful icons also vary by shape.

### 2.2 Foundations

| CSS custom property | Light | Dark | Contract |
|---|---:|---:|---|
| `--color-canvas` | `#F4F7F8` | `#07171D` | Page paper |
| `--color-surface` | `#FFFFFF` | `#0C222B` | Standard working sheet and control fill |
| `--color-surface-raised` | `#FFFFFF` | `#102B35` | Drawer, menu and floating sheet only |
| `--color-surface-sunken` | `#EAF0F1` | `#081B22` | Recessed rows, code blocks and disabled fill |
| `--color-surface-selected` | `#E2F3F4` | `#12343D` | Selected navigation or selected record |
| `--color-ink` | `#15252E` | `#EDF6F7` | Primary text |
| `--color-ink-muted` | `#52656E` | `#BBCACE` | Secondary text and labels |
| `--color-ink-subtle` | `#5B6D75` | `#9AADB3` | Timestamps, hints and tertiary text; still AA at 12px |
| `--color-line` | `#CDD8DC` | `#2B414A` | Ordinary ledger rule; decorative only |
| `--color-line-strong` | `#9AAEB5` | `#45606A` | Section rule; decorative only |
| `--color-control-boundary` | `#6D8189` | `#687F88` | Inputs, buttons and meaningful graphic boundaries |
| `--color-overlay` | `#15252EB3` | `#000000B8` | Drawer scrim; eight-digit hex includes alpha |

There is no separate “faint” text token. `ink-subtle` is the quietest permitted text and passes
4.5:1 on every sanctioned background in both schemes.

### 2.3 Verification and decision accents

| CSS custom property | Light | Dark | Contract |
|---|---:|---:|---|
| `--color-verification` | `#0F6673` | `#B5ECF0` | Brand mark, links, selected nav, evidence nodes |
| `--color-verification-strong` | `#084D57` | `#D0F7F8` | Hover/pressed verification action |
| `--color-verification-wash` | `#E2F3F4` | `#12343D` | Information and selected background |
| `--color-on-verification` | `#FFFFFF` | `#07171D` | Text on a filled verification action |
| `--color-decision` | `#7A4B00` | `#FFD27A` | Human decision, cost ceiling, attention |
| `--color-decision-strong` | `#5D3900` | `#FFE4A8` | Hover/pressed decision action |
| `--color-decision-wash` | `#FFF3D6` | `#3B2B0C` | Decision panel and attention background |
| `--color-on-decision` | `#FFFFFF` | `#2B1900` | Text on a filled decision action |
| `--color-focus-ring` | `#00606D` | `#B5ECF0` | Universal outer focus ring |
| `--color-focus-gap` | `#FFFFFF` | `#07171D` | One-pixel separation between control and ring |

Verification teal means “this can be inspected” or “this is the active view”. Decision amber
means “a person must choose” or “money/time needs attention”. Neither is decorative.

### 2.4 Semantic pairs

Each semantic family has an ink and a wash. Use the ink for the label, icon and 3px leading
rule; use the wash for the background. Body copy inside the component uses `ink`, not a faded
variant.

| CSS custom properties | Ink light | Wash light | Ink dark | Wash dark | Use |
|---|---:|---:|---:|---:|---|
| `--color-success-ink` / `--color-success-wash` | `#14613F` | `#E3F4EA` | `#8CE2B4` | `#123528` | Complete, confirmed, documented |
| `--color-warning-ink` / `--color-warning-wash` | `#7A4B00` | `#FFF3D6` | `#FFD27A` | `#3B2B0C` | Waiting for a decision, spending ceiling, attention |
| `--color-refusal-ink` / `--color-refusal-wash` | `#6B3F60` | `#F6EAF2` | `#EDB9DB` | `#3A2034` | A guardrail deliberately withheld an answer |
| `--color-failure-ink` / `--color-failure-wash` | `#9B293F` | `#FBEAED` | `#FFB3BD` | `#401A21` | A fault or unsuccessful operation |
| `--color-info-ink` / `--color-info-wash` | `#0F6673` | `#E2F3F4` | `#B5ECF0` | `#12343D` | Running, calculated, neutral notice |
| `--color-muted-ink` / `--color-muted-wash` | `#52656E` | `#EAF0F1` | `#BBCACE` | `#1A3038` | Draft, queued, skipped, inactive |

Aliases in code are allowed only when they preserve these exact values, for example
`--color-info-ink: var(--color-verification)`. Do not create a second blue for information.

### 2.5 Destructive action tokens

Failure and destructive action share a family because both mean that something went wrong or
will be removed. A destructive action still says exactly what it removes.

| CSS custom property | Light | Dark |
|---|---:|---:|
| `--color-danger-action` | `#9B293F` | `#FFB3BD` |
| `--color-danger-action-hover` | `#781A2D` | `#FFD1D7` |
| `--color-on-danger-action` | `#FFFFFF` | `#27070D` |

### 2.6 Data visualisation aliases

Use `verification`, `decision`, `success-ink`, `refusal-ink` and `failure-ink`, in that order,
for a visualisation that genuinely needs five series. Every series must also have a direct
label plus one of these line styles: solid, long dash, dot, dash-dot, double. Do not rely on a
legend, colour alone or adjacent hues. Knowledge-graph node categories use both a labelled
shape and one of these colours. All meaningful marks are at least 2px thick.

### 2.7 Measured contrast

Ratios below use WCAG relative luminance, rounded to two decimals. Normal text requires 4.5:1;
large text and meaningful graphics require 3:1. These are the sanctioned pairings. A new
pairing must ship with the same computed-colour test; “it uses a token” is not a contrast test.

#### Foundation text — light

| Text \ background | Canvas | Surface | Raised | Sunken | Verification wash | Decision wash | Success wash | Refusal wash | Failure wash |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ink` | 14.60 | 15.72 | 15.72 | 13.65 | 13.74 | 14.25 | 13.77 | 13.45 | 13.54 |
| `ink-muted` | 5.66 | 6.10 | 6.10 | 5.29 | 5.33 | 5.53 | 5.34 | 5.22 | 5.25 |
| `ink-subtle` | 5.02 | 5.40 | 5.40 | 4.69 | 4.72 | 4.90 | 4.73 | 4.62 | 4.65 |

#### Foundation text — dark

| Text \ background | Canvas | Surface | Raised | Sunken | Verification wash | Decision wash | Success wash | Refusal wash | Failure wash |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ink` | 16.64 | 14.95 | 13.48 | 16.07 | 12.07 | 12.45 | 12.22 | 13.32 | 13.78 |
| `ink-muted` | 10.83 | 9.73 | 8.78 | 10.46 | 7.86 | 8.10 | 7.95 | 8.67 | 8.97 |
| `ink-subtle` | 7.83 | 7.04 | 6.34 | 7.56 | 5.68 | 5.86 | 5.75 | 6.27 | 6.49 |

#### Semantic ink

| Pairing | Light | Dark |
|---|---:|---:|
| Success ink on its wash | 6.54 | 8.72 |
| Warning/decision ink on its wash | 6.72 | 9.60 |
| Refusal ink on its wash | 7.17 | 8.75 |
| Failure ink on its wash | 6.50 | 8.96 |
| Information ink on its wash | 5.79 | 10.24 |
| Muted ink on its wash | 5.29 | 8.17 |

| Semantic ink | Light surface / canvas | Dark surface / canvas |
|---|---:|---:|
| Success | 7.47 / 6.94 | 10.67 / 11.88 |
| Warning/decision | 7.41 / 6.88 | 11.53 / 12.83 |
| Refusal | 8.38 / 7.79 | 9.82 / 10.93 |
| Failure | 7.55 / 7.01 | 9.72 / 10.82 |
| Information | 6.63 / 6.16 | 12.68 / 14.11 |
| Muted | 6.10 / 5.66 | 9.73 / 10.83 |

#### Filled actions

| Text on fill | Light default / hover | Dark default / hover |
|---|---:|---:|
| `on-verification` on verification | 6.63 / 9.50 | 14.11 / 15.96 |
| `on-decision` on decision | 7.41 / 10.24 | 11.88 / 13.62 |
| `on-danger-action` on danger | 7.55 / 10.58 | 11.08 / 13.68 |

#### Links

| Link ink on surface / canvas / verification wash | Light | Dark |
|---|---:|---:|
| Verification, rest | 6.63 / 6.16 / 5.79 | 12.68 / 14.11 / 10.24 |
| Verification-strong, hover/pressed | 9.50 / 8.83 / 8.31 | 14.34 / 15.96 / 11.58 |

#### Non-text boundaries and focus

| Pairing | Light | Dark |
|---|---:|---:|
| Control boundary on surface | 4.08 | 3.89 |
| Control boundary on canvas | 3.79 | 4.33 |
| Control boundary on sunken | 3.54 | 4.18 |
| Control boundary on raised | 4.08 | 3.51 |
| Focus ring on surface | 7.26 | 12.68 |
| Focus ring on canvas | 6.74 | 14.11 |
| Focus ring on sunken | 6.30 | 13.63 |
| Focus ring on raised | 7.26 | 11.43 |
| Evidence node on surface | 6.63 | 12.68 |
| Evidence node on canvas | 6.16 | 14.11 |

`line` on `surface` is 1.45:1 light and 1.53:1 dark; `line-strong` is 2.31:1 and
2.45:1. Those failures are intentional only because the rules are decorative. They cannot
outline inputs, selected regions, evidence nodes, chart marks or focus.

On filled actions, the focus gap contrasts with the fill at 6.63/7.41/7.55:1 light and
14.11/12.83/10.82:1 dark for verification/decision/danger respectively; the outer focus ring
then contrasts with the surrounding surface as measured above. This two-edge treatment is what
makes focus visible against both component and page.

---

## 3. Typography

### 3.1 Families

| Role | Family | Weights used | Fallback |
|---|---|---|---|
| Object identity and display | Barlow Semi Condensed | 600, 700 | `"Arial Narrow", "Aptos Narrow", sans-serif` |
| Interface and reading | Source Sans 3 | 400, 500, 600 | `"Segoe UI", Arial, sans-serif` |
| Figures and records | IBM Plex Mono | 450, 500, 550, 600 | `"Cascadia Mono", Consolas, ui-monospace, monospace` |

Vendor WOFF2 files for latin and latin-ext, record every SHA-256, make no runtime font request,
and use `font-display: swap`. Preserve diacritics in issuer and source names. The fallback stack
must render immediately. Do not synthesise italic or bold faces.

Barlow is reserved for object identity and section hierarchy; it is not body copy. IBM Plex
Mono is reserved for values that must align or be inspected: formatted figures, dates, times,
identifiers, formulae, hashes, short provenance labels and table numerics. Prose never uses
mono merely to look technical.

### 3.2 Type scale

All sizes are CSS pixels at a 16px root. Do not set a smaller root size.

| Token | Family | Size / line-height | Weight | Letter spacing | Use |
|---|---|---:|---:|---:|---|
| `type-display` | Barlow | 40 / 44 | 700 | `-0.015em` | Page-defining object or report title |
| `type-title-1` | Barlow | 32 / 36 | 700 | `-0.01em` | Standard `h1` |
| `type-title-2` | Barlow | 24 / 30 | 600 | `-0.005em` | `h2`, major sheet title |
| `type-heading` | Barlow | 20 / 26 | 600 | `0` | `h3`, subsection title |
| `type-subheading` | Source Sans 3 | 16 / 22 | 600 | `0` | Dense component heading |
| `type-body-lg` | Source Sans 3 | 18 / 28 | 400 | `0` | Verdict and introductory explanation |
| `type-body` | Source Sans 3 | 16 / 24 | 400 | `0` | Default prose and controls |
| `type-body-strong` | Source Sans 3 | 16 / 24 | 600 | `0` | Emphasis, never an entire paragraph |
| `type-body-sm` | Source Sans 3 | 14 / 20 | 400 | `0` | Dense table and supporting copy |
| `type-label` | Source Sans 3 | 14 / 20 | 600 | `0.005em` | Form and component labels |
| `type-caption` | Source Sans 3 | 12 / 16 | 500 | `0.01em` | Timestamps and short notes |
| `type-data-xl` | IBM Plex Mono | 28 / 34 | 550 | `-0.02em` | One consequential figure, not a KPI grid |
| `type-data` | IBM Plex Mono | 14 / 20 | 450 | `0` | Figures, dates and compact records |
| `type-data-sm` | IBM Plex Mono | 12 / 16 | 500 | `0.015em` | Hash labels and utility records |
| `type-eyebrow` | IBM Plex Mono | 12 / 16 | 600 | `0.055em` | Short structural label, uppercase in CSS |

Below 640px, `type-display` becomes 32/36 and `type-title-1` becomes 28/32. Other tokens do not
shrink. Use sentence case everywhere except the short `type-eyebrow` role. Never render a raw
enum in uppercase.

Set `font-variant-numeric: tabular-nums lining-nums` on all data tokens and numeric table
columns. Enable the slashed zero only for hashes and identifiers. Figures arrive formatted
from the server; CSS aligns them but never inserts currency, separators, signs or units.

Default prose measure is 68ch. Explanatory evidence may reach 76ch. Report body may reach 84ch.
Labels and table cells are not constrained by prose measure.

---

## 4. Spacing, shape and elevation

### 4.1 Spacing scale

| Token | Value | Typical use |
|---|---:|---|
| `--space-0` | `0` | Reset |
| `--space-half` | `2px` | Optical adjustment only |
| `--space-1` | `4px` | Icon-to-label micro gap |
| `--space-1-5` | `6px` | Tight inline group |
| `--space-2` | `8px` | Chip and compact-row inset |
| `--space-3` | `12px` | Control gap, table cell inset |
| `--space-4` | `16px` | Narrow page gutter, component inset |
| `--space-5` | `20px` | Standard sheet inset |
| `--space-6` | `24px` | Section rhythm and medium gutter |
| `--space-8` | `32px` | Major section gap and wide gutter |
| `--space-10` | `40px` | Page-block separation |
| `--space-12` | `48px` | Major content separation |
| `--space-16` | `64px` | Page section boundary |
| `--space-20` | `80px` | Large report rhythm |
| `--space-24` | `96px` | Maximum editorial separation |

Do not introduce 10px, 14px, 18px or 28px spacing values. Those are type metrics, not layout
increments. A component may use `1px` or `3px` only for rules.

### 4.2 Radii

| Token | Value | Use |
|---|---:|---|
| `--radius-none` | `0` | Tables, ledger rows, docked edges |
| `--radius-xs` | `2px` | Status labels and evidence labels |
| `--radius-sm` | `4px` | Buttons, inputs, inline callouts |
| `--radius-md` | `8px` | Working sheets, menus and decision panel |
| `--radius-lg` | `12px` | Drawer or one-off floating panel only |
| `--radius-round` | `9999px` | Running dot, avatar or circular icon control only |

Tracework does not use pill-shaped status chips or nest rounded cards. A working sheet may
contain ruled sections, not more cards.

### 4.3 Elevation

| Token | Light | Dark | Use |
|---|---|---|---|
| `--shadow-sheet` | `0 1px 2px #07171D0A` | `0 1px 0 #00000052` | Raised menu or sheet only |
| `--shadow-float` | `0 16px 40px #07171D29` | `0 16px 40px #00000073` | Drawer only |

Standard sheets have a ledger-line boundary and no shadow. Elevation communicates a layer that
can occlude another layer; it is not decoration.

---

## 5. Responsive layout

### 5.1 Metrics

| Token | Value |
|---|---:|
| `--layout-max` | `1600px` |
| `--nav-width` | `232px` |
| `--decision-width` | `304px` |
| `--evidence-spine-width` | `152px` |
| `--drawer-width` | `448px` |
| `--reading-measure` | `68ch` |
| `--evidence-measure` | `76ch` |
| `--report-measure` | `84ch` |

Page gutters are 16px below 640px, 24px from 640px, and 32px from 1280px. The working area is
centred only after it reaches `--layout-max`; tables may use the available width within it.

### 5.2 Breakpoints

| Name | Range | Behaviour |
|---|---|---|
| Compact | `320–639px` | Native navigation disclosure; one content column; horizontal evidence sequence; actions stack when labels need it |
| Rail | `640–959px` | Same navigation DOM; two-column short forms allowed; decision remains in reading flow; horizontal evidence sequence |
| Workbench | `960–1279px` | Persistent 232px index; vertical evidence margin; decision panel remains after the evidence in the main flow |
| Wide | `1280–1535px` | Persistent index; main evidence area plus 304px sticky decision column |
| Expanded | `1536px+` | Same structure, larger outer gutter; never stretch prose beyond its measure |

At 320px and at 200% zoom, the page itself never scrolls horizontally. A genuinely wide table
scrolls inside its named table region. Do not convert financial tables into cards or hide
columns to make them fit.

### 5.3 Shell and DOM order

Render the navigation once. It is a `<details>` disclosure at compact and rail widths and the
same node becomes the persistent index at 960px. The single out-of-band count target therefore
also appears exactly once. At wide widths, CSS exposes the panel regardless of the mobile
`open` state and removes the summary from the visual flow.

The document order is:

1. Skip link.
2. Shared navigation and preferences.
3. Main page header and verdict.
4. Evidence/work content, including any editable evidence.
5. Decision panel and its submit controls.
6. Page disclaimer/footer.
7. Enhanced drawer container.

CSS grid may place item 5 beside item 4 at 1280px, but must not move it earlier in focus or
screen-reader order. A gate form may wrap both columns; otherwise every external submit button
must use an explicit `form` attribute pointing to the real form containing the CSRF token and
payload hash.

---

## 6. Interaction primitives

### 6.1 Focus

Every interactive element uses the same `:focus-visible` treatment:

```css
outline: 2px solid var(--color-focus-ring);
outline-offset: 2px;
box-shadow: 0 0 0 1px var(--color-focus-gap);
```

The one-pixel gap separates the ring from a filled button; the outer ring separates it from
the page. Do not suppress outline on `:focus`. Use `:focus-visible` when supported and retain a
matching `:focus` fallback. A composite widget may place the ring on `:focus-within`, but the
actual focused control remains identifiable.

### 6.2 Pointer and keyboard targets

- Primary controls, text inputs, navigation items and disclosure summaries are at least 44px
  high.
- Dense row actions may be 32×32px, exceeding the WCAG 2.2 24×24 minimum. Spacing between
  adjacent dense targets is at least 8px.
- Icon artwork is 16 or 20px; its button, link or label owns the target size.
- Nothing is disclosed only on hover. Hover styling is wrapped in `@media (hover: hover)`.
- `:active` changes fill or border; it does not move content. There is no scale-down effect.

### 6.3 Motion

Only a healthy running step animates: a 6px information-coloured dot changes opacity from 0.55
to 1 over 1.8 seconds, ease-in-out, indefinitely. Elapsed time may update as text. There are no
value count-ups, shimmer skeletons, entrance cascades or animated evidence. Under
`prefers-reduced-motion: reduce`, the dot is static and labelled `Running`; all transitions
are effectively zero-duration.

### 6.4 Selection and browser states

Text selection uses `verification` as the background and `on-verification` as the text. Native
autofill must preserve readable text and a visible `control-boundary`. In forced-colours mode,
allow system colours, retain borders, use `currentColor` for SVG and do not force semantic
washes; words and icons continue to carry the state.

---

## 7. Component state model

### 7.1 Universal states

Components implement only states that make semantic sense, but they implement every applicable
state below. A screenshot of a default component is not a complete component.

| State | Visual and behavioural contract |
|---|---|
| Rest | Uses the tokens and dimensions in this document; no caller-supplied class string |
| Hover | Changes fill or underlining only; never reveals essential content; only for hover-capable pointers |
| Focus | Universal dual-layer focus treatment; always visible in both schemes |
| Pressed / selected | Persistent wash plus a 3px verification rule or native checked mark; uses `aria-pressed`, `aria-current` or checked semantics where applicable |
| Disabled | Native `disabled` where available; sunken fill, muted ink, control boundary, no opacity reduction, no hover state |
| Loading / submitting | Keep the action noun: `Saving decision…`; disable duplicate submission; announce the text change politely; do not use an unlabeled spinner or skeleton |
| Invalid | Failure-coloured 2px boundary, inline remedy, and programmatic field association; focus moves to the error summary only after submission |
| Empty | Names what acting would produce, states what was checked and offers the next real action when one exists |
| Partial | Shows the available result and names each withheld or unavailable part; never presents a partial total as complete |
| Refused | Refusal family, the rule that caused it, what would change the answer and why the rule exists |
| Failed | Failure family, the failed operation, preserved work and a concrete retry/recovery path |
| Recorded / read-only | Success or neutral family, decision and timestamp visible, controls removed rather than merely disabled |

### 7.2 Macro/API rule

Jinja component macros accept meaning, not presentation. A macro may take `kind="refusal"` or a
server-built status object; it never takes a class string, raw hex, arbitrary icon name or
pre-composed HTML attribute. Variants are an exhaustive server-side enum mapped to literal,
scannable classes.

Recommended primitives:

```text
ui.page_header(title, eyebrow, subtitle, status, breadcrumbs, actions)
ui.verdict(label, statement, detail, provenance_ref)
ui.status(kind, label, detail=None)
ui.callout(kind, title, explanation, action=None)
ui.button(label, variant, type="button", disabled=False, form_id=None)
ui.field(field, label, hint=None, error=None, required=False)
ui.empty(title, explanation, action=None)
ui.definition_list(items, density="standard")
ui.provenance(ref, kind, label)
ui.confirmation(label, person, timestamp)
ui.grade(label)
ui.guide(number, explanation)
```

Complex tables, evidence spines and decision panels should be macros with caller blocks for
their typed content, while keeping their container and states fixed.

---

## 8. Core components

### 8.1 Application shell and navigation

The index is 232px wide from 960px and uses `canvas`, not a floating card. A 1px ledger rule
separates it from the working area. `Tracework` is set in Barlow Semi Condensed 20/24, 700;
the working-name qualification belongs in documentation, not the interface.

Navigation sections use a Source Sans 3 12/16 label in `ink-subtle`; items are 44px high,
14/20, 600. The current item uses `surface-selected`, a 3px verification leading rule and
`aria-current="page"`. Hover uses the same wash without the rule. A waiting count is a
rectangular status label with a spoken phrase such as `3 runs waiting for your decision`, not
a bare number.

Compact and rail widths use a native `<details>`/`<summary>`. Its summary says `Open index` or
`Close index` visually and contains the menu icon; it is not a glyph-only control. At 960px the
same navigation node is persistent. There must not be separate desktop and mobile renderings,
because the live badge target may appear once only.

No JavaScript: the disclosure, links, theme preference form and guidance preference form all
work natively. Counts may remain at their server-rendered initial value if the optional htmx
refresh does not run.

### 8.2 Skip link, breadcrumbs and back links

The skip link is the first focusable element. It is visually off-canvas until focused, then
appears at the top-left on `surface-raised` with 12px/16px padding and the universal focus ring.

Breadcrumbs are an ordered list of real links, 14/20. Use a decorative chevron SVG between
items and `aria-current="page"` on the final text. Show at most four levels; collapse only
middle levels into a real `More path` disclosure, never into inaccessible ellipsis text.

A back action is a link with a left-arrow icon and a destination label: `Back to requests`, not
`Back`. No JavaScript: all are ordinary links.

### 8.3 Page and object header

Every page has exactly one non-empty `h1`. The server supplies a meaningful fallback when an
issuer or object name is absent, for example `Untitled research request`, never an empty node.

Header anatomy, in order:

1. Breadcrumbs.
2. Optional 12/16 mono eyebrow naming the object type (`RESEARCH RUN`, not a status enum).
3. `h1` in `type-title-1` or `type-display` for a report object.
4. One-line identity detail in muted body text: ticker, exchange, as-of date or request owner.
5. Human status and page actions, wrapping below the title when space is tight.

Actions remain after the heading in DOM order. A destructive action is always a POST button
inside a form, never a link styled as one. No JavaScript: the layout wraps and every action
retains its normal navigation or form behaviour.

### 8.4 Verdict block

The verdict is the first ruled section beneath the object header, not a hero card. It has a
3px verification leading rule, 20px inset, a 12/16 mono label (`CURRENT VIEW`, `CURRENT STATE`
or `ANSWER WITHHELD`) and an 18/28 statement. A consequential single figure may use
`type-data-xl`. A provenance link sits on the same baseline or immediately after the sentence.

Variants:

- Current/inspectable: verification rule.
- Waiting for a human: decision rule and decision wash.
- Refused: refusal rule and wash; title starts with the withheld answer, not `Error`.
- Failed: failure rule and wash; title names the fault.
- Partial: decision rule; the statement explicitly names the missing part.

No JavaScript: provenance navigates to the full evidence/claim view. Enhancement may intercept
the same link to open the drawer.

### 8.5 Working sheet and ruled section

A working sheet uses `surface`, radius 8px and a 1px `line` boundary. Standard inset is 20px on
compact widths and 24px from 640px. Sheets divide content with horizontal ledger rules and
headings; they do not contain grids of smaller decorative cards. Nested surfaces are permitted
only for a semantic wash, code/hash block or a table scroller.

`surface-raised` is reserved for content that actually floats. An ordinary section never gets
a shadow. Empty, refused and failed sheets keep the same outer geometry so the page does not
rearrange when state changes.

### 8.6 Buttons

Standard buttons are at least 44px high, radius 4px, 14/20 Source Sans 3, 600, with 12px
horizontal padding and an 8px label/icon gap. Compact row buttons are 32px high with 8px
padding. Labels use a verb plus object and remain identical through result copy: `Approve
plan` becomes `Plan approved`.

| Variant | Rest | Hover / pressed | Use |
|---|---|---|---|
| Verification primary | Verification fill, on-verification text | Verification-strong fill | Non-consequential primary action such as `Commission research` |
| Decision | Decision fill, on-decision text | Decision-strong fill | Approve, confirm, acknowledge, allow more spend |
| Secondary | Surface fill, ink text, control boundary | Surface-selected fill, verification boundary | Alternative or cancel/back action |
| Quiet | Transparent, verification text, visible underline on text | Verification wash | Low-emphasis action; still has 44px target |
| Destructive | Danger fill, on-danger text | Danger-hover fill | Archive/remove actions that POST |

Pressed state does not translate or scale. Disabled buttons use sunken fill, muted text and the
control boundary; do not use opacity. Submitting replaces the visible label with the exact
gerund (`Approving plan…`) and uses `disabled` plus `aria-disabled="true"` for htmx-managed
submission. Keep the original width with a minimum inline size if needed; no spinner is
required.

No JavaScript: every submit button is inside, or explicitly points to, a real form with
`method="post"`, `action`, CSRF field and any payload hash. The browser navigation is the
loading state.

### 8.7 Links and icon controls

Body links use verification ink and a 1px underline offset by 3px. Hover uses
verification-strong and a 2px underline. Visited state does not become purple because a source
being visited is not a new semantic status; visited evidence may add the text `Opened` only if
the server owns that state.

Navigation links and buttons may omit the underline because their shape and location identify
them, but focus remains universal. An icon-only control is allowed only for universally
recognisable close, copy or disclosure actions and has an accessible text name. Its target is
at least 32×32px; primary controls use visible labels.

### 8.8 Status label

Status labels are compact rectangles, not pills: radius 2px, 6px vertical and 8px horizontal
inset, Source Sans 3 14/20, 600, with a 14px semantic icon. Ink, icon and 3px leading rule use
the semantic ink; the background uses its wash. The visible human phrase is mandatory. A
technical code may exist only in an expanded audit disclosure.

The component supports `success`, `decision`, `refusal`, `failure`, `information` and `muted`.
It does not accept arbitrary colours. Live status updates replace the text inside a persistent
`aria-live="polite"` node; they never replace the live-region node itself.

### 8.9 Callout / notice

A callout has a 3px semantic leading rule, semantic wash, radius 4px and 16px inset. Its icon is
20px; title is 16/22, 600; explanation is 16/24 in primary ink. Use an action link only when it
is the stated remedy.

The content shape is normative:

- Refusal: what was withheld; what would change it; why the rule exists.
- Failure: what failed; what work remains safe; how to retry or recover.
- Decision: what is waiting; what the choice changes; cost/time consequence.
- Success: what completed and where the result is now.
- Information: a fact the operator needs to interpret the current view.

Do not use dismissible notices for consequential state. Server flash messages may disappear on
the next navigation, but a refusal, failure or pending decision remains part of the page.

### 8.10 Forms and fields

All fields use real labels and native controls. Standard control height is 44px; textarea
minimum height is 120px. Control fill is `surface`, boundary is 1px
`control-boundary`, radius is 4px, text is 16/24 and horizontal inset is 12px. Placeholder text
is an example only, never the label, and uses `ink-subtle`.

Field anatomy:

1. Label, with `(required)` or `(optional)` in text when useful.
2. Control.
3. Persistent hint in 14/20 muted text.
4. Error in 14/20 failure ink, prefixed with the error icon.

Connect hint and error ids through `aria-describedby`. Invalid fields use `aria-invalid="true"`
and a 2px failure boundary. The error summary is a failure callout headed `Check these fields`;
each item links to the field. On a submitted invalid page, focus moves to the summary. Do not
validate only on blur or remove valid user input.

Specific controls:

- Security entry remains native `<input list>` plus `<datalist>`.
- Date, select and number inputs keep native keyboard semantics. The server validates and
  formats; the template does not sign or convert money.
- Checkbox and radio inputs are 20px with a 44px label wrapper. Use native checked/disabled
  semantics and `accent-color: verification`.
- Radio groups and related checkboxes use `<fieldset>` and `<legend>`.
- Units belong in the visible label or a separately labelled suffix inside the control group;
  placeholder units are not sufficient.
- A read-only value is ordinary text in a definition list unless the user needs to select or
  submit it. Do not present disabled inputs as display content.

No JavaScript: `action` and `method` are complete. The same validation code returns the full
page. With htmx, swap only the error region's `innerHTML` so its persistent live-region node is
announced.

### 8.11 Search, filter and sort

A filter that defines a returnable view is a GET form and stores its state in the URL. The
submit button is visible and labelled `Apply filters`; optional enhancement may submit after a
short delay but must preserve the URL and button.

Pure client-side table search follows progressive reveal: render the control with `hidden`,
remove `hidden` only when the script has attached, and keep the complete table when scripting
is absent. Do not render a control that cannot work. Sorting is a GET link in the column header
with the direction written for assistive technology.

### 8.12 Definition list

Use a `<dl>` for mandate details, metadata, cost/time summaries and other label/value records.
Standard layout is two columns with a 160px label track from 640px and a single column below.
Each pair has 12px vertical padding and a ledger rule. Labels are 14/20 muted; values are
16/24 ink. Numeric and identifier values switch to the data face. Long hashes wrap with
`overflow-wrap:anywhere`; ordinary words do not break arbitrarily.

For three to six short, peer-level values at the top of a working sheet, use the compact
`summary band` variant: equal ruled cells on wide screens, two columns at intermediate widths
and one column at 320px. It remains one semantic `<dl>` and uses the same label/value type
rules. It is a metadata summary, not a row of decorative KPI cards; do not add independent
shadows, accent colours or click behaviour to its cells.

### 8.13 Consequential figure

`ui.figure` shows one server-rendered value in `type-data-xl`, a 14/20 label, optional 14/20
note and required provenance whenever the figure's kind is not obvious. It is not a generic KPI
card and is not repeated into a row of coloured tiles. Negative/positive meaning is written or
shown with a sign, not colour alone.

The report figure is already rendered in millions; a portfolio figure is exact to the penny.
The component never reformats either. A value under one penny may be `under £0.01`; zero and a
rounded non-zero value must not look identical.

### 8.14 Record list / work queue

Action-oriented rows such as runs waiting for the operator are a semantic list, not a table.
Each `<li>` is a ruled record with object name, human sentence, status, waiting time/cost and a
visible action. This permits natural wrapping at 320px without destroying table semantics.

At 640px, identity and sentence occupy the main track; metadata and action occupy auto tracks.
At compact widths they follow in reading order. Row hover may use surface-selected, but the
whole row is not made clickable when it contains other actions. The primary link is the object
name or explicit `Review run` link.

### 8.15 Native disclosure

Use `<details>`/`<summary>` for secondary evidence, audit records, long formulae and guidance
explanations. Summary is at least 44px high and contains a 16px chevron plus a specific label,
for example `Show payload record`; it is not `More`. The open state turns the chevron and adds
a strong ledger rule. Under reduced motion, it changes instantly.

No JavaScript: full behaviour is native. Do not reimplement disclosure with a clickable `div`.

### 8.16 View navigation and pagination

Page-level views are ordinary GET links in a labelled `<nav>`, never JavaScript tabs. The
current view uses `aria-current="page"`, verification ink, selected wash and a 3px bottom rule.
At narrow widths the links wrap or become a native select plus explicit `Go` form; they do not
cause page-level horizontal scroll.

Pagination uses `Previous`, numbered pages where useful and `Next`, all as GET links preserving
query parameters. Disabled previous/next is text, not a dead link.

### 8.17 Empty, partial, refused, failed and loading surfaces

These states reuse the same sheet and heading position as the populated surface.

| State | Required content | Forbidden treatment |
|---|---|---|
| Empty | What was checked, what acting would create, one real next action | Bare `No results`, decorative illustration |
| Loading/running | Current named operation, elapsed time where available, realistic expectation | Shimmer skeleton, fake progress percentage |
| Partial | Available result, each unavailable part, effect on totals/conclusion | Quiet footnote that makes the whole look complete |
| Refused | Withheld answer, rule, remedy, reason | Red `Error`, retry button when retry cannot help |
| Failed | Failed operation, safe/preserved work, recovery action, fault reference | Generic `Something went wrong` |

No JavaScript: a submitted request navigates to the server-rendered running/state page. Optional
polling updates the persistent state region; without polling the page offers a normal `Refresh
status` GET link.

---

## 9. Signature component: evidence spine

### 9.1 Purpose

The evidence spine makes lineage a structural part of the answer. It answers “what kind of
number is this?” and “where did it come from?” without confusing provenance class,
confirmation state or evidence grade.

Provenance labels are exactly:

- `Source fact` — a value present in an acquired source.
- `Calculated` — a deterministic transformation of one or more inputs.
- `Attested` — a value asserted by the operator about their own book.
- `Assumed` — an explicit input used because it is not a sourced fact.
- `Judged` — an analytical conclusion or classification.

Evidence grade is separate and applies where relevant: `Typed` or `Documented`. Confirmation
is also separate: `Confirmed by <person> at <server-formatted time>`. Neither changes the
provenance label.

### 9.2 Anatomy

The spine is an `<aside aria-labelledby>` containing an ordered list in conclusion-to-origin
order, for example `Judged → Calculated → Source fact`. Each node contains:

1. A visible provenance label in 12/16 mono, 600.
2. A short object/source label in 14/20 Source Sans 3.
3. A real link to the claim, calculation or source record.
4. Optional grade or confirmation on its own line, never merged into the provenance label.

At 960px and above it occupies the 152px working-paper margin. A 1px ledger line connects 8px
nodes; each node has a 2px verification boundary and surface fill. The line is decorative; the
node and link label provide the meaningful 3:1+ mark. Space nodes 16px apart. The source name
may wrap; do not abbreviate it into an unexplained code.

Below 960px the same ordered list appears above its evidence as a compact horizontal sequence.
It uses `flex-wrap`, 8px gaps and presentational chevrons; it never causes page scroll. If a
lineage is too long for one row, it wraps between nodes while preserving DOM order. Do not hide
intermediate nodes behind a tooltip.

### 9.3 States and behaviour

| State | Treatment |
|---|---|
| Default | Quiet ledger connector, verification node, fully visible class label |
| Hover | Link underline strengthens; node wash becomes verification wash |
| Focus | Global ring around the complete linked node |
| Current target | `aria-current="location"`, selected wash and 3px verification rule |
| Missing source | Refusal node reading `Source unavailable`, plus reason/remedy; connector stops visibly |
| Partial lineage | Decision node reading `Lineage incomplete`; available nodes remain navigable |
| Loading enhanced preview | Link text becomes `Opening source…`; the original `href` remains valid |

No JavaScript: every node navigates to the full record or an in-page fragment. With scripting,
the link-first drawer may preview that same destination. The evidence spine must be server
rendered; it cannot appear only after a client request.

**New server data when not already present:** a claim needs an ordered lineage list with
`kind`, human `label`, `href`, optional grade, optional confirmation and an explicit
`is_incomplete`/reason. The template must not reconstruct lineage from identifiers.

---

## 10. Signature component: decision panel

### 10.1 Purpose and placement

The decision panel is the server-owned action edge of a gate. It says what is waiting, what the
choice changes, what it costs and exactly which evidence the decision will bind.

It follows all editable evidence in DOM and focus order. At 1280px it occupies a sticky 304px
column with `top: 24px`; below that it becomes a normal ruled section after the evidence. It
never overlays evidence and never sticks taller than the viewport (`max-height` plus its own
scroll is permitted only for the audit disclosure, not the action controls).

### 10.2 Anatomy

The panel uses a surface body, a decision-wash heading band, radius 8px, a 3px decision leading
rule and 20px inset. A compact recorded/read-only panel may use decision wash across the full
surface when that does not compete with editable evidence:

1. Eyebrow: `DECISION REQUIRED`, `DECISION RECORDED` or `NO DECISION AVAILABLE`.
2. `h2` in 24/30 Barlow, phrased for the specific gate.
3. Consequence sentence in 16/24 primary ink.
4. Cost and elapsed-time definition list, using server-formatted values.
5. Validation message or stale-decision notice when present.
6. Choice controls and actions.
7. Plain-language integrity promise: `You are deciding on exactly the evidence shown here.`
8. Native disclosure `Show payload record` containing the mono hash and audit fields.

Use the gate verb consistently: `Approve research plan`, `Confirm peer set`, `Acknowledge
sector model`, `Confirm themes`, `Confirm assumptions`, `Allow additional spend`, `Approve
finished report`. The alternative action names its consequence; it is never merely `Cancel`.

### 10.3 States

| State | Treatment and controls |
|---|---|
| Decision required | Decision family; complete choice set and action buttons |
| Submitting | Button becomes the same action in gerund form; all choice controls disabled; polite live text |
| Invalid/incomplete | Failure summary inside the panel; focus summary, link to the relevant evidence control |
| Evidence changed/stale | Refusal notice; no submit controls; link to reload the current gate |
| Already decided | Recorded/read-only view with decision, operator and timestamp; controls removed |
| Budget refusal | Refusal explanation plus `Allow additional spend` only when the server offers that gate |
| Run failed | Failure notice and recovery action; no approval controls |

The main choice is not visually preselected unless the server truly owns a default. Approve and
return/reject choices must not use colour to bias an irreversible judgement; radios or clearly
labelled secondary/decision buttons express the real model. Destructive actions use the danger
variant only when they destroy or archive data, not merely because they disagree.

No JavaScript: the panel's controls submit a real POST form with CSRF and payload hash. A
successful POST returns a full page. If buttons sit outside the form for grid placement, every
one carries `form="gate-form"`, and a browser test asserts the association. An htmx enhancement
may replace the relevant state region, but it must not remove the persistent live-region node.

**New server data when not already present:** gate-specific human prompt, consequence,
server-formatted incremental cost, waiting/elapsed text, action choices, integrity sentence,
payload hash, decided-by/time and stale-state reason. The template never guesses a consequence
from the gate key.

---

## 11. Tables and dense records

### 11.1 When to use a table

Use a table only when the reader must compare values across shared rows or columns. A work
queue, page menu, set of independent KPIs or collection of actions is not a table. This avoids
forcing every narrow-screen record into an inaccessible pseudo-card transformation.

All real tables have:

- A `<caption>` that names the comparison; it may be visually hidden only when an adjacent
  heading already provides exactly the same name.
- `<th scope="col">` and `<th scope="row">` wherever a row has a name.
- A `<thead>`; `<tbody>` groups when their boundaries mean something; `<tfoot>` for genuine
  totals.
- Text aligned left, comparable figures right, status labels left, and dates right when they
  form a comparable column.
- Tabular, lining numerals for figures. The server supplies fully formatted text.
- 44px standard rows (12px vertical/12px horizontal cell inset) or 36px dense rows (8px/12px).
  A row containing an interactive control is at least 44px.
- Ledger rules between rows, no default zebra stripes and no boxed cell grid.
- A surface-selected hover row only as orientation help. Selection has a written state and
  3px verification rule as well.
- A sticky header when the table has more than roughly one viewport of rows. The header uses
  `surface`, a strong bottom rule and `z-index` sufficient to cover scrolling cells.

The table sits in a bounded `overflow-x:auto` region. Table patterns declared wide in the next
section receive `tabindex="0"`, an accessible name tied to the caption/heading and a visible
focus ring; fit-content/simple tables do not add that focus stop. The document body never
becomes the horizontal scroller.

### 11.2 Table patterns by job

| Pattern | Structure and density | Alignment and emphasis | Narrow-width contract |
|---|---|---|---|
| Financial statement / time series | Dense 36px rows; 160px minimum row-header column; 112px minimum per period; period groups in `<thead>`; meaningful sections in separate `<tbody>` | Row labels left; all values right in mono; hierarchy indents 0/16/32px; subtotals bold with strong rule; final total uses a double ledger rule plus `Total` text | Internal horizontal scroller; first row-header column sticks left with opaque surface and boundary; no periods hidden and no card conversion |
| Portfolio holdings / reconciliation ledger | Standard 44px rows; security is row header; exact quantity, price, cost, value and gain/loss columns; cash or balancing rows may use sunken fill | Money/quantity right; identifiers left; negative values retain server-rendered sign/parentheses; totals in `<tfoot>`; incomplete pricing inserts a full-width refusal row and withholds the aggregate | Internal scroller; security column sticks left; row actions remain visible via a final sticky action column only if it does not obscure values; otherwise actions follow the table as a labelled form |
| Transactions / audit ledger | Dense or standard according to actions; immutable timestamp, action, object, operator and amount/reference | Timestamp and amount mono; narrative/action left; archived/reversed state written, not shown only by strike-through | Internal scroller; timestamp and action remain first; full detail is a real row link to a page/drawer; never truncate the only audit reference |
| Gate / decision comparison | Standard 44px minimum; issue or concept is row header; submitted controls are inside the one gate form; evidence and consequence columns wrap | Controls left; figures right; unresolved row has a decision leading rule and written label; selected radio/checkbox is native and visible | Prefer wrapping within a 640px minimum table; otherwise internal scroller. Decision panel follows after the table; do not duplicate controls in mobile cards |
| Evidence / source register | Standard rows; source title is row header; source type, acquired date, claims and integrity record follow | Source/title left; dates/counts right; hash in mono and allowed to wrap anywhere inside its cell; provenance link always visible | Internal scroller or a semantic record list when cross-row comparison is not needed. The full hash may move to a native row disclosure, but its label remains visible |
| Operational comparison | Use only for many homogeneous runs/requests where cross-row cost, time or status comparison is the job; otherwise use the record list | Object is row header; formatted cost/time right; status and action left; raw step keys/enums prohibited | Internal scroller. Non-essential duplicated metadata may wrap within the object cell but cannot disappear; use the record-list component when actions dominate |

### 11.3 Table states

- **Empty:** replace the table with the standard empty state; do not render headers over one
  `No data` row.
- **Running:** keep the latest committed rows and add a named information row such as `Prices
  are still being acquired — 14 of 18 listings recorded`. Do not fabricate empty skeleton rows.
- **Partial:** show available rows, add a decision/refusal row for each missing class, and
  withhold affected totals.
- **Failed:** preserve committed rows and place a failure caption/callout before the table.
- **Selected:** written selected state, native control where relevant and verification leading
  rule; hover is not selection.
- **Disabled control:** native disabled semantics, no opacity, and explanatory text in the row.

No JavaScript: the complete server-rendered table is present; sort/filter links and forms work
through GET; row preview links navigate to full pages. Enhancement may reveal search, sticky
affordances or the link-first drawer only after their scripts attach.

---

## 12. Operational components

### 12.1 Run phase and step list

The run console is an ordered list only where execution order is real. It is not a fixed
seven-step wizard: conditional phases and gates appear when they actually occurred. A phase
heading uses 20/26 Barlow; each step row shows a human label, human status, elapsed text,
formatted cost and a disclosure for the audit detail.

Connect completed steps with a ledger rule. Current running work uses the single permitted
heartbeat plus the visible word `Running`. A gate interrupts the line with a decision node and
the specific phrase, for example `Waiting for you to confirm its peer set`. Refused and failed
steps use their distinct semantic families.

Technical step keys (`red_team`, `classify`, `acquire`) are never the primary text. They may
appear inside `Show technical record` in mono. A percentage appears only when the server has a
real denominator; otherwise use named state plus elapsed time.

No JavaScript: the server-rendered current state and normal `Refresh status` GET link remain.
`console.js` may update elapsed text and the persistent status region; reduced motion leaves a
static dot.

### 12.2 Provenance label, confirmation and grade

These three compact components must remain visibly different:

| Component | Shape and wording | Meaning |
|---|---|---|
| Provenance | 2px-radius outlined link, mono label: `Source fact`, `Calculated`, `Attested`, `Assumed`, `Judged` | Origin/class of the figure or claim |
| Confirmation | Plain success-wash line with check icon: `Confirmed by Rowan at 14:32 on 25 August 2026` | A person confirmed a server-owned object |
| Grade | Small neutral/success rectangle: `Typed` or `Documented` | Strength of evidence beneath an attestation |

Do not label a documented attestation `Documented` in the provenance slot. Do not use `Typed`
as a synonym for `Attested`. Provenance is always a real link; confirmation and grade are text
unless they have a genuine detail destination.

### 12.3 Guidance callout

Guidance is visible inline content, never a `title` attribute. It uses a ruled aside with the
explicit 12/16 mono label `GUIDE 3` and 14/20 explanation. The number is passed explicitly so
it remains stable when conditional content is absent. When guidance mode is off, the markup is
removed from accessibility and visual flow with `display:none`; when on, it is visible to
sighted keyboard and screen-reader users alike.

No JavaScript: the shell's server-rendered `data-guidance` attribute controls the view and the
preference is a normal form. Guidance never depends on a tour script or selector list.

### 12.4 Drawer

The enhanced evidence/run preview drawer is 448px wide, at most `min(448px, 100vw)`, docked to
the right with radius 12px on its exposed left edge only. It uses `surface-raised`, a strong
boundary and float shadow. On compact widths it occupies the viewport while retaining a visible
44px `Close this panel` button.

The trigger is always a real link whose `href` targets the full page. With htmx, `hx-get`
fetches the fragment into the persistent drawer body; `drawer.js` opens because content
arrived. The drawer has `role="dialog"`, `aria-modal="true"`, an accessible heading, focus trap,
Escape close, background scroll lock and focus return to the exact triggering link. The overlay
is not focusable; click-to-close is only a pointer convenience.

States: loading retains the destination title and says `Opening evidence…`; failed loading
keeps the full-page link visible; empty preview says what the full page contains. No JavaScript:
the link navigates normally and no inert drawer trigger remains.

### 12.5 Server notice and live update

Server notices are callouts placed after the page heading. A POST success notice uses
`role="status"` only when inserted dynamically; a failure inserted dynamically uses
`role="alert"`. On a fresh full-page response, headings and document order are sufficient—do
not make every callout assertive.

Out-of-band navigation counts target one persistent `aria-live="polite"` slot and replace its
contents, never the slot. Spoken labels are full phrases. Rapid run updates are coalesced so a
screen reader is not interrupted by every elapsed-time tick.

### 12.6 Audit/hash record

The payload or artefact hash is a guarantee, not decoration. Lead with plain language—`You are
deciding on exactly the evidence shown here` or `These are the bytes the run acquired`—then
place the complete value in a sunken mono block. It wraps with `overflow-wrap:anywhere` and is
selectable.

The optional copy icon is progressively enhanced. Without scripting, the value remains
selectable and complete. With scripting, success changes the adjacent live text to `Hash
copied`; do not use a toast as the only confirmation.

### 12.7 Page footer

The footer contains the mandatory disclaimer once, application version and optional build
identity. It uses a strong ledger rule, 12/16 text and `ink-subtle`, which is AA on canvas. The
version/hash uses mono. Do not repeat the disclaimer inside every sheet or report section.

---

## 13. Status vocabulary

### 13.1 Rules

- The server maps internal enums and step keys to the phrases below before rendering.
- A waiting status says what the operator must decide; `Blocked` is not a synonym for waiting.
- `Refused` means the platform upheld a rule. `Failed` means the operation broke.
- `Complete with gaps` is not shortened to `Complete`.
- The status component receives both `kind` and a visible human `label`; it never converts raw
  enum text with string replacement in Jinja or JavaScript.

### 13.2 Run and step labels

| Internal meaning or common enum | Visible label | Semantic kind |
|---|---|---|
| Draft / request not commissioned | `Draft` or `Not run yet` according to object | Muted |
| Queued | `Queued` | Muted |
| Not started | `Not started` | Muted |
| Running | `Running` | Information |
| Awaiting a gate / `AWAITING_APPROVAL` | `Waiting for your decision` plus gate-specific sentence | Decision |
| Succeeded / complete | `Complete` | Success |
| Complete but some output is unavailable | `Complete with gaps` | Decision |
| Confirmed | `Confirmed` | Success |
| Skipped because not applicable | `Not applicable` | Muted |
| Skipped because an earlier rule stopped work | `Not run — earlier step stopped` | Refusal |
| Spending ceiling reached before a call | `Stopped at spending ceiling` | Refusal |
| Deliberate methodology refusal | `Answer refused by rule` or a specific object phrase | Refusal |
| Runtime/database/extraction failure | `Failed` plus named operation | Failure |
| Cancelled by operator | `Cancelled` | Muted |
| Archived | `Archived` | Muted |

### 13.3 Gate names and operator phrases

| Gate | Panel title/action phrase |
|---|---|
| Plan | `Approve its research plan` |
| Unmapped concepts | `Decide about the figures nothing could map` |
| Peer set | `Confirm its peer set` |
| Sector | `Acknowledge that the standard model does not fit its sector` |
| Themes | `Confirm the themes it belongs to` |
| Assumptions | `Confirm the assumptions its valuation will be built on` |
| Budget | `Decide whether it may spend more than its ceiling` |
| Final | `Review the finished report` |

These phrases are data and require an exhaustive test. Adding a gate without a human phrase
must fail the build.

### 13.4 Provenance and evidence labels

| Dimension | Allowed visible values |
|---|---|
| Provenance class | `Source fact`, `Calculated`, `Attested`, `Assumed`, `Judged` |
| Evidence grade | `Typed`, `Documented` |
| Confirmation | `Unconfirmed`, or the complete `Confirmed by … at …` phrase |
| Integrity | `Verified`, `Verification unavailable`, `Changed since decision` |

Do not reuse a status colour to imply a provenance class. A calculated figure can fail, a
source fact can be stale and an attestation can be documented; those dimensions must compose
without replacing one another.

---

## 14. Icon policy

Tracework introduces one small inline SVG system rather than an icon font or a runtime library.
Every icon uses `viewBox="0 0 24 24"`, `fill="none"`, `stroke="currentColor"`,
`stroke-width="1.75"`, `stroke-linecap="round"` and `stroke-linejoin="round"`. Standard sizes
are 16px for inline/navigation, 20px for controls and semantic notices, and 24px only for an
empty or refusal state. Status icons may render at 14px but retain the 24-unit view box.

The finite v1 allow-list is:

| Group | Icon names |
|---|---|
| Navigation | `menu`, `close`, `chevron-down`, `chevron-right`, `arrow-left`, `arrow-right`, `external-link` |
| Actions | `search`, `copy`, `archive`, `refresh` |
| Status | `check`, `clock`, `activity`, `pause`, `information`, `warning`, `refusal-shield`, `failure-x` |
| Evidence | `document`, `source`, `calculation`, `person-attestation`, `assumption`, `judgement` |

Store paths in one `_ui/icons.html` macro with an exhaustive allow-list and tests. Commit and
hash any source asset used to author them; do not load a CDN. Do not use emoji, font glyphs
(`☰`, `←`, `→`) or mixed stroke sets.

- Beside visible equivalent text: `aria-hidden="true" focusable="false"`.
- Meaningful standalone graphic: `role="img"` with a programmatic title.
- Icon-only button: the button has visible-on-focus or visually hidden text such as `Close this
  panel`; the SVG remains hidden from accessibility so the name is not duplicated.
- Presentational connectors and chevrons are hidden from accessibility.

Icons never replace the words `Refused`, `Failed`, `Running` or `Confirmed`.

---

## 15. No-JavaScript contract

This matrix is an acceptance test, not a graceful-degradation aspiration.

| Surface/component | Enhanced behaviour | Scripting-off behaviour |
|---|---|---|
| Navigation | Optional htmx badge refresh | One native disclosure/persistent index; initial server counts; all links and preference forms work |
| Form validation | Error fragment swaps into persistent live region | Identical POST and validation return the full page with summary and retained values |
| Decision panel | Submit control can disable and update in place | Real POST with CSRF and payload hash; full-page result |
| Drawer preview | Link is intercepted and fragment opens in focus-trapped drawer | Same `href` navigates to complete detail page |
| Table client search | Hidden control is revealed after the filter script attaches | Complete table, no dead search control |
| Sort/filter | Optional delayed GET submission | Visible `Apply filters` and ordinary sort GET links preserve URL state |
| Run console | Elapsed text and status may poll/update | Current server state plus normal `Refresh status` GET link |
| Evidence spine | Node may open a preview drawer | Every node is a normal full-record or fragment link |
| Disclosure | Optional chevron enhancement | Native `<details>`/`<summary>` works |
| Copy hash | Button copies and announces success | Full value remains visible and selectable; copy control stays hidden |
| Guidance | None required | Server-owned preference and `data-guidance` display inline guidance |
| Theme | Optional immediate preview | Preference form reloads into explicit light/dark/system scheme |
| Notices/live counts | Polite live update | State is present in the full page; no meaning depends on an announcement |

Revealed controls begin with the HTML `hidden` attribute and are exposed only after their
handler is attached. A no-script browser never sees a present-but-dead enhancement.

---

## 16. Accessibility contract

Tracework targets **WCAG 2.2 Level AA** in light, dark and system themes. The following is part
of component completion.

### 16.1 Structure and names

- One non-empty `h1`; headings do not skip levels. Dynamic headings have server fallbacks.
- Use native landmarks: one `main`, labelled `nav`, `aside` for evidence/decision where
  appropriate, and footer. Repeated landmarks have unique names.
- Forms, buttons, links, tables, lists and disclosures use their native elements. No clickable
  generic containers.
- Every control has an accessible name. Visible labels are preferred; icon-only controls have
  hidden text.
- The document language is `en-GB`. Issuer/source text in another language uses `lang` when
  known.
- Status words, provenance and grade remain separate in accessible names as well as visually.

### 16.2 Keyboard and focus order

- Every function is reachable with keyboard alone. There are no hover-only menus, row-only
  click handlers or drag-only controls.
- Focus order follows DOM/reading order. Evidence precedes decision controls even when CSS
  places the decision panel beside it.
- The universal focus ring is present on every link, button, field, summary, scrollable table
  region and composite control.
- Opening a drawer moves focus to its heading/first action; Escape closes it; close restores
  focus to the exact trigger. Focus never falls back to the top of the page.
- Skip link reaches `main`. After invalid submission, focus reaches the error summary. After a
  successful ordinary navigation, browser document focus behaviour is retained.

### 16.3 Reflow, resize and targets

- Support a 320px CSS viewport and 200% browser zoom without page-level horizontal scrolling.
- Text spacing overrides meeting WCAG 1.4.12 do not clip, overlap or hide text.
- Wide tables use their labelled internal scroll regions. Drawers fit `100vw`; sticky content
  never blocks the page at 400% zoom.
- Primary pointer targets are 44px; dense targets are at least 32px with 8px separation. This
  exceeds the WCAG 2.2 24×24 minimum.
- Content does not require a particular device orientation.

### 16.4 Colour, graphics and motion

- All sanctioned normal-text pairings in section 2 pass 4.5:1. Filled actions and semantic
  labels pass 4.5:1 in both schemes.
- Control boundaries, focus rings, evidence nodes and meaningful chart marks pass 3:1. Quiet
  ledger lines are decorative and cannot carry meaning.
- State always includes text; data series include direct labels and line/shape differences.
- `prefers-reduced-motion` removes the heartbeat animation and all non-essential transitions.
- Forced-colours mode retains native focus, borders, checked state and textual status.

### 16.5 Forms, errors and updates

- Hints and errors are programmatically associated; error summaries link to fields; submitted
  values survive validation.
- Required state is written and programmatic. Format examples do not replace instructions.
- A live region persists while its contents swap. Counts have complete spoken phrases.
- Routine status updates use polite announcements and are coalesced. A dynamic failure may use
  alert; initial-page callouts do not all shout as alerts.
- A decision is never submitted on selection change alone. The operator explicitly activates
  the named action button.

### 16.6 Tables and dense evidence

- Real tables keep native table display semantics at all widths; CSS does not turn cells into
  block cards.
- Captions, column headers and row headers are present. Totals and groupings are expressed in
  markup/text, not only rules.
- Sticky cells have opaque backgrounds and do not conceal focused content. The table scroll
  region itself can be focused when overflow exists.
- A truncated visual label retains its complete accessible name and a keyboard-reachable way
  to inspect it. Hashes and source titles should normally wrap instead of truncate.

### 16.7 Verification matrix

Every material surface is tested in both schemes at 320, 640, 960, 1280 and 1600px, plus 200%
zoom. Required checks:

1. Axe-core as a build failure for each populated, empty, partial, refused, failed and decision
   state.
2. Computed-colour contrast tests for the pairs in section 2; do not assert class names.
3. Keyboard-driven tests for the index, fields, gate form, table scroller, disclosure and
   drawer focus return.
4. Automated assertions for one `h1`, unique ids, table headers/captions, live-region
   persistence and one navigation badge target.
5. By-hand keyboard, screen-reader smoke test, reduced-motion, forced-colours, narrow reflow and
   text-spacing pass.

---

## 17. Implementation constraints and acceptance

### 17.1 Server and template contract

- Jinja runs with `StrictUndefined`; every new field named in this system is supplied in every
  render path or represented by an explicit typed optional state.
- `render()` remains the only page door so shell, theme, guidance, CSRF and disclaimer cannot
  be omitted.
- Formatted figures, currencies, dates and status phrases arrive from the server. Report money
  in millions and portfolio money exact to the penny remain distinct renderings.
- Destructive actions are POST forms. Returnable views are GETs with state in the URL.
- Payload hash and CSRF remain in the real decision form. An external grid-positioned button
  has a tested `form` association.
- Component macros accept data/variant enums, never arbitrary presentation classes.

### 17.2 Assets and CSS

- Vendor Barlow Semi Condensed, Source Sans 3, IBM Plex Mono and the finite SVG assets; record
  SHA-256 values. No runtime request leaves the machine.
- Add semantic variables alongside the current CSS, migrate redesigned templates away from
  stock colour ramps, run the Tailwind v4 build and commit the compiled stylesheet.
- Ensure `@source` covers every literal component class. Do not compose status classes in
  JavaScript or Jinja.
- System theme applies through the media query only when no explicit data-theme attribute is
  present.

### 17.3 JavaScript admission

This design system requires no new general-purpose JavaScript pattern. It reuses the existing
named contracts for drawer, console updates, tables/progressive reveal and htmx form swaps.
If implementation introduces another script, its component specification must state trigger,
DOM ownership, keyboard behaviour, announcement behaviour, failure state and no-script path
before the script is accepted.

### 17.4 Definition of done

A component or page is complete only when:

- Light and dark values come exclusively from these semantic tokens.
- Default, hover, focus, selected/active, disabled, loading, invalid and relevant content
  states are implemented.
- Its no-JavaScript path has been exercised, not merely described.
- It reflows at 320px and 200% zoom with no page-level horizontal scroll.
- Human status and step vocabulary replaces raw enums/keys.
- Consequential figures expose provenance, and confirmation/grade are not conflated with it.
- Refusal, failure, partial and empty copy names the next meaningful action.
- Automated accessibility, contrast and keyboard checks pass in both schemes.
- Any new server field and any enhancement script is explicit in the page implementation
  contract.

This system should feel quiet in a screenshot and unusually rigorous when used. The evidence
spine is the memorable visual device; everything else earns its place by making a decision
clearer or its proof easier to inspect.
