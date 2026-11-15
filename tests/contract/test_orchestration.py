"""Core orchestration: approval gate, resume, duplicate callbacks, model failures, safe logging, typed result."""
import json
import os

import pytest

from finagent.agent.graph import SimulatedCrash
from finagent.schemas import ApprovalDecisionIn, FinalResult, ProcessingRequest
from tests.conftest import load_case


def start(app, case):
    return app.engine.start_run(ProcessingRequest.model_validate(load_case(app.settings, case)))


def erp_count(app, run_id):
    return app.store.fetchone("SELECT COUNT(*) AS n FROM erp_decisions WHERE run_id=?", (run_id,))["n"]


def test_final_result_is_typed_and_separated(fresh_app):
    run = start(fresh_app, "FIN-001")
    res = FinalResult.model_validate(run["result"])
    assert res.sourced_facts and res.calculations and res.policy_findings and res.actions_taken
    assert res.recommendation.outcome.value == "APPROVE_FOR_POSTING"
    assert all(c.citations for c in res.policy_findings if c.status.value != "NOT_APPLICABLE")


def test_consequential_outcome_stops_for_approval(fresh_app):
    run = start(fresh_app, "FIN-001")
    assert run["status"] == "AWAITING_APPROVAL" and erp_count(fresh_app, run["run_id"]) == 0
    ex = run["approval"]["explanation"]
    assert ex["amount"] == "18150.00" and ex["vendor_id"] == "V-1001" and ex["citations"] and "exceptions" in ex


def test_duplicate_callback_replays_and_submits_once(fresh_app):
    run = start(fresh_app, "FIN-005")
    apr = run["approval"]["approval_id"]
    body = ApprovalDecisionIn(decision="APPROVE", approver_id="u-ccm-01", callback_id="cb-dup")
    c1, r1 = fresh_app.engine.decide_approval(apr, body)
    c2, r2 = fresh_app.engine.decide_approval(apr, body)
    assert c1 == c2 == 200 and r2["replayed"] is True and r1["run"] == r2["run"]
    c3, r3 = fresh_app.engine.decide_approval(apr, ApprovalDecisionIn(decision="REJECT", approver_id="u-dd-01", comment="late conflicting rejection", callback_id="cb-late"))
    assert c3 == 409 and r3["effect"] == "none"
    assert erp_count(fresh_app, run["run_id"]) == 1


def test_crash_and_resume_is_exactly_once(fresh_app):
    os.environ["FINAGENT_CRASH_AFTER_NODE"] = "GATHER_EVIDENCE"
    try:
        with pytest.raises(SimulatedCrash):
            start(fresh_app, "FIN-022")
    finally:
        os.environ.pop("FINAGENT_CRASH_AFTER_NODE")
    rid = fresh_app.store.list_runs(case_id="FIN-022")[0]["run_id"]
    assert fresh_app.store.get_run(rid)["status"] == "RUNNING"
    assert fresh_app.engine.resume(rid)["status"] == "AWAITING_APPROVAL"
    apr = fresh_app.store.get_approval_for_run(rid)["approval_id"]
    os.environ["FINAGENT_CRASH_AFTER_NODE"] = "SUBMIT_DECISION"
    try:
        with pytest.raises(SimulatedCrash):
            fresh_app.engine.decide_approval(apr, ApprovalDecisionIn(decision="APPROVE", approver_id="u-ccm-01"))
    finally:
        os.environ.pop("FINAGENT_CRASH_AFTER_NODE")
    assert fresh_app.engine.resume(rid)["status"] == "COMPLETED"
    assert fresh_app.engine.resume(rid)["status"] == "COMPLETED"     # idempotent
    assert erp_count(fresh_app, rid) == 1


def test_malformed_model_output_retried(fresh_app):
    run = start(fresh_app, "FIN-020")
    m = run["result"]["model"]
    assert m["status"] == "VALID" and len(m["attempts"]) == 2 and m["attempts"][0]["valid"] is False


def test_model_never_relaxes_outcome(fresh_app):
    run = start(fresh_app, "FIN-003")    # mock model is fault-injected to propose APPROVE_FOR_POSTING
    assert run["result"]["recommendation"]["outcome"] == "ESCALATE_CONTROL_REVIEW"
    assert any(e["event_type"] == "model.override_blocked" for e in run["audit_events"])
    assert run["approval"] is None


def test_model_invalid_after_retries_fails_explicitly_when_configured(settings, tmp_path):
    import shutil
    from finagent.services import build_app
    s = settings.with_overrides({"app.data_dir": str(tmp_path), "llm.on_invalid_output": "fail", "llm.max_validation_retries": 0})
    shutil.copy(settings.index_path, s.index_path)
    app = build_app(s)
    run = start(app, "FIN-020")      # first (only) attempt is malformed
    assert run["status"] == "FAILED" and "model output invalid" in run["error"]
    assert erp_count(app, run["run_id"]) == 0


