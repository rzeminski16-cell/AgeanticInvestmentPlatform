# Repository conventions

Read this before changing anything. It is the short version of `docs/PLAN.md`, which is
the full research, architecture and build plan and remains the authority when the two
disagree.

## What this project is

A local-first, auditable equity research platform that produces **one institutional-style
research report at a time** for a UK or US listed company, under explicit human approval,
with every number traceable to a formula and every fact traceable to a hashed source
artefact.

It is a personal research tool. It is **not** regulated investment advice, and every
user-facing surface must say so.

## The rule that everything else follows from

**Deterministic Python owns every number and every fact. The language model owns
planning, interpretation, comparison, adversarial challenge and writing.**

| Deterministic code | Language model |
|---|---|
| HTTP fetching, robots/ToS checks, SSRF guards, rate limiting, retries | Research planning |
| Hashing, caching, deduplication | Search-query formulation |
| iXBRL / PDF / HTML parsing | Source relevance triage |
| **All arithmetic** — ratios, growth, WACC, DCF, comps, scenarios | Assumption *proposal* with justification |
| Unit and currency normalisation | Drafting sections from already-structured facts |
| Date arithmetic and point-in-time filtering | Red-teaming the thesis |
| Citation resolution and excerpt verification | Natural-language writing |
| Schema validation, storage, rendering, cost metering | |

**Never move a calculation into a prompt.** A discounted cash flow is forty lines of
Python with unit tests; it is not a reasoning task. Putting arithmetic in prose is the
single most common way systems like this produce confidently wrong numbers.

## Non-negotiable invariants

1. **Every externally derived fact traces to a hashed artefact.** If it was fetched, it
   was hashed and stored, and a claim can point at the exact excerpt.
2. **The model may propose a citation; only code may confirm one.** Citation verification
   re-reads the artefact by hash and checks the excerpt actually appears there.
3. **No figure reaches a report unless it is a stored fact or a recorded calculation.**
   Calculations persist their formula, inputs (each with a unit and a source), and the
   code version that produced them.
4. **Point-in-time is enforced at acquisition, in code.** Nothing published after the
   as-of date may support a claim when point-in-time mode is on.
5. **Units are carried through all arithmetic.** A unit mismatch raises; it never coerces.
6. **Cost is metered and capped in code.** Every model call goes through the router and
   writes a cost row. Caps that only warn are caps that do not work.
7. **Skill files are additive-only.** User-authored instructions may add requirements,
   never relax them. No wording in a skill file can switch off citations, set a rating,
   or bypass point-in-time rules. Enforced structurally, not by prompt text.
8. **Untrusted content is data, never instruction.** Fetched pages and documents are
   wrapped and labelled. Tool authorisation is enforced in code, so injected text cannot
   cause a tool call the agent's role does not already permit.

## Code conventions

- **Python 3.12**, managed with `uv`. Run things via `uv run ...` or the `justfile`.
- `src/aer/core/` and `src/aer/calc/` are `mypy --strict`. Keep them **pure and free of
  side effects** — no I/O, no globals, no clock reads. They are the correctness core and
  must be trivially testable.
- Everything else is normal-mode mypy, but still fully annotated.
- `ruff` handles both linting and formatting. Line length 100.
- Use `Decimal` for money and ratios, never `float`.
- Timestamps are timezone-aware, always. The `DTZ` lint rules enforce this.
- Prefer `pathlib` over `os.path` (`PTH` rules enforce this).
- Raise `AerError` subclasses from `aer.errors`, each with a stable `code`.

## Testing

- **Tests must run with no network access and no model spend by default.** External HTTP
  is replayed from recorded cassettes; model calls go through a fake provider.
- Tests that need Docker services are marked `integration`.
- Tests that make real, billable model calls are marked `live_llm` and are excluded from
  the default suite.
- Property-based tests (`hypothesis`) are expected for anything in `calc/`.
- **One pytest process per database.** The suite empties tables between tests, so two runs
  sharing `aer_test` delete each other's rows and fail in whichever one was mid-test — as
  "no user exists" moments after a fixture committed one, nowhere near the cause. Give each
  concurrent run its own `AER_TEST_DATABASE_URL`.
- **The full suite is two processes**, `pytest --ignore=tests/e2e` then `pytest tests/e2e`:
  Playwright's sync API leaves a running loop on the main thread that wedges every
  pytest-asyncio test after it.

## Security

- **Never commit secrets.** `.env` is git-ignored; `detect-secrets` runs pre-commit.
- **Never log credentials.** `aer.logging` redacts by field name and by value shape, but
  that is a backstop — do not put secrets into log context in the first place.
- Only `aer.fetch` may make outbound network requests. Nothing else, ever.
- Only `aer.providers.anthropic` may import the `anthropic` SDK.

## Writing style

- **UK English** in all user-facing text, documentation and comments.
- Comments explain *why*, not *what*. If a comment restates the code, delete it.
- Write code that reads like the code around it.

## Working on this repository

- The build sequence is in `docs/PLAN.md`, Stage 4. Tasks build on each other; do not
  skip ahead, and do not fold a later task's work into an earlier one.
- Architectural decisions live in `docs/adr/`. A decision that changes one of the
  invariants above needs a new ADR, not just a code change.
- **If a prerequisite is missing or an architectural choice is unclear, stop and say so.**
  Guessing is worse than asking here: a wrong foundational choice is expensive to undo.
