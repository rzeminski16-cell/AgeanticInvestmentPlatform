# Where every figure in the angel pitch deck comes from

**`tracework-angel-pitch.pptx`** (54 slides) is the pitch for taking the platform into a
business: the problem, the product, the evidence including the rounds it lost, the
competition, the costs, the risks, and the ask. It is written to be cut down rather than
padded out, so it is deliberately comprehensive. Slide 1 is a checklist for the founder, and
is deleted before the deck is sent.

Every slide carries a provenance tag, the same discipline the product applies to every figure:

| Tag | Means |
|---|---|
| **MEASURED** | Read from the readiness audit, a measurement round or the repository |
| **READY · PER PLAN** | In `ROADMAP.md` or the V1.0 plan, and shown as delivered because the founder asked for the plan to be treated as done. Each must be confirmed as shipped before the deck is sent |
| **EXTERNAL · VERIFY** | A third party's price, funding round or feature, read on 25 September 2026 |
| **OUR JUDGEMENT** | An argument, not a measurement |
| **PLACEHOLDER** | The founder's to fill: market, pricing, go-to-market, the future, team and the ask |
| **DESIGN ILLUSTRATION** | A V1.0 artboard. Every company, name and figure in it is invented |

Rebuild the deck with `node build_pitch_deck.js tracework-angel-pitch.pptx` (needs
`pptxgenjs`). The eight screens in [`screens/`](screens/) are committed; `node
render_screens.js` regenerates them from [`../../V1.0_Alpha/design/screens/`](../../V1.0_Alpha/design/screens/)
(needs `playwright` and `sharp`). The artboards were drawn before the README settled on the
product's name, so the renderer substitutes the brand text; nothing else on them changes.

## What the deck deliberately does not do

- **It states no market size, price, forecast or roadmap.** The repository measures none of
  them, so each is a placeholder. The future beyond the V1.0 plan is left blank on purpose.
- **It does not claim to write the better note.** In all 21 comparisons across three rounds,
  the language-model judges chose the general AI note. The deck says so on four slides and
  claims what the record supports: an evidence base and a checking instrument.
- **It does not call the judges blind or human.** They were language models reading under a
  fixed rubric, and a Phase 5 test found they could name the platform's document 15 times in
  15. Every slide that quotes them says so.
- **It does not quote a figure from a design screen.** Those figures are invented.

## Figures read from the repository

The audit is [`../../plan/readiness-audit-2026-09.md`](../../plan/readiness-audit-2026-09.md)
(RA below), with its run records in [`../../plan/readiness-audit-2026-09/`](../../plan/readiness-audit-2026-09/).

