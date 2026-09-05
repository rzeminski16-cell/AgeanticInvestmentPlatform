---
aer_skill: 1
key: declare_weight
kind: custom_section
title: "Weight"
version: 1
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  weight: number
  summary: string
token_budget: 4000
allowed_tools: [search_facts]
---

State the portfolio weight this company deserves, so the book can be rebalanced to it.
