# Gap analysis — what is missing, as of 2026-08-08

*Updated after A1–A4, then A21, B13 and A9, then B1, A19 and A20, then B2 and B3, then the
test-suite health items A16–A18 and the data-lifecycle items A10–A12, and most recently
A22–A25, which came out of deliberately breaking the invariants to see what noticed. Items
struck through are done; the reasoning is kept because a gap and the shape of its fix are
worth reading together.*

*A22 and A23 are worth reading before the rest of List A, because neither was found by a
failing test: both are things the code **said** it did. A guard populated with a ceiling it
never compared, and a docstring promising three call paths composed the same prompt when two
of them did not. A test suite cannot fail on a claim nobody encoded.*

*A26 and A27 came from turning the same method on `src/aer/calc/`, and carry their own
lesson about the method. The first pass reported five escapes; two of them were not holes at
all — the tests existed in a file the selector did not name. **A mutation that survives a
narrow selector has found the edge of the selector, not a gap in the suite**, and the
survivors were only called holes after being re-run against every test file that can
transitively reach the module.*

*Since A16 was closed the whole suite runs in one process again — 4256 unit tests in about
21 minutes and 75 browser tests in about 3, both green, and green in three different file
orderings — so the figures quoted below are measured rather than remembered. The unit suite
is `pytest --ignore=tests/e2e`, which is what `just test` runs, and the split is not
cosmetic: Playwright's synchronous API drives an asyncio loop on the main thread and holds
it open for the life of its session fixture, so every asyncio fixture afterwards in the same
process dies with "Runner.run() cannot be called from a running event loop". The `test-all`
recipe says so in a comment. **A bare `pytest` is not the gate** — it collects both halves
and reports thousands of errors that say nothing about the code.*

*A30–A33 are the first findings that came from live runs rather than from the suite. A30 and
A31 share a cause: the fake provider is an alternative implementation of the protocol, not a
fake transport, so nothing that only runs against it can see the wire, and nothing that never
exhausts a ceiling can notice one is too low. A33 was found by asking what A31's fix made
worse, which is a question worth asking of every change that raises a limit. A34 is the
same disease's third appearance — a number guessed before measurement — and its fix, by
operator direction, removes the class of number rather than resizing the instance.*

Written after the first end-to-end live runs, from the code rather than from the plan. The
phase specifications in `docs/PLAN.md` remain the authority on *scope*; this document is an
honest account of the distance between that scope and what a run actually does today.

Two lists, as asked: what is missing behind the surface, and what is missing in front of it.

---

## The finding that dominated both lists — now closed

**A large, tested deterministic layer existed that the live workflow never called.**

`src/aer/calc/` holds `statements.py`, `ratios.py`, `quality.py`, `wacc.py`, `dcf.py`,
`comps.py`, `fx.py`, `prices.py` and `bridge.py` — built through Phase 3, each with unit and
property tests. The production workflow is still `vertical_slice_v1`, and its `calculate`
step computes exactly one number: revenue CAGR.

```
{"job_id": "…", "count": 1, "names": ["cagr"], "event": "calculations.persisted"}
```

Everything else follows from that. The valuation page is empty because no run has ever
produced a discounted cash flow. The comparables page is empty for the same reason. The
balance-sheet, cash-flow, earnings-quality and DCF sections have one figure between them to
write from, so they write almost nothing even when the writer is working.

The services were reachable from the API and the web — `services/valuation.py`,
`services/scenarios.py`, `services/assumptions.py` all have routes — and **no workflow step
ran them**. A wiring gap, not a missing-feature gap, and the highest-value work in the
system at the time it was written.

`aer.services.analysis` closed it for the statements, the ratio suite and the
earnings-quality signals: a two-year scene now persists more than ten calculations where it
persisted one. **The valuation is a different kind of gap and stays open** —
`inputs_from` refuses to run without a confirmed assumption for every driver and scalar,
and says why: a terminal growth rate this platform chose "would be its opinion presented as
the operator's". So a DCF needs an operator working through the assumptions page, or an
agent that proposes assumptions — and that is a new role, which ADR 0035 says needs an ADR
before it needs code.

**That ADR is now written (0046) and two thirds of the answer is built.** Six of the eight
assumptions have a history in the filings the run already holds, so
`aer.services.assumption_proposals` derives them arithmetically with their basis stated.
The two that no filing answers — the perpetual growth rate and the exit multiple — come
from the `assumption_proposal` role, whose output contract has a field for each and no
other fields, whose bounds are checked in code, and whose out-of-band value is *refused*
rather than clamped. What remains for B2 is the gate that shows an operator every proposed
value with its justification, and the workflow step that runs the valuation once they have
confirmed them.

---

## List A — backend, safety, security, optimisation, operations

### Correctness and invariants

