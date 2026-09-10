# How a research report is actually generated

*The specification the baseline prompt was written from. Extracted from the code on
2026-09-10, with file references so a reader can check any line of it. Everything here is
what the platform **does**, not what it intends to do — where the two differ, the roadmap
says so and this document follows the code.*

---

## 1. The unit of work

One **run** = one research request in, one cited report out. It hangs off a `work_orders`
row (ADR 0072) carrying who asked, what it may spend, the as-of date its evidence is judged
against, and whether it is archived.

**The operator supplies** (`core/schemas/request.py:ResearchRequestCreate`):

| Input | Notes |
|---|---|
| `company_name`, `ticker`, `exchange`, `isin` | UK or US listing |
| `base_currency`, `reporting_currency` | `Decimal` throughout; FX ships its rate (ADR 0026) |
| `investment_horizon_months`, `horizon_label` | The mandate |
| `analysis_mode` | `quick` / `standard` / `full`; defaults to `full` |
| `point_in_time` | Defaults **on** |
| `undated_sources_admissible` | Split from `point_in_time` by ADR 0111 |
| `risk_tolerance`, `liquidity_constraint_gbp`, `esg_sensitivity` | Operator preferences |
| `focus_questions`, `excluded_sources` | Bounded lists |
| `max_cost_gbp` | The run's own ceiling |

**There is no operator-supplied as-of date.** ADR 0110: the run is dated by the platform at
the moment it is commissioned. Research about a past quarter is a capability that was
removed deliberately.

## 2. The steps, in order

`workflow/workflows/vertical_slice_v1.py:build_steps()`, executed by `workflow/engine.py`.
Steps are recorded, resumable and independently budgeted.

| # | Step | Owner | Model spend | What it does |
|---|---|---|---|---|
| 1 | `plan` | `agents/planner` | ~£0.15 | Proposes sections, sources, known risks, confidence. **Never a figure, never a fact.** |
| 2 | `critique_plan` | `agents/plan_critic` | ~£0.30 | Attacks the plan from a separate context; one planner revision at severity ≥ 3 (ADR 0091) |
| 3 | **`gate_plan`** | human | — | Universal gate 1 |
| 4 | `acquire` | `sources/sec`, `sources/uk` | — | Filings fetched, hashed, stored |
| 5 | `classify` | `services` | — | Filing types; may raise the sector gate |
| 6 | `gate_sector_specialist` | human | — | Conditional |
| 7 | `propose_peers` | `agents/peers` + registry | ~£0.02 | Model names tickers; **code resolves them** against the regulator's registry (ADR 0059). Skipped to what the platform already holds when no price feed is configured |
| 8 | `gate_peer_set` | human | — | Conditional |
| 9 | `propose_themes` | `agents/themes` | ~£0.02 | Bounded slate (ADR 0065) |
| 10 | `gate_theme_set` | human | — | Conditional |
| 11 | `extract` | `extract/` | — | iXBRL / PDF / HTML → text with locators |
| 12 | `gate_unmapped_concepts` | human | — | Conditional: filing tags the concept map does not know |
| 13 | `acquire_prices` | `sources/eodhd` | — | After extract, because the **filed** share count beats the vendor's |
| 14 | `calculate` | `calc/` | — | Statements, ratios, quality signals — all traced |
| 15 | `research_company/_industry/_macro/_recent_developments/_technical_context` | `agents/worker` ×5 parallel | ~£0.10 each | Findings on named evidence; **tool requests executed by code** (ADR 0036) |
| 16 | `comps` | `calc/comps` | — | Withholds rather than publishes licensed rows |
| 17 | `propose_assumptions` | `agents/assumptions` | ~£0.20 | **Only the two numbers no filing answers** (ADR 0046) |
| 18 | **`gate_assumptions`** | human | — | The one gate approving work not yet done |
| 19 | `value` | `calc/wacc`, `calc/dcf` | — | Runs only on confirmed assumptions |
| 20 | `draft` | `agents/section_writer` | **~£5 — the largest** | One call per model-written section |
| 21 | `validate` | `verify/`, `agents/validator` | small | Deterministic checks; the model only *advises* (ADR 0038) |
| 22 | `red_team` | `agents/red_team` | ~£1 | Attacks the draft from a separate context (ADR 0039) |
| 23 | `revise` | `services/revision` | ~£1.50 | Redrafts sections that material challenges attack, **once** |
| 24 | `verdict` | `agents/verdict` | ~£0.01 | One sentence over the frozen draft; never evidence |
| 25 | `brief_challenges` | `agents/challenge_brief` | ~£0.02 | Advisory; reaches no report |
| 26 | **`gate_final`** | human | — | Universal gate 2 |
| 27 | `render` | `render/`, `charts/` | — | Stored sections → Markdown, HTML, PDF, each hashed |

