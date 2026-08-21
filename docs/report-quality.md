# Report quality — what the CHRW note reads like, and what to change

*Written 2026-08-21 against the C.H. Robinson run (`07d95870`, report `4705b735`, code
`d0cfd07`): 35 pages, 18 sections, 156 citations all verified, £8.18 spent, 3 sections
not generated.*

`docs/gap-analysis.md` tracks whether the platform is **correct**. This file tracks
whether its output is **readable as research**. They are different questions and the CHRW
run is the first where they clearly come apart: every correctness gate that matters
passed, the arithmetic is right, the sourcing is complete — and the document still reads,
in places, as an account of its own construction rather than as a note about a freight
broker.

## The standard

A research note is read by somebody deciding what to do. Three tests follow from that,
and every finding below is a failure of one of them:

1. **Every sentence is about the company.** Not about the run, the model, the word
   budget, or the platform's internal decisions.
2. **A gap is a limit on the conclusion, not a description of the process.** "The
   securitisation terms are not public, so the facility's covenants cannot be assessed"
   is analysis. "The filings reviewed do not set out the programme's terms" is a note to
   self.
3. **Nothing explains the platform to the reader.** The reader did not ask how the
   report was made and cannot act on the answer.

The platform's honesty requirements are not in tension with any of this. Being honest
about a gap and writing about the run are different things, and the run currently
conflates them.

## What this run got right, so that no fix loses it

- **The splitter fix (A49) is emphatic.** The 10-K yielded **60 excerpts averaging 1,710
  characters** against MTB's 9 averaging 36,003. Across the run, 169 excerpts from 8
  documents. Citations rose to 156, all verified; failed sections fell from seven to
  three; calculations from 59 to 118.
- **The revenue fix (A62) holds across the whole history.** FY2021 $23,102m → FY2022
  $24,697m → FY2023 $17,596m → FY2024 $17,725m → FY2025 $16,233m, with a 3.6% net margin.
  Correct for a gross-revenue broker, and no partial-caption break anywhere in the series.
- **The plausibility floor (A61) ran and stayed silent.** `figure_plausibility 0E-8 pass`
  on a business whose genuine margin is thin. First live exercise; no false positive.
- **The executive summary is genuinely good.** "We publish no valuation conclusion and no
  price target. The evidence in hand is the issuer's own regulatory reporting; it carries
  no share price, market capitalisation, peer multiple or consensus estimate, so any
  fair-value range would be assertion rather than analysis." That is how a gap should be
  written: the limit, its cause, and what it forbids — in the reader's terms.
- **Scenarios is the best prose in the document.** "Cash generation is the tell." "Small
  proportional changes on either side of that gap swing operating income far more than
  proportionally." A person wrote that, in effect.
- **The red team remains the strongest component.** Seven challenges, four material, and
  the valuation one is the sharpest observation in the run: a growth case leaning on
  per-share accretion, in a report with no valuation and a single share-count observation.
- **The front-page coverage notice works.** It names all three missing sections and the
  failed check, above the fold.

## Findings

