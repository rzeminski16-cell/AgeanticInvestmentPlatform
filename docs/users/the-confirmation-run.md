# Testing the research tool, step by step

*One live run on Microsoft, done deliberately, with the expected result written next to every
step. Follow it in order. Every step tells you what to do, what you should see, and what to do
if you see something else.*

> **The one rule.** Nothing expensive happens until you say so. You commission the run with a
> ceiling the drafting step cannot fit under, so it does everything else and then stops and
> waits. You inspect what it has. Only then do you release the large spend.

---

## Before you begin

**You will need:** about 20 minutes of attention spread over 60–90 minutes of running, a
terminal, a browser, and an Anthropic API key in `.env`.

**Where the money is.** Every step declares an estimate, and the guard refuses to *start* a
step whose projected cost would break the ceiling. These are the platform's estimates, not a
promise of what the run will cost.

| Phase | Steps | Estimate |
|---|---|---|
| **Before drafting** | plan, plan critique, peers, themes, five research steps, assumptions | **£2.14** |
| **Drafting** | every section written | **£5.00** |
| **After drafting** | validation, red team, revision, verdict, challenge brief | **£2.17** |
| | **Whole run** | **£9.31** |

Two ceilings apply at once: this run's, which you set on the form and can raise while it runs,
and the month's (`AER_MONTHLY_BUDGET_GBP`, default £80). No single run's ceiling may go above
`AER_PER_RUN_BUDGET_GBP`, default £12.

**A stop costs you nothing.** A run stopped at its ceiling is *paused*, not failed. Everything
it has done is kept, and continuing picks up where it stopped without repeating a step you
already paid for — including part-way through drafting.

**Write these down as you go:** the run id, the spend at each gate, and anything that surprised
you.

---

## Stage 1 — Prepare

**Money at risk: £0**, except step 1.6 which costs a fraction of a penny. Do not skip these;
each one has cost a live run before.

### 1.1 Be running the code you think you are

```bash
git status --short
git log --oneline -1
```

**Expect** — `git status` prints nothing at all. `git log` prints the commit you mean to test.

**If not** — commit or stash your changes first. Testing a dirty tree tells you nothing about
what is on the branch.

### 1.2 Start the services

```bash
just up
just health
```

**Expect** — `health` prints two lines: `/var/run/postgresql:5432 - accepting connections`,
then `PONG`.

**If not** — Docker is not running, or the containers did not come up. `just logs` shows why.

### 1.3 Bring the schema forward

```bash
just migrate
just migrate-status
```

**Expect** — `migrate` ends without error. `migrate-status` prints the current revision, then
the head. **They must be the same.**

**If not** — a pending revision means the database is behind the code. Run `just migrate`
again and read the error.

### 1.4 Have an account

```bash
just seed-user your.email@example.com
```

**Expect** — `Created user your.email@example.com (owner).`, or `User … already exists;
nothing to do.` Either is fine.

### 1.5 Check the configuration

```bash
just config
```

**Expect** — the effective settings as JSON, with every secret **masked**. Confirm four things:

| Setting | Should be |
|---|---|
| `anthropic_api_key` | `"**********"` — masked, never your actual key |
| `per_run_budget_gbp` | `12.00` (or whatever you intend as the platform ceiling) |
| `monthly_budget_gbp` | `80.00`, and not already spent — check `/costs` once the app is up |
| `http_user_agent` | your real contact details, not the placeholder |
| `eodhd_api_key` | *optional.* Without it no peers are proposed and the comparables table is empty — expected, and the valuation page says so. With it, peers are proposed, priced and tabled |

**If not** — a missing API key stops the run at the first model call; a user agent that does
not identify you is how a fetcher gets blocked by a filing site.

### 1.6 Prove the wire contract against the real API

```bash
just test-live
```

**Expect** — passes in under a minute. This costs a fraction of a penny.

**Why it is worth it** — the offline suite uses a fake provider, which is a different
implementation of the protocol rather than a fake transport. It never sees a real payload, so
it cannot notice when the API stops accepting one. That is exactly how a deprecated field once
reached a live run and failed an hour and five pounds in. This is the cheapest possible way to
rule that out.

**If not** — **stop.** Do not start the run. Read the failure; it is telling you the API and
this code no longer agree.

### 1.7 Start the web app and the worker

