#!/usr/bin/env python3
"""Evaluate embedders on the HELD-OUT natural questions (training/embeddings/natural_questions.yaml).

    python scripts/training/eval_embeddings.py                      # lexical + hash (offline)
    python scripts/training/eval_embeddings.py --embedders hash ollama st:models/embedder

For each embedder it reports, in two modes:
  dense  - vector search alone (what the embedding model contributes)
  hybrid - the real retriever (BM25 + vectors + access filter + trust re-rank), i.e. production behaviour
metrics: recall@1, recall@5, MRR@10 (any relevant section counts), and non_auth_top1 = how often a
superseded/untrusted chunk is ranked first (must stay 0 in hybrid mode).
Writes training/embeddings/eval_report.json. Adopt a new embedder only if hybrid recall@5 does not drop
and non_auth_top1 stays 0.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "training"))
from finagent.config import load_settings  # noqa: E402
from finagent.rag.retriever import Retriever  # noqa: E402
from finagent.rag.vectorstore import make_embedder  # noqa: E402
from generate_embedding_pairs import key, natural_questions  # noqa: E402


def _metrics(ranked_keys: list[list[str]], ranked_trust: list[list[str]], qs: list[dict]) -> dict:
    r1 = r5 = mrr = nat = n = 0
    for keys, trust, q in zip(ranked_keys, ranked_trust, qs):
        rel = set(q.get("relevant") or [])
        if not rel:
            continue
        n += 1
        hits = [i for i, k in enumerate(keys[:10]) if k in rel]
        r1 += bool(hits and hits[0] == 0)
        r5 += bool(hits and hits[0] < 5)
        mrr += (1 / (hits[0] + 1)) if hits else 0
        nat += bool(trust and trust[0] != "authoritative")
    return {"questions": n, "recall@1": round(r1 / n, 3), "recall@5": round(r5 / n, 3), "mrr@10": round(mrr / n, 3),
            "non_auth_top1": nat}


def evaluate(embedders: list[str]) -> dict:
    base = load_settings()
    qs = natural_questions()
    report = {}
    # lexical baseline (BM25 only, with trust re-rank)
    tmp = Path(tempfile.mkdtemp(prefix="emb-eval-"))
    s = base.with_overrides({"app.data_dir": str(tmp), "rag.mode": "lexical"})
    shutil.copy(base.index_path, s.index_path)
    r = Retriever(s)
    res = [r.search(q["q"], 10).chunks for q in qs]
    report["lexical (BM25)"] = {"hybrid": _metrics([[key(c.chunk_id) for c in x] for x in res], [[c.trust.value for c in x] for x in res], qs)}
    for name in embedders:
        overrides = {"app.data_dir": str(Path(tempfile.mkdtemp(prefix="emb-eval-"))), "rag.mode": "hybrid"}
        if name.startswith("st:"):
            overrides.update({"rag.vector.embedder": "sentence_transformers", "rag.vector.model_path": name[3:]})
        else:
            overrides["rag.vector.embedder"] = name
        s = base.with_overrides(overrides)
        shutil.copy(base.index_path, s.index_path)
        try:
            r = Retriever(s)
        except Exception as exc:  # embedder unavailable (e.g. Ollama not running, model not trained)
            report[name] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        hy = [r.search(q["q"], 10).chunks for q in qs]
        emb = make_embedder(s)
        trust = {c["chunk_id"]: c["trust"] for c in r.index.chunks}
        dense = [r.vectors.query(emb.embed([q["q"]])[0], 10) for q in qs]
        report[name] = {
            "dense": _metrics([[key(cid) for cid, _ in d] for d in dense], [[trust[cid] for cid, _ in d] for d in dense], qs),
            "hybrid": _metrics([[key(c.chunk_id) for c in x] for x in hy], [[c.trust.value for c in x] for x in hy], qs),
        }
    out = ROOT / "training" / "embeddings" / "eval_report.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedders", nargs="*", default=["hash"])
    rep = evaluate(ap.parse_args().embedders)
    print(f"{'embedder':<34}{'mode':<8}{'R@1':>6}{'R@5':>6}{'MRR':>7}{'non-auth@1':>12}")
    for name, modes in rep.items():
        if "error" in modes:
            print(f"{name:<34}unavailable: {modes['error'][:70]}")
            continue
        for mode, m in modes.items():
            print(f"{name:<34}{mode:<8}{m['recall@1']:>6}{m['recall@5']:>6}{m['mrr@10']:>7}{m['non_auth_top1']:>12}")


if __name__ == "__main__":
    main()
