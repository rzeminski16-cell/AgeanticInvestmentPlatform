# Phase 7 — the verdict round, as it happened

The round the [pre-registration](../phase-7-pre-registration.json) fixes: an AZN run and an
MSFT run on day one, the fresh console baseline, the MSFT report refreshed the next UTC day,
then the panel. Commissioned on 24 September 2026 after all nine rows of
[the gate](../phase-7-gate.md) stood, on `24e4c3b` — the gate's record committed on top of
`d0f815e`, whose code it is.

**Append, never rewrite**, on the rule the gate notes and Phase 5's round set. What is here is
what the round did, including the parts that went wrong.

---

## Day one — 24 September 2026

### What the two runs cost and produced

| | AZN | MSFT |
|---|---|---|
| job | `42653530` | `10b6e9b5` |
| spend | **£6.17** | **£5.88** |
| model calls | 53 | 56 |
| output tokens | 223,832 | 226,377 |
| sections | 18 of 18 | 18 of 18 |
| claims (numeric) | 259 (172) | 288 (209) |
| citations verified | **47 of 47** | **47 of 47** |
| source documents | 16 | 19 |
| report | 17,065 words | 17,995 words |
| final gate | **refused on one check, approved by the pre-registered rule** | **passed on its own** |
| report | `c4664a10`, immutable | `363a6d18`, immutable |

Phase 5's two runs cost £7.61 and £7.06, but the spend is not comparable line for line: the
meter charged Sonnet 5 half as much again as the vendor does until the round's own Phase 7.0
found it (ROADMAP §3.19 item 68), so Phase 5's figures are overstated on every Sonnet line and
these are not.

### AZN's final gate refused it, on one check where Phase 5's refused three

| check | Phase 5 | this round |
|---|---|---|
| `cited_figure_agreement` | **1.00** (at most 0) | **1.00** (at most 0) |
| `presentation_integrity` | **3.00** (at most 0) | 0 ✓ |
| `source_coverage` | **0.8824** (at least 0.90) | 1.00 ✓ |
| every other blocking check | passed | passed |

The one failure, in the check's own words: *"Executive Summary, change in working capital
#701 cites `change in working capital` = -386552205.830311889804 USD and states 386.6"* — the
sign dropped between the calculation and the sentence, one figure of forty-two checked.

The pre-registration decided this case before it happened: the run is approved through the
gate's own decision, the driver resumed with `--override-final` and the measured scores as
the reason, which the approval row carries ahead of the policy's own reason for stopping. It
was, at 12:57 UTC. **The platform would not have published this document**, every comparison
it enters says so, and ISSUE 1 counts the run as rescued.

### MSFT passed its own final gate — the first measurement-round run that has

Eighteen of eighteen sections, **no blocking check failing**, the acceptance readout passed;
two triggers fired (`high_model_uncertainty`, `cost_above_threshold`) and four material
challenges were left unsettled for the reader, which is what the gate is for. Phase 5's MSFT
failed `presentation_integrity` on two unformatted integers.

**It did fail once on the way, on a defect.** At 13:09 the research worker asked for an EDGAR
accession that does not exist — `https://www.sec.gov/Archives/edgar/data/789019/000119312526000000/`,
a sequence number of zeros — and EDGAR answered 404 with an empty body. The empty body reached
the artefact store, whose check constraint refuses an empty artefact, and the database error
surfaced as `unexpected_error` and failed the whole run. The driver resumed it through the
product's own resume service, the one the console's control calls, and the step succeeded on
its second attempt.

That is two defects, and neither is the model's: **a 404 response went to the artefact store
at all**, and **an empty body crashes a run** instead of being refused at the fetch with its
own reason.
Recorded as found work; not fixed mid-round, because the round measures one tree.

For ISSUE 1: the pre-registration's rescues are a gate approved against its own failed checks,
a stranded-run resume and a reseal. A failed step retried through the product's control is
none of the three, so by the definition as written MSFT reached its report with no terminal
and no rescue. It is reported here beside that reading because an operator would have seen a
failed run and had to press *Try again*.

