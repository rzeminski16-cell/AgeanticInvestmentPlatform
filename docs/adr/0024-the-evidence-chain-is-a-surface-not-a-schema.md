# 24. The evidence chain is a surface, not a schema

- **Status:** Accepted
- **Date:** 2026-07-30

## Context

By the end of task 19 everything a reader could want was in the database: hashed artefacts,
scored publication dates, located excerpts, verifier verdicts, resolution rules. None of it
was reachable without SQL.

That is not a small gap. The claim this platform makes is that every number is traceable,
and a guarantee nobody can exercise is indistinguishable from one that does not hold. Phase
2's stated user-visible outcome is the pair of surfaces that close it: what a run gathered,
and what each sentence rests on.

## Decision

### One read model, two interfaces

`aer/services/provenance.py` assembles the views; the JSON API and the server-rendered pages
both call it. Two assemblies would eventually become two accounts of the same evidence, and
the one that drifted would be the one nobody was reading.

It decides nothing. Admissibility, verification and quarantine were settled by code that
owns those rules, and this presents what they concluded.

### Nothing is filtered out, and that is the whole design

A sources table showing only admissible sources answers "what did we rely on?" while making
"what did we reject, and why?" unanswerable — and the second question is what tells a reader
whether to believe the first. So a quarantined source appears with its reason, an injection
flag appears with its passage count, and an unverifiable citation appears with its excerpt,
its ratio and the verifier's error.

Showing a failed excerpt matters more than it first looks. A ratio of 0.94 and a ratio of
0.02 are a reflowed paragraph and a fabrication, and only the words themselves let a reader
tell which they are looking at.

### Three citation states, not two

`verified`, `overridden`, `unverified`. Folding `overridden` into either neighbour would
lose exactly the distinction a research report needs to make: code confirmed this, versus
code could not and a person accepted it anyway. The same reasoning gives the sources table
two counts — `quarantined` and `inadmissible` — because one number would hide the difference
between "nothing was doubtful" and "everything doubtful was waved through".

### Ownership is checked in the route, mirrored rather than shared

The route knows who is asking. The service takes an id it has been told the caller may see.
The JSON API and the page each run the same one-query join, separately, because they differ
in what they do about a failure — one raises, one renders a problem page — and sharing the
check would mean sharing the failure mode too. Both answer 404 for "not yours", as every
other route does: distinguishing it from "does not exist" lets a caller enumerate ids.

### The excerpt is printed as text, always

It came out of a document nobody vetted. Rendering it as markup would run a filing's
contents as script on a page that can reach the database — the same class of failure the
injection work in task 13 exists to contain, arriving through the viewer instead. It goes in
a `pre`, escaped, and a test asserts a planted `<script>` arrives as characters.

### The pages carry no script of their own

Not a preference. These are the pages a reader opens *because* they doubt a number, and a
table that arrives by `fetch` is a table that is blank when anything is wrong. Both pages
render complete from the server; the tests assert there is no `<script>` after `</head>` and
a browser test loads them with scripting disabled entirely.

## Consequences

**The two-click criterion is met and is tested by walking links.** Report → claims → claim
detail, and report → sources as the same journey approached from the document end. The
in-process test follows `href` attributes actually present in the previous page's HTML, so a
renamed link fails it; the browser test clicks what a reader can see, because a link can be
present in the markup and unreachable on the page.

**The claim index is empty for a vertical-slice run, and the page says so rather than
looking broken.** The slice drafts sections whose figures carry `source_document_id` and
`calculation_id` — which the renderer turns into footnotes — but it does not decompose prose
into per-sentence `claims` rows. That is the section-writer agent's job in Phase 3, and
inventing a claim per section here would have produced rows whose text nobody asserted.

The surfaces are therefore exercised against evidence built through the real services and
verified by the real verifier, not against a live run's output. That is stated plainly here
rather than left for someone to discover: **the machinery is proven; the vertical slice does
not yet feed it.**

**Claim order within a section is stable but not meaningful.** `created_at` comes from
Postgres `now()`, which is the transaction start time, so every claim written in one
transaction shares it and the order falls through to the primary key. That is stable across
page loads — the same list every time — but it is not the order the author intended, and it
will need an explicit ordinal when a section writer emits claims in sequence. Recorded here
because the symptom, if it is forgotten, is a claim index that reads in an arbitrary order
and looks like a rendering bug.

## Alternatives considered

**Render the report's Markdown to HTML and link footnote markers directly.** Attractive: one
click instead of two. Rejected — the report page deliberately shows the approved Markdown as
text rather than re-rendering it, because a re-render can differ from what was approved and
the difference would be invisible. Turning markers into links means rendering, and the
integrity of the approved document is worth more than a click.

**Put the excerpts on the sources page instead of behind claims.** Rejected as the primary
path, and it is available anyway: the sources table reports each document's excerpt count.
But a reader arrives doubting *a sentence*, not a document, and making them find the sentence
in a filing is the work the claim page exists to remove.

**Show only what the report actually used.** Rejected for the reason the whole ADR turns on.
A viewer that quietly omitted the failures would make the evidence chain decorative.
