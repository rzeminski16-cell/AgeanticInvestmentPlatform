# Open questions

*What is not decided, who decides it, and what it blocks. Reviewed 14 September 2026. A question
leaves this page by being answered in a document, not by being forgotten.*

---

## Blocking — a feature cannot be built until these are answered

| # | Question | Who decides | Blocks | Recommendation |
|---|---|---|---|---|
| 1 | **Does the audit's run data still exist?** Five job databases and the artefact store. Every step costed at £0 assumes a report can be re-rendered from its own stored rows | Check, not a decision | Most of F8's acceptance, and the delivery plan's whole offline economy | Check before anything else. If it is gone, buy one seeding run and say so |
| 2 | **What does the price subscription permit in an exported file?** `fetch/policy.py` records that derived figures may be published; `render/document.py` is typed as though they may not | Operator, by re-reading the agreement | F8's multiples, the workbook's price-derived rows | Name a default so work continues if the answer takes weeks |
| 3 | **A bank's revenue** — derive it as net interest income plus non-interest income, withhold it, or leave the guard to refuse | Operator; it is a statement about what a bank's revenue *is* | Any bank producing an approvable report | Derive it, at the fact layer, with an ADR. A calc-layer fix clears four of eleven findings |
| 4 | **Whose view does the report state, and does the authored half ship at all in V1.0?** | Operator | F13, and F2 downstream of it | Composed half first, judged alone. The operator's own view second |

## Legal — answer before anybody who is not the author uses this

| # | Question | Notes |
|---|---|---|
| 5 | **Does the closing section (F3) survive contact with a second user?** A recommendation tailored to a specific person's portfolio, horizon and planned weight is much closer to a personal recommendation than a research note is, and advising a specific person on a specific investment is a regulated activity in the United Kingdom | Consequences-not-instructions is the design that keeps it on the right side of the line. **It is not a substitute for advice.** This is a solicitor's question, and it gates F17 rather than F3 |
| 6 | **What does the platform claim in its own documents?** "UK or US" is written in the product documentation and a domestic London listing cannot be researched at all | Either narrow the claim or build the path. Narrowing is a sentence; building is a source adapter and an ADR |

## Product — these change what gets built, not whether

| # | Question | Recommendation |
|---|---|---|
| 7 | **Is the eighteen-section spine right for one private investor?** `AnalysisMode.QUICK` exists, drops nine sections, scales budgets to 0.6, and **has never been run** | £4 answers the largest unasked question in this plan. Run it, read it, decide |
| 8 | **What cadence should a new company default to?** Monthly is drawn; quarterly may be truer for most holdings | Ship monthly, watch the dismissal rate, move it |
| 9 | **Does the authored view need a confidence, and is it the thing calibration measures?** F14's calibration finding presumes a stated confidence exists | Decide with F13, not after |
| 10 | **How is a thesis revised rather than replaced** when one premise is withdrawn and another added? The tables already refuse to delete; the surface must make the revision legible | Drawn in the thesis screen; confirm the wording is right before building |

## Measurement — answer before spending on a verdict round

| # | Question | Recommendation |
|---|---|---|
| 11 | **What result would mean stopping?** The delivery plan carries an abandonment criterion; it needs the operator's sign-off to be real | Pre-register it. A gate that cannot fail is not a gate |
| 12 | **Do the judges' stated reasons survive being asked directly?** Re-running the eighteen existing reads with *"what one change would move you off no?"* costs about £3 and no code | Do it first. It converts inference into a ranked backlog before sixty sessions are committed |
| 13 | **Is one fresh console baseline bought for the verdict round?** The comparator improves for free while this plan runs | Yes. Measuring against September's console in January measures nothing |

## Deferred — recorded so they are deliberate rather than forgotten

| # | Question | Why it is deferred |
|---|---|---|
| 14 | A domestic UK filer path | A source adapter, a company-number column that fails a check constraint today, a classification scheme that shares no prefix with the current one, and an ADR |
| 15 | Peer acquisition for comparable multiples | The console has no peer multiples either. Printing the subject's own is the cheap win; acquiring eight peers' filings is not |
| 16 | A screener or idea-generation surface | Explicitly out of scope for V1.0 |
| 17 | Importing an existing portfolio from a broker | Manual entry only for V1.0 |
| 18 | Anything that competes on speed or open-ended conversation | Conceded on purpose. See `00-the-product.md` §2.7 |