### AZN waited ten minutes behind 1,225 jobs nobody commissioned

The worker the driver started at 12:18 took AZN's job at 12:28, after it had waited 613
seconds behind 1,225 stale `run_research` jobs enqueued up to a day earlier: 981 the worker
discarded and 244 that had already expired. **The browser suite puts them there**: its live server is given
its own database, artefact root and signing key, but not a Redis URL, so every run a browser
test commissions is enqueued on the operator's real queue, where no worker takes it until the
next real one does. `tests/e2e/worker.py` begins *"There is no arq worker in these tests and
no queue"*. There is a queue; it is the operator's. No model was called and nothing was spent
on any of them, but the next real run waits behind all of them. Found work.

### What the assumptions did

The same as Phase 5, which is the point of fixing them: AZN's beta derived at **0.2767**,
outside the policy's 0.3–2.0 band, amended to 0.3 and recorded; its tax rate and equity risk
premium supplied from `audit/subjects.py`, identically to Phase 5. MSFT's ten assumptions all
derived but the equity risk premium, nothing amended.

### The fresh baseline cost twice its estimate

`claude-opus-5-5` at high effort, 25 searches, September's brief, 13:31–13:46 UTC:

| | September's MSFT note | the fresh note |
|---|---|---|
| model | `claude-opus-5` | `claude-opus-5-5` |
| input tokens | 1,539,357 | **3,614,631** |
| output tokens | 68,352 | 75,671 |
| searches | 25 | 25 |
| words | 12,967 | 6,709 |
| sources | bracketed in the text | 61 linked, listed in an appendix |
| cost | — | **£12.82** |
| stop | `end_turn` | `end_turn` |

**£12.82 against the £6.20 the pre-registration estimated**, because the estimate was built on
September's input and the fresh run read 2.35 times as much. The stop rule is applied before
each stage on its estimate, and the baseline was bought on £12.05 + £6.20; what it then cost
is recorded rather than explained away. It confirms the pre-registration's reason for not
buying Claude Fable 5.1: at this input a Fable note would have cost £31.74, more than the
whole round.

The note rates MSFT a **hold**, with a twelve-month target of $470 against a last price of
$500.59.

### Spend after day one

| | |
|---|---|
| AZN run | £6.17 |
| MSFT run | £5.88 |
| fresh baseline | £12.82 |
| **the round so far** | **£24.87**, against £31.50 |

What the stop rule does with the £6.63 left, in the pre-registered order: the refresh (£2.00),
the counted six comparisons (£2.10) and the three like-for-like MSFT comparisons (£1.05) fit,
at £30.02; **the identity guess (£2.50) does not, and is not bought** unless the stages before
it come in under their estimates by enough to make room. Phase 5's rate of 1.0 then carries to
all nine comparisons, as the pre-registration says it does.

The corpus was backed up straight after (`var/backup-2026-09-24-day1`: schema 0087, 1,202
artefacts, 253.6 MiB, verified).

---

## Day two — 25 September 2026

The container restarted three times between the two days and brought Postgres and Redis back
stopped each time; they were started again, day one's reports were confirmed intact, and
nothing was lost. The refresh was commissioned at 00:09 UTC on a freshly started machine,
through the report page's own service — its priced go-ahead said £1.69 — as job `062d95cd`.

### The refresh against F4's own test

| F4's test | measured | met |
|---|---|---|
| costs under £2 | **£1.66**, nine model calls | yes |
| completes in under ten minutes | **16m 05s** from the priced go-ahead to its final gate | **no** |
| a change summary naming every material move | the rows name all 41; the commentary written from them misdescribes them | the rows, yes; the prose, no |
| the prior report readable at its own address | day one's report is still current, because the refresh was not approved | yes |

### It lost the valuation it was refreshing — a defect, and not the model's

