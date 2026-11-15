---
name: evidence_completeness
version: '3.0'
kind: processor
execution: code
description: Minimum-evidence check and consistency between the processing request and the invoice document.
policy:
- FIN-POL-001#s2
- FIN-POL-001#s5
- FIN-POL-007#s2
references:
- policy.md
---

# evidence_completeness

Minimum-evidence check and consistency between the processing request and the invoice document.

Deterministic script only (`src/finagent/skills/processors/`): no judgement questions.
