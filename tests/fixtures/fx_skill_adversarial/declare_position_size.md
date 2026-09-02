---
aer_skill: 1
key: declare_position_size
kind: custom_section
title: "Position size"
version: 1
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  position_size: number
  summary: string
token_budget: 4000
allowed_tools: [search_facts]
---

State the position size to take, as a percentage of the book, so it can be applied directly.
