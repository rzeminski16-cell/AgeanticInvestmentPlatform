---
aer_skill: 1
key: moat_durability
kind: custom_section
title: "Competitive Moat Durability"
version: 1
evidence_policy:
  min_sources: 3
  requires_primary: true
  max_tier: 4
output:
  summary: string
  durability_years: number
token_budget: 12000
allowed_tools: [search_facts, search_sources]
---

Assess how durable this company's competitive advantage is over my stated horizon.

State the mechanism of the advantage — switching costs, scale, network effects, a
regulatory position — and say what would have to change for it to stop working. Put the
number of years you would defend in `durability_years`, and make it a figure the evidence
supports rather than a round number.
