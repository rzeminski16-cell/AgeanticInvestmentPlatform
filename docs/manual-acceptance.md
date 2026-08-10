# Manual acceptance — the things the test suite cannot tell you

*Written 2026-08-10, against `da91aaa`. Widened 2026-08-10 to four live runs.*

The automated suite is 4,200-odd tests and it is structurally blind to a handful of things.
Every one of them needs a human, real money, or both. This is that list, in the order worth
doing it, with the specific thing to look at rather than "check it works".

**Four live runs, not one.** A single US large-cap is the easy path and proves the least. The
four in §1 are chosen because each one exercises code the others never touch: a different
data source, a blocked valuation model, and an evidence assembly with almost nothing to
assemble. Budget £5–10 for the set.

Two rules for the whole document. **Write down what you see, not whether it passed** — a
number recorded is worth more later than a tick. And **if something looks wrong, stop and say
so before continuing**; several steps build on the one before, and a bad result carried
forward is hard to unpick afterwards.

---

## 0. Before you start

```bash
just up                      # postgres and redis
uv run alembic upgrade head  # should reach 0028
just test                    # ~16 minutes, expect all green
```

`.env` needs:

| Variable | Needed for | If missing |
|---|---|---|
| `AER_ANTHROPIC_API_KEY` | everything | nothing runs |
| `AER_HTTP_USER_AGENT` | SEC EDGAR | 403s that look like network faults |
| `AER_COMPANIES_HOUSE_API_KEY` | **run B only** (UK) | run B cannot be done — free key from developer.company-information.service.gov.uk |
| `AER_EODHD_API_KEY` | prices, beta, comps | those sections say so and carry on |

`AER_HTTP_USER_AGENT` must name you and give a contact address. SEC rejects a generic agent,
and the failure presents as a network problem rather than a configuration one.

**One behaviour changed since this guide was written, and it can stop a run.** The monthly
budget ceiling is now enforced, having been dead code since the engine was built (A22, ADR
0051). Four runs at two or three pounds each sit well under the £80 default, so you should
never meet it — but if you do, the console says **"Stopped on the monthly budget"** rather
than the usual banner, and raising the request's own cap will do nothing. Change it at
`/settings`, or set `AER_MONTHLY_BUDGET_GBP` before starting. Check where you are with
`/costs` if a run stops for a reason that does not match what the request allows.

Take a backup first. You are about to spend money and write real data:

```bash
just backup var/backups/before-acceptance
just verify-backup var/backups/before-acceptance
```

---

## 1. The live runs — everything else is secondary

Nothing in the suite has ever made a real model call with the current prompts. Start the
server and worker (`just dev`, `just worker`) and work through the four below **in order** —
A is the one most likely to work, so a failure there means stop rather than continue.

| | Run | Why this one | Exercises |
|---|---|---|---|
| **A** | A US large-cap you can judge | The baseline, and the only one where you can assess quality | SEC EDGAR, companyfacts, the full DCF path |
| **B** | A UK company on the LSE | Half the product's universe, never run live | Companies House, iXBRL, GBP handling |
| **C** | A bank or an insurer | The sector block is supposed to *refuse* a DCF | Sector enforcement, ADR 0028 |
| **D** | Something small or loss-making | Evidence assembly with little to assemble | Thin-evidence banners, negative denominators |

Supported exchanges are `NASDAQ`, `NYSE`, `NYSE_AMERICAN` and `LSE`. Anything else is
refused at the request form, which is itself worth seeing once.

### Run A — US large-cap

**Pick one you can judge.** The point is to read the output critically, which you cannot do
for a company you have no view on. Microsoft, Costco, John Deere: a long filing history and a
business you could describe from memory.

Drive it through the gates. Expect to stop at the plan, possibly the peer set, at assumptions,
and at the final report.

**Record as you go:**

| What | Where | Expected |
|---|---|---|
| Total spend | `/costs` | Under £3 for one run |
| Where it stopped | the run console | A gate, not an error |
| Sections generated | the report page | Most of 19, with content |
| Claims recorded | `/runs/<id>/claims` | More than zero |
| Citations verified | same page | All confirmed, none "failed" |
| A DCF exists | the valuation page | A value, with WACC and the assumptions behind it |

### Run B — a UK company

**Needs `AER_COMPANIES_HOUSE_API_KEY`.** Skip and say so if you would rather not get one, but
note that this is the half of the universe with no live evidence behind it at all.

Something with an ordinary annual report: Unilever, Diageo, Sage. Exchange `LSE`.

What to watch, beyond the run completing:

- **Currency.** Figures should be GBP throughout, or explicitly converted with the rate shown.
  A GBP company reported in USD without a stated conversion is a real fault.
