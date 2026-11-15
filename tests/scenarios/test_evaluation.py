"""End-to-end scenarios FIN-001..FIN-042 (deterministic mock model)."""
import json

import pytest

from finagent.evaluation import run_scenario

SCEN = None


def _scenarios(settings):
    return json.loads((settings.root / "mock_data" / "scenarios.json").read_text())["scenarios"]


@pytest.fixture(scope="module")
def eval_app(settings, tmp_path_factory):
    import shutil
    from finagent.services import build_app
    d = tmp_path_factory.mktemp("scenarios")
    s = settings.with_overrides({"app.data_dir": str(d)})
    shutil.copy(settings.index_path, s.index_path)
    return build_app(s)


def pytest_generate_tests(metafunc):
    if "scenario" in metafunc.fixturenames:
        from finagent.config import load_settings
        scs = json.loads((load_settings().root / "mock_data" / "scenarios.json").read_text())["scenarios"]
        metafunc.parametrize("scenario", scs, ids=[s["case_id"] for s in scs])


def test_scenario(eval_app, scenario):
    r = run_scenario(eval_app, scenario, eval_app.settings.root)
    assert r["passed"], "\n".join(f"{c['check']}: {c['detail']}" for c in r["checks"] if not c["passed"])
