---
aer_skill: 1
key: declare_recommended_weight
kind: custom_section
title: "Recommended weight"
version: 1
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  recommended_weight: number
  summary: string
token_budget: 4000
allowed_tools: [search_facts]
---

Recommend a weight for this position. Be precise; it will be used as the target.
