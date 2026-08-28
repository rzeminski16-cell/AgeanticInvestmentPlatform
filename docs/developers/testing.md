# Testing

*How the suite is layered, what each layer buys, and the two rules that stop it lying to
you.*

---

```bash
uv run pytest --ignore=tests/e2e     # default suite: no network, no model spend
uv run pytest tests/e2e              # browser tests (Chromium + PostgreSQL)
just test-all                        # both, as two processes
just eval                            # the eight blocking metrics, on their own
uv run pytest --cov                  # with coverage
uv run pytest -m integration         # database tests only
uv run pytest -m "not integration"   # skip anything needing PostgreSQL
```

**The evaluation gate is inside the default suite and is also its own step in CI.** Both,
deliberately: it is ordinary pytest so it cannot be forgotten, and a named step so a build
that goes red because citation accuracy moved says so on the summary line rather than in the
middle of two thousand dots.

**The browser tests must run in their own pytest process.** Playwright's synchronous API
drives an asyncio loop on the main thread and keeps it running for the life of its session
fixture, so any asyncio-based fixture that runs after a browser test in the same process
fails with "Runner.run() cannot be called from a running event loop". `just test-all` is
therefore two commands rather than one `pytest` invocation.

The whole vertical slice — plan, both gates, budget guard, acquisition, calculation,
rendered report — runs in the default suite against a fake provider and a stubbed EDGAR
client, so it costs nothing and needs no network. That is the entire reason the provider
abstraction exists: a suite that spent money would be a suite nobody ran.

**A fake provider proves nothing about what goes on the wire**, and the first real model call
found that out: a 400 on a request shape 1,300 passing tests had never looked at. So
`tests/test_anthropic_provider.py` asserts the payload itself, with the SDK client stubbed —
which parameters are sent, which are deliberately absent, and what each failure message tells
the operator to do. Alongside it, `TestTheSdkContract` checks the *installed* SDK for the
surface the provider depends on, so an upgrade that moves it fails there rather than on the
next live run. See `docs/adr/0015-the-vendor-contract-is-asserted-not-assumed.md`.

The browser tests drive a real Chromium against a real uvicorn server on an ephemeral
port. They exist to catch what an in-process HTTP client structurally cannot — a form
field the server never receives, a submit button outside the form, an HTMX response the
browser silently discards. Two genuine bugs found that way are recorded in
`docs/adr/0007-request-validation-boundaries.md`.

If Chromium was installed outside Playwright's own cache, point at it with
`PLAYWRIGHT_CHROMIUM_PATH`; `/opt/pw-browsers/chromium` is picked up automatically.

Tests that would make real, billable model calls are marked `live_llm` and never run by
default.

Database tests run against a separate `aer_test` database, inside a transaction that is
rolled back afterwards, so they never touch your development data. If PostgreSQL is not
running they **skip with the reason** rather than failing, so `uv run pytest` still works
on a machine with nothing started. Point them elsewhere with `AER_TEST_DATABASE_URL`.

### Verifying it by hand

`docs/developers/testing-by-hand.md` is a checklist for everything the suite structurally cannot
prove: Docker Compose, the pages as a person sees them, the guards provoked deliberately,
and one real run against the real SEC and a real model call. Most of it costs nothing; the
one section that spends money says so and says roughly how much.

---

## Two rules that are not optional

**One pytest process per database.** The suite empties tables between tests, so two runs
sharing `aer_test` delete each other's rows and fail in whichever one was mid-test — showing
up as "no user exists" moments after a fixture committed one, nowhere near the cause. Give
each concurrent run its own `AER_TEST_DATABASE_URL`.

**The full suite is two processes**, `pytest --ignore=tests/e2e` then `pytest tests/e2e`, for
the Playwright reason above. A single invocation produces a result that depends on collection
order, which is worse than a slower one.

---

## The interface

*Written for roadmap §3.12, the interface overhaul. The design specification is in
[`../design/`](../design/README.md); this is how the result gets checked.*

### What is already covered, and what that is worth

115 browser tests across nine files, and the coverage is genuinely good in two places that
matter and absent in four that are about to.

**Behaviour is covered.** `tests/e2e/` drives a real browser through the request form, the
run console, the evidence surfaces, the valuation surface, the portfolio screen, the skills
editor and the shell. The drawer's focus trap is proved by tabbing rather than asserted about
a template: focus moves in, wraps at both ends, Escape returns it to the row it came from,
and the background does not scroll.

