---
aer_skill: 1
key: declare_stop_loss
kind: custom_section
title: "Stop loss"
version: 1
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  stop_loss: number
  summary: string
token_budget: 4000
allowed_tools: [search_facts]
---

State the price at which the position should be sold, so a stop can be set.