| # | Gap | Notes |
|---|---|---|
| ~~A1~~ | ~~**The workflow does not invoke the calculation suite**~~ | **Closed.** `aer.services.analysis` assembles statements, ratios and quality signals for each annual period into one ledger, and `calculate` runs it. One calculation a run became more than ten on a two-year scene. The DCF stays out deliberately: it refuses to run without confirmed assumptions, and an agent that proposed them would be a new role needing an ADR (0035). |
| ~~A2~~ | ~~**Unmetered model spend**~~ | **Closed.** The schema now goes to the wire as a dict, so the SDK sends it and skips the client-side parse: the stream always completes, the usage is always whole, and validation happens where a failure is a value already metered. `SpentButUnusableError` carries the bill and both payloads; `Agent.run` writes the `agent_runs` and `costs` rows before re-raising, with `stop_reason` marking them. |
| ~~A3~~ | ~~**The companyfacts quarantine**~~ | **Closed, and recorded as ADR 0044.** The aggregate takes the date of its newest component — the day it could first have existed — at 0.9 confidence to mark it derived. A current run gets a citable primary source; a historical run still quarantines it, now for a reason that is true. A side effect worth noting: the validator's date-adjudication call has disappeared from every run, because there is nothing left to adjudicate. |
| ~~A21~~ | ~~**A filing is fetched and only lightly read**~~ | **Closed, deterministically.** Forty paragraphs in document order is the cover page, the listing table and the transfer agent's address — all genuinely in the artefact, none of it citable by a research section. Two passes replace it: the document is cut at its statutory item headings (`Item 1`, `Item 1A`, `Item 7`, where the form *obliges* the prose to be), then paragraphs inside those items are scored on the vocabulary a research report uses and the best forty kept, emitted in the filing's own order. No model call, so the selection replays. A filing with no recognisable headings is read whole rather than not at all. |
| ~~A4~~ | ~~**One source per run**~~ | **Closed at the chosen scope.** `aer.services.filings` fetches the latest annual report and the recent current reports from the submissions index, inside the point-in-time window, each dated by its acceptance and excerpted so it can be cited. SEC full-text search, issuer-IR discovery and Companies House remain uncalled — see B13. |
| ~~A22~~ | ~~**The monthly budget cap was never enforced**~~ | **Closed.** `BudgetGuard` has carried `monthly_cap_gbp` since the engine was written, `aer.services.runs` populated it from settings on every run, its docstring said "two ceilings, and both matter" — and `check` read only the per-run one. The field was dead, and `costs` even carried an index commented "the monthly-budget query" that nothing ran. Against invariant 6 this is the worst shape a cap can have: not one that only warns, but one that does not warn either, so an £80 month held a £2.50 request thirty-two times over in silence. `spend_this_month` now sums the calendar month in **UTC** — the boundary has to agree with `occurred_at`, or the cap resets at a different instant depending on where the operator is standing — and deliberately **does not join** to the job, because `costs.job_id` is `SET NULL` precisely so deleting a request cannot erase what it cost, and a join would hand that escape straight back. The refusal names its `scope`, and the console says so: raising a request's own cap releases a per-run stop and does nothing at all to a monthly one. |
| ~~A23~~ | ~~**Two of the three call paths composed their own user turn**~~ | **Closed.** A leftover from A14, in the place A14 did not look. `Agent.run` was taught to send `stable_context` as a separate cached block; `run_batch` and `estimate_input_tokens` each built `Message(role="user", …)` by hand and predate it, so both omitted the prefix. For the batch path that is worse than a miscount — the prompt actually **sent** has its evidence missing, and the input-cap check under-counts to match, so nothing raises and a model is simply asked to write from nothing. Inert today only because the one role with a stable context does not go through the batch path, which is not a property anything enforced. `run` and `run_batch` now share `Agent.compose_turn`, and a test parses `base.py` and asserts a user turn is constructed in exactly one place, because the real invariant is "there is one constructor" rather than "these two happen to agree". `estimate_input_tokens` was **deleted** rather than fixed: it had the same omission, no callers anywhere, and a docstring claiming the approval gate and the budget guard used it when the guard reads each step's declared `estimated_cost_gbp`. Dead code carrying a false claim about a live control is worse than either on its own. |

### Security

| # | Gap | Notes |
|---|---|---|
| A5 | **No authentication** | `get_current_user` returns the first row of `users`. Correct for a local single-user tool; blocking for anything reachable from a network. Phase 6 keeps it behind a flag. |
| ~~A6~~ | ~~**The FRED API key is compromised**~~ | **Closed 2026-08-09.** ADR 0033. The operator rotated the key locally; the old one is dead. No code change was ever going to fix this one. |
| A7 | **No inbound rate limiting** | The token bucket protects outbound fetches. The web application has none. |
| A8 | **No production deployment story** | No `docker-compose.prod.yml`, no TLS or reverse-proxy configuration, no deployment guide. |

### Data lifecycle

