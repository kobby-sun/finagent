---
name: email_parser
version: '3.0'
kind: parser
execution: code
description: Reads e-mails (headers, body, quoted invoice, bank-change wording) into finagent.document/1.
policy:
- FIN-POL-004#s2
- FIN-POL-005#s3
references:
- policy.md
---

# email_parser

Reads e-mails (headers, body, quoted invoice, bank-change wording) into finagent.document/1.

Deterministic script only (`src/finagent/skills/parsers/`): no judgement questions.
