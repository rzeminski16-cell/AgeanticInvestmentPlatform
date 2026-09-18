# ADR 0119 — A printed excerpt re-enters as data, and the prior-research type stays narrow

**Status.** **Accepted, 2026-09-18.** See *What landed* at the foot.
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

## What landed, 2026-09-18

**The three gates are one function.** `aer.services.extractions.may_print_excerpt` applies all
three together, and `printable_excerpts` is the only way a passage reaches a document. There is
no argument that switches one off, so nothing that renders can apply two of them and forget the
third — the shape ADR 0126 gave acquisition, for the same reason.

**The licence question is answered where the licence note is.** `FetchPolicy` gains
`verbatim_excerpt_publishable`, closed by default for the reason `derived_figures_publishable`
is closed: silence is not permission, so a feed added tomorrow quotes nothing until somebody
reads its terms. Reading today's nine notes answers it nine times — *not subject to
copyright*, *Open Government Licence*, *quoted for research with attribution*, *short quotation
only* — and leaves exactly one closed, EODHD, whose terms prohibit displaying the information
in original or repackaged form and whose bytes carry a deletion obligation. A test holds every
`RetentionClass.LICENSED` provider closed, so a second paid feed cannot be opened by
inattention, and a second names the open set so opening one has to be written down twice.

`MAX_EXCERPT_CHARS` moved to the module that owns the rows it bounds. Acquisition still cuts to
it and now imports it; the third gate re-reads it, because a row recorded by some other path
was never cut by the cutting that did not run.

**Measured on the stored corpus, and the measurement changed the design.** Three of the
audit's exported reports were counted — Microsoft's second run, AstraZeneca's second and
M&T's. They carry 22 to 37 source markers each, resolving to **four or five distinct
documents**, so a passage printed at every marker would be the same four paragraphs printed
seven to nine times. It prints **once per document, at that document's first marker**, and the
document's later markers say which note carries it.

Against the four runs in the database every document with a verified citation passes all three
gates: 5, 2, 5 and 3 documents, from 41, 14, 59 and 75 verified citations. The passage that
prints is the one the run leaned on most rather than an arbitrary row — the most-leaned-on
passage in each of the four carries 4, 8, 6 and 12 claims — and one per document costs 1.5 to
7.7 KB on a report of about 100 KB.

**Verified, never merely admissible.** A citation a person overrode is one the verifier could
not find in the document it names. It keeps its footnote and its hash and earns no quotation
marks, which is invariant 2 read out loud: the model may propose a citation, only code may
confirm one, and only a confirmed one is quoted.

**The provenance line is the retrieval date and the artefact digest**, beside a footnote that
already names the source, its publisher, its publication date and its tier. The digest was in
the appendix and is now also where the passage is, because that is where a reader is asked to
believe a sentence came out of a filing.

**A check must not fire on text the platform may not edit**, and this ADR turned out to need
that said. `presentation_integrity` counts the rendered document's defects — a bare five-digit
integer, a raw identifier, a stray asterisk run — and a quoted passage is a filing's own
typography, reproduced exactly or it is not the excerpt the verifier confirmed. A filer who
writes 198270 without a separator would otherwise fail a check about *this* document's
presentation, and the only ways out would be editing the excerpt or not printing it. So the
scan skips the passage, the same carve-out it already makes for code spans and for gap R9's
quoting of its own findings. The Markdown puts the quotation last on its line for exactly this
reason: a filing's text may contain quotation marks, so nothing inside the passage can be
trusted to say where it ends, and the attribution in front of it is what keeps a section
writing the words "verified passage" from opening an exemption.

**The acceptance test is behavioural, as this ADR required.** `tests/test_printed_excerpts.py`
runs the planner's own composition against a model with no judgement at all — it obeys any
instruction it meets outside an `<untrusted_source>` block — and a poisoned prior and a clean
one produce the same plan byte for byte. Two controls stop that being vacuous: the same model
handed the same sentence unquoted does comply, and the sentence is asserted to be in the
composed turn and absent from what a credulous reader takes as addressed to it. The payload is
`tests/injection_fixtures.py`'s own.

**The golden document carries all three states** — printed with its provenance, pointed at from
the same document's second marker, and withheld on a flagged source — so a serialiser that
drifted on any of them fails on bytes rather than on a claim.

**What this did not need.** Rules 1 and 2 were already true: `PlannerAgent.untrusted_sources`
declares the digests so the base does the wrapping, and `PriorResearch` had seven fields and no
excerpt. They are now *held*: the seven are pinned, pinned equal to `PriorDigest`'s seven, and
`extra="forbid"` is shown refusing an `excerpts` field rather than dropping it. Rule 3 has no
code yet because the refresh does not exist; what is pinned is that `aer.services.history` holds
no name for an extraction or a citation, so the cheap implementation cannot be written without
first importing the thing this ADR forbids.
