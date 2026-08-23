# Contributing

This is currently a single-maintainer project, but it is written as though it is not —
because the conventions that make a codebase reviewable are the same ones that make it
possible to pick it up again after three weeks away.

Read `CLAUDE.md` first. It holds the conventions and the invariants. This document covers
process.

## Workflow

1. Branch from the current development branch. Never commit directly to `main` — a
   pre-commit hook enforces this.
2. Make the change. Keep it scoped to one item from `docs/plan/ROADMAP.md`.
3. Run `just ci` (or the three commands it wraps) until green.
4. Commit with a conventional-commit message.
5. Push and open a pull request.

## Branch naming

```
<type>/<short-kebab-description>
```

`feat/`, `fix/`, `chore/`, `docs/`, `refactor/`, `test/`, `perf/`, `build/`.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <imperative summary>

<optional body: why, not what>
```

Examples:

```
feat(calc): add unit-safe deterministic calculation kernel with full provenance
fix(fetch): re-validate redirect targets against the SSRF allowlist on every hop
docs(adr): record why report sections are data rather than code
```

Scopes follow the package layout: `api`, `calc`, `db`, `fetch`, `sources`, `agents`,
`render`, `storage`, `workflow`, `web`.

## Definition of done

A change is not done until **all** of these hold:

- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run mypy` passes
- [ ] `uv run pytest` passes, with no network access and no model spend
- [ ] New behaviour has tests, including at least one failure case
- [ ] Anything in `calc/` has property-based tests, not just examples
- [ ] Public functions and modules have docstrings explaining *why*
- [ ] No secrets in code, tests, fixtures, logs or commit history
- [ ] The change does not violate an invariant in `CLAUDE.md`
- [ ] If it changes an architectural decision, there is a new ADR

## Scope discipline

The work sequence in `docs/plan/ROADMAP.md` is deliberately ordered, and each item carries an
explicit **non-goals** list. Respect it. Folding a later task's work into an earlier one
produces changes that are hard to review and foundations that were never verified in
isolation.

If you find something genuinely blocking, stop and say so rather than working around it.

## Architecture decision records

Decisions that constrain future work go in `docs/adr/` using the
[Nygard format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

Write one when the decision is hard to reverse, when a reasonable engineer would ask "why
on earth is it done this way", or when you rejected an obvious alternative for a
non-obvious reason. Record the alternatives and why they lost — that is the part that is
useful later.

Never edit an accepted ADR to change its decision. Write a new one that supersedes it.

## Tests

- Deterministic code gets exhaustive tests. It is cheap and it is the correctness core.
- Model-facing code gets contract tests against a fake provider.
- External HTTP is replayed from recorded cassettes. A test that touches the real network
  is a bug in the test.
- Use `pytest.mark.integration` for anything needing Docker, and `pytest.mark.live_llm`
  for anything that would cost money.

## Dependencies

Adding a runtime dependency is an architectural decision. Prefer the standard library.
Prefer a well-maintained small library over a framework. If it pulls in a large
transitive tree, or it wants to own the control flow, write an ADR before adding it.
