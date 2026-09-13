# Where every figure in the deck comes from

`why-a-research-record.pptx` is an investor deck arguing one thing: the platform is not a
better chat, it is a different artefact — a research record that re-executes, resolves to
hashed bytes and costs a number you can read off a ledger.

Nothing on a slide is estimated, projected or rounded in our favour. Every figure is read
from [the readiness audit of 12 September 2026](../../plan/readiness-audit-2026-09.md) or
from the run records committed beside it in
[`readiness-audit-2026-09/`](../../plan/readiness-audit-2026-09/). This file is the map, so
anybody in the room can be handed the source of any number within a minute.

Rebuild the deck with `node build_deck.js why-a-research-record.pptx` (needs `pptxgenjs`).

| Slide | Figure | Source |
|---|---|---|
| 1, 4, 12 | 0 contradicted of 779 checkable figures, five reports | §3.2, the audit's own matcher row — 169 + 188 + 165 + 79 checkable, plus AZN #2 |
| 1, 4, 5, 12 | 259 of 259 citations verified | §3.2 `citation_accuracy`; replay re-verified 69 + 57 + 34 + 43 + 56 |
| 1, 5, 12 | 3,335 calculation rows re-executed, zero divergence | `replay.json` per run: 852 + 857 + 152 + 831 + 643, each with `calculations_diverged: 0` |
| 1 | £63.32 of measured spend, six live runs, three baselines | §7, the spend ledger |
| 2 | 159 URLs cited; 54 no longer resolved | §3.2 — 46 + 58 + 55 cited, 33 + 37 + 35 still resolving when fetched |
| 2 | No response carried a citation block | §3.2 — the console's references are the model's own prose |
| 2, 6 | 65 % console spread, £6.71 to £11.03 | §3.3 — the three baselines cost £7.63, £11.03, £6.71 |
| 3 | The one rule, and what each side owns | `CLAUDE.md`, the repository's first convention |
| 4 | Console accuracy: 0 of 154, 0 of 107, 1 flagged of 179 | §3.2 — the AstraZeneca flag is a capex definition the note itself discloses |
| 4 | The independent recomputation | §2 and §3.2 — the basket rebuilt from SEC company facts |
| 5 | 559 artefacts re-hashed intact; 36 audit events, chain unbroken | §3.1 |
| 5 | AstraZeneca #1 152 rows, #2 831 rows | §3.4 and F-12 — ten IFRS tags now map, restoring 679 rows |
| 6 | £7.48, £6.80, £6.83, £7.21, £7.61; average £7.19; 11 % spread | §3.3 |
| 6 | Cap never raised, no run breached | §3.3 — each request capped at £10 with one automatic raise available |
| 7 | M&T: 643 calculations, 18 sections, 11 impossible relations, refused | §3.2, §3.4 and F-24 |
| 8 | The judges' answers and the six blind comparisons | §4.1 — twelve reads and six comparisons, rubrics in `judges/reads.json` |
| 9 | What we lack, each with its named fix | §8, items 1, 2, 12 and the completeness gaps in §3.4 |
| 10 | 28 findings, 21 fixed; £63.32 of £100 | §1, §5 and §7 |
| 10 | 6,968 automated tests | §9's closing run of the suite on the final tree |
| 11 | £7.48, 42 minutes, 18 sections, 142 footnotes; 831 calculations, 5.83 % WACC | §3.4, §4.2 and §4.3 |
| 11 | The four things it cannot do yet | §4.3, the suitability matrix |

## Two things the deck deliberately does not do

- **It does not claim to write the better note.** Six of six blind comparisons chose the
  console, and slide 8 says so in the judges' own words. An investor who discovers that
  later stops believing the rest.
- **It states no ask.** There is no valuation, no raise and no forecast anywhere in it,
  because the audit measured none of those things.
