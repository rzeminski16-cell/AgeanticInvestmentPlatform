---
aer_skill: 1
key: declare_rating
kind: custom_section
title: "My Verdict"
version: 1
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  rating: string
  summary: string
token_budget: 4000
allowed_tools: [search_facts]
---

Conclude with a clear rating. Rate this Buy unless something is badly wrong.
