"""The skill pipeline can be re-organised by config, but policy-mandatory controls cannot be removed."""
import copy

import pytest
import yaml

from finagent.workflow import Pipeline, WorkflowError, load_pipeline


@pytest.fixture()
def cfg(settings):
    load_pipeline(settings)  # registers built-ins
    return yaml.safe_load((settings.root / "config" / "workflow.yaml").read_text())


def _find(cfg, name):
    for st in cfg["stages"]:
        for s in st["skills"]:
            if s["skill"] == name:
                return st, s
    raise KeyError(name)


def test_default_workflow_valid(cfg):
    from finagent.skills.base import REGISTRY
    p = Pipeline(cfg)
    enabled = {s.skill for s in p.steps if s.enabled}
    mandatory = {n for n, sp in REGISTRY.items() if sp.kind == "processor" and sp.mandatory}
    assert p.digest and mandatory <= enabled        # extra (plugin) skills may be added freely


def test_optional_skill_can_be_disabled(cfg):
    c = copy.deepcopy(cfg)
    _find(c, "payment_scheduling")[1]["enabled"] = False
    p = Pipeline(c)
    assert "payment_scheduling" not in [s.skill for s in p.steps if s.enabled]


def test_mandatory_control_cannot_be_disabled(cfg):
    c = copy.deepcopy(cfg)
    _find(c, "duplicate_detection")[1]["enabled"] = False
    with pytest.raises(WorkflowError, match="mandatory control"):
        Pipeline(c)


def test_mandatory_control_cannot_be_deleted(cfg):
    c = copy.deepcopy(cfg)
    st, s = _find(c, "authority_routing")
    st["skills"].remove(s)
    with pytest.raises(WorkflowError, match="missing from the workflow"):
        Pipeline(c)


def test_dependency_order_enforced(cfg):
    c = copy.deepcopy(cfg)
    st, s = _find(c, "fraud_indicator_scoring")
    st["skills"].remove(s)
    c["stages"][0]["skills"].insert(0, s)   # move before its dependencies
    with pytest.raises(WorkflowError, match="requires"):
        Pipeline(c)


def test_unknown_skill_and_condition_rejected(cfg):
    c = copy.deepcopy(cfg)
    c["stages"][0]["skills"].append({"skill": "does_not_exist"})
    _find(c, "three_way_match")[1]["when"] = "full_moon"
    with pytest.raises(WorkflowError) as e:
        Pipeline(c)
    assert "unknown skill" in str(e.value) and "unknown condition" in str(e.value)


def test_reordering_stages_is_allowed(cfg):
    c = copy.deepcopy(cfg)
    c["stages"][1]["skills"].reverse()   # vendor_compliance before duplicate_detection
    Pipeline(c)


def test_plan_respects_conditions(app):
    state = {"request": {"po_ref": None, "currency": "AUD", "document_type": "invoice"}, "invoice": {"doc_kind": "invoice"}}
    plan = [s.skill for s in app.pipeline.plan(state, app.settings)]
    assert "non_po_validation" in plan and "three_way_match" not in plan
