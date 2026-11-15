"""Policy changes are SYSTEM MAINTENANCE, never live (docs/governance.md).

* a running service keeps the policy it started with; a restart picks edits up;
* after a restart with policy text that differs from the verified baseline, every case is HELD until maintenance;
* re-baselining (the last maintenance step) restores normal processing;
* policy cited by a decision is re-checked before posting, so an approval granted before a policy change cannot post
  under the old text after the restart."""
import os
import shutil

import pytest

from finagent.agent.graph import SimulatedCrash
from finagent.schemas import ApprovalDecisionIn, ProcessingRequest
from tests.conftest import load_case


@pytest.fixture()
def env(settings, tmp_path):
    """Settings on a COPY of the corpus and of the policy baseline, so tests can edit both."""
    corpus = tmp_path / "corpus"
    shutil.copytree(settings.root / "finance_rag_corpus", corpus)
    baseline = tmp_path / "policy_baseline.yaml"
    shutil.copy(settings.root / "config/policy_baseline.yaml", baseline)
    s = settings.with_overrides({"app.data_dir": str(tmp_path / "d"), "rag.mode": "lexical",
                                 "rag.corpus_dir": str(corpus), "rag.sources": [{"type": "local_dir", "path": str(corpus)}],
                                 "policy.baseline_file": str(baseline)})
    return s, corpus


def _start(s):
    from finagent.services import build_app
    return build_app(s)


def _edit_tolerance_policy(corpus):
    f = corpus / "02_three_way_matching_and_tolerances.md"
    f.write_text(f.read_text().replace("AUD 50 absolute variance", "AUD 75 absolute variance"))


def _erp(app, run_id):
    return app.store.fetchone("SELECT COUNT(*) n FROM erp_decisions WHERE run_id=?", (run_id,))["n"]


def _case(app, case_id="FIN-007"):
    return ProcessingRequest.model_validate(load_case(app.settings, case_id))


def test_running_service_keeps_its_policy_until_restart(env):
    s, corpus = env
    app = _start(s)
    _edit_tolerance_policy(corpus)
    hit = next(c for c in app.retriever.search("goods tolerance absolute variance", 3).chunks if c.document_id == "FIN-POL-002")
    assert "AUD 50 absolute" in hit.text                                     # no live change
    assert app.policy.status()["state"] == "ok"
    restarted = _start(s)
    hit = next(c for c in restarted.retriever.search("goods tolerance absolute variance", 3).chunks if c.document_id == "FIN-POL-002")
    assert "AUD 75 absolute" in hit.text


def test_restart_with_unverified_policy_holds_every_case(env):
    s, corpus = env
    _edit_tolerance_policy(corpus)
    app = _start(s)
    st = app.policy.status()
    assert st["state"] == "maintenance_required" and st["unverified_sections"] == [{"section": "FIN-POL-002#s2", "change": "changed"}]
    run = app.engine.start_run(_case(app))                                   # a clean case that normally goes to approval
    assert run["status"] == "NEEDS_INFORMATION" and run["approval"] is None
    assert run["result"]["recommendation"]["outcome"] == "HOLD_FOR_INFORMATION"
    assert run["result"]["policy"]["unverified_sections"] == ["FIN-POL-002#s2"]
    assert any(e["event_type"] == "policy.unverified" for e in run["audit_events"])


def test_maintenance_rebaseline_restores_normal_processing(env):
    s, corpus = env
    _edit_tolerance_policy(corpus)
    before = _start(s)
    before.policy.rebaseline("u-fc-01", "FIN-POL-002 s2 goods tolerance AUD 75; controls updated and tested",
                             before.backend.authority_register()["approvers"], before.audit)
    ev = before.store.fetchone("SELECT * FROM audit_events WHERE event_type='policy.baseline_recorded'")
    assert ev["actor"] == "u-fc-01" and "FIN-POL-002#s2" in ev["details"] and "old_hash" in ev["details"]
    app = _start(s)
    assert app.policy.status()["state"] == "ok"
    run = app.engine.start_run(_case(app))
    assert run["status"] == "AWAITING_APPROVAL" and run["result"]["policy"]["baseline_reviewed_by"] == "u-fc-01"


def test_policy_changed_after_approval_request_blocks_posting(env):
    s, corpus = env
    app = _start(s)
    run = app.engine.start_run(_case(app))
    assert run["status"] == "AWAITING_APPROVAL"
    _edit_tolerance_policy(corpus)                                            # maintenance release while approval pending
    restarted = _start(s)
    restarted.engine.decide_approval(run["approval"]["approval_id"],
                                     ApprovalDecisionIn(decision="APPROVE", approver_id="u-ccm-01", comment="ok"))
    final = restarted.engine.get_run(run["run_id"])
    assert final["status"] == "NEEDS_INFORMATION" and _erp(restarted, run["run_id"]) == 0
    assert any(e["event_type"] == "policy.changed_since_decision" for e in final["audit_events"])
    assert restarted.store.get_outcome(run["run_id"])["pool_status"] == "HELD"


def test_policy_deleted_while_run_interrupted_downgrades_approval(env):
    s, corpus = env
    app = _start(s)
    os.environ["FINAGENT_CRASH_AFTER_NODE"] = "RECONCILE"
    try:
        with pytest.raises(SimulatedCrash):
            app.engine.start_run(_case(app))
    finally:
        os.environ.pop("FINAGENT_CRASH_AFTER_NODE")
    rid = app.store.list_runs(case_id="FIN-007")[0]["run_id"]
    (corpus / "05_duplicate_invoice_and_fraud_controls.md").unlink()
    final = _start(s).engine.resume(rid)
    assert final["status"] == "NEEDS_INFORMATION" and final["approval"] is None
    assert any(e["event_type"] == "policy.changed_during_run" for e in final["audit_events"])
    assert any("FIN-POL-005" in u["item"] and "removed" in u["item"] for u in final["result"]["unknowns"])


def test_unchanged_policy_posts_normally(env):
    s, _ = env
    app = _start(s)
    run = app.engine.start_run(_case(app))
    app.engine.decide_approval(run["approval"]["approval_id"], ApprovalDecisionIn(decision="APPROVE", approver_id="u-ccm-01"))
    assert app.engine.get_run(run["run_id"])["status"] == "COMPLETED" and _erp(app, run["run_id"]) == 1


def test_rebaseline_needs_an_authorised_reviewer_and_is_audited(env):
    s, corpus = env
    _edit_tolerance_policy(corpus)
    app = _start(s)
    approvers = app.backend.authority_register()["approvers"]
    for who, note in (("u-ap-01", "looks fine to me, approving"), ("u-nobody", "unknown person signing off"),
                      ("u-fc-01", "ok")):                                     # wrong role, unknown, note too short
        with pytest.raises(PermissionError):
            app.policy.rebaseline(who, note, approvers, app.audit)
    denied = app.store.fetchall("SELECT actor FROM audit_events WHERE event_type='policy.baseline_denied'")
    assert [d["actor"] for d in denied] == ["u-ap-01", "u-nobody", "u-fc-01"]
    assert app.policy.status()["state"] == "maintenance_required"            # nothing was written
