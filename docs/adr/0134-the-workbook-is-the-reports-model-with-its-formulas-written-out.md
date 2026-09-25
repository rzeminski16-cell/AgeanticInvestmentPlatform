# ADR 0134 — The workbook is the report's model with its formulas written out, and it says where the record ends

- **Status:** Accepted (25 September 2026)
- **Date:** 2026-09-25
- **Implements:** `docs/V1.0_Alpha/04-feature-specifications.md` F5, which the operator brought
  back into V1.0 on 25 September 2026.
- **Applies:** ADR 0030's amendment of 2026-08-09 and ADR 0034 (a computed figure may be
  published, the series may not), ADR 0132 §3 (the preview strike), ADR 0133 (the calculator:
  the same model, inside the app). Invariant 3 is untouched: the workbook is written from
  stored facts, recorded calculations and confirmed assumptions, and reaches no report.

## Context

The target reader lives in a spreadsheet. The report carries its valuation as figures with
footnotes that lead to their formulas, which is the right form for a record and the wrong one
for somebody who wants to change the growth rate and watch the answer move. The calculator
(ADR 0133) answers that inside the platform. F5 answers it for a reader who takes the model
away, and for the colleague they send it to.

The calculation registry holds everything a spreadsheet needs for each row — the formula, the
inputs with their units and sources, the code version — except a formula in a spreadsheet's
syntax, because nothing has ever needed one.

## Decision

### 1. One workbook per approved report, archived beside the PDF

The render step writes the workbook when the report is approved and stores it as a hashed
artefact, as it stores the PDF; `reports.workbook_artefact_id` (migration 0089) points at it.
The download is the archived file, never a regeneration, by the rule the report's other
notations already follow. The render step's output records the workbook's digest, or the
sentence saying why there is none, so the run export carries either.

There is none for a run that did not value the company by a discounted cash flow (a bank's
residual income, a run that stopped short of the value step), for a report approved before
this change, and for the two cases in §2. A refusal is recorded, never raised: the report is
the record and the workbook one more way to read it, so a model that cannot be written out
leaves an approved report without a workbook, never without its render.

### 2. It is proved against the report before it is written

The model is struck again from the run's confirmed values through `aer.services.preview.strike`
(ADR 0132 §3), and the result is held against the report's recorded base case at the ledger's
stored precision, using the calculator's own check. A run whose filings a later run has moved
gets no workbook: one that did not reproduce its report would be a second model with the
report's name on it.

That check strikes the model in Python, so it cannot see the sheet leave something out. Every
base case today bridges enterprise to equity value by net debt alone; a run that ever carries
a further bridge item is refused a workbook rather than given one that drops it.

### 3. The model sheet reproduces the report with live formulas

- **Inputs are blue and unlocked.** Base revenue, the share count, the debt and interest lines
  behind the cost of debt (or the confirmed rate where no interest is filed), opening working
  capital and net debt, the market capitalisation, the confirmed rates and multiple, and every
  driver, year by year.
- **Everything else is black and locked, with no password.** The cost of capital chain, the
  forecast table, both terminal values, the enterprise, equity and per-share values, the
  terminal share, the growth and multiple each method implies, and the gap between the
  methods. The lock marks the boundary; it does not prevent anybody crossing it.
- **The report's own figures sit beside the live ones in grey**: the per-share values as
  recorded, so a reader can see the sheet reproduces the report before changing anything and
  how far they have moved it after.
- **The formulas are `aer.calc.dcf`'s, written in the sheet's syntax.** Each block names the
  traced function it mirrors. What holds them to those functions is §6's test, not a mapping
  from the registry: the registry's formulas are prose for a reader, not expressions a
  spreadsheet can evaluate.
- **The platform's terminal refusals are written out.** The sheet computes whatever it is
  given, where `aer.calc.dcf` refuses a growth rate at or above the discount rate, a final year
  of negative cash flow, non-positive EBITDA or a multiple outside its range. A row says, per
  method, whether the platform would value the sheet's current inputs. The drivers' own ranges
  are not repeated; the calculator refuses those in the gate's words.

### 4. The other sheets carry the record as values

- **Sensitivity.** The report's recorded grids, as values, their axes named in the words the
  model sheet labels the same inputs with.
