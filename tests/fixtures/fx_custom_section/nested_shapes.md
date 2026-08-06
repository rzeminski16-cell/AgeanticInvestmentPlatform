---
aer_skill: 1
key: nested_shapes
kind: custom_section
title: "Nested Shapes"
version: 1
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  narrative: string
  key_risks: string_list
  scenario_notes:
    type: array
    items: object
token_budget: 8000
allowed_tools: [search_facts]
---

A deliberately awkward contract: one field with a made-up type word and one with a nested
schema object. Both project to permissive subschemas — the closed world still applies to
field names, which is what the corpus checks.
