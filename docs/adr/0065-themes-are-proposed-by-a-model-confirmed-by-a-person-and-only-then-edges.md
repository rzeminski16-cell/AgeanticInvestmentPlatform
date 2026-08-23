# 0065 — Themes are proposed by a model, confirmed by a person, and only then edges

Date: 2026-08-19
Status: accepted

## Context

The knowledge graph's only company-to-company edge was "a run of A named B as a
comparable". That is a real relation and a narrow one: it cannot express *AI capital
expenditure* linking a hyperscaler, a fab, a utility and a REIT — the kind of connection
that makes a research library worth more than the sum of its reports
(`docs/archive/knowledge-graph.md`, K1).

Where themes come from is the design question, and three answers were considered:

- **Operator-authored.** No model and no gate, but no discovery either — the platform
  notices nothing the operator did not already notice, which forfeits the point.
- **Derived.** Clustering on shared concepts or excerpt keywords is cheap and nearly
  meaningless: nothing in the data distinguishes a theme from a coincidence, and a graph
  filled by coincidence is worse than an empty one.
- **Model-proposed, human-confirmed** — the peer pattern (ADR 0059). Naming the larger
  story a company belongs to is a judgement about the market with a written rationale:
  the model's half of the division of labour, put in front of a person before it counts.

## Decision

**The third.** A `theme_proposal` role is handed the subject's identity, classification
and the existing theme vocabulary, and returns a bounded slate of `{key, label,
rationale}` — nothing else has a field. A `THEME_SET` gate shows the slate with every
rationale at full length; only a confirmed slate becomes rows, and only rows reachable
through an **approved** report become edges.

The containments, each structural:

- **Identity is code's.** Keys are slugged before anything is matched or founded, so "AI
  Capex" and "ai-capex" cannot become two themes however the model spells them. The
  reviewer is told whether each proposal joins a tracked theme or founds a new one — that
  distinction is inside the gate's hash.
- **Membership is the subject alone.** The role has no field for a company list; other
  companies join a theme through their own runs, each behind its own gate. A model
  cannot file anyone else's company under anything.
- **An unconfirmed theme contributes nothing**, exactly like an unconfirmed peer set: no
  row, no edge, no note, no line in any journal. A failed model call proposes an empty
  slate and the run continues — there is no deterministic floor for a judgement about
  the market, and "no themes" is a fact rather than a failure.
- **The gate is conditional.** An empty slate waits for nobody.
- **Confirmation lands at report creation**, as `themes` and `theme_memberships` rows
  pointing at the run's report. The rows are inert until the report is `immutable`; the
  graph, the vault and the statistics read memberships through approved reports only.

The vault projects the result: `40-Themes/<label>.md` per confirmed theme, a `themes`
back-link array on company notes, and the export component walks the union of the
competitor and theme relations — so every company a theme note links is exported with
it, and closure survives the second relation.

## The `obsidian_linker` route (K6)

`config.py` routed an `obsidian_linker` role to Haiku and the router allow-listed it,
but no agent ever declared the role — a configured capability that did not exist, which
somebody would eventually have wired something to because it was there. Its plausible
job, proposing links, is exactly what `theme_proposal` does with a gate in front of it.
**The route is deleted and `theme_proposal` supersedes it**, routed to the workhorse
model at medium effort like the peer proposer, because naming market connections is the
same shape of judgement.

## Consequences

- One more conditional gate in `GATE_ORDER` (after `SECTOR_SPECIALIST`), one more model
  call per run (estimated with the peer proposer's figure), and a `THEME_SET` value on
  `gate_kind` (migration 0048, with the 0027 autocommit pattern and a downgrade that
  refuses to relabel a recorded decision).
- Themes accumulate across runs by construction: the second run naming `ai-capex` joins
  the first's theme, which is the entire value of slugged shared identity — and also the
  failure mode to watch. The statistics page counts themes with confirmed members;
  a library of one-member themes means the extraction is producing noise.
- A rejected slate stops the run, the same as every other gate. The operator who wants
  the run without the themes re-runs and rejects nothing — the gate offers no
  partial confirmation, because approving three of five would need a payload the
  proposal step never produced and a hash nothing displayed.
