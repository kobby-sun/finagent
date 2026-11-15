"""Per-run diagrams (svg/pdf/png) and automatic re-indexing when the corpus changes."""
import shutil

import pytest
from fastapi.testclient import TestClient

from finagent.api.app import create_app
from finagent.rundiagram import render
from finagent.schemas import ProcessingRequest
from tests.conftest import load_case


@pytest.mark.parametrize("fmt,magic", [("svg", b"<svg"), ("pdf", b"%PDF"), ("png", b"\x89PNG")])
def test_render_all_formats(fresh_app, fmt, magic):
    run = fresh_app.engine.start_run(ProcessingRequest.model_validate(load_case(fresh_app.settings, "FIN-003")))
    data = render(fresh_app.engine.get_run(run["run_id"]), fmt)
    assert data.startswith(magic) and len(data) > 1000
    if fmt == "svg":
        txt = data.decode()
        assert "HOLD_FINALIZE" in txt and "EXIT" in txt and "fraud_indicator_scoring" in txt and "WARNING" in txt


def test_diagram_endpoint_and_auto_render(fresh_app):
    fresh_app.settings.raw["app"]["run_diagrams"] = ["svg"]
    c = TestClient(create_app(fresh_app))
    run = c.post("/runs", json=load_case(fresh_app.settings, "FIN-001")).json()
    r = c.get(f"/runs/{run['run_id']}/diagram", params={"format": "png"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert any((fresh_app.settings.data_dir / "diagrams").glob(f"FIN-001_{run['run_id']}.svg"))
    fresh_app.settings.raw["app"]["run_diagrams"] = []


def test_index_rebuilds_when_corpus_changes(settings, tmp_path):
    from finagent.rag.retriever import Retriever
    corpus = tmp_path / "corpus"
    shutil.copytree(settings.root / "finance_rag_corpus", corpus)
    s = settings.with_overrides({"app.data_dir": str(tmp_path / "d"), "rag.corpus_dir": str(corpus),
                                 "rag.sources": [{"type": "local_dir", "path": str(corpus)}]})
    v1 = Retriever(s).index_version
    (corpus / "13_new.md").write_text("---\ndocument_id: FIN-POL-013\ntitle: New\nversion: 1.0\nstatus: current\n"
                                      "classification: internal\n---\n## 1. Rule\nContractor timesheets are required.\n")
    r2 = Retriever(s)
    assert r2.index_version != v1 and any(c["document_id"] == "FIN-POL-013" for c in r2.index.chunks)
