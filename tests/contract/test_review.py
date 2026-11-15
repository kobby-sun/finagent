"""POST /runs/{id}/review: a person decides a judgement question the AI could not settle.

The case is re-assessed as a new attempt with that question answered by the reviewer; only authorised roles may
review, the requester may not, and the reviewer may not approve the same case (segregation of duties)."""
import shutil

import pytest
import yaml
from fastapi.testclient import TestClient

from finagent.api.app import create_app
from finagent.schemas import ApprovalDecisionIn, ProcessingRequest, ReviewIn
from finagent.services import build_app
from tests.conftest import load_case


@pytest.fixture()
def unsure_app(tmp_path, settings):
    """The mock model answers 'unclear' on FIN-050's (genuine) emergency -> the case is held, open for review."""
    rec = tmp_path / "rec" / "non_po_validation"
    rec.mkdir(parents=True)
    (rec / "FIN-050@1.yaml").write_text(yaml.safe_dump([{"answers": {"emergency_genuine": {"answer": "unclear", "quote": ""}}}]))
    s = settings.with_overrides({"app.data_dir": str(tmp_path), "skills.llm.recordings_dir": str(tmp_path / "rec")})
    shutil.copy(settings.index_path, s.index_path)
    return build_app(s)


def _held(app):
    run = app.engine.start_run(ProcessingRequest.model_validate(load_case(app.settings, "FIN-050")))
    assert run["status"] == "NEEDS_INFORMATION"
    st = app.store.get_run(run["run_id"])["state_json"]
    assert st["procedures"]["non_po_validation"]["uncertain"] == ["emergency_genuine"]
    return run["run_id"]


def _review(**kw):
    return ReviewIn(**{"reviewer_id": "u-fc-01", "skill": "non_po_validation", "question": "emergency_genuine",
                       "decision": "clear", "reason": "Incident INC-3301 confirmed with the site safety officer.", **kw})


def test_reviewer_clears_the_question_and_the_case_continues_to_a_different_approver(unsure_app):
    from finagent.agent.graph import ApprovalError
    rid = _held(unsure_app)
    run = unsure_app.engine.review_judgement(rid, _review())
    assert run["status"] == "AWAITING_APPROVAL" and run["attempt"] == 2
    facts = [f["fact"] for o in unsure_app.store.get_run(run["run_id"])["state_json"]["skills"] for f in o["facts"]]
    assert any("decided by a person" in f and "u-fc-01" in f for f in facts)
    apr = unsure_app.store.get_approval_for_run(run["run_id"])["approval_id"]
    code, body = unsure_app.engine.decide_approval(apr, ApprovalDecisionIn(decision="APPROVE", approver_id="u-fc-01"))
    assert code == 403 and any("reviewer of the AI judgement" in r for r in body["denied_reasons"])
    events = [e["event_type"] for e in unsure_app.audit.events(rid)]
    assert "review.recorded" in events
    with pytest.raises(ApprovalError):                         # the held run was superseded; reviewing it again needs a held run
        unsure_app.engine.review_judgement(run["run_id"], _review())


def test_reviewer_confirms_the_concern_and_the_case_stays_held(unsure_app):
    run = unsure_app.engine.review_judgement(_held(unsure_app), _review(decision="concern",
                                             reason="No incident record exists for this work order."))
    assert run["status"] == "NEEDS_INFORMATION" and run["attempt"] == 2


@pytest.mark.parametrize("kw,code", [({"reviewer_id": "u-ccm-01"}, 403),        # role not allowed to review
                                     ({"question": "pack_size"}, 422)])           # not an open question
def test_review_is_restricted(unsure_app, kw, code):
    from finagent.agent.graph import ApprovalError
    with pytest.raises(ApprovalError) as e:
        unsure_app.engine.review_judgement(_held(unsure_app), _review(**kw))
    assert e.value.http_status == code


def test_review_endpoint(unsure_app):
    c = TestClient(create_app(unsure_app))
    r = c.post(f"/runs/{_held(unsure_app)}/review", json=_review().model_dump())
    assert r.status_code == 201 and r.json()["status"] == "AWAITING_APPROVAL"
