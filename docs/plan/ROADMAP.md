# Roadmap

**The authority on scope.** Where this and any other document disagree, this one wins —
except the ADRs, which are decisions and outrank a plan.

Three buckets and nothing else. **Fixes and bugs** is what is wrong; **new additions** is
what does not exist yet; **archived** is what is finished or decided against, kept because
the *why* is the part worth having. An item moves between them by being worked, never by
being tidied away.

*Supersedes the original `PLAN.md` (Stages 1–4), the five phase plans and `investment-os.md`
§12/§15 — all now in [`../archive/`](../archive/README.md). Those remain the record of how
the platform got here and why; this is the record of where it goes.*

*Written 2026-08-23 on the merged trunk. Restructured into these three buckets 2026-08-25.*

---

## What to do next

In this order. It is the operator's order rather than the author's: each one is the next
thing that would otherwise put a wrong number, or no answer at all, in front of somebody.

1. **§2.1 — five sections fail to draft.** More than a quarter of the last report was a
   coverage notice. The diagnosis now reaches the screen (§4.6); this is the fix behind it.
2. **§3.1 — the portfolio's third door.** Decided 2026-08-25: **a work order roots the
   book's own acquisitions.** Until it exists, a ticker no research run has priced cannot be
   dealt at all, so the portfolio tool is unusable on a machine whose runs were unpriced.
3. **§2.4 — the report document's layout.** The disagreement appendix puts a two-hundred-word
   objection in a narrow column and one row spans three pages; neither position can be read.
4. **§2.5 — the palette migration.** The last of the surfaces work, and the one most likely
   to go wrong quietly, so it wants its own pass with screenshots.

*Finished 2026-08-25 and now in §4: the drafted-figure check (§4.14) and the comps
disclosure (§4.15), which were the two at the top of this list.*

Everything else sits in its bucket below.

---

## 1. Where the platform actually is

**The chain is complete. The breadth is not.**

A research request becomes a costed plan you approve, filings fetched and hashed,
point-in-time facts, traced calculations, a drafted report you approve, and a frozen
document in which every figure carries a footnote resolving to either the formula that
produced it or the archived bytes it came from. That path has no gap in it, and the
evaluation gate re-derives every stored calculation from its own record on every run.

Two of nine tools work:

| Tool | State | Waiting on |
|---|---|---|
| **Equity Research** | Working, end to end | — |
| **Portfolio** | Working | — |
| Watchlist | Planned | A standing budget that is not one run's cap; the two clocks |
| Theses | Planned | The judgement record |
| Decisions | Planned | Judgements, and the reserved-field guard |
| Monitor | Planned | Theses to monitor against |
| Risk | Planned | A book to be about, and the rate store *(the rate store now exists)* |
| Post-trade review | Planned | Decisions and positions |
| Decision analytics | Planned | Enough reviewed decisions to say anything at all |

A planned tool is a real page saying what it would be and what it needs — not a dead link
and not a lie.

### What the merge established

The trunk merges two lines of work that ran in parallel from `7a438e8`:

- **The research line** — residual income for banks, the catalyst contract, the assumption
  gate, subject naming, section evidence, sector enforcement that blocks rather than
  footnotes.
- **The Investment OS line** — work orders as the run root, the tool registry, attestations
  and grades, the FX rate store, portfolio arithmetic, and the shell: nav as data, design
  tokens, badges, the drawer, the launcher.

Two collisions had to be resolved by hand, because git could not see either: both branches
had claimed ADRs **0067–0070**, and both had claimed alembic revisions **0051–0053**. The
research numbering was kept; the Investment OS records became **0071–0085** and its
migrations **0054–0057**. Anything written before 2026-08-23 that cites an Investment OS
ADR by its old number reads four low.

---

## 2. Fixes and bugs

Something here is wrong and should not be. Ordered by how much of a report or a screen each
one costs, worst first. **§2.1 is next.**

**2.1 A63 — five sections fail to draft.** Business Overview, Segment Analysis, Industry &
Competitive Positioning, Earnings Quality and Capital Allocation did not generate on the
2026-08-24 run, and three more rated themselves 0.30. One cause was identified before the
merge — a thin evidence pack, then a retry that swings past the target — and the
instrumentation to read it back is in place, and §4.6 put it on the screen. This is the fix
behind it. Until it lands, more than a quarter of every report is a coverage notice.

**2.2 Section confidence.** Three sections reporting 0.30 is either an honest signal about a
starved pack (§2.1) or a floor nobody calibrated. Read it back from the same live run before
changing anything: a confidence score that is always low is as useless as one that is always
high.

**2.3 A run that fails late cannot be resumed, only repeated. Open.** The engine skips
completed steps, and does it well — that is how a run survives the worker dying. But it only
applies to the *same* job, and the only operator-facing path is superseding, which creates a
new job precisely because the old one is a finished audit record. So a failure at the
red-team step, one step from the end, costs the entire run again: on the 2026-08-24 MSFT run
that was £8 of research and drafting to recover a £1 step.