- **Comparables.** The comparison as the shareable report prints it, read through the report's
  own reader (`comps_for`), which asks the licence question at the shareable audience — the
  workbook's audience, because it is mailed on. Where the licence permits, each company's
  multiples or the reason each has none; either way, every confirmed peer and why a person
  chose it, and who is not in the table and why.
- **Sources.** Every input with where it came from: the filing, its period, filing date and
  accession, and the date and address it was retrieved from, for a filed line; the date and
  the reason, for a confirmed assumption; the lines it was computed from, for a calculated one.
  The sheet stamps the report, its content hash, the run, the code version and the approval.
  It names nobody: the confirmer is recorded by e-mail address, and the report names nobody
  either.
- **Where the record ends.** The model and sources sheets both say, in one sentence, that once
  a figure is changed the sheet is the reader's model and no longer the platform's record.

### 5. What it may not carry

The share price is not exported. It is the vendor's figure, and only figures computed from it
may be published. The market capitalisation computed from it may be, so the discount rate is
live; the distance from the price is not recomputed and stays in the report. The comparables
carry multiples only, and the series and its charts never go in.

### 6. Proved in a real spreadsheet engine, and byte-stable

The tests recompute the workbook in LibreOffice's headless calculator and read the per-share
values back; they must equal the ledger's to a part in a billion, which is binary floating
point against decimal and far inside a cent. They then change inputs and recompute:

- **Terminal growth.** The perpetuity value lands on the report's own sensitivity cell for
  that growth rate at the report's discount rate.
- **Revenue growth.** The result matches the calculator's strike for the same change.

F5's "done when" names revenue growth against the grid. The grid does not vary revenue growth,
so no stored record predicts that figure; the calculator's strike, which is the value step's
own arithmetic, is the nearest thing that does, and this ADR says so rather than pretending
otherwise.

Where LibreOffice is missing the recompute tests skip, except where `CI` is set: both CI jobs
that run the suite install `libreoffice-calc-nogui` and fail the tests rather than letting them
pass by not running.

The file is byte-stable, the PDF's rule: every date in it — the document properties and each
member of the archive — is the approval's, so the same rows give the same bytes and the
artefact's digest can be re-checked by writing the file again.

## As built

- `aer.render.workbook` is the pure builder: a `ModelWorkbook` in, the file's bytes out.
  `aer.services.workbook` decides what goes into one, and when there is none. The render step
  calls them through `_archive_workbook`, beside `_archive_pdf`.
- `aer.services.preview.Preview` gained the cost of capital taken apart, which the model sheet
  writes out.
- The calculator's reproduction check became public (`recorded_base`, `reproduces`,
  `figures_of`) so both surfaces hold the model to the report the same way.
- `GET /api/reports/{id}/download/xlsx` serves the archived file; the report page links it.
- **The garbage sweep reads its references off the schema.** `aer gc-artefacts` listed the
  columns that point at an artefact by hand, and the workbook's was the eighth and not on the
  list. The sweep purges an orphan's bytes before it deletes the row, so `--delete` would have
  taken every workbook's bytes and then failed on the foreign key, leaving each report
  pointing at nothing. It now treats every foreign key to `artefacts` as a reference, and a
  test holds the two to each other.
- The render step clears the workbook's reference with the PDF's when it runs again, so a
  second attempt that writes no workbook does not keep the first attempt's.
- Against the operator's database on 25 September 2026, the five approved discounted cash
  flows (MSFT three times, AZN twice) each recomputed in LibreOffice to their report's recorded
  per-share figures, and the bank (M&T) was refused a workbook with the sentence in §1.

**Not built, and why.**

- **No scenarios sheet.** F5 lists one, but nothing in the product creates a scenario: no page,
  route or step calls `create_scenario`. So every run has none, and the sheet would always be
  empty. It arrives with the first path that creates one.
- **No workbook for a residual-income run.** The operator asked for the discounted cash flow
  first, and the bank model's sheet is its own piece of work.

## Consequences

- `openpyxl` becomes a declared dependency (it was transitive, through arelle), and
  `types-openpyxl` a development one.
- The render step does one more archive at approval, and the report page offers one more
  download.
- CI installs LibreOffice's headless calculator in the main job and the nightly shuffled job.
