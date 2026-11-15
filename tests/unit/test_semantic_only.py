"""Cases only the embedding model can solve: keyword search (BM25) misses them, the configured hybrid search finds them.

Skipped when semantic search is unavailable (fresh clone without models/embedder): there is nothing to test.
Fixture: tests/fixtures/semantic_only_queries.yaml (how the cases were selected is documented there).
"""
import shutil
from pathlib import Path

import pytest
import yaml

CASES = yaml.safe_load((Path(__file__).resolve().parents[1] / "fixtures" / "semantic_only_queries.yaml").read_text())
TOP_K = 5


def _key(chunk_id: str) -> str:
    return chunk_id.split("@")[0] + "#" + chunk_id.split("#")[1].split(".")[0]


@pytest.fixture(scope="module")
def retrievers(settings, tmp_path_factory):
    from finagent.rag.retriever import Retriever
    d = tmp_path_factory.mktemp("semantic-only")
    (d / "index").mkdir()
    shutil.copy(settings.index_path, d / "index" / "bm25_index.json")
    hybrid = Retriever(settings.with_overrides({"app.data_dir": str(d), "rag.mode": "hybrid"}))
    if hybrid.mode != "hybrid":
        pytest.skip(f"semantic search unavailable ({hybrid.degraded}) - train the model with `make retrain`")
    lexical = Retriever(settings.with_overrides({"app.data_dir": str(d), "rag.mode": "lexical"}))
    return lexical, hybrid


def _top(r, query):
    return [_key(c.chunk_id) for c in r.search(query, TOP_K).chunks]


@pytest.mark.parametrize("case", CASES["semantic_only"], ids=[c["expect"] for c in CASES["semantic_only"]])
def test_keyword_search_alone_misses_it(retrievers, case):
    """Guards the premise: if BM25 starts finding it (corpus or tokenizer change), the case no longer proves anything."""
    lexical, _ = retrievers
    assert case["expect"] not in _top(lexical, case["query"]), "BM25 now finds this - not a semantic-only case any more"


@pytest.mark.parametrize("case", CASES["semantic_only"], ids=[c["expect"] for c in CASES["semantic_only"]])
def test_embedding_model_finds_it(retrievers, case):
    _, hybrid = retrievers
    top = _top(hybrid, case["query"])
    assert case["expect"] in top, f"'{case['query']}' -> {top}"


@pytest.mark.xfail(strict=True, reason="known fusion gap: vector ranks it top-4, hybrid fusion pushes it below 5")
@pytest.mark.parametrize("case", CASES["known_gaps"], ids=[c["expect"] for c in CASES["known_gaps"]])
def test_known_fusion_gaps(retrievers, case):
    _, hybrid = retrievers
    assert case["expect"] in _top(hybrid, case["query"])
