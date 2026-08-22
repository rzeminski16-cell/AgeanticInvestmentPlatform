# ADR 0069 — A scheduled filing is a date, not a catalyst

**Status.** Accepted
**Date.** 2026-08-22
**Joins.** ADR 0063 (a claim about how a number was produced is a claim about a
calculation) as the second deterministic refusal over model prose. Decided while closing
report-quality R7.

## Context

The live CHRW note's catalyst section listed nothing but scheduled SEC filings. Every row
was a periodic report — the next 10-Q, the next 10-K, the annual proxy — and every date was
extrapolated from the previous two filings, with the rationale column saying so in as many
words: *"filed its Q1 2026 Form 10-Q on May 1, 2026 and its Q2 2026 Form 10-Q on July 31,
2026 … extrapolating this roughly three-month cadence."* The prose then spent its budget
explaining what a filing is.

Nothing in that section was false. The company will file a 10-Q, and it will file it around
then. It is useless anyway, and the uselessness is structural rather than a lapse: **an
event whose occurrence is certain and whose content is unknowable carries no information.**
What moves the shares is what the filing says, and that is exactly the part a catalyst list
cannot state in advance.

The contract asked for "what could move the shares towards or away from the view, and when",
and a writer with no disclosed dated event to name will fill that with the only dated things
in the evidence, which are filing dates. The instruction was not wrong; it was simply not
enforceable by asking.

## Decision

**The reporting calendar is refused as a catalyst list, in code, and an empty catalyst list
is a correct answer.**

`reporting_calendar_entries` (`aer.core.section_output`) refuses a listed event two ways,
either being enough:

* its **label** names a routine periodic report — 10-Q, 10-K, 20-F, 40-F, 6-K, DEF 14A,
  proxy statement, quarterly or annual or interim or full-year report or results, an
  earnings release, a trading statement;
* its **stated basis** dates it from the filing rhythm rather than from a disclosure —
  "extrapolating", "cadence", "filing pattern", "typically files".

The second exists because the first only catches the defect by name. A row called anything
at all, dated by "the company has filed in early February for six years", is a date nobody
disclosed, and that is the thing being refused.

The refusal is **corrective, not fatal**: it returns to the writer with the remedy named, in
the same retry loop as every other validation problem, and carries its own cause
(`calendar`) through `classify_refusals` into the placeholder a reader sees if the section
never recovers.

**The rule finds its rows by shape, never by the name of the section holding them.** It
scans any row anywhere in the content that declares an `expected_timing`. The first cut read
one named array instead and was rightly refused by `test_no_section_key_is_hardcoded` —
sections are rows in this platform, and a module that names one has made the next section a
code change. The discipline turned out to improve the rule rather than merely constrain it:
a section listing dated events under some other heading is making the same claims and now
gets the same check, with nothing in the core knowing that section exists.

**The contract says the same thing before the writer spends anything** (`catalysts` v2,
migration 0052): what qualifies, what does not, that an empty list is the correct answer
rather than a gap, and that two sentences is a complete answer to this section. A rule a
writer only meets after paying for a draft is a rule that costs a redraft every run.

## What the rule deliberately does not catch

This is the half that decides whether the change is an improvement or a closure, so it is
stated as a decision rather than left to the vocabulary:

* **A disclosed dated event** — an announced investor day, a statutory decision deadline, a
  transaction with an expected completion, a facility with a stated maturity, a disposal
  whose comparatives clear on a known date. Every one of these is what the section exists
  for, and each is pinned by a test that fails if the vocabulary ever reaches it.
* **An annual general meeting.** Routine until a vote is contested, and then it is
  genuinely the catalyst.
* **A Form 8-K.** It exists precisely because something happened, which is the opposite of
  the periodic filings this rule refuses.
* **Estimation in general.** "Expected to complete in the first half" is a real timing for
  a real event. The rhythm vocabulary is about *filing frequency* only, because a rule
  against estimated dates would refuse most genuine catalysts.
* **Commentary.** Only rows declaring a timing are scanned. A sentence explaining that the
  next 10-Q would have to disclose something is context, and refusing it would refuse the
  sentence that makes an empty list legible.

## Consequences

**A section with nothing to say may now say so and stop.** That is the intended outcome and
it will make some reports shorter. A two-sentence catalyst section stating that the evidence
dates no catalyst, and what would have to appear for one to exist, is worth more than a page
of filing dates.

**A false refusal costs a retry, not a section.** The vocabulary is closed and
word-bounded, so the failure mode is a writer being told to drop a row it should have kept,
with the retry free to rephrase. The reverse failure — a filing calendar published as
analysis — is the one that reached a reader.

**The vocabulary will need adding to.** It is a list of names, and names vary by market and
by filer. It is a named constant with its reasoning beside it precisely so that extending
it is an argument about the line rather than a guess at a regex.

## Alternatives considered

**Strengthen the prompt instead.** Rejected: this *was* the prompt-only state. The contract
already asked for catalysts and got a filing calendar, because the writer had nothing else
dated to offer and no signal that an empty list was permitted.

**Require a `source_document_id` on every catalyst.** Attractive — a disclosed event has a
disclosure — but the field is optional across the spine and making it mandatory here would
refuse a catalyst derived from several documents, or from a calculation, as firmly as it
would refuse a made-up one.

**Let the red team catch it.** It did catch the neighbouring finding (R8), which is why
that one is recorded. But an adversarial pass that has to re-derive the same objection every
run is a rule nobody wrote down, and it argues with the finished draft rather than
preventing it.
