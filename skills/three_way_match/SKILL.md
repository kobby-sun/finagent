---
name: three_way_match
version: '3.1'
kind: processor
execution: code+llm
description: Per-line and total matching of invoice vs PO vs goods/service receipt with tolerance, freight and tax rules.
policy:
- FIN-POL-002#s1
- FIN-POL-002#s2
- FIN-POL-002#s3
- FIN-POL-002#s4
- FIN-POL-002#s5
- FIN-POL-009#s1
- FIN-POL-009#s3
evidence:
- po.lines
- invoice.lines
- po.receipts
- docs.internal
questions:
- id: pack_size
  ask: For each invoice line, is it the same item AND the same pack size or unit of measure as the PO line with the same SKU
    (e.g. 'box of 100' vs 'carton of 10 boxes' is different)?
  answers:
  - 'yes'
  - 'no'
  - unclear
  'on':
    'no': HOLD
  finding: pack size matches
  reference: item_identity.md
  evidence:
  - po.lines
  - invoice.lines
  - docs.internal
- id: receipt_conflict
  ask: Does any internal document say a DIFFERENT quantity arrived than the goods receipt records (e.g. fewer items)? If a
    later internal note resolves an earlier one, judge by the latest note.
  answers:
  - 'no'
  - 'yes'
  - unclear
  'on':
    'yes': HOLD
  finding: receipt evidence agrees
  reference: item_identity.md
  evidence:
  - po.receipts
  - docs.internal
  when:
    check: has_internal_doc
references:
- policy.md
- item_identity.md
on_concern:
  category: QUANTITY_VARIANCE
  owner: Receipter
  expected: Invoiced items identical to the items ordered and received
---

# three_way_match

Per-line and total matching of invoice vs PO vs goods/service receipt with tolerance, freight and tax rules.

Judgement: are the matched lines really the same goods? The script matches SKU, quantity, price and receipt
with tolerances (all arithmetic); it cannot see that the same SKU now means a different pack, or that an internal note
disagrees with the receipt.

| Question | Answer -> code decides |
|---|---|
| pack_size | no -> HOLD (QUANTITY_VARIANCE, receipter); yes -> no change |
| receipt_conflict | yes -> HOLD (QUANTITY_VARIANCE, receipter); no -> no change |

Scope: pack_size sees only the PO lines, invoice lines and internal documents (no supplier attachments); receipt_conflict is asked only when code finds an internal document, and sees only the receipt and internal documents.

AI not sure ("unclear" or low certainty): warning for the approver (`if_uncertain: warn`).
