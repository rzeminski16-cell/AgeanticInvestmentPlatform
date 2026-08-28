# Review and corrections

*A structural review of the Tracework deliverable against the original brief in
[`../design/`](../design/README.md), the eight invariants in `CLAUDE.md`, and the code as it
actually stands. Written 2026-08-25.*

**Where this document and `01-design-system.md` disagree, this one wins.** It is an addendum,
not a rewrite: everything not named here stands exactly as delivered.

---

> **All nine corrections were resolved on 2026-08-25.** D1 → [ADR 0088](../adr/0088-a-fixed-scheme-region-carries-its-own-measured-palette.md).
> D2 → [ADR 0087](../adr/0087-a-verdict-has-two-halves-one-composed-and-one-authored.md), which
> takes the *model-authored* option and makes it work by splitting a verdict into a composed half
> and an authored one. D4 → [ADR 0089](../adr/0089-the-run-you-are-watching-has-an-address.md).
> D3, D5–D9 are settled in [the plan](../plan/interface-overhaul.md). The product is
> **Tracework Invest**.

## Verdict

**Adopt. Nine corrections before build, one of which is a WCAG failure.**

This is a disciplined piece of work. It understood the constraints rather than working around
them, and in three places it is more rigorous than the brief that commissioned it.

**What was checked rather than taken on trust:**

| Claim | Method | Result |
|---|---|---|
| The 24 sanctioned contrast pairings in §2.7 | Recomputed every ratio from the hex values, WCAG relative luminance | **Exact. Zero mismatches**, to two decimals |
| "Every complex page reflows at 320px" | Chromium at 320×800 across all twelve pages, measuring `scrollWidth − clientWidth` | **True.** No page overflows |
| "No duplicate IDs were found on any page" | Enumerated every `[id]` on all twelve pages | **True** |
| "Focus treatment — Pass" | Focused a navigation link in light theme and read the computed outline against the rail | **False. 2.04:1 — see D1** |

A validation report whose numbers survive independent recomputation is rare, and the one thing
it got wrong is the one thing its own token table could not have caught.

### The three places it improved on the brief

**It fixed the contrast failures properly rather than nudging them.** The brief reported
`ink-faint` failing at 2.98:1 and asked for a repair. The response abolished the token —
*"There is no separate 'faint' text token"* — and made `ink-subtle` the quietest permitted
text, at 4.62:1 or better on every sanctioned background in both schemes. It also introduced
the separate `control-boundary` token the brief asked for, at 3.5–4.3:1, and stated in
normative language that `line` and `line-strong` are decorative and may never bound a control.

**It made the no-JavaScript contract a matrix rather than a promise.** Thirteen rows, each
naming the enhanced behaviour and the scripting-off behaviour side by side. That is an
acceptance test.

**It refused to overreach on JavaScript.** §17.3: *"This design system requires no new
general-purpose JavaScript pattern."* It declined the knowledge-graph island the challenge
appendix pre-authorised, and left it in `Later — reserve space, do not simulate`. A designer
who takes the one permission on offer and does not spend it has understood the argument.

---

## The corrections

Ordered by consequence. **D1 and D2 are blocking; the rest are before-build.**

### D1 — The navigation rail is unspecified, and its focus indicator fails WCAG 2.2 AA

**The rail is permanently dark in both themes.** The prototype paints it `#102B35` on a light
page. That is a deliberate and good decision — but the rail's colours appear nowhere in the
normative token tables of §2.2–2.5, so **no pairing on it was ever measured**, and §2.7's
focus rows measure the ring against `surface`, `canvas`, `sunken` and `raised` only.

Measured against the rail at `#102B35`:

| Token painted on the rail | Ratio | Verdict |
|---|---:|---|
| `focus-ring` light `#00606D` | **2.04** | **Fails 1.4.11 (needs 3:1)** |
| `verification` light `#0F6673` | **2.23** | **Fails** |
| `decision` light `#7A4B00` | **2.00** | **Fails** |
| `focus-ring` dark `#B5ECF0` | 11.43 | Passes |
| `verification` dark `#B5ECF0` | 11.43 | Passes |
| `decision` dark `#FFD27A` | 10.40 | Passes |
| Selected-row fill `#183945` on rail | **1.21** | Decorative only — cannot carry selection alone |

Confirmed in Chromium: focusing the first navigation link in explicit light theme yields
`outline-color: rgb(0, 96, 109)` at `3px` on that rail. **Keyboard focus is very nearly
invisible in the navigation, in the default theme.**

**The correction, which is small and complete:**

1. **The rail is a fixed-dark region and takes the dark-scheme accents in both themes.** Every
   dark accent clears 3:1 on it by a wide margin. Scope this with a container rule keyed on
   the rail, not by duplicating tokens.
