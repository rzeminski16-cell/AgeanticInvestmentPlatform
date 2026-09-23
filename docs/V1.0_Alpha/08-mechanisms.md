# The four mechanisms

*The algorithms the feature specifications described in principle and left to a developer to
invent. Four of them, each specified to the point where two people would build the same thing.*

| § | Mechanism | Feature | The question it answers |
|---|---|---|---|
| 1 | The refresh | F4 | What counts as a figure having *moved*, and what gets re-drafted |
| 2 | Ask's tier resolution | F6 | How a question is classified, and what catches a wrong classification |
| 3 | The monitor's metric resolution | F11 | Which premises can be tested, and what a restatement does |
| 4 | The scheduler | F15 | What runs, in what order, and what happens when it does not |

---

# 1 · The refresh

## 1.1 The shape

```
 acquire what is new  →  recompute everything  →  diff  →  re-draft what moved  →  summarise
     (cheap, slow)        (free, deterministic)          (the only model spend)
```

The economics come from step four. Steps one to three are nearly free; drafting is where £7
went, so a refresh that re-drafts three sections of eighteen costs a fraction of a run.

## 1.2 Step 1 — Acquire what is new

Fetch only documents the record does not hold: filings with an accession not already stored,
exhibits inside them, and price rows since the last close held.

**If nothing is new, stop here.** Emit a refresh with no changes, a summary that says so, and no
model spend at all. A quarterly cadence on a quiet company should cost nothing, and today it
would cost £7.

## 1.3 Step 2 — Recompute everything

Every calculation, not only the affected ones. They are deterministic and cost nothing, and
recomputing the lot is what guarantees the new report cannot contradict itself — which is the
failure the audit found repeatedly.

## 1.4 Step 3 — The diff, and what "moved" means

For every fact and calculation present in both runs, keyed on `(name, period, case)`:

```
Δ = (new − prior) / |prior|          where prior ≠ 0
```

A row is **material** if any of these holds:

| Test | Threshold | Why |
|---|---|---|
| Relative change | \|Δ\| ≥ **2%** | The audit's own matcher treats >2% as a contradiction rather than a rounding difference |
| Absolute change, for a figure that anchors the report | any change | Revenue, net income, operating cash flow, total debt, share count, value per share |
| A sign change | any | −3% to +1% growth is a different story at any magnitude |
| Crossing a premise threshold | any | The figure a premise tests moved across it. **Always material**, however small |
| Appearance or disappearance | — | A figure that now exists, or no longer does |
| `prior = 0` and `new ≠ 0` | — | Relative change is undefined; treat as material |

**A section is re-drafted if** any fact or calculation in its own evidence pack is material, or
any premise it discusses crossed a threshold, or its prior text cites a document now superseded
by an amended filing. Otherwise its prose is carried forward **verbatim**, and its footnotes are
re-verified against the artefacts anyway — carrying prose forward must never carry a citation
forward unchecked.

**Expect three to six sections of eighteen** on an ordinary quarterly refresh. If a refresh
re-drafts more than twelve, it is not a refresh and should be reported as a full run with its
full price.

## 1.5 Step 4 — The change summary

Composed from `report_changes` (data model §Gap 2) by deterministic code, in this order:

1. **What broke.** Premises whose predicate changed verdict. Named first because they are the
   only rows that demand a decision.
2. **What moved materially.** Figures over threshold, largest relative change first, each with
   prior, new, and the percentage.
3. **What is new.** Filings read for the first time, and what they contained.
4. **What did not move.** One line: *"Eleven other figures are within 2% of the prior report."*
   The absence of change is information and is the most common outcome.

The model writes prose around these rows. It never selects them, never orders them and never
judges materiality.

## 1.6 Failure

| Failure | Behaviour |
|---|---|
| Acquisition fails partway | The refresh **aborts and changes nothing**. The prior report stays current. A refresh is all-or-nothing: a half-refreshed report is a document nobody can reason about |
| A section fails to re-draft | The refresh completes; that section carries forward its prior text **marked stale** with the date of the text and the reason, and the final gate reports it |
| The diff finds a figure the prior run did not have | Not a failure. It is an appearance, and it is material |
| Cost exceeds the refresh ceiling | Stop at the ceiling, keep what is drafted, report a partial refresh. Never silently become a full run |

---

# 2 · Ask's tier resolution

## 2.1 The rule

**Resolution is deterministic first and model-judged only where it must be.** A classifier that
guesses is a classifier that will eventually answer a tier-3 question from stale tier-2 material
and not say so.

## 2.2 The order of attempts

