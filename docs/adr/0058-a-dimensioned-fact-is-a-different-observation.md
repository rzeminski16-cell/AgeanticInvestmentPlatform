# 0058 — A dimensioned fact is a different observation, and never competes with the aggregate

Date: 2026-08-17
Status: accepted

## Context

The live AAPL report's segment-mix exhibit rendered its placeholder, and the cause ran
through three layers. The companyfacts aggregate — the run's structured-fact source —
carries only consolidated figures. The `financial_facts` observation key
(`company, concept, unit, period_end, fiscal_period, basis, filed_date`) could not hold a
breakdown even if one arrived: two segments' revenue for the same year are the same
observation to that key, so the second row can never be stored. And nothing read the one
document that states the breakdown — the annual report itself, which on EDGAR is inline
XBRL and tags every segment's revenue with the axis and member that name it
(`us-gaap:StatementBusinessSegmentsAxis` = `aapl:AmericasSegmentMember`, and so on).

The platform already had an offline iXBRL extractor (`aer.extract.ixbrl`, built for UK
filings, ADR 0021's offline control). It read each fact's value, unit, period and entity —
and dropped the dimensions, which made a segment's revenue indistinguishable from the
company's. That is not a neutral loss: had a dimensioned fact ever entered the store, the
statement assembler's winner-selection would happily have let one segment's slice win a
period from the consolidated line, and every ratio downstream would have divided a
fraction of the company by the whole of it.

## Decision

**The dimension is part of a fact's identity.** `RawFact` and `financial_facts` carry
`dimension_axis` and `dimension_member` (both or neither, checked in the schema and the
database), and both the observation uniqueness index and `RawFact.period_key` include
them. Two segments are two observations; a segment and the aggregate are two
observations; the consolidated rows — both columns NULL — deduplicate exactly as before.

**Dimensioned facts are excluded wherever a single value per concept-period is assumed.**
Statement assembly, the worker-visible fact search, section evidence packs, the red
team's fact listing, the revenue-history chart and the whole-history growth rate all
filter on `dimension_axis IS NULL`. The rule is stated once and applied at each reader:
a segment must never win a period from the aggregate, and a row whose payload does not
say "this is one segment's slice" must never reach a surface that would present it as
the company's line. The cross-section consistency check groups by dimension for the same
reason — two segments' revenue for one span are two numbers, not a contradiction.

**The segment sweep reads the annual report only, and its unmapped tags do not raise the
confirmation gate.** The extract step runs the iXBRL extractor over the annual report the
acquire step already fetched and hashed, and persists the single-axis dimensioned facts
whose tags map to canonical concepts. The UK_FINANCIALS gate exists for the case where
the statement lines themselves hang on a filer extension; here the statements came from
the aggregate, and the sweep is supplementary — a breakdown tagged with an invented
element is counted in the step output (`segment_unmapped_tags`) rather than stopping the
run to ask a person about an exhibit it can do without. Multi-axis cells (segment by
geography) are skipped: a row stating one axis of a two-axis cell would misstate what the
number measures.

**The segment exhibit draws stored values, not derived shares.** `SegmentRevenue` carries
the fact's value; the builder scales for the axis exactly as the revenue history does.
A percentage would be arithmetic no calculation row recorded, and invariant 3 does not
stop applying because the figure is a picture.

## Consequences

- The segment-mix chart renders from rows, each bar citing the filing it came from, and
  the axis drawn is chosen by a stated preference order (reportable segments, then
  product, then geography) rather than by whatever the filer tagged most of.
- Migration 0033 rebuilds `uq_financial_facts_observation` with the dimension columns.
  Downgrading deletes dimensioned rows — they are unrepresentable under the narrower key.
- A future consumer that *wants* segment rows (a segment-analysis section, interim
  segment trends) selects them explicitly by axis; nothing reaches them by accident.
- The UK path inherits the fix: a dimensioned fact in a UK filing now carries its
  dimensions instead of masquerading as a statement line, and is skipped when a context
  cannot be stated faithfully (typed dimensions), rather than stored wrongly.
