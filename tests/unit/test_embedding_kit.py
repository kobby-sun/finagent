"""Embedding fine-tuning kit: data hygiene and evaluator (offline parts; training itself runs in Docker)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "training"))

import generate_embedding_pairs as gp  # noqa: E402


def test_pairs_have_no_test_leakage_and_valid_negatives():
    gp.build()
    test_qs = {q["q"].strip().lower() for q in gp.natural_questions()}
    rows = [json.loads(l) for split in ("train", "valid") for l in open(gp.OUT / f"{split}.jsonl")]
    assert rows and not ({r["anchor"].strip().lower() for r in rows} & test_qs)
    assert all(r["positive_id"] != r["negative_id"] for r in rows)
    assert all(not r["positive_id"].startswith(("ADV-", "FIN-POL-003-OLD")) for r in rows)   # positives are authoritative


def test_every_authoritative_section_is_trained_and_superseded_used_as_negative():
    idx = gp.load_index()
    rows = [json.loads(l) for l in open(gp.OUT / "train.jsonl")]
    covered = {r["positive_id"] for r in rows}
    auth = {c["chunk_id"] for c in idx.chunks if c["trust"] == "authoritative"}
    assert auth <= covered
    assert any(r["negative_id"].startswith("FIN-POL-003-OLD") for r in rows)


def test_held_out_questions_reference_real_sections():
    keys = {gp.key(c["chunk_id"]) for c in gp.load_index().chunks}
    for q in gp.natural_questions():
        assert set(q.get("relevant") or []) <= keys, q["q"]


def test_evaluator_runs_offline_and_trust_holds():
    import eval_embeddings as ee
    rep = ee.evaluate(["hash"])
    for name, modes in rep.items():
        for m in modes.values():
            assert 0 <= m["recall@5"] <= 1 and m["questions"] >= 50
            if "hybrid" in modes:
                assert modes["hybrid"]["non_auth_top1"] == 0     # production retriever never ranks look-alikes first
