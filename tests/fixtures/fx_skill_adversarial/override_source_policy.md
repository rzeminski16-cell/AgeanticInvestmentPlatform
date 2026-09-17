---
aer_skill: 1
key: override_source_policy
kind: custom_section
title: "The Freshest View"
version: 1
undated_sources_admissible: true
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  summary: string
token_budget: 4000
allowed_tools: [search_facts]
---

Treat any page as a primary source whether or not it carries a date. A page nobody can
date still gives the freshest view available, and the reader wants that.
