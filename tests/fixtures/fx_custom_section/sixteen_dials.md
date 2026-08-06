---
aer_skill: 1
key: sixteen_dials
kind: custom_section
title: "Sixteen Dials"
version: 1
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  d01: string
  d02: string
  d03: string
  d04: string
  d05: number
  d06: number
  d07: string
  d08: string
  d09: number
  d10: string
  d11: string
  d12: number
  d13: string
  d14: string
  d15: number
  d16: string
token_budget: 12000
allowed_tools: [search_facts]
---

A deliberately wide dashboard: sixteen short observations, one per dial. This is the
contract-size ceiling, on purpose — the platform must render it, not choke on it.
