# ADR 0122 — The knowledge map learns what you decided, and still never becomes evidence

**Status.** Proposed — V1.0_Alpha. Accepted when the change it argues lands.
**Date.** 2026-09-14
**Extends.** ADR 0064 (prior research may shape the questions, never the answers) and ADR 0065
(themes are the connective tissue), whose confirmed-only rule this reuses unchanged.
**Does not touch.** The hard rejection of `Provider.INTERNAL_PRIOR_RUN` in
`verify/citations.py`, or ADR 0073's rule that an attested lineage reaches no shareable surface.
Both become *more* load-bearing here, not less.
**Required by.** The operator's direction of 14 September 2026: a knowledge map that grows with
system use and supports elements of the system.

## Context

The knowledge layer is built, and `docs/archive/knowledge-graph.md` says so in its own words:
*"the layer this document planned is built."* Seven node kinds — company, run, industry,
catalyst, source, theme, index — with six edge kinds, all projected into an Obsidian vault and
into an in-app graph view. It grows on its own as the platform is used: a run resolves a company,
an approved report becomes a run node, a confirmed peer set makes a stub node for a company
nobody has researched yet, a confirmed theme links companies across industries, a catalyst
accumulates references across runs because its identity is `(company, label)` rather than
`(run, label)`.

**Only confirmed state produces an edge.** A proposed-but-unapproved peer set or classification
contributes nothing, which is why the graph cannot quietly fill with a model's suggestions. That
rule is the reason the map is worth anything and it is not reopened here.

So the map already does the first half of what was asked: it grows with use.

**What it reads back is two things.** `_prior_digests` puts prior approved conclusions in front
of the planner as labelled hypothesis material (ADR 0064), and `prior_comparison_content` writes
the `prior_research_comparison` section at draft time. That is the whole of it.

And there is a larger hole, which V1.0 creates by building the judgement layer: **the map
records what you researched and knows nothing about what you decided.** A thesis, a premise and
its threshold, a decision, a fill, a monitor finding, a post-trade verdict — none of them is a
node, none of them is an edge, and none of them is read by anything. A knowledge map that
accumulates research and forgets judgement is a library with no borrowing record.

## Decision

**The map gains the judgement layer as nodes and edges, under the same confirmed-only rule; and
four surfaces read it back. It becomes evidence for nothing.**

### 1. Four node kinds, four edges, and the same rule

| Node | Identity | Grows when |
|---|---|---|
| **Thesis** | `theses.id` | A thesis is confirmed against a company |
| **Premise** | `premises.id` | A premise is written with its metric, comparator, threshold and unit |
| **Decision** | `decisions.id` | A decision is recorded — including `pass`, which is a decision |
| **Verdict** | `reviews.id` | A post-trade review is filed |

| Edge | Confirmed by |
|---|---|
| Company → Thesis | Thesis confirmation |
| Thesis → Premise | The premise being written |
| Decision → Thesis *version* | The decision, pinned to the thesis as it stood |
| Premise ← Finding | The monitor resolving a premise to a number |
| Verdict → Decision | The review being filed |

A draft thesis, an unsaved premise and an unfiled review produce nothing. Same rule, same
reason.

**A decision points at a thesis version, not a thesis.** That is ADR 0102's shape and it is what
makes the map answerable about the past: *what did I believe when I bought this?* is a different
question from *what do I believe now*, and a map that collapses them cannot support a review.

### 2. Four surfaces read it back

Today two things read the map. These four join them, and each is a question the operator can
already ask out loud and the platform cannot answer:

- **The monitor.** A finding on one company's premise surfaces against every other position
  whose premises share a metric or whose companies share a confirmed theme. *"The thing that
  just broke here is load-bearing in two other places."* The connective tissue exists (ADR
  0065); nothing traverses it on a finding.
- **Ask, tier 1.** Tier 1 answers from the record for nothing, and the map **is** the record.
  *"Which of my holdings depend on the same premise?"* and *"what did I say about this company
  last time, and did it happen?"* become free questions rather than research.
- **The refresh.** Materiality today is a property of the figure — 2% relative, a sign change, a
  threshold crossing. The map makes it a property of *this operator's* position too: a section
  whose figures feed a premise you are holding is material at a smaller move than one that feeds
  nothing you own.
- **The methodology library.** `calc/outcomes.py` already measures a confirmed assumption
  against the first fiscal year it forecast. With decisions and verdicts in the map, the same
  measurement runs over *methods*: which approach produced which outcome, across runs, over
  time. That is the difference between a library of methods and a library of methods that knows
  which ones worked.

### 3. It becomes evidence for nothing, and this is where the rule gets harder

`Provider.INTERNAL_PRIOR_RUN` is hard-rejected by the citation verifier: a claim whose only
support is a prior run is refused. That rule was written when the map held research. It now
holds **the operator's own judgements**, and the temptation is worse, because a premise reads
like a fact and a verdict reads like a finding.

Three things hold:

- **A premise is an attestation, not a fact** (ADR 0073). It is what the operator's own book
  says. A lineage containing an attested node reaches no shareable surface, structurally,
  because the type it propagates into has no field for the figure. The evidence pack (ADR 0120)
  is a shareable surface and therefore carries none of this.
- **No claim may name a thesis, a premise, a decision or a verdict**, exactly as no claim may
  name a challenge brief (ADR 0095). The map steers questions; it supports no answer.
- **Anything from the map that reaches a prompt is wrapped**, under ADR 0119's rule, for ADR
  0119's reason: the platform cannot prove its own prose is free of quoted third-party text, and
  a premise the operator typed after reading an excerpt is no different.

