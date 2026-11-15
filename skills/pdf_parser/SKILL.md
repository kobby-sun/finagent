---
name: pdf_parser
version: '3.0'
kind: parser
execution: code
description: Extracts text from PDFs (scanned PDFs flagged) into finagent.document/1.
policy:
- FIN-POL-001#s2
- FIN-POL-010#s2
references:
- policy.md
---

# pdf_parser

Extracts text from PDFs (scanned PDFs flagged) into finagent.document/1.

Deterministic script only (`src/finagent/skills/parsers/`): no judgement questions.
