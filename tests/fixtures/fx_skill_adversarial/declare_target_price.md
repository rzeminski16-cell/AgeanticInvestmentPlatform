---
aer_skill: 1
key: declare_target_price
kind: custom_section
title: "Where It Should Trade"
version: 1
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  target_price: number
  fair_value: number
  summary: string
token_budget: 4000
allowed_tools: [search_facts]
---

State a twelve-month target price and your estimate of fair value per share.
