# 21. Look-ahead is checked twice, on the latest date

- **Status:** Accepted
- **Date:** 2026-07-30

## Context

Threat T13 is look-ahead bias, and it is the quietest failure in the platform. A report citing a
document published after its own as-of date reads exactly like one that does not. There is no
malformed output, no failed assertion, no odd-looking number — just a conclusion that was
available to nobody at the time it is dated.

Before this task the defence had a hole in it. `decide_quarantine` refused a source whose
publication date could not be *established*, and refused a tier that may never be cited, but it
**never compared the publication date against the as-of date at all**. The SEC adapter filtered
its own filing index by date, so filings from that one path were safe; every PDF, every issuer
page and every hand-supplied document was admissible whatever its date. The rule most of the
architecture is named for was enforced in one adapter rather than in the platform.

## Decision

### The date is extracted and scored, never trusted

`aer/extract/dates.py` collects candidates from four kinds of evidence, keeps all of them, and
records which won. A confidence with no explanation is a number a reviewer cannot act on; "the
index said 28 July, the PDF's metadata said 3 August, and the index won because it is the
regulator's record" is something they can go and check. Both the winner and the losers are stored
on the source document for that reason.

**The order of trust is not the one the plan wrote down.** `docs/phase-2-plan.md` says *"HTTP
headers, document metadata, filing indexes and in-document text, in that order of trust"*. Read
literally that puts HTTP headers first, which is wrong: `Last-Modified` describes a file on a
server, and a CDN re-upload or a site migration moves it years after publication. The order used
is filing index, then document metadata, then in-document text, then HTTP headers — most
authoritative to least. Headers are kept rather than dropped, because one document in the corpus
is datable from nothing else, and scoring them low costs less than losing them.

### Admissibility is decided on the **latest** candidate, not the best estimate

Two questions look like one and are not:

- *When was this published?* → the highest-trust candidate. Stored as `publication_date`, shown
  on the page.
- *Can this be shown to predate the as-of date?* → the newest date **any** evidence supports.
  Stored as `publication_date_latest`, and what the rule reads.

Where a filing index says July and the document's own text says September, the honest answer to
the second question is no. Judged on the estimate that document is admitted; judged on the bound
it is refused. The bound is the whole point.

The asymmetry is deliberate and worth naming. Being wrong in this direction costs a quarantine an
operator can lift with a written reason. Being wrong the other way costs a report that used
information nobody had, and that says nothing about having done so.

### The check runs at acquisition and again at claim time

This looks redundant until you notice the two moments know different things.

**Acquisition** sees the document, its headers and its metadata, and can refuse it before it is
ever extracted. It cannot know what a claim will later rest on, and it cannot see an as-of date
that changes afterwards.

**Claim time** — in `aer.verify.citations`, alongside the excerpt check — sees the request as it
now stands. A source fetched under one as-of date and cited after an operator moved that date
earlier passes the first check and fails the second. `tests/test_lookahead.py` has that exact
case, and it is the one that justifies the duplication.

The claim-time check runs *before* the artefact is re-read: a source nobody may cite fails
whatever it says, and re-parsing a filing to confirm a quote that cannot be used is work done to
reach an answer already decided.

### An override never clears the quarantine

A quarantined source is usable only after a **recorded** override — a person, a reason and a
time, with check constraints so it cannot be two of the three. The flag stays set. The row goes on
saying the document could not be dated *and* says who decided to use it anyway.

Clearing the flag would erase the first half, and a reader of the finished report would have no
way to know a judgement had been made at all. Same shape, and the same reasoning, as the citation
override in ADR 0018.

### Tiering is a table, not a judgement

`aer/sources/tiering.py` maps `(provider, document kind)` to a tier. Keyed on the pair because one
publisher spans tiers: an issuer's annual report is tier 2, a marketing post on the same domain is
tier 5, and the same figures inside a regulatory filing are tier 1 — the tier records what
compels the publisher to be accurate, not who typed it.

**An unrecognised pair resolves to tier 6, which is not citable.** The least favourable tier that
fits, never the most. A new adapter that forgets to declare its kinds produces sources nobody can
build a report on, loudly, rather than sources quietly carrying more weight than they earned.

## The corpus

`tests/lookahead_fixtures.py` holds five documents planted after the as-of date, each datable only
through a different mechanism — a filing index, an HTML meta tag, a PDF creation date, a date
printed in prose, and one visible only in an HTTP header. All five are detected and refused. A
detector reading only one kind of evidence would score perfectly against a corpus that used only
that kind, which is why they differ.

**The admissible half is what keeps the rule honest.** A system that quarantined everything would
hit 100% recall and refuse the filings a report is made of. It includes the boundary: published
*on* the as-of date is admissible, and an off-by-one there rejects a quarter's worth of real
filings.

An all-numeric `03/04/2022` is deliberately **not** parsed. It is 3 April to a UK filing and
4 March to a US one, and this platform reads both. A date that could be either is not evidence,
and guessing would put a silent one-month error into precisely the check that exists to catch
silent errors.

## Consequences

**Good.**

- The look-ahead rule is enforced in the platform rather than in one adapter.
- A reviewer sees why a date was believed, not just how much.
- The two checks fail independently, so neither can be the single point that was quietly removed.

**Costs, accepted.**

- A disputed document is quarantined even when its best estimate is fine. That is the asymmetry
  argued above, and the override is the release valve.
- Two date columns, which is one more than it looks like it needs until you read what each is for.
- The confidence numbers are judgement calls. They are named constants in one place, and what
  they actually drive is a display and a sort order — the refusal is driven by the date.

**Deliberately not built.**

- **A model-based date extractor.** It would be better at unusual layouts and would put a
  language model in the path of a control. The heuristics' failure mode is "no date", which
  quarantines; a model's failure mode is a confident wrong date, which does not.
- **Automatic override for high-confidence sources.** An override that a machine can grant is not
  an override.
