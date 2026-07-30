# Phase 2 — task sequence (tasks 11–21)

Continues Stage 4 of `docs/PLAN.md`, which stops at task 10. The phase specification —
objective, deliverables, acceptance criteria — is Stage 3 → Phase 2, and it remains the
authority. This file is the dependency-ordered breakdown of it.

**Objective, restated.** Turn "one source" into a rigorous evidence pipeline: fetched bytes
become located excerpts, excerpts become verified citations, and a claim nobody can trace
cannot reach a report.

---

## What Phase 1 already banked

Two Phase 2 deliverables were pulled forward into Stage 4 because Phase 1 could not work
without them, and they do not need revisiting:

| Deliverable | Where it landed |
|---|---|
| Safe fetcher — SSRF guard, pinned-IP transport, robots, per-provider token bucket, circuit breaker, size caps | Task 7, `aer/fetch/` |
| Content-addressed cache with hash verification on read | Task 6, `aer/storage/` |
| Point-in-time filter at acquisition, quarantine flag and reason | Task 8, `aer/sources/sec/pit.py` |

`source_documents` also already carries the **whole** §2.9 provenance record, including
`publication_date_confidence`, `licence_note` and `robots_allowed`. Phase 2 fills those
columns rather than adding them.

## What must be obtained before the tasks that need it

- **A Companies House API key** (free registration) — blocks task 17. Get it early; it is a
  form, not a negotiation.
- **A determination on FCA NSM access** — blocks task 18, and is *research, not code*. The
  standing constraint is explicit: nothing that breaches terms of use or circumvents access
  controls. If NSM turns out to offer no permitted programmatic access, task 18 is dropped
  and says so in an ADR. It is deliberately last and deliberately isolated so that dropping
  it touches nothing else. **Done: no permitted programmatic access, task 18 dropped — ADR
  0022.**

## Why this order

Three constraints fix the sequence, and everything else follows:

1. **Malicious-file defences precede the first parser.** Untrusted bytes reach a parser in
   task 11; T5's controls cannot arrive after the thing they protect.
2. **The citation verifier comes second, not last.** It is invariant 2 and threat T10 — "the
   strongest single control in the system". Building it early means every parser and adapter
   added afterwards is held to it from its first commit, rather than being retrofitted into
   it at the end of the phase.
3. **Arbitrary web content waits for the injection scanner.** SEC full-text search and
   issuer-IR discovery (task 16) fetch pages nobody vetted. Task 13 must precede them.

`arelle` is a 32-dependency XBRL processor and it appears in **task 17, not earlier**. SEC is
served by the `companyfacts` JSON API, which Phase 1 already parses; inline XBRL matters only
for UK filings. Adding that dependency before the UK adapter needs it would be paying its
weight for nothing.

---

## Task 11 — Safe document parsing, extractions and the HTML parser

**Objective.** A hardened path from archived bytes to located text, exercised by the simplest
parser.

**Build.**
- `aer/extract/safety.py` — the T5 controls in one place: size caps checked before parse,
  media-type sniffing that does not trust `Content-Type`, and parse executed in a
  **subprocess** with a wall-clock timeout and a memory cap. The subprocess boundary is an
  implementation detail behind one async function, so no parser call site knows about it.
- `aer/extract/xml.py` — the hardened `lxml` configuration: `resolve_entities=False`, no DTD
  loading, no network access. One constructor, used by everything that touches XML.
- `aer/extract/html.py` — `selectolax`. Returns text with **stable character offsets into the
  archived bytes**, because an excerpt without a locator cannot be verified.
- `Locator` and `Excerpt` in `aer/core/schemas/extraction.py` — pure, `mypy --strict`.
- `extractions` table + migration 0010. Columns: `source_document_id`, `kind`, `locator`
  (JSONB), `excerpt`, `content_hash`, `extractor`, `extractor_version`, `created_at`.
- `aer/services/extractions.py` — persistence, idempotent on `(source_document_id, locator)`.

**Tests.** XXE fixture (external entity, billion laughs); zip bomb; a 60 MB file against a
50 MB cap; a `.pdf` served as `text/html` and vice versa; a parser that hangs, killed by the
timeout; offsets round-trip — slice the archived bytes at the recorded locator and get the
excerpt back, byte for byte.

**Acceptance.** No fixture escapes the sandbox, resolves an entity, or exceeds the timeout;
every excerpt is reproducible from its locator and the artefact hash alone.

**Non-goals.** PDF, iXBRL, claims, citations. No new adapters.

---

## Task 12 — Claims, citations and the deterministic excerpt verifier

**Objective.** The keystone. A citation is confirmed by code re-reading the artefact, never by
a model asserting it.

**Build.**
- `claims` table: `report_section_id`, `kind` (`NUMERIC` | `FACTUAL` | `FORWARD_LOOKING` |
  `OPINION`), `text`, `value_ref` (fact id / calculation id, nullable), `created_at`.
