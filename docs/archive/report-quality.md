# Report quality — what the CHRW note reads like, and what to change

*Written 2026-08-21 against the C.H. Robinson run (`07d95870`, report `4705b735`, code
`d0cfd07`): 35 pages, 18 sections, 156 citations all verified, £8.18 spent, 3 sections
not generated.*

`docs/archive/gap-analysis.md` tracks whether the platform is **correct**. This file tracks
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
| R1 | **A platform banner sits inside the body prose of six sections** | Investment Thesis, Business Overview, Earnings Quality, Cash Flow Analysis, Key Risks and Catalysts each open with: *"Insufficient evidence: This section ran past its word budget and was shortened by dropping trailing sentences rather than discarding the draft (ADR 0057). The analysis is the model's; the cut is the platform's."* An equity note that cites its own architecture decision record has stopped being an equity note. The banner exists for a good reason — the reader should know the text was cut — but the disclosure belongs in the document's front matter or its appendix, never as the first thing under a section heading, and it must never name an ADR. Fix: move salvage disclosure to a single line in the coverage notice ("Six sections were shortened to their length budget") and drop the mechanism entirely. **Done.** The two edit disclosures are shared constants in `core/section_output.py`, stated in the reader's register with no ADR reference and no mechanism; the renderer keeps them out of the inline banner (`reader_warning`) and counts them into the coverage notice one line per kind. Rows written before the change are normalised on render, so re-rendering an old report also comes out clean. |
| R2 | **"Insufficient evidence" is the wrong label for a length cut** | The banner's label and its sentence disagree: the label says the section lacked evidence; the sentence says it ran long and was trimmed. Those are opposite conditions — one is a thin section, the other an over-full one — and a reader who takes the label at face value will discount a section that was actually rich. Fix: the label must be derived from the cause. "Shortened to budget" and "Evidence was thin" are different notices and should never share a heading. **Done.** The writer no longer folds the edit sentences into the shortfalls that `degradation_note` labels: "Insufficient evidence" now covers evidence shortfalls alone, the edit notes ride after it unlabelled, and the confidence cap still applies to both. A test holds that a pure length cut never carries the evidence label. |
| R3 | **The same banner is printed twice for every affected section** | Once inline under the section heading, once again in the Validation & Disagreements appendix, verbatim, for all six. The appendix listing is the appropriate home; the inline copy is the intrusion. Fix falls out of R1. **Done with it:** the Scope and limitations appendix keeps the whole per-section account — evidence part and edit part together — and the inline banner carries only the evidence part. |
| R4 | **The DCF section is written in the platform's voice** | Section 13 reads, in full: *"No valuation exists for this run, so no commentary was requested from the writing model: there are no recorded figures to interpret, and the method note states why none were produced."* Three process nouns in one sentence — "this run", "the writing model", "the method note". The reader does not know there is a writing model. This is the A51c deterministic fill doing its job structurally and failing it editorially. Fix: rewrite the standalone text in the register of the report — what is absent, why it matters to the decision, and what would produce it. **Done.** The standalone now reads: *"No discounted cash flow was produced, so there are no valuation figures to interpret. The method record in this section names the missing input; without it there is no discount rate and no intrinsic value, and any per-share conclusion would be assertion rather than analysis."* (It points at the recorded reason rather than naming the three cost-of-capital inputs, because R5's withdrawal showed the missing input varies — for CHRW it was the cost of debt with all eleven assumptions confirmed.) The method note's wrappers lost "for this run", "the platform's" and "this run holds no market prices" in the same pass, and tests hold the process nouns out of everything the section stores. |
| ~~R5~~ | ~~**The method note gives the wrong reason for the missing DCF**~~ | **Withdrawn — the method note was right and this finding was wrong, twice over.** It says: *"The balance sheet carries debt and the income statement shows no interest expense, so the cost of that debt cannot be derived."* That is the literal text of `ValuationNotPossibleError` as raised by `_cost_of_debt` in `valuation_run.py`, and it is exactly what happened. The first version of this row claimed the three cost-of-capital inputs were left blank; the second claimed they were supplied but unconfirmed. The operator's database settles it: **all eleven required assumptions are confirmed**, and `risk_free_rate` (0.03), `equity_risk_premium` (0.03) and `beta` (1.14) each carry the operator's own address as `approved_by`. The gate worked, the entry forms worked, the confirmation worked. What stopped the valuation is R13, and only R13. The report told its reader the truth on the first attempt.
| R6 | **A section that could not be generated says only "This section could not be generated."** | That is the whole of sections 6, 7 and 11. No cause, no consequence, no direction. Contrast the front-page coverage notice, which at least names them. Each failure had a *different* cause and each is legible from the run: Management & Governance was refused over headcount, an annual-meeting year and a vote percentage — proxy-statement data the platform does not acquire; Historical Financial Analysis lost its claims to missing citations; Capital Allocation failed twice, first for uncited prose figures and then for uncited table figures. Fix: give the placeholder a stated reason in the reader's terms and, where it exists, the consequence. **Done.** The renderer reads the refusal kinds off the stored diagnostics through the same signature table the run's own cause-counter uses (`refusal_causes_in`), and states them in the reader's terms with the consequence — e.g. "This section could not be generated: its draft stated figures that could not be traced to a recorded source. What it would have covered is absent from this note rather than asserted without support." The raw ids and schema paths stay in the run, and a reason matching no signature keeps the plain line, because a wrong cause is worse than none. |
| **R7** | **Catalysts is a filing calendar, not a catalyst list** | **Done.** Every catalyst in the section is a scheduled SEC filing whose date is extrapolated from previous filing dates, and the rationale column shows the extrapolation: *"The company filed its Q1 2026 Form 10-Q on May 1, 2026 and its Q2 2026 Form 10-Q on July 31, 2026… extrapolating this roughly three-month cadence."* The prose spends its budget explaining what filings are: *"These filings function as a running record of leadership and incentive-alignment decisions."* A reader learns nothing they could act on. The honest version of this section is two sentences — no dated catalyst is disclosed in the evidence, and here is what would have to appear for one to exist — plus the one genuine item the section did find (the Europe Surface Transportation divestiture anniversarying through the comparatives). **The refusal is `reporting_calendar_entries` in `core/section_output.py`, and it catches the defect two ways.** A row whose *label* names a routine periodic report — 10-Q, 10-K, 20-F, proxy statement, results announcement, trading update — is refused by name. A row whose *stated basis* dates it from the filing rhythm — "extrapolating", "cadence", "typically files" — is refused whatever it is called, which catches the general case: a date nobody disclosed, inferred from the calendar. The vocabulary is closed, word-bounded and argued with in the docstring, and the harder half is what it deliberately lets through: an announced investor day, a statutory decision deadline, an expected completion, a debt maturity, a disposal clearing the comparatives. An annual general meeting and a Form 8-K are excluded on purpose — the first is genuinely the catalyst once a vote is contested, the second exists because something happened — and both exclusions are pinned by test, because a rule that only ever says no would close the section rather than improve it. The refusal is corrective, not fatal: it goes back to the writer with the remedy, and it carries its own cause (`calendar`) through the classifier and into the reader-facing placeholder a failed section shows. **"Allowed to be short" is the contract's half** (migration 0052, `catalysts` v2): the array's description says an empty list is the correct answer rather than a gap, and the commentary's says two sentences is a complete answer to this section. Told up front rather than only after a refusal, which would cost a redraft every run. **One correction the repository forced, and it improved the rule:** the first cut read a named array, and `test_no_section_key_is_hardcoded` refused it — sections are rows, and a module naming one makes the next section a code change. It now finds its rows by shape, scanning anything anywhere in the content that declares an `expected_timing`, so a section listing dated events under another heading gets the same check without the core knowing it exists. The doctrine, including what the rule deliberately does not catch, is ADR 0069. |
| **R8** | **Scenario bands are anchored on fiscal 2021 and 2022 ratios in an August 2026 note** | **Done, and the cause is not where this row looked for it.** The bear/base/bull cases are built on "the fiscal 2021 anchor of 4.68 percent", "the fiscal 2022 anchors of 4.68 and 5.13 percent", "the 62.6 days of receivables observed in fiscal 2021". The red team's severity-4 challenge is correct and unanswered: those two vintages disagree violently with each other — cash conversion 0.112 against 1.755, days outstanding 62.6 against 44.2, ROE 0.418 against 0.695 — so they cannot define a "historically observed band" for 2026-27. **There is no scenario builder choosing a ratio set.** "Anchor" is the writer's word, not the platform's; the section wrote about the only ratios it was shown. The defect is one layer down, in `gather_evidence`: recorded calculations reached a section ordered by `Calculation.sequence`, which is the order they were struck, and the analysis loop strikes **oldest period first** because each period's paired quality signals compare against the one before it. So the cap kept the oldest and cut the newest. Measured on a five-period scene: twelve rows of FY2025 and five of FY2024 now, where before the budget spent all seventeen slots on FY2021. **This is gap A39 again, one layer over** — the comment beside `_FACT_POOL` already records the same bug being found for facts, that "a pool the size of the cap makes the ordering clause the real selector"; facts got a ranking and calculations never did. Fixed by ordering `period_end DESC NULLS LAST, sequence`. The sabotage run reproduces the finding exactly — every slot filled with FY2021 — and the full-run golden moved with it, from the oldest year's net margin to the newest. **The nulls very nearly went the other way, and that would have been worse than the bug.** A calculation with no period is a run-level figure, and the first cut sorted those ahead on the grounds that there are few of them. There are not: the valuation runs under the same job, and its sensitivity grid alone strikes over a hundred period-less rows, which would have filled the cap with grid cells and cut *every* period. Sorting them last also leaves them exactly where the old `sequence` ordering had them, since the valuation is struck after the analysis — so the change moves the period boundary and nothing else. The near-miss is pinned by its own test. **The half not done, and why:** "where only stale vintages exist, the band should widen rather than borrow precision" is a writing judgement, and no code can widen a band written in prose. The platform can only decline to hide the staleness, which it already does — the vintage is stated inline, which is how the red team caught it. What this fix removes is the condition that made the judgement necessary: recent periods existed and were simply never shown. |
| R9 | **The failed-checks table republishes the very integers that failed the check** | `presentation_integrity` failed with ten findings, each of the form `unformatted integer '432183000'`, and gap A60's "What the Failed Checks Found" table prints all ten into the rendered document. The integers are now *in* the report — so a re-evaluation of the same job would find them again. The finding text is self-sustaining. Worse, in the final PDF these are the **only** occurrences of those integers, which means the diagnosis I gave on first reading — that a failed section's figures leaked into the page — is not supported by the finished document and needs checking against the job's stored sections before anything is changed. Two things to fix, in order: (a) find where the original ten came from, since `presentation_integrity` scanned an assembly made *before* the findings table was filled; (b) regardless of (a), mask numerals in the findings table, or exclude the validation section from the integrity scan, so a check can never fail on its own output. **Done, both.** (a) The origin is the Scope and limitations appendix: it printed failed sections' raw validator diagnostics, and those strings quote the very integers they flagged — reproduced empirically against the scan. A failed section's appendix entry is now its reader-register cause sentence (the R6 machinery), so the diagnostics never reach the document. (b) The findings table quotes each finding as a code span — the scan's own carve-out for deliberate literals, and the honest presentation for a machine string reproduced verbatim — so a re-evaluation can never re-find a finding. Both directions are pinned by test, control arm included. |
| R10 | **The comps paragraph reads like a form letter** | *"A comparable-company analysis was attempted as at 21 August 2026, but every one of the 7 proposed peer(s) was excluded for want of usable data… There is no fuller version elsewhere; the operator's own copy lists each excluded peer with the reason."* Three problems: the parenthetical plural "peer(s)", the numeral where a word belongs ("seven"), and "the operator's own copy" — which in a personal research tool is the same document the reader is holding. This is my own A53 text and it was written for the withholding case, where "the operator's copy" means something. Fix: house style spells small integers, the plural agrees, and the last clause is dropped when there is no withheld version. **Done.** Counts up to twelve are spelled, singulars read as singulars ("its single proposed peer was excluded"), "peer(s)" is gone from both states, and the attempted-and-empty paragraph no longer mentions the operator's copy at all — the clause survives only in the withholding state, where a fuller version genuinely exists. |
| R11 | **The approval page's calculations table has no period column and prints raw Decimals** | `review.html` renders Name / Formula / Result / Inputs, interpolating `row.output_value` directly: `928567000.000000000000 USD`. `depreciation_rate` appears six times — 0.65, 0.68, 0.58, 0.76, 0.88 — with nothing saying which year each belongs to; ROE appears four times likewise. A54 put period labels on the calculation record and into the report footnotes but never reached this table, and A66's money formatting lives in `display.scalar`, which this template bypasses. The consequence is direct: the red team had to reconstruct the vintages itself to make its best argument, and the operator approving the run could not see what the red team saw. Fix: add a Period column and route the value through the house-style formatter. Two template lines and a query change. **Done.** The table now carries a Period column (`period_label`, an em dash where a figure is not a statement-period result) and the value renders through `display.scalar` in the house style — no more twelve-decimal Decimals, and the vintages the red team had to reconstruct are on the page the operator decides from. |
| R12 | **Every disagreement on the approval page reads "0 thesis (T1_REGULATORY)"** | An evidence count that is always zero for thesis-level disagreements, rendered as a label fragment under both positions. It appears only on the approval page — the report body is clean — which narrows gap A68 to a single template. Fix: drop the count line for thesis-level disagreements; render "3 supporting facts, best tier T1" for fact-backed ones. **Done.** A thesis position shows its tier alone — the value and unit are the ladder's placeholders, never compared — and a challenge carrying cited evidence gets a summary line ("3 fact(s), 0 calculation(s), 1 source(s) — best tier T2") under the challenge text. |
| **R13** | **The cost of debt is derived from a line many filers never tag, and no person may supply it** | **The cause of the missing DCF, confirmed against the operator's database, and not the concept-map gap this row first alleged.** `_cost_of_debt` derives the pre-tax cost of debt as interest expense over average debt; when the balance sheet carries debt and `interest_expense` does not resolve, it raises `ValuationNotPossibleError` and the run loses the discount rate, the forecast, the scenario bridge, the sensitivity grid, the football field and the valuation section. For CHRW that happened with **eleven of eleven assumptions confirmed** and every driver present. **There is no mapping gap.** A sweep of every interest-bearing tag on the company returns eight: two are `pre_tax_income` (matched only because "Interest" sits inside *NoncontrollingInterest*), two are `InterestPaid`/`InterestPaidNet` and **already map** to `interest_paid`, and four are share-based-compensation option-pricing inputs and unrecognised-tax-benefit penalties, correctly unmapped. C.H. Robinson simply does not tag interest expense as a separate element — it is presented inside a net "interest and other" line — and that is ordinary practice, not an anomaly. So the defect is structural: **`cost_of_debt` is the only discounted-cash-flow input absent from `PROPOSABLE_NAMES`.** A risk-free rate, a beta and an equity risk premium are all inputs the platform refuses to derive and asks a person to own; the cost of debt is derived-or-nothing, so an operator who meets this has no form, no override and no move. Fix, in this order: **(a) Done.** `cost_of_debt` is in `PROPOSABLE_NAMES` — with one refinement over this row's first sketch: it is *conditionally* required rather than joining `REQUIRED_NAMES`, because a run that can derive the rate from a tagged interest-expense line must not pause demanding an opinion for a number the filings already carry. It joins the gate's outstanding list only under the exact condition that killed this run (`cost_of_debt_required`: debt on the latest balance sheet, no interest-expense line), the valuation uses the confirmed rate only where the derivation cannot run — a filed line still outranks the assumption — and the refusal message now names the remedy. **(b) Done.** The gate names the dependency before approval: `assemble` puts `cost_of_debt` in `outstanding` with a reason stating the condition and what to enter, the record carries it through `refreshed_payload` so the payload hash keeps its byte-for-byte property, and the gate page's existing supply form picks it up like any other gap. **(c) Done, on the operator's direction (ADR 0067).** Where the filer tags the cash outflow — CHRW tags `InterestPaid` — the gate now proposes cash interest paid over average debt as a starting value, and the justification names the substitution and the direction of its error: a cash-basis proxy, not the accrual cost of debt, differing by payment timing and by capitalised interest, so it can sit either side of the true rate. Four containments in code: proposed and never confirmed; offered only where the real derivation is impossible; declined rather than overclaimed when there is no cash figure, no debt, a negative charge or an implausible rate; and the justification's wording pinned by test rather than merely required to exist. |
| R18 | **A share-based-compensation risk-free rate must never be mapped to `risk_free_rate`** | The sweep surfaced `ShareBasedCompensationArrangementByShareBasedPaymentAwardFairValueAssumptionsRiskFreeInterestRate{Maximum,Minimum}`, correctly unmapped today. They are option-pricing inputs for a specific grant on a specific date, not a market risk-free rate, and the platform is currently *asking* for a `risk_free_rate` it cannot derive. The two facts sitting in the same database under a nearly identical name is exactly the shape of a future well-meant mapping that would quietly discount a whole valuation at an option model's assumption. Recorded here so that closing the `risk_free_rate` gap is never done this way. No code change; this row is the guard.
| **R14** | **Calculations appear to be recorded more than once** | **Two defects, both real, neither of them the ones this row guessed at. Done.** 118 rows for 5 periods, with visible exact duplicates — `ebitda 897779000` twice, `ebitda 1173367000` twice, `depreciation_rate 0.581915801094` twice. The row's two hypotheses were that the calculate step runs per-statement as well as per-period, or that a re-run appended rather than replaced; both were checked against the code and **both are wrong** — the analysis loop strikes each period exactly once, and the recomputations that feed the surfaces use their own throwaway contexts and are never persisted. **What actually happens is legitimate re-striking.** `@traced` appends a row per call, and several callers ask for the same arithmetic honestly: the ratio suite computes `ebitda` for its margin and again inside `net_debt_to_ebitda`; `days_outstanding` is struck three times as the receivable, inventory and payable ratios and all three again inside the cash conversion cycle; a paired quality signal recomputes its own base. Same name, same inputs, same result, same period — one derivation, and nothing in the row to say which one a citation meant. **Fix (a):** `CalculationContext.add` now memoises on identity — every field but the id, so a different period, input or result still gets its own row — and returns the row already struck instead of appending a second. Lineage stays a tree: the second caller's figure is attributed to the first caller's record, so one figure has one id. On a three-year levered scene that is 90 rows down to 74. **Fix (b), which the first fix exposed:** the paired quality signals struck the *prior* period's base under the *current* period's stamp, so FY2022's working-capital intensity was filed under FY2023 — a mislabelled row that also happened to look like a duplicate of the figure the prior period's own pass had already struck correctly. `_paired` now scopes the opening leg with `context.stamped(prior_period)`, which both fixes the label and lets the memo collapse it against the right row. **What it cost:** row count stops being a measure of work done, and two tests had been using it as one — the DCF grid proved its nine cells were nine complete valuations by counting twenty-seven free-cash-flow rows, and the comps band proved its ends were traced by counting two. Both now assert what actually varies (nine distinct terminal values and discount factors; each end of the band resolving to a recorded output), which states the same property without depending on how the ledger counts. The doctrine is ADR 0068. |
| R15 | **Point-in-time was off, and the report states it without saying what it costs** | The front matter carries "POINT-IN-TIME off" beside the as-of date. A reader who does not know the platform cannot tell that this switches off the guarantee that nothing published after the as-of date informed the note. Fix: when point-in-time is off, the coverage notice should say so in a sentence — the setting is not self-explanatory and it changes what the document is. **Done.** The notice states it plainly: point-in-time enforcement was off, so material published after the as-of date may have informed the note — and a run that is otherwise full still carries the notice for this alone. |
| ~~R17~~ | ~~**A hand-supplied assumption is saved but not usable**~~ | **Withdrawn — no evidence behind it.** The theory was that `create_assumption_page` produces a proposal rather than a confirmation, so a typed value would be saved and then filtered out by `confirmed_values`. The mechanism is real, but it is not what happened: the operator confirmed all three, and the database shows them approved with the operator's own address against each. The separate confirm step was found, understood and used. Nothing here needs changing, and the doctrine — typing a value and agreeing the run may rest on it are separate acts — stands unchallenged by this run.
| R16 | **`depreciation_rate` of 0.65 to 0.88 is arithmetically fine and reads as alarming** | Depreciation and amortisation running at two-thirds to seven-eighths of net PP&E annually. For an asset-light broker whose D&A is largely intangible amortisation this is defensible, but the label promises a depreciation rate on fixed assets and delivers something else. Fix: either rename the calculation to what it measures, or exclude intangible amortisation from the numerator, or add the denominator to the label. **Done — renamed.** The reader-facing labels are now "D&A to net PP&E" and "Change in D&A to net PP&E"; the arithmetic and the stored key are untouched, so the ledger's history stays comparable. This is a candidate for the plausibility floor's next relation only if the first two options fail — the number is odd, not impossible, and A61's set must stay closed and small (ADR 0066). |

