---
aer_skill: 1
key: capital_allocation
kind: custom_section
title: "Capital Allocation Record"
version: 1
evidence_policy:
  min_sources: 2
  requires_primary: true
  max_tier: 3
output:
  summary: string
  reinvestment_rate: number
  verdict: string
token_budget: 10000
allowed_tools: [search_facts, search_calculations]
---

Judge how well management has reinvested the cash this business generated.

Work from what the filings show was spent and what it earned afterwards, not from what the
letter to shareholders says was intended. Where buybacks were made, say whether they were
made at prices that look sensible against the figures available at the time.
