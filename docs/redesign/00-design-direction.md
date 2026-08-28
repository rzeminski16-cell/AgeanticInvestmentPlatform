# Tracework — redesign direction

> Working identity only. The name can be replaced without changing the interface system.

## The subject, audience, and single job

Tracework is a personal equity-research instrument for one financially literate operator. It is not a consumer brokerage dashboard and it is not a collaboration product. Its defining job is to let the operator make a consequential decision while seeing exactly what the platform knows, what it refuses to infer, what the decision will cost, and how every material figure can be checked.

The interface should feel like a precise analyst's working paper: calm at first glance, dense when opened, and visibly trustworthy because its proof is always within reach.

## Design thesis

**Verdict first. Evidence beside it. Proof on demand.**

Every operational page follows one hierarchy:

1. Where am I and what object am I looking at?
2. What is true now?
3. Does the platform need a decision from me?
4. What has this cost, and how long has it been in this state?
5. What evidence supports the answer?
6. What technical record is available if I need to audit it?

This replaces the current pattern in which raw enums, identifiers, hashes, and tables often appear before their meaning.

## Compact visual plan

### Colour

The core light palette uses six deliberately quiet colours:

| Name | Hex | Role |
|---|---:|---|
| Paper | `#F4F7F8` | Page canvas; cool enough to feel technical without looking clinical |
| Sheet | `#FFFFFF` | Working surfaces and evidence documents |
| Graphite | `#15252E` | Primary ink |
| Verification ink | `#0F6673` | Brand, links, selected navigation, and traceable proof |
| Ledger line | `#CDD8DC` | Structural rules that separate records without boxing everything |
| Decision amber | `#7A4B00` | Attention and cost-bearing decisions; never used as decoration |

Dark mode moves the same idea into deep blue-black paper (`#07171D`) and blue-green sheets (`#0C222B`), with pale verification ink (`#B5ECF0`). There are no gradients. Semantic success, warning, refusal, failure, information, and muted states use accessible ink/wash pairs specified in `01-design-system.md`.

### Type

- **Display and object identity:** Barlow Semi Condensed, 600–700. Its compact, engineered forms suit long issuer names and make hierarchy without oversized headings.
- **Interface and reading:** Source Sans 3, 400–650. Open, neutral, and comfortable in dense evidence and long-form explanations.
- **Figures and records:** IBM Plex Mono, 450–600. Used only for tabular figures, formulas, hashes, dates, identifiers, and compact utility labels.

All three are open-source and must be vendored and hashed by the production application. The prototype uses compatible local fallbacks when the files are unavailable.

### Layout

Wide screens use one navigation rendering and an asymmetric working area:

```text
┌────────────── index ──────────────┬───────────────────────────────────────────────┐
│ Tracework                         │ Research / Contoso plc / Run 184              │
│                                   ├───────────────────────────────────────────────┤
│ Overview                          │ OBJECT + CURRENT VERDICT                      │
│ Research                  2       │ What is true, what is waiting, what it costs  │
│   Requests                        ├──────── evidence / work ───────┬─ decision ──┤
│   Reports                         │                                │              │
│   Skills                          │ Detailed record with a         │ Sticky,      │
│   Knowledge                       │ visible evidence spine         │ server-owned │
│ Portfolio                         │                                │ action       │
│                                   │                                │              │
└───────────────────────────────────┴────────────────────────────────┴──────────────┘
```

At narrow widths, the same navigation DOM becomes a native disclosure. The decision panel follows the evidence in reading and focus order. Wide tables scroll inside their own bounded region; the page never scrolls horizontally.

### Signature: the evidence spine

The memorable device is a working-paper margin that connects a conclusion to its lineage. A vertical rule and labelled nodes show `Judged → Calculated → Source fact`, or `Attested → Documented`, without turning provenance into decorative badges. Each node is a real link. The spine is visually quiet when scanning and becomes the fastest route to proof when a number is doubted.

On narrow screens it becomes a compact horizontal sequence above the evidence. It never hides provenance and never merges provenance class with confirmation state.

## One deliberate aesthetic risk

The design spends permanent horizontal space on a margin-note column inside important research pages. Most dashboards maximise the width of cards and tables; Tracework gives part of that width to provenance, refusal reasons, and decision consequences. This is justified because inspectability is the product, not metadata. The margin collapses into the reading flow below 960px.

## Motion

Motion has one job: establish that a running step is alive. A restrained heartbeat and elapsed-time update may animate while work is healthy. Evidence, approvals, totals, and statuses never animate into a value. When reduced motion is requested, the heartbeat becomes a static labelled state and no meaning is lost.

## Self-critique and revision

The first route was a conventional cool-blue application with a sidebar, cards, and status chips. That could describe almost any enterprise dashboard. Three changes make this direction specific to the brief:

1. Cards are replaced by working sheets, ledger rules, margin notes, and object-level verdicts.
2. The evidence spine turns the product's provenance model into the signature structure rather than another badge style.
3. Conditional research gates are shown as decision points within phases, not as a generic seven-step wizard that would lie about the workflow.

The rest of the interface stays restrained so this one idea carries the identity.