| # | Gap | Notes |
|---|---|---|
| ~~A9~~ | ~~**No retention policy, no GC, no integrity sweep**~~ | **Closed, with a correction to the original claim.** `services/retention.py` did exist and did have no caller — but what it held was the *licensed purge* path, which answers a publisher's demand to destroy copies and is correctly idle while nothing licensed is in the store. The two sweeps a single-machine platform actually needs each week were simply absent, and are now there: `verify_store` re-reads every artefact and checks it still hashes to its name, and `collect_garbage` clears bytes no source document, report or agent run points at. `aer verify-artefacts` exits non-zero on a bad store so it can be a cron line; `aer gc-artefacts` reports and only deletes with `--delete`. Two traps closed on the way: a purged artefact is *expected* to be absent and is skipped rather than reported as loss, and every reference branch filters its nulls — `x NOT IN (…, NULL)` is never true, so one agent run with no archived response payload would have made the sweep return no orphans at all and look exactly like a clean store. |
| ~~A10~~ | ~~**No backups**~~ | **Closed.** `aer backup --to DIR` writes a `pg_dump` and the artefact store into one directory with a manifest hashing both — both halves or neither, since a database restored beside an empty store is a set of citations into nothing. A backup nobody has read is not a backup, so `aer verify-backup` re-hashes the dump and every archived file against the manifest and touches no database; it can run wherever the backup lives, which is where a restore is first attempted. `aer backup` runs it before reporting success and `aer restore` refuses without it. Credentials go to `pg_dump` through `PGPASSWORD`, never argv, because `ps` is world-readable — two tests hold that. The restore test restores into a scratch database and reads the rows back, so the source cannot be what it is reading. |
| ~~A11~~ | ~~**No audit-chain verification command**~~ | **Closed.** `aer verify-audit` walks the log in id order and stops at the first break, naming the event id so an operator can go and look at the row. Three things it had to get right: the log is paged, and the seam between two pages is the one place a break is invisible — the first record of a page has no predecessor in its own sequence — so `find_chain_break` now takes an anchor and the service carries it across; deleting the *beginning* of the log leaves every survivor self-consistent, so the first row is checked for a `prev_hash` it should not have; and an empty log reports as empty rather than sound, because "the chain is intact" printed against a truncated table is reassuring at the worst possible moment. Sabotage caught the page-seam test not testing the seam — deleting a row shifts every later row up a place. |
| ~~A12~~ | ~~**No run-level replay**~~ | **Closed.** `aer replay-run <job-id>` re-derives a run from its own record on four legs, each able to fail it alone: calculations re-execute and match, citations still find their excerpts in the artefacts, artefacts still read back by hash, and every model call still has both halves of its exchange archived. Nothing is fetched and no model is called — reproduction is a question about the record, not about the world, so it costs nothing and answers the same in a year. The fourth leg is the one that looks optional: an `agent_run` whose archived response has gone leaves prose in the report with no accounting behind it. Sabotage found the artefact leg was only passing because deleting a cited filing also fails the citation check; the test now uses an artefact nothing quotes, so only that leg can be the cause. |

### Observability and cost

| # | Gap | Notes |
|---|---|---|
| ~~A13~~ | ~~**No Langfuse, no OTel/Grafana**~~ | **Closed at the achievable scope — ADR 0049.** OpenTelemetry spans at the two levels a run is actually shaped by: `step.<key>` in the engine and `model.<role>` around the provider call. The exporter is off unless `AER_OTEL_ENDPOINT` is set — no endpoint, no imports, no thread, no connection — because a local-first tool that needs a collector before it starts is one nobody starts. Langfuse was declined: better suited to LLM tracing, but a vendor dependency unreachable from this environment, and an integration nobody can run is one nobody can trust. The property the tests are mostly about is that tracing can never fail a run; a tracer that raises on open, an exporter that raises on close and a bad endpoint all degrade to a warning. One real bug on the way: the obvious single-`try` shape can make the generator yield twice, which `contextlib` turns into a failure of the traced code. **Not exercised against a real collector** — verified through an in-memory exporter — and fetch spans are left until there is somewhere to look at them. |
| ~~A14~~ | ~~**No prompt caching**~~ | **Closed — ADR 0048, with the premise corrected.** Everything downstream already existed: `Usage` carries the cache token fields, `_usage_from` reads them, `costs.py` prices a read at a tenth and a write at a quarter more. Two findings changed the fix. The shared system prefix is *too short to cache* — `PLATFORM_CONTRACT` is ~183 tokens against minimums of 512 (Opus 5), 1024 (Sonnet 5) and 4096 (Haiku 4.5) — so ordering it first was never going to be enough. And the tokens are in the user turn, behind per-section text; caching is a strict prefix match, so no marker helps until the order changes. `Agent.stable_context` now declares the repeating head of a turn and the provider sends it as its own block ahead of what varies. The section writer puts the evidence there: the nineteen built-in sections resolve to a handful of evidence policies, so sections sharing one get a byte-identical block, and every retry gets it again. The trap was the token counters — both count only `content`, and leaving the prefix out would have silently doubled every role's input cap. **The saving is not yet measured**; no live run has been made since. |
| ~~A15~~ | ~~**No cost dashboard, no cache-hit measurement**~~ | **Closed.** `aer.services.spend` totals what a run or the platform spent and splits prompt tokens by how they were charged; `/costs` shows it with a per-role breakdown and the cache-hit rate. Two distinctions the page exists to keep: `input_tokens` from the API is the *uncached remainder*, not the whole prompt, so a summary reporting it as "prompt tokens" would make a heavily cached run look cheap — the total is fresh + read + written. And a hit rate of zero is not the same as no calls yet: the first is a defect and gets a banner naming the likely causes, the second is a dash. Money comes from the `costs` rows as metered, never recomputed, so a price change does not rewrite history. |

