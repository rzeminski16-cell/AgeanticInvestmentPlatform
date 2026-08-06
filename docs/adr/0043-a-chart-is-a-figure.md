# 0043 — A chart is a figure

Date: 2026-08-06. Status: accepted. Task 47.

## Context

The chart pack turns recorded rows into geometry: revenue bars, margin lines, scenario
bars, a sensitivity heatmap, valuation range bands, a price line. Two of this platform's
standing rules meet in it and both are easy to lose in pixels.

First, the licence discipline. ADR 0030 (route 2) and ADR 0034 keep EODHD-derived figures
off every exportable surface, enforced by types: a shareable report takes a
`WithheldComps`, which has no field that could carry a multiple. But a *drawn* comps band
is the same licensed data — a set of multiples arriving as rectangle coordinates instead
of digits — and a price line is a price series whatever encoding it travels in. A rule
enforced for text and forgotten for images would not be a rule.

Second, provenance. Invariant 3 says no figure reaches a report unless it is a stored
fact or a recorded calculation. A bar of a bar chart is a figure a reader will quote; a
chart whose numbers cannot be walked back to rows would be the most persuasive
unsupported claim in the whole document.

Third, reproducibility. Task 48 hashes and archives the approved report's HTML with its
charts embedded. Matplotlib output varies by default — element ids salted from the
process, a creation date in the SVG, fonts resolved from the machine — and a report whose
bytes change on re-render cannot be tamper-evident.

## Decision

**A chart is a figure, and every figure rule applies to it.**

1. **The pack splits by licence, enforced by construction.** The exportable set —
   revenue and margin history, segment mix, scenario bridge, sensitivity heatmap, and a
   football field drawn only from our own calculations — may enter a report. The
   internal set — price/relative performance, and the football field variant carrying
   the comps band — is born with `exportable=False` set by its *builder*, not its
   caller; `assemble_document` refuses a non-exportable chart outright; and the
   exportable football field's input type has no parameter that could carry a comps
   band. Wiring the wrong chart to the wrong surface is a loud error, not a leak. The
   exportable field's caption carries the licence note where the comps band would have
   been, so the absence reads as a decision rather than an oversight.

2. **Every chart cites.** Each series point, cell, bar and band-end carries the
   `CitationRef` of the row it was read from — a calculation id or a source document id,
   the two kinds a text figure cites. The assembler numbers a chart's citations into the
   document's global footnote sequence, so an exhibit's marker resolves through the same
   notes section, verifier and drill-down as a sentence's. The service layer
   (`aer/services/exhibits.py`) only *reads* rows into inputs; a chart it could not
   source from the ledger is not drawn. Scenario attribution required one recorded
   parameter that did not exist: the DCF outcome calculations now carry `case` alongside
   `method` — the same precedent, one level up — so per-scenario valuations can be read
   back from the ledger instead of reconstructed positionally.

3. **Byte-stable by pinning, not by luck.** Builders are pure functions from typed
   `Decimal` inputs to SVG under a pinned rc set: bundled DejaVu fonts, fixed DPI and
   canvas, text kept as text, no date metadata, and `svg.hashsalt` from the job id so
   ids are stable across processes. Rendering the same rows twice yields identical
   bytes, held by test.

4. **Empty inputs render the honest placeholder** — a bordered note naming what was not
   recorded, never an empty axis that reads as data. A run that recorded nothing
   chartable at all carries no exhibits block, because six pictures of absence inform
   nobody; once any exhibit has data, the placeholders stay, because there a missing
   exhibit would read as a binding error. Segment mix is placeholder-only today by
   schema honesty: `financial_facts` cannot hold dimensioned segment values, so no
   pipeline records them, and the chart says so rather than estimating from prose.

5. **Charts embed as base64 SVG data URIs in an `img`.** The stored HTML stays one
   self-contained file, and an `img` cannot script even in a viewer that would let
   inline SVG try. The Markdown notation carries each exhibit's caption and markers with
   a note that the geometry lives in the HTML and PDF editions — the figures resolve in
   both notations; only the picture is deferred.

## Consequences

- `matplotlib` joins runtime dependencies; charts render in-process with the Agg
  backend and no pyplot global state.
- The valuation surface is the only page that renders the internal set, and it labels
  the charts as licensed, internal-only material.
- A future comps-implied value band on the internal field needs recorded
  comps-implied-value calculations first; nothing may compute one at chart time.
- Re-rendering an archived report reproduces its chart bytes exactly, which is what
  lets task 48 hash the HTML as the record of what was approved.
