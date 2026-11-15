# Fraud indicators (FIN-POL-005 s3) - definitions for counting

_Internal guidance for the fraud_indicator_scoring procedure (illustrative). FIN-POL-005 prevails._

Escalate when **two or more distinct** indicators occur. Count each kind once.

| Indicator | Counts when |
|---|---|
| Newly changed bank details | bank changed recently, or a document gives payment details different from the verified master |
| Urgent or secret payment language | any urgency / secrecy / pressure wording (from instruction_screen) |
| Unusual domain | sender domain differs from the vendor's registered domain (look-alikes included) |
| Mismatched vendor name | invoice supplier name differs from the vendor master legal name |
| Payment to a new country | bank or payment country differs from the vendor's usual country |
| Weekend manual-payment request | a request for manual or out-of-run payment over a weekend |
| Repeated round-dollar invoices | two or more recent invoices from the vendor for exact round amounts (e.g. 5000.00) |
| Pressure to bypass controls | instructions to skip checks or approvals |

Never label a person or vendor fraudulent (FIN-POL-005 s4): escalate to Financial Crime and Controls and do not
notify the supplier.