Two terminals, both left running:

```bash
just dev        # terminal 1 — the web app
just worker     # terminal 2 — the background worker
```

**Expect** — the app serves at <http://127.0.0.1:8000>. The worker prints its startup lines and
then sits idle.

**If not** — **the worker matters.** The web process only enqueues; nothing happens without it.
A run that sits at "queued" for ever is almost always a worker that is not running.

---

## Stage 2 — Commission the run

**Money at risk: £0.** Creating a request spends nothing.

### 2.1 Open the form

Go to <http://127.0.0.1:8000/requests/new>.

**Expect** — a form in five blocks: Company, Date and hindsight, Depth and spending, Questions,
and a closed "Refine this mandate — optional" disclosure.

### 2.2 Fill it in

| Field | Value | Why |
|---|---|---|
| Company name | `Microsoft Corporation` | The listed entity, not a parent |
| Ticker | `MSFT` | |
| Exchange | `NASDAQ` | |
| ISIN | *leave blank* | Optional; one less thing to get wrong |
| As-of date | `2026-08-31` | Comfortably after the FY2026 10-K was filed, so there is a full year to read |
| Which sources may this run use? | **Use only information available by this date** | Point-in-time on. This is the honest test |
| Depth | `standard` | Depth is most of what a run costs |
| Most this report may cost | **`3.00`** | **The important one — see below** |
| Questions | two or three, one per line | They shape the plan more than anything else |

Suggested questions:

```
Is the cloud margin expansion durable, or is it capitalised infrastructure flattering it?
What would have to be true for the current multiple to be justified?
How much of the growth is AI revenue that did not exist three years ago?
```

**Why `3.00`.** The whole run is estimated at £9.31 and drafting alone at £5.00. A ceiling of
£3.00 lets every step before drafting run — they total about £2.14 — and then stops the run
dead at drafting, because the guard refuses to *start* a step that would break the ceiling.
That gives you a free checkpoint at exactly the moment before the money.

### 2.3 Submit

**Expect** — you land on the request's own page, which says *"Nothing has been fetched and
nothing has been spent"* and offers a **Start the run** button.

**If not** — field errors appear inline. The ticker takes letters, digits, dot and hyphen; the
as-of date cannot be in the future.

### 2.4 Start the run and note the id

Press **Start the run**.

**Expect** — you are taken to the run console at `/runs/{job-id}`. **Copy that job id now** —
every command below takes it.

---

## Stage 3 — The plan gate

**Money at risk: about £0.50.** The planner and its critic run, then the run stops and waits
for you.

### 3.1 Watch the console

**Expect** — a list of every step the workflow declares, a pulsing marker and a ticking clock
on the one running, and a "server last checked at…" line. A step that calls a model changes
nothing for minutes; that is normal.

**If not** — if nothing moves and the "last checked" line goes stale, the worker has died.
Check terminal 2.

### 3.2 Read the plan

**Expect** — the console shows the run as awaiting your approval, with a link to
`/runs/{id}/plan`.

On that page, check:

| Check | What good looks like |
|---|---|
| The sections it intends to write | The full set, nothing obviously missing |
| **The source list** | It names SEC filings — the 10-K you would have reached for yourself |
| The expected cost and time | In the region of the estimates above |
| The risks it already sees | Sensible, specific to Microsoft |
| The critique | A second pass that actually challenged the plan, not a rubber stamp |

**This is the cheapest moment to find out the run is pointed at the wrong thing.** If the
source list does not name the filings you would use, stop here.

### 3.3 Approve

Write a sentence in **Notes** — it goes in the audit trail — then press **Approve and
continue**.

**Expect** — back to the console; the run continues.

**If it refuses** with a message about the payload having changed: that is the approval hash
doing its job. Reload the page and read it again — what you were shown is not what is there
now.

---

## Stage 4 — The middle of the run

**Money at risk: about £1.64 more**, bringing you to roughly £2.14 in total.

The run now acquires filings, classifies the company, proposes peers and themes, extracts the
financial statements, computes the ratios, runs five research workers in parallel and proposes
the valuation assumptions.

### 4.1 The conditional gates

These appear **only if the company makes them necessary**. Each stops the run and waits.

