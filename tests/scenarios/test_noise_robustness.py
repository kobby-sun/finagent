"""Metamorphic noise test: every scenario x every noise type (finagent.noise) must reach the SAME outcome as the clean
case when the deterministic scripts decide (skill questions off). Noise must never change a decision.

Real-model robustness to the same noise is measured by: python scripts/dev/skill_model_check.py --noise max
"""
import json

import pytest

from finagent.noise import KINDS, variant
from finagent.schemas import ProcessingRequest


@pytest.fixture(scope="module")
def scripts_app(settings, tmp_path_factory):
    from finagent.services import build_app
    return build_app(settings.with_overrides({"app.data_dir": str(tmp_path_factory.mktemp("noise")), "skills.llm.enabled": False}))


def _outcome(app, req):
    r = app.engine.start_run(ProcessingRequest.model_validate(req))
    return r["status"], ((r.get("result") or {}).get("recommendation") or {}).get("outcome")


def test_noise_never_changes_the_scripts_decision(scripts_app, settings):
    scen = [s for s in json.loads((settings.root / "mock_data" / "scenarios.json").read_text())["scenarios"]
            if any(st["op"] == "start" for st in s["steps"])]
    changed, n = [], 0
    for sc in scen:
        req = json.loads((settings.root / sc["request_file"]).read_text())
        base = _outcome(scripts_app, req)
        for kind in KINDS:
            n += 1
            got = _outcome(scripts_app, variant(req, kind))
            if got != base:
                changed.append((sc["case_id"], kind, base, got))
    assert n >= 400 and not changed, changed
