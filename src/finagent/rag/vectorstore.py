"""Embeddings + vector store abstraction.

POC default: embeddings live in a local SQLite file (``data/index/vectors.db``)
as float32 BLOBs and are searched with brute-force cosine similarity. That is
plenty for a few thousand policy chunks and needs no extra service.

Path to a dedicated vector DB
-----------------------------
Implement :class:`VectorStore` (four methods) and register it::

    @register_vector_store("pgvector")
    class PgVectorStore(VectorStore):
        def __init__(self, settings): ...  # psycopg connect using rag.vector.dsn
        def upsert(self, items): ...       # INSERT ... ON CONFLICT (chunk_id) DO UPDATE
        def query(self, vector, top_k, filters=None): ...  # ORDER BY embedding <=> %s LIMIT %s
        def delete_documents(self, document_ids): ...
        def stats(self): ...

then set ``rag.vector.store: pgvector``. Templates for pgvector, OpenSearch
k-NN and Qdrant are in ``docs/rag_pipeline.md``. Access filtering
(classification) and trust re-ranking stay in the retriever, so they behave
identically whatever store is used.

Embedders
---------
* ``hash``   - deterministic feature-hashing embedder (offline, no model). Gives
               the hybrid pipeline something real to run in tests / CI.
* ``ollama`` - REAL local semantic embeddings (e.g. ``nomic-embed-text``).
* register more with ``@register_embedder("bedrock-titan")``.
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
import struct
from array import array
from pathlib import Path
from typing import Callable, Optional

import httpx

from .index import tokenize

# ============================================================================ embedders
EMBEDDERS: dict[str, Callable] = {}


def register_embedder(name: str):
    def deco(cls):
        EMBEDDERS[name] = cls
        return cls
    return deco


class Embedder:
    name = "base"
    dim = 0

    def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover - interface
        raise NotImplementedError


@register_embedder("hash")
class HashEmbedder(Embedder):
    """Signed feature hashing of unigrams + bigrams, L2-normalised."""
    name = "hash"

    def __init__(self, settings):
        self.dim = int(settings.get("rag.vector.dim", 384))

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            toks = tokenize(t)
            feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
            for f in feats:
                h = int(hashlib.md5(f.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0 if (h >> 64) & 1 else -1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


@register_embedder("ollama")
class OllamaEmbedder(Embedder):
    name = "ollama"

    def __init__(self, settings):
        self.base = settings.get("llm.providers.ollama.base_url", "http://localhost:11434")
        self.model = settings.get("rag.vector.embedding_model", "nomic-embed-text")
        self.dim = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        r = httpx.post(f"{self.base}/api/embed", json={"model": self.model, "input": texts}, timeout=60)
        r.raise_for_status()
        vecs = r.json()["embeddings"]
        self.dim = len(vecs[0]) if vecs else 0
        return vecs


_ST_CACHE: dict = {}


@register_embedder("sentence_transformers")
class SentenceTransformersEmbedder(Embedder):
    """Local (optionally fine-tuned) model: rag.vector.model_path = a directory or a Hugging Face id.
    Fine-tune with scripts/training/train_embeddings.py. Requires `pip install 'finagent[embeddings]'`."""
    name = "sentence_transformers"

    def __init__(self, settings):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("pip install 'finagent[embeddings]' to use the sentence_transformers embedder") from exc
        path = str(settings.get("rag.vector.model_path") or "models/embedder")
        p = Path(path)
        if not p.is_absolute():
            if not (settings.root / p).exists() and ("/" not in path or path.startswith(("training", "./", "data"))):
                raise FileNotFoundError(f"embedding model not found at {settings.root / p} - train it with `make retrain`")
            if (settings.root / p).exists():
                path = str(settings.root / p)
        if path not in _ST_CACHE:          # load each model once per process
            _ST_CACHE[path] = SentenceTransformer(path)
        self.model = _ST_CACHE[path]
        self.name = f"sentence_transformers:{Path(path).name}"
        self.dim = self.model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self.model.encode(texts, normalize_embeddings=True, batch_size=32)]


def make_embedder(settings) -> Embedder:
    name = settings.get("rag.vector.embedder", "hash")
    if name not in EMBEDDERS:
        raise ValueError(f"unknown embedder '{name}' (registered: {sorted(EMBEDDERS)})")
    return EMBEDDERS[name](settings)


# ============================================================================ stores
STORES: dict[str, Callable] = {}


def register_vector_store(name: str):
    def deco(cls):
        STORES[name] = cls
        return cls
    return deco


class VectorStore:
    def upsert(self, items: list[dict]) -> int:  # items: {chunk_id, document_id, classification, trust, vector, index_version}
        raise NotImplementedError

    def query(self, vector: list[float], top_k: int, filters: Optional[dict] = None) -> list[tuple[str, float]]:
        raise NotImplementedError

    def delete_documents(self, document_ids: list[str]) -> int:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError


def _pack(v: list[float]) -> bytes:
    return array("f", v).tobytes()


def _unpack(b: bytes) -> list[float]:
    a = array("f")
    a.frombytes(b)
    return a.tolist()


@register_vector_store("sqlite")
class SQLiteVectorStore(VectorStore):
    """Local POC store: one table, float32 BLOBs, brute-force cosine (vectors are pre-normalised)."""

    def __init__(self, settings):
        path = settings.data_dir / settings.get("rag.vector.sqlite_file", "index/vectors.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS vectors (chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                             classification TEXT, trust TEXT, index_version TEXT, embedder TEXT, dim INTEGER, vector BLOB NOT NULL)""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_vec_doc ON vectors(document_id)")
        self._cache: Optional[list[tuple[str, str, list[float]]]] = None

    def upsert(self, items: list[dict]) -> int:
        with self.conn:
            self.conn.executemany(
                """INSERT INTO vectors(chunk_id, document_id, classification, trust, index_version, embedder, dim, vector)
                   VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(chunk_id) DO UPDATE SET document_id=excluded.document_id,
                   classification=excluded.classification, trust=excluded.trust, index_version=excluded.index_version,
                   embedder=excluded.embedder, dim=excluded.dim, vector=excluded.vector""",
                [(i["chunk_id"], i["document_id"], i.get("classification"), i.get("trust"), i.get("index_version"),
                  i.get("embedder"), len(i["vector"]), _pack(i["vector"])) for i in items])
        self._cache = None
        return len(items)

    def retain_only(self, chunk_ids: set[str]) -> int:
        """Delete vectors for chunks no longer in the index (FIN-POL-010 s5 deletion propagation)."""
        existing = {r[0] for r in self.conn.execute("SELECT chunk_id FROM vectors")}
        stale = existing - chunk_ids
        with self.conn:
            self.conn.executemany("DELETE FROM vectors WHERE chunk_id=?", [(c,) for c in stale])
        self._cache = None
        return len(stale)

    def _rows(self):
        if self._cache is None:
            self._cache = [(r[0], r[1], _unpack(r[2])) for r in
                           self.conn.execute("SELECT chunk_id, classification, vector FROM vectors")]
        return self._cache

    def query(self, vector: list[float], top_k: int, filters: Optional[dict] = None) -> list[tuple[str, float]]:
        allowed = set((filters or {}).get("classification_in") or [])
        scored = []
        for cid, cls, v in self._rows():
            if allowed and cls not in allowed:
                continue
            scored.append((cid, sum(a * b for a, b in zip(vector, v))))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:top_k]

    def delete_documents(self, document_ids: list[str]) -> int:
        with self.conn:
            n = self.conn.executemany("DELETE FROM vectors WHERE document_id=?", [(d,) for d in document_ids]).rowcount
        self._cache = None
        return n

    def stats(self) -> dict:
        row = self.conn.execute("SELECT COUNT(*), MAX(dim), MAX(embedder), MAX(index_version) FROM vectors").fetchone()
        return {"store": "sqlite", "vectors": row[0], "dim": row[1], "embedder": row[2], "index_version": row[3]}


def make_vector_store(settings) -> VectorStore:
    name = settings.get("rag.vector.store", "sqlite")
    if name not in STORES:
        raise ValueError(f"unknown vector store '{name}' (registered: {sorted(STORES)})")
    return STORES[name](settings)


def build_vectors(settings, chunks: list[dict], index_version: str) -> dict:
    emb, store = make_embedder(settings), make_vector_store(settings)
    items = []
    batch = 32
    for i in range(0, len(chunks), batch):
        part = chunks[i:i + batch]
        for c, v in zip(part, emb.embed([c["index_text"] for c in part])):
            items.append({"chunk_id": c["chunk_id"], "document_id": c["document_id"], "classification": c["classification"],
                          "trust": c["trust"], "index_version": index_version, "embedder": emb.name, "vector": v})
    store.upsert(items)
    removed = store.retain_only({c["chunk_id"] for c in chunks}) if hasattr(store, "retain_only") else 0
    return {**store.stats(), "upserted": len(items), "removed_stale": removed}
