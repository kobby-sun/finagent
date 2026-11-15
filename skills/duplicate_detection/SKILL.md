---
name: duplicate_detection
version: '3.0'
kind: processor
execution: code
description: Classify invoice-history matches into exact / probable duplicates / prior rejections.
policy:
- FIN-POL-005#s1
- FIN-POL-005#s2
references:
- policy.md
---

# duplicate_detection

Classify invoice-history matches into exact / probable duplicates / prior rejections.

Deterministic script only (`src/finagent/skills/processors/`): no judgement questions.