The machinery to do better already exists; what is missing is a supported way to re-enqueue
the *same* job after a terminal failure, and a decision about what that means for the audit
record — a job row that says it failed, then later says it succeeded, is not obviously
honest. That decision is the work here, not the plumbing.

**2.4 The report document — layout.** The rendered PDF has two defects a reader meets
immediately. The disagreement appendix puts a two-hundred-word challenge in a narrow table
column, so one row spans three pages and neither position can be read. The "at a glance"
tables render label and value as separate stacked blocks, so a reader reassembles the pairing
by counting. Rework the appendix as prose blocks per disagreement, fix the key-figure tables,
and check every section's print layout against a real run rather than a fixture.

**2.5 The palette is migrated only in part.** The theme *control* is done (§4.13) and this is
what it left behind: a page's colours are correct in both schemes or they are slate grey
beside navy, and roughly half of them are the second.

`web/styles/app.css` added the semantic tokens *beside*
Tailwind's stock ramps rather than over them, deliberately, so that `text-sky-700` still
renders sky — overriding the ramp would re-skin thirty-eight templates for free and leave a
codebase where a colour name is a lie. So it is a real rewrite: 1,334 occurrences of 138
distinct ramp classes, onto `canvas / surface / ink / line / brand / good / warn / bad /
info / mute`, ending with a test that fails when a template reintroduces a raw ramp.

Deliberately sequenced last in this bucket. Everything above it is a wrong number or a
missing answer; this is a page that looks like two designs. It is also the item most likely
to go wrong quietly, so it wants its own pass with screenshots rather than being folded into
a functional change.

**2.6 A split must arrive as a transaction.** `corporate_actions` knows about splits, but
nothing turns one into a change in holdings, so a book spanning a split is currently wrong.
Derive it from the corporate action and write it as a transaction — never as a quantity
that changed with nothing behind it.

**2.7 R18 — the share-based-compensation risk-free rate.** A
`ShareBasedCompensation…RiskFreeRate` tag must never map to `risk_free_rate`. It is an
input to an option-pricing model in a footnote, not the discount-rate input, and mapping it
would put a plausible wrong number in the cost of capital.

**2.8 A55 — concept-map coverage.** 175 concepts and 110 segment tags the map cannot place.
This is judgement over accounting semantics rather than a code change, which is why it has
survived several passes: it needs somebody who knows what a tag *means* deciding what it
maps to. The gate that names the lines a filing would lose is the mechanism; the curation is
the work.

**2.9 Report readability.** The register is clean — every sentence in a report that was
*about the report* is gone or moved to where disclosure belongs. Keep it that way: the
failure mode returns whenever a new refusal path gets a placeholder written in the
platform's voice rather than the report's.

---

## 3. New additions

Nothing here is broken; it does not exist. §3.1 is the one an operator is currently blocked
by. §3.5 onwards is the judgement layer, and the order there is forced by dependency —
nothing after theses can exist before them.

**3.1 The portfolio — getting a ticker in. Two doors of three, 2026-08-25.** `Security` rows
exist only where a priced research run created one, so on a fresh database the control held
one option reading "cash, no security" and an operator could neither type a ticker nor find
out why not.

- **Typed, over what is held.** Done. The `<select>` is an `<input list>` over a
  `<datalist>` — a native typeable combobox, no script — and what is typed is resolved on
  the server: a bare ticker, the vendor symbol a run stored (`BARC.LSE`), or
  `TICKER EXCHANGE`. A dual listing is refused with **both** choices named rather than
  resolved by picking one, because a holding priced off the wrong exchange is a book nothing
  downstream can reconcile. An empty box is a cash transaction, not a mistake.
- **From a research request.** Already true and now stated on the form: `acquire_prices`
  creates the subject's listing, so a company you have researched with a subscription
  configured is dealable with no second step. The empty state names this as the one path
  that creates a listing today, rather than reporting an absence.
- **A ticker the platform has never seen. Blocked on a decision, not on plumbing.**

**The blocked one, and why it is a decision.** Verifying a new ticker means fetching its
price series, and a fetched series is an externally derived fact — invariant 1 says it was
hashed and stored. The machinery for that is `services.acquisition.record_acquisition`,
which requires a `ResearchRequest`: it needs the point-in-time setting, and the source
document is scoped to a run. A portfolio has neither.

`price_bars.source_document_id` is nullable and writing `NULL` there would compile, but its
nullability means something else — `ON DELETE SET NULL` for the licensed-payload purge under
ADR 0031, so the column reads "the bytes are gone", not "there were never any". Using it to
mean the second would quietly retire invariant 1 for every price the portfolio touches.

