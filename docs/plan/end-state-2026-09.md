# The end state — what the whole system becomes

*Agreed with the operator on 14 September 2026, from the notes discussion that followed the
readiness audit and the [remediation plan](remediation-2026-09.md). This is the high-level
shape: the decisions taken, what each one changes, and what needs an ADR. It is deliberately
not a work plan — the concrete sequencing comes after the experience design in
[`../experience/`](../experience/README.md).*

---

## 1. The loop

The product is not a report generator. It is a loop, and each stage writes a record the next
stage reads — which is the thing a chat session structurally cannot do.

```
   RESEARCH  ──►  DECIDE  ──►  HOLD  ──►  REVIEW
       ▲                                     │
       └─────────────────────────────────────┘
```

| Stage | What the user does | What the system keeps |
|---|---|---|
| **Research** | Has an idea, commissions a report, reads it | A cited, immutable document and every fact and calculation beneath it |
| **Decide** | Forms a view, writes it down, acts or declines | A thesis made of premises, and a decision with its reasons and intended size |
| **Hold** | Owns the position, watches it | Holdings valued daily, and a monitor testing the premises against new filings |
| **Review** | Closes the position, learns from it | Whether the thesis was right, whether the decision was good, and what that says across many decisions |

**A decision not to act is a first-class decision.** Researching a company and declining it,
with reasons, is recorded the same way as buying it. When the price halves, *"you passed at
$80 because Y, and Y is now false"* is the most valuable thing the system can tell anybody.

**One thesis outlives many decisions.** Open, add, trim, exit, re-enter — the belief persists
across the trades that express it, so the model is thesis → many decisions → many transactions.

## 2. Decisions taken

### 2.1 Point-in-time is removed

Every enforcement of an as-of date, the point-in-time mode, the `temporal_compliance` and
`look_ahead_recall` metrics, the gate that reads them, and every mention in the documentation
except the record of this removal. It touches **189 files**.

*Why.* The as-of date is always "now" for somebody deciding whether to buy today, and the
audit measured the machinery as never having fired: `look_ahead_recall` read "not exercised"
on all five runs, because nothing published after the as-of date was ever offered to a claim.

*The one carve-out.* **Every artefact keeps its retrieval timestamp.** That is provenance, not
point-in-time, it already exists, and the review stage needs it: *"was this a good decision or
a lucky one"* cannot be answered without knowing what was in front of the operator at the time.

*Needs an ADR* superseding invariant 4 in `CLAUDE.md` and ADRs 0110 and 0111.

### 2.2 The adversary argues the opposite case

Today the red team re-checks sources and arithmetic — work deterministic code already does
better, at a fraction of the cost. The model's advantage is argument, and that is what it
will be asked for.

**If the report concludes long, the adversary argues short. If it concludes short, it argues
long.** It is told explicitly that the figures are already verified and that auditing
arithmetic is not its job.

Two dependencies: it needs a stated view to attack, and its surviving argument must be
*resolved* before publication and printed as a real bear or bull case — today every challenge
prints "Escalated for human decision" whatever became of it.

*Needs an ADR* amending ADR 0095.

### 2.3 The recommendation section reads the operator's own book

The report's closing section takes the current portfolio and the request form's own fields —
planned weight, horizon, purpose — and says what this position would do to the book that
already exists.

**It computes consequences; it does not issue instructions.**

> At your planned 5% weight this takes your top-five concentration from 41% to 44% and your
> energy exposure from 3% to 8%. Your stated horizon is three years; this discounted cash
> flow's payback lands in year six.

That is arithmetic over the operator's own holdings — code's side of the line — and it is more
useful than a rating. **It is also the reason this stays information rather than a personal
recommendation**, which matters the moment the product has users who are not its author:
advising a specific person on a specific investment is a regulated activity in the United
Kingdom, and that is a question for a solicitor before launch rather than after.

*Consequence:* Portfolio becomes a dependency of Research, which reorders the build.

### 2.4 A refresh recomputes everything and re-drafts almost nothing

A monthly or quarterly update re-acquires only what is new, **recomputes every calculation**
— they are deterministic and nearly free, and recomputing all of them is what guarantees the
document cannot contradict itself — and re-drafts only the sections whose inputs actually
moved.

The headline deliverable is the **change summary**. The previous report is archived, immutable
and marked superseded. Target cost is £1–2 against £7 for a full run, which is the economics a
subscription needs.