| # | Finding | Notes |
|---|---|---|
| R1 | **A platform banner sits inside the body prose of six sections** | Investment Thesis, Business Overview, Earnings Quality, Cash Flow Analysis, Key Risks and Catalysts each open with: *"Insufficient evidence: This section ran past its word budget and was shortened by dropping trailing sentences rather than discarding the draft (ADR 0057). The analysis is the model's; the cut is the platform's."* An equity note that cites its own architecture decision record has stopped being an equity note. The banner exists for a good reason — the reader should know the text was cut — but the disclosure belongs in the document's front matter or its appendix, never as the first thing under a section heading, and it must never name an ADR. Fix: move salvage disclosure to a single line in the coverage notice ("Six sections were shortened to their length budget") and drop the mechanism entirely. |
| R2 | **"Insufficient evidence" is the wrong label for a length cut** | The banner's label and its sentence disagree: the label says the section lacked evidence; the sentence says it ran long and was trimmed. Those are opposite conditions — one is a thin section, the other an over-full one — and a reader who takes the label at face value will discount a section that was actually rich. Fix: the label must be derived from the cause. "Shortened to budget" and "Evidence was thin" are different notices and should never share a heading. |
| R3 | **The same banner is printed twice for every affected section** | Once inline under the section heading, once again in the Validation & Disagreements appendix, verbatim, for all six. The appendix listing is the appropriate home; the inline copy is the intrusion. Fix falls out of R1. |
| R4 | **The DCF section is written in the platform's voice** | Section 13 reads, in full: *"No valuation exists for this run, so no commentary was requested from the writing model: there are no recorded figures to interpret, and the method note states why none were produced."* Three process nouns in one sentence — "this run", "the writing model", "the method note". The reader does not know there is a writing model. This is the A51c deterministic fill doing its job structurally and failing it editorially. Fix: rewrite the standalone text in the register of the report — what is absent, why it matters to the decision, and what would produce it. Something in the shape of: *"No discounted cash flow was produced. The three cost-of-capital inputs a valuation requires — a risk-free rate, a beta and an equity risk premium — were left unset at the assumptions gate, so no discount rate exists and no intrinsic value could be computed. Any valuation conclusion in this note would therefore be assertion."* |
| ~~R5~~ | ~~**The method note gives the wrong reason for the missing DCF**~~ | **Withdrawn — the method note was right and this finding was wrong, twice over.** It says: *"The balance sheet carries debt and the income statement shows no interest expense, so the cost of that debt cannot be derived."* That is the literal text of `ValuationNotPossibleError` as raised by `_cost_of_debt` in `valuation_run.py`, and it is exactly what happened. The first version of this row claimed the three cost-of-capital inputs were left blank; the second claimed they were supplied but unconfirmed. The operator's database settles it: **all eleven required assumptions are confirmed**, and `risk_free_rate` (0.03), `equity_risk_premium` (0.03) and `beta` (1.14) each carry the operator's own address as `approved_by`. The gate worked, the entry forms worked, the confirmation worked. What stopped the valuation is R13, and only R13. The report told its reader the truth on the first attempt.
| R6 | **A section that could not be generated says only "This section could not be generated."** | That is the whole of sections 6, 7 and 11. No cause, no consequence, no direction. Contrast the front-page coverage notice, which at least names them. Each failure had a *different* cause and each is legible from the run: Management & Governance was refused over headcount, an annual-meeting year and a vote percentage — proxy-statement data the platform does not acquire; Historical Financial Analysis lost its claims to missing citations; Capital Allocation failed twice, first for uncited prose figures and then for uncited table figures. Fix: give the placeholder a stated reason in the reader's terms and, where it exists, the consequence — "Governance data is drawn from the proxy statement, which this run does not acquire; board composition and remuneration are therefore unassessed." |
| R7 | **Catalysts is a filing calendar, not a catalyst list** | Every catalyst in the section is a scheduled SEC filing whose date is extrapolated from previous filing dates, and the rationale column shows the extrapolation: *"The company filed its Q1 2026 Form 10-Q on May 1, 2026 and its Q2 2026 Form 10-Q on July 31, 2026… extrapolating this roughly three-month cadence."* The prose spends its budget explaining what filings are: *"These filings function as a running record of leadership and incentive-alignment decisions."* A reader learns nothing they could act on. The honest version of this section is two sentences — no dated catalyst is disclosed in the evidence, and here is what would have to appear for one to exist — plus the one genuine item the section did find (the Europe Surface Transportation divestiture anniversarying through the comparatives). Fix: the catalysts contract should refuse "the next periodic filing" as a catalyst outright, and the section should be allowed to be short. |
| R8 | **Scenario bands are anchored on fiscal 2021 and 2022 ratios in an August 2026 note** | The bear/base/bull cases are built on "the fiscal 2021 anchor of 4.68 percent", "the fiscal 2022 anchors of 4.68 and 5.13 percent", "the 62.6 days of receivables observed in fiscal 2021". The red team's severity-4 challenge is correct and unanswered: those two vintages disagree violently with each other — cash conversion 0.112 against 1.755, days outstanding 62.6 against 44.2, ROE 0.418 against 0.695 — so they cannot define a "historically observed band" for 2026-27. The section is honest about the vintage, which is why the red team could catch it; the defect is the selection, not the disclosure. Fix: scenario anchors should prefer the most recent complete period and state the vintage inline; where only stale vintages exist, the band should widen rather than borrow precision. Needs a look at how the scenario builder chooses its ratio set. |
| R9 | **The failed-checks table republishes the very integers that failed the check** | `presentation_integrity` failed with ten findings, each of the form `unformatted integer '432183000'`, and gap A60's "What the Failed Checks Found" table prints all ten into the rendered document. The integers are now *in* the report — so a re-evaluation of the same job would find them again. The finding text is self-sustaining. Worse, in the final PDF these are the **only** occurrences of those integers, which means the diagnosis I gave on first reading — that a failed section's figures leaked into the page — is not supported by the finished document and needs checking against the job's stored sections before anything is changed. Two things to fix, in order: (a) find where the original ten came from, since `presentation_integrity` scanned an assembly made *before* the findings table was filled; (b) regardless of (a), mask numerals in the findings table, or exclude the validation section from the integrity scan, so a check can never fail on its own output. |
| R10 | **The comps paragraph reads like a form letter** | *"A comparable-company analysis was attempted as at 21 August 2026, but every one of the 7 proposed peer(s) was excluded for want of usable data… There is no fuller version elsewhere; the operator's own copy lists each excluded peer with the reason."* Three problems: the parenthetical plural "peer(s)", the numeral where a word belongs ("seven"), and "the operator's own copy" — which in a personal research tool is the same document the reader is holding. This is my own A53 text and it was written for the withholding case, where "the operator's copy" means something. Fix: house style spells small integers, the plural agrees, and the last clause is dropped when there is no withheld version. |
| R11 | **The approval page's calculations table has no period column and prints raw Decimals** | `review.html` renders Name / Formula / Result / Inputs, interpolating `row.output_value` directly: `928567000.000000000000 USD`. `depreciation_rate` appears six times — 0.65, 0.68, 0.58, 0.76, 0.88 — with nothing saying which year each belongs to; ROE appears four times likewise. A54 put period labels on the calculation record and into the report footnotes but never reached this table, and A66's money formatting lives in `display.scalar`, which this template bypasses. The consequence is direct: the red team had to reconstruct the vintages itself to make its best argument, and the operator approving the run could not see what the red team saw. Fix: add a Period column and route the value through the house-style formatter. Two template lines and a query change. |
| R12 | **Every disagreement on the approval page reads "0 thesis (T1_REGULATORY)"** | An evidence count that is always zero for thesis-level disagreements, rendered as a label fragment under both positions. It appears only on the approval page — the report body is clean — which narrows gap A68 to a single template. Fix: drop the count line for thesis-level disagreements; render "3 supporting facts, best tier T1" for fact-backed ones. |
| **R13** | **The cost of debt is derived from a line many filers never tag, and no person may supply it** | **The cause of the missing DCF, confirmed against the operator's database, and not the concept-map gap this row first alleged.** `_cost_of_debt` derives the pre-tax cost of debt as interest expense over average debt; when the balance sheet carries debt and `interest_expense` does not resolve, it raises `ValuationNotPossibleError` and the run loses the discount rate, the forecast, the scenario bridge, the sensitivity grid, the football field and the valuation section. For CHRW that happened with **eleven of eleven assumptions confirmed** and every driver present. **There is no mapping gap.** A sweep of every interest-bearing tag on the company returns eight: two are `pre_tax_income` (matched only because "Interest" sits inside *NoncontrollingInterest*), two are `InterestPaid`/`InterestPaidNet` and **already map** to `interest_paid`, and four are share-based-compensation option-pricing inputs and unrecognised-tax-benefit penalties, correctly unmapped. C.H. Robinson simply does not tag interest expense as a separate element — it is presented inside a net "interest and other" line — and that is ordinary practice, not an anomaly. So the defect is structural: **`cost_of_debt` is the only discounted-cash-flow input absent from `PROPOSABLE_NAMES`.** A risk-free rate, a beta and an equity risk premium are all inputs the platform refuses to derive and asks a person to own; the cost of debt is derived-or-nothing, so an operator who meets this has no form, no override and no move. Fix, in this order: **(a) Done.** `cost_of_debt` is in `PROPOSABLE_NAMES` — with one refinement over this row's first sketch: it is *conditionally* required rather than joining `REQUIRED_NAMES`, because a run that can derive the rate from a tagged interest-expense line must not pause demanding an opinion for a number the filings already carry. It joins the gate's outstanding list only under the exact condition that killed this run (`cost_of_debt_required`: debt on the latest balance sheet, no interest-expense line), the valuation uses the confirmed rate only where the derivation cannot run — a filed line still outranks the assumption — and the refusal message now names the remedy. **(b) Done.** The gate names the dependency before approval: `assemble` puts `cost_of_debt` in `outstanding` with a reason stating the condition and what to enter, the record carries it through `refreshed_payload` so the payload hash keeps its byte-for-byte property, and the gate page's existing supply form picks it up like any other gap. **(c)** still open: decide separately whether the gate should *propose* cash interest paid over average debt as a default, clearly labelled as cash rather than accrual — that is a substitution affecting a published number, so it belongs to the operator and to an ADR, not to a patch. |
| R18 | **A share-based-compensation risk-free rate must never be mapped to `risk_free_rate`** | The sweep surfaced `ShareBasedCompensationArrangementByShareBasedPaymentAwardFairValueAssumptionsRiskFreeInterestRate{Maximum,Minimum}`, correctly unmapped today. They are option-pricing inputs for a specific grant on a specific date, not a market risk-free rate, and the platform is currently *asking* for a `risk_free_rate` it cannot derive. The two facts sitting in the same database under a nearly identical name is exactly the shape of a future well-meant mapping that would quietly discount a whole valuation at an option model's assumption. Recorded here so that closing the `risk_free_rate` gap is never done this way. No code change; this row is the guard.
| R14 | **Calculations appear to be recorded more than once** | 118 rows for 5 periods, with visible exact duplicates — `ebitda 897779000` twice, `ebitda 1173367000` twice, `depreciation_rate 0.581915801094` twice. Either the calculate step runs some calculations per-statement as well as per-period, or a re-run appended rather than replaced. Worth a look before the table gains a period column, because duplicate rows with identical periods would then be visibly wrong rather than merely redundant. |
| R15 | **Point-in-time was off, and the report states it without saying what it costs** | The front matter carries "POINT-IN-TIME off" beside the as-of date. A reader who does not know the platform cannot tell that this switches off the guarantee that nothing published after the as-of date informed the note. Fix: when point-in-time is off, the coverage notice should say so in a sentence — the setting is not self-explanatory and it changes what the document is. |
| ~~R17~~ | ~~**A hand-supplied assumption is saved but not usable**~~ | **Withdrawn — no evidence behind it.** The theory was that `create_assumption_page` produces a proposal rather than a confirmation, so a typed value would be saved and then filtered out by `confirmed_values`. The mechanism is real, but it is not what happened: the operator confirmed all three, and the database shows them approved with the operator's own address against each. The separate confirm step was found, understood and used. Nothing here needs changing, and the doctrine — typing a value and agreeing the run may rest on it are separate acts — stands unchallenged by this run.
| R16 | **`depreciation_rate` of 0.65 to 0.88 is arithmetically fine and reads as alarming** | Depreciation and amortisation running at two-thirds to seven-eighths of net PP&E annually. For an asset-light broker whose D&A is largely intangible amortisation this is defensible, but the label promises a depreciation rate on fixed assets and delivers something else. Fix: either rename the calculation to what it measures, or exclude intangible amortisation from the numerator, or add the denominator to the label. This is a candidate for the plausibility floor's next relation only if the first two options fail — the number is odd, not impossible, and A61's set must stay closed and small (ADR 0066). |

