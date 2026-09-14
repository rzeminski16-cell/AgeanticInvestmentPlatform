# ADR 0119 — A printed excerpt re-enters as data, and the prior-research type stays narrow

**Status.** Proposed — V1.0_Alpha. Accepted when the change it argues lands.
**Date.** 2026-09-14
**Extends.** ADR 0064 (prior research may shape the questions, never the answers) and ADR 0034
(a withheld figure is a type with no field for it), whose structural argument this reuses.
**Upholds.** Invariant 8 — untrusted content is data, never instruction — which this ADR does
not relax and exists to keep true under a change that would otherwise quietly weaken it.
**Required by.** `docs/V1.0_Alpha/04-feature-specifications.md` F16, which must be decided
before F8 prints a verified excerpt into an exported document.

## Context

The platform holds 259 verified excerpts across the stored runs — passages of a filing,
re-read by hash to confirm each citation actually appears in the document it names. **None is
printed in the exported report.** A reader following a footnote gets a document reference and
a hash, not the sentence. The judges scored the exported file, and this is one of the places
it lost: a claim with a hash behind it is checkable in principle and unreadable in practice.

F8 prints them. That is right, and it opens a door that has been closed by accident rather
than by decision.

**The route.** A report's conclusions feed the next run on the same company through
`_prior_digests`, which builds a `PriorResearch` for the planner (ADR 0064). Today that type
carries a report id, an as-of date, a rating, a confidence, a valuation range, named risks and
catalyst lines. It has **no field for prose from a filing**, so no fetched byte has ever
reached a planner prompt through it — not because a check refuses one, but because the type
has nowhere to put it. That is ADR 0034's shape, and it is the reason invariant 8 has held on
this path without anybody defending it.

Print excerpts into reports and two things change. A prior report now contains verbatim
third-party text; and `named_risks` and `catalyst_lines` — free-text fields written by a model
that has just been reading quoted excerpts — may carry fragments of it forward. The route from
fetched bytes to a future prompt opens by inches, in fields that were safe when the documents
they summarised contained only the platform's own prose.

The refresh (F4) makes it worse before it makes it better: it reads a prior report to decide
what to re-draft, and the obvious cheap implementation reads the prose.

## Decision

**Three rules. The first is the one that matters; the other two keep it from being needed.**

### 1. Any free text that reaches a prompt from a stored report is wrapped as untrusted

`wrap_untrusted` already exists and already neutralises its own closing delimiter. It is
applied to every free-text field of `PriorResearch` — `named_risks` and `catalyst_lines` today,
and anything added later — regardless of the fact that a model wrote them.

The argument for wrapping the platform's own prose is that **the platform cannot prove it is
its own**. Once a section may quote a filing, a named risk is a sentence written by a model
that was reading quoted third-party text, and no check distinguishes "the model's summary" from
"the model's summary containing a quoted clause". Wrapping costs a few tokens and removes the
distinction from the set of things that must be got right.

This is a mitigation and is named as one. `aer.agents.untrusted`'s own docstring says so:
the controls are that no agent has a network tool and that `Agent.allowed_tools` is checked in
Python before any tool runs. Those are unchanged and remain the defence. Wrapping makes the
boundary legible; it does not make it real.

### 2. The prior-research type stays narrow, and gains no field for an excerpt

`PriorResearch` keeps its seven fields. It does not gain `excerpts`, `evidence`,
`supporting_quotes` or a prose summary, in this change or in the refresh's change-summary work.
If a later feature needs a prior report's evidence, it reads the **citation rows** — which
resolve to the artefact by hash and carry their own source, tier and licence — rather than the
rendered text.

This is the load-bearing rule, because it is the one a future change will want to break. A
report is a document; reading it as a document is always the obvious implementation; and the
type having no field for it is the only thing that stops the obvious implementation.

### 3. The refresh compares records, never prose

F4's *"re-draft only what moved"* is decided by comparing **facts and calculations** between
the prior run and the new one — figures with ids, units and sources — and the change summary is
composed from those figures. The prior report's prose is read by exactly one thing: the
renderer that keeps an unchanged section's text. It never reaches a prompt.

### What gates printing in the first place

An excerpt prints into an exported document only when all three hold:

- **The licence permits verbatim reproduction.** A licensed provider's payload does not print;
  it is purgeable under ADR 0030 and ADR 0031, and a purge that leaves the bytes reproduced in
  every exported PDF is not a purge.
- **The source carries no injection signal.** The scanner's finding suppresses the excerpt
  from the export, and the claim keeps its hash reference. A badge the reviewer sees is not a
  reason to print the payload.
- **It is an excerpt.** Bounded by the same cap the citation record already enforces — a
  passage, not a page. That bound is a copyright posture and an injection-surface bound at the
  same time, which is a coincidence worth taking.

## What is given up, named rather than discovered

**A reader of the exported file may see a hash where they wanted a sentence.** For a licensed
source or a flagged one, the excerpt does not print, and the footnote is the reference it is
today. That is a worse document in exactly the cases where printing would be worst.

**Wrapping the platform's own prose reads as paranoid**, and somebody will eventually propose
removing it on the grounds that a named risk is obviously the platform's own words. This ADR is
the answer: it stopped being obvious the moment excerpts printed.

**The refresh cannot use the cheapest implementation.** Comparing records rather than prose is
more work than diffing two documents, and it is what keeps the boundary closed.

## Consequences

**The acceptance test is behavioural, not structural.** An excerpt containing an
instruction-shaped sentence is printed into a report, that report is fed to a later run's
planner through the prior-research path, and **the later run's plan is byte-identical to the
plan produced without it**. Not "the wrapper is present" — the outcome is unchanged. The
corpus in `tests/injection_fixtures.py` supplies the payloads, and its clean half supplies the
control.

**The export gains a visible provenance line per excerpt** — source, date retrieved, hash
prefix — because an excerpt printed without its origin is the same unverifiable quotation the
console produces, which is the thing this platform sells against.

**Nothing about tool authorisation changes**, and that is the point. If this ADR were the
defence rather than a mitigation, the platform would be relying on a model to notice a trick.

## Alternatives considered

**Do not print excerpts.** Closes the route completely and keeps the loss the judges named.
Rejected on evidence: the excerpt is the difference between a checkable claim and a checked
one, and the risk is bounded by rules that cost very little.

**Print excerpts and forbid prior research entirely.** Also closes the route, at the cost of
ADR 0064's whole benefit — a second run on a company that cannot see what the first concluded
repeats its questions and pays again for the answers.

**Strip quotations out of prose before it re-enters.** A regex over free text looking for
quoted fragments, removing what it finds. It fails open on anything phrased without quotation
marks, which is most paraphrase, and it makes the safety of the system depend on a heuristic
rather than on a type with no field in it.

**Detect injection at the point of re-entry rather than at acquisition.** The scanner already
runs at acquisition, where the document arrives and where its signal is recorded against the
source. Running it again on prose derived from a document it already passed adds a second
chance to be wrong and no new information.
