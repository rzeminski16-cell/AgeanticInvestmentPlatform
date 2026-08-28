# ADR 0088 — A fixed-scheme region carries its own measured palette

**Status.** Accepted
**Date.** 2026-08-25
**Extends.** ADR 0006's stylesheet and the design tokens that arrived with the shell.
**Required by.** Roadmap §3.12. Found by measuring the delivered design rather than reading
it.

## Context

The redesign's navigation is a rail that stays dark while the page changes scheme. On a
light page it is `#102B35`; on a dark page it is the same. That is a good decision — a
persistent index reads better as a distinct plane than as another sheet of the same paper —
and it introduced a defect that the design's own validation reported as passing.

**The rail's colours appear in no token table.** §2.2–2.5 of the delivered design system
specify canvas, surface, raised, sunken, selected, the inks, the lines, the control boundary
and five semantic pairs. It specifies none of the rail. So §2.7, which measures contrast
exhaustively and correctly for everything it contains, measures the focus ring against
`surface`, `canvas`, `sunken` and `raised` — and never against the one surface the ring is
most often drawn on.

Measured in Chromium, focusing the first navigation link with the light theme explicitly
chosen:

| Token painted on the rail | Ratio | |
|---|---:|---|
| `focus-ring` light `#00606D` | **2.04** | **Fails WCAG 2.2 SC 1.4.11 (3:1)** |
| `verification` light `#0F6673` | 2.23 | Fails |
| `decision` light `#7A4B00` | 2.00 | Fails |
| `focus-ring` dark `#B5ECF0` | 11.43 | Passes |
| Selected-row fill `#183945` | 1.21 | Cannot carry selection alone |

**Keyboard focus is very nearly invisible in the navigation, in the default theme.** Every
individual token is correct; every measurement taken was accurate; the region they are
painted on was not in the list of things to measure.

This is the same shape of defect as roadmap §2.5, one storey down. There, a test proved the
theme switch worked and forty-one templates did not follow it. Here, a table proved every
pairing passed and the surface that needed it most was not a row.

## Decision

**A region that keeps one scheme's colours while the page changes scheme is a palette in its
own right. It declares its own token family, and every colour painted on it is measured
against it.**

Three parts.

**It is named.** The rail's colours become a token family beside the others — `nav`,
`nav-surface`, `nav-ink`, `nav-muted` — rather than living as literals in one component's
rules. A colour that is not a token is a colour nobody will measure.

**It takes the scheme it looks like, not the scheme the page is in.** A permanently dark
region takes the dark accents in both themes. Every dark accent clears 3:1 on the rail
comfortably; every light accent fails it. Scoped by a container rule on the region, never by
duplicating tokens.

**Its pairings are measured and recorded**, in the same table as every other surface, from
computed colour rather than from a class name. Text on the rail already passes — `nav-ink` at
13.65:1, `nav-muted` at 8.54:1 — and recording that is what stops a future adjustment quietly
breaking it.

### And a corollary about the selected row

The selected-item fill is `#183945` on `#102B35`: **1.21:1**, which is nothing. Selection is
carried by the teal left rule at 11.43:1 and by `aria-current="page"`, and **the fill is
decorative**. Written down because a fill that appears to indicate selection is exactly the
thing a later simplification removes the rule from.

## Consequences

### What it costs

- **A second small palette to keep in step.** Real, and the alternative is a region nobody
  measures. Held by the same computed-colour test as everything else.
- **A component rule rather than a global one.** Accents inside the rail resolve differently
  from accents outside it, which is a thing a developer has to know. It is one rule in one
  place and the test fails loudly when it is forgotten.

### What it buys

**The general case, not the instance.** The rail is the only fixed-scheme region today. A
future code block, a print preview, a permanently light document surface inside a dark
application — each is the same trap, and each is now covered by a rule rather than by
somebody happening to remember.

**A measurement discipline that names its own blind spot.** The lesson worth keeping is not
"the rail was wrong". It is that **an exhaustive table is only exhaustive over the things
it lists**, and the surface most likely to be missing from the list is the one that behaves
differently from all the others.
