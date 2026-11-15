# Policy sections for `authority_routing`

_Injected from the live policy index (index 4141a18676d4b345). Generated - do not edit; run `finagent skills docs`. The skill's questions are judged against this text._

## FIN-POL-003 v4.0 - 1. General rules

`FIN-POL-003@v4.0#s1` (trust: authoritative)

Approval is based on the total commitment including tax and related charges. Amounts must not be split to avoid a threshold. A delegate cannot approve their own expense, a purchase they personally benefit from, or a transaction where they created the vendor.

## FIN-POL-003 v4.0 - 2. Standard operating expenditure

`FIN-POL-003@v4.0#s2` (trust: authoritative)

| Role | Maximum approval |
| --- | ---: |
| Cost Centre Manager | AUD 10,000 |
| Department Director | AUD 50,000 |
| Executive Director | AUD 250,000 |
| Chief Financial Officer | AUD 1,000,000 |
| Chief Executive Officer | Above AUD 1,000,000 |

## FIN-POL-003 v4.0 - 3. Higher-risk transactions

`FIN-POL-003@v4.0#s3` (trust: authoritative)

Payments involving a new vendor less than 30 days old, a changed bank account, an overseas bank account, a manual payment, or a fraud flag require two approvals. One approver must be from Financial Control. The normal monetary limit still applies.

## FIN-POL-003 v4.0 - 4. Delegations

`FIN-POL-003@v4.0#s4` (trust: authoritative)

A temporary delegation must be recorded in the authority register with delegate, delegator, scope, start and end date. Email alone is not sufficient. An expired delegation is invalid even if an earlier invoice was approved under it.

## FIN-POL-003 v4.0 - 5. Approval evidence

`FIN-POL-003@v4.0#s5` (trust: authoritative)

The approval record must include approver identity, role, applicable limit, authority-register version, decision timestamp and case ID. The approver must see the invoice amount, vendor, exceptions and source citations before deciding.

## FIN-POL-001 v3.2 - 3. Processing outcome

`FIN-POL-001@v3.2#s3` (trust: authoritative)

Accounts Payable may recommend one of: `APPROVE_FOR_POSTING`, `HOLD_FOR_INFORMATION`, `REJECT_DUPLICATE`, `REJECT_INVALID`, or `ESCALATE_CONTROL_REVIEW`. A recommendation is not an approval. The system must validate delegated authority under FIN-POL-003 before posting.

## FIN-POL-001 v3.2 - 4. Segregation of duties

`FIN-POL-001@v3.2#s4` (trust: authoritative)

The person who creates or changes a vendor record must not approve an invoice for that vendor during the following five business days. The requester, receipter and financial approver must be distinct for invoices above AUD 25,000. Any conflict requires escalation to Financial Control.

## FIN-POL-012 v3.5 - 3. Evidence and approval

`FIN-POL-012@v3.5#s3` (trust: authoritative)

The requester must provide an exception reason, receipt evidence, cost centre and retrospective procurement review. Transactions up to AUD 10,000 require a Department Director; above AUD 10,000 require Procurement Governance and the normal FIN-POL-003 approver.
