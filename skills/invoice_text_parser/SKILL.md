---
name: invoice_text_parser
version: '3.0'
kind: parser
execution: code
description: Reads text invoices and supporting notes into finagent.document/1.
policy:
- FIN-POL-001#s2
- FIN-POL-002#s4
references:
- policy.md
---

# invoice_text_parser

Reads text invoices and supporting notes into finagent.document/1.

Deterministic script only (`src/finagent/skills/parsers/`): no judgement questions.