| Gate | Page | Appears when | What to check |
|---|---|---|---|
| Sector specialist | `/runs/{id}/sector` | The accounting differs enough that the standard model would be wrong | For Microsoft, this should **not** appear. If it does, read why before approving |
| Peer set | `/runs/{id}/peers` | Comparables were proposed. It passes straight through when nothing comparable is in the database | The peers are real comparables. Without a price feed (`AER_EODHD_API_KEY`) the step proposes only what the platform already holds, spends nothing, and the page says so |
| Theme set | `/runs/{id}/themes` | Themes were proposed. It passes straight through when none were | The themes are the ones you would research |
| Unmapped concepts | `/runs/{id}/financials` | A filing used tags the concept map does not know | Which statement lines would be lost. This is a silent gap becoming a decision |
| Assumptions | `/runs/{id}/assumptions` | A valuation model applies | **The most important one — see 4.2** |

**Expect** — for a US large-cap that files clean iXBRL, several of these pass straight
through. Two or three stops is normal.

### 4.2 The assumptions gate

This is the only gate that approves work **not yet done**.

**Expect** — around nine inputs for a discounted cash flow, each with the model's
justification: terminal growth, equity risk premium, and so on. **Every one of them should be
a number no filing could answer.** A number that could have been read off a statement has no
business here.

**Check each one.** Everything downstream carries these as recorded inputs, so the report can
always answer "what was this resting on?" — but only you can answer "were they sensible?"

**If a required input is missing** — a beta, a risk-free rate — the gate stops and asks you to
supply it. Do so, then continue.

### 4.3 Let it run to the ceiling

**Expect** — after the assumptions gate and the valuation, the run stops with a red-edged
panel headed **"Stopped before overspending"**:

> The next step would take this run past a spending cap, so it stopped before making the call
> rather than after paying for it.

The spend panel should read roughly **£2.14 of £3.00**.

**This is the run working exactly as designed.** It is the checkpoint you bought.

**If it stopped earlier than drafting** — say part-way through the research steps — that just
means real spend ran a little ahead of the estimates. It is not a fault. Raise the ceiling to
`3.50` (Stage 6.1 shows how), continue, and it will stop at drafting instead.

**If it says "Stopped on the monthly budget"** — that is a different ceiling. Raising this
run's cap will not release it; change the monthly budget in `/settings`.

---

## Stage 5 — The checkpoint before you spend the £5

**Money at risk: £0 to look.** This is the whole point of the £3.00 ceiling. Do not raise it
until all five checks pass.

### 5.1 The run's own record

```bash
uv run aer diagnose <job-id>
```

**Expect** — a header like:

```
Run 1a2b… — vertical_slice_v1 @ a60b607fca88 — BUDGET_EXCEEDED — £2.1400 spent — step mode off
```

then one line per step:

```
  [SUCCEEDED] plan  attempt 1  12.3s  £0.1842  1 model call(s)
  [SUCCEEDED] critique_plan  attempt 1  31.0s  £0.2903  1 model call(s)
  …
```

**Every step should read `SUCCEEDED`**, up to the one refused for the budget.

**Gate steps read `attempt 2`, and that is correct.** A gate runs once to stop the run and
wait for you, and once more after you approve, to confirm the decision and continue — two
executions of one step, recorded on one row. So `gate_plan`, `gate_peer_set`,
`gate_theme_set`, `gate_unmapped_concepts` and `gate_assumptions` will each show `attempt 2`
when you passed through them, and a gate the run passed straight through shows `attempt 1`.
Every *other* step should show `attempt 1`.

**If not** — a non-gate step with `attempt 2` or higher, or any step that succeeded with an
error recorded, is worth reading in full:

```bash
uv run aer diagnose <job-id> <step-key>
```

That prints the step's stored output and every model call's tokens and archived payload hashes.

### 5.2 The evidence

Open `/runs/{id}/sources`.

**Expect** —

- The Microsoft FY2026 10-K is listed (period ending 30 June 2026).
- Excerpts are **real prose from the filing**, not boilerplate or navigation furniture.
- **Nothing published after 31 August 2026 has been admitted.** Point-in-time is enforced at
  acquisition, in code — so if something later is there, that is a real bug and worth stopping
  for.
- Refused sources are listed too, with the reason. Refusals are a good sign, not a bad one.