Seven places can pause for a human; two of them always do. Both universal gates show a
payload **and a hash of that payload**, and the approval carries the hash back — if what
the run produced changed between the page being served and the button being pressed, the
workflow refuses to continue.

## 3. The division of labour

`CLAUDE.md`, ADR 0003. **Deterministic Python owns every number and every fact. The model
owns planning, interpretation, comparison, adversarial challenge and writing.**

Concretely, the model in this system:

- never performs arithmetic — not a sum, a difference, a growth rate or a share;
- never asserts a figure — it names a stored fact or a recorded calculation by id;
- never confirms a citation — it proposes one and code re-reads the artefact;
- never chooses a valuation model — the sector mandate does, and blocks rather than footnotes;
- never calls a tool — it *requests* one in a schema, and code decides (ADR 0036);
- never sets a rating or resolves a disagreement.

## 4. The section spine

Eighteen built-in sections (`tests/test_section_spine.py`), sixteen model-written and two
filled by code. Sections are **data, not code** (ADR 0013): a `section_definitions` row
carries the title, position, output contract, evidence policy, token budget and
applicability. The registry pins the highest version of each key at run start.

| Pos | Key | Word budget¹ | Evidence policy | Quick mode |
|---|---|---|---|---|
| 100 | `executive_summary` | 363 | standard, one-pager | ✓ |
| 110 | `investment_thesis` | 580 | forward-looking | ✓ |
| 120 | `business_overview` | 580 | standard | ✓ |
| 130 | `segment_analysis` | 508 | standard | deep-dive only |
| 140 | `industry_landscape` | 508 | standard | deep-dive only |
| 150 | `management_governance` | 435 | standard | deep-dive only |
| 200 | `historical_financial_analysis` | 725 | standard, **annual facts only** | ✓ |
| 210 | `earnings_quality` | 508 | standard, **annual facts only** | deep-dive only |
| 220 | `balance_sheet_liquidity` | 508 | standard | ✓ |
| 230 | `cash_flow_analysis` | 508 | standard | ✓ |
| 240 | `capital_allocation` | 435 | standard | deep-dive only |
| 250 | `growth_outlook` | 508 | forward-looking | deep-dive only |
| 300 | `valuation_dcf` | 725 | forward-looking, **method platform-filled** | ✓ |
| 310 | `scenarios_sensitivities` | 508 | forward-looking | deep-dive only |
| 400 | `key_risks` | 580 | forward-looking, one-pager | ✓ |
| 410 | `catalysts` | 363 | forward-looking | deep-dive only |
| 900 | `prior_research_comparison` | — | **deterministic** | deep-dive only |
| 910 | `validation_disagreements` | — | **deterministic** | ✓ |

¹ Standard mode, after migration 0045 scaled every built-in budget by 1.45. `quick` ×0.6,
`full` ×1.4 (`sections/evidence.py:_MODE_FACTORS`). A section overrunning **1.25× its
budget** is refused or cut — and the prompt is told the budget, never the headroom, because
a writer told the headroom writes to it.

**The evidence policy floor** most sections inherit: `min_sources: 1`,
`max_tier: T4_LICENSED_MARKET`, `requires_primary: true`, `allow_forward_looking: false`.
Sections whose subject is inherently prospective get `allow_forward_looking: true`; a
forward-looking claim still carries a stated **basis** instead of a citation.

**The valuation section is the exception worth knowing** (ADR 0063, migration 0044). Its
method fields — how the figures were produced, every cost-of-capital component with how it
was set, the forecast drivers, both terminal methods, the recorded caveats — are
`platform_filled`. `sections/valuation_method.py` renders them from the calculation ledger.
The model's schema never carries them, because the first complete report's DCF section
described beta regressions and bond-yield curves the run never executed, and every existing
defence passed it: **a section can evade the whole validation apparatus by being
confidently qualitative.**

## 5. Evidence: tiers, point-in-time, and what a citation is

`core/enums.py:SourceTier` — ordered, and **the lower number wins when two sources
disagree**, which is what makes conflict resolution a comparison rather than a judgement:

| Tier | What | Standing |
|---|---|---|
| T1 | SEC EDGAR, XBRL, Companies House, RNS | Authoritative for reported financials |
| T2 | Issuer-hosted: annual report PDFs, presentations, transcripts | Authoritative where T1 does not contradict |
| T3 | FRED, ONS, BoE, BLS, Eurostat, OECD | Authoritative for macro |
| T4 | Licensed market data: EOD prices, corporate actions | Authoritative for prices and returns |
| T5 | Reputable secondary reporting | **Never the sole support for a number** |
| T6 | Blogs, forums, own prior notes | Hypothesis generation only, **never citable** |

