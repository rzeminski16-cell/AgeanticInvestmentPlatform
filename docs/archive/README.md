# Archive

Documents that are no longer the current word on their subject, kept **whole** rather than
deleted or summarised away.

They are here because most of them are records rather than instructions: a gap and the
shape of its fix are worth reading together, and a plan is worth reading beside what
actually happened to it. Nothing in this directory is authoritative. Where an archived
document and a live one disagree, the live one wins.

| Document | What it was | Replaced by |
|---|---|---|
| [`PLAN.md`](PLAN.md) | The original research, architecture and build plan, in four stages: data-source research, the product and architecture specification, the phase roadmap and the first task sequence. 2,687 lines, and the authority on scope for most of the platform's life. | [`../plan/ROADMAP.md`](../plan/ROADMAP.md) for scope; [`../developers/architecture.md`](../developers/architecture.md) and the ADRs for design. Its §2.x specification sections are still cited from docstrings across the codebase and remain readable as the original specification. |
| [`investment-os.md`](investment-os.md) | The design note that proposed turning one research tool into a platform holding several, plus a live build to-do. Explicitly a draft for discussion — "nothing here is decided until it is an ADR" — and everything in it since became ADRs 0071–0085. | [`../developers/architecture.md`](../developers/architecture.md) for the boundary; [`../plan/ROADMAP.md`](../plan/ROADMAP.md) §2–3 for the outstanding work. Its ADR references were renumbered with the rest of the tree at the merge, so they match the current records. |
| [`gap-analysis.md`](gap-analysis.md) | The honest distance between plan and reality: 94 findings, 72 closed, each kept with its reasoning. Several came from deliberately breaking invariants to see what noticed; several more from real runs against real companies. | [`../plan/ROADMAP.md`](../plan/ROADMAP.md) carries the items still open (A5, A7, A8, A55, A63, B9). The closed ones stay here — the reasoning is the value. |
| [`report-quality.md`](report-quality.md) | Whether the output *reads as research*, as opposed to whether it is correct. Eighteen findings from the CHRW note, of which one (R18) is open. | [`../plan/ROADMAP.md`](../plan/ROADMAP.md) §2.5 and §4.3. |
| [`knowledge-graph.md`](knowledge-graph.md) | What the knowledge graph is, what exists, what does not, and how to finish it. | Still cited from the source; superseded as a plan by [`../plan/ROADMAP.md`](../plan/ROADMAP.md). |
| [`phase-2-plan.md`](phase-2-plan.md) | Tasks 11–21: ingestion, provenance and point-in-time. **Complete.** | — |
| [`phase-3-plan.md`](phase-3-plan.md) | Tasks 22–32: financial data, calculations and valuation. **Complete.** | — |
| [`phase-4-plan.md`](phase-4-plan.md) | Tasks 33–43: agents, skills, custom sections, validation and the red team. **Complete.** | — |
| [`phase-5-plan.md`](phase-5-plan.md) | Tasks 44–52: report, PDF, charts, Obsidian and the review experience. **Complete.** | — |
| [`polish-phase-1.md`](polish-phase-1.md) | Tasks P1–P11, from the first complete live run: a cross-company contamination of the evidence pack, a fiscal year taken from the wrong field, and a valuation section describing a method nothing executed. Tracked separately from the gap analysis because they had a dependency order and shared root causes. **Complete.** | — |
| [`manual-verification.md`](manual-verification.md) | The phase-era setup-to-report walkthrough: install, infrastructure, the gates, a run, the controls provoked, cancelling, and walking a figure back to bytes. Accurate when written; its branch names, test counts and surfaces pre-date the merge, and it knows nothing of the Portfolio tool or the shell. | [`../developers/testing-by-hand.md`](../developers/testing-by-hand.md) |
| [`manual-acceptance.md`](manual-acceptance.md) | The things only a live run establishes — the live runs themselves, reproducing one, settings, skills, tracing, and backup and restore. Folded into the new sheet's §14–§16. | [`../developers/testing-by-hand.md`](../developers/testing-by-hand.md) |

## Why these were archived rather than deleted

Three of them describe defects that were found by methods worth repeating — mutation
sweeps over `calc/`, deliberately breaking each invariant, and reading a real report as a
reader rather than as a test. The findings are closed; the methods are not, and the
write-ups are the only place they are recorded.

The phase plans are kept because the codebase is full of docstrings citing them by task
number, and a citation that resolves to nothing is worse than one that resolves to history.

The two manual-test sheets are kept because they record *how* things were checked as much as
*what* — the two-`psql`-sessions measurement behind the cancellation design, the deliberate
invariant-breaking, the live-run findings. The new sheet carries the procedures forward; it
does not carry forward every reason.

## A note on ADR numbers

The two lines of work merged on 2026-08-23 had both claimed ADRs 0067–0070 for different
decisions. The research numbering was kept; the Investment OS records moved to 0071–0085.

`investment-os.md` was written before that split was known about, but its references were
renumbered along with the rest of the tree, so they resolve correctly. What it still carries
are its **own** historical notes — a line reading "ADRs 0071–0081 drafted" describes work
done when those records were numbered 0067–0077.

Every other document here predates the Investment OS records entirely and is unaffected.