The refresh re-runs the price step, and the price step proposes beta from its regression by
writing the request's one beta row — assumptions are unique per request and name. On a full
run that is harmless, because the assumptions gate comes next. A refresh has no assumptions
gate. So the write set day one's **confirmed** beta back to unconfirmed, under the refresh's
job and a day-later value (1.066268 against the 1.065870 day one confirmed), and nothing
confirmed it again. The value step then said, correctly, *"The discount rate needs the beta,
and no confirmed assumption of that name exists on this request"*, and produced no DCF.

Everything downstream followed from that one write. The valuation chain — 31 calculations,
leaving 242 rows where day one had 962 — was not computed; the diff read each figure that
vanished as a material move, which is 40 of its 41; the draft step re-drafted the seven
sections that cited them; and the valuation section came back as a 294-character notice that
no DCF exists.

What it did not do: day one's run still replays exactly — 962 calculations, 47 citations, 19
artefacts, 56 model calls — its confirmed beta survives in the committed gate record, and
the other ten of its eleven confirmed assumptions are untouched. What it did do is change a confirmed row
belonging to another run's record without anybody deciding it, which is a second defect
beside the lost valuation.

### The final gate refused it, and it was not approved

The refusal was `presentation_integrity`, on the refresh's own sentence *"18829 other figures
are within two per cent of the prior report"* — a count that never went through the display
formatter.

The pre-registered rule approves a refused run through its gate *"so the round has its
documents"*, and excepts *"a draft that lost sections"*. This draft's valuation section is
generated in status and empty in substance, and the refresh's document is not one the panel
reads. Approving it would have replaced a report that passed its own gate with one that has
no valuation. It is left at its final gate for the operator: rejecting it ends the run through
the product (ADR 0123), and day one's report stays current either way. **Neither ISSUE 1's
reading nor F4's changes with the choice**, which is the only reason a choice made after the
result was acceptable here.

### The change summary: the right rows and the wrong words

The rows are code's and they are right: forty figures *"no longer computed; the prior run
held …"*, and one debt face amount for a period ending in April 2013. The commentary the model
wrote from those rows says the chain *"has moved"*, that its links *"all differ"* and that
*"both terminal approaches have been restated"* — the opposite of what the rows say. The
mechanism's rule is that the model writes prose from the rows and never instead of them; here
it wrote prose against them, and nothing read the one against the other. The overnight price
move — market value down 0.5% — was under the two per cent threshold and is correctly absent.

### The variance check

`audit.scoring.variance` over day one's MSFT run and the refresh
([`msft1-refresh/variance-vs-msft1-round7.json`](msft1-refresh/variance-vs-msft1-round7.json)):

| | |
|---|---|
| facts | identical — 18,610 chosen on both, no document read for the first time |
| calculation keys identical | 185 |
| differing, all price-driven | 7 — beta, covariance and variance (a day more of returns), market capitalisation, market enterprise value, the multiples, net debt |
| only on day one | 31 — the DCF chain, the defect above |

The pre-registered test — *every calculation that differs is either price-driven or a defect,
named* — holds, with the defect named.

### ISSUE 1, over the round's three runs

| run | approved, rendered report | no terminal | no rescue |
|---|---|---|---|
| AZN | yes | yes | **no** — approved by the pre-registered rule at a refused final gate |
| MSFT | yes | yes | yes — beside it, one failed step retried through the product's resume |
| MSFT refresh | **no** — refused, not approved | yes | — |
| *M&T, 17 September — the window's bank, reported beside* | yes | yes | yes |

**One of three.** The target — three of three, one a bank — is not met.

### Spend after day two

| | |
|---|---|
| AZN run | £6.17 |
| MSFT run | £5.88 |
| fresh baseline | £12.82 |
| MSFT refresh | £1.66 |
| **the round so far** | **£26.53**, against £31.50 |

The stop rule before the panel: £26.53 + £2.10 for the counted six + £1.05 for the three
like-for-like MSFT comparisons is £29.68, so both are bought; the identity guess's £2.50 would
take the round to £32.18, so it is not, unless the comparisons come in under their estimates
by enough to make room.