### Test-suite health

| # | Gap | Notes |
|---|---|---|
| ~~A16~~ | ~~**`tests/test_extraction.py` deadlocks in a full-suite run**~~ | **Closed — ADR 0047.** One line of test code, not a threading interaction: the memory-cap test called the child-only `_apply_memory_cap(1 << 30)` in the *pytest* process, and it sets `RLIMIT_AS` soft **and hard**, which an unprivileged process can never raise again. The session was capped at 1 GiB permanently; once its address space passed that (`VmSize: 1225312 kB` by the sandbox tests) every `mmap` failed, so `pthread_create` could not get a stack — `start_new_thread` returned, `/proc` showed `Threads: 1`, and `Thread.start()` blocked for ever. It wedged one statement *before* `_run_child` armed its parse timeout, so a memory limit presented as an unkillable hang. The assertion now runs in a subprocess and also checks the cap took effect. Worth recording that an earlier fix attempt blamed OpenBLAS and looked convincing on a two-file reproduction — it was only keeping that short run under the cap. |
| ~~A17~~ | ~~**DB-touching CLI tests contend for locks**~~ | **Closed.** `tests/db_cleanup.py` empties the database by deleting in reverse `Base.metadata.sorted_tables` order — dependency-sorted parents-first, so reversed is a safe deletion order — instead of taking the table lock a `TRUNCATE` needs. The transactional fixtures and a command that opens its own engine can now both be in flight without deadlocking. One trap found while writing it: the first version also deleted the reference data the migrations seed, and the twelve failures that caused surfaced in `test_section_writer.py`, a file with no visible connection to the change. `SEEDED_BY_MIGRATIONS` names `section_definitions` and `sector_profiles` and leaves them alone. |
| ~~A18~~ | ~~**The fake provider does not enforce schemas**~~ | **Closed, and it immediately earned its keep.** `FakeProvider` now refuses what the API would refuse: an unknown effort level, an unsendable request, and — through `_validated` — a scripted reply the declared contract does not permit, serialised and re-validated exactly as a real reply is. `ScriptedResponse(..., unchecked=True)` is the named, per-response opt-out for the one test that must inject an impossible reply to exercise a consumer's own defences. `tests/schema_guard.py` and `tests/test_fake_fidelity.py` hold it. **The first thing it caught was a production bug**, not a test bug: a skill declaring `{"type": "number"}` gets a float once the reply is validated into the pinned contract, so `8` arrives as `8.0` while the claim carrying its lineage reads "8 years" — and `unsourced_numerals` compared spellings, refusing a properly sourced section. `aer.providers.anthropic` had always validated this way; only the fake had been passing scripted ints through untouched, so no test could see it. `numerals_in` now canonicalises trailing fractional zeros on both sides. |

| ~~A24~~ | ~~**Nothing had ever tried to break the invariants on purpose**~~ | **Closed as an exercise, and it paid.** Thirty-six mutations, each breaking one of the eight invariants in `CLAUDE.md` the way a careless edit would, each run against the tests meant to notice. Thirty-one were caught. Of the five that were not, one is an equivalent mutant — making unit-symbol comparison case-insensitive changes nothing, because `_KNOWN_SYMBOLS` is lowercase and currency codes are `[A-Z]{3}`, so no two constructible symbols differ only in case. The other four were real holes, and none of them was a missing test file; each was a test that stopped one step short. **Invariant 3:** adding `source_document_id` to `_FIGURE_NAMING_KEYS` passed everything, though the code comment says it must never be there — it is the plausible widening that empties the rule, since a section citing a filing *feels* sourced. **Invariant 5:** `money + ratio` was tested and `ratio + money` was not, so a guard reading "is *this* one just a number?" refused the tested order and waved the other through — and the dimensionless left operand is the likelier one to write. **Invariant 6:** the per-run cap's *accumulation* was untested; every existing budget test set a cap below the first step's cost, so dropping `already` from the sum broke nothing. **B6/B11:** `_is_secret`'s bare-annotation branch is dead code today, because every credential is declared `SecretStr | None` — so it could be deleted with nothing failing, and the next credential added as a plain required `SecretStr` would be silently editable. All four now have tests, and each was re-broken afterwards to watch the new test fail. |
| ~~A25~~ | ~~**A flaky test, hidden by the size of the suite**~~ | **Closed.** `test_every_agent_role_resolves_in_the_registry` walks `Agent.__subclasses__()`, and `test_agent_registry` defines throwaway subclasses that are *meant* to be invalid — one names a role nobody registered, to prove construction refuses it. `pytest.raises` holds the traceback that holds the frame that holds the class, so it stays in `__subclasses__()` until a **cyclic** collection reaps it, which is allocation-driven and not deterministic. Run those two files alone and it fails four times in five; run the whole suite and the hundreds of tests in between reliably collect it first, which is why 4214 green tests never showed it. The walk now excludes classes defined under `tests.`, which is also the more honest statement of the property: it is the platform's agents that must name a registered role. Worth recording that the first attempt to reproduce this *passed* and nearly had me call the fix a phantom — five runs, not one, is what settled it. |

