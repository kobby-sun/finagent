"""Typed contracts for requests, tools, model output and final results.

All money is ``Decimal`` and serialised as strings so no float rounding ever
enters a calculation or the audit trail.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _money(v: Decimal | None) -> str | None:
    return None if v is None else format(v, "f")


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------
class Outcome(str, Enum):
    APPROVE_FOR_POSTING = "APPROVE_FOR_POSTING"
    HOLD_FOR_INFORMATION = "HOLD_FOR_INFORMATION"
    REJECT_DUPLICATE = "REJECT_DUPLICATE"
    REJECT_INVALID = "REJECT_INVALID"
    ESCALATE_CONTROL_REVIEW = "ESCALATE_CONTROL_REVIEW"


# Higher number = more conservative / higher priority when several rules fire.
OUTCOME_PRECEDENCE = {
    Outcome.APPROVE_FOR_POSTING: 0,
    Outcome.HOLD_FOR_INFORMATION: 1,
    Outcome.REJECT_INVALID: 2,
    Outcome.REJECT_DUPLICATE: 3,
    Outcome.ESCALATE_CONTROL_REVIEW: 4,
}

# Outcomes that change a financial decision and therefore need human approval
# before ``submit_finance_decision`` may run (deny-by-default otherwise).
CONSEQUENTIAL_OUTCOMES = {Outcome.APPROVE_FOR_POSTING, Outcome.REJECT_DUPLICATE, Outcome.REJECT_INVALID}


class ExceptionCategory(str, Enum):  # FIN-POL-007 s1
    MISSING_PO = "MISSING_PO"
    MISSING_RECEIPT = "MISSING_RECEIPT"
    PRICE_VARIANCE = "PRICE_VARIANCE"
    QUANTITY_VARIANCE = "QUANTITY_VARIANCE"
    DUPLICATE_RISK = "DUPLICATE_RISK"
    VENDOR_BLOCK = "VENDOR_BLOCK"
    BANK_CHANGE = "BANK_CHANGE"
    AUTHORITY_GAP = "AUTHORITY_GAP"
    TAX_QUERY = "TAX_QUERY"
    OTHER_CONTROL_RISK = "OTHER_CONTROL_RISK"


class RunStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"          # terminal: outcome decided (and submitted if approved)
    NEEDS_INFORMATION = "NEEDS_INFORMATION"  # terminal-until-evidence: hold / escalation, resumable
    FAILED = "FAILED"


class FindingStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class TrustLevel(str, Enum):
    AUTHORITATIVE = "authoritative"  # current, internally owned policy
    SUPERSEDED = "superseded"        # historical only, never authority
    UNTRUSTED = "untrusted"          # supplier / external / adversarial text


# --------------------------------------------------------------------------
# Inbound request
# --------------------------------------------------------------------------
class Attachment(Strict):
    filename: str = Field(min_length=1, max_length=200)
    content_type: str = Field(default="text/plain", max_length=100)
    content: str = Field(max_length=15_000_000, description="Text content, or base64 when encoding='base64' (PDF, images, DOCX)")
    encoding: Literal["utf-8", "base64"] = "utf-8"
    source: Literal["supplier", "internal", "email", "unknown"] = "unknown"


class ProcessingRequest(Strict):
    case_id: str = Field(pattern=r"^[A-Z]{2,5}-\d{3,8}$")
    invoice_ref: str = Field(min_length=1, max_length=64)
    vendor_id: str = Field(pattern=r"^V-\d{3,6}$")
    vendor_name: Optional[str] = Field(default=None, max_length=200)
    amount: Decimal = Field(gt=Decimal("0"), max_digits=14, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    po_ref: Optional[str] = Field(default=None, pattern=r"^PO-\d{3,8}$")
    document_type: Literal["invoice", "credit_note"] = "invoice"
    requester_id: Optional[str] = Field(default=None, max_length=64)
    notes: Optional[str] = Field(default=None, max_length=5_000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)

    @field_serializer("amount")
    def _s_amount(self, v: Decimal) -> str:
        return _money(v)


# --------------------------------------------------------------------------
# Tool contracts
# --------------------------------------------------------------------------
class RetrieveDocsInput(Strict):
    query: str = Field(min_length=3, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)
    doc_types: Optional[list[str]] = None
    caller_role: str = "ap_agent"


class Chunk(Strict):
    chunk_id: str
    document_id: str
    title: str
    doc_type: str
    version: str
    section: str
    status: str
    effective_date: Optional[str] = None
    classification: str
    trust: TrustLevel
    text: str
    score: float
    rank: int
    citation: str
    warnings: list[str] = Field(default_factory=list)


class RetrieveDocsOutput(Strict):
    query: str
    index_version: str
    chunks: list[Chunk]
    filtered_by_access: int = 0


class VendorInput(Strict):
    vendor_id: str = Field(pattern=r"^V-\d{3,6}$")


class VendorRecord(Strict):
    vendor_id: str
    legal_name: str
    status: Literal["ACTIVE", "BLOCKED", "DORMANT", "SANCTIONS_REVIEW", "PENDING_VERIFICATION"]
    country: str
    email_domain: Optional[str] = None
    payment_details: dict[str, Any]  # masked: {bank_country, account_last4, verified, verified_at}
    risk_flags: list[str] = Field(default_factory=list)
    created_at: str
    created_by: Optional[str] = None
    bank_changed_at: Optional[str] = None
    bank_changed_by: Optional[str] = None
    payments_since_bank_change: int = 0
    sanctions_screened_at: Optional[str] = None
    last_updated: str


class POInput(Strict):
    po_ref: str = Field(pattern=r"^PO-\d{3,8}$")


class POLine(Strict):
    line_no: int
    sku: str
    description: str
    category: Literal["goods", "services", "freight"]
    quantity: Decimal
    unit_price: Decimal
    line_value: Decimal

    @field_serializer("quantity", "unit_price", "line_value")
    def _s(self, v: Decimal) -> str:
        return _money(v)


class GoodsReceipt(Strict):
    receipt_id: str
    line_no: int
    quantity_received: Decimal
    received_at: str
    receipted_by: str
    service_confirmed: bool = False

    @field_serializer("quantity_received")
    def _s(self, v: Decimal) -> str:
        return _money(v)


class PurchaseOrder(Strict):
    po_ref: str
    vendor_id: str
    currency: str
    lines: list[POLine]
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    freight_allowed: bool = False
    fx_conversion_permitted: bool = False
    tolerances: dict[str, str]
    approval_status: Literal["APPROVED", "PENDING", "REJECTED", "CLOSED"]
    approved_by: Optional[str] = None
    requester_id: Optional[str] = None
    payment_terms_days: Optional[int] = None
    goods_receipts: list[GoodsReceipt] = Field(default_factory=list)
    last_updated: str

    @field_serializer("subtotal", "tax", "total")
    def _s(self, v: Decimal) -> str:
        return _money(v)


class InvoiceHistoryInput(Strict):
    vendor_id: str
    invoice_ref: str
    amount: Decimal
    currency: str
    invoice_date: Optional[str] = None
    po_ref: Optional[str] = None
    attachment_hash: Optional[str] = None
    exclude_case_id: Optional[str] = None
    lookup_mode: Literal["duplicate", "reference"] = "duplicate"
    fuzzy_amount_pct: Decimal = Decimal("0.005")     # policy parameter duplicates.fuzzy_amount_pct
    fuzzy_date_window_days: int = 14                 # policy parameter duplicates.fuzzy_date_window_days
    non_po_window_days: int = 90                     # policy parameter non_po.repeat_window_days

    @field_serializer("amount")
    def _s(self, v: Decimal) -> str:
        return _money(v)


class HistoryMatch(Strict):
    record_id: str
    case_id: Optional[str] = None
    vendor_id: str
    invoice_ref: str
    normalised_ref: str
    amount: str
    currency: str
    invoice_date: Optional[str] = None
    po_ref: Optional[str] = None
    status: Literal["PAID", "POSTED", "HELD", "REJECTED"]
    rejection_reason: Optional[str] = None
    is_non_po: bool = False
    match_type: Literal["EXACT", "FUZZY", "SAME_VENDOR_NON_PO", "REFERENCE"]
    match_reasons: list[str]
    fingerprint: str


class InvoiceHistoryOutput(Strict):
    matches: list[HistoryMatch]
    checked_statuses: list[str]


class FxInput(Strict):
    from_currency: str
    to_currency: str = "AUD"
    rate_date: str


class FxRate(Strict):
    from_currency: str
    to_currency: str
    rate: Decimal
    rate_date: str
    source: str

    @field_serializer("rate")
    def _s(self, v: Decimal) -> str:
        return format(v, "f")


class AuthorityInput(Strict):
    as_of: str


class AuthorityRegister(Strict):
    version: str
    policy_document: str
    role_limits: dict[str, Optional[str]] = Field(default_factory=dict)
    approvers: list[dict[str, Any]]
    delegations: list[dict[str, Any]]


class SubmitDecisionInput(Strict):
    run_id: str
    case_id: str
    approval_id: str
    decision: Literal["POST", "REJECT"]
    outcome: Outcome
    vendor_id: str
    invoice_ref: str
    amount: Decimal
    currency: str
    idempotency_key: str = Field(min_length=16, max_length=128)

    @field_serializer("amount")
    def _s(self, v: Decimal) -> str:
        return _money(v)


class SubmitDecisionOutput(Strict):
    posting_ref: str
    decision: str
    status: Literal["ACCEPTED", "DUPLICATE_IGNORED", "ALREADY_DECIDED"]   # ALREADY_DECIDED: this invoice was decided by another run
    idempotency_key: str
    recorded_at: str
    previous_run_id: Optional[str] = None   # set with ALREADY_DECIDED: the run that decided this invoice
    simulated: bool = True


# --------------------------------------------------------------------------
# Evidence / result building blocks
# --------------------------------------------------------------------------
class Citation(Strict):
    source_id: str          # chunk id, or tool evidence id e.g. "vendor:V-1001@2026-09-01"
    source_type: Literal["policy", "vendor_master", "purchase_order", "goods_receipt", "invoice_history",
                         "fx_rate", "authority_register", "case_document", "request"]
    document_id: Optional[str] = None
    version: Optional[str] = None
    section: Optional[str] = None
    trust: TrustLevel = TrustLevel.AUTHORITATIVE
    quote: Optional[str] = None


class SourcedFact(Strict):
    fact: str
    citations: list[Citation]


class Calculation(Strict):
    name: str
    formula: str
    inputs: dict[str, str]
    result: str
    rounding: str = "ROUND_HALF_UP to 0.01"
    threshold: Optional[str] = None
    passed: Optional[bool] = None


class Inference(Strict):
    statement: str
    basis: str
    produced_by: Literal["rules", "model"]


class Unknown(Strict):
    item: str
    impact: str
    resolution: str


class PolicyFinding(Strict):
    control: str
    rule: str
    status: FindingStatus
    detail: str
    citations: list[Citation] = Field(default_factory=list)


class ExceptionRecord(Strict):  # FIN-POL-007 s2
    category: ExceptionCategory
    failed_rule: str
    expected: str
    observed: str
    citations: list[Citation] = Field(default_factory=list)
    owner: str
    next_review_date: str


class RiskIndicator(Strict):
    indicator: str
    evidence: str
    source: str


class ApprovalSlot(Strict):
    slot: str
    description: str
    min_limit_aud: Optional[str] = None
    requires_flag: Optional[str] = None


class ActionTaken(Strict):
    action: str
    tool: Optional[str] = None
    at: str
    detail: dict[str, Any] = Field(default_factory=dict)


class Recommendation(Strict):
    outcome: Outcome
    primary_exception: Optional[ExceptionCategory] = None
    confidence: float = Field(ge=0, le=1)
    next_action: str
    summary: str
    required_approvals: list[ApprovalSlot] = Field(default_factory=list)
    amount_aud: Optional[str] = None
    proposed_payment_date: Optional[str] = None


class FinalResult(Strict):
    run_id: str
    case_id: str
    status: RunStatus
    recommendation: Optional[Recommendation] = None
    sourced_facts: list[SourcedFact] = Field(default_factory=list)
    calculations: list[Calculation] = Field(default_factory=list)
    inferences: list[Inference] = Field(default_factory=list)
    unknowns: list[Unknown] = Field(default_factory=list)
    policy_findings: list[PolicyFinding] = Field(default_factory=list)
    exceptions: list[ExceptionRecord] = Field(default_factory=list)
    risk_indicators: list[RiskIndicator] = Field(default_factory=list)
    actions_taken: list[ActionTaken] = Field(default_factory=list)
    approval: Optional[dict[str, Any]] = None
    submission: Optional[dict[str, Any]] = None
    reasoning: list[dict[str, Any]] = Field(default_factory=list,
                                            description="Why each key step went the way it did: actor (system/skill/model/human), decision, reason, refs")
    model: dict[str, Any] = Field(default_factory=dict)
    retrieval: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict,
                                   description="Policy baseline used: who reviewed it and when; sections that differ from it (case held)")


# --------------------------------------------------------------------------
# Model (LLM) output contract
# --------------------------------------------------------------------------
class ModelDraft(Strict):
    """What the model is allowed to contribute: narrative + inferences.

    The model never decides arithmetic or the authoritative outcome; its
    proposed outcome may only make the decision *more* conservative.
    """
    summary: str = Field(min_length=10, max_length=1500)
    proposed_outcome: Outcome
    rationale: list[str] = Field(min_length=1, max_length=12)
    cited_source_ids: list[str] = Field(min_length=1, max_length=30)
    inferences: list[str] = Field(default_factory=list, max_length=10)
    unknowns: list[str] = Field(default_factory=list, max_length=10)
    risk_notes: list[str] = Field(default_factory=list, max_length=10)
    additional_queries: list[str] = Field(default_factory=list, max_length=2)
    confidence: float = Field(ge=0, le=1)

    @field_validator("rationale", "inferences", "unknowns", "risk_notes")
    @classmethod
    def _non_empty(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]


# --------------------------------------------------------------------------
# Approval contracts
# --------------------------------------------------------------------------
class ApprovalDecisionIn(Strict):
    decision: Literal["APPROVE", "REJECT"]
    approver_id: str = Field(min_length=2, max_length=64)
    comment: Optional[str] = Field(default=None, max_length=2000,
                                   description="Reason for the decision. REQUIRED for REJECT (recorded in the reasoning trace).")
    callback_id: Optional[str] = Field(default=None, max_length=128,
                                       description="Idempotency key of the callback delivery; duplicates replay the stored response")

    @model_validator(mode="after")
    def _reject_needs_reason(self):
        if self.decision == "REJECT" and len((self.comment or "").strip()) < 5:
            raise ValueError("a REJECT decision needs a reason in 'comment' (at least 5 characters)")
        return self


class EvidenceIn(Strict):
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)
    note: Optional[str] = Field(default=None, max_length=2000)
    po_ref: Optional[str] = Field(default=None, pattern=r"^PO-\d{3,8}$")


class ReviewIn(Strict):
    """A person decides a judgement question the AI could not settle (uncertain or not answered)."""
    reviewer_id: str = Field(min_length=2, max_length=64)
    skill: str = Field(min_length=3, max_length=64)
    question: str = Field(min_length=3, max_length=64)
    decision: Literal["clear", "concern"]          # clear = no concern on this point; concern = the concern stands
    reason: str = Field(min_length=10, max_length=1000)


def to_jsonable(obj: Any) -> Any:
    """Serialise pydantic models / decimals / dates for JSON storage."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    return obj
