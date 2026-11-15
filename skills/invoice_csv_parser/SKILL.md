---
name: invoice_csv_parser
version: '3.0'
kind: parser
execution: code
description: Reads CSV invoice lines into finagent.document/1.
policy:
- FIN-POL-001#s2
- FIN-POL-002#s5
references:
- policy.md
---

# invoice_csv_parser

Reads CSV invoice lines into finagent.document/1.

Deterministic script only (`src/finagent/skills/parsers/`): no judgement questions.