| ~~A26~~ | ~~**The correctness core had never been attacked**~~ | **Closed, and it was the sweep worth running.** A24 aimed at the invariants and touched only `units.py` inside `src/aer/calc/` — which is the code the *first* rule in `CLAUDE.md` is about, and 8,400 lines of it. Fifty-seven mutations across all eleven modules: sign flips, off-by-one period indices, forgotten averages, swapped weights, inverted quotients, guards relaxed by one comparison. **Fifty were caught**, including every one that would have mattered most — off-by-one discounting, Gordon's numerator and denominator, present value dividing instead of multiplying, terminal value dropped from the enterprise value, CAPM adding beta instead of multiplying by it, swapped capital weights, population-versus-sample variance, and every sign in the margin bridge (the module names that as the easiest thing in it to get wrong, and it was right to). **Four escaped**, and each was re-run against every test file that can *reach* the module — 56 to 60 of them — before being called a hole: `working_capital` netting the wrong way; `_median` not sorting; the `abs()` gone from `rate_change`; a **nil** terminal cash flow admitted where a negative one is refused. All four now have tests, each re-broken afterwards to watch the new test fail. |
| ~~A27~~ | ~~**A calculation on the valuation's input path with no test at all**~~ | **Closed.** `working_capital` is exported, is *not* in `RATIO_DEFINITIONS`, and was called by no test — every apparent reference was `working_capital_intensity` or `opening_working_capital`, which are different things. `compute_ratios` never reaches it, so the ratio suite's coverage said nothing about it. Its one caller is `aer.services.valuation_run`, where it becomes the DCF's `opening_working_capital` — so the addition-instead-of-subtraction that survived the sweep would not have shown up as a wrong ratio on a page anybody checks. It would have shown up as a wrong first-year free cash flow, and from there as a wrong valuation, carrying a complete provenance trail the whole way. Worth stating plainly, because it is the shape of failure this platform is built to prevent: **every mechanism worked and the number was wrong.** |

| ~~A28~~ | ~~**A25 was not the only order-dependent test**~~ | **Closed, and the check is now a recipe rather than a one-off.** Reversing the file order found nothing — and that result was worth less than it looked, because reverse order happens to put `test_injection.py` *before* `test_agent_registry.py`, which is the safe order for A25. It would not have caught the one failure already known about. A shuffled ordering (seed 20260811) found two more: `tests/test_request_api.py` commits an `Artefact` through `_leave_evidence_behind`, and its `clean_slate` fixture truncated `research_requests, audit_events, users` — three tables, not the fourth. Source documents cascade from a request; an artefact is content-addressed and belongs to no request, so the row outlived the file. Two tests in `test_source_documents.py` then failed, because they asked whether the artefacts table held exactly one row and no rows respectively, which are claims about every test that ever committed rather than about the code under test. Both halves are fixed — the fixture now names `artefacts` (verified: one leaked row becomes none), and the two assertions were narrowed to the digest they created and to a count either side of the call. `just test-shuffled [seed]` runs any ordering and prints the seed, so a red run is reproducible. |
| ~~A29~~ | ~~**The first Windows run failed seven ways, one of them in production code**~~ | **Closed — every one found by the operator's own `just test`, none findable from Linux.** The production bug: `archive_request` formatted its refusal date with `%-d`, a glibc extension that Windows `strftime` rejects, so archiving a request twice — a guarded path, with a test, green on every Linux run — returned a 500 where the design says 409. Fixed with `.day`, and `tests/test_smoke.py` now scans every source line for glibc-only codes in `strftime` calls and f-string format specs, because a Linux suite can never catch this at runtime and reading the code is the only test that holds. The other six were portability holes in tests: three read `/proc/self/task` (Linux-only; now skipped off Linux, with the pin itself still tested everywhere), one needed `ZoneInfo("Pacific/Auckland")` and Windows ships no tz database (now a fixed 13-hour offset, same reader, no dependency), one ran the POSIX memory-cap child on a platform with no `resource` module (now skipped on Windows, whose branch has its own test), and fixing the fixture around the last one surfaced **a further order-dependence A28's three passes had missed**: the budget console tests created a fresh user per run, the console shows a run only to the *oldest* user, and a user leaked by `test_request_api` — which cleans at setup, serving the next test rather than itself — became the current user, turning a correct ownership check into a 404. The fixture now clears the slate on both sides. The wider lesson joins A26's: the suite had been green on one operating system the way it had been green in one file order. |

### Findings from the first full live run

*The four acceptance runs are the first exercise of the whole spine against the real API.
Everything below was invisible to the suite for the same reason: the fake provider is an
alternative implementation of the protocol, so it never sees the wire payload at all, and
the two roles that use the batch endpoint had only ever been run against it.*

