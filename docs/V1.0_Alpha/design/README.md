# The designs — what they are and how to use them

*Nineteen screens covering every surface in V1.0. Drawn 14 September 2026.*

**The canvas:** https://claude.ai/code/artifact/4af01758-27d6-4c34-9f5c-d9283d6f44c7

---

## What these are, and what they are not

**They are static mockups.** Nothing is wired, nothing is clickable, and **no figure in them
comes from a real run** — every company, premise, price and percentage is invented so the
screens read as real. Do not lift a number from a screen into anything.

**They settle three things**: layout, hierarchy and wording. Where a screen and a page
specification disagree, [`../03-page-specifications.md`](../03-page-specifications.md) wins on
behaviour, data and states; the screen wins on nothing — it is an illustration of the spec, not
a second source of truth.

**They are drawn in the real design system.** Every colour, size, radius and font is lifted from
[`../../design-system.md`](../../design-system.md) — the same contract the shipped interface is
built to. Nothing here invents a token. If a screen shows a colour, that colour has a name and a
meaning in that document.

## How to read a screen

Three conventions carry most of the meaning, and they are the design system's rules rather than
this canvas's:

| You see | It means |
|---|---|
| **Teal** (`verification`) | This can be inspected. Links, evidence, footnotes, anything that opens a drawer |
| **Amber** (`decision`) | A person must choose, or money and time need attention. Gates, ceilings, thresholds |
| **Green / red / purple** (`success` · `failure` · `refusal`) | A test passed · something is a fault · **a guardrail deliberately withheld an answer**. The third is not an error and never shares a colour with one |

**Colour never carries meaning alone.** Every coloured thing is paired with a word, because a
reader who cannot distinguish the hues must still get the whole meaning.

## How to use them

**To evaluate the design.** Open the canvas and walk the five pages in order — the hub, then
research, decide, hold, review. That is the loop, and each page carries a note saying what to
look for. Judge each screen against two questions: *would I know what to do here?* and *is
anything on this page not earning its place?*

**To build from them.** Do not build from the screen. Build from
[`../03-page-specifications.md`](../03-page-specifications.md), which names every component,
every state (empty, loading, refused, failed, partial), the data contract and the constraints —
and use the screen to see what the spec means. A screen shows one state; the spec lists all of
them, and the states you cannot see here are where the work actually is.

**To change one.** The screens are `.dc.html` files in [`screens/`](screens), each one an
artboard. Edit the file and re-seed the canvas, or edit it in the canvas itself and save. Every
artboard imports `screens/_shared.css`, which holds the design-system tokens — change a token
there and all nineteen screens move together.

**To show somebody.** The canvas exports PNG per screen and PDF for the set.

## What is deliberately not drawn

- **Every state but one.** Each screen shows its populated, working state. Empty states, loading
  skeletons, refusals, failures and partial data are specified but not drawn — there would be
  sixty screens and the extra fifty would settle nothing that the spec does not already settle.
- **Mobile and narrow widths.** The design system's breakpoints say what happens below 1280px;
  these are all drawn at 1440. The rules are written; the drawings would be repetition.
- **Motion.** Nothing here moves, and nothing in V1.0 should move much.

## The files

| File | What it is |
|---|---|
| [`page-guide.md`](page-guide.md) | Every screen, in depth: what it is for, what to look at, and the design decision it embodies |
| `screens/*.dc.html` | One artboard per surface. Static HTML with inline styles |
| `screens/_shared.css` | The design-system tokens every artboard imports |
| `screens/canvas.json` | The canvas layout: five pages, positions, and the notes on each page |
