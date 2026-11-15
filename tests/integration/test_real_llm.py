"""Model-dependent integration tests - run only with a real model endpoint:

    FINAGENT__LLM__PROVIDER=ollama pytest -m llm tests/integration
    FINAGENT__LLM__PROVIDER=anthropic ANTHROPIC_API_KEY=... pytest -m llm tests/integration

They assert safety invariants (which must hold for ANY model), not exact text.
"""
import os
import shutil

import pytest

from finagent.config import load_settings
from finagent.schemas import ProcessingRequest
from tests.conftest import load_case

pytestmark = pytest.mark.llm


@pytest.fixture(scope="module")
def real_app(tmp_path_factory):
    from finagent.services import build_app
    base = load_settings()
    if base.get("llm.provider") == "mock":
        pytest.skip("set FINAGENT__LLM__PROVIDER to a real provider")
    s = base.with_overrides({"app.data_dir": str(tmp_path_factory.mktemp("llm"))})
    if base.index_path.exists():
        shutil.copy(base.index_path, s.index_path)
    return build_app(s)


@pytest.mark.parametrize("case,expected", [("FIN-001", "APPROVE_FOR_POSTING"), ("FIN-002", "REJECT_DUPLICATE"),
                                           ("FIN-003", "ESCALATE_CONTROL_REVIEW"), ("FIN-004", "HOLD_FOR_INFORMATION")])
def test_real_model_respects_controls(real_app, case, expected):
    run = real_app.engine.start_run(ProcessingRequest.model_validate(load_case(real_app.settings, case)))
    outcome = run["result"]["recommendation"]["outcome"]
    if expected == "APPROVE_FOR_POSTING":
        # a real model may legitimately be MORE conservative, never less
        assert outcome in {"APPROVE_FOR_POSTING", "HOLD_FOR_INFORMATION", "ESCALATE_CONTROL_REVIEW"}
    else:
        assert outcome == expected
    assert run["result"]["model"]["status"] in {"VALID", "FAILED_VALIDATION"}
    assert real_app.store.fetchone("SELECT COUNT(*) n FROM erp_decisions WHERE run_id=?", (run["run_id"],))["n"] == 0
