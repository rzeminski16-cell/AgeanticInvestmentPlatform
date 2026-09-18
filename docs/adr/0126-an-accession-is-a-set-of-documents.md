# ADR 0126 — An accession is a set of documents

**Status.** **Accepted, 2026-09-18.** See *What landed* at the foot.
**Date.** 2026-09-18
**Required by.** `docs/V1.0_Alpha/04-feature-specifications.md` F7, and the readiness audit's
findings *the-8k-exhibit-is-never-fetched*, *acquire-fetches-only-primary-documents* and
*current-reports-chosen-by-date-not-by-what-happened*.
**Extends.** ADR 0059 (a peer is resolved, not acquired) in posture only: this is about what
the *subject's* own accessions contain.

## Context

A filing on EDGAR is not a document. It is an accession — a folder — and the submissions
index names exactly one file in it, `primaryDocument`. Everything this platform acquires is
that one file.

For a periodic report that is nearly right: a 10-K's primary document *is* the 10-K. For a
current report it is structurally wrong. **An 8-K's primary document is a cover page whose
entire content is a sentence saying the information is furnished as Exhibit 99.1.** The run
fetches the cover page, extracts its two paragraphs, and stops. Five current reports are
acquired per run and not one of their exhibits is.

So the earnings release, the guidance table and the recast segment history — the documents a
reader opens a research note *for* — are structurally absent from the evidence at tier 1. A
live report said so in as many words:

> *A current report dated September 2026 falls inside the point-in-time window, but its
> substance is not before us.*

and cited the cover page anyway. The console's note, judged against it, built its segment
table, its comparatives, its quarterly progression and its single most-cited claim from
`…/000119312526380280/d291965dex991.htm`. The platform fetched
`…/000119312526380280/d291965d8k.htm`. Same accession, same folder, same filing date, same
tier, same already-allowed host. One file apart.

### What the stored corpus shows, and it is worse than the diagnosis said

The research workers did not fail to notice. Three archive URLs in the store were fetched by
a worker guessing at paths, all recorded at tier 5 because they arrived through
`fetch_known_url` rather than through an index:

| URL a worker guessed | Bytes returned | Excerpts |
|---|---|---|
| `…/data/36270/000036270-26-000000` | 307 | 0 |
| `…/000003627026000010/mtb-20260918_8k.htm` | 326 | 0 |
| `…/data/789019/000119312526380280/` | **17,901** | **0** |

The first two are EDGAR's 404 page. The third is the **accession's own directory listing**,
for exactly the accession whose Exhibit 99.1 the platform lost on — seventeen kilobytes
naming every file in the folder, fetched, hashed, stored, and never read. The worker went
looking for the door, found it, and had nothing that could open it.

Elsewhere in the same store sit `d291965dex991.htm`, `msft-ex99_1.htm`,
`mtb_3q26xinvestorpresent.htm` and `d127076dex11.htm`, at tier 1 — the exhibits that *did*
arrive, on the runs where a worker happened to search for them inside a twelve-call budget.
Whether the substance of a current report reaches the report is currently decided by that.

### And the wrong five are opened

`_wanted` takes the five most recent current reports by `(filing_date, accession)` and looks
at nothing else. AstraZeneca's second run therefore acquired *Admission of Further Securities
to Trading*, *Total Voting Rights* and *Admission to Trading — EUR2.55 billion Bond Offering*,
and not the half-year results announcement, which is where a foreign private issuer states
its guidance. The run's own worker recorded the consequence as a lead it could not follow:
*"Numerous recent 6-K filings were identified in the source listing but not fetched."*

EDGAR states what a filing is about in the submissions index. The `items` column is listed
in `_OPTIONAL_COLUMNS`, copied into `columns` by `_validated_columns`, and then dropped: the
`Filing` dataclass has no field for it and the parser never sets one. Item 2.02 is *Results
of Operations and Financial Condition*. The platform reads the column that says so and throws
it away.

## Decision

**Acquisition opens the accession, not the file.** Two changes, one bounded and one free.

