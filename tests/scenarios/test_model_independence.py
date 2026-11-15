"""The core flow does not depend on the model.

For every scenario, run the first step twice: skill questions OFF (scripts decide alone) and ON. Then assert:
  1. the deterministic script results (findings, calculations, exceptions) are identical - the model cannot change them;
  2. the outcome with questions is never LESS cautious than scripts alone - any model can only add caution.
Together with the runner's validation this is why switching models can only change the extra judgement, never the core.
"""
import json

import pytest

from finagent.schemas import OUTCOME_PRECEDENCE, Outcome, ProcessingRequest

SCEN = None


def _scenarios(root):
    global SCEN
    if SCEN is None:
        SCEN = [s for s in json.loads((root / "mock_data" / "scenarios.json").read_text())["scenarios"]
                if any(st["op"] == "start" for st in s["steps"])]
    return SCEN


@pytest.fixture(scope="module")
def apps(settings, tmp_path_factory):
    from finagent.services import build_app
    off = build_app(settings.with_overrides({"app.data_dir": str(tmp_path_factory.mktemp("off")), "skills.llm.enabled": False}))
    on = build_app(settings.with_overrides({"app.data_dir": str(tmp_path_factory.mktemp("on"))}))
    return off, on


def _script_results(app, run_id):
    st = app.store.get_run(run_id)["state_json"]
    out = []
    for s in st.get("skills", []):
        out.append((s["skill"],
                    sorted((f["control"], f["status"]) for f in s["findings"] if not f["control"].startswith(("llm:", "llm_"))),
                    sorted((c["name"], str(c["result"]), c["passed"]) for c in s["calculations"]),
                    sorted(e["category"] for e in s["exceptions"] if not e["failed_rule"].startswith(f"{s['skill']}")),
                    ))
    return out


def test_core_flow_is_model_independent(apps, settings):
    off, on = apps
    checked = 0
    for sc in _scenarios(settings.root):
        req = json.loads((settings.root / sc["request_file"]).read_text())
        try:
            r_off = off.engine.start_run(ProcessingRequest.model_validate(req))
            r_on = on.engine.start_run(ProcessingRequest.model_validate(req))
        except Exception:          # requests rejected by validation (FIN-061..063) never reach the skills
            continue
        if not (r_off.get("result") and r_on.get("result")):
            continue
        assert _script_results(off, r_off["run_id"]) == _script_results(on, r_on["run_id"]), sc["case_id"]
        o_off = Outcome(r_off["result"]["recommendation"]["outcome"])
        o_on = Outcome(r_on["result"]["recommendation"]["outcome"])
        assert OUTCOME_PRECEDENCE[o_on] >= OUTCOME_PRECEDENCE[o_off], f"{sc['case_id']}: {o_off.value} -> {o_on.value}"
        checked += 1
    assert checked >= 60
