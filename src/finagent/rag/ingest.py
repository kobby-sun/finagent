"""Corpus ingestion: sources -> loaders -> CorpusDocument contract -> chunks.

Extensible in three places (all registries, no engine change):

* **Loaders** turn a file into a ``CorpusDocument`` (``finagent.corpus/1``):
  ``.md`` (YAML front matter + ``##`` sections), ``.json`` (a CorpusDocument),
  ``.txt`` / ``.pdf`` (text + sidecar ``<file>.meta.yaml`` metadata). Register
  more with ``@register_loader(".docx")``.
* **Connectors** enumerate documents from a source declared in
  ``rag.sources``: ``local_dir`` (default: finance_rag_corpus), ``http_feed``
  (JSON API returning ``{"documents": [CorpusDocument, ...]}``). Register more
  with ``@register_connector("s3")``.
* **Trust** is assigned from metadata only, never from document text, and a
  source can only *lower* trust via ``trust_ceiling`` (e.g. a supplier portal
  feed is always ``untrusted``).

Chunking strategy: one chunk per policy section (``##`` heading / declared
section), split further on paragraphs above ``max_chars``. Title + heading +
tags are prepended to the indexed text so short sections stay retrievable.
Chunk ids are stable: ``<document_id>@v<version>#<section_id>[.n]``.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import httpx
import yaml

from ..contracts import CorpusDocument, CorpusSection, Provenance
from ..schemas import TrustLevel

FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
HEADING_RE = re.compile(r"^##\s+(.*)$", re.M)
_TRUST_ORDER = {"untrusted": 0, "superseded": 1, "authoritative": 2}


@dataclass
class DocChunk:
    chunk_id: str
    document_id: str
    title: str
    doc_type: str
    version: str
    section: str
    status: str
    effective_date: str | None
    classification: str
    owner: str
    trust: str
    text: str
    index_text: str
    tags: list[str] = field(default_factory=list)
    source_path: str = ""
    content_sha256: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================================ trust
def classify_trust(doc: CorpusDocument) -> TrustLevel:
    status, cls, owner = doc.status.lower(), doc.classification.lower(), doc.owner.lower()
    if status == "superseded":
        t = TrustLevel.SUPERSEDED
    elif status in ("untrusted", "draft", "withdrawn") or "unverified" in cls or owner.startswith("external"):
        t = TrustLevel.UNTRUSTED
    elif status == "current":
        t = TrustLevel.AUTHORITATIVE
    else:
        t = TrustLevel.UNTRUSTED
    if doc.trust_ceiling and _TRUST_ORDER[doc.trust_ceiling] < _TRUST_ORDER[t.value]:
        t = TrustLevel(doc.trust_ceiling)
    return t


def doc_type_for(doc: CorpusDocument) -> str:
    if doc.doc_type:
        return doc.doc_type
    if doc.document_id.startswith("FIN-POL"):
        return "policy"
    if "supplier" in doc.owner.lower() or "supplier" in doc.tags:
        return "supplier_correspondence"
    return "reference"


# ============================================================================ loaders
LOADERS: dict[str, Callable[[Path], CorpusDocument]] = {}


def register_loader(*extensions: str):
    def deco(fn):
        for e in extensions:
            LOADERS[e.lower()] = fn
        return fn
    return deco


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _sections_from_markdown(body: str) -> list[CorpusSection]:
    headings = list(HEADING_RE.finditer(body))
    out: list[CorpusSection] = []
    if not headings:
        text = re.sub(r"^#\s+.*$", "", body, flags=re.M).strip()
        return [CorpusSection(heading="Document", text=text, section_id="s0")] if text else []
    pre = re.sub(r"^#\s+.*$", "", body[: headings[0].start()], flags=re.M).strip()
    if pre:
        out.append(CorpusSection(heading="Preamble", text=pre, section_id="s0"))
    for i, h in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        text = body[h.end():end].strip()
        if text:
            out.append(CorpusSection(heading=h.group(1).strip(), text=text))
    return out


def _sidecar_meta(path: Path) -> dict:
    for cand in (path.with_suffix(path.suffix + ".meta.yaml"), path.with_suffix(".meta.yaml")):
        if cand.exists():
            return yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
    # no metadata => cannot be authoritative
    return {"document_id": path.stem.upper(), "title": path.stem, "version": "0", "status": "untrusted",
            "classification": "unknown", "owner": "unknown"}


def _meta_to_doc(meta: dict, sections: list[CorpusSection], path: Path, producer: str, raw: bytes) -> CorpusDocument:
    known = set(CorpusDocument.model_fields) - {"sections", "provenance", "schema_"}
    ext = {f"x-meta.{k}": v for k, v in meta.items() if k not in known}
    payload = {k: (str(v) if k in {"version", "effective_date", "superseded_date"} and v is not None else v)
               for k, v in meta.items() if k in known}
    payload.setdefault("title", payload.get("document_id", path.stem))
    payload.setdefault("version", "0")
    payload.setdefault("status", "untrusted")
    return CorpusDocument(**payload, sections=sections or [CorpusSection(heading="Document", text="(empty)", section_id="s0")],
                          provenance=Provenance(producer=producer, producer_version="1.0", source_uri=path.name,
                                                content_sha256=_sha(raw)),
                          extensions={**payload.get("extensions", {}), **ext})


@register_loader(".md", ".markdown")
def load_markdown(path: Path) -> CorpusDocument:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    m = FRONT_MATTER_RE.match(text)
    meta = (yaml.safe_load(m.group(1)) or {}) if m else _sidecar_meta(path)
    body = text[m.end():] if m else text
    return _meta_to_doc(meta, _sections_from_markdown(body), path, "markdown_loader", raw)


@register_loader(".json")
def load_json(path: Path) -> CorpusDocument:
    raw = path.read_bytes()
    doc = CorpusDocument.model_validate(json.loads(raw))
    if doc.provenance is None:
        doc.provenance = Provenance(producer="json_loader", producer_version="1.0", source_uri=path.name, content_sha256=_sha(raw))
    return doc


@register_loader(".txt")
def load_text(path: Path) -> CorpusDocument:
    raw = path.read_bytes()
    return _meta_to_doc(_sidecar_meta(path), _sections_from_markdown(raw.decode("utf-8", errors="ignore")), path, "text_loader", raw)


@register_loader(".pdf")
def load_pdf(path: Path) -> CorpusDocument:
    raw = path.read_bytes()
    try:
        from pypdf import PdfReader
        import io
        pages = [(p.extract_text() or "").strip() for p in PdfReader(io.BytesIO(raw)).pages]
    except ImportError as exc:
        raise RuntimeError("pip install 'finagent[pdf]' to ingest PDF corpus documents") from exc
    sections = [CorpusSection(heading=f"Page {i}", text=t, section_id=f"p{i}", page=i) for i, t in enumerate(pages, 1) if t]
    return _meta_to_doc(_sidecar_meta(path), sections, path, "pdf_loader", raw)


# ============================================================================ connectors
CONNECTORS: dict[str, Callable[[dict, Path], Iterable[CorpusDocument]]] = {}


def register_connector(kind: str):
    def deco(fn):
        CONNECTORS[kind] = fn
        return fn
    return deco


@register_connector("local_dir")
def local_dir(cfg: dict, root: Path) -> Iterable[CorpusDocument]:
    base = Path(cfg["path"])
    base = base if base.is_absolute() else (root / base)
    for f in sorted(base.iterdir()):
        if f.name.startswith(".") or f.name.endswith(".meta.yaml") or f.suffix.lower() not in LOADERS:
            continue
        doc = LOADERS[f.suffix.lower()](f)
        if cfg.get("trust_ceiling"):
            doc.trust_ceiling = cfg["trust_ceiling"]
        if doc.provenance:
            doc.provenance.connector = f"local_dir:{cfg['path']}"
        yield doc


@register_connector("http_feed")
def http_feed(cfg: dict, root: Path) -> Iterable[CorpusDocument]:
    """GET <url> -> {"documents": [CorpusDocument, ...]}. Headers may reference env vars: {"Authorization": "env:FEED_TOKEN"}."""
    import os
    headers = {k: (os.environ.get(v[4:], "") if str(v).startswith("env:") else v) for k, v in (cfg.get("headers") or {}).items()}
    r = httpx.get(cfg["url"], headers=headers, timeout=float(cfg.get("timeout_s", 15)))
    r.raise_for_status()
    for item in r.json().get("documents", []):
        doc = CorpusDocument.model_validate(item)
        doc.trust_ceiling = cfg.get("trust_ceiling", doc.trust_ceiling)
        doc.provenance = doc.provenance or Provenance(producer="http_feed", producer_version="1.0", source_uri=cfg["url"])
        doc.provenance.connector = f"http_feed:{cfg.get('name', cfg['url'])}"
        yield doc


# ============================================================================ chunking
def chunk_corpus_document(doc: CorpusDocument, max_chars: int = 1200) -> list[DocChunk]:
    trust = classify_trust(doc).value
    common = dict(document_id=doc.document_id, title=doc.title, doc_type=doc_type_for(doc), version=doc.version,
                  status=doc.status, effective_date=doc.effective_date, classification=doc.classification, owner=doc.owner,
                  trust=trust, tags=list(doc.tags), source_path=(doc.provenance.source_uri if doc.provenance else ""),
                  content_sha256=(doc.provenance.content_sha256 if doc.provenance else "") or "")
    chunks: list[DocChunk] = []
    for sec in doc.sections:
        num = re.match(r"^(\d+)\.", sec.heading)
        sec_id = sec.section_id or (f"s{num.group(1)}" if num else re.sub(r"\W+", "-", sec.heading.lower())[:24])
        parts = _split(sec.text, max_chars)
        for j, part in enumerate(parts):
            cid = f"{doc.document_id}@v{doc.version}#{sec_id}" + (f".{j + 1}" if len(parts) > 1 else "")
            chunks.append(DocChunk(chunk_id=cid, section=sec.heading, text=part,
                                   index_text=f"{doc.title}. {sec.heading}. {' '.join(doc.tags)}. {part}", **common))
    return chunks


def _split(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    out, buf = [], ""
    for para in re.split(r"\n\s*\n", text):
        if buf and len(buf) + len(para) > max_chars:
            out.append(buf.strip()); buf = ""
        buf += para + "\n\n"
    if buf.strip():
        out.append(buf.strip())
    return out


def load_sources(sources: list[dict], root: Path) -> tuple[list[DocChunk], str, list[dict]]:
    chunks: list[DocChunk] = []
    h = hashlib.sha256()
    report = []
    for src in sources:
        if not src.get("enabled", True):
            continue
        kind = src.get("type", "local_dir")
        if kind not in CONNECTORS:
            raise ValueError(f"unknown corpus connector '{kind}' (registered: {sorted(CONNECTORS)})")
        n = 0
        for doc in CONNECTORS[kind](src, root):
            h.update(doc.document_id.encode()); h.update(doc.version.encode())
            h.update(((doc.provenance and doc.provenance.content_sha256) or "").encode())
            chunks.extend(chunk_corpus_document(doc))
            n += 1
        report.append({"source": src.get("name", kind), "type": kind, "documents": n})
    return chunks, h.hexdigest()[:16], report


def load_corpus(corpus_dir: Path) -> tuple[list[DocChunk], str]:
    chunks, version, _ = load_sources([{"type": "local_dir", "path": str(corpus_dir)}], corpus_dir)
    return chunks, version
