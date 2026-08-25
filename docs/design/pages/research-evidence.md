# The evidence surfaces

*Where a figure is walked back to the bytes it came from. This is what the product is for.*

---

## At a glance

| | |
|---|---|
| **URLs** | `/runs/{id}/sources` · `/runs/{id}/claims` · `/claims/{id}` · `/runs/{id}/footnotes/{n}` · `/runs/{id}/valuation` · `/calculations/{id}` · `POST /runs/{id}/replay` |
| **Who arrives** | Somebody who doubts a number — usually the operator, occasionally the operator six months later |
| **From where** | A footnote marker in a report; the console's evidence links; a calculation's own inputs, recursively |
| **What they came for** | One question: **does this check out?** |
| **Token state** | 58 · 22 · 53 · 58 · 58 · 27 · 32 raw ramp classes |

---

## The job

**Answer "where did this number come from?" in one click, and "prove it" in two.**

The two-click standard is the design target and the product is built to meet it. A figure's
footnote resolves either to the formula that produced it or to the archived bytes it came
from, and each of those resolves onward to its own inputs.

---

## The surfaces

### `/runs/{id}/sources` — what this run acquired

A table: **Document · Tier · Published · Retrieved · Artefact · Admissible**.

**Deliberately shows everything.** A quarantined source appears here with its reason rather
than being filtered out — *"what did this run refuse to use, and why?"* is a question a reader
of the report is entitled to ask, and a table showing only what was used could not answer it.

Tier is provider-plus-kind resolved by table; an unknown pair is not citable at all.

### `/runs/{id}/claims` — the claim index

Every assertion the run makes, and one click from here to the excerpt behind any of them.
That is what makes the whole chain checkable in two clicks rather than in "read the source and
search for the sentence".

### `/claims/{id}` — the drill-down

The sentence, the figure, and the exact words behind it.

**The excerpt is shown verbatim, as stored, with the verifier's verdict beside it.** A page
that showed the excerpt without saying whether code had confirmed it would imply a check that
may never have happened, which is worse than showing nothing. Two blocks: *The figure*, and
the excerpt with its verdict.

### `/runs/{id}/footnotes/{n}` — the walk back from a marker

Marker numbers are meaningful because this page assembles the same document, with the same
inputs, as the preview that showed the marker. Three outcomes:

| Marker kind | What the page does |
|---|---|
| **Calculation** | Continues to the calculation walk |
| **Source** | Answers here: the source, its licence note, and **every claim in this run the verifier checked against it** |
| **Unresolvable** | States it in exactly the words the document used |

### `/calculations/{id}` — the arithmetic

The second click, and the recursive one.

| Block | Contents |
|---|---|
| The formula | |
| **Structural choices** | What was decided rather than derived |
| **What it rests on** | A table: **Input · Kind · Value · Origin** — and each origin is a link onward |
| **What the formula takes for granted** | |

Ownership is checked through the calculation's job rather than assumed, because a calculation
id is a UUID somebody could guess at.

### `/runs/{id}/valuation` — the phase's outcome

Both terminal methods, the sensitivity grid, and the comparables.

**Read back from the run's own ledger, never recomputed.** A page that re-ran the valuation
would show today's answer against yesterday's report, and both would look authoritative. Where
a figure is absent, this says the run did not produce it.

The sensitivity grid is 81 cells — nine by nine — and is the densest numeric display in the
product. A bank valued on residual income has no grid at all, and says so in its caveats.

### `POST /runs/{id}/replay` — reproduce this run

Re-derives everything the run produced and reports what still holds. Fetches nothing, calls no
model, costs nothing to press.

**A POST rather than a link**, and not only for the CSRF token: re-verifying a citation writes
its verdict back onto the row, so this changes stored state even though it reads like a report.

Each problem names what went wrong rather than saying "does not replay" and stopping there —
a re-derivation outside tolerance, a unit mismatch, and a re-run error are three different
findings.

---

## Inputs

Almost none. These are reading surfaces.

| Control | Where |
|---|---|
| Reproduce this run | `/runs/{id}` → `POST /replay` |
| Table filters | Where a table is long enough to warrant one. Filters rows already on the page — fetches nothing, computes nothing |

---

## States

