# Tracework redesign — start here

Tracework is the proposed working identity and interface direction for the platform. It treats
the product as an analyst's working paper rather than a generic finance dashboard.

> **Verdict first. Evidence beside it. Proof on demand.**

The name is a replaceable working label. The information architecture, interaction rules and
visual system do not depend on keeping it.

## What is included

| Artefact | Use it for |
|---|---|
| [`00-design-direction.md`](00-design-direction.md) | Product experience, design thesis, visual character and the major UX changes |
| [`01-design-system.md`](01-design-system.md) | Normative tokens, type, layout, components, responsive rules, accessibility and content style |
| [`02-page-specifications.md`](02-page-specifications.md) | Route-by-route layouts, states, interactions, exact content patterns and marked server proposals |
| [`03-claude-implementation-handoff.md`](03-claude-implementation-handoff.md) | Production architecture, migration sequence, template contracts, tests and a ready implementation brief for Claude |
| [`04-validation-report.md`](04-validation-report.md) | What was checked in the prototype and what must still be verified in production |
| [`prototype/index.html`](prototype/index.html) | Visual entry point to the twelve-screen interactive reference |

Recommended reading order for implementation: original requirements in `../design/`, then
`00`, `01`, `02`, and finally `03`. Give Claude the entire `redesign/` folder together with the
original `design/` folder so it can follow the authority order in the handoff.

## Prototype map

The prototype is intentionally a small multi-page website so routes, browser history and
no-script links stay understandable. Its pages are:

1. `index.html` — attention-first overview and page index.
2. `requests.html` — research request queue.
3. `request-new.html` — request authoring form.
4. `run.html` — run console and phase journey.
5. `gate.html` — shared gate and decision-panel pattern.
6. `review.html` — verdict-first final review.
7. `evidence.html` — claim, source and calculation lineage.
8. `reports.html` — report history.
9. `skills.html` — skills and import readiness.
10. `knowledge.html` — queryable knowledge records.
11. `portfolio.html` — dated holdings, provenance and transaction drawer.
12. `components.html` — visual states and core component inventory.

Every page includes light and dark themes, the persistent single-DOM index, keyboard-visible
focus, one disclaimer and responsive behaviour down to 320px.

Representative captures are available in `previews/`:

- [`overview-light.png`](previews/overview-light.png) — returning overview at 1440px.
- [`review-light.png`](previews/review-light.png) — final review with its decision edge.
- [`evidence-dark.png`](previews/evidence-dark.png) — evidence lineage and calculation replay.
- [`overview-mobile.png`](previews/overview-mobile.png) — compact overview at 320px.

## Viewing it

Open [`prototype/index.html`](prototype/index.html) directly, or run the dependency-free local
server from this folder:

```powershell
cd redesign/prototype
node serve.cjs
```

Then open `http://127.0.0.1:8765/`.

The prototype's JavaScript only demonstrates theme switching and the accessible evidence
drawer. It is not production state management. Approval status, costs, figures, formatting,
evidence and totals remain server-owned exactly as required by the original contract.

## Implementation authority

If artefacts disagree, preserve domain truth, safety and required behaviour from the original
requirements first. Use the design direction and design system for hierarchy and appearance,
the page specifications for route composition, and the Claude handoff for production structure
and migration order. The prototype is a visual and interaction reference, never a source of
domain truth.

Items labelled **[NEW SERVER DATA]**, **[NEW SERVER BEHAVIOUR]**, or **[NEW ROUTE]** are explicit
proposals. Claude should implement or defer them deliberately; it must not fabricate them in
templates or JavaScript.