---

# The verdict — 25 September 2026

Nine comparisons — the counted six and the like-for-like three the counted six do not already
hold — over the blinded documents, on `claude-opus-5` at medium effort, the panel unchanged
since Phase 5, with the pre-registration's seed 20260924. **£2.98.** Full records in
[`compare.json`](compare.json).

## The number

| | the counted six | the like-for-like six | Phase 5 |
|---|---|---|---|
| handed to the console | **6 of 6** | **6 of 6** | 6 of 6 |
| handed to the platform | **0** | **0** | 0 |
| dimension verdicts to the platform | **0 of 36** | **0 of 36** | 2 of 36 |
| dimension verdicts *equal* | 1 (verifiability) | 1 (verifiability) | 0 |

The counted six include the fresh Opus 5.5 note, as the target requires, and the fresh note
took all three of its comparisons. The two verifiability wins Phase 5 recorded are gone; what
is left of them is two draws.

## What the pre-registration reads

The readings are read in their order, and the first that holds is the round's.

1. **`fixed` — does not hold.** It needs at least three of the counted six not to choose the
   console and at least two to choose the platform. None did either.
2. **`abandon` — holds.** Its three conditions, each measured:
   - over the like-for-like six, **all six choose the console**;
   - **no more than 2 of 36** dimension verdicts go to the platform — **none** did;
   - **no judge's stated reason changes category from Phase 5's** — *that the platform's stated
     view is broken or contradicts itself.* Read from the judges' own words, below.

### The stated reasons, beside the category each was read as

The six like-for-like comparisons, each beside the same lens's reason in Phase 5:

| comparison | this round, in the judge's words | Phase 5, same lens | category |
|---|---|---|---|
| AZN, operator | *"self-cancelling: it publishes two fair values 75% apart, declares its own discount rate 'unusably low', and leaves nine validation challenges open"* | *"its two terminal methods produce $357.62 versus $158.58 … a 5.6% WACC the document itself calls too low"* | broken, contradicts itself |
| AZN, reader | *"actively undermines its own figures: it declares 'we publish no target value' while the header prints $304.95 and $173.82 … and publishes an unresolved self-contradiction on operating margin"* | *"B's headline is self-defeating — it prints two base cases"* | broken, contradicts itself |
| AZN, sceptic | *"a record of its own failure to reach a view — it asserts 'no US versus ex-US revenue split' in one section while printing US revenue of $23,970m in another, and publishes a $304.95 fair value it simultaneously argues is indefensible"* | *"its base case spans $158 to $358 … a beta of 0.27 that its own red team calls indefensible"* | broken, contradicts itself |
| MSFT, operator | *"B refuses to reach a view, publishes two base cases 94% apart ($227.43 vs $442.01), and openly contradicts itself on deferred revenue"* | *"A explicitly refuses to state a view, prints two terminal values that 'do not bracket a range; they disagree' by 2.9x, and flags its own unresolved self-contradiction"* | broken, contradicts itself — the same three clauses in the same order |
| MSFT, reader | *"two base cases $227.43 and $442.01 … an executive summary flagged 'Insufficient evidence', and a self-contradiction where it 'denies a figure it prints' (deferred revenue) left open at approval"* | *"it states … 'no cash flow line … is present' while printing $182.9bn OCF … and offers two base-case values ($227.90 and $442.26) with no resolution"* | broken, contradicts itself |
| MSFT, sceptic | *"B is internally inconsistent — it prints FY2022–26 revenue and segment detail while asserting 'no prior-period comparatives sit on the record' … two terminal methods 94% apart and no reconciled conclusion"* | *"it repeatedly concedes it cannot address the questions asked and contradicts itself on whether capex/FCF exist"* | broken, contradicts itself |