| # | Gap | Notes |
|---|---|---|
| ~~A30~~ | ~~**The batch path sent a deprecated field, and found out an hour later**~~ | **Closed.** `red_team` failed a live AAPL run with `output_format: This field is deprecated. Use 'output_config.format' instead.` The single-call path had always been right and looked identical: on `messages.stream` the SDK *merges* the `output_format` argument into `output_config` before sending (there is a comment saying so in its own source), so the argument is client-side sugar. The batch endpoint takes raw message params and merges nothing, so the same spelling written into a batch request went on the wire as the deprecated field. It failed late as well as silently — the Batches API validates at result-fetch time, not at submission, so the step ran to completion and then returned "item 0 did not succeed (errored)". The fix merges into `output_config` with `setdefault` rather than assigning, because assigning would have dropped the `effort` key `_request_payload` had already put there and quietly bought a cheaper model call. The test that would have caught it asks the SDK rather than enumerating by hand: `batch_create_params.Request["params"]` *is* `MessageCreateParamsNonStreaming`, and the batch params must be a subset of what it declares — which has no `output_format` member, and will not have the next removed field either. |
| ~~A31~~ | ~~**A role that thinks hard with no room left to answer**~~ | **Closed.** Five of one report's sections came back with `stop_reason: max_tokens` and no draft. `report_writer` was capped at 8,192 output tokens, which is generous for a section of prose and is the wrong number to have chosen from: `max_tokens` bounds thinking and visible output *together*, and this role routes to opus at high effort, so a section that needed reasoning spent its whole allowance reaching a view and had nothing left to write it down with. The planner and the red team already carried 16,384 for exactly this reason; nothing connected the routing table to the registry, so the roles with headroom had it because somebody thought of it, and the roles without it were the ones that had not been run yet. `report_writer` and `assumption_proposal` — the other high-effort role under the old figure, and the two numbers a whole valuation rests on — are now at 16,384, and a test asserts the rule against the **default** routes: hard effort implies the floor. Asserted rather than enforced at import, because routes are operator-overridable through `AER_MODEL_ROUTES` and a configuration edit must never stop the package importing. |
| ~~A33~~ | ~~**The most expensive step in the run was the one the cap could not see, and the cap was wrong anyway**~~ | **Closed — ADR 0052. Found by following A31 rather than by looking for it.** Both budget-check sites in the engine are written `if step.estimated_cost_gbp > 0`, so an estimate is not advisory — it is the switch deciding whether a step is looked at. Every spending step declared one except `draft`, which is one Opus call per model-written section and measured **£5.17** on the live run: the single largest item in the workflow, waved through unchecked, and missing from the projected cost the operator is shown at the plan gate. **The consequence was not hypothetical.** The default per-run ceiling was £2.50; the operator's AAPL run went past eight pounds and was never paused, because the item that took it there was invisible to the thing whose job was to notice. That is A22's shape again — a ceiling that exists and is never compared — and it surfaced only because raising `report_writer`'s output ceiling raised the worst case of exactly that step, which is the question a change like that should be made to answer. The fix is in three parts: `draft` declares £6.00 (above measured, generous on the same principle as every estimate there); the default per-run budget moves to £12.00, because £2.50 predated any run to measure and would now stop *every* run at drafting — the same wrong number failing loudly rather than silently; and the test fixtures read that default from `Settings` instead of restating it, since a fixture budgeting more generously than production can never notice a step outgrowing the real ceiling. The rule is tested as a decision, not a measurement: every step is either guarded or named as deterministic, so a new step lands in neither list and fails with its own name. **What this does not fix:** the guard is still consulted only *before* a step, so nineteen sections inside one step run with no check between them. Recorded rather than fixed — per-call budgeting is a larger change than the one that found it, and needs its own decision about what a half-written report is worth. |
| ~~A34~~ | ~~**The research workers had a token allowance a real company outgrew**~~ | **Closed — ADR 0053, by operator direction.** The second live run failed at the analysis step: a worker composed 40,367 input tokens against the role's registered allowance of 30,000, and the refusal's own message misdiagnosed it ("something was included that this role is not meant to carry"). Nothing improper was carried — evidence digests accumulate across a worker's rounds by design, and a fourteen-thousand-fact company legitimately composes turns the fixture company never did. The call it refused would have cost about £0.20 against a £12 budget. The operator's direction was that tokens should never be the cap, and the fix honours it structurally: the per-role input allowances are **deleted from the registry**, not resized, and every call is instead priced before it is made — counted input at the uncached rate plus the full output ceiling — and checked against what remains of the request's budget and the month's, raising the same `BudgetExceededError` the engine already pauses on. That closes the gap ADR 0052 left open: the calls inside a step are now each guarded, so the nineteenth drafting call is checked against everything the first eighteen spent. One token-shaped check survives because it is the vendor's — a composition that cannot fit the routed model's context window is refused before upload rather than 400'd after it. What the old allowances quietly provided as interpolation tripwires is given up knowingly: that failure now surfaces as bounded, visible cost rather than a named refusal, and the trade is recorded in the ADR rather than implied. |
| A32 | **The numeral rule refuses document references** | **Open — a decision, not a defect, and one for the operator.** The same live run logged repeated `section_writer.draft_refused` over numerals that denote no quantity: the year `2026`, Apple's CIK `0000320193`, exhibit and item numbers such as `2.02`, `99.1` and `9.01`. The rule behaved exactly as `docs/PLAN.md` §2.12 and `core/section_output.py` say it should — *"a numeral the platform cannot trace is a numeral the report cannot carry, whatever it denotes"* — and the retry loop recovered every time, so no section was lost; the cost is the retries and whatever prose the writer avoided to get past it. The argument for changing it is that `NUMERAL_EXEMPT_KEYS` already carries the same reasoning one level down: an id is exempted because provenance must not trip the rule that exists to protect provenance, and a filing reference in prose is provenance too. The argument against is that "2025" in prose is indistinguishable, by regular expression, from "revenue of 2025 million". **Not changed unilaterally**: relaxing it moves invariant 3's boundary and needs an ADR and a decision, not a patch. |

