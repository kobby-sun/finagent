---
name: fx_validation
version: '3.0'
kind: processor
execution: code
description: Convert foreign-currency amounts to AUD with the corporate daily rate for authority assessment.
policy:
- FIN-POL-009#s2
- FIN-POL-009#s3
references:
- policy.md
---

# fx_validation

Convert foreign-currency amounts to AUD with the corporate daily rate for authority assessment.

Deterministic script only (`src/finagent/skills/processors/`): no judgement questions.
