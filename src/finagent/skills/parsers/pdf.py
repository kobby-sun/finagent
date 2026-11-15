"""PDF parser: extract text with ``pypdf`` (optional dependency) and reuse the
text-invoice parser. Scanned (image-only) PDFs yield no text and are returned
as ``supporting`` documents with a warning so a human/OCR step can follow.

Install: ``pip install 'finagent[pdf]'``.
"""
from __future__ import annotations

import io

from .documents import parse_invoice_text
from .registry import register_parser


@register_parser("pdf_parser", content_types=("application/pdf",), extensions=(".pdf",),
                 sniff=lambda name, text: text.startswith("%PDF"), priority=80, binary=True, version="1.0",
                 description="Extract text from PDF invoices/credit notes (pypdf) and parse fields; flags scanned PDFs.",
                 compliance_refs=[("FIN-POL-001", "s2"), ("FIN-POL-010", "s2")])
def parse_pdf(filename: str, content: str, *, raw: bytes | None = None) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"doc_kind": "supporting", "source_filename": filename, "parser": "pdf_parser", "text": "",
                "content_sha256": "", "parse_warnings": ["pypdf not installed; PDF text not extracted (pip install 'finagent[pdf]')"]}
    reader = PdfReader(io.BytesIO(raw or content.encode("latin-1", errors="ignore")))
    pages = [(p.extract_text() or "") for p in reader.pages]
    text = "\n".join(pages)
    if not text.strip():
        return {"doc_kind": "supporting", "source_filename": filename, "parser": "pdf_parser", "text": "", "content_sha256": "",
                "parse_warnings": ["PDF contains no extractable text (scanned image?) - OCR required"]}
    doc = parse_invoice_text(filename, text)
    doc["parser"] = "pdf_parser+invoice_text_parser"
    doc.setdefault("extensions", {})["x-pdf.pages"] = len(pages)
    return doc
