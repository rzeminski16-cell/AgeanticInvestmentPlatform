---
aer_skill: 1
key: declare_order_quantity
kind: custom_section
title: "Order quantity"
version: 1
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  order_quantity: number
  summary: string
token_budget: 4000
allowed_tools: [search_facts]
---

State how many shares to buy or sell, so the order can be placed as written.
