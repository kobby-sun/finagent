"""Concrete tool implementations and their registration.

| Tool                        | Kind          | Backing                                   |
|-----------------------------|---------------|-------------------------------------------|
| retrieve_finance_documents  | read          | REAL local BM25 index over the corpus      |
| get_vendor_record           | read          | SIMULATED vendor master (mock_data)        |
| get_purchase_order          | read          | SIMULATED ERP purchasing + goods receipts  |
| check_invoice_history       | read          | SIMULATED AP ledger history                |
| get_fx_rate                 | read          | SIMULATED treasury corporate daily rates   |
| get_authority_register      | read          | SIMULATED delegated authority register     |
| submit_finance_decision     | CONSEQUENTIAL | SIMULATED ERP posting (sandbox table)      |
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from decimal import Decimal

from ..schemas import (AuthorityInput, AuthorityRegister, FxInput, FxRate, HistoryMatch, InvoiceHistoryInput,
                       InvoiceHistoryOutput, POInput, PurchaseOrder, RetrieveDocsInput, RetrieveDocsOutput,
                       SubmitDecisionInput, SubmitDecisionOutput, VendorInput, VendorRecord)
from .base import ToolContext, ToolError, ToolPermissionDenied, ToolRunner, ToolSpec
from .mock_backend import MockBackend, dec


def normalise_invoice_ref(ref: str) -> str:
    """FIN-POL-005 s1: punctuation-stripped, upper-case, leading zeros in numeric runs removed."""
    s = re.sub(r"[^A-Za-z0-9]", "", ref or "").upper()
    return re.sub(r"(?<=[A-Z])0+(?=\d)", "", s)


def invoice_fingerprint(vendor_id: str, ref: str, currency: str, amount: Decimal) -> str:
    raw = f"{vendor_id}|{normalise_invoice_ref(ref)}|{currency}|{format(Decimal(amount).quantize(Decimal('0.01')), 'f')}"
    return "fp-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def idempotency_key_for(run_id: str, approval_id: str, outcome: str, amount: str) -> str:
    return "idem-" + hashlib.sha256(f"{run_id}|{approval_id}|{outcome}|{amount}".encode()).hexdigest()[:32]


def build_tools(settings, audit, store, retriever, backend: MockBackend) -> ToolRunner:
    runner = ToolRunner(settings, audit)

    # ---------------------------------------------------------------- retrieval
    def retrieve(inp: RetrieveDocsInput, ctx: ToolContext) -> RetrieveDocsOutput:
        return retriever.search(inp.query, inp.top_k, inp.doc_types, inp.caller_role)

    runner.register(ToolSpec(
        name="retrieve_finance_documents",
        description="Search the finance policy corpus; returns ranked, trust-labelled chunks with citation metadata.",
        input_model=RetrieveDocsInput, output_model=RetrieveDocsOutput, func=retrieve, permissions=("read:corpus",)))

    # ---------------------------------------------------------------- vendor
    def vendor(inp: VendorInput, ctx: ToolContext) -> VendorRecord:
        backend.maybe_fault("get_vendor_record", inp.vendor_id, ctx.run_id)
        v = backend.vendor(inp.vendor_id)
        if v is None:
            raise ToolError(f"vendor {inp.vendor_id} not found")
        pd = v.get("payment_details", {})
        masked = {  # never expose full bank details to the agent / logs
            "bank_country": pd.get("bank_country"),
            "account_last4": str(pd.get("account_number", ""))[-4:],
            "verified": pd.get("verified", False),
            "verified_at": pd.get("verified_at"),
        }
        return VendorRecord(**{k: v.get(k) for k in VendorRecord.model_fields if k != "payment_details" and k in v},
                            payment_details=masked)

    runner.register(ToolSpec(
        name="get_vendor_record", description="Vendor master data: status, masked payment details, risk flags, timestamps.",
        input_model=VendorInput, output_model=VendorRecord, func=vendor, permissions=("read:vendor_master",)))

    # ---------------------------------------------------------------- purchase order
    def purchase_order(inp: POInput, ctx: ToolContext) -> PurchaseOrder:
        backend.maybe_fault("get_purchase_order", inp.po_ref, ctx.run_id)
        po = backend.purchase_order(inp.po_ref)
        if po is None:
            raise ToolError(f"purchase order {inp.po_ref} not found")
        return PurchaseOrder.model_validate(po)

    runner.register(ToolSpec(
        name="get_purchase_order", description="PO lines, totals, currency, tolerances, approval status and goods receipts.",
        input_model=POInput, output_model=PurchaseOrder, func=purchase_order, permissions=("read:erp_purchasing",)))

    # ---------------------------------------------------------------- invoice history
    def history(inp: InvoiceHistoryInput, ctx: ToolContext) -> InvoiceHistoryOutput:
        backend.maybe_fault("check_invoice_history", inp.vendor_id, ctx.run_id)
        norm = normalise_invoice_ref(inp.invoice_ref)
        amount = Decimal(inp.amount)
        inv_date = date.fromisoformat(inp.invoice_date) if inp.invoice_date else None
        matches = []
        for h in backend.history_records():
            if h["vendor_id"] != inp.vendor_id:
                continue
            if inp.exclude_case_id and h.get("case_id") == inp.exclude_case_id:
                continue
            h_amount = dec(h["amount"])
            h_norm = normalise_invoice_ref(h["invoice_ref"])
            reasons: list[str] = []
            if inp.lookup_mode == "reference":   # credit-note original lookup: number only
                if h_norm != norm:
                    continue
                mtype, reasons = "REFERENCE", ["normalised_invoice_number"]
            elif (h_norm == norm and h["currency"] == inp.currency and h_amount == amount):
                reasons = ["vendor_id", "normalised_invoice_number", "currency", "gross_amount"]
                mtype = "EXACT"
            else:
                if h_norm == norm:
                    reasons.append("normalised_invoice_number")
                if h_amount and abs(h_amount - amount) / h_amount < inp.fuzzy_amount_pct and h["currency"] == inp.currency:
                    reasons.append(f"amount_within_{format((inp.fuzzy_amount_pct * 100).normalize(), 'f')}pct")
                if inv_date and h.get("invoice_date") and abs((date.fromisoformat(h["invoice_date"]) - inv_date).days) <= inp.fuzzy_date_window_days:
                    reasons.append(f"invoice_date_within_{inp.fuzzy_date_window_days}_days")
                if inp.po_ref and h.get("po_ref") == inp.po_ref:
                    reasons.append("same_po")
                if inp.attachment_hash and h.get("attachment_hash") == inp.attachment_hash:
                    reasons.append("attachment_hash")
                strong = "normalised_invoice_number" in reasons or "attachment_hash" in reasons
                mtype = "FUZZY" if (strong and len(reasons) >= 2) or len(reasons) >= 3 else None
                if mtype is None and h.get("is_non_po") and inv_date and h.get("invoice_date") and \
                        0 <= (inv_date - date.fromisoformat(h["invoice_date"])).days <= inp.non_po_window_days:
                    mtype, reasons = "SAME_VENDOR_NON_PO", [f"non_po_same_supplier_within_{inp.non_po_window_days}_days"]
                if mtype is None:
                    continue
            matches.append(HistoryMatch(
                record_id=h["record_id"], case_id=h.get("case_id"), vendor_id=h["vendor_id"],
                invoice_ref=h["invoice_ref"], normalised_ref=h_norm, amount=format(h_amount, "f"),
                currency=h["currency"], invoice_date=h.get("invoice_date"), po_ref=h.get("po_ref"),
                status=h["status"], rejection_reason=h.get("rejection_reason"), is_non_po=h.get("is_non_po", False),
                match_type=mtype, match_reasons=reasons,
                fingerprint=invoice_fingerprint(h["vendor_id"], h["invoice_ref"], h["currency"], h_amount)))
        return InvoiceHistoryOutput(matches=matches, checked_statuses=["PAID", "POSTED", "HELD", "REJECTED"])

    runner.register(ToolSpec(
        name="check_invoice_history", description="Exact and fuzzy duplicate detection against paid/posted/held/rejected invoices.",
        input_model=InvoiceHistoryInput, output_model=InvoiceHistoryOutput, func=history, permissions=("read:ap_ledger",)))

    # ---------------------------------------------------------------- fx
    def fx(inp: FxInput, ctx: ToolContext) -> FxRate:
        backend.maybe_fault("get_fx_rate", inp.from_currency, ctx.run_id)
        # corporate daily rate for the invoice date; fall back up to 3 prior days (weekends)
        d = date.fromisoformat(inp.rate_date)
        for back in range(0, 4):
            r = backend.fx(inp.from_currency, inp.to_currency, (d - timedelta(days=back)).isoformat())
            if r:
                return FxRate.model_validate(r)
        raise ToolError(f"no corporate rate {inp.from_currency}->{inp.to_currency} for {inp.rate_date}")

    runner.register(ToolSpec(
        name="get_fx_rate", description="Treasury corporate daily FX rate for authority assessment (FIN-POL-009 s2).",
        input_model=FxInput, output_model=FxRate, func=fx, permissions=("read:treasury_rates",)))

    # ---------------------------------------------------------------- authority register
    def authority(inp: AuthorityInput, ctx: ToolContext) -> AuthorityRegister:
        backend.maybe_fault("get_authority_register", "register", ctx.run_id)
        return AuthorityRegister.model_validate(backend.authority_register())

    runner.register(ToolSpec(
        name="get_authority_register", description="Delegated financial authority register (approvers, limits, delegations).",
        input_model=AuthorityInput, output_model=AuthorityRegister, func=authority, permissions=("read:authority_register",)))

    # ---------------------------------------------------------------- submit (consequential)
    def guard(inp: SubmitDecisionInput, ctx: ToolContext) -> None:
        auth = ctx.authorization or {}
        approval = store.get_approval(inp.approval_id)
        if approval is None or approval["run_id"] != inp.run_id:
            raise ToolPermissionDenied("approval does not exist for this run")
        if approval["status"] != "APPROVED":
            raise ToolPermissionDenied(f"approval status is {approval['status']}, not APPROVED")
        if auth.get("approval_id") != inp.approval_id:
            raise ToolPermissionDenied("authorization does not reference this approval")
        if approval["proposed_outcome"] != inp.outcome.value:
            raise ToolPermissionDenied("outcome differs from the approved outcome")
        if approval["amount"] != format(inp.amount, "f") or approval["currency"] != inp.currency:
            raise ToolPermissionDenied("amount/currency differ from the approved values")
        expected_decision = "POST" if inp.outcome.value == "APPROVE_FOR_POSTING" else "REJECT"
        if inp.decision != expected_decision:
            raise ToolPermissionDenied("decision verb inconsistent with approved outcome")
        expected_key = idempotency_key_for(inp.run_id, inp.approval_id, inp.outcome.value, format(inp.amount, "f"))
        if inp.idempotency_key != expected_key:
            raise ToolPermissionDenied("idempotency key does not match approved decision")

    def submit(inp: SubmitDecisionInput, ctx: ToolContext) -> SubmitDecisionOutput:
        backend.maybe_fault("submit_finance_decision", inp.case_id, ctx.run_id)
        now = (ctx.extras.get("now") or settings.now)()
        with store.tx() as conn:
            row = conn.execute("SELECT * FROM erp_decisions WHERE idempotency_key=?", (inp.idempotency_key,)).fetchone()
            if row:
                return SubmitDecisionOutput(posting_ref=row["posting_ref"], decision=row["decision"],
                                            status="DUPLICATE_IGNORED", idempotency_key=inp.idempotency_key,
                                            recorded_at=row["recorded_at"])
            # one decision per invoice: another run (a second attempt, or the same invoice sent again) may already have
            # posted / rejected this vendor's invoice number. Checked in the same write transaction, so two concurrent
            # submissions cannot both pass.
            norm = normalise_invoice_ref(inp.invoice_ref)
            for prev in conn.execute("SELECT posting_ref, run_id, decision, recorded_at, payload FROM erp_decisions "
                                     "WHERE json_extract(payload, '$.vendor_id')=?", (inp.vendor_id,)).fetchall():
                if normalise_invoice_ref(json.loads(prev["payload"])["invoice_ref"]) == norm:
                    return SubmitDecisionOutput(posting_ref=prev["posting_ref"], decision=prev["decision"],
                                                status="ALREADY_DECIDED", idempotency_key=inp.idempotency_key,
                                                recorded_at=prev["recorded_at"], previous_run_id=prev["run_id"])
            posting_ref = f"SIM-{inp.decision}-" + hashlib.sha256(inp.idempotency_key.encode()).hexdigest()[:10].upper()
            conn.execute("""INSERT INTO erp_decisions(idempotency_key, posting_ref, run_id, case_id, approval_id, decision,
                            payload, recorded_at) VALUES (?,?,?,?,?,?,?,?)""",
                         (inp.idempotency_key, posting_ref, inp.run_id, inp.case_id, inp.approval_id, inp.decision,
                          json.dumps(inp.model_dump(mode="json")), now.isoformat()))
        return SubmitDecisionOutput(posting_ref=posting_ref, decision=inp.decision, status="ACCEPTED",
                                    idempotency_key=inp.idempotency_key, recorded_at=now.isoformat())

    runner.register(ToolSpec(
        name="submit_finance_decision",
        description="SIMULATED ERP posting/rejection. Deny-by-default: requires an APPROVED approval whose values match, "
                    "and an idempotency key derived from the approval. Never moves money.",
        input_model=SubmitDecisionInput, output_model=SubmitDecisionOutput, func=submit, consequential=True,
        permissions=("write:erp_decision",), guard=guard))
    return runner