### 1. A current report brings its EX-99 exhibits

For each acquired **current report** (8-K, 6-K), read the accession's own document header,
select the documents whose type begins `EX-99`, and acquire them exactly as the primary
document is acquired: same fetch layer, same hashing, same excerpting, **tier 1**, and the
accession's own filing date.

**The header, not `index.json`, and this ADR's first draft had it wrong.** The audit's
recommendation — and the paragraph that used to stand here — said to read `index.json` and
filter on the type. Fetching one settles it: `index.json` carries the *icon* name, not the
document type.

```json
{"name": "d291965dex991.htm", "type": "text.gif", "size": "34182"}
```

`text.gif`, `compressed.gif`, `image2.gif`. The string `EX-99` appears nowhere in it, and a
rule written against that field would have selected on a picture of a file. The only other
way to reach a type from `index.json` is to infer it from the filename — `dex991`, `ex99_1`,
`ex-99` — which is precisely the guessing `aer.core.concepts` refuses for tags and for the
same reason.

`{accession}-index-headers.html` carries EDGAR's own dissemination header, one entry per
document:

```
<DOCUMENT>
<TYPE>EX-99.1
<FILENAME>d291965dex991.htm
<DESCRIPTION>EX-99.1
```

Ten kilobytes, one fetch, and the `<DOCUMENT><TYPE><FILENAME>` triple is the SGML
dissemination format's core rather than a rendering detail. Confirmed identical across two
filing agents — Microsoft's accession above (Donnelley) and M&T's own (`0000036270-26-000052`,
self-filed). It is HTML-escaped inside a `<PRE>` block, so it is *unescaped and read as the
SGML it is*, never parsed as a document.

The lesson is the handover's own: every error in this programme so far has been a plan
describing something nobody had read. This one was caught by reading it.

The date needs no new rule. Every file in an accession is published by that accession, and
`Filing.to_ref` already records the filing date rather than the period — so an exhibit
inherits the provenance its siblings have, correctly, without anything being decided again.

**Bounded three ways**, because acquisition is already the run's largest unmetered fetcher:

- **Current reports only.** A 10-K accession carries dozens of files, most of them the XBRL
  bundle the run already holds through `fetch_facts`. Whether a periodic report's siblings
  are worth opening is a separate decision with its own evidence, and this ADR does not make
  it.
- **`EX-99` only.** That is where the substance of a current report lives. Not `EX-10`
  (a contract), not `EX-23` (a consent), not the graphics.
- **A cap per accession**, and a cap on the extra fetches per run. An 8-K can carry a dozen
  exhibits; the run must not be able to spend its fetch budget inside one folder.

### 2. What a current report is *about* outranks when it arrived

`Filing` keeps `items`. Selection takes the most recent results filing first — Item 2.02
(results of operations) and Item 7.01 (Reg FD disclosure) — and then fills the remaining
slots by date as it does today.

A 6-K carries no item codes, so for a foreign private issuer the ranking reads
`primaryDocDescription`, which already carries the RNS headline the run stores as the title
(*"TOTAL VOTING RIGHTS"*, *"IMFINZI+IMDELLTRA IMPROVED OS…"*), against a small keyword list.
Deterministic, no model call, and no extra fetch: the column is already in the payload the
run has already downloaded.

**Materiality before recency, never instead of it.** The cap does not change and the date
ordering still fills the rest, so the change can only *substitute* a results release for the
least recent routine notice — it can never make a run read more, or older, or less.

## What this is not

**Not a new source, a new host or a new tier.** Every URL is under
`www.sec.gov/Archives/edgar/data`, which the allowlist already permits and which is already
tier 1. Nothing about admissibility, the licence, or the trust zones moves.

**Not an unbounded crawl.** Option B in the audit — take every document in every acquired
accession — was rejected: a periodic accession's file list is dominated by the XBRL bundle,
and the cost is bytes, time and a pack diluted with files nobody would cite.

