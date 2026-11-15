"""Input parser skills: turn heterogeneous case attachments into one normalised
document shape. All parsed content is UNTRUSTED evidence (supplier-provided);
parsers extract data, they never execute or obey text.

Normalised document::

    {
      "doc_kind": "invoice" | "credit_note" | "email" | "supporting",
      "source_filename": str, "parser": str, "content_sha256": str,
      "invoice_number", "invoice_date", "vendor_name", "bill_to", "currency", "po_ref",
      "lines": [{"line_no", "sku", "description", "category", "quantity", "unit_price", "amount"}],
      "subtotal", "tax", "total"                     # Decimal | None
      "bank_details": {"account_last4", "bank_country"} | None,
      "original_invoice_ref": str | None,            # credit notes
      "non_po": {"category", "justification", "emergency_type"} | None,
      "email": {"from", "from_domain", "subject", "sent_at"} | None,
      "text": str,                                   # raw text for screening
      "parse_warnings": [str]
    }
"""
from __future__ import annotations

import csv
import email
import hashlib
import io
import json
import re
from decimal import Decimal, InvalidOperation
from email import policy as email_policy
from typing import Any

from .registry import register_parser

DOC_FIELDS = ["invoice_number", "invoice_date", "vendor_name", "bill_to", "currency", "po_ref", "subtotal", "tax",
              "total", "bank_details", "original_invoice_ref", "non_po", "email", "payment_terms_days"]