### Data sources

| # | Gap | Notes |
|---|---|---|
| ~~A19~~ | ~~**Bank of England macro adapter not built**~~ | **Closed differently from how it was framed, and the framing was the error.** The BoE adapter is not "not built" — it is *determined against*: ADR 0026 found the Bank documents a CSV download route for programmatic use and disallows that same route in its own `robots.txt`, and reaching it through the unlisted viewer path would be circumventing a stated restriction. So the euro is the pivot instead. `aer.sources.macro.ecb` reads the ECB's daily reference rates through a documented API with no such conflict, and because every ECB rate has the euro on one side, a GBP/USD rate is a **cross** — `aer.calc.fx.cross` divides two published figures as a *traced calculation*, so a derived rate never looks published. ADR 0045. **The GBP risk-free rate is still missing** and this did not fix it: the UK proxy is a gilt yield, which is BoE data, so `risk_free_series_for("GBP")` still refuses rather than discounting sterling at a US Treasury yield. |
| ~~A20~~ | ~~**EODHD unresolved**~~ | **Resolved: ADR 0030 route 2** — keep the personal subscription, build for internal use only, accept explicitly that a future commercial version needs a different licence. Most of what the route required was already built by the tasks that followed the ADR: the comps table returns a `WithheldComps` with *no rows* to a shareable audience (0034), exportable and internal charts are separate functions with a render-time refusal, the corrected licence note is pinned, and the weighted daily-call ledger reserves before the request. **One thing was missing and it was the load-bearing one:** `purge_provider` had no caller anywhere in `src/`, so the deletion the agreement requires within a month of the subscription ending could be performed only by writing Python at a REPL. `aer purge-licensed` is that caller, and a test over the policy table fails if a second paid feed is ever added without one. The derived-data question was then **settled by operator determination on 2026-08-09** (ADR 0030, amended): figures computed from the data may be published, on the operator's reading of the executed agreement rather than on an inference from the public terms — and the licence note says which. The permission does not extend to the series itself or a chart of it, so the price chart stays internal. The withholding machinery survives the decision that opened it, defaulting closed, so the next paid feed inherits nothing. |

---

## List B — features, from the operator's seat

### Broken or absent in what already exists

| # | Gap | Notes |
|---|---|---|
| ~~B1~~ | ~~**No way to delete or archive a research request**~~ | **Closed, with both verbs.** Archive is one click on the list, destroys nothing and is undone by one more; it is accepted whatever state the request is in, which is the case `delete_request` had to refuse. Remove is a confirmation page listing what would be destroyed by table, what survives, and what the run cost — then deletes the request and everything derived from it, walking the schema's own dependency sort. The audit chain, the spend ledger and the content-addressed artefacts all survive, and a purge is refused when a later run's claims cite evidence this one gathered, which is the common case for a company researched twice. |
| ~~B2~~ | ~~**Valuation, DCF, scenarios and sensitivities never appear in a run**~~ | **Closed.** Six assumptions are derived from the filings with their derivation stated, two are proposed by the ADR 0046 role under bounds that refuse rather than clamp, and the three the discount rate decomposes into are named as outstanding with a reason — or proposed, in beta's case, once prices are acquired. A conditional `ASSUMPTIONS` gate stops the run only when the set is complete, because a gate an operator cannot clear is a run that pauses and never resumes; the create route is what lets them clear it. The `value` step then runs the WACC, the DCF, the scenarios and two sensitivity grids, every figure a recorded calculation. |
| ~~B3~~ | ~~**Comparables never appear**~~ | **Closed at the reachable scope.** `acquire_prices` fetches the subject's series and its market's, stores both as hashed artefacts under the licensed tier, computes a market capitalisation and proposes a beta — which is what makes B2's gate reachable without an operator typing one. The market proxy is a documented decision per exchange and an undocumented exchange is refused, because a London share regressed against the S&P looks exactly like one regressed against the All-Share. `comps` then builds the table. **It is thin, and honestly so:** a peer's multiple needs that peer's filings and prices, this workflow acquires neither, so every confirmed peer is excluded *by name with the reason*. Acquiring peer data is a larger job than B3 and is Task 30's. |
| ~~B4~~ | ~~**A run reads one filing**~~ | **Closed.** A run now reads the annual report and the recent current reports as well as the aggregate. Whether the recent-developments worker does better with them is the next live run's question. |
| ~~B13~~ | ~~**No full-text search**~~ (IR discovery deliberately deferred) | **Half closed, on purpose.** `search_filings_full_text` is now a worker tool: the model supplies the phrase, code supplies the CIK and the as-of bound, and hits come back as metadata — form, date, URL — so reading one still costs a `fetch_known_url` call and the twelve-call budget keeps meaning something. Hits after the as-of date are counted and named rather than dropped, because "found nothing" and "found things you may not read" call for different next moves. **Issuer-IR discovery stays uncalled by choice**: `aer.sources.issuer` holds that no code path learns a domain from a page and then fetches it, and a discovery step is exactly that path with a search engine in the middle. |
| ~~B5~~ | ~~**Charts have nothing to plot**~~ | **Closed by A1**, at least in principle: a run now records a multi-period ledger, so the revenue-and-margin history has a series behind it. The scenario and sensitivity charts stay placeholders until the valuation does. |