## The plan

Ordered by what most improves the next report per unit of work.

**First — the cost of debt (R13). Done, parts (a) and (b).** Not a readability finding
at all: it is why three consecutive runs have shipped without a valuation, and on this
run it discarded eleven confirmed assumptions over a line the filer never tagged. There
was nothing to map — `cost_of_debt` is now suppliable by hand, exactly as the risk-free
rate already is, and the gate names the dependency before approval for the runs where
the derivation cannot happen. A filer's presentation choice can no longer defeat a
forecast the operator has otherwise fully specified. Part (c) — a proposed cash-interest
default — remains an operator decision needing an ADR.

**Second — the register fixes (R1, R2, R3, R4, R6, R10).** These are the ones that decide
whether the document reads as research. They are prompt, template and copy changes with
no architectural content, they share one theme, and together they remove every sentence
in the report that is about the report. Do them as a single batch:

1. Salvage disclosure moves out of the section body into the coverage notice, loses the
   ADR reference, and gets a label derived from its actual cause.
2. The DCF standalone text and the method note are rewritten in the report's voice, with
   the outstanding assumptions named first (R4 + R5).
3. Not-generated placeholders gain a cause and a consequence.
4. The comps sentence gets house style and loses the withheld-copy clause.

**Third — the integrity loop (R9).** A validation check that fails on its own output is
a correctness problem, not a presentation one, and it is the only finding here that could
mislead a future run. Investigate the origin first; fix the loop regardless.

