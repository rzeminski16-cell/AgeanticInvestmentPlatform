# 0059 — A model proposes peers, the registry resolves them, and a person still decides

Date: 2026-08-18
Status: accepted, amended 2026-08-19 — a peer is recorded by name and nothing is fetched
for it — and again 2026-09-03 — the model is asked only when a price feed is configured
(see the amendments at the end)

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

**A budget refusal is not a failed model call**, and the fallback must not absorb one.
`BudgetExceededError` is the signal the engine turns into a stopped run awaiting a
person's decision; catching it alongside the outages would spend past the ceiling and
carry on, which is invariant 6's failure wearing the costume of graceful degradation. It
is re-raised, and a test holds the distinction.

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
* Refusals are part of the step's recorded output **and are rendered at the gate**, so a
  reviewer sees what the model proposed that the registry rejected — a hallucinated ticker
  is visible, not vanished. They stay outside the hashed payload: what is being confirmed
  is the peer set, and an approval that moved with what the model got wrong would be an
  approval of the wrong thing.
* **Peers are US-listed, because resolution is EDGAR-only.** The prompt says so rather
  than leaving the model to discover it through refusals. That is the slice's existing
  limit rather than a new one — this workflow acquires the subject from EDGAR too — and it
  is where a Companies House resolver would attach when a UK run needs UK comparables.
* The peers step gains an `estimated_cost_gbp`, keeping ADR 0052's rule that every step
  either declares an estimate or is named deterministic.

---

## Amendment, 2026-08-19 — a peer is recorded by name, and nothing is fetched for it

**The third decision above — acquiring a resolved peer's facts on demand — is withdrawn.**
A proposed ticker is still resolved against the registry, the subject is still refused as
its own peer, and the human at the PEER_SET gate still decides. What changes is what
confirmation buys: the set is *recorded*, and no filing, aggregate or document is fetched
for any peer.

Two findings from the first complete run drove this, and each alone would have sufficed:

1. **The acquisition bought nothing a comps table could use.** A peer's multiple needs the
   peer's *price* as well as its filings, and no price feed is subscribed (task 29 is
   conditional on one). Every acquired peer therefore ended excluded anyway — eight
   companyfacts fetches and eight companies' fact rows, for a table with no peer in it.
2. **It was the vector for evidence contamination.** The acquired peers' facts landed in
   the same store the subject's evidence queries read, and under the then request-scoped
   queries (fixed by ADR 0061) eight issuers' figures entered the subject's evidence pool.
   ADR 0061's subject scoping now contains that class of defect on its own; not fetching
   removes the exposure entirely while the fetch buys nothing.

Concretely: `PeerProposal` carries `period_end: date | None`, and a peer this database has
never held resolves with `None` — recorded by registry identity (CIK) and name, with the
model's rationale. A peer whose facts *are* already stored (a past subject, say) keeps its
company id and its latest stored period at or before the as-of date, and proceeds into
alignment as before. An undated peer reaches the comps build and is excluded there with
the one reason (`UNACQUIRED_PEER_REASON`): computing its multiple needs its filings and
its prices, and this workflow deliberately acquires neither. The gate page says the same
thing — confirming records the set; it fetches nothing.

**The return condition is a price feed.** When task 29's subscription exists, peer
acquisition can come back — behind ADR 0061's subject scoping, so a peer's facts are
scoped to the *peer's* company row and can never surface as the subject's evidence — and
the recorded-by-name peers a reviewer confirmed become the acquisition list. The refusal
for "resolves but no usable facts" also returns then; today it cannot arise, because
nothing is fetched to be found unusable.

The consequences above that describe peer artefacts on the sources surface and the up to
eight companyfacts fetches no longer apply. The step's cost estimate is untouched — it
always covered the model call, which is the only thing the step spends money on.

## Second amendment, 2026-09-03 — the model is asked only when a price feed is configured

**The model's slate is bought only on a machine with a market-data subscription.** The
first amendment left `propose_peers` spending one model call per run on a reasoned peer
list that, without a price feed, can contribute no multiple: a peer recorded by name
reaches the comps build and is excluded there with `UNACQUIRED_PEER_REASON`, every time.
The roadmap's §4.15 remnant asked for that to be chosen rather than inherited, and the
operator chose: the call is skipped until the feed exists, rather than the feed being
bought to justify the call.

Concretely, `Settings.price_feed_configured` is the one definition of "is there a price
feed" — the same predicate the client builder reads — and the peer step consults the
model only when it is true. Otherwise the step proposes the deterministic floor alone,
records `model_consulted: false` and a sentence saying why (`model_skipped_because`),
both outside the gate's hash as `refused` is, and spends nothing. The gate page and its
"does not apply" refusal show the sentence, so an operator reading "no peers" learns it is
a subscription rather than a fault. The step's estimate stays at the model call's cost:
an estimate is a ceiling the budget guard reads, and a ceiling that is never reached is
not wrong.

The return condition is unchanged: when the feed exists, the model is asked again and the
first amendment's path — resolved, recorded, nothing fetched — is exactly as it was. What
would change *that* is peer acquisition coming back behind ADR 0061's scoping, which is
the first amendment's own return condition and remains a decision for the day the feed is
subscribed.