```
1  Can a stored model answer it?          → TIER 1
2  Is it about material already held?     → TIER 2
3  Otherwise                              → TIER 3
```

**Step 1 — tier 1 is a match, not a judgement.** The question is matched against the run's
**registered recomputable operations**: the scenario engine's inputs (discount rate, growth,
margin, terminal method, exit multiple), the sensitivity grid's axes, and the margin bridge's
decomposition. A tier-1 answer is only possible when the question resolves to *an operation and a
parameter change*. If the parameter cannot be extracted with a value and a unit, it is not tier 1.

**Step 2 — tier 2 is a scope question.** The question is tier 2 when everything it names is in
the company's stored documents. A model proposes the entities the question refers to; **code
checks whether the record holds them.** The model's opinion is a proposal; the store's contents
are the answer. If any named entity is absent — another company, a period not held, a document
type never fetched — it is not tier 2.

**Step 3 — everything else is tier 3**, which is the safe default because it is the only tier
that asks permission.

> **Corrected 23 September 2026, on building it** (ADR 0130 §2). Step 2 as written has a model
> propose the entities before the tier is known, and that call is metered — so a tier-3
> question would spend before the approval the tier exists to demand, and §2.5's *nothing is
> spent before approval* could not be kept to the letter. As built, the extraction is
> deterministic: `aer.core.ask.resolve` reads the question's years, quarters, document kinds,
> temporal references and capitalised names against what the record holds — the subject's
> names, the held documents' titles, the fiscal and publication years, and the capitalised
> vocabulary of the held titles and excerpts. The model's opinion arrives one call later,
> inside the tier-2 pass, as a field naming what the question needed that the material did
> not hold; it can only push a question upward, and it is one of the checks in §2.4 that
> discard the answer. The store's contents are still the answer; the model's proposal is still
> only a proposal.

## 2.3 The asymmetry, stated as a rule

**Resolution may err upward and never downward.** A tier-2 question answered at tier 3 wastes
money and asks first. A tier-3 question answered at tier 2 produces a confident answer from
material that does not contain the answer, and says nothing about the gap. So:

