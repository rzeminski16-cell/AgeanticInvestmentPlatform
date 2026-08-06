---
aer_skill: 1
key: declare_recommendation
kind: custom_section
title: "Action This Day"
version: 1
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  recommendation: string
  summary: string
token_budget: 4000
allowed_tools: [search_facts]
---

End with a one-word recommendation: buy, hold or sell.
