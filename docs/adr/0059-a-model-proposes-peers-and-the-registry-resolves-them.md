# 0059 — A model proposes peers, the registry resolves them, and a person still decides

Date: 2026-08-18
Status: accepted

## Context

The comps pipeline is complete from the peer-set gate onward: a confirmed set is priced,
aligned and turned into a table whose exclusions are named (tasks 29–30), and
`confirmed_peer_set` refuses to build anything from a set nobody approved. What feeds the
gate is `propose_peers_from_sic`, which proposes only companies **already in this
database** that share the subject's SIC major group. That was a deliberate floor — free,
deterministic, exercising the gate on every run — but it has the consequence its own
docstring admits: a fresh database proposes nobody. In practice every run so far has been
the first run for its company, so no run has ever had a comps table, and the report's
"no comparison was performed" has been the permanent state rather than the honest edge
case.

The floor cannot fix itself. The companies most comparable to a subject are precisely the
ones this platform has not researched yet, and no deterministic lookup over an empty table
will ever name them. Naming plausible comparables for a well-known listed company is,
however, exactly the kind of judgement-with-a-rationale the platform's division of labour
assigns to the model — and exactly the kind of output the peer-set gate was built to put
in front of a person.

## Decision

**A new agent role, `peer_proposal`, proposes comparable companies by ticker, each with a
written rationale.** It holds no tools and is handed only the subject's identity, listing
and classification; its output contract (`PeerSlate`) has one field — a bounded list of
`{ticker, name, rationale}` — so there is nowhere to put a figure, a rating or a peer
set's approval. It is routed under its own name to the workhorse model at medium effort:
the answer is a short list drawn from general knowledge of the market, and the expensive
route would be buying a certainty this role is not trusted for in any case.

**A proposed ticker is a claim, and code checks it.** Every ticker in the slate goes
through `SecEdgarClient.resolve_entity`, which answers from EDGAR's own registry and
raises for a ticker it does not know or cannot disambiguate. An unresolvable ticker is
recorded as a refusal with the reason and is never fetched; a hallucinated company
therefore costs one lookup against an index the run had already fetched, and cannot put a
name in front of the reviewer that no registry backs. The subject is refused as its own
peer, by CIK rather than by string.

**A resolved peer's facts are acquired on demand, through the same chain as the
subject's.** `fetch_company_facts` → `record_acquisition` (T1, publication date from
`latest_filed`) → `upsert_company` → `parse_company_facts` → `select_point_in_time` →
`persist_facts`. Every peer figure therefore traces to a hashed artefact and point-in-time
selection applies at acquisition, exactly as for the subject. Two subject-path steps are
deliberately skipped: excerpt location and the segment sweep. Peer facts feed comps
arithmetic — multiples computed and recorded as calculations — and are never cited in
prose, so there is no claim for an excerpt to verify and no surface that reads a peer's
segments. A peer that resolves but has no usable facts at or before the as-of date is
refused with the reason, not silently dropped.

**The SIC floor remains, underneath.** Model-proposed peers come first, carrying the
model's rationales; companies from `propose_peers_from_sic` not already in the list are
appended after, carrying their code-written rationale; the merged set is capped at
`MAX_PROPOSED_PEERS`. When the model call fails — provider error, unusable reply — the
step logs it and falls back to the floor alone, because a proposal the deterministic path
can still make must not die for the enrichment.

**Nothing downstream moves.** The step's output shape, the gate payload and its hash, the
approval flow, `confirmed_peer_set` and the comps build are all unchanged — the
confirmation and the refusal were always indifferent to who proposed. The human at the
PEER_SET gate remains the control, now reviewing rationales a model wrote instead of an
empty list.

## Consequences

* Runs against a fresh database get a real peer proposal, so the comps table becomes
  reachable on an ordinary first run — at the cost of one model call and up to eight
  EDGAR companyfacts fetches, all rate-limited and cached like any other fetch.
* The step acquires artefacts for companies the operator did not name in the request.
  That is contained three ways: only EDGAR (already allowlisted, T1) is reachable, only
  tickers that resolve are fetched, and nothing computed from them reaches a report
  without the gate's approval of the set.
* Those artefacts are recorded against this run, so the sources surface lists a peer's
  XBRL aggregate alongside the subject's documents. That is the honest record — the run
  did fetch them — and it is why they carry the peer's own name in the title.
* Refusals are part of the step's recorded output, so a reviewer can see what the model
  proposed that the registry rejected — a hallucinated ticker is visible, not vanished.
* **Peers are US-listed, because resolution is EDGAR-only.** The prompt says so rather
  than leaving the model to discover it through refusals. That is the slice's existing
  limit rather than a new one — this workflow acquires the subject from EDGAR too — and it
  is where a Companies House resolver would attach when a UK run needs UK comparables.
* The peers step gains an `estimated_cost_gbp`, keeping ADR 0052's rule that every step
  either declares an estimate or is named deterministic.