- Ambiguous → the higher tier.
- The model's confidence in its entity extraction is below threshold → the higher tier.
- The question contains a temporal reference the store cannot satisfy (*"since the last
  results"*, when the last results are not held) → tier 3.

## 2.4 Catching a wrong resolution

**After** a tier-2 answer is produced, a deterministic check runs: did the answer cite at least
one extraction from the stored documents? A tier-2 answer with **no citations** is a
mis-resolution — the material did not contain the answer — and it is discarded, not shown. The
operator sees: *"That is not in this record. Researching it costs about £X."*

That check is cheap, it runs always, and it is the whole safety net for §2.3's one-directional
error.

> **As built, 23 September 2026** (ADR 0130 §4): three checks rather than one, all in code. A
> citation naming anything outside the dealt pack is dropped, and an answer left with none is
> discarded. An answer whose reader says the material does not answer the question, or names
> something it needed and did not hold, is discarded. A numeral in the prose that no dealt
> figure or cited excerpt reads as is a figure of the model's own, and the answer is discarded.
> The pass's cost is recorded against the question either way, and the tier-3 price follows.

## 2.5 Tier 3, end to end

1. Resolve, and estimate: what will be fetched, how many documents, roughly what it costs.
2. **Show the estimate and stop.** Nothing is spent before approval.
3. On approval: acquire through the normal fetch path — hashed, tiered, policy-checked, stored
   against the company.
4. Answer, citing what it fetched.
5. Record the added documents on the question row.

**If tier 3 fetches nothing useful**, the honest outcome is an answer that says so, the documents
are still added to the record (they were read, and the next question benefits), and the cost is
reported. It is not a failure and must not be presented as one.

---

# 3 · The monitor's metric resolution

## 3.1 What a premise can name

A premise's `metric` must resolve to something the platform computes. The resolvable set is
**the calculation registry plus the canonical concept map** — not free text, and not a name the
operator invents. The thesis editor offers the resolvable set as a select, which is why F9 says a
metric that cannot be resolved must never be offered.

## 3.2 Resolution, per check

```
metric name  →  registry lookup  →  latest observation in the window  →  compare  →  verdict
```

**The window** is `(premise.last_read_at, now]`, and the observation must come from a filing
**filed** inside it. Filed, not *for a period* inside it — a filing in September reporting a June
year-end is news in September, which is when the operator learns it.

**The comparison** applies `comparator` and `threshold` with `unit` checked. A unit mismatch
raises rather than coercing; it is the same rule as everywhere else and it matters more here,
because a silently coerced threshold produces a confident wrong verdict about somebody's beliefs.

## 3.3 When a metric cannot be resolved for this filer

Four cases, and they are different:

| Case | Finding | What the operator sees |
|---|---|---|
| The filer does not report it (a bank has no inventory days) | `kind = stopped` | *"This premise cannot be tested for this filer: it reports no {concept}. Give it a review date instead."* |
| The concept is unmapped for this filer | `kind = stopped`, `opens_gate = true` | It joins the unmapped queue (F/Knowledge); mapping it makes the premise testable |
| Nothing has been filed in the window | **no finding at all** | Silence. A premise nothing bears on is not news, and reporting "still true" every month is how a monitor becomes noise |
| The metric resolves but the value is null | `kind = stopped` | The filing omitted it; the premise is untested this cycle and says so |

**Only the third case is silent**, and it is by far the most common.

## 3.4 Restatements and amended filings

The hard case, and the one a naive implementation gets wrong.

- **An amended filing (10-K/A) supersedes the original for the same period.** The monitor reads
  the latest-filed observation for a period, which the point-in-time module already does
  correctly for facts.
- **A restatement that changes a prior period does not retroactively break a premise.** The
  premise was tested against what was filed at the time; the record of that test stands. The
  restatement produces a **new** finding, in the window it was filed in, and the operator is told
  the figure they were relying on has been restated. That is a different and more alarming piece
  of news than a premise breaking, and it should read differently.
- **A premise that broke on a figure later restated back above its threshold** is not silently
  un-broken. The operator revised or withdrew it in between, and that decision is theirs. The
  monitor emits a finding saying the underlying figure has moved back.

## 3.5 The price half

Independent of premises, and simpler:

```
move = (close_today − close_(today − window)) / close_(today − window)
alert if |move| ≥ threshold
```

`window` defaults to seven calendar days, `threshold` to the account default. **Suppression:** at
most one price alert per company per window — a stock that stays down 12% does not alert daily —
and an alert is suppressed entirely if one on the same company was dismissed within the window.

Every price alert carries, computed at the same moment: whether anything has been filed since the
last check, the current verdict of every premise, and the sector's move over the same window.
**A price finding with those three fields empty is not shipped.**

---

# 4 · The scheduler

## 4.1 What it is

One daily job. Not a queue — the platform has a queue, and a queue is a different thing: it runs
work somebody asked for, when they asked. This runs work nobody asked for, on time.

## 4.2 The daily pass, in order

```
1  Prices        read the close for every watched security
2  Valuation     recompute the book at that close
3  Price alerts  evaluate thresholds; emit findings
4  Premise checks for every watchlist entry whose next_check_at has passed
5  Staleness     mark reports past their cadence
6  Queue         write everything into Today's queue
```

**The order matters.** Valuation before alerts, because an alert quotes the book's exposure.
Premise checks after prices, because a premise may name a market figure.

## 4.3 The properties it must have

**Idempotent.** Running the pass twice for the same date produces the same findings and no
duplicates. Enforced by a unique key on `(security_id, kind, window_to)` for price findings and
on `(premise_id, window_to)` for readings, not by hoping it runs once.

**Catch-up, bounded.** The machine is a laptop and will be off. On starting after a gap:

- **Prices and valuation**: fetch the missing closes, value the book at each, and carry on. Cheap.
- **Price alerts**: evaluate the *current* window only. Do not emit four days of alerts for a
  move the operator can now see in one. Emit one finding covering the whole gap and say so.
- **Premise checks**: run every entry now due, oldest first.
- **A gap longer than thirty days** produces one banner rather than a queue of alerts: *"The
  monitor did not run between {a} and {b}. Here is what changed over the whole period."*

**Never spends.** The daily pass reads prices and does arithmetic. Refreshes cost money and are
*proposed* by staleness rather than started by it — a scheduler that commissions paid work
unattended is a scheduler that will eventually empty a budget overnight.

## 4.4 Failure

| Failure | Behaviour |
|---|---|
| A price source is unavailable | Valuation carries yesterday's close, **marked stale on every surface showing it**. Retry next pass. Three consecutive failures raise a health item |
| One company's check fails | The pass continues. That entry's `last_checked_at` is not advanced, so the next pass retries it. One bad filer never stops the others |
| The whole pass fails | It is visible on the health page as a missed run with its error. **Silence is the failure mode to design against**: a monitor nobody can tell has stopped is worse than no monitor |
| The pass is still running when the next fires | The second exits immediately. One at a time, by a lock, like runs |

## 4.5 What is explicitly not scheduled

Research runs. Refreshes. Anything that spends money or writes a document. Those are proposed on
Today and started by a person — which is the same principle as the gates, applied to time.