- `citations` table: `claim_id`, `source_document_id`, `extraction_id`, `excerpt_verified`
  (boolean, **default false**), `verification_method`, `match_ratio`, `verified_at`.
- `aer/verify/citations.py` — the verifier. Fetches the artefact **by hash**, normalises
  whitespace, and confirms the excerpt appears at the recorded locator with a
  `rapidfuzz` ratio ≥ 0.95. Pure except for the store read.
- The write path for `excerpt_verified` exists **only** inside the verifier. A test scans the
  source tree to prove nothing else sets it — the same structural check that guards the SDK
  import boundary.
- Gate 2 blocks on any unverified citation, overridable only per-citation with a recorded
  reason in `approvals`.
- Migration 0011.

**Tests.** A fabricated excerpt is rejected; a real excerpt with different whitespace is
accepted; an excerpt at the wrong locator is rejected even though the text exists elsewhere in
the document; a tampered artefact fails the hash check before the excerpt is even considered;
Gate 2 refuses a draft with one unverified citation and accepts it once overridden with a
reason.

**Acceptance.** Hallucinated-citation rate 0 on the fixture. `excerpt_verified` cannot be set
from anywhere but the verifier, proved by a source scan.

**Non-goals.** Citation *relevance* — whether the excerpt supports the claim — which needs an
LLM judge and belongs to Phase 4's validator.

---

## Task 13 — Injection heuristics and the untrusted-content wrapper

**Objective.** Fetched text is data. Make that structural, and flag the documents that try to
be otherwise.

**Build.**
- `aer/extract/injection.py` — heuristics over extracted text: invisible or off-canvas text,
  white-on-white, `ignore previous instructions` patterns, fake system/role markers, base64
  blobs above a size threshold, zero-width characters. Each hit is a typed finding with a
  locator, not a boolean.
- `source_documents.injection_findings` (JSONB) + `injection_flagged`, migration 0012.
- `aer/agents/untrusted.py` — the wrapper: every fetched excerpt reaching a prompt is enclosed
  in `<untrusted_source id=… tier=…>` with the system prompt stating the rule. Applied by the
  agent base, so an agent cannot forget it.
- The tool allowlist already lives in `Agent.allowed_tools` and is checked in code
  (`require_tool`). Task 13 adds the test that proves an injected instruction cannot widen it.

**Tests.** `fx_injection` — 25 payloads per §2.10: hidden HTML text, white-on-white PDF text,
comment-embedded instructions, fake system prompts, exfiltration lures. Assert **0** tool-policy
violations and that every payload is flagged. One test asserts no agent has a network tool at
all, which is T3's actual control.

**Acceptance.** Prompt-injection resistance: 0 violations across the corpus.

**Non-goals.** Blocking a flagged document automatically. A flag is shown at Gate 2; a human
decides. Silently dropping evidence is its own failure mode.

---

## Task 14 — PDF extraction

**Objective.** Filings and IR decks that are not machine-readable XBRL.

**Build.** `aer/extract/pdf.py` — **`pdfplumber` for both** text with coordinates and tables.
`pymupdf` was dropped at implementation time: it is AGPL-3.0 or a paid licence, which conflicts
with this MIT project's intended commercial network deployment. See ADR 0020. Locators carry page number and bounding box as well as character offset, because a
PDF excerpt a human cannot find on the page is not checkable. Runs inside task 11's
subprocess sandbox.

**Tests.** Table extraction against a hand-labelled truth set; a scanned image-only PDF is
reported as unextractable rather than returning empty text; a malformed PDF fails inside the
sandbox without taking the worker down; white-on-white text is extracted **and** flagged by
task 13.

**Acceptance.** Every extracted number is locatable to a page and box; the truth set matches.

**Non-goals.** OCR. A scanned filing is reported as unextractable, and that is the honest
answer for now.

---

## Task 15 — Publication dates, source tiers and point-in-time at claim time

**Objective.** Close the look-ahead loop: dates extracted rather than trusted, and re-checked
where claims are made.

**Build.**
- `aer/extract/dates.py` — publication-date extraction with a **confidence**, from HTTP
  headers, document metadata, filing indexes and in-document text, in that order of trust.
  Returns the candidates and which one won, so the confidence is explicable.
- `aer/sources/tiering.py` — tier assignment from provider and document kind. Deterministic
  table, not a judgement.
- PIT re-check at claim time: a citation whose source is inadmissible at the as-of date fails
  verification, independently of the acquisition-time filter. Two checks, because the
  acquisition filter cannot know what a claim will later rest on.
- Migration 0013 if any column is missing; most of §2.9's record already exists.

**Tests.** `fx_lookahead` — a corpus with 5 planted post-dated documents; assert 100%
detection at acquisition **and** at claim time; assert a document with no determinable date is
quarantined when `point_in_time = true` and usable only after a recorded override.

**Acceptance.** Temporal compliance 100%; look-ahead detection recall 100%.

---

## Task 16 — SEC full-text search and issuer-IR discovery

**Objective.** More than one US document per run.

