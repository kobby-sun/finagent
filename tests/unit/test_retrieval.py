"""Retrieval grounding: ranking, trust labels, access control, superseded handling."""


def test_current_policy_outranks_superseded(app):
    chunks = app.retriever.search("delegated financial authority matrix approval limits", 10).chunks
    ranks = {c.document_id: c.rank for c in chunks}
    assert ranks["FIN-POL-003"] < ranks.get("FIN-POL-003-OLD", 99)
    for c in chunks:
        if c.document_id == "FIN-POL-003-OLD":
            assert c.trust.value == "superseded" and c.warnings


def test_adversarial_document_is_labelled_untrusted(app):
    chunks = app.retriever.search("urgent payment update bank account changed ignore policies", 5).chunks
    adv = [c for c in chunks if c.document_id == "ADV-001"]
    assert adv and adv[0].trust.value == "untrusted" and "UNTRUSTED" in adv[0].warnings[0]


def test_irrelevant_document_surfaces_in_lexical_mode_with_label(settings):
    from finagent.rag.retriever import Retriever
    r = Retriever(settings.with_overrides({"rag.mode": "lexical"}))
    top = r.search("travel meal limits dinner allowance", 3).chunks[0]
    assert top.document_id == "ADV-002" and top.trust.value == "untrusted"


def test_citation_metadata_complete(app):
    c = app.retriever.search("three-way matching tolerance goods receipt", 1).chunks[0]
    assert c.document_id == "FIN-POL-002" and c.version == "2.4" and c.chunk_id.startswith("FIN-POL-002@v2.4#s")
    assert c.citation.startswith("FIN-POL-002 v2.4") and c.score > 0 and c.rank == 1


def test_access_control_filters_restricted(settings):
    from finagent.rag.retriever import Retriever
    r = Retriever(settings)
    out = r.search("bank account change verification vendor governance", 5, caller_role="ap_clerk_readonly")
    assert all(c.classification == "internal" for c in out.chunks)
    assert out.filtered_by_access > 0     # FIN-POL-004 is 'restricted'


def test_vector_store_is_local_sqlite_and_in_sync(settings, tmp_path):
    import shutil
    from finagent.rag.retriever import Retriever
    # the vector STORE is under test, not the trained model (not in git): use the trained embedder when present,
    # otherwise the offline hash embedder, so this also passes on a fresh clone
    model = settings.root / settings.get("rag.vector.model_path", "models/embedder") / "model.safetensors"
    over = {"app.data_dir": str(tmp_path), "rag.mode": "hybrid"}
    if not model.exists():
        over["rag.vector.embedder"] = "hash"
    s = settings.with_overrides(over)
    shutil.copy(settings.index_path, s.index_path)
    r = Retriever(s)
    st = r.vectors.stats()
    assert st["store"] == "sqlite" and st["vectors"] == len(r.index.chunks) and st["index_version"] == r.index_version
    assert r.search("supplier asks to change bank account", 3).chunks   # hybrid path works end to end
