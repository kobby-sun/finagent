# Policy sections for `duplicate_detection`

_Injected from the live policy index (index 4141a18676d4b345). Generated - do not edit; run `finagent skills docs`. The skill's questions are judged against this text._

## FIN-POL-005 v2.8 - 1. Duplicate detection

`FIN-POL-005@v2.8#s1` (trust: authoritative)

Every invoice must be checked against paid, posted, held and rejected records. Exact matching uses vendor ID, normalised invoice number, currency and gross amount. Fuzzy matching should also consider punctuation-stripped invoice numbers, invoice date within 14 days, amount variance below 0.5%, purchase-order number and attachment hash.

## FIN-POL-005 v2.8 - 2. Outcomes

`FIN-POL-005@v2.8#s2` (trust: authoritative)

An exact match to a paid or posted invoice results in `REJECT_DUPLICATE`. A probable fuzzy match results in `HOLD_FOR_INFORMATION` with both record IDs cited. A prior rejection does not automatically prove a new invoice is a duplicate; the reason and corrected fields must be reviewed.
