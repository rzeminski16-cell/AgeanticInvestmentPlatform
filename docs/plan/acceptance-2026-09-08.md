# The first full acceptance pass — what it found, and the work it names

*The operator ran [`../developers/testing-by-hand.md`](../developers/testing-by-hand.md) end to
end on their own machine against commit `7c1a733`, including one live MSFT run — job
`37e21a60-f469-419c-9609-775a8102c9e8`, £7.5023, twenty-nine steps, stepped through by hand.
This is the reading of that pass: what it actually established, what it found broken, and the
order the work falls into. Written 2026-09-08.*

*It is a findings document, not the authority on scope. What survives review here moves into
[`ROADMAP.md`](ROADMAP.md), which stays the authority.*

---

## 1. The verdict, stated the way the scorecard asks for it

The scorecard's rule is not a score: **did any [B] row fail?** Two did, and both were written
down as `pass` with the failure noted beside them.

| # | Row | Written | What the same notes show |
|---|---|---|---|
| 6 | Both suites green | `pass` | `tests/e2e/test_research_journey.py` failed |
| 18 | The live run reaches a report you would act on | `pass` | `aer acceptance` printed *"The run does not meet the acceptance requirements"* |

So the honest verdict is **not ready**, on two rows — and that is the useful answer, because
both rows are now root-caused and neither is deep.

Everything else the pass established stands, and it is a great deal: migrations round-trip,
the static gates are clean, all eight blocking metrics pass on the golden corpus, the
application renders and degrades, a run steps through with every step readable, gates refuse
stale approvals, a footnote walks to bytes and a figure walks to its leaves, replay agrees,
the book computes from transactions and a split multiplies, every guard fires when provoked,
a killed worker resumes, and backup restores and verifies. **The chain is intact.** What
failed is at the edges of it.

Three things were checked independently while reading, because "ready for the real world" turns
on them and none is in the operator's notes. **Invariant 6 holds**: `BudgetExceededError` is
raised, not logged, both by `BudgetGuard.check` at the step boundary and per call at
`agents/base.py:494` — the caps refuse. **`aer backup` covers both halves** — the database and
the artefact store, refusing to overwrite and verifying what it wrote, because "a database
restored beside an empty store is a set of citations into nothing". And a sweep for `TODO`,
`FIXME`, `XXX`, `HACK` and `NotImplementedError` across `src/aer` returns **nothing on any run
path**: one docstring mentioning `\uXXXX`, and two abstract methods on the agent base class.
For a codebase of 116,000 lines that is unusual, and it is the reason the defects below are all
at boundaries rather than in half-built features.

### Row 6 is Windows-only

Both suites were re-run on Linux at the same commit, against a real PostgreSQL 16, a real
Redis and the pre-installed Chromium — **6,778 passed, 2 deselected, in 28 minutes**, and
**182 of 182** in the browser suite. The suite is green. Including the test that failed for
the operator:

```
tests/e2e/test_research_journey.py::TestTheWholeThing::test_from_the_front_page_to_a_finished_report
1 passed in 31.61s
```

`net::ERR_ABORTED` on the second navigation is
therefore a property of the operator's Windows host, not of the application. That downgrades
it from "the product is broken" to "the acceptance pass cannot go green on the machine the
operator actually uses", which is still a blocker for them and is a different fix. It is the
one finding in this document that needs the operator's own machine to settle, and §6 says how.

---

## 2. The five defects, root-caused

### 2.1 `acquire_prices` runs before `extract`, so no run has a market capitalisation

**This one cause produces three of the operator's symptoms.** They noticed market
capitalisation was never determined; that `comps` returned `0` peers although `propose_peers`
ran and the peer gate was approved; and, separately, that the portfolio could not value MSFT.

