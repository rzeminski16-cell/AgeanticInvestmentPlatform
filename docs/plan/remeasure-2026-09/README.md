# The re-measurement — 25 September 2026, as it happened

This is the second of the operator's four finish-line conditions (ROADMAP, the decisions of 25
September): *three fresh runs reach an approved report with no terminal and no rescue — a US
filer, AZN through its 20-F, and M&T — and a refresh keeps its valuation.* It is also the
first live test of F2 (ADR 0135): the report argues the case for and the case against, and
takes neither side.

The runs were driven by the audit driver under the gate policy the verdict round used. The
worker was started by the driver, and nothing was typed at a terminal on the run's behalf.
**A rescue** is the pre-registration's: a gate approved against its own failed checks, a
stranded-run resume, or a reseal. None happened.

**Append, never rewrite.** What is here is what the runs did, including the one that stopped.

## The result

| | MSFT | AZN (first) | AZN | M&T | MSFT refresh |
|---|---|---|---|---|---|
| folder | [`v1-msft`](v1-msft/) | [`v1-azn`](v1-azn/) | [`v1-azn-2`](v1-azn-2/) | [`v1-mtb`](v1-mtb/) | [`v1-msft-refresh`](v1-msft-refresh/) |
| tree | `6014de8` | `0e80d25` | `3e0ecb0` | `3e0ecb0` | `3e0ecb0` |
| spend | £5.64 | £6.70 | £6.98 | £7.46 | £1.37 |
| wall clock | 30 min | 29 min | 35 min | 42 min | 17 min |
| sections | 17 of 18 | 18 of 18 | 18 of 18 | 18 of 18 | 19 of 19 |
| failing check | none | `cited_figure_agreement` | none | none | none |
| resumes, overrides, reseals | none | — | none | none | none |
| citations verified on replay | 42 | 48 | 70 | 44 | 42 |
| calculations replayed | 1,226 | 1,575 | 1,509 | 888 | 1,190 |
| challenges left open for the reader (material) | 7 (2) | — | 9 (4) | 7 (3) | 7 (6) |
| outcome | **approved, no rescue** | stopped at the final gate; rejected | **approved, no rescue** | **approved, no rescue** | **approved, no rescue; valuation kept** |

**The condition is met.** MSFT, AZN through its 20-F and M&T each reached an approved,
immutable report with no rescue. The MSFT refresh kept the valuation it refreshed, to the
cent: $222.34 by perpetuity growth and $411.23 by the exit multiple, the same as the report it
refreshes. That is the verdict round's item 73 closed on a live run.

**It was not met first time.** The first AZN run stopped on a defect in the checker, not in
the draft (below). The fix is item 80, and a fresh AZN run on the fixed tree is the
measurement. The stopped run was rejected at its final gate with its reason recorded, and it
stays in the record. The measurement cost **£28.15**, against the ~£21 approved, because of
the second AZN run. The audit ledger stands at £106.58 of its £125 ceiling.

## What the runs found, and what was fixed the same day

Three defects, all in deterministic code, each fixed with tests before the next run used the
tree:

1. **A year range joined by "through" cost MSFT a section** (ROADMAP §3.19 item 78, ADR 0054
   amended). Earnings Quality was lost to *"tax years 2004 through 2013"*. The year eraser
   took "through 2013" as a year in temporal company and left the 2004 naked. The refresh,
   drafted on the fixed tree, wrote the section.
2. **A lever argued against its own case** (item 79, ADR 0135 amended). MSFT's case against
   named depreciation at its heaviest share of revenue. The strike beside it raised both
   values, to $253.99 and $439.64, because the model holds the operating margin and adds
   depreciation back. Code now strikes every lever when the list is built and tells the
   writer which way each one moves the value. The check refuses a lever that argues the other
   case. On the refresh, the carried point stood in words, and AZN's four levers and the
   refresh's four all moved the value the way their case argued.
3. **A sign in front of a currency's code was dropped** (item 80). The first AZN run's draft
   wrote *"a working capital change of negative USD 386.6 million"* of the stored
   -386,552,205.83, which is right. The scanner read a sign through "$" but not through
   "USD", reported a dropped sign that was never dropped, and stopped the run. It is the same
   stored figure the verdict round's AZN was stopped on.

## F2, as the first real reports show it

- **Every report takes no side.** The masthead says *"Non-binding view: none — this report
  takes no side"*, and no section states a view on the shares.
- **The two cases are balanced by construction**, to within one point:
  - MSFT 3 and 3;
  - AZN 3 and 4;
  - M&T 4 and 4, in words only: a bank's residual income has no discounted cash flow to move
    an input of, and the list says so.
- **Every priced figure is code's.** Each lever's value is an observation already on the
  record. Each struck value per share is recorded under the case `argued`, and none of them
  displaced a base-case row from the evidence index (item 77).
- **The question each pair turns on** is the one a reader would ask:
  - MSFT: whether AI-attributable revenue compounds faster than the depreciation and lease
    costs the build creates;
  - M&T: whether its excess return over the cost of equity justifies a premium to book, and
    whether the book it is measured against is a reported one;
  - AZN: whether the growth record can be spent twice.

## The decision this was to inform

ADR 0135 left one half of ADR 0115's decision 4 for the re-measurement to settle: **refuse
approval while a red-team challenge is open**, with a *carried* state for one the operator
defers. The runs say what building it would cost. Every one of the four approved reports left
seven to nine challenges open, two to six of them material. The report publishes each with
both sides and *"Open at approval: no rule settled this and nobody preferred either side, so
both are published here and the reader decides."* A report that takes no side has no thesis
for an open challenge to break. Refusing approval on an open challenge would have stopped all
four runs and turned the reader's decision into a gate.

**Recommendation: withdraw that half.** It is the operator's call, and it is left open here
until they make it.

## Found and not fixed

Recorded for the operator rather than changed mid-measurement:

- **The Executive Summary carries an *"Insufficient evidence"* banner** on three of the four
  reports, as the verdict round's MSFT did. The summary cites calculations, whose lineage
  ends in the filings, and the section's policy counts only cited documents as primary. The
  top of the most-read section says the evidence is weak when it is not (item 81).
- **A value per share that rounds to a whole number of cents prints without them**: "$308"
  beside "$431.55" in MSFT's priced table. A percentage does the same, "12%" beside "46.8%"
  (item 82).
- **The comparison with prior research says the view was "none stated"** where the masthead
  says the report takes no side (item 83).
