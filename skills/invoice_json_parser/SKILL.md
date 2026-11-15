---
name: invoice_json_parser
version: '3.0'
kind: parser
execution: code
description: Reads JSON e-invoices into finagent.document/1.
policy:
- FIN-POL-001#s2
- FIN-POL-008#s1
references:
- policy.md
---

# invoice_json_parser

Reads JSON e-invoices into finagent.document/1.

Deterministic script only (`src/finagent/skills/parsers/`): no judgement questions.
