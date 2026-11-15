"""Versioned, extensible data contracts for everything that ENTERS the system.

Two contracts decouple *sources* from the workflow so new document types,
file formats (PDF, DOCX, HTML ...) and policy sources can be added without touching
the agent graph or the control skills (cases themselves enter as ``ProcessingRequest``):

1. ``NormalizedDocument`` (schema ``finagent.document/1``) - what every case
   attachment parser must emit. Controls only read this shape.
2. ``CorpusDocument`` (schema ``finagent.corpus/1``) - what every corpus loader
   or feed connector must emit. The chunker / indexer only read this shape.

Evolution rules (documented in docs/extending.md):
* additive changes only within a major version (new optional fields);
* source-specific data goes in ``extensions`` under a namespaced key
  (``"x-<source>.<field>"``) - never as new top-level fields;
* breaking changes bump the major (``/2``) and ship a converter;
* every record carries ``schema`` and ``provenance`` (who produced it, how,
  from which bytes) so derived index entries can be traced and deleted
  (FIN-POL-010 s5).
JSON Schemas are exported to ``contracts/`` with ``finagent contracts export``.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

DOCUMENT_SCHEMA = "finagent.document/1"
CORPUS_SCHEMA = "finagent.corpus/1"

_EXT_KEY = re.compile(r"^x-[a-z0-9][a-z0-9_.-]*$")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_extensions(v: dict) -> dict:
    bad = [k for k in v if not _EXT_KEY.match(k)]
    if bad:
        raise ValueError(f"extension keys must be namespaced like 'x-<source>.<field>': {bad}")
    return v


class Provenance(_Base):
    producer: str                       # parser / loader / connector name
    producer_version: str
    source_uri: Optional[str] = None    # filename, URL, s3://..., feed id
    content_sha256: Optional[str] = None
    received_at: Optional[str] = None
    connector: Optional[str] = None


# ============================================================================ case documents
class DocumentLine(_Base):
    line_no: int
    sku: str = ""
    description: str = ""
    category: str = "goods"             # goods | services | freight | <extension>
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("extensions")
    @classmethod
    def _ext(cls, v: dict) -> dict:
        return _check_extensions(v)


class NormalizedDocument(_Base):
    schema_: Literal["finagent.document/1"] = Field(default=DOCUMENT_SCHEMA, alias="schema")
    doc_kind: Literal["invoice", "credit_note", "email", "supporting", "purchase_order", "receipt", "statement", "other"]
    source_filename: str
    parser: str
    parser_version: str = "1.0"
    content_sha256: str
    attachment_source: str = "unknown"  # supplier | internal | email | feed | unknown  (trust input)
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    vendor_name: Optional[str] = None
    bill_to: Optional[str] = None
    currency: Optional[str] = None
    po_ref: Optional[str] = None
    lines: list[DocumentLine] = Field(default_factory=list)
    subtotal: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    total: Optional[Decimal] = None
    payment_terms_days: Optional[int] = None
    bank_details: Optional[dict[str, Optional[str]]] = None   # masked only: {account_last4, bank_country}
    original_invoice_ref: Optional[str] = None
    non_po: Optional[dict[str, Optional[str]]] = None
    email: Optional[dict[str, Optional[str]]] = None
    text: str = ""
    parse_warnings: list[str] = Field(default_factory=list)
    provenance: Optional[Provenance] = None
    extensions: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    @field_validator("extensions")
    @classmethod
    def _ext(cls, v: dict) -> dict:
        return _check_extensions(v)

    @field_validator("bank_details")
    @classmethod
    def _masked(cls, v):
        if v and v.get("account_last4") and len(re.sub(r"\D", "", v["account_last4"])) > 4:
            raise ValueError("bank_details may only carry the last 4 digits (FIN-POL-004 s2)")
        return v


# ============================================================================ corpus
class CorpusSection(_Base):
    heading: str
    text: str
    section_id: Optional[str] = None    # stable id e.g. "s2"; derived from heading number if absent
    page: Optional[int] = None


class CorpusDocument(_Base):
    schema_: Literal["finagent.corpus/1"] = Field(default=CORPUS_SCHEMA, alias="schema")
    document_id: str = Field(min_length=2, max_length=80)
    title: str
    version: str
    status: Literal["current", "superseded", "draft", "untrusted", "withdrawn"]
    effective_date: Optional[str] = None
    superseded_date: Optional[str] = None
    classification: str = "internal"
    owner: str = ""
    jurisdiction: Optional[str] = None
    doc_type: Optional[str] = None      # derived if absent: policy | supplier_correspondence | reference
    tags: list[str] = Field(default_factory=list)
    sections: list[CorpusSection] = Field(min_length=1)
    provenance: Optional[Provenance] = None
    trust_ceiling: Optional[Literal["authoritative", "superseded", "untrusted"]] = None  # a source may lower, never raise
    extensions: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    @field_validator("extensions")
    @classmethod
    def _ext(cls, v: dict) -> dict:
        return _check_extensions(v)


def export_json_schemas() -> dict[str, dict]:
    from .schemas import ApprovalDecisionIn, FinalResult, ModelDraft, ProcessingRequest
    return {
        "normalized_document.v1.schema.json": NormalizedDocument.model_json_schema(by_alias=True),
        "corpus_document.v1.schema.json": CorpusDocument.model_json_schema(by_alias=True),
        "processing_request.v1.schema.json": ProcessingRequest.model_json_schema(),
        "final_result.v1.schema.json": FinalResult.model_json_schema(),
        "model_draft.v1.schema.json": ModelDraft.model_json_schema(),
        "approval_decision.v1.schema.json": ApprovalDecisionIn.model_json_schema(),
    }