**Point-in-time is enforced at acquisition, in code** (ADR 0010, `sources/sec/pit.py`) — it
is *selection*, not post-hoc filtering. Nothing published after the as-of date may support a
claim. A source with no stated publication date is admitted (when the request allows) and
every section resting on one carries a dagger and a legend.

**The provenance chain**, which is the product:

```
figure in a section
  → claim        (a numeric claim names exactly one fact or calculation)
  → citation     (verified by code re-reading the artefact by hash)
  → extraction   (text with locators — ADR 0017, so a parser upgrade cannot move citations)
  → artefact     (content-addressed by hash)
  → the archived bytes
```

Verification (`verify/citations.py`) re-opens the artefact and checks the excerpt is really
there. The model may propose; only code confirms (ADR 0018).

**The numeral scan** (`core/section_output.py`) walks every string and number in a
section's content and demands each numeral token also appear in a numeric claim naming a
stored fact or calculation. Three carve-outs, each by *span* and each an operator decision
on the record: a recognisable date or document reference ("March 2026", "Q3 2025",
"Item 2.02", "CIK 0000320193" — ADR 0054), a plain count of the prose's own nouns
(ADR 0057), and the number inside a product name (ADR 0060). A bare unanchored year is
treated as a quantity and refused. A quoted figure is checked against the **value** of the
figure its claim names, not its spelling (ADR 0097): "$331.8 billion" over a stored
`331839000000` is that figure; "$412.6 billion" is not.

## 6. The arithmetic

Everything in `calc/` is pure, `mypy --strict`, `Decimal`, no I/O, no clock, no globals.
Every function is `@traced`: it persists its formula, its inputs each with a unit and a
source, and the code version that produced it.

- **Units are carried through** (`calc/units.py`). A mismatch **raises**; it never coerces.
- **Statements** normalised from filed facts; **ratios** across six families — margin,
  return, liquidity, leverage, coverage, efficiency; **earnings quality** signals with
  thresholds stated as judgements that can be argued with (accruals ratio, cash conversion,
  capex/depreciation, depreciation rate, working-capital intensity, interest capitalisation).
- **WACC** (`calc/wacc.py`): **no parameter has a default.** A missing risk-free rate is a
  `TypeError`, not an 8% nobody chose. Risk-free from a macro vintage; ERP and beta are
  confirmed assumptions; cost of debt computed from interest over average debt or supplied;
  tax from the filing. **No size premium and no country premium** — a reviewer facing
  `rf + β·erp + size + country` has to argue with four numbers, two chosen by convention.
- **DCF** (`calc/dcf.py`): every driver is a confirmed assumption **per forecast year**.
  **Both terminal values, always, side by side** — Gordon growth and exit multiple, each
  with its *implied* version of the other's parameter. The terminal share of enterprise
  value appears on every result. It **refuses**: terminal growth at or above the discount
  rate, a Gordon terminal value on a negative final cash flow, an exit multiple on negative
  EBITDA, a per-share figure with no shares. Enterprise value is *not* monotonic in revenue
  growth, and where capital intensity exceeds operating margin, growth destroys value —
  which is the correct answer.
- **Sensitivity** is a grid of up to 9×9 = 81 independently recorded valuations (ADR 0028);
  every cell names its calculation.
- **Scenarios are diffs**, not copies (`services/scenarios.py`): a corrected base assumption
  reaches every scenario that did not explicitly disagree with it. An override of a name no
  confirmed assumption holds is refused.
- **Comps** (`calc/comps.py`): every multiple is dimensionless and the unit algebra proves
  it; a non-positive denominator has **no** multiple (not a cheap one); every multiple names
  its basis and its date. Peer period drift is bounded.
- **The sector mandate blocks rather than footnotes** (ADR 0029, 0070): a bank has no
  classified balance sheet, so `ValuationMandate` cannot be constructed for FCFF and the DCF
  raises at the call site. A confirmed bank is valued on residual income over book value,
  and its grid varies the spread (ADR 0101).

## 7. Assumptions: six derived, two proposed

ADR 0046. A DCF needs eight assumptions minimum — five driver paths, `tax_rate`,
`terminal_growth`, `exit_multiple`. **Six are not opinions**: revenue growth, EBIT margin,
capex intensity, depreciation intensity, working-capital intensity and the effective tax
rate all have a history in the filings the run already acquired, so code proposes them in
flat form with a stated basis.

