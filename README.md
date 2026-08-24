# Ageiantic Equity Research Platform

A local-first, auditable equity research platform for UK and US listed equities.

It produces **one institutional-style research report at a time**, under explicit human
approval, with every number traceable to a formula and every fact traceable to a hashed
source document.

> **This is a personal research tool. It is not regulated investment advice.** Nothing it
> produces is a recommendation to buy, sell or hold any security. Ratings are non-binding
> personal views, and every generated report carries this disclaimer.

---

## The design principle

**Deterministic Python owns every number and every fact. The language model owns planning,
interpretation, comparison, adversarial challenge and writing.**

An unconstrained model asked to "research Microsoft" produces fluent, plausible, partly
fabricated prose with invented figures and mismatched citations. Everything in this
architecture exists to make that outcome structurally impossible rather than merely
discouraged — a discounted cash flow here is forty lines of tested Python, not a reasoning
task. See [`docs/adr/0003`](docs/adr/0003-deterministic-code-owns-numbers-and-facts.md).

## Status

**The chain is complete; the breadth is still growing.** A research request becomes a
costed plan you approve, filings fetched and hashed, point-in-time facts, traced
calculations, a drafted report you approve, and a frozen document in which every figure
carries a footnote that resolves either to the formula that produced it or to the archived
bytes it came from.

Two of nine planned tools work today — **Equity Research** and **Portfolio**. The other
seven are honest placeholders that say what they would be and what they are waiting on.

[`docs/plan/ROADMAP.md`](docs/plan/ROADMAP.md) is the authority on scope, and is candid
about what is missing.

## Quickstart

```bash
git clone https://github.com/rzeminski16-cell/AgeianticEquityResearchPlatform.git
cd AgeianticEquityResearchPlatform

uv python install 3.12
uv sync --all-groups
uv run pre-commit install

cp .env.example .env          # then set AER_HTTP_USER_AGENT — the only required setting
docker compose up -d          # Postgres and Redis
uv run alembic upgrade head
uv run aer seed-user --email you@example.com

uv run aer serve                        # the GUI and API, on 127.0.0.1:8000
uv run arq aer.worker.WorkerSettings    # the worker that executes runs — a second terminal
```

Both processes must be up: the web process enqueues, the worker executes.

Full instructions, including Windows and the WeasyPrint native dependencies, are in
[`docs/users/getting-started.md`](docs/users/getting-started.md).

## Documentation

**[`docs/`](docs/README.md) is the index.** It routes by who is reading.

| You want to | Read |
|---|---|
| Understand or explain what this is | [`docs/product/what-it-is.md`](docs/product/what-it-is.md) |
| See the whole pipeline as a diagram | [`docs/product/anatomy-of-a-research-run.html`](docs/product/anatomy-of-a-research-run.html) — open in a browser |
| Install and run it | [`docs/users/getting-started.md`](docs/users/getting-started.md) |
| Commission a report | [`docs/users/running-a-report.md`](docs/users/running-a-report.md) |
| Read the output properly | [`docs/users/reading-a-report.md`](docs/users/reading-a-report.md) |
| Change the code | [`docs/developers/knowledge-map.md`](docs/developers/knowledge-map.md) |
| Know what happens next | [`docs/plan/ROADMAP.md`](docs/plan/ROADMAP.md) |
| Know why something is the way it is | [`docs/adr/`](docs/adr/) — 85 decision records |

## The invariants

Eight rules the platform holds structurally rather than by convention. Weakening one is an
ADR-level decision. They are stated in [`CLAUDE.md`](CLAUDE.md); what enforces each is in
[`docs/developers/knowledge-map.md`](docs/developers/knowledge-map.md) §5.

1. Every externally derived fact traces to a hashed artefact.
2. The model may propose a citation; **only code confirms one**.
3. No figure reaches a report unless it is a stored fact or a recorded calculation.
4. Point-in-time is enforced at acquisition, in code.
5. Units are carried through all arithmetic. A mismatch raises; it never coerces.
6. Cost is metered and capped in code. A cap that only warns is not a cap.
7. Skill files are additive-only — they may add requirements, never relax them.
8. Untrusted content is data, never instruction.

## Common commands

```bash
just dev          # web server with auto-reload
just worker       # the background worker
just test         # the suite: no network, no model spend
just ci           # everything CI runs, in the same order
just config       # the effective configuration, secrets masked
```

Without `just`, read the `justfile` — every recipe is a one-line `uv run …` command. The
full table is in [`docs/users/getting-started.md`](docs/users/getting-started.md).

## Testing

```bash
uv run pytest --ignore=tests/e2e     # default suite: no network, no model spend
uv run pytest tests/e2e              # browser tests (Chromium + PostgreSQL)
just eval                            # the blocking evaluation metrics, on their own
```

**The full suite is two processes, not one.** Playwright's synchronous API leaves a running
loop on the main thread that wedges every asyncio test after it. **And one pytest process
per database** — the suite empties tables between tests, so two runs sharing `aer_test`
delete each other's rows.

Details, and the layers of the suite, in
[`docs/developers/testing.md`](docs/developers/testing.md). To test the whole thing on your
own machine — setup, both tools by eye, every guard provoked deliberately, failure and
recovery — follow
[`docs/developers/testing-by-hand.md`](docs/developers/testing-by-hand.md).

## Repository layout

`src/aer/` is the application package, organised by trust zone rather than alphabetically —
a pure correctness core (`core/`, `calc/`), guarded doors (`fetch/`, `providers/`,
`storage/`), a model-facing layer (`agents/`, `skills/`), orchestration and services, and
the shell. The annotated inventory is
[`docs/developers/repository-layout.md`](docs/developers/repository-layout.md); the reasons
are [`docs/developers/knowledge-map.md`](docs/developers/knowledge-map.md).

## Contributing

[`CONTRIBUTING.md`](CONTRIBUTING.md) for the workflow and the definition of done;
[`CLAUDE.md`](CLAUDE.md) for the conventions that govern how code here is written.

## Licence

MIT — see [`LICENSE`](LICENSE). Provisional while the project is personal; revisit before
any public distribution, and note that some data providers restrict redistribution of their
content independently of this repository's licence.
