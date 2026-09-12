### Platform runs

| Run | Status | Spend | Wall-clock | Sections | Reliability | Accuracy (the platform's own metrics) | Accuracy (the audit's matcher) |
|---|---|---|---|---|---|---|---|
| msft1 | SUCCEEDED | £7.48 | 42 min of steps | 18/18 (degraded: none) | retries: extract#1; resumes: 0 | citations 1.00000000 of 70; metrics failing: none | 0 contradicted of 169 checkable numerals; headline missing: ev_ebitda, pe |
| msft2 | AWAITING_APPROVAL | £6.80 | 36 min of steps | 18/18 (degraded: earnings_quality) | retries: draft#1; resumes: 1 | citations 1.00000000 of 63; metrics failing: presentation_integrity | 0 contradicted of 188 checkable numerals; headline missing: ev_ebitda, pe |
| azn | SUCCEEDED | £6.83 | 31 min of steps | 18/18 (degraded: valuation_dcf, catalysts) | retries: red_team#1, revise#1; resumes: 1 | citations 1.00000000 of 35; metrics failing: none | 0 contradicted of 165 checkable numerals; headline missing: market_capitalisation, ev_ebitda, pe |

### Baselines (Opus 5, high effort, server-side web search)

| Baseline | Price | Elapsed | Size | Structure | Accuracy (the audit's matcher) | Verifiability |
|---|---|---|---|---|---|---|
| msft1 | £7.63 | 16 min | 12967 words; 25 searches | 18/18 section headings | 0 contradicted of 154 checkable numerals | 46 URLs, 33 resolved, primary share 37%, citation blocks: 0 |
| azn | £11.03 | 19 min | 12907 words; 25 searches | 18/18 section headings | 1 contradicted of 178 checkable numerals | 58 URLs, 37 resolved, primary share 41%, citation blocks: 0 |
| mtb | £6.71 | 14 min | 11225 words; 25 searches | 18/18 section headings | 0 contradicted of 106 checkable numerals | 55 URLs, 35 resolved, primary share 24%, citation blocks: 0 |

### Gate stops (as the driver met them)

| Run | Gate | Wait | Decision | Items shown |
|---|---|---|---|---|
| msft1 | PLAN | 63 s | approved | 0 |
| msft1 | PEER_SET | 63 s | approved | 0 |
| msft1 | THEME_SET | 63 s | approved | 0 |
| msft1 | FINAL | 63 s | refused | 18 |
| msft1 | UNMAPPED_CONCEPTS | 86 s | approved | 105 |
| msft1 | ASSUMPTIONS | 63 s | approved | 2 |
| msft1 | FINAL | 66 s | approved | 1 |
| msft2 | PLAN | 4 s | approved | 0 |
| msft2 | PEER_SET | 3 s | approved | 0 |
| msft2 | THEME_SET | 3 s | approved | 0 |
| msft2 | UNMAPPED_CONCEPTS | 11 s | approved | 105 |
| msft2 | ASSUMPTIONS | 3 s | approved | 2 |
| msft2 | FINAL | 5 s | refused | 1 |
| azn | PLAN | 73 s | approved | 0 |
| azn | PEER_SET | 64 s | approved | 0 |
| azn | THEME_SET | 63 s | approved | 0 |
| azn | UNMAPPED_CONCEPTS | 69 s | approved | 51 |
| azn | ASSUMPTIONS | 64 s | approved | 5 |
| azn | FINAL | 10 s | approved | 6 |

### Estimate against actual, per paid step

| Run | Step | Estimate £ | Actual £ | Ratio |
|---|---|---|---|---|
| msft1 | plan | 0.20 | 0.1537 | 0.77 |
| msft1 | critique_plan | 0.30 | 0.2873 | 0.96 |
| msft1 | propose_peers | 0.02 | 0.0141 | 0.70 |
| msft1 | propose_themes | 0.02 | 0.0120 | 0.60 |
| msft1 | research_company | 0.30 | 0.4660 | 1.55 |
| msft1 | research_industry | 0.30 | 0.5329 | 1.78 |
| msft1 | research_macro | 0.30 | 0.3105 | 1.04 |
| msft1 | research_recent_developments | 0.30 | 0.2584 | 0.86 |
| msft1 | research_technical_context | 0.30 | 0.1348 | 0.45 |
| msft1 | propose_assumptions | 0.10 | 0.0501 | 0.50 |
| msft1 | draft | 5.00 | 3.7138 | 0.74 |
| msft1 | validate | 0.02 | 0 | 0.00 |
| msft1 | red_team | 0.35 | 0.3936 | 1.12 |
| msft1 | revise | 1.50 | 1.0917 | 0.73 |
| msft1 | verdict | 0.10 | 0.0109 | 0.11 |
| msft1 | brief_challenges | 0.20 | 0.0454 | 0.23 |
| msft2 | plan | 0.20 | 0.1766 | 0.88 |
| msft2 | critique_plan | 0.30 | 0.3206 | 1.07 |
| msft2 | propose_peers | 0.02 | 0.0132 | 0.66 |
| msft2 | propose_themes | 0.02 | 0.0121 | 0.60 |
| msft2 | research_company | 0.30 | 0.4052 | 1.35 |
| msft2 | research_industry | 0.30 | 0.3257 | 1.09 |
| msft2 | research_macro | 0.30 | 0.4364 | 1.45 |
| msft2 | research_recent_developments | 0.30 | 0.2242 | 0.75 |
| msft2 | research_technical_context | 0.30 | 0.0837 | 0.28 |
| msft2 | propose_assumptions | 0.10 | 0.0602 | 0.60 |
| msft2 | draft | 5.00 | 2.3111 | 0.46 |
| msft2 | validate | 0.02 | 0 | 0.00 |
| msft2 | red_team | 0.35 | 0.3048 | 0.87 |
| msft2 | revise | 1.50 | 1.3423 | 0.89 |
| msft2 | verdict | 0.10 | 0.0022 | 0.02 |
| msft2 | brief_challenges | 0.20 | 0.0100 | 0.05 |
| azn | plan | 0.20 | 0.1497 | 0.75 |
| azn | critique_plan | 0.30 | 0.3420 | 1.14 |
| azn | propose_peers | 0.02 | 0.0153 | 0.76 |
| azn | propose_themes | 0.02 | 0.0120 | 0.60 |
| azn | research_company | 0.30 | 0.3407 | 1.14 |
| azn | research_industry | 0.30 | 0.5735 | 1.91 |
| azn | research_macro | 0.30 | 0.2716 | 0.91 |
| azn | research_recent_developments | 0.30 | 0.2707 | 0.90 |
| azn | research_technical_context | 0.30 | 0.2247 | 0.75 |
| azn | propose_assumptions | 0.10 | 0.0501 | 0.50 |
| azn | draft | 5.00 | 2.8337 | 0.57 |
| azn | validate | 0.02 | 0 | 0.00 |
| azn | red_team | 0.35 | 0.3044 | 0.87 |
| azn | revise | 1.50 | 0.9748 | 0.65 |
| azn | verdict | 0.10 | 0.0123 | 0.12 |
| azn | brief_challenges | 0.20 | 0.1158 | 0.58 |
