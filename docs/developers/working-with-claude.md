# Working with Claude on this platform

*Two machines, one branch, and the short list of things only your machine can do.*

---

Claude Code runs this repository in two places, and the difference is **environment, not
capability**. It is the same tool with the same tools; what changes is whose operating
system, whose credentials, whose network and whose data.

| | Cloud session (claude.ai/code) | Your machine (terminal or VS Code) |
|---|---|---|
| Default suite (6,865) | Yes | Yes |
| Browser suite (183) | Yes | Yes |
| `ruff`, `mypy --strict` | Yes | Yes |
| Screenshots, PDF rendering, the worker | Yes | Yes |
| **Windows** | No — Linux container | **Yes** |
| **Real model calls** | No — placeholder key | **Yes, and billed to you** |
| **Real SEC / Companies House / EODHD** | No — recorded fixtures | **Yes** |
| **Your book, your runs, your artefact store** | No | **Yes** |
| Breaking the database is | free | your afternoon |

**The automated suite is not the reason to work locally.** All of it already runs in the
cloud. What local buys is the four rows in bold — everything the suite structurally
cannot reach, which is also everything still open on the acceptance list.

## What belongs where

**Cloud.** Anything the suite can answer: writing code, fixing defects, refactoring,
long runs that would tie up your laptop for forty minutes, and any experiment where
killing PostgreSQL is a shrug rather than a problem. Two test databases in parallel cost
nothing there.

**Local.** The four things:

1. **The Windows e2e failure.** `docs/plan/acceptance-2026-09-08.md` §6. It does not
   reproduce on Linux, and the next step is a Windows run of that one test with the
   server's own log and the browser's network panel. No model spend, no network — the
   cheapest of the four and the only real defect among them.
2. **A live confirmation run.** The one that matters most. Every defect fixed in the
   September round is argued from code and covered by tests, and **not one has been
   verified by an actual run**. The step-ordering fix is supposed to produce a market cap,
   comps and a valuable portfolio; the tests prove the ordering changed, not that a real
   MSFT run gets there. `docs/users/the-confirmation-run.md` is the runbook.
3. **The source adapters against live endpoints**, on your subscription — which settles
   whether the EODHD tier includes `/api/fundamentals` by trying it.
4. **Your actual book** — attestations, holdings, the portfolio's third door (ADR 0093).
   Those screens have only ever been seen with fixtures.

## One branch, two machines

The failure mode is ordinary and annoying: two checkouts of the same branch drift, and
the second push is a merge conflict in work nobody wanted to merge.

**Push before you switch.** Every time, in both directions. A session that ends without a
push is a session whose work the other machine cannot see.

**Pull before you start.** `git fetch origin && git status` is the first thing either
session should do, and it is on the allowlist so it costs no prompt.

**Never force-push this branch.** It is the one action that can discard the other
machine's work, and neither machine can tell the difference afterwards. If the histories
have genuinely diverged, merge them — a merge commit is ugly and recoverable; a
force-push is neither.

## What `.claude/settings.json` decides

Committed, because it is a statement about this project rather than a personal preference.

**The allowlist is "routine and reversible".** Tests, linters, type-checking, `alembic
upgrade`, the read-only `just` recipes, `git status`/`diff`/`log`/`add`/`commit`. These
run dozens of times an hour and a prompt on each is a treadmill nobody reads by the
twentieth.

**`git push` is deliberately *not* on it.** Pushing is outward-facing and happens a
handful of times a session, so the prompt costs almost nothing — and it is the one place
you see the full command, flags included, before it runs. That is also what closes the
force-push hole without a deny rule that would block the legitimate
`--force-with-lease` case the branch instructions describe.

**Two recipes are denied outright**, not merely prompted: `just down-hard` (deletes the
data volumes) and `just migrate-base` (unwinds every migration). Both destroy your
development database with no undo, and both are things you can run yourself in a terminal
in five seconds. A tool that can do them is a tool that can do them by mistake.

**`.env` is denied to reads.** Your real Anthropic key lives there, and a transcript is a
durable record. Nothing Claude does needs the value — `aer preflight` answers "is it
configured?" without printing it. `detect-secrets` and the git-ignore already stop the
file being committed; this stops it being *quoted*.

**If a rule does not match, it fails safe and fixes itself.** The allow patterns use the
`Bash(<command>:*)` prefix form. A pattern that fails to match costs a permission prompt
and nothing else — so if one of these still asks, answer it once with *don't ask again*
and Claude Code writes the correct rule into `.claude/settings.local.json`, which is
git-ignored and yours. The deny rules and the deliberate absence of `git push` are what
carry the actual safety, and neither depends on a pattern matching.

## Before anything expensive or destructive

**Take a backup.** `just backup <destination>` covers the database and the artefact store
together, and `just verify-backup <source>` proves it before you rely on it. Both are on
the allowlist. Do this before a live run, before a restore, and before any migration you
have not run on this data.

**Know what a run costs.** The full vertical slice is estimated at **£9.31** and drafting
alone at £5.00. Caps refuse rather than warn — invariant 6, verified independently — so
the spend is bounded by `max_cost_gbp` on the request. Set it deliberately; a ceiling of
£3.00 stops the run at the moment before drafting, which is a free checkpoint.

**Live model calls need saying out loud.** `just test-live` and any real run bill your
account. Nothing in the default suite does: `-m 'not live_llm'` is in `addopts`, and that
is enforcement rather than a hint.

## A failure mode worth recognising

A suite run that comes back in under three minutes reading *"12 failed, 4,473 passed,
2,377 skipped"* has **not** run. It is what the suite says when PostgreSQL is not
reachable: `tests/db_fixtures.py` skips rather than failing, deliberately, so `uv run
pytest` still works on a machine with nothing started — and the handful of genuine
failures are the backup tests, which want a live `pg_dump` target.

This cost two forty-minute runs in the September round before it was recognised. A real
run is forty minutes and reports thousands of passes with no skip block. **Check the
duration and the skip count before believing the colour**, and `just health` says in one
line whether the services are up.