**Decided 2026-08-25: a work order roots the book's own acquisitions.** Of the three
candidates — a work order, loosening `record_acquisition` to take the point-in-time flag
directly, or a synthetic research request per portfolio — the first is the one consistent
with where the schema already went. ADR 0072 makes the work order the run root; a *portfolio
data acquisition* is a work order whose subject is the portfolio rather than a company, and
`source_documents.work_order_id` already exists and is already what §3.3 is migrating
everything onto. The other two were smaller: loosening the signature leaves the source
document scoped to nothing in particular, and a synthetic request puts a row in
`research_requests` that nobody commissioned and every "how many reports have I run?" query
then has to know to exclude.

It is the largest of the three and that is the cost of the decision, not an argument against
it. The work:

1. **A work-order kind**, so a run and a book acquisition are distinguishable in the table
   rather than by inference. Needs an ADR amending 0072 — the record says the work order is
   the *run* root, and this widens it.
2. **`record_acquisition` reads the point-in-time setting off the work order** instead of a
   research request. A book acquisition is inherently not point-in-time: you want today's
   close, and refusing it as post-dated would be enforcing a rule nobody set.
3. **`add_listing(session, ticker, exchange, client)`** — resolve the symbol, fetch a short
   window of bars, refuse with the reason where the vendor returns none, and record the
   artefact, the security and the bars under the book's work order.
4. **The form's third door**, which is then just a call: what is typed and not held gets
   verified once, at first sight, and either becomes dealable or is refused with why.

**3.2 The portfolio — return and exposure.** Four tiles is not an overview. Add:

- **Return over time.** Time-weighted and money-weighted, since inception and per period,
  over a value series walked from the transactions and the price history. Deposits and
  withdrawals are flows, not gains, and a top-up must not read as performance.
- **Concentration and exposure.** Weight by holding, sector, currency and listing country,
  with a top-five concentration figure. Sector comes from the company record behind the
  security, which exists only for names a run has touched — so it reports what it knows and
  names what it does not, rather than bucketing the rest as "other".

Both are calculations under ADR 0083 like everything else on that screen: derived on the way
to the page, nothing stored, every figure carrying the grade of the weakest thing beneath it.

**3.3 Step 4 of the work-order migration.** Drop `jobs.request_id`,
`approvals.request_id`, `source_documents.request_id` and the columns duplicated on
`research_requests`. Deliberately staged as a later revision (ADR 0072): while those
columns still hold the data, dropping `work_orders` discards nothing, so the downgrade is
lossless rather than merely declared. Needs the ~20 `session.get(ResearchRequest,
job.request_id)` lookups to become optional mandate reads first, since a monitor run will
have none.

**3.4 Scenarios and sensitivity for the residual-income model.** The bank model ships
without them, and says so in its caveats rather than quietly. The discounted cash flow has
an 81-cell grid; the bank model has none.

**3.5 Judgements and theses** (ADRs 0074, 0079). A thesis is a view a named person held at
a time, with the evidence it rests on and the questions that would defeat it. The record
that makes it *storable without becoming evidence* already exists — **a judgement is never
a source reference** — and this is where it earns its keep.

Also here: `RESERVED_OUTPUT_FIELDS` gains `conviction`, with its attack file. A conviction
score that something else can multiply is exactly the laundering ADR 0074 refuses.

**3.6 The thesis monitor** (ADRs 0078, 0079). What has happened since a thesis was written
that bears on it. **It raises questions and answers none**, and a monitor finding is not a
gated decision — an alert feed that decides things is the thing that record exists to
refuse.

**3.7 Decisions and the trade journal.** The entry written *before* the outcome is known.

**3.8 Post-trade review and decision analytics** (ADR 0081). Scored against the process
that was supposed to be followed, deliberately **not** against whether it made money.

**3.9 Portfolio risk and scenarios** (ADR 0080). Commented on rather than scored. Its rate
prerequisite is now met.

**3.10 Watchlist and research queue.** Needs the standing budget and the two clocks — a
watchlist is followed continuously and researched as at a date, and conflating those is the
mistake ADR 0075 names.

**3.11 The methodology library.** Three `SkillKind`s that are versioned, pinned and
composed. Mostly does not exist yet.

**3.12 A critique-and-revise loop for drafting, with a memory of what needed revising.**
Today `red_team` (ADR 0039) already attacks the draft from a separate context, and it has
twice caught a section publishing a number that contradicts its own citation (4.14, ADR
0086) — not a hypothetical failure mode, the platform's own log. What it does not do is loop
back: a challenge reaches the disagreement ladder for a human at `gate_final`, the section
that provoked it is never redrafted, and nothing about the challenge survives past that one
run. The machinery to critique already exists; what is missing is a writer that gets a
second attempt before a human ever sees the draft, and a way for a recurring class of
challenge to be recognised as recurring.