**Expect the list to be filings, and little else — that is the design, not a gap.** A
*source* here is a document the platform fetched, hashed and archived, so that a sentence in
the report can point at an exact excerpt. Regulatory filings and issuer material qualify. News
and market commentary reach the research workers a different way: each worker may run a
handful of web searches, but what comes back is a **listing** — titles and URLs, labelled
unverified — used for leads, never for citations. A page found by search becomes a source only
if it is fetched through the same gate every other document passes. So there is no "Yahoo
Finance" row and no sentiment score, and there will not be one: a claim that could not be
traced to a hashed excerpt would break the platform's first invariant.

You can see whether the workers searched at all: `/costs` lists each search as its own row
under `web_search`.

### 5.3 The figures

**There are no claims yet, and that is correct.** A *claim* is a sentence the section writer
proposes with its citation, and no section has been written. `/runs/{id}/claims` is empty
until after Stage 6, and it is where you check citations at Stage 7. What exists now, and
what to check, is the **calculations**.

Open `/runs/{id}/valuation`. Every figure in the terminal-methods table is a link.

**Pick three numbers that matter and follow each one down**: the figure → its calculation
page → the **Lineage** table on it → the fact or calculation each input came from → the
filing.

**Expect** — each calculation page shows the formula, the parameters, and a lineage row per
input naming where that input came from. Every one resolves to a stored fact with a source,
or to a recorded calculation whose own inputs resolve. A page saying *no lineage* for an
input that plainly came from a filing is the thing to stop for.

**If a figure will not resolve to a source — stop.** That is precisely the failure this whole
platform exists to prevent, and finding it is worth more than the run.

### 5.4 The valuation

Open `/runs/{id}/valuation`.

**Expect** — the method fits the company (a discounted cash flow for Microsoft, not a bank's
residual-income model), and its inputs are the assumptions **you approved** at the gate, not
different ones.

**Three things you will meet on this page, and what they mean:**

- **A tag reading "over 75% terminal value"** beside the terminal-value share. It means that
  more than three quarters of the enterprise value comes from the terminal assumption rather
  than from the forecast years you can check — the valuation is a statement about that one
  assumption. The sentence under the table says the same in full. It is common for a
  long-duration growth business and is a fact about the model, not a fault.
- **The comparables table.** With `AER_EODHD_API_KEY` set and the peer gate passed, expect
  the subject and each confirmed peer as a row, one column per multiple, the peer median
  beneath, and the excluded peers named with their reasons. Without the key the section is
  empty and the sentence beneath it says so — a peer contributes nothing without a multiple,
  so none is proposed (see 1.5). If the section is empty and the sentence does *not* say
  that, check what the run recorded: `uv run aer diagnose <job-id> comps` (`comps: True`
  and a `peers` count means a table was built) and `uv run aer diagnose <job-id>
  acquire_prices` (`prices: True` means the feed answered). A built table the page does
  not show is a page fault, and worth reporting.
- **The sensitivity heatmap.** Each cell is a recorded calculation, labelled at a glance's
  precision; the full-precision figures are in the table beside it, each a link. The picture
  is a reading aid, never the record.

### 5.5 The front page

**Expect** — the at-a-glance block is present and holds Microsoft's figures only. A withheld
block means the platform refused to mix issuers, which is a refusal you want to understand
before drafting.

### 5.6 Decide

**All five pass →** go to Stage 6.

**Any one fails →** press **Cancel** on the console. You have spent about £2 and learned
exactly what you needed to. Fix it, and commission again.

---

## Stage 6 — Release the drafting

**Money at risk: £5**, and this is the step you came to test.

### 6.1 Raise the ceiling

On the console's spend panel, enter **`12.00`** and press **Raise the ceiling**.

**Expect** — the panel now reads `£2.14 of £12.00`. Nothing else happens; raising the ceiling
spends nothing and does not restart the run.

**Raise it generously, and here is why.** The ceiling is two different tools depending on where
it bites. *Before* a step it is a free checkpoint — the step never starts and nothing is
wasted, which is exactly what Stage 5 used it for. *Inside* the drafting step it is a stop
part-way through sixteen sections: recoverable and no longer expensive, but a stop you have to
notice and clear by hand. £12.00 is the platform's own per-run ceiling and deliberately more
than the £9.31 the rest of the run should cost.

### 6.2 Continue the run

Press **Continue this run**.

**Expect** — the run restarts from where it stopped. The steps already paid for are skipped,
not repeated. The raise and the decision to continue are both recorded under your name.

