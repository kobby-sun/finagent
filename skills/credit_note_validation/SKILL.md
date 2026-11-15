---
name: credit_note_validation
version: '3.0'
kind: processor
execution: code
description: Credit note reference, vendor/currency consistency, amount and tax treatment.
policy:
- FIN-POL-008#s1
- FIN-POL-008#s2
references:
- policy.md
---

# credit_note_validation

Credit note reference, vendor/currency consistency, amount and tax treatment.

Deterministic script only (`src/finagent/skills/processors/`): no judgement questions.
