# Manual acceptance — the things the test suite cannot tell you

*Written 2026-08-10, against `da91aaa`.*

The automated suite is 4,200-odd tests and it is structurally blind to four things. Every
one of them needs a human, real money, or both. This is that list, in the order worth doing
it, with the specific thing to look at rather than "check it works".

Two rules for the whole document. **Write down what you see, not whether it passed** — a
number recorded is worth more later than a tick. And **if something looks wrong, stop and
say so before continuing**; several of these steps build on the one before, and a bad result
carried forward is hard to unpick afterwards.

---

## 0. Before you start

```bash
just up                      # postgres and redis
uv run alembic upgrade head  # should reach 0028
just test                    # ~16 minutes, expect all green
```

Confirm `.env` has `AER_ANTHROPIC_API_KEY` set and `AER_HTTP_USER_AGENT` naming you — SEC
EDGAR rejects a generic agent, and that failure looks like a network problem rather than a
configuration one.

Take a backup first. You are about to spend money and write real data:

```bash
just backup var/backups/before-acceptance
just verify-backup var/backups/before-acceptance
```

---

## 1. The live run — the one that matters most

Everything below is secondary to this. Nothing in the suite has ever made a real model call
with the current prompts.

Start the server and worker (`just dev`, `just worker`), then create a request for a US
company you know well. **Pick one you can judge** — the point is to read the output
critically, which you cannot do for a company you have no view on. Microsoft, Costco, John
Deere: something with a long filing history.

Drive it through the gates. Expect to be stopped at the plan, possibly at UK financials or
the peer set, at assumptions, and at the final report.

**Record as you go:**

| What | Where | Expected |
|---|---|---|
| Total spend | `/costs` | Under £3 for one run |
| Where it stopped | the run console | A gate, not an error |
| Sections generated | the report page | Most of 19, with content |
| Claims recorded | `/runs/<id>/claims` | More than zero |
| Citations verified | same page | All confirmed, none "failed" |

### 1a. The prompt reorder — the highest-risk unvalidated change

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

Go to `/costs` after the run.

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

---

## 2. Reproduce the run (B8)

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

---

## 3. Settings (B6/B11)

Go to `/settings`.

1. **Change routing.** Point `source_triage` at `claude-haiku-4-5` and save. Start a new run
   and confirm on `/costs` that the new run used Haiku for that role while the old run still
   shows whatever it used. This is the "applies to runs that start after it" property.
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

- The spend figure and the cache hit rate from `/costs`.
- Your honest read on section quality (§1a) — worse, same, or better than you expected.
- Anything that failed, with the page you were on.
- Anything that worked but felt wrong to use. That last one is the most valuable and the
  least likely to be caught by anything I can write.