Scope it to where an open-ended, model-written step is both expensive and wrong often
enough to matter. `draft` first — already the largest cost line per report, already the
most reader-facing, already the step §2.1, §2.2 and 4.14 are about. `plan` second — cheap on
its own, but a wrong plan sends the whole run after the wrong target, so catching it early is
the highest leverage in the workflow. `propose_assumptions` is a plausible third and a lower
priority: high-stakes, but already narrow (ADR 0046) and immediately human-gated. It does not
belong on `acquire`, `classify`, `extract`, `calculate`, `comps`, `value` or `render` — those
are correct by construction under the one rule (`CLAUDE.md`, ADR 0003), and a model's opinion
feeding back into one of them is the "calculation drifting into a prompt" failure the
architecture exists to prevent. It does not belong on the gates either, since the human is
already the critic there, nor on `validate`, which is advisory-only by design and was never
meant to have the last word (ADR 0038), nor on `red_team` itself — recursing the loop onto
the critic is diminishing returns that the disagreement ladder already covers.

**The revise loop is the easy half.** The knowledge base is not: a memory that changes what
a future agent writes is exactly the shape of thing invariant 7 exists to govern — skill
files may only add requirements, never relax them, proved by a corpus that must all fail
(ADR 0040), not assumed. An auto-written "do not repeat this" lesson has no such proof
behind it, and a critic that was wrong once would otherwise get to entrench its own mistake,
unreviewed. Whether that memory has to be a skill file, or some other mechanism that still
needs the same proof, is an architectural choice CLAUDE.md says to stop and ask about rather
than guess — the safe default is a person confirming a finding is real and recurring before
a future run inherits it, possibly through §3.11's methodology library rather than a new
mechanism. Either way this needs an ADR before any of it is built: a new step is a new agent
behaviour (ADR 0035), and this one touches invariant 7 by nature, whichever way it lands.