**Fourth — the approval-page surfaces (R11, R12).** The calculations table is where the
operator decides whether to approve, and it is currently unreadable. Small, and it
improves the decision rather than the document.

**Fifth — the evidence-and-selection issues (R7, R8, R14).** Catalysts needs a
contract change; scenario anchoring needs a look at the builder; interest expense needs
the unmapped list from a live run; the duplicate calculations need the database. These
are each a small investigation before they are a fix.

**Not scheduled: R15, R16.** Both are one-line judgements that can ride with any batch.

## How to tell it worked

The next run should be readable end to end without the reader learning anything about
the platform. Concretely, in the rendered report:

- No occurrence of "ADR", "the writing model", "this run", "word budget", "the platform",
  or "the operator's own copy".
- Every section that was shortened or is missing says so once, in the coverage notice,
  in a sentence naming the cause in the reader's terms.
- The valuation section names the three unset assumptions and the exhibits their absence
  costs.
- `presentation_integrity` either passes, or fails on something that is not its own
  findings table.
- Catalysts contains no catalyst whose event is "the company will file a periodic report".
- The next run of a company carrying debt produces a discounted cash flow, or names the
  single input it still lacks *at the gate*, while the operator can still act on it.

A cheap way to hold most of this permanently: extend the `presentation_integrity` metric
with a **register check** — a closed vocabulary of process words that must not appear in
a rendered section body. It is the same shape as the existing defect scan, it costs
nothing to run, and it converts this whole document into a gate rather than a memory.

## What this deliberately does not change

- **The honesty requirements stay exactly as they are.** Every fix above changes where a
  disclosure appears and what register it is written in. None of them removes a
  disclosure, softens a refusal, or lets an uncited figure through.
- **The numeral rule is not touched.** Three sections failed over it in this run and all
  three refusals were correct — the data classes behind them (proxy governance data,
  uncited table figures) are acquisition and drafting problems, not rule problems.
- **The red team is not touched.** It is the best-performing component in the system and
  its output is the one place where process language is appropriate, because a challenge
  is *about* the draft.
