# Policy sections for `three_way_match`

_Injected from the live policy index (index 4141a18676d4b345). Generated - do not edit; run `finagent skills docs`. The skill's questions are judged against this text._

## FIN-POL-002 v2.4 - 1. Matching basis

`FIN-POL-002@v2.4#s1` (trust: authoritative)

PO-backed invoices must be compared against the approved purchase order and recorded goods or service receipt. Matching is performed per line and for the document total. Currency must match unless FIN-POL-009 authorises conversion.

## FIN-POL-002 v2.4 - 2. Tolerances

`FIN-POL-002@v2.4#s2` (trust: authoritative)

For goods, a line is within tolerance when invoiced quantity does not exceed received quantity and the lower of these limits is met: AUD 50 absolute variance or 1% of the PO line value. For services, the limit is AUD 100 or 2%, whichever is lower, provided the service owner confirms completion.

Freight may vary by up to AUD 75 when the PO explicitly permits freight. Tax, rounding and foreign-exchange differences are assessed separately and must not be hidden inside a price variance.

## FIN-POL-002 v2.4 - 3. Outcomes

`FIN-POL-002@v2.4#s3` (trust: authoritative)

All lines within tolerance may proceed to delegated approval. A variance outside tolerance produces `HOLD_FOR_INFORMATION` and must identify the failed line, expected value, actual value, difference and applicable threshold. Splitting a variance across multiple invoices does not make it acceptable.

## FIN-POL-002 v2.4 - 4. Missing receipt

`FIN-POL-002@v2.4#s4` (trust: authoritative)

An invoice without a required receipt cannot be approved for payment. The agent may request confirmation from the designated receipter but must not infer receipt from delivery language on the invoice.

## FIN-POL-002 v2.4 - 5. Calculation rule

`FIN-POL-002@v2.4#s5` (trust: authoritative)

Tolerance calculations must use decimal arithmetic and the invoice currency. Model-generated arithmetic is not authoritative. The calculation record must store input values, formula, result and rounding method.

## FIN-POL-009 v1.6 - 1. Currency agreement

`FIN-POL-009@v1.6#s1` (trust: authoritative)

The invoice currency must match the purchase order or signed contract. A supplier cannot change currency through invoice text alone. A mismatch requires a contract owner confirmation and Treasury review.

## FIN-POL-009 v1.6 - 3. Variance

`FIN-POL-009@v1.6#s3` (trust: authoritative)

Exchange-rate movement is not a price variance when the invoice and PO share the same foreign currency. When a PO is in AUD and the invoice is foreign currency, the case must be held unless the PO explicitly permits conversion.
