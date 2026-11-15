---
name: vendor_compliance
version: '3.0'
kind: processor
execution: code
description: Vendor status, bank-detail verification, payment hold after bank change, higher-risk flags and SoD exclusions.
policy:
- FIN-POL-004#s2
- FIN-POL-004#s3
- FIN-POL-004#s4
- FIN-POL-003#s3
- FIN-POL-001#s4
- FIN-POL-009#s4
references:
- policy.md
---

# vendor_compliance

Vendor status, bank-detail verification, payment hold after bank change, higher-risk flags and SoD exclusions.

Deterministic script only (`src/finagent/skills/processors/`): no judgement questions.