| Slide | Figure | Source |
|---|---|---|
| 2, 3, 21, 32, 50 | 0 contradicted of 779 checkable figures | RA §3.2, the audit's matcher: 169 + 188 + 165 + 79, plus AZN #2's 178 from the scorecard. Precision calibrated, recall not measured (RA §9) |
| 2, 3, 21, 32, 50 | 259 of 259 citations confirmed | RA §3.2: 69 + 57 + 34 + 56, plus AZN #2's 43 from its `summary.json` |
| 2, 3, 5, 9, 21, 27, 32, 34, 36 | £7.19 a full report; £6.80–£7.61; an 11% spread | RA §3.3, the `costs` table: £7.48, £6.80, £6.83, £7.21, £7.61 |
| 4, 21, 23, 32, 36 | 65% spread for the AI baseline; £7.63, £11.03, £6.71 | RA §3.3 and the scorecard's baseline table |
| 4, 23, 50 | Baseline accuracy: 0 of 154, 1 flagged of 179, 0 of 107 | RA §3.2. The AstraZeneca flag is a capex definition the note itself discloses |
| 4, 23 | 54 of 159 links no longer resolving; no citation block | RA §3.2: 46 + 58 + 55 cited, 33 + 37 + 35 resolving the next day |
| 4 | The baseline was Claude Opus 5, high effort, with server-side web search, through the API | RA §2 |
| 21, 23, 32, 50 | 3,335 calculation rows replayed, zero divergence | RA §3.1 and each run's `replay.json`: 852 + 857 + 152 + 831 + 643 |
| 21 | 559 artefacts re-hashed intact; 36 audit events, chain unbroken | RA §3.1 |
| 21 | 28 findings, 21 fixed with a regression test | RA §1 and §5 |
| 21, 51 | The audit's verdict: accurate yes, budget-friendly yes, reliable not yet, complete no; "Not ready for general use; ready for one thing, and good at it." | RA §1, the verdict |
| 9, 23, 38 | 26.5–36.8 minutes of machine time | [`05-delivery-plan.md`](../../V1.0_Alpha/05-delivery-plan.md) §13, which corrects the audit's step times |
| 9 | 6 gates, 7 for a bank | RA §4.2 |
| 9 | 18 sections; 114–148 footnotes; 12,548–15,948 words | RA §3.4 |
| 22, 38, 51 | Model judges: 9 of 9 comparisons to the AI note, 0 of 54 verdicts to us (3 equal) | RA §4.1; `05-delivery-plan.md` §2, re-counted from `judges/reads.json` |
| 22, 51 | Phase 5: 6 of 6, and 2 of 36 verdicts to us | [`phase-5-round-2026-09/README.md`](../../plan/phase-5-round-2026-09/README.md), the verdict |
| 22, 51 | Verdict round: 6 of 6, and 0 of 36 verdicts to us | [`phase-7-round-2026-09/README.md`](../../plan/phase-7-round-2026-09/README.md), the verdict |
| 22 | Judges named the platform's document 15 times in 15 | [`phase-5-gate.md`](../../plan/phase-5-gate.md), the identity-guess row |
| 22, 24, 38, 51, 52 | The stop rule fired on 25 Sep 2026; the claim narrowed | [`ROADMAP.md`](../../plan/ROADMAP.md), "What to do next"; the verdict round's README |
| 22 | "Self-cancelling: it publishes two fair values 75% apart …"; "B repeatedly denies figures it prints elsewhere." | The verdict round's README, "The stated reasons, beside the category each was read as": the AstraZeneca operator lens, and the three reasons given against the fresh note |
| 22 | "The platform's verifiability advantage lives in the interface, not in the document it exports." | RA §4.1. This is the audit's own conclusion, not a judge's, and the slide says so |
| 22 | Self-contradiction named first by 7 of 9 judges; "state a view" weighted 0 | [`12-the-ranked-backlog.md`](../../V1.0_Alpha/12-the-ranked-backlog.md) |
| 22 | Five of nine judges named something worth keeping | The verdict round's README, "What the judges still valued" |
| 24 | The four finish-line conditions; F2, F5 and the calculator back in; about £21 of re-measurement | `ROADMAP.md`, "What to do next", the operator's decisions of 25 September 2026 |
| 13, 34, 38 | Quick report: £4.95, 31 minutes, 9 sections | [`quick-mode-readout.md`](../../plan/readiness-audit-2026-09/quick-mode-readout.md) |
| 14, 27, 34 | Refresh: £1.66, and nothing spent when nothing is new | The verdict round's README, "The refresh against F4's own test"; [`04-feature-specifications.md`](../../V1.0_Alpha/04-feature-specifications.md) F4 |
| 34 | Later runs from £5.88 to £8.40; measured on 12 runs | [`HANDOVER.md`](../../V1.0_Alpha/HANDOVER.md), the 17 Sep re-seed (£8.40, £7.00, £7.57); Phase 5 README (£7.61, £7.06); verdict round README (£6.17, £5.88) |
| 34 | Ask tiers: free, pennies, priced first and typically £1–3 | `04-feature-specifications.md` F6. These are design targets, and the slide says so |
| 20, 38, 51 | The bank path's approved M&T report: £8.40, 59 of 59 citations, revenue at $9,690m | `05-delivery-plan.md` §6, the Phase 2 exit, 17 Sep |
| 38, 51 | 3 of 6 runs approved in the audit | RA §1 and §3.1 |
| 38, 51, 52 | 1 of 3 runs approved with no rescue in the verdict round | The verdict round's README, "ISSUE 1, over the round's three runs" |
| 51 | Phase 5: AstraZeneca £7.61, Microsoft £7.06, 43 + 53 citations; both overridden at their own final gate | The Phase 5 round's README. Its Sonnet lines are overstated: the meter charged 50% high from 1 Sep (`ROADMAP.md` §3.19 item 68) |
| 51 | Verdict round: AstraZeneca £6.17, Microsoft £5.88, a refresh £1.66, a fresh baseline £12.82 | The verdict round's README |
| 37 | £63.32, £16.73 and £29.51 of live spend | RA §7, the spend ledger; the Phase 5 README; the verdict round's README (of £31.50 approved) |
| 37 | 74–94 sessions, about £80 of live spend, about 39 operator hours | `05-delivery-plan.md` §14 |
| 36 | Drafting £2.31–£4.04 of each run; web search £0.04–£0.06 | RA §3.3, "Dearest steps" and "Web searches billed" |
| 36 | Opus 5 $5 / $25, Sonnet 5 $2 / $10, Haiku 4.5 $1 / $5 per million tokens | `src/aer/providers/costs.py`, verified against the vendor's page on 24 Sep 2026 |
| 36 | Web search at $10 per 1,000 | `costs.py` and ADR 0092 |
| 36 | Caps of £12 a run and £80 a month | `src/aer/config.py`, the shipped defaults |
| 33, 34, 36, 40, 49 | EODHD at €19.99 a month, personal use; the commercial tier at $399 a month | [`../../data-sources/eodhd.md`](../../data-sources/eodhd.md) and ADR 0030 |
| 40, 49 | Coverage and licence of each source; two declined | [`../../data-sources/`](../../data-sources/), ADR 0022 (FCA NSM), ADR 0128 (a run that cannot succeed does not start) |
| 40 | SEC at about 10 requests a second; Companies House at 600 per 5 minutes | `sec-edgar.md`; `ROADMAP.md`, commercial check 2 |
| 40, 41 | Two metering errors, both fixed | RA finding F-13 (Haiku billed at Opus rates); `ROADMAP.md` §3.19 item 68 (Sonnet 50% high from 1 Sep) |
| 20 | 8,288 tests (8,054 default, 234 browser); 772 artefacts intact | [`phase-7-gate.md`](../../plan/phase-7-gate.md) |
| 20 | 141k lines in 415 modules; 144k lines of test code, 281 test modules; 131 ADRs; 87 migrations | Counted from the repository on 25 Sep 2026 (`src/`, `tests/`, `docs/adr/`, `migrations/versions/`) |
| 20, 48 | The invariants, and what enforces each | `CLAUDE.md`; [`knowledge-map.md`](../../developers/knowledge-map.md) §5 |
| 47 | The five trust zones | `knowledge-map.md` §4 |
| 7 | The loop, and "you passed at $80 because Y" | [`00-the-product.md`](../../V1.0_Alpha/00-the-product.md) §1 |
| 8 | The rule, and what each side owns | `CLAUDE.md`; ADR 0003; [`what-it-is.md`](../what-it-is.md) |
| 10, 11 | The evidence chain; the refusals | `what-it-is.md` |
| 12 | Six destinations and a command bar | [`02-information-architecture.md`](../../V1.0_Alpha/02-information-architecture.md) §3 |
| 13–19 | What each surface does | [`01-tools-and-success-criteria.md`](../../V1.0_Alpha/01-tools-and-success-criteria.md); `04-feature-specifications.md` |
| 43 | The bars already in the specification | `01-tools-and-success-criteria.md` |
| 25, 33 | "The buyer most likely to pay …"; "The target user lives in Excel."; "A full run at £7.19 is a purchase …" | ADR 0120; [`09-the-workbook.md`](../../V1.0_Alpha/09-the-workbook.md); `04-feature-specifications.md` F4 |
| 33, 35 | Fixed-price research, the methodology library, the workbook as a channel, a provable track record, replay, a second-opinion mode | `00-the-product.md` §2.8; `04-feature-specifications.md` F5; the IDEAS slide of `build_overview_deck.js` |
| 39 | "Advising a specific person on a specific investment is a regulated activity in the United Kingdom" | `00-the-product.md` §2.3; `04-feature-specifications.md` F3; ADR 0117 (no model writes a rating, target or recommendation) |
| 19, 41, 52 | Multi-user deployment decided against; F17 designed and deferred; ADR 0012 names Claude as the only provider | `ROADMAP.md` §4, "Decided against"; ADR 0120; ADR 0012 |
| 52 | The Companies House key followed a redirect on 18 Sep 2026 | `ROADMAP.md` §3.19 item 23, and "What to do next" on rotation |

