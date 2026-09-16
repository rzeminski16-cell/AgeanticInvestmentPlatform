# The source map — where the console's decisive figures came from, and whether the platform already has them

*Phase 0.2 of the V1.0 delivery plan, done 16 September 2026 for £0 from committed records only:
the eighteen judge reads and nine blind comparisons in [`judges/reads.json`](judges/reads.json),
the eighteen re-reads in [`judges/rereads.json`](judges/rereads.json), the three console notes'
own source appendices, and the `acquire` step's output in the committed run exports
([`msft2/run-export.json`](msft2/run-export.json), [`azn2/run-export.json`](azn2/run-export.json),
[`mtb/run-export.json`](mtb/run-export.json)). Nothing here was fetched; the stored database is
not in this container, so "in the record" means "in the run's own acquisition list".*

**The question.** For each figure a judge called decisive on a console note — the *first thing
to check*, the *strongest paragraph*, the evidence behind a focus question answered "yes", and
the changes the re-read ranked first — which document did the console read, and is that
document already inside what the platform fetched?

**What the platform fetches, read from `services/filings.py::_wanted`.** The latest annual
report (`10-K`, `10-K/A`, `20-F`, `20-F/A`, `40-F`); the quarterlies filed *after* it, so a
10-Q the annual has since covered is deliberately absent; and the most recent current reports
(`8-K`, `6-K`) by filing date — five, on every committed run — with no regard to what a current
report contains. Every one of them as its **primary document only**: `Filing.url()`
(`sources/sec/submissions.py:82-91`) builds the primary document's URL and nothing enumerates an
accession's other documents. Facts come from EDGAR's companyfacts JSON, not from the documents.

---

## 1 · Microsoft (judged: MSFT #1 against the console note)

| Decisive figure, and who called it decisive | The console's source | Document and accession | In the run's record? | What stands between the platform and it |
|---|---|---|---|---|
| **Intelligent Cloud gross margin 62.2% → 58.0%, segment cost of revenue +44.1% against revenue +29.7%** — the *strongest paragraph* for all three lenses, and the whole answer to focus question 1 | [S5] Form 8-K Exhibit 99.1 "Segment History as Reported" | Accession `0001193125-26-380280`, the 8-K of 2 September 2026 | **The accession, yes** — the run fetched its primary document `d291965d8k.htm`. **The exhibit, no** | F7 item 1: enumerate the accession's documents and fetch EX-99 |
| **FY26 depreciation and amortisation** — the *first thing to check* for all three lenses, because the console never read it and built a $24–44bn range from two proxies | [S34] stock-analysis-on.net, [S7] the Q3 FY26 10-Q; the console admits its printed formula does not produce its stated result | FY26 10-K cash flow statement, accession `0001193125-26-323660` | **Yes** — the 10-K primary document is in the record, and companyfacts is the fact source (the audit's F-11 fix already records `free_cash_flow` from the mapped operating cash flow and capex) | Nothing on the acquisition side; whether the D&A concept reaches a section is F8's territory (print what is held) |
| **The 30 June 2026 balance sheet, finance lease liabilities and $329.1bn of uncommenced leases** — *provenance* is the category the console's judges rank first, and three of three re-reads ask for exactly this | [S36] Yahoo Finance, [S29] btw.media, [S30] a Substack; the note admits a $62bn-vs-$16.53bn finance-lease contradiction it cannot reconcile | FY26 10-K balance sheet and leases note, the same accession | **Yes** — the balance sheet is in companyfacts and the leases note is in the acquired 10-K | Nothing on the acquisition side. The uncommenced-lease figure is narrative in the leases note: F7 item 3, "the narrative already fetched and barely read" |
| **FY27 guidance** — Q1 FY27 revenue $89.85–90.95bn, Azure about +45% in constant currency, capex to grow; every re-read asks for it to be re-sourced from the CFO | [S25] CNBC, [S31] a blog, [S26] GuruFocus, [S4] the CFO's prepared remarks on the investor-relations site | The prepared remarks and transcript are on microsoft.com, not on EDGAR; the Q4 figures are Exhibit 99.1 on the 8-K of 29 July 2026, accession `0001193125-26-323632` | **The 8-K's primary document, yes; the exhibit, no; the IR pages, no** | F7 items 1, 2 and 4: the exhibit, the transcript, and the issuer adapter that exists and is constructed nowhere |
| **Q4 FY26 results** (press-release figures) | [S1] the IR press release, [S6] the same 8-K's Exhibit 99.1 | Accession `0001193125-26-323632` | Accession yes, exhibit no | F7 item 1 |
| **Microsoft Cloud gross margin 69% → 68/67/66%** across FY26 | [S9] the FY25 10-K, [S11–S13] IR quarterly performance pages | FY25 10-K, accession `0000950170-25-100235`; the IR pages | **No** — only the latest annual's document is fetched (the numbers are in companyfacts; the prior year's narrative is not), and no IR page is | F7 items 3 and 4 |
| **Capital allocation** — cash capex $115.95bn, the buyback, the $0.91 dividend | [S2] the 10-K, [S19] stockanalysis.com, [S28] Data Center Dynamics, [S15/S16] news.microsoft.com | The FY26 10-K | **Yes** | Nothing; the platform scored this row *present-sourced* on its own |
| **Item 1A risk factors** — *absent* from the console on both lenses' checklist | — | The FY26 10-K | **Yes**, and printed: the platform is ahead on this row unanimously | The platform's advantage, not a gap |
| **Named competitors' figures** — AWS and Google Cloud growth and margin, AMZN/GOOGL multiples | [S17] Yahoo (share prices only); the console's own table reads "not sourced" | Amazon's and Alphabet's own 10-K/10-Q | **No** — peers' filings are not acquired | A decision, not a gap: open question 15, deferred |
| **The 10-year Treasury yield** (4.95%) and the equity risk premium | [S23] Trading Economics, [S44] the Federal Reserve's H.15 | FRED `DGS10` | The macro stack is built, licence-cleared and has no caller | Phase 1.6 |
| **The share price and market capitalisation** ($493.16, 10 September) | [S17] Yahoo | The price subscription | Present on MSFT #1; lost on a warm database on the later runs (audit F-22's neighbour, Phase 4.1) | Phase 4.1 |

