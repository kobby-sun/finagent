---
name: fraud_indicator_scoring
version: '3.0'
kind: processor
execution: code
description: Aggregate distinct FIN-POL-005 s3 indicators; two or more => escalate to Financial Crime and Controls.
policy:
- FIN-POL-005#s3
- FIN-POL-005#s4
- FIN-POL-007#s3
references:
- policy.md
- indicator_definitions.md
---

# fraud_indicator_scoring

Aggregate distinct FIN-POL-005 s3 indicators; two or more => escalate to Financial Crime and Controls.

Code counts the distinct FIN-POL-005 s3 indicators. With skill questions on, code re-runs the same count over the indicators from scripts AND from the answers of earlier skills (llm_runner.count_indicators); two or more distinct -> ESCALATE. The model never counts.