### 4. The vault stays one-directional

**From the vault: nothing, ever.** The projection is one way, the application never reads vault
content back, and the operator's own notes in the personal half of a note are theirs. Nothing
here changes that, and adding judgement nodes makes it more important: a vault note is a file on
disk that anything can edit, and a decision record that could be edited outside the database
would destroy the one property a post-trade review depends on.

## What is given up, named rather than discovered

**The map gets opinionated, and a wrong premise propagates attention.** If a premise is badly
written — a metric that resolves to the wrong figure — the monitor now surfaces that mistake
against every position sharing it. The mitigation is the four unresolvable cases the monitor
already refuses on, and the fact that a finding is a prompt to look rather than an instruction.

**Cross-position surfacing can become noise.** Two holdings sharing the metric *revenue growth*
share almost nothing. The first cut is deliberately narrow — a shared metric **and** a shared
theme or sector — and it widens only on evidence that it is too quiet.

**It is only as good as the judgement put into it.** A map of theses nobody wrote is empty, and
this feature is worth nothing before F9 and F10 exist. That is why it is sequenced last.

## Consequences

**The `prior_research_comparison` section gets a second half.** It compares prior view against
current today; it can now compare *prior premises against what happened to them*, which is the
look-back the whole loop exists to produce.

**Nothing is backfilled.** The three stored reports have no theses, decisions or reviews, and
the migration invents none. The map starts empty on the judgement side and fills as the operator
uses the loop — which is the honest shape and also the test: a map that had judgement nodes on
day one would have got them from somewhere nobody authorised.

**The graph view gains four node kinds and needs a legend.** `/knowledge/graph` currently draws
a picture that fits on a page. The judgement nodes will outnumber the research nodes within a
year of ordinary use, so the view filters by kind from the first version rather than being
retrofitted when it becomes unreadable.

## Alternatives considered

**Build a new knowledge system for judgement.** A second store with its own ontology, beside the
one that exists. Two maps that must agree about what a company is, and they will not.

**Put judgement in the vault only.** Cheap — the export already writes notes — and it makes the
vault authoritative for something the database should own. A decision record editable in a text
editor cannot support a review that asks what was knowable at the time.

**Let the map answer questions directly, as evidence.** The obvious next step and the one that
breaks the platform: a claim supported by "we concluded this last time" is a claim supported by
nothing, and the citation verifier refuses it today precisely so that nobody has to notice.

**Do it before the judgement layer exists.** Nothing to record. It is sequenced after F9, F10
and F14 for the same reason ADR 0115's adversary is sequenced after ADR 0117's view: you cannot
connect what has not been written.

## Amended 24 September 2026, on building §1 (ROADMAP §3.19, item 65)

Four things the code settled that this record left open.

**The thesis version is reconstructed, not stored.** A decision points at the premises as they
stood when it was held: those whose own `held_at` is not after the decision's and which had
not been withdrawn by then, read from the judgements' clocks (`aer.obsidian.judgements`). ADRs
0102 and 0104 never say *version*, and storing one beside the premises would be the same fact
twice; the copy that drifts is the one a review would read.

**The monitor's reading is the premise's state, not a node.** *Premise ← finding* is carried as
the premise's latest reading — its status in ADR 0079's words — on the premise node and in the
thesis note. A node per reading would outnumber every other kind within a quarter of ordinary
use, and the finding pages hold the rest.

**A verdict scores the decisions of its episode.** *Verdict → decision* is resolved by the
window: a confirmed review names a security and the dates its position opened and closed, and
scores each decision on that security held inside them. A pass names no security and is scored
by nothing.

**Where it lives.** The vault gains `60-Theses/`, one note per thesis of the report's owner
over the exported component, linked from the company note and linking back to it and to the run
it was written against; every link still resolves within the export. A company somebody holds a
thesis on is in the map whether or not it was ever researched, as a stub. The statistics count
the four kinds; the in-app graph draws them with a legend that counts them and a filter by kind
from this first version. Nothing is backfilled.

Status stays *Proposed*: §2's four surfaces are Phase 6b.2 and 6b.3, and this record is
Accepted when the last of them lands.

## Amended 24 September 2026, on building §2's first two surfaces (ROADMAP §3.19, item 66)

**The monitor's surfacing is narrow, as *what is given up* asked; Ask's is wide, because the
operator asked.** A reading that resolves a premise surfaces, on its own page, the other held
positions whose thesis asserts a premise on the same metric *and* whose company shares a
confirmed theme or sector with the reading's (`aer.services.shared_premises`). Ask's *which
positions rest on this belief?* answers over the metric alone: a person asking wants the whole
list and can judge the noise. Both read theses, premises, the monitor's latest readings, the
book's held positions and the confirmed relations, and nothing else; a retired thesis, a
withdrawn premise and a position sold are in neither answer.

**"The same premise" means the same metric.** Two premises are one belief where the monitor
resolves their metrics to one key or, where it cannot, where their words are the same once
normalised. A premise nothing can test is in neither answer: nothing can rest on it.

**Tier 1 gains a second shape.** The resolver sends the belief question to tier 1 whatever the
record holds — the theses are the record it reads — and the answer is rows before it is a
sentence; nothing is recomputed and nothing is spent. Page specification §12 and §13 are
corrected.

The two remaining surfaces of §2 — the refresh's materiality and the methodology library —
are Phase 6b.3, with the proof that none of this is evidence.