**Reading the table.** Of the nine decisive figures the console got from a primary source,
**seven are inside an accession or a fact set the platform already holds**, and the two it does
not hold (the FY25 10-K's narrative, the IR pages) are a selection rule and an unconstructed
adapter, not an evidence-policy question. Nothing decisive was T5.

## 2 · AstraZeneca (judged: AZN #1 and AZN #2 against one console note)

| Decisive figure, and who called it decisive | The console's source | Document and accession | In the run's record? | What stands between the platform and it |
|---|---|---|---|---|
| **Which products lose exclusivity between September 2026 and September 2027** — the *first thing to check* for five of six lenses; the console's own note says it could not read the schedule | Appendix #21, "Patent Expiries of Key Marketed Products" as at 10 February 2026, a PDF on astrazeneca.com | Not on EDGAR; an issuer document | **No** | F7 item 4 (the issuer adapter) and item 6 (the drug-approval register). The console could not read it either, which is why this is the one row where reading it would beat both documents |
| **Farxiga's US-only FY2025 revenue** — the second thing to check for four lenses; the console applied a global figure to a US event | Appendix #2, the 20-F's Exhibit 15.1 (the Annual Report and Form 20-F Information), Note 2's regional table | Accession `0001104659-26-019130`; the exhibit is `azn-20251231xex15d1.htm` | **The accession, yes** — the run fetched the 20-F's primary document. **The exhibit, no** — and for AstraZeneca the primary document is a wrapper; the annual report *is* the exhibit | F7 item 1, and it matters more for a 20-F filer than for a 10-K filer |
| **Revenue by geography and by product** (Tagrisso $7,254m, Imfinzi $6,063m, Calquence $3,518m …) | Appendix #2 (Ex. 15.1) and #4, the annual-report extract furnished as Exhibit 99.1 on the 6-K of accession `0001104659-26-019110` | The 20-F exhibit; the 6-K exhibit | The platform's own iXBRL sweep stored **96 revenue-by-geography facts** from this filing and no writer has seen them (ADR 0118); product-level rows are narrative in the exhibits | ADR 0118's carve-out for geography; F7 item 1 for product level |
| **FY2025 owner cash flow** — OCF $14,575m, capex $3,270m, non-capex investing $3,955m, the *strongest paragraph* for two lenses | Appendix #3, the FY and Q4 2025 results announcement furnished on a 6-K (`a3234s.htm`, accession `0001654954-26-001073`, 10 February 2026); #4 the annual-report extract | 6-K of 10 February 2026 | **No.** The run took the five most recent 6-Ks by date, all of them from September 2026: two clinical announcements, two admissions to trading and a total-voting-rights notice. The February results release fell outside the window | The current-report rule selects by recency, not by content. A results announcement and a voting-rights notice are the same form. F7 item 2, and it is small |
| **Net debt $26,912m at 30 June 2026, the A1/A+ ratings, interest cover** — focus question 3 answered "yes" | Appendix #5, the H1 2026 half-year report on a 6-K (`azn-20260630x6k.htm`, accession `0001104659-26-086846`, 27 July 2026) | 6-K of 27 July 2026 | **No**, for the same reason | Same |
| **The cost of equity** — 8.5% assumed against a market-implied 7.2%; "the rating is one assumption deep"; every re-read asks for a sourced build | The note's own assumption over the company's 4.9% bond coupons (appendix #22) | A risk-free series, a beta, an equity risk premium | The platform computes its WACC from operator-confirmed assumptions with provenance; AZN #2 discounted at 5.83% on a 0.4 beta the operator supplied | Phase 4.2 (market equity in the WACC) and the assumption gate's provenance; the macro stack (Phase 1.6) for the risk-free leg |
| **The share price** ($159.64), the sterling conversion and the ADR ratio — the *first thing to check* for the azn2 operator | CNN and Robinhood quote pages | The price subscription | Withheld on both AZN runs (Phase 4.1); the per-share basis is audit F-28 | Phases 4.1 and 4.4 |
| **The 20-F's own risk factors (Item 3.D) and the legal-proceedings note** — *absent* from the console | — | The 20-F and its exhibit | **Yes**, and printed | The platform's advantage |
| **Peer multiples** (Merck, Novartis, Roche, GSK, Sanofi) | — | Peers' filings | **No** | Deferred by decision (open question 15) |

**Reading the table.** The console's two strongest AstraZeneca planks — the cash-flow table and
the net-debt answer — come from **two 6-Ks the platform's own rule skipped in favour of five
September notices**, and its patent-cliff answer, which every judge called the first thing to
check, comes from an issuer PDF neither side read properly. The product breakdown the judges
wanted sits in the exhibit of an accession the platform opened.

## 3 · M&T Bank (not judged: the platform's report was refused at gate 2)

The console note's primary sources, mapped the same way for when the bank is judged:

| Source | Accession | In the run's record? |
|---|---|---|
| Q2 2026 results, EX-99.1 press release (S4) | `0000036270-26-000046`, the 8-K of 15 July 2026 | Accession yes (primary document); exhibit no |
| 10-Q for the quarter ended 30 June 2026 (S7) | `0000036270-26-000050` | **Yes** |
| 10-Q for the quarter ended 31 March 2026 (S8, via a third-party copy) | `0000036270-26-000034` | **Yes** |
| 10-K for FY2025 (S9) | `0000036270-26-000010` | **Yes** |
| Q4 and FY2025 results, EX-99.1 (S12) | `0000036270-26-000004`, the 8-K of 16 January 2026 | No — older than the five most recent 8-Ks |
| Q1 2025 investor presentation furnished on an 8-K (S24) | `0001628280-25-009865` | No — same |
| The 2026 proxy statement (S25) | A `DEF 14A`, on the IR site | No — no form set includes a proxy, which is also why "management or board changes" is *absent* on both sides of every comparison |
| Earnings-call transcripts (S14, S15, S41) | Third-party transcript sites | No; the primary is the company's own webcast, an issuer document |

## 4 · What the map settles

1. **Delivery plan §2's finding 2 holds, and is now measured rather than remembered.** Nothing
   decisive was T5. Every decisive figure the console took from a primary source is inside an
   accession the platform opened (Microsoft), inside a filing type it already fetches
   (AstraZeneca's results 6-Ks), or already in its fact store. The gap is exhibits and
   selection, not evidence policy, and the tier-ceiling decision stays out of the plan.
2. **Three acquisition changes, in order of value, all small.**
   - **Exhibits inside accessions already opened** (F7 item 1): EX-99.1 on the 8-K carries the
     segment history, the quarterly statements and a bank's results release; Exhibit 15.1 on the
     20-F *is* AstraZeneca's annual report. One accession-listing call per filing.
   - **Current reports selected by what they are, not when they were filed** (F7 item 2): a
     results announcement furnished as a 6-K or an 8-K Item 2.02 must not lose its place to a
     total-voting-rights notice. The five-most-recent rule cost AstraZeneca both its results
     releases.
   - **The issuer adapter, constructed** (F7 item 4): the patent-expiry schedule, the prepared
     remarks, the quarterly performance pages. The one row where reading the document would beat
     both sides is an issuer PDF.
3. **Four decisive figures the platform already holds and does not print** — Microsoft's D&A,
   its leases note, its capital allocation, AstraZeneca's geography breakdown — belong to F8
   and Phase 4, not to acquisition. The judges' *provenance* ranking is what the platform
   already does best; the audit's "holds more than it shows" is confirmed from the comparator's
   side.
4. **Two are decisions, not gaps**: peers' filings (open question 15, deferred) and the discount
   rate's build (operator-owned assumptions, with the macro stack wired in Phase 1.6).
5. **One new small item for F7's list**: the proxy statement. `DEF 14A` is in no form set, and
   "management or board changes in the last year" is *absent* on both sides of all nine
   comparisons. It is one form name and the same fetch path.