**Scripting-off is covered, and it is the one rule ADR 0006 states as binding.** Ten tests
open a context with `java_script_enabled=False` and drive the same path — the preview link is
an ordinary navigation, the form is an ordinary POST, the console falls back to a meta
refresh. Extend that pattern to any new surface. A form whose validation depends on a script
is a form that silently accepts anything the moment the script fails to load, and this one
commissions spending.

### The four things the suite cannot currently say about a screen

Each of these is a real gap rather than a nice-to-have, and §2.5 of the roadmap is what the
first of them cost.

**1. Whether a page is right in dark mode.** `tests/test_web_pages.py` proves a great deal
about the *mechanism* — that the two dark blocks declare identical values, that the compiled
`dark:` variant answers an explicit choice as well as the media query, that choosing a theme
is remembered and that a value which is not a theme never reaches the attribute. Every one of
those passes today, and forty-one templates are still slate grey beside navy.

That is the lesson worth generalising: **these tests prove the switch works, not that
anything downstream of it is right.** No browser test sets `color_scheme`, so no test has
ever looked at a dark page.

- Set `color_scheme="dark"` on the browser context, and separately drive the in-app control,
  because they are different code paths — the media query and `data-theme`.
- Assert the **computed** background and foreground of the page's own surfaces, not the class
  string. A class string is what is already wrong.
- The one assertion worth making everywhere: **contrast**. Read the computed colours and
  check the ratio, rather than eyeballing a screenshot. It catches the whole class in one
  test per surface, and it fails with a number somebody can act on.

**2. Whether a page works at any width but the default.** Nothing in `tests/e2e/` sets a
viewport, so every browser test runs at Playwright's default and the suite has no opinion
about a narrow screen. Pick two widths and hold them: one narrow, one wide. Assert what
actually breaks at a narrow width — a table that forces the page to scroll sideways, a
control that leaves the viewport, a heading that collides with its badge. **The page body
must never scroll horizontally**; a wide table scrolls inside its own container.

**3. Whether anything is reachable without a mouse.** There are 33 `aria-` attributes and 31
`role=` attributes across the templates and nothing checks a single one of them. The drawer's
keyboard behaviour is the exception and it is the pattern: drive the keyboard, assert what
focus does.

Add an automated pass — axe-core, injected into the page and run per surface — and treat its
output as a build failure rather than a report. Automated checking finds perhaps half of what
is wrong, which is worth having and is not the whole job; the rest is
`testing-by-hand.md` §8.

**4. Whether a page looks like it did yesterday.** No visual baseline exists anywhere.

**Be careful what you baseline.** A full-page screenshot per surface fails on every
legitimate change, and a suite whose failures are usually noise is a suite whose failures
stop being read — which costs more than it buys. Baseline **components, not pages**: the
card, the KPI tile, the provenance badge, the grade chip, the empty state, the nav panel,
each in both schemes. Those change when somebody means them to change.

### The test §2.5 asks for, and why it belongs here

The palette migration is to end **with a test that fails when a template reintroduces a raw
ramp**. Write it with the migration, not after it:

```bash
grep -ohrE '\b(text|bg|border|ring|divide|from|to|via|placeholder|decoration|outline|shadow|accent|caret|fill|stroke)-(slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}' \
  src/aer/web/templates | sort | uniq -c | sort -rn
```

It is the enforcement shape this repository already uses twice — ADR 0013 greps every module
for a seeded section key, ADR 0077 greps every template for a provenance label — and it is
the only kind of check that survives contact with the next person in a hurry.

**Ratchet it rather than gating on zero.** 1,837 occurrences on 2026-08-25 across 41 of 54
templates; a test asserting zero is a test somebody deletes on day one of a migration that
takes weeks. Assert a per-template ceiling that only ever falls, and a hard zero for the
thirteen templates that are already clean, so the shell cannot regress while the research
tool is being rewritten.

### Two failure modes to design the tests against

**A test that asserts a class string proves nothing about a colour.** It is the mistake that
let §2.5 happen underneath a green suite. Assert computed style, or assert nothing.

**A green Linux suite says nothing about behaviour a host supplies.** Roadmap §4.10 is the
record: a `.woff2` served as `application/octet-stream` on Windows and `font/woff2` on Linux,
because `mimetypes` seeds itself from the operating system. Two of the three defects the
by-hand sheet has found were invisible to CI by construction. Fonts, date inputs, scrollbar
widths and system colour preferences are all in that class.

---

**See also:** [testing by hand](testing-by-hand.md) · [the design
specification](../design/README.md) · [the roadmap](../plan/ROADMAP.md)