## The plan

Ordered by what most improves the next report per unit of work.

**First — the cost of debt (R13). Done, all three parts.** Not a readability finding
at all: it is why three consecutive runs have shipped without a valuation, and on this
run it discarded eleven confirmed assumptions over a line the filer never tagged. There
was nothing to map — `cost_of_debt` is now suppliable by hand, exactly as the risk-free
rate already is, and the gate names the dependency before approval for the runs where
the derivation cannot happen. A filer's presentation choice can no longer defeat a
forecast the operator has otherwise fully specified. Part (c) is done too: on the operator's
direction the gate proposes the cash-basis rate where one is filed, labelled as the
substitution it is (ADR 0067).

**Second — the register fixes (R1, R2, R3, R4, R6, R10).** These are the ones that decide
whether the document reads as research. They are prompt, template and copy changes with
no architectural content, they share one theme, and together they remove every sentence
in the report that is about the report:

1. **Done (R1/R2/R3).** Salvage disclosure moved out of the section body into the
   coverage notice and the appendix, lost the ADR reference, and its label is derived
   from its actual cause.
2. **Done (R4).** The DCF standalone text and the method note are rewritten in the
   report's voice, pointing at the recorded missing input.
3. **Done (R6).** Not-generated placeholders gain a cause and a consequence, read off
   the stored refusal kinds.