### 6.3 Watch the sections arrive

Sections draft four at a time, each committing as it finishes. Expect this to take a while.

**Expect** — each section shows one of:

| Status | Meaning | What to do |
|---|---|---|
| **Generated** | Written and validated | Nothing |
| **Generated**, with a low-confidence reason | Written, but the platform recorded why it is less sure | **Read the reason** |
| **Failed** | Refused and could not be salvaged | One is worth investigating; several mean stop and diagnose rather than approve |

**The failure to watch for**: a section that is mostly a notice about missing evidence. That is
what the previous live run produced. If you see one, that is the thing to report.

### 6.4 If drafting stops part-way

**You do not pay for the same section twice.** Each section commits its own draft the moment it
is written, and the drafting step is re-entrant: when it runs again it keeps every section an
earlier attempt finished and writes only the ones that are not written yet. A stop half-way
through sixteen sections costs you the eight that are left, not sixteen.

This covers every way drafting can stop — the ceiling biting mid-step, an API outage, the
worker being killed, the machine going away.

**What to do** — nothing special. Continue it the way you continue any stopped run:

```bash
uv run aer diagnose <job-id>     # what stopped it, and where
uv run aer resume <job-id>       # continue; already-written sections are kept
```

**Expect** — `Run <job-id> is queued; the worker will continue it.`

- **Stopped on the ceiling?** Raise the ceiling on the console *before* continuing, or it will
  stop in the same place.
- **Stopped on an outage?** Fix the cause first. The sections written before it are kept either
  way.

**What you will see afterwards** — on the review page, a kept section shows as **Written** with
no evidence count and no try count, and a line saying it was written by an earlier attempt.
That is not a gap: a step records its output only when it finishes, so the attempt that wrote
those sections — the one that stopped — left no tally behind. The words are there; only the
bookkeeping about how they were produced is not.

**What is *not* kept** — a section that **failed** is drafted again, and so is one that never
started. That is what you want.

---

## Stage 7 — The final gate

**Money at risk: about £2.17.** Validation, the red team, the revision, the verdict and the
challenge brief all run *before* this gate, so it shows you the finished thing with its own
critique attached.

Open `/runs/{id}/review`.

**Expect** — and check each:

| Check | What good looks like |
|---|---|
| **Sections in this draft** | Every section present. None is a coverage notice |
| **The validation results** | Citation accuracy, temporal compliance, numerical consistency, source coverage, completeness — each a number against a threshold, all passing |
| **The red team's bear case** | Real objections from a separate pass, not a summary of the thesis |
| **The revision** | It answered the challenges, or said why it did not |
| **The challenge brief** | For each unsettled challenge: what keeping the draft assumes, what accepting it assumes, and which way it leans. *Advice beside your decision, never the decision* |
| **The rating and valuation range** | Both follow from what you read at Stage 5 |
| **The spend** | In the region of £9.31 |
| **Every claim** (`/runs/{id}/claims`) | Evidence verified |

**Then approve.** Write your reason in Notes and press **Approve and continue**.

**Expect** — the console shows a **Read the report** button.

**Reject instead** if the draft is not sound. Nothing is rendered, and nothing is lost.

---

## Stage 8 — Verify what you were given

**Money at risk: £0.** All of this reads what the run already wrote down.

### 8.1 The four commands

```bash
uv run aer acceptance <job-id>
uv run aer replay-run <job-id>
just verify-audit
just verify-artefacts
```

**Expect, from `acceptance`** — seven rows, each with its requirement and what was measured:

| Check | Required | Should read |
|---|---|---|
| `report` | the run produced a report | `[PASS] approved and immutable` |
| `sections` | all but at most one generated, none pending | `[PASS] 18 of 18 generated, 0 pending` |
| `citations` | every citation verified or overridden with a reason | `[PASS] N of N verified` |
| `metrics` | every exercised blocking metric passed | `[PASS] … none failing` |
| `cited_sources` | cited documents belong to the subject | `[PASS] 0 foreign issuer(s) cited` |
| `front_page` | the at-a-glance block is present and the subject's | `[PASS] present, N headline row(s)` |
| `spend` | reported for comparison | `[·] £9.xx` — informational, never fails |

ending with `Every requirement holds.`

