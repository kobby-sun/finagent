"""Shared fixtures: every test session gets an isolated data dir and a frozen clock."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from finagent.config import load_settings
from finagent.evaluation import EVAL_OVERRIDES
from finagent.services import build_app


@pytest.fixture(scope="session")
def settings(tmp_path_factory):
    base = load_settings()
    d = tmp_path_factory.mktemp("finagent-data")
    s = base.with_overrides({**EVAL_OVERRIDES, "app.data_dir": str(d), "llm.provider": "mock"})
    if base.index_path.exists():
        shutil.copy(base.index_path, s.index_path)
    return s


@pytest.fixture(scope="session")
def app(settings):
    return build_app(settings)


@pytest.fixture()
def fresh_app(tmp_path, settings):
    """Function-scoped app on its own empty database (for isolation-sensitive tests)."""
    s = settings.with_overrides({"app.data_dir": str(tmp_path)})
    shutil.copy(settings.index_path, s.index_path)
    return build_app(s)


def load_case(settings, case_id: str) -> dict:
    import json
    return json.loads((settings.root / "mock_data" / "cases" / case_id / "request.json").read_text())