4. **Done (R10).** The comps sentence gets house style and loses the withheld-copy
   clause where nothing is withheld.

The register batch is complete: every sentence in the report that was about the report
is gone or moved to where disclosure belongs.

**Third — the integrity loop (R9). Done.** The origin was the limitations appendix
reproducing failed sections' diagnostics verbatim; the appendix now carries cause
sentences, and the findings table quotes its strings as code spans the scan ignores. A
check can no longer fail on its own output.

**Fourth — the approval-page surfaces (R11, R12). Done.** The calculations table dates
and formats every figure, and thesis disagreements read as what they are instead of
"0 thesis".

**Fifth — the evidence-and-selection issues (R7, R8, R14). All three done, and each was
somewhere other than where its row expected.** The duplicate calculations were two defects
in the ledger rather than one in the run: a row appended per call where several callers
legitimately strike the same arithmetic, and a paired quality signal stamping the prior
period's figure with the current period's label. The catalysts section needed a refusal in
code as well as the contract wording, because what the prompt was discouraging was the
section's entire content. And the scenario anchoring had no builder to look at: the section
wrote about the only ratios it was shown, because calculations reached it in strike order
and strike order is oldest-first. Two of the three rows record a wrong hypothesis
disproved; that is the pattern in this batch, not an accident of it.

