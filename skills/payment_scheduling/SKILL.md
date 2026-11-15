---
name: payment_scheduling
version: '3.1'
kind: processor
execution: code+llm
description: Propose the payment run date from agreed terms (proposal only; release is out of scope).
policy:
- FIN-POL-006#s1
- FIN-POL-006#s2
- FIN-POL-006#s4
evidence:
- docs
questions:
- id: payment_pressure
  ask: 'Does the invoice demand payment sooner than the agreed terms, or threaten a late fee, interest or penalty? (FIN-POL-006
    s1: an invoice''s printed terms do not override the agreed terms.)'
  answers:
  - 'no'
  - 'yes'
  'on':
    'yes': FLAG
  finding: late-fee pressure
  indicator: late-fee pressure
  reference: policy.md
references:
- policy.md
---

# payment_scheduling

Propose the payment run date from agreed terms (proposal only; release is out of scope).

Judgement: is the supplier pressing for early payment? The script proposes the date from agreed terms (agreed
terms always apply); the answer only adds a visible warning for the approver.

| Answer | Code decides |
|---|---|
| yes | FLAG: warning / risk indicator "late-fee pressure" |
| no | no change |

AI not sure (low certainty): warning for the approver (`if_uncertain: warn`).
