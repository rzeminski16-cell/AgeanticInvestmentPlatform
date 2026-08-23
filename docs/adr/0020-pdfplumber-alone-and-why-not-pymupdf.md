# 20. `pdfplumber` alone, and why not PyMuPDF

- **Status:** Accepted
- **Date:** 2026-07-30
- **Supersedes:** the library choice in `docs/archive/PLAN.md` §1.4 and `docs/archive/phase-2-plan.md` task 14

## Context

Both plans specify **`pymupdf` for text with coordinates and `pdfplumber` for tables**. That is
the right answer on the technical merits: PyMuPDF is faster than anything else in Python, and its
`rawdict` output gives per-glyph geometry directly.

It is the wrong answer here, for a reason that has nothing to do with parsing.

**PyMuPDF is AGPL-3.0, or a commercial licence purchased from Artifex.** Checked rather than
recalled — the PyPI metadata reads *"Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial
License"*.

Two facts about this repository make that a problem rather than a footnote:

1. `pyproject.toml` declares the project **MIT**.
2. The platform is explicitly *"designed for later server deployment"*, and the operator has
   stated a requirement to hold the rights for **future commercial software use**.

AGPL §13 is the network clause: it is triggered by *interacting with users over a network*, which
is precisely what deploying this as a web application does. A copyleft runtime dependency inside
an MIT project intended for eventual commercial network deployment is a licence conflict that
gets more expensive the later it is found — by task 17 there would be iXBRL and Companies House
code sitting on top of it.

## Decision

**Use `pdfplumber` for both text-with-coordinates and tables. Do not add PyMuPDF.**

The whole resulting dependency tree is permissive, verified from installed metadata rather than
assumed:

| Package | Licence |
|---|---|
| `pdfplumber` | MIT |
| `pdfminer.six` | MIT |
| `pypdfium2` | BSD-3-Clause / Apache-2.0 |
| `pillow` | MIT-CMU |
| `cryptography` | Apache-2.0 OR BSD-3-Clause |
| `cffi` | MIT-0 |
| `pycparser` | BSD-3-Clause |

No copyleft anywhere.

### What is given up, and why it does not matter here

**Speed.** `pdfminer.six` is pure Python and is roughly an order of magnitude slower than MuPDF.
Against this platform's actual workload — about one report a week, a filing measured in seconds,
inside a subprocess that already has a wall-clock timeout, ahead of model calls measured in
minutes — it does not register. This is the rare case where the slower library is simply
affordable.

**Nothing else.** `pdfplumber` turned out to cover the whole requirement:

- `extract_words()` gives words with `x0`, `x1`, `top`, `bottom` and a page number.
- `find_tables()` gives table, row **and per-cell** bounding boxes, which is what makes the
  acceptance criterion — *every extracted number locatable to a page and box* — checkable at
  cell level rather than table level.
- `chars` carry `non_stroking_color` and `size`, so white-on-white and unreadably-small text are
  detectable in a PDF, which is what task 13's scanner needs from this format.

Using one library rather than two is a real simplification: one parse, one coordinate system, one
set of failure modes, and no question about which of two libraries a given locator came from.

### The text is built from word geometry

The extractor does its own line assembly rather than calling `extract_text()`. On an ordinary
filing the two produce the same string — that was tested, not assumed. The reason is where the
guarantee comes from: the text is built *out of* the spans, so a character offset and a rectangle
are two views of one list and cannot disagree. Calling `extract_text()` and separately asking for
word positions would rely on two code paths in a third-party library continuing to agree, which
is not a contract it offers, and the failure mode is a citation highlighting the wrong figure.
A highlighted wrong figure is more convincing than no highlight, which is what makes it worth the
extra code.

`WordExtractor.iter_extract_tuples` is used rather than `page.extract_words()`, because the
hidden-text checks need each word's glyphs and that is the one call yielding both. Asking
`extract_words` for the attributes via `extra_attrs` makes it split a word wherever an attribute
changes, and a rotated word's glyphs report a spread of sizes — which turned `Sideways` into
`S`, `i`, `ed`, `w`, `a`, `sy`. A test asserts the two calls group identically.

## Consequences

**Good.**

- The dependency tree is entirely permissive, so commercial deployment stays open.
- One PDF library instead of two.
- The page map is a pure, testable value object in `core`, and `PageMap.resolve` is checked
  against a linear scan by a property test.

**Costs, accepted.**

- Slower parsing, quantified above.
- No OCR, so a scanned filing is reported `unextractable`. Stated as the honest answer rather
  than papered over with empty text.

**A known defect, recorded rather than hidden.**

**Rotated text extracts in the wrong order.** `pdfplumber` orders characters along the page's x
axis, so a 90-degree heading comes back reversed: `elbat tnemges syawediS`. Wide tables in annual
reports are routinely printed sideways, so this is an ordinary document rather than an exotic one.

The text is still *extracted* — evidence first, the same rule as hidden text — and a test pins
the current output so that fixing it has to be deliberate. Fixing it means grouping by the text
matrix rather than by x, which changes the layout algorithm and therefore `pdf.VERSION`, and
would move every locator recorded before it. That is a task of its own, not a rider on this one.

It cannot produce a *wrong number*: digits reverse too, so a reversed figure does not match a
real one. What it can do is make a sideways table unreadable, and a reader of the extracted text
will see that immediately.

**Deliberately not built.**

- **OCR.** A non-goal for this task, per the plan.
- **An LLM vision pass for hard tables.** `docs/archive/PLAN.md` reserves this. Nothing here needs it
  yet, and it costs money per document.
- **Persisting the page map or the tables.** Both are regenerable from the artefact by the named
  extractor at the named version, which is the whole point of recording those. What *is* stored
  is the answer — the page and box on a locator — not the lookup table that produced it.
