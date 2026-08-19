# 0062 — A fiscal year is a property of the period, not of the filing

Date: 2026-08-19
Status: accepted

Task P3 of `docs/polish-phase-1.md`.

## Context

The first complete run's historical financial analysis presented a ratio set "recorded
against FY2022". All twelve values were the subject's actual **FY2021**, to the decimal:
gross margin 42.0%, operating 5.29%, net 7.10%, return on equity 24.1%, return on assets
7.9%, current ratio 1.14, and the rest. Nothing was miscalculated. Every figure was real,
sourced and verifiable — under the wrong year.

The mechanism is one line:

```
src/aer/sources/sec/companyfacts.py — fiscal_year=_parse_int(entry.get("fy"))
```

In SEC companyfacts, `fy` and `fp` describe the fiscal frame of the **filing the
observation appeared in**, not of the period the observation covers. A company's FY2021
figures appear as comparatives inside the FY2022 annual report, so they arrive tagged
`fy: 2022`. Every comparative figure the platform stored was labelled a year late —
silently, and consistently enough to look right. The test fixture carries the defect in
miniature: `companyfacts_msft.json` holds the period `2019-07-01 → 2020-06-30` twice, once
tagged `fy: 2020` from its own 10-K and once tagged `fy: 2022` from the later one.

The mislabelling also manufactured a plausible-looking anomaly. The red team challenged a
net margin (7.1%) above an operating margin (5.3%) as suspicious; it was real for the
subject's actual FY2021, on a one-off below-the-line gain. Under the correct label the
figure would have explained itself.

**The correct rule already existed in one adapter.** The inline-XBRL path
(`aer/services/segments.py`) derives the year from the period and says so: *"The
convention the rest of the store already uses: a year ending September 2025 is FY2025."*
The companyfacts adapter never adopted it — the same shape as ADR 0061, a rule derived
once and left local.

## Decision

**A fiscal-year row's year is derived from its own period end.**
`aer.core.dates.fiscal_year_of(period_end)` is the one implementation, and both adapters
call it:

- the calendar year in which the period ends, **except**
- a period ending in the first seven days of January belongs to the prior year.

The exception is the 52/53-week calendar: a retail year ending the Saturday nearest
31 December can land on 2 January, and a year that is in substance 2026 must not flip to
FY2027 over two days. Seven days covers every nearest-to-year-end convention without
reaching the genuine mid-January year ends, which stay in their own calendar year — a
31 January year end is FY-of-that-January by the platform's convention, which matches
those filers' own naming.

This is a **labelling convention, applied uniformly**, not a promise to reproduce each
filer's marketing label. A filer that calls its February-2025 year end "fiscal 2024" will
read FY2025 here — internally consistent across every figure of that company, which is
what comparability needs. The defect being fixed was a *shift within* the convention, not
a disagreement about the convention.

**The rule applies to fiscal-year rows (`fp == "FY"`), durations and instants alike.**
For those, the period alone answers the question.

**Interim rows keep the filing's frame, and that boundary is deliberate.** Which fiscal
year a Q2 belongs to depends on the company's fiscal calendar — a quarter ending
31 December is Q2 of FY-next-June for a June filer and Q4 of FY-that-December for a
calendar one — and the parser is pure, per-document, and does not hold that calendar. The
filing's own frame is correct for the filing's current period, which is the only interim
row the front page anchors on; an interim *comparative* can still carry a stale frame,
and that residual is accepted and stated here rather than hidden. It does not touch the
ratio suite, the annual series or the history sections, which all select by actual
year-long spans.

**The filing's raw `fy` is not stored in a second column.** It remains available, exactly,
in the hashed artefact every fact already traces to; a provenance column duplicating a
value nothing consumes would be schema for its own sake.

**Existing rows are corrected in place** — migration 0043 recomputes
`financial_fact.fiscal_year` for `fiscal_period = 'FY'` rows from `period_end` in SQL,
using the same rule. Idempotent by construction: the new value is a pure function of a
column the statement does not change. Re-parsing the artefacts was considered and
rejected — it would miss rows whose artefacts have been pruned, and a one-pass data
migration is the honest fix for data that is wrong.

## Consequences

- The live run's failure shape cannot recur: a comparative annual row now carries its own
  year, so one company's history lines up under the years it happened in.
- The observation-key dedupe stops storing the same observation twice under two labels.
  The fixture's duplicated 2020 period — once `fy: 2020`, once `fy: 2022` — now parses to
  the same fiscal year both times.
- `segments.py` adopts the shared helper and gains the early-January rule it lacked.
- Consumers needed no change: `analysis` labels periods from this field, `glance` keys its
  annual series on it, `consistency` and `exhibits` print it — all were correct already
  and were being fed wrong data.
- A known residual, restated: interim comparatives can carry a stale quarter label. Fixing
  that needs the company's fiscal calendar at parse time, which is a different change with
  its own trade-offs, taken only if a live run shows it mattering.
