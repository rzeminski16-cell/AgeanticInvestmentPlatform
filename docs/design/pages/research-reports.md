# Reports: the history and the finished document

*Where finished work lives. The smallest of the research surfaces and the one an operator
returns to longest after the run.*

---

## At a glance

| | |
|---|---|
| **URLs** | `/reports` · `/reports/{id}` · `/reports/{id}/preview` · `POST /reports/{id}/export-obsidian` |
| **Who arrives** | The operator, looking for something they produced — often months later |
| **From where** | The Reports nav item; a run's console after approval; a company's history page |
| **What they came for** | To read a report, to check what they concluded last time, or to export it |
| **Templates** | `reports/index.html` (32 raw ramps) · `reports/detail.html` (70) |

---

## The job

**Make a body of past work findable, and make each finished report readable as the document
it is rather than as a database row about a document.**

---

## `/reports` — the history

Every report this account has produced, **grouped by company, newest first**.

**Drafts appear, marked as such.** This is the account's own work list, so an unapproved draft
belongs on it. But the *history* surfaces — the company page, the API, the comparison section
inside a report — show approved reports only, because those are the history a decision could
rest on. The grouping here links through to them.

That distinction is worth preserving carefully: **"everything I have worked on" and "what this
platform will treat as established" are different sets**, and this is the one page that shows
both.

---

## `/reports/{id}` — a finished report

Four blocks:

| Block | Contents |
|---|---|
| **Sections** | What the report contains |
| **The report** | The document itself |
| **Obsidian vault** | The export form |
| **Important** | The disclaimer, in full, again |

Plus the report's **hash** and a link to the **archived bytes** — the frozen record of what
was approved.

### The Obsidian export

**Nothing exports automatically.** This form and the command line are the only two doors, and
the exporter re-checks every rule itself — approval status, containment, the reserved personal
tree — so the button decides nothing beyond ownership and the CSRF token.

### `/reports/{id}/preview` — the HTML notation

The report as a document: no navigation, no scripts, the print stylesheet included. Carries
the view and the date the report row recorded, **so the document says when it was produced
rather than when it was viewed**.

Re-assembled from the run's stored rows rather than replayed from an archive. The archived
Markdown remains the hashed record of what was approved.

> **The rendered PDF's own layout is out of scope for this brief** and has its own roadmap
> entry — the disagreement appendix and the at-a-glance tables both have known defects. What
> *is* in scope is this page as a reading surface in the browser.

---

## Inputs

| Control | Type | Notes |
|---|---|---|
| Export to Obsidian | Submit | CSRF-protected. Every rule re-checked by the exporter |

Nothing else. These are reading surfaces.

---

## States

| State | Notes |
|---|---|
| **Populated** | Grouped by company |
| **Empty** | No reports yet. Needs a proper empty state — this is a plausible first visit |
| **A draft** | Marked as such. Appears here, not in the history surfaces |
| **An approved report** | With its hash and archived bytes |
| **Export succeeded / refused** | The exporter can refuse, and the refusal has reasons worth showing |
| **Not yours** | 404, identically to "no such report" |

---

## What is wrong today

**The history is a list, not a history.** Grouped by company and sorted by date, with no sense
of what changed between two reports on the same company — which is the entire reason to group
them. Two reports on the same name six months apart is the most interesting thing this page
can show and it shows only that both exist.

**Nothing on the list says what a report concluded.** No rating, no valuation, no summary
line. Finding "the one where I decided it was expensive" means opening them.

**Nothing says what a report cost.** Recorded, absent here.

**The finished report page is four blocks with no hierarchy**, and "The report" — the actual
document — is the third of them, after a list of its own sections.

**The Obsidian export occupies a quarter of the page.** It is a useful feature used
occasionally, sitting at the same weight as the document.

**The hash and the archived bytes are presented as metadata.** They are the *proof that this
document is what was approved*, which is a stronger and more interesting claim than their
presentation suggests.

**The preview is a separate page with no way back.** Opening the document as a document leaves
the application, and returning means the browser's back button.

---

## What to improve

**1. Make the history a history.** Same company, two reports: what changed? Rating, valuation,
date, and what the later one says about the earlier. The comparison machinery exists inside
the report; the list does not use it.

**2. Put the conclusion on the list row.** Rating, valuation, as-of date, cost. Enough to find
a report without opening it.

**3. Lead the report page with the report.** Sections become navigation *within* it rather
than a preamble to it.

**4. Demote the export.** An action, not a section.

**5. Make the hash a statement.** "Approved on 14 March. This document is byte-for-byte what
was approved, and here is the proof."

**6. Give the preview a way home.** It is deliberately chrome-free as a document, which is
right — but a single unobtrusive return link is not chrome enough to break that.

**7. Design the empty state.** "No reports yet" is a likely first visit and currently says
nothing about how to get one.

---

## What must not change

**Drafts appear here and nowhere else.** The history surfaces show approved reports only.

**Nothing exports without an explicit request.**

**The preview carries the date the report was produced**, not the date it was viewed.

**The archived bytes remain the hashed record.** The preview is a reading surface; it is not
the record.

**The disclaimer appears on the report page in full.**

---

## Done when

- An operator can find a report from six months ago by what it concluded, not by its date.
- Two reports on one company visibly relate to each other.
- The document is the first thing on the page about the document.
- The hash reads as a guarantee rather than as metadata.
