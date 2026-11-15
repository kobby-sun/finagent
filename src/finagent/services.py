"""Composition root: builds every component from Settings."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass

from .agent.graph import Engine
from .audit import AuditLog
from .config import Settings, load_settings
from .governance.baseline import PolicyBaseline
from .llm.providers import LLMProvider, make_provider
from .rag.retriever import Retriever
from .store import Store
from .tools.base import ToolRunner
from .tools.finance_tools import build_tools
from .tools.mock_backend import MockBackend
from .webhooks import Webhooks
from .workflow import Pipeline, load_pipeline


@dataclass
class App:
    settings: Settings
    store: Store
    audit: AuditLog
    retriever: Retriever
    backend: MockBackend
    tools: ToolRunner
    llm: LLMProvider
    webhooks: Webhooks
    engine: Engine
    pipeline: Pipeline
    policy: PolicyBaseline


def skill_models(settings: Settings, fault_lookup=None):
    """The two models that answer skill questions: SMALL (every question) and LARGE (second opinion; optional).
    skills.llm.use_mock: true pins both to the deterministic mock (tests, eval, fast demos)."""
    def tier(key):
        p = "mock" if settings.get("skills.llm.use_mock", False) else settings.get(f"skills.llm.{key}.provider")
        if not p or p == "none":
            return None
        over = {"llm.provider": p}
        if p != "mock" and settings.get(f"skills.llm.{key}.model"):
            over[f"llm.providers.{p}.model"] = settings.get(f"skills.llm.{key}.model")
        return make_provider(settings.with_overrides(over), fault_lookup=fault_lookup)
    return tier("small"), tier("large")


def ensure_embedder_current(settings: Settings, retriever: Retriever) -> Retriever:
    """Start-up: the fine-tuned search model must have been trained on the CURRENT policy corpus.

    Its stamp (models/embedder/trained_on.json) is compared with the index version. If the corpus has changed, the
    gated retrain pipeline runs (scripts/training/retrain.py: new model kept only if it is at least as accurate). If that
    is disabled, not possible or fails, this process uses keyword search (BM25) - never an unverified model."""
    if retriever.mode == "lexical" or settings.get("rag.vector.embedder") != "sentence_transformers":
        return retriever
    stamp = settings.path("rag.vector.model_path") / "trained_on.json"
    trained_on = json.loads(stamp.read_text()).get("index_version") if stamp.exists() else None
    if trained_on == retriever.index_version:
        return retriever
    print(f"[finagent] policy corpus changed since the search model was trained ({trained_on} -> {retriever.index_version})")
    if settings.get("rag.retrain_on_startup", True) and not os.environ.get("FINAGENT_IN_RETRAIN"):
        print("[finagent] retraining the search model (gated) before serving ...")
        rc = subprocess.run([sys.executable, "scripts/training/retrain.py", "--embeddings", "host"], cwd=settings.root,
                            env={**os.environ, "FINAGENT_IN_RETRAIN": "1"}).returncode
        fresh = json.loads(stamp.read_text()).get("index_version") if stamp.exists() else None
        if rc == 0 and fresh == retriever.index_version:
            return Retriever(settings)
        print("[finagent] retrain did not produce a model for the current corpus")
    retriever.mode = "lexical"
    retriever.degraded = "search model trained on an older policy corpus - keyword search (BM25) until it is retrained"
    print(f"[finagent] WARNING {retriever.degraded}")
    return retriever


def build_app(settings: Settings | None = None) -> App:
    settings = settings or load_settings()
    clock = settings.now
    store = Store(settings.db_path)
    audit = AuditLog(store, settings.audit_jsonl_path, clock)
    retriever = ensure_embedder_current(settings, Retriever(settings))   # start-up: index + search model match the corpus
    backend = MockBackend(settings.path("mock.data_dir"), float(settings.get("mock.timeout_sleep_s", 1.5)))
    tools = build_tools(settings, audit, store, retriever, backend)
    llm = make_provider(settings, fault_lookup=backend.llm_fault)
    webhooks = Webhooks(settings, store, audit, clock)
    pipeline = load_pipeline(settings)
    policy = PolicyBaseline(settings, retriever)
    if policy.changes():
        print(f"[finagent] WARNING policy text differs from config/policy_baseline.yaml - every case will be HELD "
              f"until policy maintenance is done ({len(policy.changes())} section(s); finagent policy status)")
    small, large = skill_models(settings, backend.llm_fault)
    if settings.get("skills.llm.enabled", True):
        from .llm.providers import probe_pair
        st = probe_pair(small, large)
        for t in (st["small"], st["large"]):
            if t and (not t["reachable"] or t.get("model_pulled") is False):
                print(f"[finagent] WARNING skill model {t['provider']}/{t['model']} not usable: {t.get('hint')}")
        if not st["approved"]:
            print(f"[finagent] WARNING {st['approval_hint']}")
    from .skills.llm_runner import SkillRunner
    engine = Engine(settings, store, audit, tools, llm, retriever, webhooks, pipeline, policy,
                    SkillRunner(settings, small, large, audit))
    return App(settings, store, audit, retriever, backend, tools, llm, webhooks, engine, pipeline, policy)
