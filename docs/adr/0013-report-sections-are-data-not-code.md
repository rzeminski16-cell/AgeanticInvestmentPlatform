# 13. Report sections are rows, not code

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

A research report is a sequence of sections: an executive summary, a financial analysis, a
valuation, a risk register. The obvious implementation is a module per section, or an enum
of section keys with a renderer that branches on it. Almost every reporting system is built
that way.

It cannot work here, for a reason that is structural rather than aesthetic.

Phase 4 of this platform lets a user author their own sections in a natural-language skill
file — "analyse the durability of this company's moat, and here is what I mean by that".
A section defined that way has **nobody to write its template**. If rendering requires a
per-section template, or ordering requires a per-section entry in a list, or drafting
requires a per-section branch, then a user-authored section is not a thing the system can
produce. The feature is not hard to add later; it is impossible to add later, because the
architecture would have to be taken apart first.

There is a second reason, smaller but real. A report that has already been approved must
not change because a section definition was edited afterwards. Sections therefore have to
be *versioned*, and a version is a row.

## Decision

### A section is a row in `section_definitions`

```
key           executive_summary
version       1
title         Executive Summary
position      100          -- NUMERIC, sparse
output_contract  { JSON Schema }
evidence_policy  { min_sources, requires_primary, ... }
applicability    { exchange: ["LSE"] }   -- empty means always
```

Adding a section — built-in or user-authored — is an `INSERT`. Nothing else.

`position` is `NUMERIC` and sparsely allocated (100, 200), so a section slots in at 150
without renumbering anything.

### No section key appears in code

Not in an enum, not in a list, not in a branch. The workflow's draft step iterates
`report_sections`; the Markdown renderer iterates `report_sections`; neither knows what a
section is called.

This is enforced by a test that parses every module under `src/`, strips comments and
docstrings, and fails if a seeded key appears in the remaining code. The test has a
self-check in both directions: a hardcoded key in a fixture file is detected, and a key
mentioned only in a comment is not.

### The renderer walks the contract

`aer.sections.render.render_section` produces Markdown from the section's `output_contract`
— a JSON Schema — with four rules:

* an **object** renders its properties in declared order, each under its `title`;
* an **array of strings** renders as a bullet list;
* an **array of objects** renders as a table when the objects share a shape, and as
  sub-sections when they do not;
* a **string** renders as a paragraph.

Field order and headings come from the contract, so a section author controls the shape of
their output by writing a schema rather than a template. A table's columns come from the
contract's `items.properties` too: the values decide *whether* a table is appropriate, the
author decides what order its columns go in.

### The contract is stored as `json`, not `jsonb`

Everything else in this schema that holds a document uses `jsonb`. This column does not,
and the exception is load-bearing.

`jsonb` normalises. It discards key order, reordering by key length and then bytewise, and
it does so silently. A contract declaring `thesis, key_points, key_risks` came back as
`thesis, key_risks, key_points`, and a figures table declaring `label, value, unit`
rendered its columns as **Unit, Label, Value** — 4, 5, 5 by length. The author's deliberate
ordering had been replaced by an implementation detail of the storage engine, in the one
document whose entire purpose is to let an author control their own output.

`json` stores the text exactly as written. Nothing queries inside this column, so the
indexing `jsonb` buys is worth nothing here. Migration 0007 makes the change and rewrites
the two seeded contracts; the check constraint moves from `jsonb_typeof` to `json_typeof`
with it.

This was found by rendering a real report and reading it, not by a test. The tests now
cover it in both directions — a contract written and read back, and the rendered headings.

### Citation is a key name, not a position

Any object carrying `source_document_id` or `calculation_id` is a *cited item*: it renders
normally and gains a footnote marker. That is the only coupling between content and
provenance, and it is a **field name**, so a section author gets citations by naming a
field rather than by knowing where the renderer looks.

Footnotes are numbered across the whole document in marker order, not per section. A reader
chasing `[^3]` finds the third marker in the report.

### A run pins the version it started with

`report_sections` copies `section_definition_id` **and** `position` from the definition at
the moment the run starts. A definition published or repositioned later cannot retroactively
reorder or alter a report that has already been rendered.

### The proof is a test, not a claim

`tests/test_report_sections.py::TestAThirdSection` inserts one `section_definitions` row at
position 150 and asserts that the rendered report gains a third section, between the two
built-in ones, with its contract supplying the sub-headings and footnote numbering staying
correct across the document — **with no change to any Python file**.

Deliberately breaking the property (filtering the renderer to the two known keys) fails
five of those tests. If that test ever has to be weakened, the Phase 4 feature is no longer
implementable and the architecture needs revisiting rather than patching.

## Consequences

**Adding a section is an INSERT.** For the platform's own sections as much as for a user's,
which is what keeps the two paths from diverging — there is only one path.

**A section cannot contain arbitrary logic.** Its output is a JSON Schema and its
applicability is a mapping from request attribute to permitted values. That predicate
language is deliberately tiny: anything richer would be a query language embedded in a
JSONB column, and the first thing anyone would want is a negation, the second an `or`, and
by then it is a language nobody can test.

**Built-in sections may register an override template later.** The generic path stays the
default and is the one the tests exercise — an override never compared against the generic
output would be an override nobody could tell was necessary.

**The renderer must handle content it has never seen.** It dispatches on the *value* rather
than the declared type, because a section whose content disagrees with its own schema is a
validation problem, and rendering is not the place to discover it.

**An unknown applicability attribute excludes the section rather than including it.** A
predicate nobody can evaluate is one whose author expected it to do something; silently
ignoring it would include a section they meant to exclude.