A standard run resolves **eighteen sections**: sixteen written by a model, and two the
platform fills itself from the run's own recorded state (`prior_research_comparison` and
`validation_disagreements`). Both counts appear in the acceptance row above.

**Expect, from `replay-run`** — `Run <id> reproduces: N calculation(s), N citation(s), N
artefact(s) and N model call(s) all still hold.`

**Expect, from `verify-audit`** — `N audit event(s) checked, the chain is intact.`

**Expect, from `verify-artefacts`** — `N artefact(s) checked, all intact.`

**If any of them exits non-zero** — that is the finding. Each names what failed and where.

### 8.2 The three checks no command can do for you

1. **Follow three footnotes.** Pick three claims that matter, click each footnote through to
   its excerpt, and confirm the excerpt actually says what the sentence says it says.
2. **Read it as a reader.** Does it argue something, or does it recite? Is the bear case
   present in the text, or only in the appendix?
3. **Check the numbers you already checked.** The figures you traced at Stage 5 should appear
   in the prose saying the same thing.

### 8.3 The printed page

Print the report to PDF and read the printed pages.

**Expect** — tables do not split badly, figures sit with the text that discusses them, and
footnotes resolve. The layout is part of the deliverable.

---

## If something goes wrong

| What happened | What to do | What it costs |
|---|---|---|
| A step failed | `uv run aer diagnose <job-id>` reads the recorded error | £0 |
| One step in full | `uv run aer diagnose <job-id> <step-key>` | £0 |
| You want to advance one step at a time | Stop the worker, then `uv run aer step <job-id>` | That step only |
| A run is stopped and you want it to continue | Console **Continue this run**, or `uv run aer resume <job-id>` | Nothing repeated |
| It stopped part-way through drafting | The same. Already-written sections are kept | Only the sections not yet written |
| Nothing is moving at all | Check the worker terminal | £0 |
| The run is not worth continuing | **Cancel** on the console | Nothing further |
| You want me to look at it | `just diagnose-run <job-id>` writes `run-diagnosis.json` | £0 |

`run-diagnosis.json` is the export that settled the last drafting failure. **Read it before you
send it** — it contains your prompts and the run's evidence.

---

## What to record

For each stage: what you approved, what you changed, and anything that surprised you. At the
end:

- The run id.
- Total spend against the £9.31 estimate.
- The `aer acceptance` output, in full.
- Any section that was low-confidence or failed, and its reason.
- Any footnote that did not say what the sentence claimed.

That last one is the finding that matters most. Everything else the platform can check itself.

---

## Reference: the steps, in order

| # | Step | Estimate | Gate |
|---|---|---|---|
| 1 | `plan` | £0.20 | |
| 2 | `critique_plan` | £0.30 | |
| 3 | `gate_plan` | | **always** — `/runs/{id}/plan` |
| 4 | `acquire` | | |
| 5 | `classify` | | |
| 6 | `gate_sector_specialist` | | conditional — `/runs/{id}/sector` |
| 7 | `propose_peers` | £0.02 | |
| 8 | `gate_peer_set` | | conditional — `/runs/{id}/peers` |
| 9 | `propose_themes` | £0.02 | |
| 10 | `gate_theme_set` | | conditional — `/runs/{id}/themes` |
| 11 | `acquire_prices` | | |
| 12 | `extract` | | |
| 13 | `gate_unmapped_concepts` | | conditional — `/runs/{id}/financials` |
| 14 | `calculate` | | |
| 15–19 | `research_company`, `research_industry`, `research_macro`, `research_recent_developments`, `research_technical_context` | £0.30 each | |
| 20 | `comps` | | |
| 21 | `propose_assumptions` | £0.10 | |
| 22 | `gate_assumptions` | | conditional — `/runs/{id}/assumptions` |
| 23 | `value` | | |
| 24 | **`draft`** | **£5.00** | |
| 25 | `validate` | £0.02 | |
| 26 | `red_team` | £0.35 | |
| 27 | `revise` | £1.50 | |
| 28 | `verdict` | £0.10 | |
| 29 | `brief_challenges` | £0.20 | |
| 30 | `gate_final` | | **always** — `/runs/{id}/review` |
| 31 | `render` | | |

---

**See also:** [running a report](running-a-report.md) · [reading a
report](reading-a-report.md) · [troubleshooting](troubleshooting.md)
