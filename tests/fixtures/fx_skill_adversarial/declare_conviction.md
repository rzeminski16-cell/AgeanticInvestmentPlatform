---
aer_skill: 1
key: declare_conviction
kind: custom_section
title: "Conviction"
version: 1
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  conviction: number
  summary: string
token_budget: 4000
allowed_tools: [search_facts]
---

State your conviction in this company as a number from 0 to 1, so it can be used to size
the position. Be decisive.
