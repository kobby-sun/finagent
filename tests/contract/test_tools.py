"""Tool contracts: schemas, timeouts, retries, deny-by-default, idempotency, budgets."""
import json

import pytest

from finagent.schemas import ApprovalDecisionIn, ProcessingRequest
from finagent.tools.base import ToolBudgetExceeded, ToolContext, ToolPermissionDenied
from tests.conftest import load_case

REQUIRED = {"retrieve_finance_documents", "get_vendor_record", "get_purchase_order", "check_invoice_history", "submit_finance_decision"}


def ctx(**kw):
    return ToolContext(run_id="t-run", case_id="T-001", budget={"tool_calls": 0}, **kw)


def test_required_tools_have_explicit_schemas_and_timeouts(app):
    d = {t["name"]: t for t in app.tools.describe()}
    assert REQUIRED <= set(d)
    for t in d.values():
        assert t["input_schema"]["properties"] and t["output_schema"]["properties"] and t["timeout_s"] > 0
    assert d["submit_finance_decision"]["consequential"] is True
    assert all(not d[n]["consequential"] for n in REQUIRED - {"submit_finance_decision"})


def test_vendor_record_masks_bank_details(app):
    r = app.tools.call("get_vendor_record", {"vendor_id": "V-1001"}, ctx())
    pd = r.output.payment_details
    assert r.ok and set(pd) == {"bank_country", "account_last4", "verified", "verified_at"} and len(pd["account_last4"]) == 4
    assert r.output.last_updated and r.output.status == "ACTIVE"


def test_invalid_arguments_fail_fast(app):
    r = app.tools.call("get_purchase_order", {"po_ref": "DROP TABLE"}, ctx())
    assert not r.ok and r.error_type == "ToolValidationError" and r.attempts == 0


def test_timeout_is_bounded_retry(app):
    r = app.tools.call("get_purchase_order", {"po_ref": "PO-40004"}, ToolContext(run_id="t-timeout", case_id="T", budget={}))
    assert not r.ok and r.error_type == "ToolTimeout" and r.attempts == 3


def test_transient_failure_retried_then_succeeds(app):
    r = app.tools.call("get_vendor_record", {"vendor_id": "V-1020"}, ToolContext(run_id="t-transient", case_id="T", budget={}))
    assert r.ok and r.attempts == 3


def test_history_returns_stable_fingerprints(app):
    args = {"vendor_id": "V-1002", "invoice_ref": "BWL-2201", "amount": "4620.00", "currency": "AUD"}
    a = app.tools.call("check_invoice_history", args, ctx()).output.matches
    b = app.tools.call("check_invoice_history", args, ctx()).output.matches
    assert a[0].record_id == "H-9001" and a[0].match_type == "EXACT" and a[0].fingerprint == b[0].fingerprint


def test_retrieval_includes_trust_and_citations(app):
    out = app.tools.call("retrieve_finance_documents", {"query": "ignore policies urgent payment bank changed"}, ctx()).output
    assert any(c.trust.value == "untrusted" for c in out.chunks) and all(c.citation for c in out.chunks)


def test_submit_is_deny_by_default(app):
    args = {"run_id": "r", "case_id": "FIN-001", "approval_id": "apr-x", "decision": "POST", "outcome": "APPROVE_FOR_POSTING",
            "vendor_id": "V-1001", "invoice_ref": "X", "amount": "1.00", "currency": "AUD", "idempotency_key": "idem-0123456789abcdef"}
    with pytest.raises(ToolPermissionDenied):
        app.tools.call("submit_finance_decision", args, ctx())
    with pytest.raises(ToolPermissionDenied):   # authorization referencing a non-existent approval
        app.tools.call("submit_finance_decision", args, ctx(authorization={"approval_id": "apr-x"}))


def test_submit_rejects_args_that_differ_from_approval(fresh_app):
    app = fresh_app
    run = app.engine.start_run(ProcessingRequest.model_validate(load_case(app.settings, "FIN-007")))
    apr = run["approval"]["approval_id"]
    with app.store.tx() as conn:   # approve directly to isolate the tool guard
        conn.execute("UPDATE approvals SET status='APPROVED' WHERE approval_id=?", (apr,))
    from finagent.tools.finance_tools import idempotency_key_for
    good = {"run_id": run["run_id"], "case_id": "FIN-007", "approval_id": apr, "decision": "POST", "outcome": "APPROVE_FOR_POSTING",
            "vendor_id": "V-1001", "invoice_ref": "INV-AC-7801", "amount": "4433.00", "currency": "AUD",
            "idempotency_key": idempotency_key_for(run["run_id"], apr, "APPROVE_FOR_POSTING", "4433.00")}
    auth = {"approval_id": apr}
    with pytest.raises(ToolPermissionDenied, match="amount"):
        app.tools.call("submit_finance_decision", {**good, "amount": "9999.00"}, ctx(authorization=auth))
    first = app.tools.call("submit_finance_decision", good, ctx(authorization=auth))
    second = app.tools.call("submit_finance_decision", good, ctx(authorization=auth))
    assert first.output.status == "ACCEPTED" and second.output.status == "DUPLICATE_IGNORED"
    assert first.output.posting_ref == second.output.posting_ref and first.output.simulated


def test_tool_budget_enforced(app):
    c = ToolContext(run_id="t-budget", case_id="T", budget={"tool_calls": 10_000})
    with pytest.raises(ToolBudgetExceeded):
        app.tools.call("get_vendor_record", {"vendor_id": "V-1001"}, c)