- **iXBRL.** UK annual reports are inline XBRL — a different parser from the US path. Check
  the facts on `/runs/<id>/financials` look like the filing, not like nothing.
- **Whether it stops at the UK financials gate.** That gate exists for this case.

### Run C — a bank or insurer

Pick a clear one: Lloyds, HSBC, JPMorgan, Aviva. The seeded sector profiles block
`dcf_fcff` for `banks`, `insurers` and `reits`, and `biotech_pre_revenue` blocks both DCF
variants.

**The expected outcome is a refusal, not a valuation.** Specifically:

- The valuation page should say the DCF was **blocked for this sector**, naming why.
- It should not quietly produce a DCF with odd inputs, and it should not fail the run.
- The sector's required metrics should appear instead — for a bank: net interest margin,
  CET1 ratio, cost-income ratio, loan-loss provisions, tangible book value per share.

A bank with a discounted cash flow on the page is the most serious single thing you could
find in this exercise. It would mean the block is a footnote rather than a control.

### Run D — small or loss-making

A recent IPO, a company with two or three years of filings, or one with negative earnings.

This one is about honesty under thin evidence:

- Sections with little to work from should carry a **thin-evidence banner**, not fabricate.
- Ratios with negative or zero denominators should be **absent or marked**, never rendered as
  a confident figure.
- The run should still complete. A company with a short history is a legitimate subject, not
  an error.

### 1a. The prompt reorder — the highest-risk unvalidated change

*Do this on run A, where you can judge the subject.*

ADR 0048 moved the evidence block *ahead* of the instruction in every section-writer call.
No live model has ever seen the new shape; the fake returns scripted drafts and cannot tell
you whether real output got better or worse.

**Read three or four generated sections properly.** Not for correctness of figures — the
suite covers that — but for whether they read like someone who had the evidence in front of
them. Specifically:

- Does the section answer *its own* question, or drift into general commentary?
- Does it use the evidence, or gesture at it?
- Are the numbers the ones in the evidence, or invented plausible-looking ones?

If they read worse than you expect, say so. Reverting `stable_context` in
`SectionWriterAgent` is a small change and the caching is not worth degraded output.

### 1b. The cache hit rate — whether A14 bought anything

**Read this after all four runs**, not after one. The first run of a fresh prompt pays the
write premium and reads nothing; reuse shows up across runs and across sections within a run.

Go to `/costs`.

- **"Served from cache" above zero** — caching is working. Note the percentage.
- **Exactly 0% with more than one call** — the page will show an amber banner. This is the
  interesting failure: it means every call asked for a cache and was refused. Most likely
  every prompt is under the model's minimum (512 tokens on Opus 5, 1024 on Sonnet 5, 4096 on
  Haiku 4.5), which would mean the platform's prompts are simply too small to cache and
  ADR 0048 should be revisited.
- **A dash** — no calls recorded. Something is wrong with metering, not with caching.

Also note the split of fresh / cache-read / cache-write tokens per role. The section writer
is where reuse should show, because sections sharing an evidence policy get an identical
block.

### 1c. Totals, after all four runs

| What | Where | Note |
|---|---|---|
| Total across four runs | `/costs` | The extrapolation to a month is your real budget answer |
| Spend by role | `/costs` | Which role dominates is what routing should target |
| Cache hit rate | `/costs` | See §1b — read it *after* all four, not after one |

---

## 2. Reproduce the run (B8)

*On run A, then once more on run C — a blocked-valuation run has a different shape and is
the one most likely to have nothing on a leg.*

On the run console, press **Reproduce this run**. It costs nothing.

Expect: green, with the four counts non-zero. If any leg fails, the page names which — a
citation that no longer verifies against its artefact is the one worth investigating, because
it means the stored excerpt and the stored document disagree.

Then try the honest negative. Corrupt one artefact deliberately and re-run it:

```bash
# find one, then overwrite it
find var/artefacts -type f | head -1
echo "not the original bytes" > <that path>
```

Press the button again. It must now report that artefact as unreadable. **Restore from the
backup afterwards** — or accept the loss, since it is a test artefact.

One thing to look for on run C: a leg with **zero checked** is not the same as a leg that
passed. A blocked-valuation run may legitimately have fewer calculations; it should not have
zero citations or zero archived exchanges.

---

## 3. Settings (B6/B11)

Go to `/settings`.

1. **Change routing.** Point `source_triage` at `claude-haiku-4-5` and save. Do this *between*
   two of the four runs, and confirm on `/costs` that the later run used Haiku for that role
   while the earlier one still shows what it used. This is the "applies to runs that start
   after it" property, and comparing two real runs is the only way to see it.
2. **Set a budget you will hit.** Per-run budget to £0.20, start a run, confirm it stops at
   the budget rather than running on. This is the one that costs real money if it does not
   work.