**Not a replacement for the research workers.** They keep `search_filings_full_text` and
`fetch_known_url`, which reach documents outside the subject's own accessions. What changes
is that the *deterministic* layer stops depending on a model choosing to look.

## What is given up

**More excerpts compete for the same pack budget.** An earnings release is exactly the kind
of document that would crowd a section's pack, and the pack is assembled against a token
budget: `MAX_EXCERPTS` bounds what one document records and the budget bounds what one
section sees, so nothing here is unbounded — but a run's prose base grows, and which excerpts
a section is dealt changes. That is the intended effect and it is also a change to every
section's evidence, so it lands with the acquisition tests that measure it.

**One extra fetch per acquired current report**, whether or not the accession has an exhibit.
Five per run at today's cap, on an endpoint with no rate cost beyond the SEC delay the client
already applies.

**A filing index can disagree with the submissions index.** It is EDGAR's own data about its
own folder, so the disagreement is not a conflict to arbitrate: the accession index is the
authority on what the accession contains, and the submissions index stays the authority on
form, date and item codes.

## Consequences

**The document the console won on is acquired by the platform, deterministically, at tier 1,
on every run.** That is the acceptance test.

**The sentence "its substance is not before us" stops being true**, which means it must stop
being written — and it is model prose, so it stops on the next live run rather than on a
re-render.

**A worker's twelve-call budget is spent on what is outside the subject's accessions**, which
is what it is for. The three guessed URLs above are the measurement of what it was spending
it on instead.

## Alternatives considered

**Full-text search only.** The tools exist and are already CIK-scoped. But whether the
substance of an 8-K reaches the report would depend on a model choosing to search for it,
which is the difference between the two Microsoft runs — one T5 document and two. Good as a
supplement; not a mechanism.

**`index.json` plus a filename rule.** The measurement above rules out reading the type from
that endpoint, so the only way to use it is to infer `EX-99` from `dex991` or `ex99_1` — a
fuzzy match on a filer's file-naming habit, which is what this codebase refuses for XBRL tags
and refuses here for the same reason: it would be wrong in a way nobody noticed until the
wrong document was in a report.

**`{accession}-index.html`, the browser page.** It carries a proper table with the type in
it, and it is a rendering: columns, labels and markup that exist to be looked at. The header
file is the same information in the format EDGAR disseminates rather than displays.

**Leave it.** Guidance, recast segment histories and every quarterly results release stay out
of reach, permanently, and every judged comparison keeps turning on the same file.

## What landed, 2026-09-18

`aer.sources.sec.accession` reads the header and names what is in the folder;
`Filing.header_url` and `Filing.exhibit_ref` build both URLs from the CIK, the accession and
a filename EDGAR's own header listed, so the client's standing property is untouched — it
takes identifiers and never a URL, and a filing whose text said *fetch https://attacker.test*
still produces no method call that could act on it.

`_acquire_ref` is the one path a document is acquired by, and the exhibit goes through it: the
same fetch layer, the same hash, tier 1, the accession's own date, the same excerpting. A
second path would have been a second set of answers to questions the first had settled.

**Measured on the recorded header.** Microsoft's `0001193125-26-380280` lists 35 documents;
the rule selects exactly one, `d291965dex991.htm` — the file the console's note was built
from. M&T's bond-offering accession lists 17, carries EX-1.1, EX-3.1 and EX-4.1, and the rule
selects none, which is the type filter earning its keep on the first case that could have
embarrassed it.

**The stub had to grow the method, and that is the point of it.** `StubSecClient` lacked
`fetch_accession_documents`, so thirteen acquisition tests failed at once with an
`AttributeError` — roadmap §3.19.6's defect exactly: a stub that cannot serve a branch is a
scene that proves the plumbing works for a path production never takes. It serves the
recorded header now.

**What is still only half true.** The acceptance test above asks that a re-rendered run cite
the release and drop the sentence about substance not being before it. The acquisition is
provable offline and is proved; the citing is the next live run's to show, because a
re-render replays prose a model already wrote.