| State | Notes |
|---|---|
| **Populated** | The ordinary case |
| **Empty, early in a run** | All three console evidence links lead to empty pages before acquisition. They say nothing about it |
| **A figure the run did not produce** | Says so, rather than showing a blank or a zero |
| **A citation that will not resolve** | Stated in the document's own words |
| **A quarantined source** | Listed, with its reason |
| **A bank** | No sensitivity grid; the caveat says why |
| **Replay: everything holds** | |
| **Replay: findings** | Each naming its own kind of problem |
| **Not yours / no such run** | The same 404 for both, deliberately — two answers would let a caller enumerate which ids exist by watching which ones answer differently |

---

## What is wrong today

**These pages look like database output.** Tables of identifiers, hashes, tiers and UUIDs, in
monospace, with no visual hierarchy. The information is complete and correctly reasoned; the
*question* the reader arrived with — does this check out? — is nowhere emphasised. On
`/claims/{id}` the verdict is a value in a block, not the headline.

**The verdict is the answer and it is not the loudest thing on any page.** Confirmed,
unconfirmed, or failed verification is what the reader came for. It should be legible from
across the room, and instead it sits inline with everything else.

**The chain is not visible as a chain.** Standing on a calculation, there is nothing showing
you are two steps down a lineage, where you came from, or how deep it goes. Each page is an
island that happens to link to another island. **The evidence chain is a surface, and it is
not currently rendered as one.**

**The excerpt is the most important text and is not treated as such.** The verbatim words from
the filing — the actual evidence — should be the visual centre of `/claims/{id}` and are
typographically ordinary.

**The sensitivity grid is 81 undifferentiated numbers.** No shading, no anchor on the central
case, no indication of which cells are plausible. It is the one place in the product where a
chart would say more than a table, and a chart is permitted — it just has to be produced on
the server, deterministically, because **a chart is a figure**.

**Hashes are shown in full.** Forty-hex-character digests inline, in tables, competing with
the content. They must be *available* — they are the proof — but they need not be prominent.

**Nothing distinguishes an admissible source from an excluded one visually.** The table has an
"Admissible" column, which is correct and easy to miss on a long table.

**Replay's result has nowhere good to go.** It re-derives an entire run and reports findings,
and the reporting surface is a plain page listing them.

---

## What to improve

**1. Lead every evidence surface with the verdict.** *Confirmed — code re-read the artefact
and found this excerpt.* Everything else supports it. This is the single largest improvement
available on these pages and it costs no new data.

**2. Render the chain as a chain.** A visible lineage — figure → calculation → input → source
— present on every page in the walk, showing where you are and what is above and below you.
The data for this exists; it is what the whole provenance model is.

**3. Make the excerpt look like evidence.** Verbatim text from a filing, set as a quotation,
with the surrounding context and the located position. It is the payoff of the entire system.

**4. Give the sensitivity grid a visual form.** Shading by value, the central case anchored,
the axes labelled with what they vary. Server-rendered and deterministic.

**5. Demote the hashes without hiding them.** A short prefix that expands, or a link labelled
by what it proves rather than by its digest.

**6. Use the drawer.** An excerpt, a calculation's inputs, a source's provenance — every one
is something a reader wants to glance at without losing their place in the report. The drawer
is built, accessible, tested, and used in exactly one place.

**7. Make the console's evidence links honest about emptiness.** A count would do it.

**8. Design replay's readout.** It answers "does this run still reproduce?", which is a strong
claim, and it deserves a result page that states it plainly and lists exceptions.

---

## What must not change

**Everything the run acquired is listed, including what it refused.** With the reason.

**The excerpt is verbatim, as stored.** Never re-wrapped, never summarised, never tidied.

**The verdict is always shown beside the excerpt.** An excerpt without a verdict implies a
check that may not have happened.

**The valuation is read back from the ledger, never recomputed.**

**An absent figure says the run did not produce it.** Never a blank, never a zero.

**Every figure on every one of these pages is rendered by the server.** No client-side
formatting, no client-side arithmetic, no computed totals. A figure computed in the browser is
a figure no guard has ever seen.

**A chart is a figure**, so a chart is built server-side and deterministically — the same rows
must always produce the same picture, which is what lets a test hold it.

**"Not yours" and "no such run" answer identically.**

**Replay stays a POST.** It writes.

---

## Done when

- A reader lands on any evidence surface and knows within two seconds whether the thing checks
  out.
- The walk from a figure to the bytes behind it is visible as a single connected path.
- The excerpt is the most prominent element on the claim page.
- The sensitivity grid can be read at a glance and interrogated in detail.
- Hashes are available on every artefact and prominent on none.
- Nothing on these pages looks like it was designed for the person who wrote the schema.
