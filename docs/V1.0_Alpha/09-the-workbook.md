# The model workbook

*The spreadsheet shipped with every report: sheet by sheet, cell by cell, and the rules that
generate it. Specification for F5.*

**What it is for.** The target user lives in Excel. A report they cannot poke at is a report they
half-trust; a model they can change is one they can argue with. And when they email it to a
colleague, the provenance tab travels with it.

**The constraint that shapes everything.** It carries **formulas, not values**. A workbook of
pasted numbers is a screenshot with extra steps.

---

## 1 · The sheets

Six, in tab order. Each is generated from the calculation registry, which already stores every
formula, its inputs, their units and their sources.

| # | Tab | What it holds |
|---|---|---|
| 1 | **Read me** | What this file is, what it is not, and the one-sentence warning |
| 2 | **Inputs** | Every assumption and driver, in one place. The only sheet with blue cells |
| 3 | **DCF** | The forecast, the discounting, the bridge to equity, the per-share result |
| 4 | **Sensitivity** | The two-axis grid, as a real Excel data table |
| 5 | **Comparables** | The peer table, with the subject's own multiples |
| 6 | **Provenance** | Every input, its source document, its retrieval date, and the stamps |

A model the platform did not produce does not get a sheet. A bank's run ships **Residual income**
in place of **DCF**; a run with no comparables ships no **Comparables** tab rather than an empty
one.

## 2 · The convention, because the audience knows it

| Cell | Format | Meaning |
|---|---|---|
| **Blue text, white fill** | `#0000FF` | An input. Change it |
| **Black text** | `#000000` | A formula. Do not type over it |
| **Green text** | `#008000` | A reference to another sheet |
| Grey italic | — | A label or a note |

This is the standard financial-modelling convention and it is not negotiable — a user who knows
it will trust the file in three seconds, and a user who does not will learn it from the Read-me
tab.

**Locking.** Computed cells are locked, the sheet is protected, and **the password is empty**.
The point is to mark the boundary, not to defend it: a user who wants to change a formula should
be able to, having been told they are leaving the platform's guarantee behind.

## 3 · Sheet by sheet

### 3.1 Read me

Six lines, no formatting cleverness:

> **Microsoft Corporation — valuation model**
> Generated from report `{id}` on 12 September 2026 · code version `4c63dbfc4794`
>
> **Blue cells are inputs. Change them and the model recomputes.**
> Black cells are formulas generated from the platform's own calculation record; the figures they
> produce match the report exactly as generated.
>
> **The moment you change a blue cell, this becomes your model rather than the platform's
> record.** Nothing here is re-verified after that, and the report's footnotes no longer describe
> this file. That is the intended use — it is simply worth knowing which side of the line you are
> on.
>
> This is not investment advice.

### 3.2 Inputs

One table, every driver the model takes, each on its own row:

| Column | Contents |
|---|---|
| A | Name, in the operator's language — *"Risk-free rate"*, not `risk_free_rate` |
| B | **The value. Blue. The only editable cells in the file** |
| C | Unit — `%`, `USD m`, `years`, `×` |
| D | Where it came from — *"you, at the assumptions gate"* · *"proposed and you confirmed"* · *"the FY2026 10-K"* |
| E | A named range, so every other sheet refers to `Risk_free_rate` rather than `Inputs!$B$4` |

**Every input is a named range.** It makes the DCF sheet readable, it makes a user's edit
propagate everywhere, and it is what stops the file becoming unmaintainable the first time
somebody inserts a row.

Grouped: cost of capital · forecast drivers · terminal assumptions · share count and net debt.

### 3.3 DCF

Years across, line items down. The forecast years come from the stored calculation rows, so the
sheet has exactly the horizon the report had.

```
                       FY2027   FY2028   FY2029   FY2030   FY2031
Revenue                =E12*(1+Revenue_growth)   →  →  →  →
Operating margin       =Operating_margin         →  →  →  →
EBIT                   =E13*E14                  →  →  →  →
Tax                    =-E15*Tax_rate            →  →  →  →
NOPAT                  =E15+E16
D&A                    =E13*DA_pct
Capex                  =-E13*Capex_pct
Change in WC           =-(E13-D13)*WC_pct
Free cash flow         =SUM(E17:E20)
Discount factor        =1/(1+WACC)^E$11
PV of FCF              =E21*E22
```