2. **Add the rail to §2.2** as a named family — `nav`, `nav-surface`, `nav-ink`, `nav-muted` —
   with its measured contrast, exactly as every other surface has. Text on it is already fine:
   `nav-ink` 13.65, `nav-muted` 8.54.
3. **State that the selected-row fill is decorative.** At 1.21:1 it cannot be the only cue.
   The teal left rule at 11.43:1 is what carries selection, and `aria-current="page"` is what
   carries it non-visually. Both are already in the prototype; the rule needs writing down so
   the fill is not later "simplified" into carrying the job alone.

**Generalise the lesson:** any region that keeps one scheme's colours while the page changes
scheme needs its own measured block. It is the same class of gap that produced §2.5 of the
roadmap.

### D2 — The prototype's review verdict cannot be produced by the specified server data

The specification and the prototype describe different products, and only one of them is
buildable.

| Source | The sentence |
|---|---|
| `02-page-specifications.md` §review | *"2 items need a decision; 3 red-team challenges are available to read; £6.40 of £8.00 spent."* |
| `prototype/review.html` | *"The draft is complete, traceable, and cautious—but the critic found one valuation dependency worth reading."* |

The first is a count, a count and two figures — composable deterministically from rows the run
already holds, at no cost. **The second requires reading the challenge and judging it**, and
no deterministic composer produces "cautious" or "one valuation dependency worth reading".

**Resolved 2026-08-25: the operator chose the authored sentence, and ADR 0087 is how it is
built.** A verdict becomes two halves — a *composed* half that is live and cannot go stale, and
an *authored* half a model writes once over a subject that has already frozen. The main menu
never gets an authored half, because its verdict aggregates live state across runs and there is
no moment to write it once about.

The analysis below stands as the reason the plain reading does not work. Taken literally it
would require:

- a **new agent role** — which ADR 0035 makes an ADR, not a diff;
- a **model call on a page load**, which every cost rule in the platform is built to prevent;
  and
- a **judgement**, which ADR 0074 says is never a source reference and must be stored as one
  if it is to exist at all.

**The deliverable already contains the right answer, applied elsewhere.** §9 `Should` says of
the report-to-report delta: *"If the summary is model-authored, store it as a judged
statement; do not generate it on page load."* That rule governs `review_verdict`,
`verdict_explanation` and `overview_verdict` identically.

**ADR 0087 answers all three.** The role is admitted by a record, as ADR 0035 requires. The
call happens **once when the subject freezes**, not on a page load. And the sentence is stored
as a step output rather than in the judgement table §3.5 has not built yet — because that table
is for views a *person* holds, and putting a model's interpretation in it would be folding a
later item's work into an earlier one.

**So the rule is scope, not prohibition.** A `*_verdict` field has an authored half only where
its subject has stopped changing: the review gate, the evidence pages, a finished report. Every
other surface — the run console, and **the main menu permanently** — is composed only. And the
authored half is never evidence: no claim may name it, no citation may resolve to it, and the
type carrying it cannot construct a source reference.

The prototype's copy is now a fair statement of what the review gate will say. It remains a
statement of *tone* rather than a content spec — the sentence is the model's each time.

### D3 — `ink-faint` survives in the prototype stylesheet

§2.2 abolishes the token. `prototype/assets/tracework.css` still defines and uses it, 18 times.
Harmless in a prototype; the moment anyone lifts a rule from that file, the failing token is
back. **The production stylesheet defines `ink-subtle` and no `faint` token at any point**, and
the ramp-ratchet test (see the testing plan) should treat `ink-faint` as a banned name.

### D4 — "Active run" is a navigation item with a state-dependent destination

The prototype's rail carries **Active run**. `NavItem.href` is a static string on a frozen
dataclass, and `tests/test_shell_nav.py` asserts every `href` resolves to a real route — so
`/runs/{id}` cannot be a nav item, and a nav item that sometimes has no destination is worse
than none.

**Correction: add `GET /runs/active` as a real route that redirects** — 303 to the operator's
current run, or to `/requests` when there is none. The nav stays static data, the drift test
stays honest, and the operator gets the one-action access the design is for. Add the route to
`UNLISTED` only if it is *not* given a nav item; if it is a nav item, it is in the nav and
needs no exemption.

### D5 — "Components" must not ship

The design-reference page is a prototype artefact. It has no place in `INSTALLED_TOOLS`, the
navigation, or the route table. Keep the component inventory as a test fixture and a
screenshot baseline instead — see the testing plan.

### D6 — Two badges are shown; one provider is registered

The rail shows **Overview 3** and **Requests 2**. `web/shell/badges.py` registers exactly one
provider, `approvals`. Two counts is a *data-contract decision*, not a template detail:
either a second `BadgeProvider` is registered with its own key, label and ADR, or the design
shows one badge. **Do not template a number that no provider produces.**