**3.13 A web-search tool for the qualitative sections.** Nothing has a `web_search`
capability today. `fetch_known_url` reaches only a host this run has already acquired a
document from through the named adapters, and refuses anything else by design — an
unlisted host gets back "this run holds no document from that host, and a host is never
taken from a request." What is missing is not the trust model: `sources/tiering.py` and
`fetch/policy.py` already carry `Provider.WEB_SEARCH`, tiered before this item existed —
search-found news and issuer material at `T5_SECONDARY` ("never the sole support for a
numeric claim"), search-found commentary at `T6_UNVERIFIED` ("never citable evidence").
What is missing is a tool nothing calls: no role's allowlist grants it, and the commercial
check still outstanding on the Anthropic web-search tool's price is what keeps it out of
the budget guard (invariant 6).

Scope it to the qualitative workers — `research_recent_developments` and the rest of the
five parallel research agents (ADR 0036) — for what does not belong in `calc/`: business
description, sentiment, recent developments, the colour a filing does not carry. It does
not widen what can reach a figure. `calc/` consumes structured facts extracted from filings
and nothing else, so a T5 or T6 source has no route into a number whatever tool exists —
that boundary is the one rule (`CLAUDE.md`, ADR 0003), not a permission to add. Wired the
same way as any other tool request — the model asks, code executes, results return wrapped
as `<untrusted_source tier=…>` (ADR 0019) — it is contained the same way a filing already
is, and the corroboration the operator wants ("confirmed when multiple sources agree") is
`T5_SECONDARY`'s existing "never sole support" constraint, already enforced, not a new one
to write.

**Do not pre-approve named publishers.** Checked against robots.txt — the same mechanical
test ADR 0009 already treats as an absolute refusal, no ToS reading required — Seeking
Alpha disallows roughly 150 crawlers outright, `Claude-User` and `ClaudeBot` by name among
them: it appears to refuse the model this platform itself runs on. `ft.com` could not be
reached to check at all. That is the same shape of finding that put the Bank of England and
the FCA NSM (ADR 0022) in §4's "decided against" — a documented block, not a negotiable
one. So each specific always-on publisher, if wanted, is its own adapter-style decision — a
ToS/robots determination recorded in an ADR before the first request, per the existing
recipe — never a batch of sources assumed trustworthy for being well known. Needs an ADR
either way: a new tool capability is new agent behaviour (ADR 0035).

**3.14 A step-by-step developer mode for debugging a run.** Pause after every step, print a
diagnostic, let the operator confirm or correct before the next one spends anything — the
point being to catch a defect at the step that caused it, not three steps and several pounds
later. The primitive already exists for the accidental case: the engine already records
each step and skips the ones already completed on resume, which is how a run survives a
worker dying today. What does not exist is the deliberate case. §2.3 already names the gap
this depends on: there is no supported way to pause and continue *the same job*, only crash
recovery and superseding into a new job — and superseding is wrong here specifically,
because it creates a fresh audit record when the whole point is reviewing one run as it
happens. Build this as §2.3's resolution, not a second mechanism beside it, and settle its
open question — what a deliberately paused, not-failed job's own status record says — once,
for both. Unlike §3.12 and §3.13, nothing here touches an invariant or adds an agent
capability, so the item itself does not obviously need its own ADR — only §2.3's decision
does, and that one already stands regardless of this.

It is not a gate. `gate_plan`, `gate_final` and the rest are domain-approval checkpoints
through `services/approvals`, each meaning something specific about a business decision;
stepping through a run is a generic, lighter thing — closer to a breakpoint than an
approval — and belongs in the engine's own execution loop instead. Unlike §3.12, scope it
to every step, not only the model-written ones: a wrong number out of `calculate` or a bad
extraction is a code bug, and catching it here matters at least as much as catching a bad
paragraph out of `draft` — arguably more, since a silent arithmetic mistake is exactly the
kind of thing nothing today stops to show anyone.

**The diagnostic is code, not a model call.** An LLM judging each step would add a paid
call to steps that cost nothing today (`extract`, `calculate`, `render`), work against the
speed this is meant to buy, and duplicate the critique agent in §3.12. Most of what it needs
is already captured and simply not surfaced — a failed section already records its attempt
count, its evidence tally and its own refusal reason (4.6), and the roadmap's own words for
the gap are "the run console still shows none of this." Assemble it from what each step
already records — timing, retries, raw versus parsed output, validation errors, cost — in
the same family as the existing `job_id`-scoped CLI commands (`replay-run`, `acceptance`)
that already print a typed readout to the console rather than a web page, which is the right
shape for something meant to be read by whoever — human or Claude — is sitting at the
terminal deciding whether to continue.

### Before this leaves one machine

None of this is needed for a personal tool on a laptop, and all of it is needed before
anything else. Grouped because they stand or fall together.

- **A5 — no authentication.** `get_current_user` returns the first row of `users`.
- **A7 — no inbound rate limiting.** The token bucket protects outbound fetches only.
- **A8 — no production deployment story.** No production compose file, no TLS, no
  supervision.

Treat these as a single gate rather than three tickets. Shipping any one alone buys nothing.

### Commercial and licence checks still outstanding

Carried forward from the original plan. Each is a verification against a primary source,
not a design task, and each should be done **before** money or a dependency is committed.

1. Verify Anthropic **web-search tool pricing** against the official pricing page — the
   figure in the cost model came from secondary aggregators.
2. Verify the **Companies House rate limit** (600 requests / 5 minutes) against the official
   developer documentation.
3. Verify **EODHD's licence terms** for internal commercial use versus redistribution, in
   writing, before building further on it.
4. Verify **Langfuse's current self-host licence** before making it a dependency. The
   OpenTelemetry + Postgres + Grafana fallback has no licence risk, and the `costs` table is
   needed either way.
5. Validate **WeasyPrint's native dependencies** on the target Windows machine. It is the one
   tooling choice that can force late rework.

---

## 4. Archived

Finished, with the date, or decided against. Kept in full: a diff records what changed and
these records are why, which is the half nobody can reconstruct afterwards.

**4.1 The replay report called a rounding error a divergence. Fixed 2026-08-25.**
`just replay-run` on the 2026-08-24 MSFT run reports 113 of 1,034 calculations as "does not
replay", while the same run's evaluation gate passed `numerical_consistency` on the same
rows. Both cannot be right, and the gate is the one that is.

`calculations.output_value` is `NUMERIC(38, 12)`, so a non-terminating quotient is stored
rounded to twelve places. `services.run_replay` then compares `observation.replayed !=
observation.expected` **exactly**, and a recomputed ratio carries the full context precision
— `gross_margin` on those figures stores `0.679546406541` and replays
`0.6795464065405211563438896573338275`, a relative difference of 7 × 10⁻¹³. Every ratio in
the run fails that comparison and every sum survives it, which is why `invested_capital` and
`working_capital` are the two rows per period that pass.

The gate already had the right rule and the replay service now reads it:
`ReplayObservation.delta` against the `numerical_consistency` threshold, with a unit mismatch
and a re-run error as failures in their own right. The old comparison also accepted a unit
mismatch silently, which is a second defect the same line carried. Each problem now names
what went wrong rather than saying "does not replay" and stopping there.

**4.2 "Reproduce this run" failed in the browser and worked from the shell. Fixed
2026-08-25.** The button returns `internal_error`; `just replay-run` on the same job
succeeds. The difference is the event loop. `just dev` passes `--reload`, uvicorn sets
`use_subprocess`, and on Windows that selects `SelectorEventLoop` — where
`asyncio.create_subprocess_exec` raises `NotImplementedError`. Replay is the only web route
that re-extracts a document, so it is the only one that trips it; the CLI gets the Proactor
loop from `asyncio.run` and never does.

The fix belongs in `extract.sandbox`, not in the instructions: the child is spawned through
a thread, so isolation no longer depends on which loop the server happened to choose.

**The page is deliberately left able to fail.** The first draft of this entry also proposed
catching whatever a leg raises and reporting it as a finding. That is wrong: an unreadable
artefact and a parser that will not start are already findings — the artefact leg catches
everything and the citation leg catches every `ExtractionError` — and the only thing a
broader catch would have added is swallowing the `NotImplementedError` that made this
diagnosable at all. A 500 with a request id in the log is what a code defect should look
like.

**4.3 Gate 3 — separate a fault from the system working. Done, 2026-08-25.** Three triggers
fired on the MSFT run and only two were faults. `MATERIAL_MISSING_SECTION` and `HIGH_MODEL_UNCERTAINTY` are
real: five sections did not exist and three rated themselves at 0.30. `THESIS_DISAGREEMENT`
is not — the red team's job is to contradict the draft, and a run where it found nothing
would be the one worth worrying about.

The red team is out of the trigger banner and has its own section — each challenge's
severity, its objection at reading width, its basis and its cited evidence — and it still
reaches the report's appendix. The banner now means one thing: something is wrong.
`escalation._thesis_disagreement` and `TriggerKind.THESIS_DISAGREEMENT` are gone; the
challenges were always rows and remain so. The calculations table is closed by default with
a filter over name, period and formula, and the coverage table says *not generated* across
the row for a section that never ran rather than reporting zero coverage for an absence.

**4.4 Settling a disagreement, on the record. Done, 2026-08-25.**
`services.disagreements.settle_by_hand`
existed from the first day of the ladder and nothing reached it, so the page showed two
positions and offered no way to prefer either — which reads as a question the operator is
failing to answer. It is wired: choose a side, give a rationale, and the choice is written
under the operator's name beside the rule that escalated it, which is not overwritten. A
disagreement nobody settles keeps publishing both sides, which stays the default. The labels
follow the kind — "keep the draft's position" and "accept the challenge" for a red-team row,
because asking somebody to choose between A and B on a thesis is asking an unanswerable
question.

**4.5 Gate — confirm the extracted financials. Done, 2026-08-25.** The page listed raw
taxonomy element names and nothing else, so the question it asks — does this gap matter? —
could not be answered from it.

Each unmapped tag now carries its label, the largest figure it held in this filing, the
period that figure belongs to, and what it is as a share of the biggest mapped line; the
rows are sorted biggest share first, so the one that decides the gate is the first on the
screen. Beside it, closed, is what the run *did* capture — because the question is a
comparison, and an operator asked it over element names alone was being asked to hold the
statements in their head. Both tables filter as you type, from markup that is hidden until
a script reveals it, so scripting off gets a complete table rather than a dead search box.

**The largest figure, not the latest**: a tag's most recent observation can be a quarter, a
restatement or a zero, and what is being decided is whether anything material hangs on the
element at all.

A run recorded before this date has no figures in its step output and falls back to the tag
list it always showed.

**4.6 Why a section failed. Done, 2026-08-25, at gate 3.** `sections.writing._failed` already
records
what a section was dealt and why it refused, on the row and in the step's own output (gap
A63), and nothing displayed it — so five failed sections read as five chips indistinguishable
from the twelve that worked, and diagnosing one meant reading a worker log. "Sections in this
draft" is now a record: outcome, the evidence tally by kind, the attempt count, the refusal
in the producer's own words, and the causes counted. **The run console still shows none of
this** and should; gate 3 was the surface an operator was actually on.

