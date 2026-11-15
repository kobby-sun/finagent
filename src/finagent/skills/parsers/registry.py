"""Parser registry: route any attachment to the best parser, validate the output
against the ``NormalizedDocument`` contract.

Adding a new input type (PDF, DOCX, XML e-invoice, image OCR, ...) is a
registration, not an engine change::

    from finagent.skills.parsers.registry import register_parser

    @register_parser("ubl_xml_parser", content_types=("application/xml",), extensions=(".xml",),
                     sniff=lambda name, text: "<Invoice" in text[:500], priority=60, version="1.0")
    def parse_ubl(filename: str, content: str, *, raw: bytes | None = None) -> dict:
        ...  # return a dict matching NormalizedDocument

Routing order: explicit ``sniff`` match > content-type match > extension match,
ties broken by ``priority`` (higher first). Unknown inputs fall back to the
plain-text parser and are tagged ``doc_kind=supporting`` - never dropped
silently. Every parse is validated; a parser that violates the contract is a
parse failure recorded in ``parse_warnings``, not an exception in the run.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from pydantic import ValidationError

from ...contracts import NormalizedDocument, Provenance
from ...schemas import to_jsonable
from ..base import skill


@dataclass
class ParserPlugin:
    name: str
    func: Callable[..., dict]
    content_types: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    sniff: Optional[Callable[[str, str], bool]] = None
    priority: int = 50
    version: str = "1.0"
    binary: bool = False                 # receives decoded bytes via raw=
    description: str = ""
    compliance_refs: list = field(default_factory=list)


PARSERS: dict[str, ParserPlugin] = {}


def register_parser(name: str, *, content_types=(), extensions=(), sniff=None, priority: int = 50, version: str = "1.0",
                    binary: bool = False, description: str = "", compliance_refs: list | None = None):
    def deco(fn):
        PARSERS[name] = ParserPlugin(name, fn, tuple(c.lower() for c in content_types), tuple(e.lower() for e in extensions),
                                     sniff, priority, version, binary, description or (fn.__doc__ or "").strip().split("\n")[0],
                                     compliance_refs or [])
        # parsers are also skills (listed, documented, testable, compliance-injected)
        skill(name, "parser", PARSERS[name].description or name, compliance_refs or [("FIN-POL-010", "s2")], version=version)(fn)
        return fn
    return deco


def unregister_parser(name: str) -> None:
    PARSERS.pop(name, None)


GENERIC_CONTENT_TYPES = ("text/plain", "application/octet-stream", "binary/octet-stream", "")


def route(filename: str, content_type: str, text: str) -> ParserPlugin:
    """sniff > specific content-type > extension > generic content-type > text fallback."""
    fn, ct = filename.lower(), (content_type or "").lower().split(";")[0].strip()
    ranked = sorted(PARSERS.values(), key=lambda p: -p.priority)
    for p in ranked:
        try:
            if p.sniff and p.sniff(fn, text):
                return p
        except Exception:
            continue
    if ct not in GENERIC_CONTENT_TYPES:
        for p in ranked:
            if ct in p.content_types:
                return p
    for p in ranked:
        if any(fn.endswith(e) for e in p.extensions):
            return p
    for p in ranked:
        if ct and ct in p.content_types:
            return p
    return PARSERS["invoice_text_parser"]


def parse_attachment(attachment: dict) -> dict:
    """Parse one ``Attachment`` dict into a validated NormalizedDocument dict."""
    filename = attachment["filename"]
    ctype = attachment.get("content_type", "text/plain")
    raw: bytes | None = None
    text = attachment.get("content", "")
    warnings: list[str] = []
    if attachment.get("encoding") == "base64":
        try:
            raw = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            raw, warnings = b"", ["attachment declared base64 but could not be decoded"]
        text = raw.decode("utf-8", errors="ignore") if raw else ""
    plugin = route(filename, ctype, text[:2000])
    sha = hashlib.sha256(raw if raw is not None else text.encode()).hexdigest()[:16]
    try:
        out = plugin.func(filename, text, raw=raw) if plugin.binary else plugin.func(filename, text)
    except Exception as exc:  # parser bug or hostile input: degrade to a supporting document
        out = {"doc_kind": "supporting", "source_filename": filename, "parser": plugin.name, "content_sha256": sha,
               "text": text[:20000], "parse_warnings": [f"parser {plugin.name} failed: {type(exc).__name__}"]}
    out.setdefault("parser", plugin.name)
    out["parser_version"] = plugin.version
    out["content_sha256"] = sha
    out["attachment_source"] = attachment.get("source", "unknown")
    out["parse_warnings"] = list(out.get("parse_warnings", [])) + warnings
    out["provenance"] = Provenance(producer=plugin.name, producer_version=plugin.version, source_uri=filename,
                                   content_sha256=sha, connector=attachment.get("source", "unknown")).model_dump()
    try:
        doc = NormalizedDocument.model_validate(out)
    except ValidationError as exc:
        minimal = {k: out.get(k) for k in ("source_filename", "parser", "content_sha256", "attachment_source")}
        doc = NormalizedDocument(doc_kind="supporting", text=str(out.get("text", ""))[:20000], parser_version=plugin.version,
                                 parse_warnings=[f"output violated NormalizedDocument contract: {exc.error_count()} error(s)"],
                                 **minimal)
    return to_jsonable(doc.model_dump(by_alias=True))


def describe_parsers() -> list[dict]:
    return [{"name": p.name, "version": p.version, "content_types": p.content_types, "extensions": p.extensions,
             "sniff": bool(p.sniff), "priority": p.priority, "binary": p.binary, "description": p.description}
            for p in sorted(PARSERS.values(), key=lambda p: -p.priority)]
