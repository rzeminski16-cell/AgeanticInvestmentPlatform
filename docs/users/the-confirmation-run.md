# The confirmation run

*One live run of the research tool, on Microsoft, done deliberately. Every fix from the last
live run is in the code and none of it has been proved by a real report. This document is the
order to do it in, what to look at before each irreversible step, and how to stop cheaply if
something is wrong.*

> **The one rule.** Nothing expensive happens until you say so. The run is commissioned with a
> ceiling that the drafting step cannot fit under, so it does everything else and then stops
> and waits. You look at what it has, and only then decide to spend the large part.

---

## Where the money is

Every step declares an estimate, and the spend guard refuses to **start** a step whose
projected cost would break the ceiling. These are the platform's own estimates, not a promise
of what the run will actually cost.

| Phase | Steps | Estimate |
|---|---|---|
| **Before drafting** | plan, plan critique, peers, themes, five research steps, assumptions | **£2.14** |
| **Drafting** | every section written | **£5.00** |
| **After drafting** | validation, red team, revision, verdict, challenge brief | **£2.17** |
| | **Whole run** | **£9.31** |

Two ceilings apply at once: the run's own, which you set on the form and can raise while the
run is going, and the month's, which is `AER_MONTHLY_BUDGET_GBP` and defaults to £80. The
platform will not let a single run's ceiling go above `AER_PER_RUN_BUDGET_GBP`, which defaults
to £12.

