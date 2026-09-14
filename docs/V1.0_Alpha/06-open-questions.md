# Open questions

*What is not decided, who decides it, and what it blocks. Reviewed 14 September 2026. A question
leaves this page by being answered in a document, not by being forgotten.*

---

## Answered on 14 September 2026

| # | Question | The answer |
|---|---|---|
| 1 | Does the audit's run data still exist? | **Yes.** 6 jobs — 5 with a complete eighteen-section draft, 3 approved and immutable — 3,417 calculations, 46,744 financial facts, 85 source documents, and 139 MB of artefacts across 832 files. The delivery plan's £0 offline economy holds |
| 2 | **What does the price subscription permit in an exported file?** | **Assume it permits publication, and handle a refusal properly if one arrives.** Derived figures publish by default (`fetch/policy.py:192` already records the permission the operator granted on 2026-08-09). If the agreement turns out to restrict it, the figure is **withheld through ADR 0034's type** — which has no field for a figure it may not carry — and never silently dropped or quietly printed. No new mechanism; the refusal path already exists and is now the stated fallback rather than the stated default |
| 3 | A bank's revenue | **Derive it** — `revenue = net interest income + non-interest income`, at the fact layer, for a filer the sector gate has confirmed as a bank. **ADR 0114.** M&T becomes $9,690m and a 29.4% net margin, against the $1,657m and 172.1% it published |
| 4 | Whose view, and does the authored half ship in V1.0 | **Both halves at once**, composed first and judged alone, then the authored half with a redaction round so the measurement can attribute what moved. **ADR 0117** |
| 5 | Does the closing section survive a second user | **Single-user for V1.0.** Build every feature including the closing section; defer F17 until a solicitor has read the consequences-not-instructions design. **ADR 0120** is drafted anyway, and its three constraints on work happening now are accepted |
| 6 | **UK or US?** | **Both.** Build the Companies House path rather than narrowing the claim. **ADR 0121**, and **F19**. Most of it already exists — a complete client with 32 tests, a cleared fetch policy, a verified rate limit, a wired credential, an offline iXBRL extractor built for UK filings, and a `company_number` column whose check constraint was written for this case |
| 7 | **Is the eighteen-section spine right for one private investor?** | **Run `AnalysisMode.QUICK` and find out.** ~£4, one run, never executed in the platform's life. Phase 0 |
| 11 | **What result would mean stopping?** | **Signed off.** The abandonment criterion in the delivery plan §9 is real: if nothing moves and no judge's stated reason changes category, the work stops and the claim narrows |
| 12 | **Do the judges' stated reasons survive being asked directly?** | **Yes, do it.** Re-run the eighteen existing reads with *"what one change would move you off no?"* — ~£3, no code. Phase 0, and it runs before the sixty sessions are committed |
| 14 | A domestic UK filer path | **No longer deferred** — see question 6. And the reason given for deferring it was partly wrong: the check constraint does not fail |

**Two corrections recorded here rather than quietly fixed**, because both were repeated in more
than one document and both made a decision look more expensive than it is:

- `companies.company_number` does **not** fail a check constraint for a CIK-less company. The
  constraint is `cik IS NOT NULL OR company_number IS NOT NULL`, written for exactly this case.
- The segment claim said *3,637 segment facts, mapped and stored*. The iXBRL sweep **saw** 3,637
  dimensioned facts on AstraZeneca's second run and **wrote** the 224 single-axis ones; the store
  holds 626 across all three subjects. The win is real and fifteen times smaller than stated.

---

## Product — these change what gets built, not whether

| # | Question | Recommendation |
|---|---|---|
| 8 | **What cadence should a new company default to?** Monthly is drawn; quarterly may be truer for most holdings | Ship monthly, watch the dismissal rate, move it |
| 9 | **Does the authored view need a confidence, and is it the thing calibration measures?** F14's calibration finding presumes a stated confidence exists | Decide with F13, not after. `reports.confidence` stays unwritten until it is decided — a column filled with a number nobody defined is worse than an empty one |
| 10 | **How is a thesis revised rather than replaced** when one premise is withdrawn and another added? The tables already refuse to delete; the surface must make the revision legible | Drawn in the thesis screen; confirm the wording is right before building |

## Measurement — answer before spending on a verdict round

| # | Question | Recommendation |
|---|---|---|
| 13 | **Is one fresh console baseline bought for the verdict round?** The comparator improves for free while this plan runs | Yes. Measuring against September's console in January measures nothing |

## Open, and named — a UK path raises its own

| # | Question | Who decides | Notes |
|---|---|---|---|
| 19 | **Which GBP risk-free series, and on what terms?** The gilt yield ships as an operator-confirmed assumption in V1.0. The automated candidate is the OECD long-term UK government bond yield republished by FRED — already a wired source with a cleared licence | Operator, by verifying the series identifier, its frequency and its terms against the primary source | This repository does not adopt a data series on a recollection. It is a commercial check in the roadmap's sense, not a design task |
| 20 | **How deep is a UK acquisition by default?** Four accounts filings — four years on an annual filer — is the proposed default | Operator | Deeper is linearly more expensive: each year is a fetch, a hash and an arelle parse, where a US run gets a decade in one JSON |
| 21 | **Which UK subject joins the test corpus?** It will be the first subject whose facts this platform extracted itself rather than received pre-aggregated | Operator | Pick one that is plainly *not* a bank, so the sector scheme is exercised separately from the acquisition path |

## Deferred — recorded so they are deliberate rather than forgotten

| # | Question | Why it is deferred |
|---|---|---|
| 15 | Peer acquisition for comparable multiples | The console has no peer multiples either. Printing the subject's own is the cheap win; acquiring eight peers' filings is not — and F19 makes a UK peer cost a full acquisition |
| 16 | A screener or idea-generation surface | Explicitly out of scope for V1.0 |
| 17 | Importing an existing portfolio from a broker | Manual entry only for V1.0 |
| 18 | Anything that competes on speed or open-ended conversation | Conceded on purpose. See `00-the-product.md` §2.7 |