**4.7 One weak objection cost a whole run. Done, 2026-08-24.** Found by the first live run
on the merged trunk, which died at `red_team` — the second-to-last step — after £8 and forty
minutes. The adversary returned six challenges; the sixth cited no evidence; a schema
validator raised on it, which failed the parse of the whole `RedTeamReport`, which failed the
step, which failed the run. Five well-evidenced objections were discarded to punish the sixth.

`services.red_team` **already** dropped challenges citing ids the run does not hold, one at a
time, logging each. The schema was simply stricter than the service and fatal where the
service was graceful — so the rule moved to where the other drops happen. An objection
resting on nothing still gets no row; it now costs a challenge instead of a run.

A second attempt fires **only when every challenge was dropped**, which is the case where a
retry rescues the step rather than paying for a second adversary to recover an objection the
report did not need. That gate is one condition in `run_red_team` if it proves wrong.

**4.8 An empty series could not be replayed. Done, 2026-08-24.** From the same run:
`numerical_consistency` failed with 62 findings, every one reading `equity_value#N (did not
replay: TypeError: missing a required argument: 'adjustments')`. None was a real
inconsistency.

An empty sequence argument expands to no input rows, so a record holding none is
indistinguishable from one where the argument was never passed — and most companies have no
non-operating items, so this was the ordinary case rather than the edge. The recorder now
writes an empty series as a structural parameter, which is what it is: no number entered, and
that fact is the thing worth keeping. Replay needed no change; a list parameter already
passes through.

**Forward-only.** Calculations already stored keep the ambiguous shape, so a run recorded
before this date still reports those findings. Re-running the report is the cheaper remedy
than a backfill, and is what the failed run needs anyway.

**4.9 A seed-data downgrade on a used database. Done, 2026-08-24.** Found by a manual run
of the acceptance sheet. Six revisions seed a `section_definitions` row and delete it again
on the way down (0036, 0037, 0039, 0044, 0050, 0052); once a report has used that section
version, `report_sections` holds it and the delete is refused. What an operator got was a
bare `ForeignKeyViolationError` naming a constraint.

