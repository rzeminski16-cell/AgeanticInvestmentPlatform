# Experiments

Deliberate comparisons run against the platform, kept whole with their method so a result
can be read a year later. An experiment here is a question with a procedure, not a
conclusion — what it found belongs in whichever document owns the decision.

---

## Baseline: the platform against a well-prompted Claude

**The question.** The platform's premise is that a chatbot asked to research a company
produces fluent, plausible, partly fabricated prose, and that the fix is architectural
rather than a matter of asking more carefully. That premise is worth testing rather than
assuming, because the honest version of it is narrower than the slogan: *asking* is a
request, not a constraint — but a very detailed request is still a strong baseline, and if
it matches the platform on the things that matter, the architecture is buying less than it
costs.

**The two documents:**

| File | What it is |
|---|---|
| [`pipeline-specification.md`](pipeline-specification.md) | Exactly what the platform does, extracted from the code with file references. The specification the prompt was written from, and the thing to re-derive if the code moves. |
| [`baseline-prompt.md`](baseline-prompt.md) | A prompt asking a general assistant to produce the same note under the same constraints. Written to be a **strong** baseline, not a strawman. |

### Method

1. Pick a subject that is **not** convenient: a mid-cap outside the training set's comfort
   zone, a recent restatement, an unusual fiscal calendar, or a company whose filings use
   vocabulary the concept map does not know. A mega-cap with fifteen years of clean US
   filings flatters both systems and separates neither.
2. Run the platform normally, gate by gate. Keep the run id.
3. In a **fresh** conversation, paste `baseline-prompt.md` with the same parameters, and
   answer its two stop points the way you answered the platform's gates.
4. Run the baseline **twice more from scratch**, same parameters. Variance between the three
   is itself a result — the platform's arithmetic is deterministic by construction, so any
   spread in the baseline's numbers is a property the platform does not have.
5. Optionally run a fourth pass with the stop points removed, for the one-shot case.

### Scoring

Score the baseline against the platform on evidence, not on reading pleasure. The prose will
usually be at least as good; that is not the question.

**The verification pass — do this before reading either note for content.** Sample twelve
figures from each: four from the summary, four from the financial sections, four from the
valuation. For each, follow the citation to the document and check the number.

| Measure | How | Platform's own threshold |
|---|---|---|
| Figures traceable to a fetched document or a shown calculation | Sampled, followed | 100% by construction |
| Excerpts that are word-for-word in the cited document | Re-read the source | ≥ 98% |
| Numerals that agree with the figure their citation names | Compare value, not spelling | 0 disagreements |
| Sources published after the as-of date | Check each date | 0 |
| Impossible relations among headline figures | Margin > 1, income > revenue | 0 |
| Arithmetic reproducible from stated inputs | Recompute | ≤ 0.5% relative delta |
| Share of citations at T1–T3 | Count | ≥ 60% |

**The things a prompt structurally cannot do**, which is where the interesting differences
should appear if they appear anywhere:

- **Refuse rather than degrade.** Does the baseline withhold a figure it cannot source, or
  does it reach for a plausible one? Count silent substitutions.
- **Block a model rather than footnote it.** Run one bank. The platform cannot produce a
  DCF for it. Ask the baseline for the same company and see what it does when the prompt
  says not to.
- **Keep the adversary honest.** The platform's red team cannot see the drafting context —
  the input type has no field for it. A single conversation's red-team pass has read
  everything. Compare what each finds, and whether the baseline's challenges are ones the
  draft already answers.
- **Survive the operator.** Try to talk each into a rating the evidence does not support, and
  into relaxing a citation rule. The platform's skill files are additive-only and proved so
  against a corpus; a prompt's constraints are only as durable as the next message.
- **Stay put.** Re-run the same request a week later. The platform pins section versions,
  prompt versions and a code version per calculation, so a report is reproducible from its
  own record.

**Then read for content**, and ask the only question that finally matters: *which note would
you be willing to act on without spot-checking its numbers?*

### Interpreting a result honestly

The baseline winning on a dimension is a finding, not an embarrassment — it says that
dimension did not need the architecture, and that is worth knowing before more of it gets
built. The failure mode to guard against is the opposite one: scoring the two on fluency,
finding them equal, and concluding the constraints were never load-bearing. They are
load-bearing on exactly the axis the reader cannot check by reading.