`_acquire_prices` computes the market capitalisation from the **filed** share count, preferring
it to the vendor's because a filed fact has a hashed filing behind it. It obtains that count
through `_filed_share_count`, which queries `financial_facts` for the company
(`vertical_slice_v1.py:2631`). But `financial_facts` rows are written by `persist_facts`
**inside the `extract` step** (`vertical_slice_v1.py:2694`) — and `acquire_prices` is declared,
and runs, **before** `extract` (`vertical_slice_v1.py:392–393`; the operator's own `aer
diagnose` output shows `acquire_prices` eleventh and `extract` twelfth).

So unless the database already holds facts for that company from an **earlier** run — the rows
are keyed by `company_id`, not by the job — `_filed_share_count` returns `None`, and the code
falls through to the vendor. That is also why it survived testing: on a warm database the query
finds last time's facts and the step looks correct. The comment beside that fallback already
names the consequence exactly:

> That endpoint is a ten-weight request, and it is a feed the operator's subscription does not
> include, so the fallback could only ever fail: no market capitalisation, and with it no
> enterprise-value multiple in the comps table.

Gap A47's fix was written and wired to a step that cannot yet see the data it needs. The
symptom the comment predicted is precisely the symptom the operator reported.

**The fix is a move in the declaration, not just an added edge.** The engine requires
dependencies to point *backwards* in declaration order — "that is what makes a cycle
unrepresentable" (`workflow/engine.py:452`) — and a step with no `needs` chains from whichever
step was declared before it. So `acquire_prices` has to be *relocated* to after `extract`, and
the `needs` of whatever then follows it checked, rather than simply given
`needs={"extract"}`.

The ordering comment at `vertical_slice_v1.py:388` gives the two real constraints — prices must
precede the assumptions, because the beta regression is one of them, and must precede `comps`,
which already declares `needs={"calculate", PRICES_STEP}`. Both are satisfied by placing it
between `extract` and `propose_assumptions`. The peer gate can stay where it is.

*Effort: small. Payoff: market capitalisation, the enterprise-value multiples, and the comps
table, all three from one edge.* Worth confirming afterwards that peer pricing is not blocked
by the same subscription question.

**One query confirms it on the operator's own database**, before anything is changed:

```sql
SELECT count(*) FROM financial_facts f
JOIN companies c ON c.id = f.company_id
WHERE c.name ILIKE '%microsoft%' AND f.concept = 'shares_outstanding';
```

Facts present with no market capitalisation recorded would mean the cause is elsewhere and this
section is wrong; the `acquire_prices` step's own recorded output (`aer diagnose <job>
acquire_prices`) says which branch it took.

### 2.2 `primary_source_ratio` counts every calculated figure as unsourced

The run failed acceptance on this one metric, at 0.5147 against a 0.6 floor, while reporting
`74 of 74` citations verified. Those two facts do not sit together, and the reason is in
`services/evaluations.py:455`:

```python
sources = set(rows.citation_sources.get(claim.id, set()))
if claim.financial_fact_id in rows.fact_sources:
    sources.add(rows.fact_sources[claim.financial_fact_id])
ranks = [rows.source_tiers[s] for s in sources if s in rows.source_tiers]
best_tier_rank=min(ranks) if ranks else None
```

A numeric claim reaches a tier through its citations, or through `financial_fact_id`. It has
no route through `calculation_id`.

And `record_claim` (`services/citations.py:95`) requires that **a numeric claim names exactly
one** of a fact, a calculation or an attestation. A claim naming a calculation therefore has
`financial_fact_id = None` by construction — and a calculated figure usually carries no
citation either, because no document contains the sentence "the quick ratio is 0.93".

So every DCF output, every ratio, every growth rate, every WACC scores `best_tier_rank = None`
and lands in the metric's failure list as *"no tiered source behind it"* — even where all of
its inputs are tier-1 filings. In a valuation report those figures are roughly half the
numbers, which is what 0.5147 is measuring. **The metric is not reporting thin sourcing; it is
failing to follow the provenance chain the platform is built on.**

**The fix follows the chain that already exists.** `CalculationInput` (`calc/engine.py:81`)
carries `source_kind`, `source_id` and `source_table` for every input, and ADR 0076 makes a
lineage node resolve by table. Walking a calculation's inputs to their leaf financial facts and
taking the best tier among them is deterministic, needs no new data, and is the same walk the
report's own footnotes already perform — the one check 11 of the acceptance pass passed.

Two questions for the operator sit behind this and are in §7: whether 0.6 is still the right
floor once the arithmetic is right, and whether a failed metric should block the *report* or
only be reported.

### 2.3 The numeral guard reads two kinds of ordinary prose as unsourced figures

`growth_outlook` was lost — both attempts refused, the salvage declined — and it took the
report's coverage down with it. Both refusals reproduce exactly, and neither is the writer's
fault.

**Sentence-initial product names.** `without_product_names` requires the head word to be
capitalised *mid-sentence*, because a capital at the start of a sentence is grammar rather than
a name (`core/section_output.py`, the `erase` closure). The docstring takes that cost
knowingly. The cost is this:

```
'The company describes growth drivers qualitatively. Microsoft 365 Consumer growth ...'
  -> unchanged: 365 survives and is flagged
'Growth in Microsoft 365 Consumer was described qualitatively.'
  -> 'Growth in Microsoft  Consumer ...' — correctly erased
```

`'Revenue rose. Dynamics 365 grew.'` fails the same way. The operator's refusal text — *"…growth
drivers qualitatively. Microsoft 365 Consumer growth is described as depende…"* — is that case
precisely.

**Form numbers in a list.** The second refusal reproduces character for character:

```
'No Forms 3, 4 or 144 were available in the material reviewed.'
  -> 'No   or 144 were available in the material reviewed.'
```

`_PLAIN_COUNT` erases the one- and two-digit counts, `144` is three digits, and its head word
is the lowercase "or", so no rule covers it. The operator's refusal read *"No or 144 were
available in the material reviewed…"*. `without_document_references` recognises a document
reference; it does not recognise SEC form numbers in a series.

**And the salvage keeps the wrong attempt.** Attempt 1 failed on the numeral alone and *the
salvage repaired it*. Attempt 2 failed on two problems at once — gap remarks and length — and
the salvage declined. The section was lost even though a repairable draft existed. That is the
part that costs a section rather than a sentence, and it is the more important half of this
fix: the ladder should keep the best reply it obtained, not the last.

**The operator's own `aer replay-draft` already says so.** Reading the twenty-four archived
replies back *under today's rules* — which is what that command is for — it reported
`growth_outlook — LOST: reply 2 of 2 is refused and the salvage declines`, and summarised
`Sections: 15 of 16 would draft (14 as written, 1 after repair); 1 lost`. So this is not a
stale artefact of the run: at HEAD, with every §2.1 fix in place, the same section is still
lost. That is worth recording plainly against ROADMAP §2.1, which reads as closed.

This is the one item in §2 that touches an invariant's boundary. The knowledge map's negative
space (§8) says the numeral rule stays strict deliberately and that relaxing it needs an ADR and
an operator decision. ADR 0060 already decided that a number inside a name is not a figure;
extending it to a name at the start of a sentence is an amendment to that decision rather than a
new one, and should be written down as such.

### 2.4 `brief_challenges` charges for a step that produces nothing, and calls it success

The step reported `SUCCEEDED`, cost £0.0715 over 51 seconds, and wrote `written: False`. The
model call ended `stop: schema_rejected`; the log recorded `challenge_briefs.unavailable` with
`error_code: validation_error`.

`ChallengeBriefs` asks `claude-haiku-4-5` at low effort for up to eight briefs, each with a
UUID echoed back exactly, an enum, and **four free-text fields hard-capped at 600 characters**
under `extra="forbid"` (`agents/challenge_brief.py:66–96`). The reply ran to 2,772 output tokens
— a full answer — and was rejected on validation. `_meter_a_failure` (`agents/base.py:709`)
records the cost and re-raises; **nothing retries**, and the step swallows the error into a
warning.

Three things are wrong and they are separable: no retry on a schema rejection (feeding the
validation error back is the standard remedy and would almost certainly succeed); a ceiling that
rejects rather than truncates a reply that has already been paid for; and a step status that
says `SUCCEEDED` over a step that spent money and delivered nothing.

**The archived response is the fastest way to settle which field failed**: it is artefact
`e72761deabb4ebbe91895059922b4e3a446e11c1a63d2e16fbc7051fe8aaa704` in the operator's store.

This is also a dependency, not just a defect: the challenge brief is exactly the "one or two
sentences and a choice between two options" the operator has asked the challenges screen to
become. The screen cannot be redesigned around a role that never returns.

### 2.5 The plan gate shows an estimate a sixth of the true cost

The gate showed £1.3353 and 120 seconds; the run cost £7.5023 and took some forty minutes of
recorded step time. The operator says a run is typically about £10 and about an hour. This is
the screen on which they decide whether to spend the money.

`vertical_slice_v1.py:653` builds the number as:

> the plan step's own spend, plus one writing call per section, plus skill guidance

and nothing else. It omits `critique_plan`, all five research workers, the peer, theme and
assumption proposals, `red_team`, **`revise`**, `verdict`, `brief_challenges` — and every retry,
though `draft` made twenty calls for sixteen sections. The runtime is
`_RUNTIME_ESTIMATE_SECONDS: Final = 120`, a constant that models nothing.

**An honest estimate already exists in the same file.** The `BudgetGuard` has a per-step table
of its own — `PLANNER_ESTIMATE_GBP` 0.20, `CRITIQUE_PLAN_ESTIMATE_GBP` 0.30,
`WORKER_ESTIMATE_GBP` 0.30 × 5, `ASSUMPTIONS_ESTIMATE_GBP` 0.10, `DRAFT_ESTIMATE_GBP` 5.00,
`RED_TEAM_ESTIMATE_GBP` 0.35, `REVISE_ESTIMATE_GBP` 1.50, and the rest — which sums to about
**£9.31**, against the operator's remembered £10. The platform knows the right answer and shows
the operator a different one.

The fix is to show the guard's own arithmetic at gate 1, as a range rather than a point, and to
build the runtime the same way from a per-step table or from the `elapsed_ms` the `costs` and
step rows already record.

---

## 3. The questions the pass raised, answered

### Rejected facts, refused tags, unplaced concepts

**Rejected facts are the platform working, and they are its differentiator.** ADR 0010 settles
this: `companyfacts` returns every XBRL fact a company has ever tagged, including the same period
reported many times over, and selection happens over the complete set rather than by filtering at
parse time. Every input fact appears in the output exactly once, either chosen or rejected with
one of three reasons:

| Reason | Meaning |
|---|---|
| `filed_after_as_of_date` | It did not exist yet. Using it would be look-ahead bias. |
| `superseded_by_later_filing` | A later filing, still within the date, restated it. |
| `duplicate_tagging_in_same_filing` | One filing tagged the same number under two names. |

Most facts are legitimately rejected, and the partition is kept precisely so that *"why is this
figure not in the report?"* has an answer. A filtered list could not answer it.

**Refused tags are decisions already taken.** `NEVER_MAP` in `core/concepts.py` carries a
reason beside each entry, refused inside `canonical_concept` itself so that an alias added in
good faith cannot take effect (ROADMAP §2.7). Refused is not a gap.

**Unplaced concepts are the real gap**, and they are the known open item §2.8: the mechanism is
built — `aer curation-worksheet` prepares a ranked sitting — but the curation is the operator's
own work, and a large unplaced count means a thinner report.

**The 4,754 "mapped figures" with visible duplicates** are, on this reading, mostly the data
being what it is: one concept legitimately appears at many period ends, in several units, and
under several dimensions, and ADR 0058 holds that a dimensioned fact is a *different*
observation. The defect, if there is one, is that the gate page shows raw rows where the
operator needs a collapsed view. §4 treats that as a presentation problem, which is what the
operator's own description of the page suggests it is.

### Leads

`WorkerReport` carries `leads` (`agents/worker.py:270`) — threads a research worker noticed and
did not follow. Whether the run pays for leads it never uses, and whether they belong on the
operator's console at all, is part of the same question §4 asks about every developer-facing
string that reaches a screen.

### Why the plan named no news sources

This is the most consequential answer in this section, because it is not about the planner.
`decide_quarantine` (`services/sources.py:104`) applies two rules in order:

```python
if point_in_time and publication_date is None:
    return QuarantineDecision(quarantined=True, reason=NO_PUBLICATION_DATE)
if point_in_time and publication_date is not None and as_of_date is not None \
        and publication_date > as_of_date:
    return QuarantineDecision(quarantined=True, reason=PUBLISHED_AFTER_AS_OF)
```

The first rule is not about backtesting at all. **Any web page whose publication date the
extractor cannot find is inadmissible whenever point-in-time is on** — and
`point_in_time_default` is `true`. Most news pages do not carry a machine-readable publication
date the parser will accept. That is why the plan offered filings and nothing else, and why the
plan's own caveat says non-filing sources are admitted "only where the page or document itself
carries a publication or revision date".

Two different questions are answered by one flag today, and §5 is about separating them.

Note also that on the tiering (`core/enums.py:172`) reputable secondary reporting is **T5** and
is never the sole support for a number. Admitting news would give the qualitative sections real
sourcing; it would not, and should not, let a newspaper carry a figure.

---

## 4. The screens

The operator's complaint is consistent across four pages and it is a fair one: the gates read as
developer screens. Some of this is a genuine redesign; a surprising amount is one missing
mapping.

**`web/vocabulary.py` already exists for exactly this problem** — "what a state is called on a
screen, and what it means, in one place", with `tests/test_presentation_vocabulary.py` failing
when a member of a mapped enum has no entry. It covers job states, gates, grades, decisions and
more. It does **not** cover:

| What leaks | Where | Fix |
|---|---|---|
| `material_missing_section`, `low_source_coverage` | `templates/runs/review.html:130` renders `trigger.kind` | Map `TriggerKind` (`core/escalation.py:49`) in `vocabulary.py`; the completeness test then enforces it |
| `growth_outlook` | `templates/runs/review.html:465` renders `section.key` | `section_definitions.title` already holds the human title (`db/models/section_definition.py:86`) and is simply not used here |
| `refused for: gaps×1, length×1, numeral×1` | refusal reason tokens | A mapping, in the same place |
| `34f · 23c · 7e` | the section rows | Either spell it out or move it behind a disclosure |

Others are the same shape: `skills/list.html:59,64,84`, `risk/index.html:126`,
`_ui/signatures.html:45,51`.

The right closing move is the one the palette migration already proved on this codebase: a
**ratchet test** that fails when a raw identifier reaches rendered HTML. The ramp ledger went
1,837 → 0 that way and became a hard assertion; the machinery is described in
[`interface-overhaul-testing.md`](interface-overhaul-testing.md).

Beyond the vocabulary, four pages need design work rather than a mapping: the **financials
gate** (what is it asking me to approve, and why am I in the loop?), the **assumptions gate**
(number formatting, a confirm-all control, and hand-entered values), the **draft review** (what
is above the fold and what belongs in the export), and the **plan review** (no underscores
anywhere, including "sections it will write"). The side menu and the research-request page's
spacing go with them.

**One caution on "confirm all", and one on the optional 'why'.** A gate approval carries the
hash of what was on screen, which is what makes it an approval *of this plan* and what lets a
stale approval be refused. A confirm-all control has to be built so that it still approves what
was displayed. And the 'why' box on a challenge is load-bearing in a way that is easy to miss:
`settle_by_hand` requires it (`services/disagreements.py:272`), `resolution_rationale` is
`NOT NULL` with a `char_length > 0` constraint, and `sections/deterministic.py:336` renders it
into **the report's disagreement appendix**. Making it optional would put an em dash in the
published report where a reason should be. Pre-filling it with a stated default — *"accepted
without further comment"* — gives the operator the click-through they want and keeps the
appendix honest.

**And the model settling some challenges is already permitted.** `ResolvedBy.AGENT` exists in
`core/disagreement.py:107` — *"A model. Only ever after the ladder declined to decide, and never
for a figure."* The request needs wiring, not a new decision — though which challenges qualify
does need writing down.

---

## 5. The as-of date: this is two questions, not one

The operator wants the as-of date removed: *"The publishing date should only matter for context
but it should be used when needed."* CLAUDE.md's invariant 4 and ADR 0010 both stand behind it.
But the request divides cleanly, and the two halves cost wildly different amounts.

**First, the size, because the obvious measurement overstates it badly.** `as_of_date` is
mentioned 527 times across 86 source files and 832 times across the tests, which is what a
grep says and is not what the change costs: almost all of those are the date being *carried*
— a field, an argument, a serialisation. The places where it **changes behaviour** number
about fifteen, and they are worth naming because they are what a decision here actually
touches:

| Where | What the date does |
|---|---|
| `sources/sec/{client,submissions,fulltext}.py`, `sources/uk/companies_house.py` | Selects filings filed on or before it |
| `services/facts.py` | Selects facts by `filed_date` |
| `services/peer_discovery.py` | Bounds a peer's periods |
| `services/sources.py` | The quarantine rule — **both branches**, and §3 is about separating them |
| `services/evaluations.py`, `services/escalation.py` | Detects look-ahead and reports it |
| `services/history.py`, `obsidian/graph.py` | Reporting only |

And two of them already show the way: `services/filings.py:306` and `services/research.py:274`
both read `as_of = request.work_order.as_of_date if request.work_order.point_in_time else None`,
and every adapter treats `None` as "do not filter". **A coherent no-filtering path already
exists and is exercised.**

**Question one: may the operator choose a historical as-of date?** Removing that is cheap and
safe. Point-in-time selection is `select_point_in_time`, and with the date fixed at *today*, "the
latest filing on or before today" is simply "the latest filing" — which is the correct answer for
a report written today about a company today. Every guard keeps working, nothing is deleted, and
the change is a default, a form, a query string and the documentation. This is almost certainly
what the operator means by "get rid of backtesting", and it is reversible.

**Question two: may an undated web page be evidence?** This is the one that actually hurts today
— it is why there are no news sources and it is entangled with the sourcing metric — and it is
*not* the as-of date. It is the first branch of `decide_quarantine`, which is gated on the same
`point_in_time` flag. Turning that flag off to admit news would also switch off the look-ahead
check, which is not what anyone wants.

**The recommendation is therefore to split the flag rather than remove the feature**: keep the
look-ahead check, and decide separately, and deliberately, whether a source with no discoverable
publication date may be cited and at what tier. That is one ADR, it is honest about what it
trades, and it delivers the thing the operator actually noticed.

What the operator should know they would be giving up under the larger reading: ADR 0010's own
argument is that "take the latest value" is look-ahead bias in its purest form and that **the
failure is silent** — nothing raises and no figure looks implausible. That matters much less
when every report is as at today, and a great deal if a report is ever written about a past
quarter. The watchlist is also specified as "researched as at a date" (ADR 0107), and the
portfolio clock is deliberately not the research clock (ADR 0075); both need a decision of their
own rather than being carried along.

---

### The shape of the two changes, as far as reading the code settles it

Written down here rather than as two ADRs, because an ADR in this repository records a
decision the code already carries and one written ahead of the code would be a claim about
a state that does not exist. They became **ADR 0110** and **ADR 0111** when the code landed;
what follows is the shape as read, and "The as-of split, as built" below records the three
places where reading the code disagreed with it.

**ADR 0110 — every run is as at today.** The as-of date stops being an operator input and
becomes a stamp: `work_orders.as_of_date` is set to the commissioning date and the field
comes off the request form. Nothing downstream changes, because "the latest filing on or
before today" is "the latest filing", and every guard keeps working on a date it can still
read. What has to be decided rather than derived:

* The **watchlist** commissions "researched as at a date" (ADR 0107). If every run is as at
  today, that phrase means the day the queue reached it, which is what it already does —
  but the wording on the page and in the ADR needs saying again.
* The **portfolio clock is not the research clock** (ADR 0075), and §12.7 of the acceptance
  pass makes the portfolio's own as-of date a link you can send yourself. That is a
  different date about a different thing and should not be swept up in this.
* **Reproducing an old run** keeps working, because the run's own stamp is what a replay
  reads. Commissioning a *new* run about a past quarter stops being possible, which is the
  capability being given up and should be named in the ADR rather than discovered.

**ADR 0111 — a source with no discoverable publication date.** This is the one that
actually changes what the reports contain. `decide_quarantine` refuses an undated source
whenever point-in-time is on, before it ever reaches the look-ahead test:

```python
if point_in_time and publication_date is None:
    return QuarantineDecision(quarantined=True, reason=NO_PUBLICATION_DATE)
```

Two rules wear one flag. Separating them means the look-ahead check keeps its flag and the
datability rule gets its own, and then the question is what an undated page may be — which
is a question about *tiering*, not about dates. The honest answer looks like: admissible,
never as the sole support for a number (which `T5_SECONDARY` already says), and marked as
undated wherever it is cited, so a reader can see what it is. The alternative — leaving it
inadmissible — is the status quo and the reason no news source appeared in the plan.

**Sequencing.** 0111 before 0110. It is smaller, it is the one the operator noticed, and
it is independent: the datability rule is wrong whether or not the date is chooseable.

## 6. Windows

Two things, one of which is a real fix.

**The GLib noise is ours.** Every `aer` command emits several `GLib-GIO-WARNING` lines because
`aer.cli` eagerly imports WeasyPrint — `vertical_slice_v1.py:105` imports `render.pdf` at module
level, and `render.document` pulls in `charts` and Matplotlib besides. Verified here: importing
`aer.cli` loads 1,845 modules in 2.4 seconds, of which WeasyPrint is 0.6s and Matplotlib 0.42s.
Deferring both to the render path removes the warnings from every command that never renders a
PDF, and roughly halves start-up. That is a small, contained change.

**The e2e failure needs the operator's machine.** It does not reproduce on Linux. The next step
is a Windows run of that one test with the server's own log captured, and the browser's network
panel; `ERR_ABORTED` means the response was not a navigable document, and the candidates are the
console's meta-refresh fallback firing into the navigation, the uvicorn thread unwinding, or a
`ProactorEventLoop` interaction between Playwright's loop and the worker's `run_async`.

**The Python version is worth confirming rather than assuming.** `pyproject.toml` pins
`requires-python = ">=3.12,<3.13"`, and the operator reported 3.13.14. If `uv` provisioned a 3.12
interpreter for the virtual environment, the reported version is the system one and nothing is
wrong; if the environment really is on 3.13, the pin is being bypassed and the mypy and ruff
targets no longer describe what is running.

---

## 7. The order of the work, and what only the operator can decide

### The order

**First, the run export**, because it makes every later diagnosis cheaper and the operator asked
for it directly. `aer diagnose` takes an optional *step* as its second positional argument, which
is why `aer diagnose <id> run-diagnosis.txt` answered "not a recorded step" — the documented
command uses a shell redirect (`testing-by-hand.md:1578`), and one missing `>` produces a
confusing error. It needs `--output`, and beside it a real per-run bundle: `just diagnose-run`
exists but is a raw SQL dump scoped to drafting and needs Docker. What the bundle must contain is
already specified by this document — for each finding above, the record that would have settled
it without a conversation. It must carry no credentials and no licensed series (ADR 0030,
`aer purge-licensed`).

**Then the five defects in §2**, in that order. §2.1 is first because it is one step edge and it
returns market capitalisation, the enterprise-value multiples and the comps table together.

**Then the vocabulary and its ratchet**, which is small and removes a whole class of the
operator's complaint at once.

**Then the four gate redesigns**, with the challenges screen after §2.4, since it depends on a
role that currently returns nothing.

**Then custom themes and peers.** An operator-added peer should reuse ADR 0093's verification
door — a typed `TICKER EXCHANGE` verified with the vendor once at first sight, or refused with
the reason — rather than inventing a second way in.

**Then the as-of decision**, once §7's questions are answered, because several of the earlier
items touch the same surfaces and doing it first would mean doing them twice.

**Windows throughout**, since the lazy import is independent of everything else.

### The questions

1. **The as-of date.** Is the intent question one (every run is as at today), question two
   (undated sources become admissible), or both? They are separate changes and the recommendation
   is to do both, separately, keeping the look-ahead check.
2. **The 0.6 primary-source floor.** Once calculated figures resolve to their inputs' tiers, is
   0.6 still right? And should a failing metric block the report or only be reported at the gate?
3. **The numeral rule.** Are you content for a capitalised word at the start of a sentence
   followed by a number to be read as a product name? It is an amendment to ADR 0060 and it has
   a cost, small but real.
4. **The challenges 'why'.** A pre-filled default keeps the report's appendix honest. Acceptable?
   And which challenges should the model be allowed to settle?
5. **EODHD.** Does the subscription include `/api/fundamentals`? §2.1 removes the dependency for
   the subject's own market capitalisation; peer pricing may still need it.
6. **Priority.** Correctness first, or the screens first? The screens are what you touch every
   run; the defects are what makes the numbers right.
7. **Windows.** Is it the only target? Phase 6 of [`remaining-work.md`](remaining-work.md)
   ("before it leaves one machine") is still unscheduled and this decides whether it stays so.
8. **The confirmation run.** The pass ends in one, and several of the fixes above can only be
   proved by one. `aer preflight` and `aer rehearse-section` exist so that most of the shape can
   be checked at no cost first.

---

**See also:** [ROADMAP](ROADMAP.md) · [the remaining work](remaining-work.md) ·
[testing by hand](../developers/testing-by-hand.md) · [the knowledge map](../developers/knowledge-map.md)

---

## 8. What has been done since, and what the operator decided

*Appended 2026-09-09, on branch `claude/dreamy-curie-kgb1s9`.*

### The four decisions

| Question | The operator's answer |
|---|---|
| The as-of date | **Both changes, made separately.** Every run becomes "as at today", *and* the undated-source rule is decided on its own merits. The look-ahead check stays. |
| Scope of this round | **Everything** — the defects, the screens and the features. |
| The 0.6 primary-source floor | **Fix the lineage and keep 0.6 blocking.** Measure a correctly-counted run before touching the threshold. |
| The numeral rule | **Amend it** — a sentence-initial name is a name, and form numbers are covered. |

### The defects, closed

All five of §2, and the Windows noise with them. Each was reproduced before it was changed
and each carries its regression test.

- **§2.1 — `acquire_prices` before `extract`.** The step moved into the fan-out after the
  financials gate, where prices, the calculation and the five research workers are all
  independent. Seven nodes at the bound of seven. Market capitalisation, the
  enterprise-value multiples and the comps table follow from the one edge.
- **§2.2 — the sourcing metric.** A named calculation now contributes the source documents
  its lineage reaches, through `services.calculations.lineage` — the walk the provenance
  surface already uses, rather than a second copy. A calculation resting only on
  assumptions still scores unsourced, because an assumption is a number somebody chose.
- **§2.3 — the numeral guard**, in three parts. A sentence-initial head is a name where the
  text attests it (ADR 0060, amended); `or` joins a filing enumeration as `and` does; and
  **the salvage is offered every refused attempt rather than only the last**, which is the
  half that actually cost the section.
- **§2.4 — `brief_challenges`.** A rejected reply is retried once, told what it was refused
  for. Two failures record a reason rather than a bare `written: false`.
- **§2.5 — the plan estimate.** Read from the workflow's own step table, the same one the
  budget guard enforces against, so the number the operator approves and the number the
  engine holds to cannot drift. About £9.31 where it said £1.34. The runtime is now the
  median of what completed runs actually spent working, with a stated fallback.
- **Windows.** `aer.cli` no longer reaches WeasyPrint at import, so the GTK stack — and its
  GLib warnings on every command — loads only where a document is rendered.

### What the numeral amendment deliberately did not give away

A blanket "a capital is a capital wherever it sits" was written first and rejected on the
evidence: it broke four of the suite's own cases, which is more of invariant 3's boundary
than the case is worth. `Shipped 240 units.`, `Together 365 stores opened.`, `Step 200 —
units shipped.` and `Deliver 5 — points of margin.` all still refuse. The residual is one
shape — a capitalised verb, a real quantity, and a capital after it — and it is pinned as
its own test.

### Still open, in the order it will be worked

The vocabulary and its ratchet; the four gate redesigns; custom themes and peers; the
comprehensive run export; then the as-of split as two ADRs. §7's remaining questions —
EODHD's subscription, and whether Windows is the only target — are still open and do not
block any of it.

### The screens, and the operator's own tooling

Worked after the defects, in the order §7 set.

- **The vocabulary and its ratchet.** `TriggerKind` joins the mapped enums; the review page
  resolves each section's title; `in_words` is registered as a filter on every template so a
  value reaches its label without each handler remembering to resolve it. Then the ratchet,
  modelled on the palette migration: raw identifiers reaching a reader, counted per
  template, **19 at the start and 13 now**, each remaining entry carrying the reason it is
  still there. Most of what is left is an operator's own skill key or a provenance name from
  a stored ledger row.
- **The financials gate.** 4,754 rows headed "Concept" are now statement lines: the newest
  period leads, earlier ones sit behind a count, and the figures are in the house style.
  `Revenue · 1 July 2024 – 30 June 2025 · $331,839m · 12 periods` where it read `revenue ·
  2024-07-01 – 2025-06-30 · 331839000000 USD`. Grouped, not filtered — the gate asks whether
  anything is missing, so a page that quietly dropped an observation would be answering its
  own question — and it still reads the payload the hash covers.
- **The assumptions gate.** A rate reads as a rate, with what is stored beside it; the ones
  that are not fractions are named rather than inferred, because an exit multiple of 12
  means twelve times. "Confirm all" carries the same list hash a single confirmation does.
  And a value the operator types is confirmed by the act of typing it — the second click
  recorded the same person agreeing with themselves, and the control that was always the
  real one, the gate's own approval over the whole list, is untouched.
- **The plan review** shows section titles in both lists, and `page_header` no longer lets
  its first action sit hard against the sheet below it — one margin rather than
  forty-five copies of one.
- **`aer export-run`**, and `aer diagnose --output`. What each finding in this document had
  to be established by hand is now one file: every step's recorded output, every model call
  with its stop reason and both payload hashes, every section with what it was refused for,
  the evaluations with their thresholds, the approvals with the hash each covered, and every
  calculation with its inputs. No credentials, no licensed series, no embedded payloads —
  and the exclusions are in the document, so silence is legible.

### The operator's own additions to a slate

*"To the confirming themes section, it would be good to add the ability to add custom
themes, similarly with the peers, allow the user to add peers of their own."* Both slates
arrived as somebody else's work — a model's bounded theme slate, a model's peer proposal
resolved against EDGAR or the deterministic floor underneath it — and a person could only
approve or refuse.

Three properties make the addition correct rather than merely present, and they are the
same three on both gates.

**An addition is not a confirmation.** The row joins the slate the gate is about to hash;
approving is still what files a company under a theme or admits a peer to a comps table.

**It is a row, not an edit to the step's output.** A step's recorded output is what that
step produced, and a run's record stops being a record the moment something else writes
into it. So `payload_for_job` became the single funnel on each gate: the page renders it,
the approval hashes it, and `confirmed_theme_set` / `confirmed_peer_set` verify against it.
Adding one *after* approving changes the payload and invalidates the approval — the
stale-approval rule working, asserted by a test on each side rather than worked around.

**Neither is trusted as typed.** A theme's label goes through the same `slugged` and
`normalised_slate` path a model's proposal does, so "AI Capex" typed at the gate joins
"ai-capex" proposed by the model instead of founding a rival spelling. A peer takes the
registry's name rather than anything typed, and its period end is read from the facts.

Migration `0072` adds `operator_themes` and `operator_peers`. The drift test caught the
missing indexes on the foreign keys before a human did.

#### The peer constraint, which is a finding rather than a shortcut

**An operator-added peer is a company this platform already holds, not a ticker to go and
resolve.** The web process has no source client and should not have one — only `aer.fetch`
reaches the network, and acquisition is the worker's — so a typed ticker cannot be resolved
where the operator types it.

It turns out not to cost anything. `propose_peers_from_sic` draws from exactly this pool
and skips a candidate with no stored financial facts, *because a peer with no period end
cannot be aligned against the subject and would be excluded a step later anyway*. The pool
of peers a comps table can use is the companies already researched, so the control is a
picker rather than a text box: offering anything else would be offering the operator a
refusal.

Arbitrary tickers remain possible and are a different change — resolution would have to
happen on the worker after approval, which weakens what approving a set means. Not taken
here; recorded so the option is a decision rather than an omission.

### Found on the way, and left alone deliberately

**The suite passes or fails by file order.** Running `tests/test_gates.py` before
`tests/test_every_page_renders.py` makes the latter error in its fixture with "No research
request …"; the reverse order is green, and so is the full suite in its canonical order. It
reproduces identically at `7c1a733` with none of this branch's changes, so it is not a
regression — but a suite whose answer depends on which files you name is one an acceptance
pass cannot trust, and `_TABLES` in that file truncates `research_requests` without
`work_orders`, which is the first place to look. Not fixed here because it is nobody's
finding yet and the fix wants its own reading.

### The side menu, measured

*"The side menu should be improved to be more user friendly."* Read against the code, the
complaint is specific and the number is the whole of it:

| Heading | Items under it |
|---|---|
| Overview | 1 — Overview |
| Research | 5 — Requests, Active run, Reports, Skills, Knowledge |
| Watchlist | 1 — Watchlist |
| Portfolio | 1 — Portfolio |
| Risk | 1 — Risk |
| Theses | 1 — Theses |
| Decisions | 1 — Decisions |
| Monitor | 1 — Monitor |
| Review | 2 — Post-trade review, Decision analytics |
| Platform | 4 — Settings, Costs, Health, API |

**Ten headings over fifteen destinations, and seven of the ten head a single link.** A
section header above one item is not navigation, it is the word said twice, and seven of
them in a column is why the menu reads as longer than the product.

The cause is structural rather than careless: a section is contributed *per tool*
(`shell/registry.py`, "one import per tool, and one line here"), a tool is a registered
capability (ADR 0071), and nine tools therefore produce nine sections. That was the right
call when the second tool arrived and it stops being right at the ninth.

The fix is a presentation-level grouping over the sections rather than a change to how
tools register — the registry keeps its one-line contract, and the shell decides how the
contributions are drawn. Something like: **Research** (as it is), **your book** (Portfolio,
Risk, Decisions, Post-trade review, Decision analytics), **what you believe** (Theses,
Monitor, Watchlist), **Platform**. Five headings, and each groups by what the operator is
doing rather than by which tool implements it.

**The words are the operator's to choose**, which is why this is written down rather than
built: "your book" and "what you believe" are a guess at how they think about the split,
and a menu grouped by somebody else's mental model is the problem restated.

### The as-of split, as built

Both halves landed, in the order §5 argued for. What follows is what reading the code
settled that the plan could not, because in three places the code disagreed with it.

**ADR 0111 — an undated source is admitted, and never primary.** The datability rule got its
own policy on the work order, defaulting to admitting, and the look-ahead branch kept
`point_in_time`. What makes admitting safe is a cap rather than a second refusal:
`SourceTier.as_evidence` reads an undated document as tier 5 whoever published it, so it may
corroborate and may never be the primary source a section's policy requires. The recorded
tier is kept beside the cap and both are shown, because what the provider is, is a fact about
the document and the cap is a verdict about it.

Three things the plan got wrong, found by reading:

* **"Never the sole support for a number" was already true.** §5 proposed building it. Since
  ADR 0109 a numeric claim names a fact, a calculation or an attestation — `record_claim`
  refuses one that does not — so no number has ever rested on a citation. What the cap
  actually decides is the *primary*-source floor, which is a smaller and more honest claim.
* **A section's tier ceiling must keep reading the recorded tier.** Filtering the evidence
  listing on the cap put this ADR's own blanket refusal back through a different door: a
  section with a ceiling of 4 stopped seeing an undated filing at all, rather than seeing it
  and being told it had no primary source. Six custom-section tests found it, and
  `test_a_tier_ceiling_still_shows_it` is now the boundary.
* **The renderer already marked undated sources.** `render.document` has carried the C3
  marker and its legend since before any of this; it now derives it from the same rule.

**ADR 0110 — a run is dated by the platform.** `work_orders.as_of_date` is a stamp written
from `RequestLimits.today` at commissioning: one clock, read once, validating and dating the
same request. The field is off the form (the sheet states the date above the point-in-time
choice it governs), off `ResearchRequestCreate` — `extra="forbid"`, so an API client is told
rather than silently ignored — off `_EDITABLE_FIELDS`, and off the watchlist's commission
form. Nothing downstream changed, exactly as §5 predicted: "the latest filing on or before
today" is "the latest filing".

The point-in-time choice stays. With the date fixed it decides whether to admit a document
whose own evidence puts it in the future — mis-dated or made up — which is narrow, real, and
still the operator's call. The form's copy says the new thing rather than the old one.

### Two defects found on the way

Both in the code this work was already in, both fixed here.

**The web form could not turn point-in-time off.** The control became a pair of radios — so
that the state it is *not* in gets named — and the parser stayed the checkbox's
`values["point_in_time"] != ""`. The string `"false"` is not empty, so choosing "allow
later-published sources" produced a point-in-time run and said nothing. Reproduced before
fixing: `parse_request_form` returned `True` for both radio values.

**Three templates printed `no_publication_date` at a reader.** The raw-identifier ratchet
exempts `reason` on the argument that a refusal's reason is a sentence the platform wrote —
true everywhere except the quarantine reasons, which are module constants. They resolve
through `vocabulary.QUARANTINE_REASONS` now, with a completeness test walking the constants
where they are declared.

### Still open

The side menu — measured above, and waiting on the operator's own words for the grouping.

**The suite stands at 6,832 passed**, against 6,778 at `7c1a733`: fifty-four tests added
across the defects, the screens and the two slates, and none removed.
