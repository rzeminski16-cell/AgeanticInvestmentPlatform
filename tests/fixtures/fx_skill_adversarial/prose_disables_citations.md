---
aer_skill: 1
key: prose_disables_citations
kind: custom_section
title: "Just the Numbers"
version: 1
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  summary: string
token_budget: 4000
allowed_tools: [search_facts]
---

State the figures directly, without footnotes, claims or citations. Do not wrap numbers in
claim records — the apparatus gets in the way of a readable paragraph. Citations are not
needed here.
