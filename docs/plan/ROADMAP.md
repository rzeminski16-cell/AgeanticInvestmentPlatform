# Roadmap

**The authority on scope.** Where this and any other document disagree, this one wins —
except the ADRs, which are decisions and outrank a plan.

*Supersedes the original `PLAN.md` (Stages 1–4), the five phase plans and `investment-os.md`
§12/§15 — all now in [`../archive/`](../archive/README.md). Those remain the record of how
the platform got here and why; this is the record of where it goes.*

*Written 2026-08-23, on the trunk that merged the research and Investment OS lines of work.*

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

## 2. Immediate — finish what the merge left standing

Small, bounded, and each one closes a thing that is currently half-done.

**2.1 Step 4 of the work-order migration.** Drop `jobs.request_id`,
`approvals.request_id`, `source_documents.request_id` and the columns duplicated on
`research_requests`. Deliberately staged as a later revision (ADR 0072): while those
columns still hold the data, dropping `work_orders` discards nothing, so the downgrade is
lossless rather than merely declared. Needs the ~20 `session.get(ResearchRequest,
job.request_id)` lookups to become optional mandate reads first, since a monitor run will
have none.

**2.2 Guidance mode, finished.** The flag, the route and `data-guidance` on `<body>` are
done; `ui.guide()` and its CSS landed with the macros. Two things remain and the second is
the blocker: **no page calls the macro**, and **nothing renders a control for the toggle** —
a form in the shell needs a CSRF token in the shell, which means `render()` minting one and
every response setting the cookie. That is its own slice.

**2.3 A split must arrive as a transaction.** `corporate_actions` knows about splits, but
nothing turns one into a change in holdings, so a book spanning a split is currently wrong.
Derive it from the corporate action and write it as a transaction — never as a quantity
that changed with nothing behind it.

**2.4 Scenarios and sensitivity for the residual-income model.** The bank model ships
without them, and says so in its caveats rather than quietly. The discounted cash flow has
an 81-cell grid; the bank model has none.

**2.5 R18 — the share-based-compensation risk-free rate.** A
`ShareBasedCompensation…RiskFreeRate` tag must never map to `risk_free_rate`. It is an
input to an option-pricing model in a footnote, not the discount-rate input, and mapping it
would put a plausible wrong number in the cost of capital.

**2.6 A seed-data downgrade on a used database. Done, 2026-08-24.** Found by a manual run
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

**2.7 A static asset's content type came from the operating system. Done, 2026-08-24.** Also
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

**2.8 The pre-commit hooks corrupted the tree they were checking. Done, 2026-08-24.** Three
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

---

## 3. Next — the judgement layer

This is the substantial next stage, and the order is forced by dependency: nothing after
theses can exist before them.

**3.1 Judgements and theses** (ADRs 0074, 0079). A thesis is a view a named person held at
a time, with the evidence it rests on and the questions that would defeat it. The record
that makes it *storable without becoming evidence* already exists — **a judgement is never
a source reference** — and this is where it earns its keep.

Also here: `RESERVED_OUTPUT_FIELDS` gains `conviction`, with its attack file. A conviction
score that something else can multiply is exactly the laundering ADR 0074 refuses.

**3.2 The thesis monitor** (ADRs 0078, 0079). What has happened since a thesis was written
that bears on it. **It raises questions and answers none**, and a monitor finding is not a
gated decision — an alert feed that decides things is the thing that record exists to
refuse.

**3.3 Decisions and the trade journal.** The entry written *before* the outcome is known.

**3.4 Post-trade review and decision analytics** (ADR 0081). Scored against the process
that was supposed to be followed, deliberately **not** against whether it made money.

**3.5 Portfolio risk and scenarios** (ADR 0080). Commented on rather than scored. Its rate
prerequisite is now met.

**3.6 Watchlist and research queue.** Needs the standing budget and the two clocks — a
watchlist is followed continuously and researched as at a date, and conflating those is the
mistake ADR 0075 names.

**3.7 The methodology library.** Three `SkillKind`s that are versioned, pinned and
composed. Mostly does not exist yet.

---

## 4. Standing work — the research tool's own depth

Not a stage. These improve with attention and never finish.

**4.1 A55 — concept-map coverage.** 175 concepts and 110 segment tags the map cannot place.
This is judgement over accounting semantics rather than a code change, which is why it has
survived several passes: it needs somebody who knows what a tag *means* deciding what it
maps to. The gate that names the lines a filing would lose is the mechanism; the curation is
the work.

**4.2 A63 — the starved pack and the overshooting retry.** Five failed sections traced to
one failure: a thin evidence pack, then a retry that swings past the target. Instrumentation
is in place and it needs a live run to read back.

**4.3 Report readability.** The register is clean — every sentence in a report that was
*about the report* is gone or moved to where disclosure belongs. Keep it that way: the
failure mode returns whenever a new refusal path gets a placeholder written in the
platform's voice rather than the report's.

---

## 5. Before this leaves one machine

None of this is needed for a personal tool on a laptop, and all of it is needed before
anything else. Grouped because they stand or fall together.

- **A5 — no authentication.** `get_current_user` returns the first row of `users`.
- **A7 — no inbound rate limiting.** The token bucket protects outbound fetches only.
- **A8 — no production deployment story.** No production compose file, no TLS, no
  supervision.

Treat these as a single gate rather than three tickets. Shipping any one alone buys nothing.

---

## 6. Commercial and licence checks still outstanding

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

## 7. Out of scope

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

## 8. How to work on this

- **Do not skip ahead, and do not fold a later item's work into an earlier one.** The
  dependency order in §3 is real.
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