Then the terminal value **by both methods, side by side** — perpetuity growth and exit multiple —
because the report carries both and their spread is the honest width of the answer. Then the
bridge: enterprise value, less net debt, to equity value, over diluted shares, to value per
share.

**Every figure the report states appears here and must tie exactly.** A generated workbook whose
per-share figure differs from the report's is a defect, and §5 says how it is caught.

### 3.4 Sensitivity

A real Excel two-input data table — `Data → What-If Analysis → Data Table` — with WACC down the
side and terminal growth across, pointing at the DCF's per-share cell.

Not a pasted grid of the platform's 81 numbers. A pasted grid stops being true the moment a blue
cell changes, which is the one thing this file exists to allow.

Conditional formatting: a three-colour scale, and the cell matching the report's base case
outlined in black.

### 3.5 Comparables

The peer table as the report has it: each peer, its multiples where held, and **the subject's own
P/E and EV/EBITDA**, which the platform computes on every run.

Where a peer's figures are absent the row says so in the cell — *"no filings held"* — rather than
showing a blank, for the same reason the report withholds rather than blanks.

### 3.6 Provenance

The tab that makes this file different from every other spreadsheet anybody will email that week.

| Column | Contents |
|---|---|
| A | Input name |
| B | Value as generated |
| C | Source — the document's title |
| D | Publisher |
| E | Published date · retrieved date |
| F | Tier |
| G | Artefact digest |

And, at the top, the stamps: report id, job id, **code version**, generation timestamp, and the
content hash of the report this was generated from.

> Every figure above was produced by `aer.calc` from the sources listed. To verify one, open the
> report and follow the footnote of the same name — it resolves to the archived document this
> digest identifies.

## 4 · Generation

**Library.** `openpyxl`. Nothing in the project writes spreadsheets today, so it is a new
dependency; it is pure Python, actively maintained, and writes formulas as strings without
needing Excel present.

**Module.** `aer.export.workbook`, beside the other renderers, reading the same run export the
Markdown and PDF renderers read. It imports nothing from `aer.agents` — a model is never involved
in generating this file.

**Formula generation.** Each registry entry carries a formula in the platform's own notation. The
exporter maps that notation to Excel's, substituting named ranges for inputs and cell references
for intermediate rows. **Where a formula cannot be expressed in Excel** — a lookup into stored
facts, a conditional the registry expresses in Python — the cell carries the computed value,
black, with a comment naming the calculation it came from. Those cells are counted and reported
on the Read-me tab: *"Four figures are values rather than formulas; see the notes on each."*

**Determinism.** The same run produces a byte-identical workbook, which is what makes it testable.

## 5 · How it is proved

| Check | How |
|---|---|
| **The workbook ties to the report** | Generate from a stored run, open with `openpyxl` in data-only mode after recalculation in LibreOffice headless, and assert every headline figure equals the report's to the penny |
| **It recomputes correctly** | Change `Revenue_growth` by one percentage point, recalculate, and assert the per-share result matches what the platform's own sensitivity grid says for that input. **This is the test that proves the file is a model rather than a picture** |
| **No formula is broken** | No cell evaluates to an Excel error |
| **Provenance is complete** | Every blue cell has a provenance row; every provenance row names a document held in the store |
| **It opens** | In LibreOffice, headless, without a repair dialogue |

## 6 · What it deliberately does not do

- **No macros.** A file that asks to enable macros is a file nobody in a regulated environment
  can open.
- **No live links.** No web queries, no external references, no add-ins. It works on a laptop with
  no network, for ever.
- **No charts**, in the first version. The report has the exhibits; the workbook is for arguing
  with the numbers.
- **No writing back.** Changing the workbook never returns anything to the platform. If the
  operator's changed assumptions are worth keeping, they belong in a new run with those
  assumptions at the gate, where they become part of the record.
