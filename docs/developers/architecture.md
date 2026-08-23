# Architecture

*How one research tool became a platform that can hold several, and where the boundary
between them sits. Read [`knowledge-map.md`](knowledge-map.md) first — it covers the one
rule, the anatomy of a run, the trust zones and the invariants. This document picks up
where that leaves off.*

---

## The finding this rests on

The equity-research tool was never really an equity-research tool. It is an
**evidence-and-arithmetic engine with one workflow mounted on it**, and most of the engine
was already domain-agnostic before anybody set out to make it so:

| Already generic | Evidence |
|---|---|
| Workflow engine | `aer/workflow/engine.py` imports only `Job`, `JobStep`, `Cost`, `JobCancellation`, hashing, errors, tracing. Zero equity vocabulary. |
| Section rendering | `aer/sections/render.py` imports only `HouseStyle` and `render.display`. |
| Evidence spine | `aer/storage`, `aer/extract`, `aer/core/schemas/extraction.py` — a locator points into an extraction, not into a subject. |
| Fetch, providers, hashing, CSRF, templating, DB base | No module mentions a company. |
| Approval machinery | `services/approvals.py` — payload hashing, ordering and refuse-double-decision are all subject-agnostic. |

So establishing the boundary was mostly a matter of *declaring* one that already existed,
then fixing the four places where an equity mandate was genuinely load-bearing.

## The kernel and the tool boundary

**The kernel** is everything above: runs, steps, budgets, gates, approvals, evidence,
extraction, calculation, provenance, rendering, cost metering, the audit log. It knows
nothing about companies.

**A tool** is a registered capability that mounts on the kernel — it brings a subject, a
workflow, its own surfaces, and the ADR that admits it. Equity Research is one. Portfolio
is the second.

Registration is **contribute-or-fail**: an explicit tuple in `web/tools/registry.py` plus a
test that fails when you forget. Not entry points, not `pkgutil.walk_packages`. A tool row
with a `PLANNED` status and no page is a placeholder that says what it is waiting on; a
tool with a primary action and no page raises at import.

**Adding a tool is not the sixth extension recipe.** The five recipes in
[`extending.md`](extending.md) extend the research tool from inside, and each inherits a
subject, a run, a budget and a gate that already exist. A tool brings its own. That is why
it is settled by ADR 0071 and why it is the only kind of change that adds a row to
`INSTALLED_TOOLS`. If what you are building fits one of the five, it is not a tool.

## The four seams that had to be fixed

Declaring the boundary exposed four places where the research tool's assumptions had leaked
into the kernel. All four are now closed.

**1. The run root was a research request.** Every run hung off a `research_requests` row,
so a run with no company had nowhere to live. `work_orders` is now the supertype;
`research_requests` is a 1:1 detail row sharing its key. A model call no longer requires an
equity mandate, and still requires a cap (ADR 0072).

**2. Evidence scope took a `ResearchRequest`.** `EvidenceScope(work_order_id, as_of_date,
point_in_time, subject_kind, subject_id)` replaced it in `visible_facts`, `visible_sources`
and `verify.citations`, carrying the run identity so ADR 0061's one-predicate rule — evidence
is scoped to the *subject*, not the request — survives unchanged.

**3. A source reference resolved by hope.** `SourceRef.fact()` covered several relations
that hold published figures and resolved as "a row in `financial_facts`". Nothing complained
when a caller meant neither. There is now **one constructor per relation**, each carrying a
table discriminator. Two relations that had no loader at all — `macro_observations` and
`securities` — now resolve, and the price half of that defect was live rather than latent
(ADR 0076).

**4. The workflow was a hard import.** Four modules imported `vertical_slice_v1` directly,
so an unrecognised `workflow_version` blanked the run console. A `WorkflowDefinition`
registry keyed by version replaced it: the engine now runs the steps **the job recorded**
rather than whichever workflow happened to be imported.

## The shell

One chrome, many tools, and no tool owning the frame.

- **Navigation is data.** A frozen `NavItem`/`NavSection` tuple and a template loop, not
  hand-written anchors. A drift test asserts every `href` resolves and every page route is
  either in the nav or explicitly named as unlisted.
- **`shell` is injected in `render()`** and is constructible with no database — asserted
  against a broken-engine client, because `StrictUndefined` would otherwise turn the one
  page an operator opens when Postgres is down into a 500.
- **Badge counts are off the critical render path**, behind `GET /_shell/badges`, one
  `BadgeProvider` per counted thing, cached briefly and best-effort in both directions:
  Redis being down must not cost you the sidebar.
- **JavaScript may own chrome and never a figure** (ADR 0077). The drawer's focus trap is a
  script; its semantics are markup. A grep asserts no page grows a second focus trap, and
  every trigger is a working link before it is anything else.

## Provenance across tools

The chain does not change shape when a second tool uses it, but it gained a third arm.

A claim may name a fact, a calculation, **or an attestation** — something the operator
asserted about their own book, at two times, with a grade of evidence. `documented` means a
hashed artefact with the full chain unchanged; `attested` means typed and self-certified.

**The grade propagates up a lineage.** A net asset value computed from one attested holding
is an attested net asset value, and reaches a shareable surface as a type with no field for
a bare number. That is enforced in `aer/calc/attestation.py`, in the pure kernel, precisely
so it cannot be forgotten one caller away in a service.

**A judgement is never a source reference** (ADR 0074). A view somebody holds and a number
somebody typed must not become interchangeable, because a thesis that can be cited as
evidence is a system that can launder an opinion into a fact.

## Two clocks

The research clock is an **as-of date**: a run is a point-in-time selection over the record,
and look-ahead is refused. The portfolio clock is **continuous**: a book is followed, and
what it was worth last March is a different question from what it is worth now.

Conflating them is the mistake ADR 0075 exists to prevent, and it is why a watchlist —
followed continuously, researched as at a date — is not built yet: it needs both, plus a
standing budget that is not one run's cap.

## What is deliberately absent

- **No `positions` table.** A position is a calculation (ADR 0083).
- **No trade execution, broker connection, optimiser or multi-user deployment.** Out of
  scope, not deferred.
- **No currency-exchange transaction kind.** One event touching two currencies does not fit
  a one-currency row, and the failure mode is a silently double-counted cash balance.
- **No GBP risk-free rate.** The Bank of England documents a CSV route its own `robots.txt`
  disallows; reaching around that is circumvention. The euro is the pivot instead, and
  `risk_free_series_for("GBP")` refuses rather than substituting a US Treasury yield.

---

**Next:** [extending it](extending.md) · [the decision records](../adr/) ·
[the roadmap](../plan/ROADMAP.md)