Each of the six now counts the citing rows first and **refuses with the remedy** — `N stored
report section(s) cite 'x' at version n … run just reset-research` — rather than letting
Postgres refuse with a constraint name. Deleting the report's own content or repointing it at
an older contract were both rejected: either would change what a stored report says it was
written under, and `ON DELETE RESTRICT` on that column is deliberate.

**The more useful half was the test gap.** `TestRoundTrip` downgrades a throwaway *empty*
database, so it proved the chain reverses on a fresh schema and could never have caught this.
`test_a_seed_downgrade_refuses_when_a_report_still_cites_it` now seeds a realistic run and
asserts the refusal names its remedy — verified by removing the guard and watching it
reproduce the original foreign-key error.

Seeding it realistically is the part worth knowing about: revision 0054's downgrade deletes
any job without a `request_id`, so a job carrying only a work order is swept away three
revisions before 0050 is reached and the guard is never exercised.

**4.10 A static asset's content type came from the operating system. Done, 2026-08-24.** Also
found by a manual run of the sheet, on Windows. `mimetypes` seeds itself from the host —
`/etc/mime.types` on Linux, the registry on Windows — and `.woff2` is in neither Python's own
hardcoded table nor the Windows registry, so the vendored Inter face was served as
`application/octet-stream` there and `font/woff2` on Linux.

Not cosmetic: `base.html` preloads the face as `type="font/woff2"`, and a preload whose
declared type does not match the response is discarded and fetched again — the head start
paid for twice, and slower than no preload at all. Nothing errors, which is why it survived.

`aer.api.app` now pins the types it serves rather than asking the host. The lasting part is
the drift guard: a fresh `MimeTypes()` is Python's hardcoded table alone, which is the one
baseline identical on every machine, and any suffix in the served tree that it cannot name
must be pinned. That fails on Linux — where the existing response assertion passes either
way — so this class cannot come back through CI unnoticed.

**The general lesson is worth more than the fix.** A green Linux suite says nothing about
behaviour that a host supplies. Two of the three defects this sheet has found were invisible
to CI by construction.

**4.11 The pre-commit hooks corrupted the tree they were checking. Done, 2026-08-24.** Three
faults, each independently minor and jointly enough that `just hooks` could not be run:

- The config **pinned ruff 0.14.2 while the project ran 0.16.0**. The two disagree about
  docstring formatting, so the hook rewrote `tests/test_phase5_acceptance.py` into a state
  that made `just lint` fail — a formatter and a linter undoing each other with the
  repository as the battlefield. The pin now follows the project.
- `end-of-file-fixer` appended a newline to `tests/fixtures/fx_report/golden.html`, which a
  golden test compares byte for byte. `tests/fixtures/` now sits in the same exclusion as the
  generated stylesheets and vendored libraries, for the reason all three share: they are
  committed *output*, and rewriting output means it stops matching what produced it.
- `.secrets.baseline` was five weeks stale and failed on 33 findings. All were checked and
  all are false positives — a stub `sk-test` key, SHA-256 digests in fixtures, the pinned
  font hashes, Jupyter cell IDs. The baseline records them as reviewed rather than unseen.

`no-commit-to-branch` also listed `main`, which had been the trunk since the merge, so the
hook forbade committing to the only branch anybody works on. It now guards `master` alone.

All fourteen hooks pass and the working tree is unchanged afterwards, which is the assertion
that matters and the one the sheet now makes.

**4.12 Guidance mode had a flag and no control. Done, 2026-08-25.** The flag, the route and
`data-guidance` on `<body>` shipped with the design tokens; nothing rendered a control for
any of it, so the only way to turn callouts on was to edit a cookie by hand.

The blocker was stated at the time and turned out to be the whole of it: **a form in the
shell needs a CSRF token in the shell**, which means `render()` minting one and setting the
cookie for any handler that did not supply its own. That is now what `render()` does, and
the menu carries both preference controls — guidance, and the light/dark/auto choice that
arrived with it (§4.13). A handler that mints its own token still wins; this only fills in
for the ones that never thought about it.

**4.13 There was no way to choose a colour scheme. Done, 2026-08-25.** Dark mode shipped with
the design tokens and followed `prefers-color-scheme` alone, so the only way to change it was
to change the operating system. Nobody found the control because there was not one.

Light / dark / auto now sits in the menu, remembered in a cookie and stamped on `<html>` by
the renderer. **Not a `<head>` script**, which is the usual way this is done: the cookie is
already in hand when the page is built, so there is no flash to beat, and buying a scripting
dependency to avoid one on an application whose navigation deliberately works without
scripting would be the wrong trade.

`dark:` was redefined as a custom variant answering `[data-theme]` as well as the media
query, and that is what makes the control work at all: without it the shell would have
flipped and forty panels written as `dark:bg-slate-900` would not — a control that works on
some pages is worse than none, because a reader cannot tell which half is broken. What
remains is consistency of the colours themselves, which is §2.5.