**What a stop costs you: nothing.** A run stopped at its ceiling is paused, not failed. Its
work is kept, and raising the ceiling and then continuing picks up from where it stopped
without repeating a step you already paid for. That holds *inside* the drafting step too: sections commit one at a
time, and a re-entry writes only the ones that are not written yet. See
[If drafting stops part-way](#if-drafting-stops-part-way).

---

## Stage 0 — before anything spends

Money at risk: **£0**. Do not skip these; each one has cost a live run before.

**0.1 Be running the code you think you are.**

```bash
git status --short          # expect no output
git log --oneline -1        # expect the commit you mean to test
```

**0.2 Bring the services up and the schema forward.**

```bash
just up
just health                 # expect: accepting connections, then PONG
just migrate
just migrate-status         # expect head, with no pending revisions
```

**0.3 Have an account.**

```bash
just seed-user your@email.address
```

**0.4 Check the settings that decide what the run may do.** In `.env`:

- `AER_ANTHROPIC_API_KEY` — set, or nothing will run.
- `AER_HTTP_USER_AGENT` — set to something with a real contact address. SEC refuses anonymous
  traffic, and this is the commonest reason a first run fails at acquisition.
- `AER_MONTHLY_BUDGET_GBP` — confirm what is already spent this month leaves room for £10.
- `AER_EODHD_API_KEY` — optional. Without it the run reports price-derived figures as
  unavailable and does not ask a model for a peer slate. That is a legitimate way to run; just
  know which one you chose before you read the comparables section.

**0.5 Start the two processes, in two terminals.** The web application only enqueues work.
Nothing happens without the worker.

```bash
just dev                    # terminal 1
just worker                 # terminal 2
```

**0.6 Prove the machine before you trust the run.**

```bash
just lint
just typecheck
just verify-audit           # the audit chain is intact
just verify-artefacts       # every archived byte still hashes to its name
```

Open `http://127.0.0.1:8000`, sign in, and confirm the launcher lists the tools and the
research tool offers a new request.

---

## Stage 1 — commission the run

Money at risk: **£0**. Commissioning spends nothing; the first step does.

Go to **Research → new request** and fill it in exactly:

| Field | Value | Why |
|---|---|---|
| Company name | `Microsoft Corporation` | The filer's own name |
| Ticker | `MSFT` | |
| Exchange | `NASDAQ` | |
| As-of date | `2026-08-31` | The FY2026 10-K, for the year ended 30 June 2026, was filed on 29 July 2026. An as-of date at the end of August means a full audited year is available and nothing is half-filed. |
| Which sources | **Use only information available by this date** | Point-in-time. Enforced at acquisition, so the run reads as a clean historical replay. |
| Depth | `standard` | Depth is most of what a run costs. Quick is not what you are testing. |
| Most this report may cost | **`3.00`** | **The important one.** Everything before drafting fits under £3; drafting cannot. The run will do the whole evidence and valuation path and then stop and wait for you. |
| Questions you want answered | Optional | Whatever you actually want to know. This shapes the plan more than any other field. |

Submit, and note the run's id from the console URL. Everything below refers to it as
`<job-id>`.

---

## Stage 2 — the gates before drafting

Money at risk: **about £2**, and only as each step runs.

The run stops at each gate and waits. Approving is a decision recorded under your name against
a hash of exactly what you were shown, so read the screen rather than the summary.

**Gate 1 — the plan.** *Always fires.* Check:

- The sections it intends to write, and that the questions you asked appear in the focus of at
  least one of them.
- **Your skills on this run** — the pinned methodology skills and their versions. If you meant
  to enable one and it is not listed, reject now: pins are taken before the planner runs, and
  a run cannot pick up a skill later.
- The forecast cost against your £3 ceiling.

*Reject if:* the plan misreads what the company does, or the sections do not cover what you
asked. Rejecting costs you the planner's £0.20 and nothing else.

**The sector.** *Fires only for a specialist sector.* Microsoft should not trip it. If it does,
the run has classified the company wrongly, and that is worth stopping for.

**The peers.** *Fires when comparable-company analysis runs.* Every name should be a plausible
comparator you would defend in front of somebody. Remove the ones you would not.

**The themes.** *Fires when themes are proposed.* These file the company in the knowledge
graph. Wrong ones are not expensive, but they persist beyond this run.

**The financials.** *Fires when extracted tags did not map.* This is the one to read slowly.
The gate ranks the unmapped tags by how much of the statement they account for. A large share
unmapped means the statements the valuation is built on have holes in them.

*Stop here if:* a material line is missing. Proceeding is legitimate — the gate exists because
some tags never map — but you are agreeing that the report may be built without those figures.

**The assumptions.** *Fires before a discounted cash flow.* The one gate that approves work
that has not happened yet. Every assumption shows where it came from: a stored fact, a
calculation, or a model's proposal with its justification. Read the proposed ones. The
valuation is only as good as these, and this is the last cheap moment to change them.

---

## Stage 3 — the checkpoint before you spend the £5

Money at risk: **£0 to look**. The run has now stopped at its ceiling, before drafting, with
about £2 spent. The console shows it as stopped against its own ceiling and offers to raise it.

**Do not raise it yet.** Verify these first.

**3.1 The run's own record.**

```bash
uv run aer diagnose <job-id>
```

Every step should read `SUCCEEDED` up to the one refused for the budget. Any step that
succeeded with an error recorded, or attempts greater than one, is worth reading in full with
`uv run aer diagnose <job-id> <step-key>`.

**3.2 The evidence.** On the console, open the sources. Confirm the 10-K you expect is there,
that its excerpts are real text rather than boilerplate, and that nothing published after
31 August 2026 has been admitted.

**3.3 The figures.** Open the calculations. Spot-check three of them: pick a number, open its
formula, and follow its inputs down to the filing they came from. If a figure will not
resolve to a source, stop — that is exactly the failure the whole platform exists to prevent,
and it is worth more than the run.

**3.4 The valuation.** Confirm the method the run chose fits the company, and that its inputs
are the assumptions you approved at the gate.

**If any of this is wrong, stop here.** Cancel the run from the console. You have spent about
£2 and learned what you needed to know. Fix, and commission again.

---

## Stage 4 — release the drafting

Money at risk: **£5**, and it is the step you cannot undo cheaply.

On the console's spend panel, raise the run's ceiling to **`12.00`** — the platform's own
limit, and deliberately more than the £9.31 the rest of the run is estimated to cost.

**Raise it generously, and here is why.** The ceiling is two different tools depending on
where it bites. Before a step it is a free checkpoint: the step never starts and nothing is
wasted, which is exactly what Stage 3 used it for. *Inside* the drafting step it is a stop
part-way through sixteen sections — recoverable, and no longer expensive, but a stop you have
to notice and clear by hand. Use the ceiling to hold the run *before* drafting, and then get
it out of the way.

The raise is recorded under your name. The run continues from where it stopped and repeats
nothing it has already completed as a step.

While it drafts, watch the console. Each section arrives with a status:

- **Generated** — written and validated.
- **Generated with a low-confidence reason** — written, but the platform recorded why it is
  less sure. Read the reason.
- **Failed** — the section was refused and could not be salvaged. One is worth investigating;
  several mean stop and diagnose rather than approve.

A section that is mostly a notice about missing evidence is the failure the last live run
produced. If you see one, that is the thing to report.

### If drafting stops part-way

**You do not pay for the same section twice.** Each section commits its own draft the moment
it is written, and the drafting step is re-entrant: when it runs again it keeps every section
an earlier attempt finished and writes only the ones that are not written yet. So a stop
half-way through sixteen sections costs you the eight that are left, not sixteen.

This covers every way drafting can stop — the ceiling biting mid-step, an API outage, the
worker being killed, the container going away.

**How to use it.** Nothing special: continue the run the same way you continue any stopped
run.

```bash
uv run aer diagnose <job-id>     # what stopped it, and where
uv run aer resume <job-id>       # continue; already-written sections are kept
```

If it stopped on the ceiling, raise the ceiling on the console *before* continuing. Raising it
does not restart the run — that is the button beside the form, and `aer resume` is the same
decision from the terminal — and continuing under a ceiling the step still cannot fit under
just stops the run in the same place. If it stopped on an outage, fix the cause first; the
sections written before it are kept either way.

**What you will see afterwards.** The review page lists every section in the run. A kept one
shows as **Written**, with no evidence count and no try count, and a line saying it was written
by an earlier attempt. That is not a gap in the record: a step writes its record only when it
finishes, so the attempt that wrote those sections — the one that stopped — left no tally
behind. The words are there; only the bookkeeping about how they were produced is not.

**What is *not* kept.** A section that **failed** is drafted again, and so is one that never
started. That is what you want: resuming is for finishing the run, and a failed section has
not been paid for in any useful sense.

**One thing to check.** If you resumed mid-drafting, count the sections on the review page
before you approve at Stage 5. Every section the run owes should be present and written; kept
and freshly written ones are equally finished.

---

## Stage 5 — the final gate

The validation scores, the red team's challenges and the revision all run before gate 2, so
the gate shows you the finished thing with its own critique attached. Check:

- Every section is present and none is a coverage notice.
- The challenges the red team raised, and how the revision answered them.
- The rating and the valuation range, and that both follow from what you read at Stage 3.
- The spend, against the £9.31 estimate.

Approve, and the report is sealed and rendered.

---

## Stage 6 — verify the report you were given

Money at risk: **£0**. All of this reads what the run already wrote down.

```bash
uv run aer acceptance <job-id>     # every requirement, PASS or FAIL, beside what it measured
uv run aer replay-run <job-id>     # re-derives every figure from stored rows; fetches nothing
just verify-audit                  # the decision chain, including your approvals
just verify-artefacts              # every archived byte still hashes to its name
```

Then read the report itself, and do the one check no command can do for you: pick three claims
that matter, follow each footnote to its excerpt, and confirm the excerpt says what the
sentence says it says. Print it to PDF and read the printed page — the layout is part of the
deliverable.

---

## If something goes wrong

| What happened | What to do | What it costs |
|---|---|---|
| A step failed | `uv run aer diagnose <job-id>` reads the recorded error | £0 |
| You want to advance one step at a time | Stop the worker, then `uv run aer step <job-id>` | That step only |
| A run is stopped and you want it to continue | `uv run aer resume <job-id>` | Nothing repeated |
| It stopped part-way through drafting | Same: `uv run aer resume <job-id>` | Only the sections not yet written |
| The run is not worth continuing | Cancel it on the console | Nothing further |
| You want me to look at it | `just diagnose-run <job-id>` writes `run-diagnosis.json` | £0 |

`run-diagnosis.json` is the export that settled the last drafting failure. Read it before you
send it: it contains your prompts and the run's evidence.

---

## What to record

For each stage: what you approved, what you changed, and anything that surprised you. At the
end, the run id, the total spend against the £9.31 estimate, the `aer acceptance` output, and
the report itself.

---

**See also:** [running a report](running-a-report.md) · [reading a report](reading-a-report.md)
· [troubleshooting](troubleshooting.md)