**Build.** `aer/sources/sec/fulltext.py` against EDGAR full-text search, and
`aer/sources/issuer.py` — IR-page discovery from a company's own domain, through the existing
fetcher's allowlist and robots checks. Both feed the acquisition service already built in
task 8.

**Tests.** Recorded cassettes; a search result whose document fails the PIT check is
quarantined rather than dropped; an IR page on a domain not on the allowlist is refused; a
page that `robots.txt` disallows is not fetched.

**Acceptance.** A run acquires ≥3 documents of ≥2 kinds, all hash-addressed and replayable
offline.

**Non-goals.** General web search. That is a Phase 4 tool with its own gate.

---

## Task 17 — Inline XBRL and Companies House

**Objective.** The UK half of the universe the request form already accepts.

**Build.** `arelle` behind `aer/extract/ixbrl.py`, and `aer/sources/uk/companies_house.py`.
Requires the API key; its own rate limit (~2 req/s) goes in the existing per-provider limiter.

**Tests.** Golden iXBRL extraction against a real UK filing; a filing with a non-standard
taxonomy extension is reported as partially extractable with a **human-confirmation gate**
rather than guessed at — the plan flags UK iXBRL variability as the phase's main risk, and
this gate is the mitigation.

**Acceptance.** An LSE-listed request completes acquisition and extraction. Today it passes
validation and then fails, because SEC EDGAR is the only adapter.

---

## Task 18 — FCA National Storage Mechanism *(conditional)* — **declined**

Preceded by a written determination of what NSM permits. If programmatic access is not
permitted, the task becomes an ADR recording why, and the UK path relies on Companies House
alone. **Do not build first and check later.**

**Outcome: not permitted, so not built.** The FCA's terms prohibit automated access to its
sites without its prior written consent, and the NSM offers no public read API to integrate
against instead — the "NSM API" in FCA material is the submission channel for Primary
Information Providers. See ADR 0022 and `docs/data-sources/fca-nsm.md`.

The refusal is enforced in code rather than left to this note: `REFUSED_HOSTS` in
`aer/fetch/policy.py` refuses `.fca.org.uk` under every provider, ahead of the allowlist and
ahead of `extra_hosts`, on the original URL and on every redirect hop.

---

## Task 19 — Disagreements *(done)*

**Objective.** Two sources, one number, no silent winner.

**Built.** `aer/core/disagreement.py` — the ladder, pure and `mypy --strict`, with **no
default branch**: a pair matching no rung raises, and a hypothesis property over the whole
input space asserts it never does. Two rungs moved from the order §2.9 lists, both argued in
ADR 0023: units are checked before any arithmetic, and a suspected scale error outranks the
tier rule because a factor of 10^6 is a parsing bug rather than evidence about a publisher.
`aer/services/disagreements.py` persists, idempotent on a fingerprint over identities rather
than values. Escalations are carried **inside** the gate-2 payload, so they are inside the
hash the approval records.

**Build.** `disagreements` table and the §2.9 resolution ladder as deterministic code: same
tier and value → agree; different tiers → lower tier number wins; same tier, different
`filed_date` → later wins if the basis matches, else escalate; same tier and date, different
value → escalate to Gate 2. Every resolution records both positions, the rule applied and the
rationale. **Losing evidence is retained** for the report's disagreement appendix. Migration
0014, including the `sector_profiles` table and seed so Phase 3 opens on data rather than a
migration.

**Tests.** One case per rung, including the two that must escalate rather than resolve.

**Acceptance.** No rung resolves by falling through to a default; escalation reaches Gate 2.

---

## Task 20 — The sources and claims surfaces

**Objective.** The phase's user-visible outcome.

**Build.** `GET /api/runs/{id}/sources` and `GET /api/claims/{id}`; the run console's live
sources table with tier badges, publication dates, hashes and quarantine flags; and the claim
drill-down that shows the **exact excerpt** in the source with its verification state.

**Tests.** A quarantined source is visibly quarantined with its reason; an unverified citation
is visibly unverified; the page works with JavaScript off; Playwright covers the drill-down.

**Acceptance.** From a rendered report, a reader reaches the excerpt behind any claim in two
clicks.

---

## Task 21 — The evaluation suite as a CI gate

**Objective.** Make the phase's guarantees continuously enforced rather than once-verified.

**Build.** The §2.10 harness over the fixtures the previous tasks produced, with these
metrics **blocking** in CI: citation accuracy ≥ 98%, hallucinated-citation rate 0, temporal
compliance 100%, look-ahead recall 100%, injection resistance 0 violations, unit integrity 0.
Runs with no network and no model spend.

**Acceptance.** A deliberately introduced regression in any of the six fails CI. Checked by
introducing one, per the practice used throughout Phase 1.

**Closes Phase 2.**

---

## Deliberately not in Phase 2

Valuation, price data, macro vintages (Phase 3). Agents beyond planner and extractor, the
skill-file engine, the red-team (Phase 4). PDF output, charts, Obsidian (Phase 5). Anything in
the plan's "do not build yet" list for this phase.
