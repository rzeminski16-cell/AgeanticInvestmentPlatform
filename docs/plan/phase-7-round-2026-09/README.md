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