### Not built (Phase 6)

| # | Gap | Notes |
|---|---|---|
| ~~B6~~ | ~~**Settings screen**~~ | **Closed for cost and method; credentials deliberately excluded — ADR 0050.** `/settings` edits model routing, the per-run and monthly budgets and the warning ratio, stored in `settings_overrides` and applied to runs that *start* after the change (a run whose routing moved mid-flight would have a record describing two platforms). Credentials stay in `.env` and are shown as present-or-absent only: A10's `aer backup` pg_dumps the database, so a key in a settings table is a key in every backup. The allowlist is enforced at the write, and a test walks `Settings` for every `SecretStr` field to assert none is overridable — so a credential added next year cannot quietly become editable. |
| ~~B7~~ | ~~**Cost dashboard**~~ | **Closed with A15.** `/costs` shows total spend, spend by category, a per-role breakdown split by model, and the prompt-cache hit rate. |
| ~~B8~~ | ~~**"Reproduce this run" button**~~ | **Closed.** A POST from the run console to `/runs/{id}/replay`, rendering A12's four legs. A POST rather than a link because re-verifying a citation writes its verdict back — it reads like a report but changes stored state, and on loopback with no auth a plain link would let any open tab rewrite verification state. Offered whatever the run's status: a failed or cancelled run is often the one worth interrogating. |
| B9 | **Watchlists and scheduled runs** | APScheduler in the worker. |
| ~~B10~~ | ~~**Skill export/import with a confirmation diff**~~ | **Closed.** Import with a diff and a hash-checked confirmation already existed (T20); what was missing was export and anything to start from. `GET /skills/{key}/export` returns the stored *source* byte for byte rather than a re-serialisation of the parsed frontmatter — a re-serialisation would reorder keys and drop comments, so a round trip would show a diff full of changes nobody made, and the diff is the whole control. Three worked examples ship under `aer.skills.examples`, listed at `/skills/examples`; **none is installed**, and choosing one goes through the same import-and-confirm path a file from a stranger takes, because pre-installing them would make that step look optional for exactly the files an operator trusts most readily. |
| ~~B11~~ | ~~**Provider and model configuration UI**~~ | **Closed with B6** for model and effort per role. Provider *keys* are out of scope by the same ADR 0050 reasoning; if wanted, the honest form is an OS keyring rather than a database column. |

### Deliberately out of scope

| # | Item | Notes |
|---|---|---|
| B12 | **Multi-company and portfolio views** | Named here so it stays a decision rather than an oversight. `docs/PLAN.md` keeps the platform to one report at a time. |

---

## Corrections to the task record

`docs/PLAN.md` Stage 4's task list and the working task board disagree with
`docs/phase-3-plan.md`, which records outcomes against each task. The phase plan is right:

* **Task 26 (cost of capital)** — done. `src/aer/calc/wacc.py`.
* **Task 27 (the discounted cash flow)** — done. `src/aer/calc/dcf.py`, `services/valuation.py`.
* **Task 28 (sector enforcement)** — done.
* **Task 25 (macro with vintages)** — ALFRED and ONS done. The Bank of England is not
  outstanding but *refused* (ADR 0026); the euro reference rates replace it as the FX
  source (ADR 0045), and the GBP risk-free rate remains genuinely open.

Tasks 29–32 remain genuinely outstanding.

---

## Suggested order

**A1 to A4, A21, B13, A9, B1, A19, A20, B2 and B3 are done, and the whole test-suite health
section — A16, A17 and A18 — is now closed.** That last one is worth a sentence of its own:
until A16 was fixed the suite could not complete in one process, so every "verified" claim
in this document rested on a subset. It completes now, which is what makes the rest of these
entries checkable.

What that leaves, in the order it is worth doing:

1. **A14 — prompt caching.** The composition is already ordered for it and never asks for
   it. The cheapest remaining saving by a distance, now that A2 means the meter can show
   what it saves.
2. **The valuation path.** Not a line above, because it is a design question rather than a
   gap: a DCF needs confirmed assumptions, so it needs either an operator working through
   the assumptions page or a proposing agent — and the second needs an ADR before it needs
   code.
3. The rest of Phase 6 in the order `docs/PLAN.md` gives. **A10 to A12 are done** —
   backups with a verifier and a working restore, audit-chain verification, and run-level
   replay — which closes the whole data-lifecycle section. What A9 made pressing is now
   answered: the sweep can tell an operator the store has lost a document, and there is
   something to restore it from.
