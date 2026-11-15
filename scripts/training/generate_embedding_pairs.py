#!/usr/bin/env python3
"""Build contrastive training data for fine-tuning an embedding model on the finance corpus.

Design (honest evaluation first):
* TRAIN / VALID are SYNTHETIC queries generated from the corpus itself:
    - heading templates            ("what is the rule on tolerances for three-way matching")
    - inverse-cloze pseudo-queries (a sentence from the section with ~30% of words dropped)
    - the skills' curated retrieval queries -> each governing section of that skill
* TEST is the HAND-WRITTEN held-out set training/embeddings/natural_questions.yaml (never trained on),
  phrased like real AP-desk questions with little word overlap - it measures semantic recall.
* Hard negatives: BM25's best non-relevant chunks for the query, plus superseded / untrusted chunks
  (FIN-POL-003-OLD, ADV-001, ADV-002) whenever they compete - the model must learn to rank current
  policy above look-alikes. Trust is still enforced by the retriever regardless of the model.

Output (sentence-transformers triplet format):
  training/embeddings/{train,valid}.jsonl  rows: {anchor, positive, negative, positive_id, negative_id, source}
  training/embeddings/manifest.json
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from finagent.config import load_settings  # noqa: E402
from finagent.rag.index import BM25Index  # noqa: E402
from finagent.skills.base import REGISTRY  # noqa: E402
from finagent.workflow import load_pipeline  # noqa: E402

load_pipeline(load_settings())   # registers built-in skills AND plugin skills listed in config/workflow.yaml

OUT = ROOT / "training" / "embeddings"
TEMPLATES = ["{h}", "{t}: {h}", "what is the rule on {h}", "{h} requirements for supplier invoices",
             "policy guidance about {h} in accounts payable"]


def key(chunk_id: str) -> str:
    """Version-agnostic id: FIN-POL-002@v2.4#s2 -> FIN-POL-002#s2."""
    doc, sec = chunk_id.split("#")
    return f"{doc.split('@')[0]}#{sec.split('.')[0]}"


def load_index() -> BM25Index:
    return BM25Index.load(load_settings().index_path)


def natural_questions() -> list[dict]:
    return yaml.safe_load((OUT / "natural_questions.yaml").read_text())["questions"]


def synthetic_queries(idx: BM25Index, seed: int = 13) -> list[tuple[str, str, str]]:
    """(query, positive_chunk_id, source) for every authoritative chunk."""
    rng = random.Random(seed)
    out = []
    for c in idx.chunks:
        if c["trust"] != "authoritative":
            continue
        heading = re.sub(r"^\d+\.\s*", "", c["section"]).lower()
        for t in TEMPLATES:
            out.append((t.format(h=heading, t=c["title"].lower()), c["chunk_id"], "heading_template"))
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", c["text"]) if len(s.split()) >= 6]
        for s in sents[:4]:
            words = [w for w in re.findall(r"[A-Za-z0-9%$.,'-]+", s)]
            keep = [w for w in words if rng.random() > 0.3] or words
            out.append((" ".join(keep).lower().strip(".,"), c["chunk_id"], "inverse_cloze"))
    by_key = {key(c["chunk_id"]): c["chunk_id"] for c in idx.chunks}
    for spec in REGISTRY.values():
        for q in spec.retrieval_queries:
            for doc, sec in spec.compliance_refs:
                cid = by_key.get(f"{doc}#{sec}")
                if cid:
                    out.append((q, cid, f"skill_query:{spec.name}"))
    return out


def hard_negatives(idx: BM25Index, query: str, positives: set[str], n: int = 2) -> list[str]:
    ranked = [idx.chunks[i]["chunk_id"] for i, _ in idx.score(query)]
    negs = [c for c in ranked if c not in positives][:n]
    # look-alike non-authoritative chunks are the most valuable negatives when they match at all
    for c in ranked[:10]:
        if (c.startswith(("FIN-POL-003-OLD", "ADV-"))) and c not in negs and c not in positives:
            negs.insert(0, c)
    if not negs:
        negs = [c["chunk_id"] for c in idx.chunks if c["chunk_id"] not in positives][:n]
    return negs[:n + 1]


def build(seed: int = 13) -> dict:
    idx = load_index()
    text = {c["chunk_id"]: f"{c['title']}. {c['section']}. {c['text']}" for c in idx.chunks}
    test_qs = {q["q"].strip().lower() for q in natural_questions()}
    rows = {"train": [], "valid": []}
    # multi-positive map so a query's other positives are never used as its negatives
    positives_for: dict[str, set[str]] = {}
    syn = synthetic_queries(idx, seed)
    for q, cid, _ in syn:
        positives_for.setdefault(q, set()).add(cid)
    for q, cid, src in syn:
        if q.strip().lower() in test_qs:
            continue  # never leak held-out questions
        split = "valid" if int(hashlib.md5(q.encode()).hexdigest(), 16) % 10 == 0 else "train"
        for neg in hard_negatives(idx, q, positives_for[q]):
            rows[split].append({"anchor": q, "positive": text[cid], "negative": text[neg], "positive_id": cid,
                                "negative_id": neg, "source": src})
    OUT.mkdir(parents=True, exist_ok=True)
    for split, data in rows.items():
        with open(OUT / f"{split}.jsonl", "w", encoding="utf-8") as fh:
            for r in data:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    manifest = {"index_version": idx.index_version, "train_rows": len(rows["train"]), "valid_rows": len(rows["valid"]),
                "unique_train_queries": len({r["anchor"] for r in rows["train"]}),
                "test_questions": len(test_qs), "authoritative_chunks": sum(c["trust"] == "authoritative" for c in idx.chunks),
                "sources": sorted({r["source"].split(":")[0] for r in rows["train"]})}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
