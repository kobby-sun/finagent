"""Semantic search must never compromise accuracy: the configured retrieval is gated against BM25 on every measure."""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "training"))


def test_configured_search_at_least_as_accurate_as_bm25(settings):
    import accuracy_gate as g
    from finagent.rag.retriever import Retriever
    probe = Retriever(settings.with_overrides({"app.data_dir": str(settings.data_dir / "probe")}))
    if probe.mode == "lexical" and settings.get("rag.mode") != "lexical":
        pytest.skip(f"semantic search unavailable here ({probe.degraded}) - fallback is BM25, nothing to compare")
    base = g.measure({"rag.mode": "lexical"})
    cand = g.measure({})
    ok, why = g.verdict(cand, base)
    assert ok, f"configured retrieval is less accurate than BM25: {why}"
    assert cand["curated_recall"] == 1.0 and cand["non_auth_top1"] == 0


def test_missing_model_falls_back_to_keyword_search(settings, tmp_path):
    from finagent.rag.retriever import Retriever
    s = settings.with_overrides({"app.data_dir": str(tmp_path), "rag.mode": "hybrid", "rag.vector.embedder": "sentence_transformers",
                                 "rag.vector.model_path": "training/embeddings/does-not-exist"})
    shutil.copy(settings.index_path, s.index_path)
    r = Retriever(s)
    assert r.mode == "lexical" and r.degraded and r.describe()["configured_mode"] == "hybrid"
    assert r.search("three-way matching tolerance", 3).chunks[0].document_id == "FIN-POL-002"


def test_policy_edit_is_not_applied_live_only_after_a_restart(settings, tmp_path):
    """Policy changes are system maintenance: a running service keeps its index; a restart picks the edit up."""
    from finagent.rag.retriever import Retriever
    corpus = tmp_path / "corpus"
    shutil.copytree(settings.root / "finance_rag_corpus", corpus)
    s = settings.with_overrides({"app.data_dir": str(tmp_path / "d"), "rag.mode": "lexical",
                                 "rag.corpus_dir": str(corpus), "rag.sources": [{"type": "local_dir", "path": str(corpus)}]})
    r = Retriever(s)
    v1 = r.index_version
    (corpus / "13_timesheets.md").write_text("---\ndocument_id: FIN-POL-013\ntitle: Contractor Timesheets\nversion: 1.0\n"
                                             "status: current\nclassification: internal\n---\n## 2. Approved timesheet required\n"
                                             "Contractor invoices need an approved timesheet covering the billed hours.\n")
    assert r.index_version == v1                                              # running service: unchanged
    assert not any(c.document_id == "FIN-POL-013" for c in r.search("contractor timesheet approved hours", 5).chunks)
    restarted = Retriever(s)                                                   # restart: rebuilt from the corpus
    assert restarted.index_version != v1
    assert restarted.search("contractor timesheet approved hours", 5).chunks[0].document_id == "FIN-POL-013"


def test_ingest_without_trained_model_falls_back_instead_of_crashing(settings, tmp_path, monkeypatch, capsys):
    """Fresh clone: no models/embedder -> `finagent ingest` still builds the BM25 index and exits 0."""
    import argparse
    from finagent import cli
    s = settings.with_overrides({"app.data_dir": str(tmp_path), "rag.mode": "hybrid",
                                 "rag.vector.embedder": "sentence_transformers", "rag.vector.model_path": str(tmp_path / "no-model")})
    monkeypatch.setattr(cli, "load_settings", lambda: s)
    cli.cmd_ingest(argparse.Namespace())
    out = capsys.readouterr()
    assert "skipped" in out.out and (tmp_path / "index" / "bm25_index.json").exists()


def _fake_retriever(version, mode="hybrid"):
    from types import SimpleNamespace
    return SimpleNamespace(mode=mode, index_version=version, degraded=None)


def test_startup_keeps_search_model_trained_on_current_corpus(settings):
    from finagent.services import ensure_embedder_current
    stamp = json.loads((settings.path("rag.vector.model_path") / "trained_on.json").read_text())["index_version"]
    r = ensure_embedder_current(settings, _fake_retriever(stamp))
    assert r.mode == "hybrid" and r.degraded is None


def test_startup_with_changed_corpus_never_uses_an_unverified_model(settings):
    """Corpus changed since training and retrain is off (or impossible) -> keyword search, with the reason reported."""
    from finagent.services import ensure_embedder_current
    s = settings.with_overrides({"rag.retrain_on_startup": False})
    r = ensure_embedder_current(s, _fake_retriever("corpus-has-changed"))
    assert r.mode == "lexical" and "older policy corpus" in r.degraded
