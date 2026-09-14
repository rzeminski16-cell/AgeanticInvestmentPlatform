# The drawn screens

**Nineteen artboards — every surface in the system.** Drawn in the platform's own **Tracework**
design system: teal where something can be inspected, amber where a person must choose, every
colour paired with a word. Every token is lifted from
[`../../../design-system.md`](../../../design-system.md); nothing here invents
a colour, a size or a radius.

**The canvas: https://claude.ai/code/artifact/4af01758-27d6-4c34-9f5c-d9283d6f44c7**

It is organised as five pages that follow the loop, so it can be evaluated a stage at a time.

| Page | Artboards | Specified in |
|---|---|---|
| **The hub** | Today | [`../../03-page-specifications.md`](../../03-page-specifications.md) §1 |
| **1 · Research** | New request · Active run · Report reader · Ask · Reports · Methods · Knowledge | §6, §7, §8, §13, §9, §17, §18 |
| **2 · Decide** | Company · Thesis · Decision | §5, §10, §11 |
| **3 · Hold** | Portfolio · Position · Risk · Companies · Monitor alert | §2, §3, §14, §4, §12 |
| **4 · Review & platform** | Post-trade review · Decision analytics · Platform | §15, §16, §19 |

Each page carries a note explaining what to look for. `_shared.css` holds the tokens every
artboard imports.

These are **static mockups** — nothing is wired, and no figure in them comes from a real run.
They settle layout, hierarchy and wording; the specifications are normative where the two
disagree.

## What to look at first

- **Today** is a briefing, not an inbox. It leads with the conviction strip — every position by
  weight, coloured by whether its reasoning holds — because the two-second read is *how much of
  my book still has a reason behind it*. The queue is small, and on the right.
- **Portfolio** sorts by conviction risk. A position with no thesis sorts above everything.
- **Decision analytics** is drawn greyed out on purpose: eleven reviews is not twenty, so the
  page says so and shows nothing else.