def _d(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return None


def _base(kind: str, filename: str, parser: str, content: str) -> dict:
    doc = {k: None for k in DOC_FIELDS}
    doc.update(doc_kind=kind, source_filename=filename, parser=parser, lines=[], text=content,
               content_sha256=hashlib.sha256(content.encode()).hexdigest()[:16], parse_warnings=[])
    return doc


def _norm_lines(lines: list[dict], warnings: list[str]) -> list[dict]:
    out = []
    for i, ln in enumerate(lines, start=1):
        qty, price, amt = _d(ln.get("quantity")), _d(ln.get("unit_price")), _d(ln.get("amount"))
        if amt is None and qty is not None and price is not None:
            amt = (qty * price).quantize(Decimal("0.01"))
        if amt is None:
            warnings.append(f"line {i}: amount missing")
        out.append({"line_no": int(ln.get("line_no") or i), "sku": str(ln.get("sku") or "").strip(),
                    "description": str(ln.get("description") or "").strip(),
                    "category": str(ln.get("category") or "goods").strip().lower(),
                    "quantity": qty, "unit_price": price, "amount": amt})
    return out


# ----------------------------------------------------------------------------- JSON
@register_parser("invoice_json_parser", content_types=("application/json",), extensions=(".json",), priority=60,
                 description="Parse structured e-invoice / credit-note JSON into the normalised document.",
                 compliance_refs=[("FIN-POL-001", "s2"), ("FIN-POL-008", "s1")])
def parse_invoice_json(filename: str, content: str) -> dict:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        doc = _base("supporting", filename, "invoice_json_parser", content)
        doc["parse_warnings"].append(f"invalid JSON: {exc.msg}")
        return doc
    kind = "credit_note" if str(data.get("document_type", "invoice")).lower() == "credit_note" else "invoice"
    doc = _base(kind, filename, "invoice_json_parser", content)
    for k in ("invoice_number", "invoice_date", "vendor_name", "bill_to", "currency", "po_ref", "original_invoice_ref"):
        if data.get(k) is not None:
            doc[k] = str(data[k]).strip()
    doc["currency"] = (doc["currency"] or "").upper() or None
    doc["subtotal"], doc["tax"], doc["total"] = _d(data.get("subtotal")), _d(data.get("tax")), _d(data.get("total"))
    doc["lines"] = _norm_lines(data.get("lines", []), doc["parse_warnings"])
    if data.get("payment_terms_days") is not None:
        doc["payment_terms_days"] = int(data["payment_terms_days"])
    bank = data.get("bank_details") or data.get("remit_to")
    if bank:
        acct = str(bank.get("account_number", "") or bank.get("account_last4", ""))
        doc["bank_details"] = {"account_last4": re.sub(r"\D", "", acct)[-4:], "bank_country": bank.get("bank_country")}
    if data.get("non_po"):
        doc["non_po"] = {k: data["non_po"].get(k) for k in ("category", "justification", "emergency_type")}
    return doc


# ----------------------------------------------------------------------------- text
_FIELD_PATTERNS = {
    "invoice_number": r"(?:tax\s+)?invoice\s*(?:number|no\.?|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]+)",
    "invoice_date": r"invoice\s+date\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})",
    "po_ref": r"\b(PO-\d{3,8})\b",
    "currency": r"currency\s*[:\-]?\s*([A-Z]{3})",
    "bill_to": r"bill\s+to\s*[:\-]?\s*(.+)",
    # explicit supplier / vendor label first; "From:" only as a fallback (quoted e-mail headers are not the supplier)
    "vendor_name": [r"(?:supplier|vendor)\s*[:\-]\s*(.+)", r"^from\s*[:\-]\s*(.+)"],
    "subtotal": r"sub\s*total\s*[:\-]?\s*(?:[A-Z]{3}\s*)?\$?\s*([\d,]+\.\d{2})",
    "tax": r"(?:gst|tax|vat)(?:\s*\(\d+%\))?\s*[:\-]?\s*(?:[A-Z]{3}\s*)?\$?\s*([\d,]+\.\d{2})",
    "total": r"(?:total\s+(?:due|amount|payable)|amount\s+due|^total)\s*[:\-]?\s*(?:[A-Z]{3}\s*)?\$?\s*([\d,]+\.\d{2})",
    "original_invoice_ref": r"original\s+invoice\s*(?:ref|number|no\.?)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-]+)",
}
_LINE_RE = re.compile(r"^\s*(\d+)\s*\|\s*([^|]*)\|\s*([^|]*)\|\s*([^|]*)\|\s*([\d.,]+)\s*\|\s*([\d.,]+)\s*\|\s*([\d.,]+)\s*$", re.M)
_BANK_RE = re.compile(r"(?:account|acct)[^\n\d]{0,40}?(?:ending(?:\s+in)?\s*)?((?:\d[\s-]?){4,20})", re.I)


@register_parser("invoice_text_parser", content_types=("text/plain",), extensions=(".txt",), priority=10,
                 description="Parse OCR/PDF-extracted or plain-text invoices (label: value fields + pipe-delimited line table).",
                 compliance_refs=[("FIN-POL-001", "s2"), ("FIN-POL-002", "s4")])
def parse_invoice_text(filename: str, content: str, kind_hint: str | None = None) -> dict:
    is_invoice = bool(re.search(r"invoice\s*(number|no|#)", content, re.I))
    is_credit = bool(re.search(r"credit\s+note", content, re.I))
    kind = kind_hint or ("credit_note" if is_credit else "invoice" if is_invoice else "supporting")
    doc = _base(kind, filename, "invoice_text_parser", content)
    for key, pat in _FIELD_PATTERNS.items():
        m = next((x for x in (re.search(p, content, re.I | re.M) for p in (pat if isinstance(pat, list) else [pat])) if x), None)
        if m:
            val = m.group(1).strip()
            doc[key] = _d(val) if key in {"subtotal", "tax", "total"} else val
    if doc["currency"]:
        doc["currency"] = doc["currency"].upper()
    rows = [{"line_no": m.group(1), "sku": m.group(2), "description": m.group(3), "category": m.group(4),
             "quantity": m.group(5), "unit_price": m.group(6), "amount": m.group(7)} for m in _LINE_RE.finditer(content)]
    doc["lines"] = _norm_lines(rows, doc["parse_warnings"])
    bm = _BANK_RE.search(content)
    if bm:
        digits = re.sub(r"\D", "", bm.group(1))
        if len(digits) >= 4:
            doc["bank_details"] = {"account_last4": digits[-4:], "bank_country": None}
    np = re.search(r"non-po\s+category\s*[:\-]\s*(\w+)", content, re.I)
    if np:
        just = re.search(r"justification\s*[:\-]\s*(.+)", content, re.I)
        emer = re.search(r"emergency\s+type\s*[:\-]\s*(\w+)", content, re.I)
        doc["non_po"] = {"category": np.group(1).lower(), "justification": just.group(1).strip() if just else None,
                         "emergency_type": emer.group(1).lower() if emer else None}
    if kind in {"invoice", "credit_note"} and not doc["invoice_number"]:
        doc["parse_warnings"].append("invoice number not found")
    return doc


# ----------------------------------------------------------------------------- CSV
@register_parser("invoice_csv_parser", content_types=("text/csv",), extensions=(".csv",), priority=60,
                 description="Parse CSV invoice exports: '# key=value' header comments + line-item table.",
                 compliance_refs=[("FIN-POL-001", "s2"), ("FIN-POL-002", "s5")])
def parse_invoice_csv(filename: str, content: str) -> dict:
    doc = _base("invoice", filename, "invoice_csv_parser", content)
    meta, rows = {}, []
    body_lines = []
    for line in content.splitlines():
        if line.startswith("#"):
            if "=" in line:
                k, v = line[1:].split("=", 1)
                meta[k.strip().lower()] = v.strip()
        elif line.strip():
            body_lines.append(line)
    reader = csv.DictReader(io.StringIO("\n".join(body_lines)))
    for r in reader:
        rows.append({k.strip().lower(): (v or "").strip() for k, v in r.items() if k})
    for k in ("invoice_number", "invoice_date", "vendor_name", "bill_to", "currency", "po_ref"):
        if meta.get(k):
            doc[k] = meta[k]
    doc["currency"] = (doc["currency"] or "").upper() or None
    doc["subtotal"], doc["tax"], doc["total"] = _d(meta.get("subtotal")), _d(meta.get("tax")), _d(meta.get("total"))
    doc["lines"] = _norm_lines(rows, doc["parse_warnings"])
    if not doc["invoice_number"]:
        doc["parse_warnings"].append("invoice number missing from CSV header")
    return doc


# ----------------------------------------------------------------------------- email
@register_parser("email_parser", content_types=("message/rfc822",), extensions=(".eml",), priority=70,
                 sniff=lambda name, text: bool(re.match(r"^(From|Subject|To|Date):", text)) and "\nSubject:" in "\n" + text[:1000],
                 description="Parse RFC-822 e-mails (.eml): sender domain, subject, body; embedded invoice text is delegated to the text parser.",
                 compliance_refs=[("FIN-POL-004", "s2"), ("FIN-POL-005", "s3")])
def parse_email(filename: str, content: str) -> dict:
    msg = email.message_from_string(content, policy=email_policy.default)
    body_part = msg.get_body(preferencelist=("plain",)) if msg.is_multipart() else msg
    body = body_part.get_content() if body_part is not None else ""
    body = re.sub(r"(?m)^(?:>\s?)+", "", body)          # forwarded / replied chains quote every line with "> "
    sender = str(msg.get("From", ""))
    m = re.search(r"@([A-Za-z0-9.-]+)", sender)
    meta = {"from": sender, "from_domain": m.group(1).lower() if m else None, "subject": str(msg.get("Subject", "")),
            "sent_at": str(msg.get("Date", ""))}
    if re.search(r"invoice\s*(number|no|#)", body, re.I):
        doc = parse_invoice_text(filename, body)
        doc["parser"] = "email_parser+invoice_text_parser"
    else:
        doc = parse_invoice_text(filename, body, kind_hint="email")
        doc["parser"] = "email_parser"
    doc["email"] = meta
    doc["text"] = f"Subject: {meta['subject']}\n{body}"
    return doc


# ----------------------------------------------------------------------------- dispatcher
def parse_attachment(filename: str, content: str, content_type: str = "text/plain", source: str = "unknown",
                     encoding: str = "utf-8") -> dict:
    """Backwards-compatible helper: route through the parser registry (see registry.py)."""
    from .registry import parse_attachment as _parse
    return _parse({"filename": filename, "content": content, "content_type": content_type, "source": source,
                   "encoding": encoding})