3. **Break something on purpose.** Paste malformed JSON into the routing table. Expect a
   refusal with a reason, and the previous value still in force.
4. **Look at the credentials list.** It should say *set* or *not set* and never show a value.
   View source on the page and search for `sk-ant` — nothing should match.

---

## 4. Skills (B10)

1. `/skills/examples` — three examples listed.
2. Choose one, **Review and import**. Confirm you get the diff screen and that nothing is
   installed until you confirm.
3. Import it. Then `/skills/<key>/export` and diff the downloaded file against the original
   in `src/aer/skills/examples/`. **They must be byte-identical.**
4. Edit the skill, save, export again, and re-import the exported file. The diff should show
   *no changes* — if it shows spurious ones, export is re-serialising rather than returning
   the source.
5. Enable a custom section and run it. This is the extensibility story end to end.

---

## 4c. A resumed run — the cache TTL nobody has measured

The prompt cache has a **five-minute TTL**. A run that stops at a gate while you go and read
the plan properly comes back to a cold cache, so the hit rate from an uninterrupted run
flatters what a real gated workflow achieves.

Worth one deliberate measurement. On any run, stop at a gate, **wait ten minutes**, then
approve and let it continue. Compare the cache-read tokens before and after the pause on
`/costs`.

If reuse across a gate is near zero, that is a genuine finding: the five-minute choice in
ADR 0048 suits a run that proceeds without pause and not the way this platform actually
works, and the one-hour TTL — at a 2× write premium instead of 1.25× — would be the better
trade. That is a one-line change, but only worth making on evidence.

---

## 5. Tracing (A13), only if you want it

Untested against a real collector — verified in-process only. If you want to know whether the
wire format is right:

```bash
docker run -d --name jaeger -p 4318:4318 -p 16686:16686 jaegertracing/all-in-one
# then in .env
AER_OTEL_ENDPOINT=http://127.0.0.1:4318/v1/traces
```

Restart the worker, run something, open `http://localhost:16686`. Expect a trace per run with
`step.*` spans and `model.*` spans nested inside them.

**If nothing appears, that is a finding, not a mistake on your part** — nothing has ever
confirmed these spans leave the process correctly. Note it and move on; tracing failing must
not affect the run itself, and that property *is* tested.

---

## 6. Backup and restore — the one nobody tests until they need it

The restore path is exercised in CI against a scratch database, never against yours.

```bash
just backup var/backups/after-acceptance
just verify-backup var/backups/after-acceptance
```

**Only if you are willing to lose the local database**, prove the restore:

```bash
just restore var/backups/before-acceptance   # asks first; drops and rebuilds every table
```

Then check the platform still works and the run history is what the backup held. If you would
rather not, restore into a scratch database instead by pointing `AER_DATABASE_URL` at a fresh
one for a single command.

---

## What to send back

**Numbers:**

- Spend per run for all four, and the total.
- The cache hit rate from `/costs` after all four, and the per-role split.
- Cache-read tokens either side of the pause in §4c.

**Judgements — these are the ones I cannot get any other way:**

- §1a: did the sections read worse, the same, or better than you expected?
- Run C: was the DCF actually refused, and did the page say why in terms you would accept?
- Run D: did thin evidence produce an honest banner or a confident-sounding paragraph?

**And anything that worked but felt wrong to use.** That is the most valuable single thing in
this document and the least likely to be caught by anything I can write.

---

## What this does and does not establish

Passing all of the above means the platform works across the shapes you care about, the
controls fire, the numbers trace, and a run can be reproduced and restored. That is a
reasonable bar for relying on it yourself.

**What was done at my end, so you know what your half is being added to.** The suite is green
(4247 unit tests, 75 browser tests), and beyond that the eight invariants in `CLAUDE.md` were
attacked directly: thirty-six mutations, each breaking one of them the way a careless edit
would, each run against the tests meant to notice. Thirty-one were caught. One escape was an
equivalent mutant and four were real gaps in the suite, all now closed and each re-broken
afterwards to watch the new test fail. Two defects that no mutation could have found came out
of reading instead — a monthly cap that was never enforced and two call paths that composed a
prompt differently from the one they claimed to — because both were claims made only in prose,
and a test suite cannot fail on a claim nobody encoded. See A22–A25 in `docs/gap-analysis.md`.

It still would not establish:

- **A steady-state monthly cost.** Four runs give a per-run figure and an extrapolation.
  Phase 6's acceptance criterion is a measured month.
- **Behaviour under live failure.** SEC rate-limiting, a filing that will not parse, a model
  refusal mid-run. The suite covers these with fakes; nothing has seen them for real.
- **Anything about A5, A7 or A8.** No authentication, no inbound rate limiting, no deployment
  story — all deliberately skipped for personal use, all blocking the moment anyone else
  touches it.