The likely right answer is one badge on Requests, and the Overview count folded into the
attention verdict, which already says how many things are waiting and in what state.

### D7 — The prototype's navigation contradicts its own handoff

`tracework.js` sets `navShell.open = matchMedia("(min-width: 60.001rem)").matches`. The handoff
§5 forbids exactly this: *"Do not add `open` based on guessed viewport width."* The handoff is
right and the prototype is wrong; build the handoff.

Two consequences worth carrying into the build:

- **The wide-width CSS reveal must be verified per engine.** Making a *closed* `<details>`'s
  content visible from author CSS is not reliably specified, and Chromium's `::details-content`
  now applies `content-visibility: hidden`, which a `display` override on the child does not
  defeat. The handoff already says to verify this and names the fallback (one CSS layout
  variant around the same `nav`, never a duplicated tree). **Do the verification in Tranche 4,
  not at the end.**
- **The markup ships `open`.** Measured: with scripting off at 320px the panel is expanded over
  the content on load, and eight of nine links are visible. That is the safe direction to fail,
  but it is currently an accident. Make it a decision: `<details open>` in the markup, closed
  by script at narrow widths, and a test that asserts the links are reachable with scripting
  off at both widths.

### D8 — axe-core has nowhere to come from

Four separate checks require it. The suite runs with **no network**, and `package.json` has
Tailwind and htmx and nothing else. Vendoring is not optional here — it is the same rule that
governs every other asset.

**Correction:** add `axe-core` as an npm devDependency, commit `axe.min.js` under a test
fixture path with its SHA-256 recorded, and inject it from the local file in the browser
tests. It is a test-time asset and must never be served from `/static`.

### D9 — Three font families is a 3× vendoring increase, and it is a task

Barlow Semi Condensed, Source Sans 3 and IBM Plex Mono replace Inter. Latin and latin-ext for
each is **six WOFF2 files where there are two**, six SHA-256 pins in `tests/test_fonts.py`
where there are two, and three licence files where there is one. All three are SIL Open Font
Licence 1.1, so there is no licence risk — but the work is real and belongs in the tranche
that does it rather than being discovered inside it.

**Two things to settle when it is done:** prefer the variable cut of each family where one
exists, so a page using three weights makes one request per family rather than three; and keep
the "no italic" rule — nothing in the product is italic, and three unused italic faces is a
larger version of a mistake this repository already declined once.

---

## What is missing, and is being added

The deliverable is a design specification and does not claim to be a delivery plan. Six things
it could not know are supplied in [`../plan/interface-overhaul.md`](../plan/interface-overhaul.md)
and [`../plan/interface-overhaul-testing.md`](../plan/interface-overhaul-testing.md):

1. **How this interleaves with the rest of the roadmap.** §2.1 and §3.1 are in flight and both
   touch templates this overhaul rewrites.
2. **Which ADRs are required.** Several proposals extend recorded decisions.
3. **A ratchet.** "No stock ramp remains" is a test somebody deletes on week one of a
   multi-week migration.
4. **The two-process pytest rule.** A browser-heavy plan has to respect it or it produces
   results that depend on collection order.
5. **A resolution for all 84 server proposals** — 72 `[NEW SERVER DATA]`, 7
   `[NEW SERVER BEHAVIOUR]`, 5 `[NEW ROUTE]` — each as adopt, defer or decline.
6. **The seven planned tools' own pages.** In scope for the launcher, unspecified as pages.

---

## Adopted without change

Recorded so the build does not relitigate them:

- The **evidence spine** as the signature component, and the decision to spend permanent
  horizontal space on provenance.
- **Verdict first, evidence beside it, proof on demand** as the hierarchy for every operational
  page.
- The **shared gate frame** across all seven gates, with a sticky decision panel that follows
  the evidence in DOM and focus order.
- **Conditional gates shown as conditional**, with `decisions_remaining` as a range. A
  seven-step wizard would have been a lie about the workflow, and the deliverable says so.
- The full **token, type, spacing and elevation system**, subject to D1 and D3.
- The **no-JavaScript matrix** (§15) as written.
- The **do-not list** (§12 of the handoff) as written. It is the best short statement of this
  platform's interface rules that exists, including in the brief that commissioned it.
- **Refusal and failure as separate visual families.** The brief asked for the distinction;
  the response gave refusal its own colour pair and its own vocabulary.
- The **deterministic sensitivity heatmap** with byte-for-byte testing and table equivalence.
- Leaving portfolio return and exposure in **`Later — reserve space, do not simulate`**. That
  is roadmap §3.2 and it needs arithmetic that does not exist yet.

---

**Next:** [the implementation plan](../plan/interface-overhaul.md) · [the testing
plan](../plan/interface-overhaul-testing.md)
