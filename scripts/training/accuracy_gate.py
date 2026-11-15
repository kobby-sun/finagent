#!/usr/bin/env python3
"""Search-accuracy gate: semantic search may only be enabled if it is at least as accurate as keyword search (BM25).

    python scripts/training/accuracy_gate.py                       # check the CONFIGURED retrieval vs BM25 (exit 1 if worse)
    python scripts/training/accuracy_gate.py --candidates "hybrid:2" "hybrid:4" "vector:1"   # compare options

Measures (all must be >= the BM25 baseline, and non_auth_top1 must be 0):
  curated_recall  - every control's governing policy sections found by its own search queries (top-5)
  R@1, R@5, MRR   - on the hand-written held-out questions (training/embeddings/natural_questions.yaml)
  non_auth_top1   - times an untrusted / superseded document is ranked first (must be 0)
  semantic_only   - share of tests/fixtures/semantic_only_queries.yaml found in the top 5: paraphrases keyword search
                    cannot find (BM25 scores ~0 by construction). A semantic candidate must keep >= SEMANTIC_ONLY_MIN,
                    so a retrain can never quietly lose what the embedding model is there for.
Writes training/embeddings/accuracy_gate.json.
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
from eval_embeddings import _metrics  # noqa: E402
from finagent.config import load_settings  # noqa: E402
from finagent.evaluation import rag_metrics  # noqa: E402
from finagent.services import build_app  # noqa: E402
from generate_embedding_pairs import key, natural_questions  # noqa: E402

KEYS = ["curated_recall", "recall@1", "recall@5", "mrr@10"]
SEMANTIC_ONLY_MIN = 0.8
SEMANTIC_ONLY = ROOT / "tests" / "fixtures" / "semantic_only_queries.yaml"


def _semantic_only(app) -> float:
    import yaml
    cases = yaml.safe_load(SEMANTIC_ONLY.read_text())["semantic_only"]
    hits = sum(c["expect"] in [key(x.chunk_id) for x in app.retriever.search(c["query"], 5).chunks] for c in cases)
    return round(hits / len(cases), 3)


def measure(overrides: dict) -> dict:
    base = load_settings()
    s = base.with_overrides({"app.data_dir": tempfile.mkdtemp(prefix="gate-"), **overrides})
    if base.index_path.exists():
        shutil.copy(base.index_path, s.index_path)
    app = build_app(s)
    if app.retriever.mode != s.get("rag.mode"):
        return {"error": f"requested {s.get('rag.mode')} but retriever fell back to {app.retriever.mode}: {app.retriever.degraded}"}
    rag = rag_metrics(app)
    qs = natural_questions()
    res = [app.retriever.search(q["q"], 10).chunks for q in qs]
    m = _metrics([[key(c.chunk_id) for c in x] for x in res], [[c.trust.value for c in x] for x in res], qs)
    return {"curated_recall": rag["skill_policy_recall_at_k"], "curated_misses": rag["misses"], **m,
            "semantic_only": _semantic_only(app)}


def verdict(cand: dict, base: dict) -> tuple[bool, list[str]]:
    if "error" in cand:
        return False, [cand["error"]]
    worse = [f"{k} {cand[k]} < BM25 {base[k]}" for k in KEYS if cand[k] < base[k]]
    if cand["non_auth_top1"]:
        worse.append(f"non_auth_top1 = {cand['non_auth_top1']}")
    if cand.get("semantic_only", 1) < SEMANTIC_ONLY_MIN and cand is not base:
        worse.append(f"semantic_only {cand['semantic_only']} < {SEMANTIC_ONLY_MIN}")
    return not worse, worse


def parse(c: str) -> dict:
    mode, _, w = c.partition(":")
    o = {"rag.mode": mode}
    if mode != "lexical":
        o.update({"rag.vector.embedder": "sentence_transformers", "rag.fusion.vector_weight": float(w or 1)})
    return o


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", nargs="*", help='e.g. "hybrid:2" "hybrid:4" "vector:1" (default: the configured settings)')
    a = ap.parse_args()
    base = measure({"rag.mode": "lexical"})
    s = load_settings()
    cands = {c: parse(c) for c in a.candidates} if a.candidates else {
        f"configured ({s.get('rag.mode')}, {s.get('rag.vector.embedder')}, w={s.get('rag.fusion.vector_weight')})": {}}
    rows, report, ok_all = [("BM25 baseline", base, True, [])], {"baseline": base}, True
    for name, ov in cands.items():
        m = measure(ov)
        ok, why = verdict(m, base)
        rows.append((name, m, ok, why))
        report[name] = {**m, "passes_gate": ok, "reasons": why}
        ok_all &= ok
    print(f"{'retrieval':<46}{'curated':>9}{'R@1':>7}{'R@5':>7}{'MRR':>7}{'non-auth@1':>12}{'sem-only':>10}  gate")
    for name, m, ok, why in rows:
        if "error" in m:
            print(f"{name:<46}  ERROR: {m['error']}")
            continue
        print(f"{name:<46}{m['curated_recall']:>9}{m['recall@1']:>7}{m['recall@5']:>7}{m['mrr@10']:>7}{m['non_auth_top1']:>12}{m['semantic_only']:>10}  "
              f"{'baseline' if name.startswith('BM25') else ('PASS' if ok else 'FAIL: ' + '; '.join(why))}")
    (ROOT / "training" / "embeddings" / "accuracy_gate.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
