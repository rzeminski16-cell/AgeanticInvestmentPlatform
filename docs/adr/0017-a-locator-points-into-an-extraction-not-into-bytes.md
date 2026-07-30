# 17. A locator points into an extraction, not into bytes

- **Status:** Accepted
- **Date:** 2026-07-30

## Context

Phase 2's central control is deterministic citation verification: code re-reads the archived
artefact and confirms the excerpt actually appears where the citation says it does. The plan
describes the locator that makes this possible as a position in the archived document, and the
task's own acceptance criterion says "slice the archived bytes at the recorded locator and get
the excerpt back".

**That is not obtainable, and would be the wrong thing even if it were.**

Neither `selectolax` nor `lxml` exposes source positions for text nodes. The markup offset of a
sentence is simply not recoverable from either parser's output. Nor is it worth recovering: a
byte range in HTML spans tags, entities and attribute noise, so what a reviewer would be shown
when they clicked through to check a citation is `<td class="num">198,270</td>` rather than the
sentence they are being asked to verify.

A decision was therefore needed before any of it was built, because everything in tasks 12
through 21 stores locators and none of it can be changed cheaply afterwards.

## Decision

### The verification contract

A locator addresses **the text a named extractor at a named version produces from an artefact**,
and records where in that text the excerpt begins and ends. So:

```
artefact SHA-256 + extractor + extractor version + locator  →  exactly one excerpt
```

Every part is load-bearing. The hash fixes the input; the extractor and its version fix the
function; the locator fixes the slice. Change any one and the excerpt may legitimately differ,
which is why all four are recorded on every extraction rather than being assumed.

### What this demands in return

**Extraction must be deterministic.** The contract is worthless if the same bytes can produce
different text on two runs, so it is asserted directly — same input, same text, same content
hash — rather than trusted. An extractor whose output varied would make every citation resting
on it unverifiable with nothing failing loudly, which is the worst available failure mode.

**The extractor version must be bumped whenever output could change.** A silent change to the
separator, the strip behaviour or the removal list shifts every stored locator by some number of
characters. With the version bumped, the verifier reports "the extractor changed", which is
true and actionable. Without it, the verifier reports "this excerpt is wrong", which is not.

**The whole extracted text is hashed, and stored on each extraction.** That is what distinguishes
the two cases above. It is `content_hash`, and it is the hash of the *document's* text, not of
the excerpt — a distinction the column name does not carry on its own, so the model says so.

### What is not stored

The extracted text itself. It is a deterministic function of three things that *are* stored, so
keeping a copy would double the disk for something regenerable — and worse, would create a
second source of truth that could drift from the artefact it came from.

### Consequences for what an extraction is

A row is one located excerpt, keyed uniquely on
`(source_document, extractor, extractor_version, locator)`. Re-extracting a span is not a second
piece of evidence: a resumed run re-extracts documents it already extracted, and without the key
each attempt would add another copy of the same sentence, inflating every "sources consulted"
count and putting duplicate footnotes in reports.

Uniqueness is over a hash of the canonical locator rather than over the JSONB itself. A unique
constraint on JSON fields needs an expression index per field and would have to be rewritten
each time a locator kind gains a coordinate — which the PDF page-and-bounding-box locator does
immediately. The hash excludes null fields, so adding an optional coordinate does not silently
re-key every row that never had one.

## The isolation decision, and its one honest gap

Parsing runs in a child process with a wall-clock timeout and, where the platform allows it, an
address-space cap.

**Why a subprocess despite the cost.** Parsing is the one operation where untrusted bytes drive
a large third-party parser — `lexbor` for HTML, `pdfminer.six` and `pypdfium2` for PDF (ADR 0020).
A segfault in a C extension is not an exception a caller can catch, and nor is an unbounded loop in
a pure-Python parser; in-process it takes the worker down, and with it the run. Isolation is what
makes "the extract step failed" a recoverable outcome. The cost is a process spawn per document,
around a hundred milliseconds on Windows, against a filing that parses in seconds and a model
call that takes a minute.

**The gap: `resource.setrlimit` is POSIX-only.** Windows has no equivalent without a Job Object
and a native extension, so on the platform this runs on for local use the memory cap is *not*
applied. The child reports whether it was, rather than letting the absence pass unnoticed.

That gap is narrower than it sounds, because the memory cap was never the primary control for
the attacks it appears to address:

| Attack | What actually stops it |
|---|---|
| Decompression bomb | The size ceiling, checked **before** the bytes reach a parser |
| Billion laughs | `resolve_entities=False` — nothing is expanded, so there is nothing to cap |
| Pathological small input | The wall-clock timeout, which is enforced on every platform |

The cap is a backstop for the unanticipated case. Its absence on one platform is recorded here
rather than discovered later.

## Hostile-document tests are differentials

Each hardening test runs its payload through the hardened parser **and** through an unhardened
one, and asserts that the unhardened parser discloses something real — that the XXE payload
returns actual content from the host filesystem, that the billion-laughs entity expands past a
thousand characters.

Without that second half, a test asserting "the hardened parser returned empty" passes just as
happily against a parser that returns empty for everything, and goes on passing after somebody
removes the setting doing the work. The differential is what makes the test about the control
rather than about the payload.

Every control in this task was checked by removing it and confirming a named test fails:
entity resolution, the size ceiling, the media-type check, hidden-text retention, insert
idempotency, and the error class surviving the process boundary.

## Consequences

**Good.**

- Task 12's verifier has an unambiguous contract to implement, and one that can fail with a
  useful diagnosis rather than a bare mismatch.
- What a reviewer is shown when they click a citation is a sentence, not a fragment of markup.
- Adding an extractor is adding a function and a name; the sandbox, the sniffing and the
  ceilings are already there and no call site changes.

**Costs, accepted.**

- An extractor change invalidates existing locators. That is inherent — the text moved — and the
  version and content hash make it legible instead of silent. Re-extraction is the remedy and it
  is cheap, because the artefacts never go away.
- The archived text is regenerated on every verification rather than read from a column. It is a
  parse of a document already on local disk, inside a run that makes model calls.

**Deliberately not built.**

- **Byte-offset locators for formats that could support them.** iXBRL nodes do carry source
  positions. Having two kinds of locator that mean different things is a worse problem than
  re-parsing, and the uniform contract is what lets one verifier serve every extractor.
- **Storing extracted text as its own artefact.** Tempting for speed, and it would create a
  second copy that can drift from the document it describes. Revisit only with a measurement
  showing re-extraction actually costs something.
