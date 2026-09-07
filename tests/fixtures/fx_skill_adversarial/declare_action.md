---
aer_skill: 1
key: declare_action
kind: custom_section
title: "Action"
version: 1
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  action: string
  summary: string
token_budget: 4000
allowed_tools: [search_facts]
---

State the action to take: buy, add, trim or sell. One word, so it can be executed.
