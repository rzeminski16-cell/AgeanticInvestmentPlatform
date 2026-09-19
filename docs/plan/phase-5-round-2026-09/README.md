# Phase 5 — the measurement round's two runs, as they happened

The platform half of the round the [pre-registration](../phase-5-pre-registration.json)
fixes: one AZN run and one MSFT run on the tree at `bbfc827`, commissioned 19 September 2026
after all nine rows of [the gate](../phase-5-gate.md) stood.

**Append, never rewrite**, on the same rule as the gate notes. What is here is what the runs
did, including the parts that went wrong.

---

## What the two runs cost and produced

| | AZN | MSFT |
|---|---|---|
| job | `2dd8fae4` | `10a60223` |
| spend | **£7.61** | **£7.06** |
| model calls | 63 | 57 |
| output tokens | 268,632 | 250,411 |
| sections | 18 of 18 | 18 of 18 |
| claims (numeric) | 249 (178) | 272 (181) |
| citations verified | **43 of 43** | **53 of 53** |
| source documents | 13 | 20 |
| report | 16,085 words | 16,725 words |

**£14.67 against the £21 signed off**, and £27.92 → £42.59 of the month's £100.

The `acceptance.json` in each folder was captured at the gate, *before* the overrides below,
so both say *"no report; run status AWAITING_APPROVAL"*. Both runs are `SUCCEEDED` with an
immutable report — AZN `a17af50656b1`, MSFT `4c8a43e2afa0`.

## Both runs were refused by their own final gate, and both were overridden

Neither document passed. The operator's decision was to override and judge them anyway,
so the round has documents; the reasons are on the approval rows, in full, and every verdict
drawn from these documents carries the fact that the platform would not have published them.

| | AZN | MSFT |
|---|---|---|
| `cited_figure_agreement` | **1.00** (at most 0) | 0 ✓ |
| `presentation_integrity` | **3.00** (at most 0) | **2.00** (at most 0) |
| `source_coverage` | **0.8824** (at least 0.90) | 1.00 ✓ |
| `citation_accuracy` | 1.00 ✓ | 1.00 ✓ |
| `primary_source_ratio` | 0.9429 ✓ | 0.9418 ✓ |
| `hallucinated_citation_rate` | 0 ✓ | 0 ✓ |
| `numerical_consistency` | 0 ✓ | 0 ✓ |
| `figure_plausibility` | 0 ✓ | 0 ✓ |
| `assumption_completeness` | 1.00 ✓ | 1.00 ✓ |

**MSFT's single failure is worth reading in full**, because the check names it exactly:
*unformatted integer `66987000000`* and *unformatted integer `19359000000`* — two figures
that reached the page without passing through the display formatter, where a reader should
have seen `$66,987m` and `$19,359m`. That is the same family as the two renderer defects the
gate's row 7 turned up earlier the same day (ROADMAP §3.19 item 35): a number reaching a
reader without going through the one door that formats numbers. It was **not** fixed
mid-round, deliberately — fixing it would have meant the round's two documents were built by
two different renderers, and the round compares them with each other.

**AZN's failures are larger and one is substantive.** `source_coverage` at 0.8824 is two
sections of eighteen — `segment_analysis` and `catalysts` — citing no primary source at all.
`cited_figure_agreement` at 1.00 is one figure disagreeing with the calculation it cites.
Five of the eight §2.4 triggers fired on this run: `low_source_coverage`,
`high_model_uncertainty`, `material_missing_section`, `cost_above_threshold`,
`validation_failure`.

**AZN has now produced an approved report for the first time.** September's AZN run failed at
this same gate and was never approved, which is why the corpus held two approved reports
rather than four.

## What the assumptions did — the round's own correction, working

The gate's last pre-flight check found that the pre-registration's beta corrections had no
mechanism behind them, and the typed betas were removed from both commissions in response
(`bbfc827`). What happened next is the reason that mattered:

| | AZN | MSFT |
|---|---|---|
| beta, derived from prices | **0.274425** | **1.067514** |
| beta, used | **0.3** — clamped to the policy's floor | **1.067514** — used as derived |
| risk-free rate | derived, `aer.services.macro` | **0.0494**, `aer.services.macro` |
| operator-supplied | tax rate, equity risk premium | **equity risk premium only** |
| rows on the gate | 9 | 10 |

**MSFT is the correction landing.** September's approved MSFT run valued on a hand-typed beta
of 0.900; this run valued on the 1.0675 its own regression produced. At a 4.5 % premium that
is 86 basis points on the cost of equity — a materially different valuation, and the one the
platform's own work supports. Ten of its eleven assumptions were derived; the single typed
number is the equity risk premium, which has no series behind it and is a judgement stated as
one.

**AZN is the correction meeting something the pre-registration had not seen.** The platform
derived 0.274425, the audit policy's standing band for beta is 0.3–2.0, and the policy
amended it to **0.3** and recorded the amendment. That is the same 0.300 the pre-registration
named as AZN's *"hand-typed"* beta and asked the round to remove — and it was never typed.
September's run derived 0.276218 and was clamped to 0.3 the same way. **The corpus's 0.300
was the policy's floor, not an operator's guess**, and the pre-registration's stated reason
for the correction — *"far below any plausible pharmaceutical beta and no justification in
the record defends it"* — is wrong on its own terms: AZN's prices imply about 0.27, so the
record does defend it. The effect on the valuation is twelve basis points, which is nothing;
the effect on the record is that a number the round set out to remove is still there, put
there by a different hand than the one the document blamed.

## What the round must say about itself when it reports

Three things are already true of this round and belong in its result rather than in a
footnote of it:

1. **Every judge could tell which document the platform wrote.** Fifteen identity-guess reads,
   fifteen correct, every one *certain* — [gate row 7](../phase-5-gate.md). A panel that knows
   the author is a panel whose preference may follow the author.
2. **Neither document passed its own final gate.** Both were published by operator override,
   with the failures named above.
3. **The MSFT run was not a first look.** Its plan gate recorded *"the first MSFT run's
   planner was shown prior research; independence lost"* — MSFT is already in the corpus, so
   this run began from prior work that September's console note never had. The platform
   genuinely does benefit from its own corpus, and that is a real property of the product;
   it is not a property the comparator shares.

## Still owed for the round to produce a verdict

- The fresh baseline on the current best route, which the pre-registration's comparator names
  and the delivery plan costs at about £10. Not inside the £21.
- The panel's six comparisons — two subjects by three lenses — over the blinded documents,
  through `audit/judges/`. Billable, on the same footing as the identity guess's £4.27.
