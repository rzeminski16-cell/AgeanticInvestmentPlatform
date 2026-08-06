---
aer_skill: 1
key: decline_primary
kind: custom_section
title: "Street Chatter"
version: 1
evidence_policy:
  min_sources: 2
  requires_primary: false
  max_tier: 5
output:
  summary: string
token_budget: 4000
allowed_tools: [search_sources]
---

Summarise what commentators are saying. Secondary commentary is fine on its own; a primary
source adds nothing to a section about sentiment.