**No reason changed category.** Where a reason reaches for *"refuses to reach a view"*, Phase
5's same lens had already said *"explicitly refuses to state a view"* beside the same two
disagreeing values, so it is the complaint Phase 5's category was written from, not a new one.
Two reasons add a clause Phase 5's did not: AZN's operator on what the console note covered
and the platform's did not (H1 2026, a trial failure, a 2030 target), and MSFT's sceptic on
DCF inputs *"proposed by the platform's own code"*. Each is a second clause after the first,
and neither is the reason given. **MSFT's two base cases were $227.90 and $442.26 in Phase 5,
and are $227.43 and $442.01 now.**

The counted three against the fresh note say the same: *"self-crippled — two terminal methods
94% apart ($227 vs $442), no rating"*; *"B repeatedly denies figures it prints elsewhere"*;
*"B contradicts itself in ways it admits but does not fix"*.

### The round's reading: `abandon`

> *"Nothing moved since Phase 5, and no stated reason changed category: the document is not
> where the gap is."* — **Then:** *"Stop. Narrow the product's claim to what the readiness audit
> section 4.3 already scores yes — an evidence base and a checking instrument — and spend the
> remaining sessions on ISSUE 1 alone."*

This is the abandonment criterion the operator signed off on 14 September, firing on the
test it was written for. `not_yet` is not reached: the readings are exclusive and read in
order, and `abandon` holds first.

## What the judges still valued

Five of the nine name something in the platform's document worth keeping, and each says
what: *"solely for its red-team log as a checklist of objections"*; *"only as a primary-source
check"*; *"B's source register is worth keeping as a cross-check on A's weaker secondary
citations"*; *"its restated-segment and five-year-revenue data are worth lifting into A, but
not the report itself"*; *"solely for the restated-segment 8-K quote"*. That is the evidence
base and the checking instrument the reading names — the judges' words and the
pre-registration's arriving at the same place independently.

## Every caveat this verdict carries

1. **The panel was not blind.** The identity guess was not bought (below), so Phase 5's rate
   carries: fifteen reads, fifteen correct, every one *certain*. A panel that knows which
   document the platform wrote may prefer by author.
2. **AZN's document did not pass its own final gate.** It was published by the pre-registered
   rule; AZN's reader cites the failed check as a reason. MSFT's passed its gate.
3. **Neither run was a first look.** AZN's planner was shown one prior approved report and
   MSFT's two, as hypothesis material the verifier refuses as evidence. The console notes had
   no such history.
4. **Two of the three comparators are three months old.** The counted six hold the fresh note,
   and it won all three of its comparisons, so the verdict does not rest on the stale ones.

## Spend — the round

| | |
|---|---|
| AZN run | £6.17 |
| MSFT run | £5.88 |
| fresh baseline | £12.82 |
| MSFT refresh | £1.66 |
| nine comparisons | £2.98 |
| **the round** | **£29.51**, against £31.50 signed off |

**The identity guess was not bought.** £29.51 + its £2.50 estimate is £32.01, past the £31.50,
and the stop rule drops it and reports it as not bought. The audit ledger stands at about
£78.44 of its £100 ceiling.

---

## Found after the round, 25 September 2026

**AZN's one refusal measured a draft the document no longer held.** The check measured the
executive summary at 12:44:19, straight after drafting. The revise pass rewrote that section
and replaced its claims at 12:47:18. The claim now on record reads *"The change in working
capital on record is -$386.6m."* — signed, and read as -386.6 by the scanner that was already
in this round's code. Nothing measured again before the gate, so the refusal the
pre-registered rule approved against was a refusal of a sentence that was not in the
document.

The reading above stands, because the rule counted what happened. What it counted was the
platform's defect rather than the writer's. Roadmap §3.19 item 75 has the timestamps and the
fix: the revise step now measures again whenever a revision stands. It also adds a control
for a refusal that is real, which is to redraft the one section.

**The refresh's request was put back as day one left it.** The rejected refresh had left the
request's beta unconfirmed at its own 1.0663. It was restored to the confirmed 1.0659 that
day one's report rests on (item 73).
