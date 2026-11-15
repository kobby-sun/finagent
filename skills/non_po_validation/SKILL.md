---
name: non_po_validation
version: '3.1'
kind: processor
execution: code+llm
description: Non-PO and emergency purchase eligibility, evidence and repeated-use escalation.
policy:
- FIN-POL-012#s1
- FIN-POL-012#s2
- FIN-POL-012#s3
- FIN-POL-012#s4
evidence:
- invoice.non_po
- docs.internal
questions:
- id: emergency_genuine
  ask: Is there a document with source="internal" (an incident ticket, outage log or safety report - not a supplier document,
    not a work or call-out log) that describes an actual safety risk, critical service outage or material asset loss? A lapsed
    or expired agreement, poor planning or supplier pressure is NOT an emergency.
  answers:
  - 'yes'
  - 'no'
  - unclear
  'on':
    'no': HOLD
    unclear: HOLD
  finding: emergency is genuine
  reference: emergency_guidance.md
  if_uncertain: ask_evidence
  when:
    path: invoice.non_po.category
    equals: emergency
references:
- policy.md
- emergency_guidance.md
on_concern:
  category: MISSING_PO
  owner: Requester
  expected: A permitted non-PO category with a genuine emergency or valid basis (FIN-POL-012)
---

# non_po_validation

Non-PO and emergency purchase eligibility, evidence and repeated-use escalation.

Judgement: is a claimed emergency real? The script checks the category, the keyword list and the evidence on
file; it cannot tell a real outage from a lapsed contract described as one.

| Answer | Code decides |
|---|---|
| yes | no change (the script result stands) |
| no / unclear | HOLD: MISSING_PO for the requester |

Asked only when the non-PO category is `emergency`. AI not sure of a "yes": HOLD and ask the requester for the
internal incident record (`if_uncertain: ask_evidence`).