def test_audit_has_no_secrets_and_chain_verifies(fresh_app):
    run = start(fresh_app, "FIN-003")
    blob = json.dumps(fresh_app.audit.events(run["run_id"]))
    assert "06200010" not in blob and "+61 400 000 000" not in blob
    assert fresh_app.audit.verify_chain()["valid"]
    for e in fresh_app.audit.events(run["run_id"]):
        assert e["ts"] and e["run_id"] == run["run_id"] and e["outcome"] is not None or e["event_type"].startswith(("node.", "workflow.", "skill.", "run.", "webhook."))


def test_audit_tamper_detected(fresh_app):
    start(fresh_app, "FIN-007")
    with fresh_app.store.tx() as conn:
        conn.execute("UPDATE audit_events SET outcome='tampered' WHERE seq=(SELECT MIN(seq)+2 FROM audit_events)")
    assert fresh_app.audit.verify_chain()["valid"] is False


def test_start_run_idempotency_key(fresh_app):
    req = ProcessingRequest.model_validate(load_case(fresh_app.settings, "FIN-007"))
    a = fresh_app.engine.start_run(req, idempotency_key="client-key-1")
    b = fresh_app.engine.start_run(req, idempotency_key="client-key-1")
    assert a["run_id"] == b["run_id"] and len(fresh_app.store.list_runs(case_id="FIN-007")) == 1


def test_callback_id_reuse_across_approvals_is_not_a_replay(fresh_app):
    """A callback id reused on a DIFFERENT approval must be processed, not answered from another approval's cache."""
    a = start(fresh_app, "FIN-005")["approval"]["approval_id"]
    b = start(fresh_app, "FIN-007")["approval"]["approval_id"]
    body = ApprovalDecisionIn(decision="APPROVE", approver_id="u-ccm-01", callback_id="same-id")
    c1, r1 = fresh_app.engine.decide_approval(a, body)
    c2, r2 = fresh_app.engine.decide_approval(b, body)
    assert c1 == c2 == 200 and r2["replayed"] is False and r2["approval_id"] == b
    assert fresh_app.store.get_approval(b)["status"] == "APPROVED"


def test_approval_webhook_sent_only_after_state_persisted(fresh_app):
    """An approval system that reacts instantly to approval.requested must find the run already AWAITING_APPROVAL."""
    seen = []
    orig = fresh_app.webhooks.emit
    def spy(event_type, run_id, data):
        if event_type == "approval.requested":
            seen.append(fresh_app.store.get_run(run_id)["status"])
        return orig(event_type, run_id, data)
    fresh_app.webhooks.emit = spy
    start(fresh_app, "FIN-007")
    assert seen == ["AWAITING_APPROVAL"]


def test_resume_repairs_resolved_approval_left_unresumed(fresh_app):
    run = start(fresh_app, "FIN-007")
    with fresh_app.store.tx() as conn:   # simulate crash between recording the decision and resuming the run
        conn.execute("UPDATE approvals SET status='APPROVED' WHERE run_id=?", (run["run_id"],))
    assert fresh_app.engine.resume_all() == [run["run_id"]]
    assert fresh_app.store.get_run(run["run_id"])["status"] == "COMPLETED" and erp_count(fresh_app, run["run_id"]) == 1


def test_reasoning_covers_every_key_step_and_human_decisions(fresh_app):
    run = start(fresh_app, "FIN-024")
    apr = run["approval"]["approval_id"]
    fresh_app.engine.decide_approval(apr, ApprovalDecisionIn(decision="APPROVE", approver_id="u-dd-02", comment="looks fine"))   # SoD refusal
    fresh_app.engine.decide_approval(apr, ApprovalDecisionIn(decision="APPROVE", approver_id="u-dd-01", comment="PO and receipt checked"))
    r = fresh_app.engine.reasoning(run["run_id"])
    steps = {x["step"] for x in r}
    assert {"PARSE_INPUTS", "PLAN", "RETRIEVE_POLICY", "GATHER_EVIDENCE", "RECONCILE", "DRAFT", "DECIDE", "APPROVAL_GATE",
            "HUMAN_DECISION", "SUBMIT_DECISION"} <= steps
    assert all(x["reason"] for x in r)
    skills = {x["actor"] for x in r if x["actor"].startswith("skill:") and x["step"] == "RECONCILE"}
    assert len(skills) >= 9                                   # every executed control explains itself
    human = [x for x in r if x["step"] == "HUMAN_DECISION"]
    assert any("REFUSED" in x["decision"] and "segregation of duties" in x["reason"] for x in human)
    assert any(x["actor"] == "human:u-dd-01" and "PO and receipt checked" in x["reason"] for x in human)
    assert any("skip" in x["decision"] for x in r if x["step"] == "PLAN")     # skipped controls say why


def test_reject_requires_a_reason():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ApprovalDecisionIn(decision="REJECT", approver_id="u-ccm-01")
    ApprovalDecisionIn(decision="APPROVE", approver_id="u-ccm-01")   # approve may omit a comment