**Not scheduled: R15, R16. Both done** — they rode with the R9 and approval-page batch.

## How to tell it worked

The next run should be readable end to end without the reader learning anything about
the platform. Concretely, in the rendered report:

- No occurrence of "ADR", "the writing model", "this run", "word budget", "the platform",
  or "the operator's own copy".
- Every section that was shortened or is missing says so once, in the coverage notice,
  in a sentence naming the cause in the reader's terms.
- The valuation section names the input the run actually lacked and what its absence
  costs. *(Not "the three cost-of-capital assumptions": R5's withdrawal established that
  the missing input varies — for CHRW it was the cost of debt with all eleven others
  confirmed — so the section points at the recorded reason rather than a fixed list.)*
- `presentation_integrity` either passes, or fails on something that is not its own
  findings table.
- Catalysts contains no catalyst whose event is "the company will file a periodic report".
- The next run of a company carrying debt produces a discounted cash flow, or names the
  single input it still lacks *at the gate*, while the operator can still act on it.

**Done: the register check is part of `presentation_integrity`.** A closed vocabulary of
process language, scanned over the same visible text as the other defect classes, at the
same threshold of zero. The selection rule is strict — every entry is a phrase that
*cannot* appear in prose about a company, so the check has no false positives by
construction — and it is what excludes the obvious candidates: bare "ADR" (an American
Depositary Receipt is an ordinary subject for an equity note), "the platform" (a platform
company's own platform is what a report about it discusses), and "the model" (the sector
block's "The model is blocked for this sector" means the valuation model). What the
vocabulary does hold permanently is the CHRW note's own three classes: `ADR 0057`,
`word budget`, `writing model`, plus internal gap references.

Those excluded phrases are still defects where they occur; they are simply not safely
detectable by a word list, so they are fixed in the prose that produced them instead —
the valuation's missing-line refusal and two sector warnings no longer say "this platform
maps" or "not implemented in this build" to a reader.

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
