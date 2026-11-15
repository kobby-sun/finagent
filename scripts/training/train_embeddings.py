#!/usr/bin/env python3
"""Fine-tune a small embedding model on the finance corpus (contrastive, in-batch + hard negatives).

    pip install 'finagent[embeddings]'          # sentence-transformers (+ torch)
    python scripts/training/train_embeddings.py --base BAAI/bge-small-en-v1.5 --epochs 3
    python scripts/training/eval_embeddings.py --embedders hash st:models/embedder

Uses MultipleNegativesRankingLoss on (anchor, positive, hard negative) triplets from
generate_embedding_pairs.py, validates with an InformationRetrievalEvaluator on the synthetic VALID
split, and saves to models/embedder. The held-out natural questions are only used by
eval_embeddings.py. Enable in config:  rag.vector.embedder: sentence_transformers
                                        rag.vector.model_path: models/embedder
then `finagent ingest` (vectors are rebuilt automatically when the embedder changes).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "training" / "embeddings"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="BAAI/bge-small-en-v1.5")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=2e-5)
    a = ap.parse_args()
    try:
        from datasets import Dataset
        from sentence_transformers import SentenceTransformer, SentenceTransformerTrainer, SentenceTransformerTrainingArguments
        from sentence_transformers.evaluation import InformationRetrievalEvaluator
        from sentence_transformers.losses import MultipleNegativesRankingLoss
    except ImportError:
        sys.exit("pip install 'finagent[embeddings]' (sentence-transformers, datasets) first")
    if not (OUT / "train.jsonl").exists():
        sys.exit("run scripts/training/generate_embedding_pairs.py first")
    load = lambda p: [json.loads(l) for l in open(p, encoding="utf-8")]
    train, valid = load(OUT / "train.jsonl"), load(OUT / "valid.jsonl")
    model = SentenceTransformer(a.base)
    ds = Dataset.from_list([{"anchor": r["anchor"], "positive": r["positive"], "negative": r["negative"]} for r in train])
    corpus = {r["positive_id"]: r["positive"] for r in train + valid} | {r["negative_id"]: r["negative"] for r in train + valid}
    queries = {f"v{i}": r["anchor"] for i, r in enumerate(valid)}
    relevant = {f"v{i}": {r["positive_id"]} for i, r in enumerate(valid)}
    evaluator = InformationRetrievalEvaluator(queries, corpus, relevant, name="finance-valid")
    args = SentenceTransformerTrainingArguments(output_dir=str(OUT / "checkpoints"), num_train_epochs=a.epochs,
                                                per_device_train_batch_size=a.batch_size, learning_rate=a.lr,
                                                warmup_ratio=0.1, eval_strategy="epoch", save_strategy="no",
                                                batch_sampler="no_duplicates", report_to=[])
    trainer = SentenceTransformerTrainer(model=model, args=args, train_dataset=ds, loss=MultipleNegativesRankingLoss(model),
                                         evaluator=evaluator)
    trainer.train()
    dest = ROOT / "models" / "embedder"   # shipped with the code; fp16 keeps it under 100 MB (same accuracy, see accuracy_gate.py)
    model.half()
    model.save(str(dest))
    # stamp: the policy-corpus version this model was trained on; start-up retrains when the corpus has moved on
    trained_on = json.loads((OUT / "manifest.json").read_text())["index_version"]
    (dest / "trained_on.json").write_text(json.dumps({"index_version": trained_on}) + "\n")
    print(json.dumps(evaluator(model), indent=2, default=str))
    print(f"saved {dest}; next: python scripts/training/eval_embeddings.py --embedders hash st:models/embedder")


if __name__ == "__main__":
    main()
