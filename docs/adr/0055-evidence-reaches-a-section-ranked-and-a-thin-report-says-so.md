# 0055 — Evidence reaches a section ranked, and a thin report says so at the front

Date: 2026-08-16
Status: Accepted

## Context

The first complete live run produced a 23-page research note with no revenue figure in
its prose, no valuation, four sections missing outright — and page 21 plotting four years
of revenue and margins from stored facts. The run held 18,588 facts and 69 extracted
excerpts. The sections were starved anyway, by three compounding selection defects in
`gather_evidence`:

- **Facts were chosen newest-period-first, then alphabetically**, capped at forty. A
  large filer's newest period runs to thousands of facts, so the alphabet was the real
  selector: every section received "Accrued…, Accumulated…, AvailableForSale…" and
  Revenue never survived to R.
- **Excerpts were chosen oldest-created-first** with no regard for the section's
  subject. Every section received the same two excerpts — a LinkedIn segment
  description, and a filing signature page.
- **The compact listings were gathered first and the budget admitted in order**, so the
  bulky excerpts were pure overflow. Every section reported its evidence truncated.

The writers then did exactly what they are built to do: they described, at length and
with remediation instructions, evidence that was absent — producing a document shaped
like an institutional note whose content was an account of its own starvation, and whose
"the evidence does not contain this" statements were false of the run that produced them.

The operator's decision on the product question: a thin run still renders a research
note, with a small warning at the front and the sources in reach.

## Decision

**Selection is the section's, computed deterministically — and the section's preferences
are rows, not code.** The first cut keyed preference tables by section key inside
`gather_evidence`, and the hardcoded-key guard (`TestNoSectionKeyIsHardcoded`) refused
it, correctly: sections are rows, and a module that names one has made the next section
a code change. So migration 0029 merges `concept_priority` and `excerpt_keywords` into
each seeded definition's `evidence_policy`, `SectionPolicy` carries them, and
`gather_evidence` never learns which section it serves. Facts rank by where their
canonical concept (`aer.core.concepts`) sits in the policy's preference order —
cash-flow lines first for the cash-flow section, the income statement first for the
history — then by period recency; canonical concepts a policy did not name still beat
unmapped footnote debris, and a policy declaring nothing (every custom section) gets
that default. Excerpts rank by keyword affinity with the policy's keywords and drop
non-substantive text — signature blocks, powers of attorney — before ranking. Ranking
pools are far wider than the old caps, because a pool the size of the cap makes the
query's ORDER BY the real selector.

**The listings are capped to a share of the budget** (facts to 45%, all compact listings
to 60%), so the excerpts always keep a seat and what overflows is the *least relevant*
excerpt rather than every excerpt. Truncation still keeps or drops a unit whole, so the
closed world of citable ids is unchanged.

**A thin report carries one coverage notice, at the front, derived from recorded state.**
`assemble_document` builds it from section statuses and failed evaluation rows — never
from prose — and every notation renders it beside the disclaimer with a link to the
Sources appendix. The contents page marks ungenerated sections. A failed section renders
its status line only: the validator's diagnostics (raw ids, schema paths) stay in the
run's console and logs, where their reader lives.

**The writers are told who they write for.** Both drafting prompts now instruct: never
mention evidence budgets, truncation, retrieval or what a future revision should fetch;
state a gap in one clause and analyse what the evidence supports.

## Consequences

**Ranking can only surface what extraction captured.** If the extract step never took an
excerpt from the debt note, no ordering will produce one. Extraction breadth is the next
lever, deliberately out of this ADR's scope.

**The preferences are seed data, maintained the way sections are.** A new built-in
section declares its `concept_priority` and `excerpt_keywords` in the migration that
seeds it — the spine seed pin requires both, non-empty and canonical — and a section
that declares none runs safely on the default ordering. The keyword lists are
deliberately short; they rank, they do not filter.

**The coverage notice cannot be softer than the run.** Because it derives from statuses
and evaluation rows, a run with four failed sections cannot render without saying so on
page one. The reverse also holds: a full run carries no notice at all.

**Custom sections inherit all of it** — the default concept ordering, the substance
filter, the share guard — with no change to the pinned-grant category gate.

**Enforced by mutation-verified tests**: ranking disabled, keyword affinity disabled,
substance filter disabled, share guard removed, coverage never built, diagnostics
restored to the document, display rounding removed, escapes left literal, and the voice
rule dropped each fail a named test.
