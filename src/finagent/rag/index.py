"""BM25 lexical index (pure Python, persisted as JSON) with optional dense
embeddings fused by Reciprocal Rank Fusion.

Why BM25 first: finance policy questions are dominated by exact terms
("three-way", "tolerance", "bank account", "AUD 50,000"); BM25 is
deterministic, explainable and needs no external service, which makes the
evaluation reproducible. Dense retrieval (Ollama embeddings) can be switched on
for paraphrase recall via ``rag.embeddings: ollama``.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

from .ingest import DocChunk, load_corpus, load_sources

TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
STOPWORDS = set("""a an and are as at be by for from has have in is it its of on or that the this to was were will with
must may not no any all only be been being which who whom when where what does do""".split())

SYNONYMS = {  # tiny domain thesaurus applied to queries only
    "duplicate": ["duplicate", "duplicates", "match"],
    "approval": ["approval", "approve", "approver", "authority", "delegated"],
    "bank": ["bank", "account", "payment-instructions"],
    "receipt": ["receipt", "receipted", "received", "goods"],
    "fx": ["foreign", "currency", "exchange", "conversion"],
    "po": ["purchase-order", "purchase", "order"],
}


def tokenize(text: str) -> list[str]:
    toks = []
    for t in TOKEN_RE.findall(text.lower()):
        if t in STOPWORDS:
            continue
        toks.append(t)
        if "-" in t:  # index hyphenated terms and their parts
            toks.extend(p for p in t.split("-") if p and p not in STOPWORDS)
    return toks


def expand_query(tokens: list[str]) -> list[str]:
    out = list(tokens)
    for t in tokens:
        out.extend(SYNONYMS.get(t, []))
    return out


class BM25Index:
    def __init__(self, chunks: list[dict], index_version: str, k1: float = 1.4, b: float = 0.7):
        self.chunks = chunks
        self.index_version = index_version
        self.k1, self.b = k1, b
        self.doc_tokens = [tokenize(c["index_text"]) for c in chunks]
        self.tf = [Counter(toks) for toks in self.doc_tokens]
        self.dl = [len(t) for t in self.doc_tokens]
        self.avgdl = sum(self.dl) / max(len(self.dl), 1)
        df: Counter = Counter()
        for toks in self.doc_tokens:
            df.update(set(toks))
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: str) -> list[tuple[int, float]]:
        q = expand_query(tokenize(query))
        scores = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            for t in set(q):
                if t not in tf:
                    continue
                f = tf[t]
                s += self.idf.get(t, 0) * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * self.dl[i] / self.avgdl))
            if s > 0:
                scores.append((i, s))
        return sorted(scores, key=lambda x: (-x[1], self.chunks[x[0]]["chunk_id"]))

    # -- persistence ---------------------------------------------------------
    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"index_version": self.index_version, "k1": self.k1, "b": self.b,
                                    "chunks": self.chunks}, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["chunks"], data["index_version"], data.get("k1", 1.4), data.get("b", 0.7))

    @classmethod
    def build(cls, corpus_dir: Path, k1: float = 1.4, b: float = 0.7) -> "BM25Index":
        chunks, version = load_corpus(corpus_dir)
        return cls([c.to_dict() for c in chunks], version, k1, b)


def build_and_save(corpus_dir: Path, index_path: Path, k1: float = 1.4, b: float = 0.7,
                   sources: list[dict] | None = None, root: Path | None = None) -> dict:
    """Build the index from ``rag.sources`` (connectors) - or a single directory - and persist it."""
    sources = sources or [{"type": "local_dir", "path": str(corpus_dir), "name": "corpus"}]
    chunks, version, report = load_sources(sources, root or corpus_dir)
    idx = BM25Index([c.to_dict() for c in chunks], version, k1, b)
    idx.save(index_path)
    docs = sorted({c["document_id"] for c in idx.chunks})
    trust = {}
    for c in idx.chunks:
        trust[c["trust"]] = trust.get(c["trust"], 0) + 1
    return {"index_version": idx.index_version, "chunks": len(idx.chunks), "documents": len(docs), "sources": report,
            "chunks_by_trust": trust, "document_ids": docs, "index_path": str(index_path)}
