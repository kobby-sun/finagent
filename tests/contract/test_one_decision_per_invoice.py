"""One decision per invoice: a held case is resumed at most once, and an invoice is posted / rejected at most once,
however many runs (attempts, or the same invoice sent again) reach SUBMIT.

Regression for a real bug: adding evidence twice to the same held run created two "attempt 2" runs; approving both
posted the same invoice twice."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from finagent.agent.graph import ApprovalError
from finagent.schemas import ApprovalDecisionIn, EvidenceIn, ProcessingRequest
from tests.conftest import load_case


def _fin038(app):
    """FIN-038: held for missing evidence; the scenario's evidence makes it approvable."""
    d = json.loads((app.settings.root / "mock_data/scenarios.json").read_text())
    sc = next(x for x in (d if isinstance(d, list) else d["scenarios"]) if x["case_id"] == "FIN-038")
    ev = next(st for st in sc["steps"] if st["op"] == "add_evidence")
    approver = next(st for st in sc["steps"] if st["op"] == "decide")["approver_id"]
    body = EvidenceIn.model_validate({k: v for k, v in ev.items() if k in ("attachments", "note", "po_ref")})
    return ProcessingRequest.model_validate(load_case(app.settings, "FIN-038")), body, approver


def _postings(app):
    return app.store.fetchone("SELECT COUNT(*) n FROM erp_decisions WHERE case_id='FIN-038'")["n"]


def _approve(app, run, approver):
    app.engine.decide_approval(run["approval"]["approval_id"], ApprovalDecisionIn(decision="APPROVE", approver_id=approver))
    return app.engine.get_run(run["run_id"])


def test_held_run_is_resumed_only_once(fresh_app):
    req, ev, approver = _fin038(fresh_app)
    held = fresh_app.engine.start_run(req)
    assert held["status"] == "NEEDS_INFORMATION"
    child = fresh_app.engine.add_evidence(held["run_id"], ev.attachments, ev.note, ev.po_ref)
    assert child["parent_run_id"] == held["run_id"] and child["attempt"] == 2
    with pytest.raises(ApprovalError) as exc:
        fresh_app.engine.add_evidence(held["run_id"], ev.attachments, ev.note, ev.po_ref)
    assert exc.value.http_status == 409 and exc.value.body["newer_attempt"] == child["run_id"]
    old = fresh_app.store.get_outcome(held["run_id"])
    assert old["pool_status"] == "SUPERSEDED" and old["payload"]["superseded_by"] == child["run_id"]
    assert held["run_id"] not in {o["run_id"] for o in fresh_app.store.list_outcomes(pool_status="HELD")}
    assert _approve(fresh_app, child, approver)["status"] == "COMPLETED" and _postings(fresh_app) == 1


def test_concurrent_resumes_start_one_attempt(fresh_app):
    req, ev, _ = _fin038(fresh_app)
    held = fresh_app.engine.start_run(req)

    def resume(_):
        try:
            return fresh_app.engine.add_evidence(held["run_id"], ev.attachments, ev.note, ev.po_ref)["run_id"]
        except ApprovalError as e:
            return e.http_status

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(resume, range(4)))
    assert sum(isinstance(r, str) for r in results) == 1 and results.count(409) == 3


def test_same_invoice_sent_again_is_never_posted_twice(fresh_app):
    req, ev, approver = _fin038(fresh_app)
    first = fresh_app.engine.add_evidence(fresh_app.engine.start_run(req)["run_id"], ev.attachments, ev.note, ev.po_ref)
    assert _approve(fresh_app, first, approver)["status"] == "COMPLETED"
    again = fresh_app.engine.add_evidence(fresh_app.engine.start_run(req)["run_id"], ev.attachments, ev.note, ev.po_ref)
    final = _approve(fresh_app, again, approver)
    assert final["status"] == "NEEDS_INFORMATION" and _postings(fresh_app) == 1
    assert final["result"]["recommendation"]["primary_exception"] == "DUPLICATE_RISK"
    assert any(e["event_type"] == "decision.already_decided" for e in final["audit_events"])