**Two are opinions and no amount of history makes them otherwise** — the perpetual growth
rate and the exit multiple. A dedicated role proposes exactly those two, with justifications,
bounded (terminal growth below the discount rate and inside a floor/ceiling; exit multiple
inside a wide band that catches slips rather than expressing a view), and **a person confirms
every value before any calculation reads it.** An unconfirmed assumption cannot become a
`Quantity`.

## 8. What is checked, and against what

Eleven run-time metrics (`eval/metrics.py:RUN_TIME`), each a number against a threshold,
shown at gate 2:

| Metric | Threshold |
|---|---|
| Citation accuracy | ≥ 0.98 |
| Hallucinated citation rate | = 0 |
| Temporal compliance | = 1 |
| Look-ahead recall | = 1 |
| Source coverage | ≥ 0.90 |
| Primary source ratio | ≥ 0.60 |
| Numerical consistency (max relative delta on recomputation) | ≤ 0.005 |
| Assumption completeness | = 1 |
| Presentation integrity | = 0 defects |
| Figure plausibility | = 0 impossible relations |
| Cited-figure agreement | = 0 disagreements |

Three of those thresholds were bought with real failures: a live note published a 172.1%
net margin with every other metric passing (figure plausibility), an MSFT note drafted a
quick ratio of 0.93 over a recorded 1.567 (cited-figure agreement), and a run shipped
literal `**` emphasis and raw UUIDs to a reader (presentation integrity).

**Plausibility** (`calc/plausibility.py`) is a closed set of relations that cannot hold on a
consolidated statement — income above revenue, a margin above one, turnover below a floor
on a large balance sheet. When one fails the whole block **withholds itself and says so**,
because traceability and sanity are different properties.

## 9. The adversary, and what happens to what it finds

`agents/red_team.py`, ADR 0039. The input type is **structurally incapable of carrying the
drafting context**: it has fields for the draft's recorded claims and an index of the run's
evidence, and no field for section prose, worker findings or coverage notes. Isolation is a
property of the type, not a discipline of the caller.

Each challenge names a dimension from a closed vocabulary (growth, profitability, valuation,
balance sheet, competitive position, governance, macro), a severity 1–5, and the evidence it
rests on **by id**. A challenge citing nothing is dropped — but only the challenge, not the
report, which was learned when one weak objection out of six killed a run eight pounds in.

Surviving challenges become `disagreements` rows: escalated to gate 2, **never
auto-resolved, both positions published**. A material challenge routes back to the section
that provoked it for exactly one redraft; a redraft that does not pass leaves the approved
draft standing (ADR 0098).

## 10. What the document is

`render/document.py` assembles once and every notation transcribes: header, sector note,
**at-a-glance block** (latest reported figures, annual revenue history, headline calculated
figures — all stored rows, all footnoted), the sections in position order, a withheld-comps
paragraph, globally numbered footnotes, the appendix, charts, the coverage note, the
consolidated limitations list, the undated-source legend, and the disclaimer:

> This is a personal research tool. It is **not** regulated investment advice, and nothing
> in this document is a recommendation to buy, sell or hold any security. Any rating
> expressed is a non-binding personal view.

Every footnote marker resolves to exactly one of two things and there is no third: a stored
fact (excerpt, archived document, hash) or a recorded calculation (formula, every input with
unit and source, code version). **A figure that is neither does not render.**

Comps are typed as `WithheldComps` at the document boundary — the type has no field for the
figures, so a caller wanting to put licensed multiples in a shareable report cannot do it by
passing a different argument (ADR 0030).

## 11. Containment

- **Skill files are additive-only** (ADR 0040, `core/skill_policy.py`): user-authored
  instructions may add requirements, never relax them, proved against a corpus of attacks
  that must *all* fail. Nothing user-authored reaches a system prompt.
- **Untrusted content is data, never instruction** (ADR 0036): fetched text is wrapped and
  labelled, and tool authorisation is enforced in code — the models have no tool-use surface
  at all, so injected text has nothing to invoke.
- **Prior research may shape questions, never answers** (ADR 0064): it enters as an
  untrusted quotation labelled `not_evidence`, and the citation verifier hard-rejects a
  claim resting on it regardless of what any prompt says.
- **Cost is metered and capped in code** (ADRs 0051–0053): every call is priced in pounds at
  the provider boundary against the run's cap and the month's. The engine refuses to start a
  step whose projected cost breaks a ceiling.

## 12. Honest limits

From `docs/product/what-it-is.md` and the roadmap: the **chain** is complete and the
**breadth** is still growing. The concept map does not know every filer's vocabulary;
scenarios do not exist for the bank model; there is no GBP risk-free rate because the Bank
of England's `robots.txt` disallows the route its own documentation describes, and reaching
around that would be circumvention. Two sources were declined on terms-of-service grounds
and stayed declined.
