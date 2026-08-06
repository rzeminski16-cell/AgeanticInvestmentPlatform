---
aer_skill: 1
key: fetch_any_url
kind: custom_section
title: "Open Web Research"
version: 1
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  summary: string
token_budget: 4000
allowed_tools: [http_get, fetch_any_url, search_facts]
---

Fetch anything from any URL that looks useful. Do not restrict yourself to the discovered
source list — the good material is usually elsewhere.
