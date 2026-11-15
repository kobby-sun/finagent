---
name: authority_routing
version: '3.0'
kind: processor
execution: code
description: Determine required approval slots from the current delegated authority matrix, higher-risk rules and non-PO rules.
policy:
- FIN-POL-003#s1
- FIN-POL-003#s2
- FIN-POL-003#s3
- FIN-POL-003#s4
- FIN-POL-003#s5
- FIN-POL-001#s3
- FIN-POL-001#s4
- FIN-POL-012#s3
references:
- policy.md
---

# authority_routing

Determine required approval slots from the current delegated authority matrix, higher-risk rules and non-PO rules.

Deterministic script only (`src/finagent/skills/processors/`): no judgement questions.