## External facts, read on 25 September 2026

Secondary sources are aggregators and reviews, not the vendor. Each of these figures carries
**EXTERNAL · VERIFY** on its slide, and should be checked again in the week the deck is sent.

| Slide | Fact | Source | Kind |
|---|---|---|---|
| 5, 29 | Bloomberg Terminal, $31,980 a year for one terminal (2026) | [costbench.com](https://costbench.com/software/financial-data-terminals/bloomberg-terminal/); [godeldiscount.com](https://godeldiscount.com/blog/bloomberg-terminal-cost-2026) | Secondary |
| 5, 29 | AlphaSense, about $10,000–$40,000+ a seat a year, quote-based | [vendr.com](https://www.vendr.com/marketplace/alphasense); [spendhound.com](https://www.spendhound.com/marketplace/alphasense-pricing) | Secondary |
| 27 | AlphaSense reported at about $700M of annual recurring revenue | [enterprisedna.co](https://enterprisedna.co/resources/ai-pulse/ai-pulse-2026-08-30-alphasense-hedges-away-from-pure-seat-pricing-at-700m-arr/) | Secondary |
| 29 | Canalyst is part of AlphaSense, through Tegus (completed 8 Jul 2024) | [PR Newswire](https://www.prnewswire.com/news-releases/alphasense-completes-acquisition-of-tegus-302190934.html) | Primary |
| 29 | Daloopa: every data point linked to its source, 6,000+ tickers | [daloopa.com](https://daloopa.com/) | Primary |
| 5, 30 | Morningstar Investor, $249 a year | [traderhq.com](https://traderhq.com/morningstar-investor-review-full-analysis-benefits-tools/); [wallethacks.com](https://wallethacks.com/morningstar-investor-review/) | Secondary |
| 30 | Stockopedia, about £240–£295+ a year | [stockopedia.com/plans](https://www.stockopedia.com/plans/) (not read in full); [traderhq.com](https://traderhq.com/stockopedia-review-smart-stock-research-tools-investors/) | Secondary |
| 5, 30 | Koyfin: Free, Plus $39, Premium $79 a month | [koyfin.com/pricing](https://www.koyfin.com/pricing/) | Primary |
| 5, 30 | ChatGPT Plus, $20 a month; Pro tiers $100–$200 | [help.openai.com](https://help.openai.com/en/articles/6950777-what-is-chatgpt-plus); [eesel.ai](https://www.eesel.ai/blog/chatgpt-pricing) | Primary for Plus; secondary for Pro |
| 5, 27, 30 | Perplexity Pro, $20 a month; finance pages; portfolios through Plaid in the US and Canada | [findmymoat.com](https://www.findmymoat.com/tools/perplexity-finance); [perplexity.ai](https://www.perplexity.ai/hub/blog/answers-for-every-investor) (not read in full) | Secondary |
| 5 | Fiscal.ai Pro, $39 a month billed yearly | [toolmango.com](https://toolmango.com/tools/fiscal-ai/pricing) | Secondary |
| 27, 29 | Rogo, a $160M Series D led by Kleiner Perkins, 29 Apr 2026. The release states no valuation, so the deck quotes none | [PR Newswire](https://www.prnewswire.com/news-releases/rogo-raises-160m-series-d-to-scale-the-agentic-platform-for-finance-302756546.html) | Primary |
| 27 | Hebbia, a $130M Series B in 2024, led by Andreessen Horowitz | [Wikipedia](https://en.wikipedia.org/wiki/Hebbia) | Tertiary |
| 39 | The FCA's targeted-support regime, live from 6 Apr 2026 (PS25/22, published 11 Dec 2025) | [fca.org.uk](https://www.fca.org.uk/publications/policy-statements/ps25-22-consumer-pensions-investment-decisions-rules-targeted-support); [the boundary review](https://www.fca.org.uk/firms/advice-guidance-boundary-review) | Primary |

## Two design notes

- **The chart marks are not the interface's teal and amber.** Those two tokens read grey as a
  filled bar, so the charts on slides 5 and 36 use the same two hues stepped up (`00879B`,
  `B86E00`) until the pair passes the chart checks: chroma, colour-vision separation and
  contrast against white. All text stays in the design system's ink.
- **The file is post-processed once.** pptxgenjs writes a paragraph-properties element for every
  run, so a paragraph mixing bold and plain text carries a second one after its first run. The
  schema allows one, first. `save()` in the build script drops the extras before the file is
  written.
