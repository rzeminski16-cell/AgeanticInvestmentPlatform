# Extending the platform

*Nearly every change is one of six shapes. Five extend the research tool from inside; the
sixth adds a tool beside it. Each has an existing pattern, and the seventh way is the wrong
way.*

*This expands [`knowledge-map.md`](knowledge-map.md) §7. Where the two disagree, the
knowledge map is pinned to the code by a test and this is not — so it wins.*

---

## Before you start

Two questions decide everything that follows.

**Which side of the line is this on?** Deterministic Python owns every number and every
fact; the model owns planning, interpretation, comparison, challenge and writing. Most
wrong changes to this codebase are wrong by moving something across that line — a
calculation drifting into a prompt, or a model output being trusted as a fact.

**Does this weaken an invariant?** The eight in `CLAUDE.md` are not style. Weakening one is
an ADR-level decision, not a code change, and the enforcement is structural: you will find
a test standing in the way rather than a convention.

---

## 1. Add a built-in report section

Sections are **data, not code** (ADR 0013).

- A `section_definitions` row, seeded by a migration.
- An output contract.
- An evidence policy in `sections/`.

The generic writer, renderer and provenance walk pick it up. **No new agent.** The section
writer holds no tools by design (ADR 0042) — it writes from evidence it was handed, and a
writer that could fetch could write from something nobody approved.

## 2. Add a calculation

- A pure function in `calc/`, `Decimal` in and `Decimal` out.
- `@traced`, so its formula and inputs persist as a record that can be replayed.
- Property-based tests (`hypothesis`) in the matching `tests/test_*.py`.
- A golden case in `tests/fixtures/calc/golden.json` if it feeds the evaluation gate — and
  bump `EXPECTED_CORPUS_SIZE`, which exists to make adding one a deliberate act.

**Never a default parameter** in `calc/wacc.py` or `calc/dcf.py`. Absence must be loud: a
silent default is an assumption nobody approved, wearing the costume of a constant.

Units are carried through all arithmetic. A mismatch **raises**; it never coerces, in
either operand order.

## 3. Add a data source

- An adapter package under `sources/` implementing the `SourceAdapter` protocol.
- Fetching **only** through `aer.fetch` — the single component permitted outbound network
  requests, enforced by a socket-blocked test suite.
- A terms-of-service and robots determination **recorded in an ADR before the first
  request**.

That last step is not paperwork. Two sources were **declined** at it — the FCA National
Storage Mechanism, and the Bank of England's statistical database — and that outcome has to
stay reachable. An adapter written first and licensed afterwards is an adapter that will be
argued into existence.

## 4. Add an agent role

- A `RoleDefinition` in `agents/registry.py`: its tools, its output ceiling, its output
  contract.
- The ADR the registry test demands. **A new role requires an ADR** (ADR 0035), and the
  test fails without the file.
- A route in `config.py`'s `DEFAULT_MODEL_ROUTES`.

Capability lives **only** in the registry. A subclass declaring its own is refused at class
definition. There is deliberately no input-token allowance to set: allowances existed, and a
live run died on one that a large company's evidence legitimately outgrew. Every call is now
priced in pounds at the provider boundary against the run's budget and the month's. The only
token-shaped bound left is the routed model's context window (ADR 0053).

Workers **request** tools in a schema and code executes them (ADR 0036). That is what stops
text inside a fetched filing from causing a tool call the role does not already hold.

## 5. Add or change a skill

User-authored Markdown with validated frontmatter. The composer is **additive-only**: it
intersects a skill's requests against what the role already holds, so a skill can add
requirements and never relax them.

This is proved rather than asserted. `tests/skill_corpus.py` is a corpus of attacks that
must **all** fail — including files instructing the platform to skip citations or to set a
rating. If a skill needs a capability the platform lacks, that is a platform change first.

Skills are **version-pinned per run**, so editing one mid-flight cannot change a run already
under way.

## 6. Add a tool

**A different class of change, and not a shortcut around the five.** The five inherit a
subject, a run, a budget and a gate that already exist. A tool brings its own.

- A row in `INSTALLED_TOOLS` (`web/tools/registry.py`) with its status.
- An ADR admitting it — the registry refuses a tool with no ADR.
- A `NavSection` it contributes, rather than a branch in a template.
- An `AttentionProvider` if it has anything the operator should come back for.
- Its own subject, workflow and surfaces.

A `PLANNED` row is a real, honest page saying what the tool would be and what it is waiting
on. A tool ships by changing its `status` and giving it a real page; **nothing else in the
registry moves.**

If what you are building fits one of the five recipes, it is not a tool.

---

## 7. Map a filer's tag onto a canonical concept

**Judgement, not typing** (roadmap §2.8, A55). A tag maps onto one canonical concept or
onto nothing, and nothing is ever guessed from a tag's spelling — two elements differing by
one word can differ by whether sales tax is in the number.

```bash
uv run aer curation-worksheet --out worksheet.md   # or --top 20 for one sitting
```

That reads every run's recorded extract rows, aggregates them, and ranks by the largest
share of a mapped line any run saw, so a sitting works down from the top and stops. Fill in
`Maps to` and `Why`; then, deliberately and by hand:

- an entry in the taxonomy's alias table in `core/concepts.py`, pointing at an existing
  canonical concept;
- **or** nothing, when the honest answer is that the tag should stay unmapped — a
  components split, a footnote disclosure, a measure with no concept here;
- **or**, when a tag must *never* map, an entry in `NEVER_MAP` with the reason (§2.7).
  That is a refusal, not a gap, and the difference is what stops the mapping arriving later
  in good faith.

Adding a *concept* rather than an alias is a larger decision: every adapter has to be able
to reach it, and `tests/test_facts_schema.py` fails a concept no tag can populate.

---

## The definition of done

A change is finished when all of these are true:

```bash
just lint         # ruff check and format
just typecheck    # mypy — strict in core/ and calc/
just test         # the suite, no network, no model spend
just eval         # the blocking metrics
```

and:

- **The core stayed pure.** `core/` and `calc/` have no I/O, no globals and no clock reads.
- **New arithmetic has property tests**, not only examples.
- **Anything that could drift from a document is pinned by a test.** The knowledge map is,
  the ADR references are, the nav is, the golden corpus is. A document that can go stale
  silently will.
- **Nothing secret is logged.** `aer.logging` redacts by field name and value shape, but
  that is a backstop, not a licence to put credentials into log context.
- **The decision is recorded if it was a decision.** An ADR is cheap; reconstructing why
  something was done from a diff is not.

## The traps worth naming

- **A green offline suite is not a proven vendor contract.** The `FakeProvider` is an
  alternative implementation of the protocol, not a fake transport — it never sees a
  payload. `just test-live` exists because of what that blindness cost once.
- **A mutation that survives a narrow selector has found the edge of the selector**, not a
  gap in the suite. Re-run against every test file that can transitively reach the module
  before calling it a hole.
- **A test suite cannot fail on a claim nobody encoded.** Two real defects were docstrings
  that described behaviour the code did not have — a guard populated with a ceiling it never
  compared, and three call paths documented as composing different prompts when two were
  identical.
- **Traceability is not sanity.** A figure can have a perfect chain and still be impossible.
  That is why the plausibility layer exists, and why it is arithmetic rather than a model
  call.

---

**Next:** [architecture](architecture.md) · [testing](testing.md) ·
[the decision records](../adr/)
