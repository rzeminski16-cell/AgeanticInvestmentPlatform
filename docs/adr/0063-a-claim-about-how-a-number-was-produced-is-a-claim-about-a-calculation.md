# 0063 — A claim about how a number was produced is a claim about a calculation

Date: 2026-08-19
Status: accepted

## Context

The first complete report's DCF section described a methodology the run never executed:
beta from five years of weekly returns (the operator typed 1.79, `by_human: true`), a
risk-free rate read off the on-the-run ten-year Treasury (typed, 0.03), a cost of debt
built from the company's own note coupons and traded yields (derived from filed interest
expense; the run holds no bond data), market-value weights (the recorded basis is book),
segment forecasts built on capacity and unit pricing (six ratio drivers on consolidated
revenue), minority stakes added in the bridge (no such calculation exists), and an
implied return against a closing price (the run holds no price data).

**Every existing defence passed it.** The numeral rule guards figures, and this prose
stated almost none. Citation verification guards quotes, and these were not quotes. The
excerpt verifier re-reads artefacts, and no artefact was named. A section can evade the
entire validation apparatus by being confidently qualitative — and qualitative prose
about method is exactly where a reader's trust in the numbers is set.

The invariant "no figure reaches a report unless it is a stored fact or a recorded
calculation" turned out to guard the numbers and not the *account of the numbers*. The
account is what a reader believes.

## Decision

**A statement about how a figure was produced is treated as a claim about a calculation,
and only code may make one.** The general rule: any section content whose subject is the
platform's own record — a method description, a provenance table, a list of what was
measured against what was typed — is rendered from that record by deterministic code,
never written by a model. A model's paraphrase of a record can only be equal to it or
wrong, and when it is wrong it is wrong in the most trusted register the report has.

Concretely, for the valuation section:

* **The contract gains platform-filled fields.** Migration 0044 publishes version 2 of
  `valuation_dcf`, whose method fields carry `"platform_filled": true`: how the figures
  were produced, every cost-of-capital component with its value and *how it was set*, the
  forecast drivers named as the assumptions they are, both terminal methods carried to
  per-share figures with the share count and its source, and the valuation's recorded
  caveats — which is where "the two methods disagree by more than a quarter" finally
  reaches a reader, the most informative sentence the first run produced and never
  printed.
* **`aer.sections.valuation_method` renders them from the ledger.** Calculations are read
  back by name, method and case (the *first* base-case row, because a sensitivity grid's
  cells are whole DCFs recorded after the base run under the same case label);
  assumptions carry the `by_human` of their standing proposal, so a typed beta says "set
  by the operator" and can never say it was estimated from returns. Nothing is
  recomputed: a figure that is not in the ledger is not in the block.
* **The model keeps `commentary`, and cannot write the rest.** The writer is bound by the
  contract minus the platform-filled fields (`model_facing_contract`), and its envelope
  forbids unknown keys, so the method fields are unrepresentable in a reply rather than
  merely discouraged. The rendered block is merged into the stored content at the
  contract's declared positions after the draft passes — and kept even when the model's
  part fails, because the record is true whatever the commentary did.
* **The commentary has a deterministic edge.** `commentary_problems` refuses a commentary
  that names a method input absent from the rendered block — and refuses outright the
  inputs this workflow never holds: prices, market capitalisations, bond yields, return
  regressions. The refusal happens inside the drafting loop, so a retry is told which
  term to remove rather than throwing the draft away, and the salvage path revalidates
  against the same edge.

## Consequences

* Less fluent prose about method, in exchange for prose that cannot describe work that
  did not happen. That is the trade, and it is accepted knowingly: the method block reads
  like a record because it is one.
* The mechanism is general — `SectionAugmenter`, registered by section key beside the
  deterministic builders — so the next section whose subject is provenance (a data-
  lineage appendix, an assumptions register) attaches the same way without touching the
  drafting loop.
* The vocabulary in `commentary_problems` is a deliberately blunt instrument: a fixed
  term list, matched on word boundaries. It will miss paraphrases ("the equity beta was
  regressed" is caught; "estimated against the index" is not), and that residual is
  accepted — the method statements a reader acts on are now rendered, so a slippery
  commentary can mislead only about emphasis, not about what was run.
* Old runs keep version 1 of the definition and re-render unchanged; the version pin on
  `report_sections` is what makes the contract change safe to publish.