**4.14 The draft's figures contradicted the calculations they cited. Fixed 2026-08-25
(ADR 0086).** On the 2026-08-24 MSFT run the draft asserted a quick ratio of 0.93 and a
current ratio of 1.23; the recorded `quick_ratio` calculations were 1.567 and 1.536 and the
`current_ratio` values 1.785 and 1.769. Debt to equity was drafted at 0.09× against 0.299
and 0.229, interest cover at ~50.9× against 40.4 and 45.0, the cash conversion cycle at
−51.8 days against −7.41 and −2.56.

The direction mattered as much as the size: the section concluded liquidity was thin where
the run's own arithmetic says it is comfortable, so a reader taking it at face value would
have reached the opposite view of the balance sheet.

**Only the red team caught it**, for the second time in two live runs.
`numerical_consistency` re-executes stored rows and never reads the prose;
`citation_accuracy` re-reads the quoted excerpt, which was quoted correctly — what was wrong
was the number in the sentence beside it; `figure_plausibility` asks whether a figure is
*possible*, and 0.93 is a perfectly possible quick ratio.

`cited_figure_agreement` closes it at threshold zero: **a claim naming a calculation must
state that calculation's figure.** Structural rather than textual — `claims.calculation_id`
already exists and the writer already sets it — so there is no ratio vocabulary to maintain.
Agreement is the draft's own precision rather than a tolerance, which is what lets 0.09 over
a stored 0.0857 pass while 0.93 over 1.567 fails at every precision. The renderings the
platform actually produces are admitted (a percentage is the fraction times a hundred; money
reaches prose in millions or billions), and a claim resting on a calculation without printing
it is not a violation. It joins `VALIDATION_FAILURE`, so it reaches the banner rather than
only the table.

**4.15 Comps said "for want of usable data" over a deliberate choice. Fixed 2026-08-25.**
Eight peers were discovered on the 2026-08-24 MSFT run and all eight were excluded. Nothing
was broken: `services.comps.UNACQUIRED_PEER_REASON` is the true reason, and this workflow
acquires neither a peer's filings nor a peer's prices (ADR 0059), so a peer recorded by name
alone can never contribute a multiple.

What was wrong was the report. §17 read "every one of the eight proposed peers was excluded
**for want of usable data**", which reads as a failure to get hold of something on a run that
made a deliberate choice — a reader would go looking for a fault. The step already grouped
its exclusions by reason; `WithheldComps` now carries them and the disclosure names them.

The reason itself was rewritten to survive the report's register: the first draft cited the
architecture decision inside the sentence, which is exactly the process language
`presentation_integrity` refuses in a document that should be about a company. The decision
belongs in the code comment; the sentence belongs to the reader.

**The peer gate already said it**, and that is worth recording rather than re-fixing:
*"Confirming records the set; it fetches nothing. Computing a peer's multiple needs its
filings and its prices, and this run acquires neither."*

**What remains is a decision, not a defect.** `propose_peers` is a model step and a gate, and
on the present design its whole output is a list of names and rationales that contribute no
figure. That may be worth the money — a reasoned peer set is not nothing, and it is held for
the day a subscription makes it computable — but it should be a choice somebody made. The
options are to skip peer discovery when no price client is configured, or to acquire peer
filings and prices and make comps actually compute, which is an ADR 0059 amendment and
multiplies the data subscription across the set.

### Decided against

Not deferred. Not on this roadmap. Deciding otherwise needs an ADR, not a ticket.

- **Trade execution and any broker connection.**
- **A portfolio optimiser** — no efficient frontier, no allocation solver.
- **Multi-user deployment.**
- **Investment advice.** Every surface keeps its disclaimer.
- **A `positions` table** (ADR 0083). A position is a calculation.
- **A currency-exchange transaction kind**, until it has a row shape that cannot silently
  double-count a cash balance.
- **A Bank of England adapter.** Its documentation describes a CSV route its own
  `robots.txt` disallows, and reaching around that is circumvention. The consequence is
  real and stays visible: `risk_free_series_for("GBP")` refuses rather than substituting a
  US Treasury yield.
- **FCA National Storage Mechanism fetching** (ADR 0022).
- **An external tracing vendor.** OpenTelemetry spans exist behind a setting.

---

## 5. How to work on this

**Do not skip ahead, and do not fold a later item's work into an earlier one.** The
  dependency order in §3.5 onwards is real.
- **If a prerequisite is missing or an architectural choice is unclear, stop and ask.** A
  wrong foundational choice is expensive to undo here, and guessing has been the more
  expensive option every time it has been tried.
- **A decision that changes an invariant needs a new ADR**, not a code change. The eight
  invariants are in `CLAUDE.md`; what enforces each is in
  [`../developers/knowledge-map.md`](../developers/knowledge-map.md) §5.
- **Record the decision if it was a decision.** Eighty-five records exist because
  reconstructing *why* from a diff does not work.

---

**See also:** [what is built](../product/what-it-is.md) ·
[the decision records](../adr/) · [the archive](../archive/README.md)