### 2.5 Every report ships with a live model workbook

A spreadsheet carrying the discounted cash flow (and the comps, scenarios and sensitivity
grid) **as real formulas rather than values**, so changing a growth rate recomputes the
valuation in the sheet. Input cells and computed cells are distinguished by the ordinary
modelling convention. A provenance tab lists every input with its source, and stamps the run
identifier, the code version and the hash of the report it came from — so the audit trail
leaves the building with the file.

No spreadsheet library exists in the project today, but the calculation registry already
stores formula, inputs, units and sources, so generating it is mechanical.

### 2.6 Breadth comes from depth in free primary sources

The console's breadth advantage came from its *weakest* sourcing — the judges caught it using
a blog and an aggregator for load-bearing balance-sheet figures. The answer is not to licence
market commentary. It is to read further into primary material that is free:

- **Earnings-call transcripts and prepared remarks**, frequently furnished as EX-99 exhibits
  on an 8-K. This is where guidance and management commentary live.
- **The narrative already fetched and barely read** — management's discussion, risk factors,
  the competition section, legal proceedings.
- **Investor-relations material**, through the issuer adapter that exists and is wired to
  nothing.
- **Competitors' filings describing the subject.** Rivals characterise each other and the
  market. Free, primary, and no chat does it systematically.
- **Regulatory primaries** — the FDA's Orange Book would have answered the patent-expiry
  question that beat us on AstraZeneca, for nothing.
- **13D and 13G filings** for activist positions.

**Depth in primary sources, not breadth in secondary commentary.** It is cheaper, better
sourced, and it is a position a chat cannot copy without building the acquisition layer.

### 2.7 Everything the audit found is addressed

All five rows of the readiness audit's shortfall — no stated view, withheld figures, the bank,
narrow coverage, single-user — are in the remediation plan and remain in scope.

Of the four dimensions the console currently wins: **argument and breadth are closed** by 2.2,
2.3 and 2.6. **Speed and open-ended flexibility are not chased**, though the grounded
follow-up layer closes most of the flexibility gap cheaply and is kept for that reason.

### 2.8 Four further ideas adopted

| Idea | Why it was taken |
|---|---|
| **Model portability** | The arithmetic is in Python, so the language model is swappable. A hedge against a supplier's price or policy, and margin that improves as models commoditise |
| **Breadth by filing type** | The same strategy as 2.6, and the clearest expression of what the product is: it researches *deeper*, not wider |
| **A methodology library** | User-written sections are additive-only and attack-tested, so a shared library of methods is a network effect the safety model already permits |
| **Fixed-price research** | Held for public launch. Cost is bounded in code rather than estimated, so a flat price can be offered with confidence a metered competitor cannot match |

## 3. Operating notes

- **Daily end-of-day portfolio valuation needs no standing budget.** The price subscription's
  ceiling is high and permits several reads a day; the daily update runs inside it.
- **The monitor does need scheduling.** Monthly and quarterly checks are a scheduled job
  rather than a queued run, which the platform does not have today.
- **One run at a time** remains true by design, so a measurement round or a batch refresh is
  serial in machine time and in the operator's attention.

## 4. What this changes elsewhere

| Document | Change |
|---|---|
| `CLAUDE.md` | Invariant 4 (point-in-time) removed; the invariant list renumbered with an ADR recording why |
| ADRs 0110, 0111 | Superseded by the point-in-time removal |
| ADR 0095 | Amended: an escalated challenge is resolved before publication |
| ADR 0059 | Revisited if peer acquisition is taken |
| `ROADMAP.md` | The seven planned tools gain their real definitions from the experience design; §2 and §3 gain the items above |
| `docs/product/what-it-is.md` | Rewritten once the loop, rather than the report, is the product |

## 5. Still open

1. **Whether the recommendation section ships to non-author users at all**, and on what legal
   footing. Recommended: portfolio consequences only, and take advice before launch.
2. **What enters the watchlist automatically** — researched-not-owned, held, and
   closed-but-watching is the proposal.
3. **How a thesis is revised rather than replaced** when a premise is withdrawn and another
   added. The tables already refuse to delete a premise, which is the right instinct; the
   surface has to make the revision legible.
4. **Whether the grounded follow-up layer is a chat surface or a structured one** — the
   design work in `../experience/` answers this.
