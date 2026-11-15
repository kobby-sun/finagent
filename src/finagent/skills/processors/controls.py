"""Processor skills: deterministic finance controls.

Each processor receives the *evidence bundle* ``ev`` (dict) assembled by the
agent graph and returns a :class:`SkillOutput`. Processors never fetch data;
missing evidence is reported as UNKNOWN + a HOLD vote (FIN-POL-001 s5:
"Missing evidence results in a hold, not a guessed conclusion").

Evidence bundle keys::

    request, invoice, docs, vendor, vendor_error, po, po_error, history,
    history_error, fx, fx_error, authority, authority_error, now (datetime),
    base_currency, northstar_entities, risk (filled by vendor/fraud skills)
"""
from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

from ...agent.calc import add_business_days, business_days_between, calc, q, to_date
from ...governance.params import P, PD
from ...redaction import redact_text
from ...schemas import Outcome
from ..base import SkillOutput, skill

OWNERS = {
    "MISSING_PO": "Requester", "MISSING_RECEIPT": "Receipter", "PRICE_VARIANCE": "Requester",
    "QUANTITY_VARIANCE": "Receipter", "DUPLICATE_RISK": "Accounts Payable", "VENDOR_BLOCK": "Vendor Governance",
    "BANK_CHANGE": "Vendor Governance", "AUTHORITY_GAP": "Financial Control", "TAX_QUERY": "Group Tax",
    "OTHER_CONTROL_RISK": "Accounts Payable",
}
FRAUD_OWNER = "Financial Crime and Controls"


def _amount(ev) -> Decimal:
    inv = ev.get("invoice") or {}
    return Decimal(inv.get("total") or ev["request"]["amount"])


def _norm_name(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"\b(pty|ltd|limited|inc|co|company|llc|plc|group)\b", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _norm_ref(s: str | None) -> str:
    x = re.sub(r"[^A-Za-z0-9]", "", s or "").upper()
    return re.sub(r"(?<=[A-Z])0+(?=\d)", "", x)


# =============================================================================
# 1. Evidence completeness and request/document consistency
# =============================================================================
@skill("evidence_completeness", "processor",
       "Minimum-evidence check and consistency between the processing request and the invoice document.",
       compliance_refs=[("FIN-POL-001", "s2"), ("FIN-POL-001", "s5"), ("FIN-POL-007", "s2")],
       retrieval_queries=["minimum evidence invoice case supplier legal name invoice number purchase-order reference receipt",
                          "required checks before approval duplicate detection confirm vendor status payment instructions match verified vendor master",
                          "required exception record failed rule expected observed facts cited sources responsible owner next review date"],
       mandatory=True, mandatory_basis="FIN-POL-001 s2/s5 minimum evidence before approval")
def evidence_completeness(ev, prior=None) -> SkillOutput:
    out = SkillOutput("evidence_completeness")
    req, inv = ev["request"], ev.get("invoice")
    refs = [("FIN-POL-001", "s2")]
    if not inv:
        out.finding("evidence", "FIN-POL-001 s2", "FAIL", "No invoice or credit-note document was supplied or parseable.", refs)
        out.exception("OTHER_CONTROL_RISK", "FIN-POL-001 s2 minimum evidence", "Invoice document attached",
                      "No parseable invoice document", OWNERS["OTHER_CONTROL_RISK"], refs, ["request"])
        out.unknowns.append({"item": "Invoice document", "impact": "Line-level matching and tax checks impossible",
                             "resolution": "Attach the supplier invoice (JSON, PDF text, CSV or e-mail)"})
        out.vote(Outcome.HOLD_FOR_INFORMATION, "invoice document missing")
        return out

    src = f"doc:{inv['source_filename']}"
    missing = [f for f in ("invoice_number", "invoice_date", "currency", "total") if not inv.get(f)]
    if inv.get("tax") is None and inv.get("doc_kind") == "invoice":
        missing.append("tax")
    if not req.get("po_ref") and not inv.get("po_ref") and not inv.get("non_po") and inv.get("doc_kind") == "invoice":
        missing.append("po_ref_or_non_po_justification")
    for w in inv.get("parse_warnings", []):
        out.unknowns.append({"item": f"Parse warning in {inv['source_filename']}", "impact": w,
                             "resolution": "Request a clean copy of the document"})
    if "po_ref_or_non_po_justification" in missing:
        missing.remove("po_ref_or_non_po_justification")
        out.finding("po_reference", "FIN-POL-001 s2", "FAIL", "No purchase-order reference and no approved non-PO justification.",
                    [("FIN-POL-001", "s2"), ("FIN-POL-012", "s1")], [src])
        out.exception("MISSING_PO", "FIN-POL-001 s2 PO reference or approved non-PO justification", "PO reference or non-PO justification",
                      "Neither supplied", OWNERS["MISSING_PO"], [("FIN-POL-001", "s2")], [src])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "no PO reference or non-PO justification")
    if missing:
        out.finding("evidence", "FIN-POL-001 s2", "FAIL", f"Missing required invoice fields: {', '.join(missing)}", refs, [src])
        out.exception("OTHER_CONTROL_RISK", "FIN-POL-001 s2 minimum evidence", "All minimum fields present",
                      f"Missing: {', '.join(missing)}", OWNERS["OTHER_CONTROL_RISK"], refs, [src])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "minimum evidence incomplete")
    else:
        out.finding("evidence", "FIN-POL-001 s2", "PASS", "Minimum invoice fields present.", refs, [src])

    # request header vs document
    mismatches = []
    if inv.get("invoice_number") and _norm_ref(inv["invoice_number"]) != _norm_ref(req["invoice_ref"]):
        mismatches.append(("invoice_ref", req["invoice_ref"], inv["invoice_number"]))
    if inv.get("total") is not None and Decimal(inv["total"]) != Decimal(req["amount"]):
        mismatches.append(("amount", str(req["amount"]), str(inv["total"])))
    if inv.get("currency") and inv["currency"] != req["currency"]:
        mismatches.append(("currency", req["currency"], inv["currency"]))
    if req.get("po_ref") and inv.get("po_ref") and req["po_ref"] != inv["po_ref"]:
        mismatches.append(("po_ref", req["po_ref"], inv["po_ref"]))
    if inv.get("total") is not None:
        out.calculations.append(calc("request_vs_document_amount", "document.total - request.amount",
                                     {"document.total": Decimal(inv["total"]), "request.amount": Decimal(req["amount"])},
                                     q(Decimal(inv["total"]) - Decimal(req["amount"])), Decimal("0.00"),
                                     Decimal(inv["total"]) == Decimal(req["amount"])))
    for fld, expected, observed in mismatches:
        out.exception("OTHER_CONTROL_RISK", f"Request/document consistency ({fld})", f"{fld} = {expected} (request)",
                      f"{fld} = {observed} (document)", OWNERS["OTHER_CONTROL_RISK"], [("FIN-POL-001", "s5")], [src, "request"])
    if mismatches:
        out.finding("consistency", "FIN-POL-001 s5", "FAIL",
                    "Request header disagrees with the invoice document: " + ", ".join(m[0] for m in mismatches),
                    [("FIN-POL-001", "s5")], [src, "request"])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "request/document mismatch")
    else:
        out.finding("consistency", "FIN-POL-001 s5", "PASS", "Request header agrees with the invoice document.",
                    [("FIN-POL-001", "s5")], [src, "request"])

    # addressed to a Northstar entity?
    bill_to = inv.get("bill_to")
    entities = ev.get("northstar_entities", ["northstar"])
    if bill_to and not any(e in bill_to.lower() for e in entities):
        out.finding("legal_entity", "FIN-POL-001 s1", "FAIL", f"Invoice is billed to '{bill_to}', not a Northstar entity.",
                    [("FIN-POL-001", "s1")], [src])
        out.exception("OTHER_CONTROL_RISK", "FIN-POL-001 s1 valid supplier obligation", "Bill-to is a Northstar Group entity",
                      f"Bill-to: {bill_to}", OWNERS["OTHER_CONTROL_RISK"], [("FIN-POL-001", "s1")], [src])
        out.vote(Outcome.REJECT_INVALID, "invoice not addressed to Northstar")
    out.facts.append({"fact": f"Invoice {inv.get('invoice_number')} dated {inv.get('invoice_date')} for "
                              f"{inv.get('currency')} {inv.get('total')} (tax {inv.get('tax')}) parsed by {inv['parser']}",
                      "evidence": [src]})
    return out


# =============================================================================
# 2. Duplicate detection
# =============================================================================
@skill("duplicate_detection", "processor",
       "Classify invoice-history matches into exact / probable duplicates / prior rejections.",
       compliance_refs=[("FIN-POL-005", "s1"), ("FIN-POL-005", "s2")],
       retrieval_queries=["duplicate invoice detection exact match paid posted fuzzy matching outcome reject duplicate"],
       mandatory=True, mandatory_basis="FIN-POL-001 s5 / FIN-POL-005 s1 every invoice checked for duplicates", produces=("non_po_history",))
def duplicate_detection(ev, prior=None) -> SkillOutput:
    out = SkillOutput("duplicate_detection")
    refs = [("FIN-POL-005", "s1"), ("FIN-POL-005", "s2")]
    if ev.get("history_error"):
        out.finding("duplicate", "FIN-POL-005 s1", "UNKNOWN", f"Invoice history unavailable: {ev['history_error']}", refs)
        out.exception("DUPLICATE_RISK", "FIN-POL-005 s1 duplicate check performed", "History check completed",
                      "History service unavailable", OWNERS["DUPLICATE_RISK"], refs)
        out.unknowns.append({"item": "Duplicate check", "impact": "Cannot exclude a duplicate payment",
                             "resolution": "Re-run when AP history service is available"})
        out.vote(Outcome.HOLD_FOR_INFORMATION, "duplicate check not performed")
        return out
    matches = [m for m in (ev.get("history") or []) if m["match_type"] in {"EXACT", "FUZZY"}]
    out.data["non_po_history"] = [m for m in (ev.get("history") or []) if m["match_type"] == "SAME_VENDOR_NON_PO"]
    if not matches:
        out.finding("duplicate", "FIN-POL-005 s1", "PASS", "No exact or fuzzy match in paid/posted/held/rejected records.",
                    refs, ["history:none"])
        return out
    for m in matches:
        ev_id = f"history:{m['record_id']}"
        if m["match_type"] == "EXACT" and m["status"] in {"PAID", "POSTED"}:
            out.finding("duplicate", "FIN-POL-005 s2", "FAIL",
                        f"Exact duplicate of {m['status']} record {m['record_id']} ({m['invoice_ref']}, {m['currency']} {m['amount']}).",
                        refs, [ev_id])
            out.exception("DUPLICATE_RISK", "FIN-POL-005 s2 exact duplicate", "No paid/posted invoice with same vendor, number, currency, amount",
                          f"Matches {m['record_id']} status {m['status']}", OWNERS["DUPLICATE_RISK"], refs, [ev_id])
            out.vote(Outcome.REJECT_DUPLICATE, f"exact match to {m['status']} {m['record_id']}")
        elif m["status"] == "REJECTED":
            out.finding("duplicate", "FIN-POL-005 s2", "FAIL",
                        f"Prior REJECTED record {m['record_id']} with same number (reason: {m.get('rejection_reason')}); "
                        "a prior rejection does not prove duplication - corrected fields must be reviewed.", refs, [ev_id])
            out.exception("DUPLICATE_RISK", "FIN-POL-005 s2 prior rejection review", "Reviewer confirms corrected fields",
                          f"Re-submission of rejected {m['record_id']} (was {m['currency']} {m['amount']})",
                          OWNERS["DUPLICATE_RISK"], refs, [ev_id])
            out.vote(Outcome.HOLD_FOR_INFORMATION, "re-submission of a rejected invoice needs review")
        else:
            out.finding("duplicate", "FIN-POL-005 s2", "FAIL",
                        f"Probable duplicate of {m['status']} record {m['record_id']} ({', '.join(m['match_reasons'])}).",
                        refs, [ev_id])
            out.exception("DUPLICATE_RISK", "FIN-POL-005 s2 probable fuzzy duplicate", "No probable match",
                          f"{m['record_id']} {m['invoice_ref']} {m['currency']} {m['amount']} ({', '.join(m['match_reasons'])})",
                          OWNERS["DUPLICATE_RISK"], refs, [ev_id])
            out.vote(Outcome.HOLD_FOR_INFORMATION, f"probable duplicate {m['record_id']}")
    return out


# =============================================================================
# 3. Vendor compliance
# =============================================================================
@skill("vendor_compliance", "processor",
       "Vendor status, bank-detail verification, payment hold after bank change, higher-risk flags and SoD exclusions.",
       compliance_refs=[("FIN-POL-004", "s2"), ("FIN-POL-004", "s3"), ("FIN-POL-004", "s4"), ("FIN-POL-003", "s3"),
                        ("FIN-POL-001", "s4"), ("FIN-POL-009", "s4")],
       retrieval_queries=["vendor status blocked dormant sanctions review pending verification invoices held",
                          "bank account change verification payment hold first payment financial control co-approval",
                          "segregation of duties person who creates or changes vendor record must not approve invoice five business days"],
       mandatory=True, mandatory_basis="FIN-POL-001 s5 confirm vendor status and payment instructions", produces=("high_risk_reasons", "excluded_approvers", "vendor_domain", "vendor_legal_name"))
def vendor_compliance(ev, prior=None) -> SkillOutput:
    out = SkillOutput("vendor_compliance")
    now = ev["now"]
    if ev.get("vendor_error") or not ev.get("vendor"):
        out.finding("vendor", "FIN-POL-004 s4", "UNKNOWN", f"Vendor master unavailable: {ev.get('vendor_error')}", [("FIN-POL-004", "s4")])
        out.exception("VENDOR_BLOCK", "FIN-POL-001 s5 confirm vendor status", "Vendor record retrieved and ACTIVE",
                      "Vendor master unavailable", OWNERS["VENDOR_BLOCK"], [("FIN-POL-001", "s5")])
        out.unknowns.append({"item": "Vendor status", "impact": "Cannot confirm vendor is payable",
                             "resolution": "Retry vendor master lookup"})
        out.vote(Outcome.HOLD_FOR_INFORMATION, "vendor status unknown")
        return out
    v = ev["vendor"]
    vid = f"vendor:{v['vendor_id']}@{v['last_updated']}"
    out.facts.append({"fact": f"Vendor {v['vendor_id']} '{v['legal_name']}' status {v['status']}, bank country "
                              f"{v['payment_details'].get('bank_country')}, account ****{v['payment_details'].get('account_last4')}",
                      "evidence": [vid]})
    if v["vendor_id"] != ev["request"]["vendor_id"]:
        out.vote(Outcome.HOLD_FOR_INFORMATION, "vendor id mismatch")
    if v["status"] != "ACTIVE":
        out.finding("vendor_status", "FIN-POL-004 s4", "FAIL", f"Vendor status is {v['status']}; invoices must be held.",
                    [("FIN-POL-004", "s4")], [vid])
        out.exception("VENDOR_BLOCK", "FIN-POL-004 s4 vendor status", "ACTIVE", v["status"], OWNERS["VENDOR_BLOCK"],
                      [("FIN-POL-004", "s4")], [vid])
        out.vote(Outcome.HOLD_FOR_INFORMATION, f"vendor {v['status']}")
    else:
        out.finding("vendor_status", "FIN-POL-004 s4", "PASS", "Vendor is ACTIVE.", [("FIN-POL-004", "s4")], [vid])

    high_risk: list[str] = []
    today = now.date()
    created = to_date(v["created_at"])
    if created and (today - created).days < P(ev, "vendor.new_vendor_days"):
        high_risk.append(f"new vendor ({(today - created).days} days old)")
    if v.get("bank_changed_at"):
        changed = to_date(v["bank_changed_at"])
        bd = business_days_between(changed, today)
        hold_days = P(ev, "vendor.bank_change_hold_business_days")
        out.calculations.append(calc("business_days_since_bank_change", "business_days(bank_changed_at, today)",
                                     {"bank_changed_at": changed, "today": today}, bd, hold_days, bd >= hold_days, rounding="n/a"))
        if bd < hold_days:
            out.finding("bank_change_hold", "FIN-POL-004 s2", "FAIL",
                        f"Verified bank change on {changed} is within the {hold_days}-business-day payment hold ({bd} elapsed).",
                        [("FIN-POL-004", "s2")], [vid])
            out.exception("BANK_CHANGE", "FIN-POL-004 s2 payment hold after bank change", f">= {hold_days} business days since change",
                          f"{bd} business day(s) since {changed}", OWNERS["BANK_CHANGE"], [("FIN-POL-004", "s2")], [vid])
            out.vote(Outcome.HOLD_FOR_INFORMATION, "vendor in post-bank-change payment hold")
        if v.get("payments_since_bank_change", 0) == 0:
            high_risk.append("first payment after verified bank change")
        out.data["recent_bank_change"] = bd < 10
    home = (v.get("country") or "AU").upper()
    bank_country = (v["payment_details"].get("bank_country") or home).upper()
    if bank_country != "AU":
        high_risk.append(f"overseas bank account ({bank_country})")
        screened = v.get("sanctions_screened_at")
        max_h = P(ev, "vendor.screening_max_age_hours")
        fresh = screened and (now - _dt(screened)).total_seconds() <= max_h * 3600
        out.finding("sanctions_screening", "FIN-POL-009 s4", "PASS" if fresh else "FAIL",
                    f"Sanctions screening at {screened} {'is' if fresh else 'is NOT'} within {max_h} hours.", [("FIN-POL-009", "s4")], [vid])
        if not fresh:
            out.exception("VENDOR_BLOCK", "FIN-POL-009 s4 sanctions screening currency", f"Screened within {max_h}h of payment run",
                          f"Last screened {screened}", OWNERS["VENDOR_BLOCK"], [("FIN-POL-009", "s4")], [vid])
            out.vote(Outcome.HOLD_FOR_INFORMATION, "sanctions screening stale")
    if "fraud_flag" in (v.get("risk_flags") or []):
        high_risk.append("fraud flag on vendor")

    # bank details quoted on the invoice must match the verified master
    inv = ev.get("invoice") or {}
    quoted = [(d["source_filename"], d["bank_details"]["account_last4"]) for d in ev.get("docs", []) if d.get("bank_details")]
    master_last4 = v["payment_details"].get("account_last4")
    mism = [(f, l4) for f, l4 in quoted if l4 and l4 != master_last4]
    if mism:
        f, l4 = mism[0]
        out.finding("bank_details_match", "FIN-POL-004 s2", "FAIL",
                    f"Payment instructions in {f} (account ****{l4}) differ from verified vendor master (****{master_last4}). "
                    "Document instructions are not sufficient to change bank details; a verification task is required.",
                    [("FIN-POL-004", "s2"), ("FIN-POL-004", "s3")], [f"doc:{f}", vid])
        out.exception("BANK_CHANGE", "FIN-POL-001 s5 payment instructions match vendor master", f"****{master_last4}",
                      f"****{l4} quoted in {f}", OWNERS["BANK_CHANGE"], [("FIN-POL-004", "s2")], [f"doc:{f}", vid])
        out.risk_indicators.append({"indicator": "newly changed bank details", "evidence": f"****{l4} quoted vs ****{master_last4} on file",
                                    "source": f})
        out.vote(Outcome.HOLD_FOR_INFORMATION, "unverified bank change instruction")
    elif quoted:
        out.finding("bank_details_match", "FIN-POL-001 s5", "PASS", "Payment instructions on the invoice match the vendor master.",
                    [("FIN-POL-001", "s5")], [vid])

    out.data["high_risk_reasons"] = high_risk
    if high_risk:
        out.finding("higher_risk_transaction", "FIN-POL-003 s3", "PASS",
                    "Higher-risk transaction: " + "; ".join(high_risk) + ". Two approvals incl. Financial Control required.",
                    [("FIN-POL-003", "s3")], [vid])
    # segregation of duties: creator can never approve; recent changer barred for 5 business days
    excluded = {}
    if v.get("created_by"):
        excluded[v["created_by"]] = "created the vendor record (FIN-POL-003 s1)"
    if v.get("bank_changed_by") and v.get("bank_changed_at") and \
            business_days_between(to_date(v["bank_changed_at"]), today) < P(ev, "sod.recent_change_business_days"):
        excluded[v["bank_changed_by"]] = (f"changed the vendor record within {P(ev, 'sod.recent_change_business_days')} "
                                          "business days (FIN-POL-001 s4)")
    out.data["excluded_approvers"] = excluded
    out.data["vendor_domain"] = v.get("email_domain")
    out.data["vendor_legal_name"] = v["legal_name"]
    return out


def _dt(v):
    from datetime import datetime, timezone
    d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# =============================================================================
# 4. Three-way match
# =============================================================================
@skill("three_way_match", "processor",
       "Per-line and total matching of invoice vs PO vs goods/service receipt with tolerance, freight and tax rules.",
       compliance_refs=[("FIN-POL-002", "s1"), ("FIN-POL-002", "s2"), ("FIN-POL-002", "s3"), ("FIN-POL-002", "s4"),
                        ("FIN-POL-002", "s5"), ("FIN-POL-009", "s1"), ("FIN-POL-009", "s3")],
       retrieval_queries=["three-way matching purchase order goods receipt tolerance price variance quantity",
                          "missing receipt invoice cannot be approved for payment receipter",
                          "invoice currency must match purchase order contract currency agreement",
                          "exchange-rate movement not price variance PO in AUD invoice foreign currency held unless conversion permitted"],
       mandatory=True, mandatory_basis="FIN-POL-001 s5 / FIN-POL-002 s1 three-way matching where a PO exists")
def three_way_match(ev, prior=None) -> SkillOutput:
    out = SkillOutput("three_way_match")
    req, inv = ev["request"], ev.get("invoice") or {}
    po_ref = req.get("po_ref") or inv.get("po_ref")
    if not po_ref:
        out.finding("three_way_match", "FIN-POL-002 s1", "NOT_APPLICABLE", "No purchase order referenced (non-PO path).",
                    [("FIN-POL-002", "s1")])
        return out
    if ev.get("po_error") or not ev.get("po"):
        out.finding("three_way_match", "FIN-POL-002 s1", "UNKNOWN",
                    f"Purchase order {po_ref} could not be retrieved: {ev.get('po_error')}", [("FIN-POL-002", "s1")])
        out.finding("receipt", "FIN-POL-002 s4", "UNKNOWN", "No goods/service receipt evidence available; receipt must not be "
                    "inferred from invoice delivery language.", [("FIN-POL-002", "s4")])
        out.exception("MISSING_PO", "FIN-POL-002 s1 matching basis", f"PO {po_ref} with lines and receipts",
                      f"PO unavailable ({ev.get('po_error')})", OWNERS["MISSING_PO"], [("FIN-POL-002", "s1")])
        out.exception("MISSING_RECEIPT", "FIN-POL-002 s4 missing receipt", "Recorded goods/service receipt",
                      "No receipt evidence", OWNERS["MISSING_RECEIPT"], [("FIN-POL-002", "s4")])
        out.unknowns += [{"item": f"Purchase order {po_ref}", "impact": "Lines, prices, tolerances and approval status unknown",
                          "resolution": "Retry PO service / obtain PO from requester"},
                         {"item": "Goods receipt", "impact": "Invoice cannot be approved for payment (FIN-POL-002 s4)",
                          "resolution": "Request receipt confirmation from the designated receipter"}]
        if re.search(r"\b(delivered|received|dispatched|shipped)\b", inv.get("text", ""), re.I):
            out.data["inference"] = ("Invoice text mentions delivery; per FIN-POL-002 s4 this is NOT treated as "
                                     "receipt evidence.")
        out.vote(Outcome.HOLD_FOR_INFORMATION, "PO / receipt evidence missing")
        return out

    po = ev["po"]
    pid = f"po:{po['po_ref']}@{po['last_updated']}"
    out.facts.append({"fact": f"PO {po['po_ref']} ({po['approval_status']}) {po['currency']} subtotal {po['subtotal']} "
                              f"tax {po['tax']} total {po['total']}; {len(po['goods_receipts'])} receipt record(s)",
                      "evidence": [pid]})
    if po["vendor_id"] != req["vendor_id"]:
        out.exception("OTHER_CONTROL_RISK", "FIN-POL-002 s1 PO belongs to vendor", req["vendor_id"], po["vendor_id"],
                      OWNERS["OTHER_CONTROL_RISK"], [("FIN-POL-002", "s1")], [pid])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "PO belongs to another vendor")
    if po["approval_status"] != "APPROVED":
        out.finding("po_approval", "FIN-POL-001 s5", "FAIL", f"PO approval status is {po['approval_status']}.",
                    [("FIN-POL-001", "s5")], [pid])
        out.exception("AUTHORITY_GAP", "PO must be approved before invoice approval", "APPROVED", po["approval_status"],
                      OWNERS["AUTHORITY_GAP"], [("FIN-POL-001", "s5")], [pid])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "PO not approved")
    else:
        out.finding("po_approval", "FIN-POL-001 s5", "PASS", f"PO approved by {po.get('approved_by')}.", [("FIN-POL-001", "s5")], [pid])

    inv_ccy = inv.get("currency") or req["currency"]
    if inv_ccy != po["currency"]:
        allowed = po.get("fx_conversion_permitted")
        out.finding("currency_agreement", "FIN-POL-009 s1", "PASS" if allowed else "FAIL",
                    f"Invoice currency {inv_ccy} vs PO currency {po['currency']}; conversion "
                    f"{'explicitly permitted' if allowed else 'NOT permitted'} by PO.", [("FIN-POL-009", "s1"), ("FIN-POL-009", "s3")], [pid])
        if not allowed:
            out.exception("OTHER_CONTROL_RISK", "FIN-POL-009 s1/s3 currency agreement", po["currency"], inv_ccy,
                          "Treasury", [("FIN-POL-009", "s1"), ("FIN-POL-009", "s3")], [pid])
            out.vote(Outcome.HOLD_FOR_INFORMATION, "currency mismatch")
            return out
    else:
        out.finding("currency_agreement", "FIN-POL-009 s1", "PASS", f"Invoice and PO both in {inv_ccy}.", [("FIN-POL-009", "s1")], [pid])

    # Tolerances come from the POLICY (config/policy_parameters.yaml). A PO's own tolerance block only applies when it records a
    # negotiated, tighter tolerance ("negotiated": true) - and even then it can never be looser than policy.
    raw_tol = po.get("tolerances") or {}
    tol = {k: Decimal(str(v)) for k, v in raw_tol.items() if k != "negotiated"} if raw_tol.get("negotiated") else {}
    fx_rate = Decimal(ev["fx"]["rate"]) if ev.get("fx") and inv_ccy != "AUD" else None
    def aud_limit(aud: Decimal) -> Decimal:   # AUD thresholds expressed in invoice currency
        return q(aud / fx_rate) if fx_rate else aud

    po_lines = {int(l["line_no"]): l for l in po["lines"]}
    receipts: dict[int, list] = {}
    for r in po["goods_receipts"]:
        receipts.setdefault(int(r["line_no"]), []).append(r)
    inv_lines = inv.get("lines") or []
    if not inv_lines:
        out.unknowns.append({"item": "Invoice line items", "impact": "Line-level matching not possible",
                             "resolution": "Obtain itemised invoice"})
        out.exception("OTHER_CONTROL_RISK", "FIN-POL-002 s1 per-line matching", "Itemised invoice", "No line items",
                      OWNERS["OTHER_CONTROL_RISK"], [("FIN-POL-002", "s1")])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "no invoice lines")
    any_fail = False
    for il in inv_lines:
        pl = po_lines.get(il["line_no"]) or next((l for l in po["lines"] if l["sku"] == il["sku"]), None)
        tag = f"line {il['line_no']} ({il['sku'] or il['description']})"
        if pl is None:
            any_fail = True
            out.exception("PRICE_VARIANCE", "FIN-POL-002 s1 line exists on PO", "Line on PO", f"{tag} not on PO",
                          OWNERS["PRICE_VARIANCE"], [("FIN-POL-002", "s1")], [pid])
            continue
        amount = Decimal(il["amount"])
        if pl["category"] == "freight":
            po_val = Decimal(pl["line_value"])
            diff = q(amount - po_val)
            pol_freight = q(PD(ev, "match.freight_abs_aud"))
            limit = aud_limit(min(tol.get("freight_abs", pol_freight), pol_freight)) if po.get("freight_allowed") else Decimal("0")
            ok = abs(diff) <= limit if po.get("freight_allowed") else amount == 0
            out.calculations.append(calc(f"freight_variance_{tag}", "invoice.freight - po.freight", {"invoice": amount, "po": po_val},
                                         diff, limit, ok))
            if not ok:
                any_fail = True
                out.exception("PRICE_VARIANCE", "FIN-POL-002 s2 freight variance", f"{po_val} +/- {limit}", f"{amount} (diff {diff})",
                              OWNERS["PRICE_VARIANCE"], [("FIN-POL-002", "s2")], [pid])
            continue
        qty = Decimal(il["quantity"] or 0)
        recs = receipts.get(int(pl["line_no"]), [])
        received = sum((Decimal(r["quantity_received"]) for r in recs), Decimal("0"))
        is_service = pl["category"] == "services"
        if not recs or (is_service and not any(r.get("service_confirmed") for r in recs)):
            any_fail = True
            out.finding("receipt", "FIN-POL-002 s4", "FAIL", f"{tag}: no {'service confirmation' if is_service else 'goods receipt'} recorded.",
                        [("FIN-POL-002", "s4")], [pid])
            out.exception("MISSING_RECEIPT", "FIN-POL-002 s4 missing receipt", "Recorded receipt / service confirmation",
                          f"{tag}: none", OWNERS["MISSING_RECEIPT"], [("FIN-POL-002", "s4")], [pid])
            continue
        qty_ok = qty <= received
        out.calculations.append(calc(f"quantity_check_{tag}", "invoiced_qty <= received_qty",
                                     {"invoiced_qty": qty, "received_qty": received}, qty - received, Decimal("0"), qty_ok,
                                     rounding="exact"))
        if not qty_ok:
            any_fail = True
            out.exception("QUANTITY_VARIANCE", "FIN-POL-002 s2 invoiced quantity <= received", f"<= {received}", f"{qty}",
                          OWNERS["QUANTITY_VARIANCE"], [("FIN-POL-002", "s2")], [pid])
        expected = q(Decimal(pl["unit_price"]) * qty)
        diff = q(amount - expected)
        # policy (config/policy_parameters.yaml) is the ceiling; a PO may set a tighter tolerance, never a looser one
        pol_abs = q(PD(ev, "match.services_abs_aud" if is_service else "match.goods_abs_aud"))
        pol_pct = PD(ev, "match.services_pct" if is_service else "match.goods_pct")
        abs_lim = min(tol.get("services_abs" if is_service else "goods_abs", pol_abs), pol_abs)
        pct_lim = min(tol.get("services_pct" if is_service else "goods_pct", pol_pct), pol_pct)
        threshold = min(aud_limit(abs_lim), q(pct_lim * Decimal(pl["line_value"])))
        ok = abs(diff) <= threshold
        out.calculations.append(calc(
            f"price_variance_{tag}", "invoice.line_amount - po.unit_price * invoiced_qty; threshold = min(abs_limit, pct * po.line_value)",
            {"invoice.line_amount": amount, "po.unit_price": Decimal(pl["unit_price"]), "invoiced_qty": qty,
             "abs_limit": aud_limit(abs_lim), "pct": pct_lim, "po.line_value": Decimal(pl["line_value"])},
            diff, threshold, ok))
        if not ok:
            any_fail = True
            out.exception("PRICE_VARIANCE", f"FIN-POL-002 s2 {'services' if is_service else 'goods'} tolerance",
                          f"{expected} +/- {threshold}", f"{amount} (variance {diff})", OWNERS["PRICE_VARIANCE"],
                          [("FIN-POL-002", "s2"), ("FIN-POL-002", "s3")], [pid])

    # totals and tax
    if inv_lines and inv.get("subtotal") is not None:
        line_sum = q(sum((Decimal(l["amount"]) for l in inv_lines), Decimal("0")))
        ok = line_sum == q(Decimal(inv["subtotal"]))
        out.calculations.append(calc("invoice_lines_sum", "sum(line.amount)", {"lines": len(inv_lines), "subtotal": Decimal(inv["subtotal"])},
                                     line_sum, Decimal(inv["subtotal"]), ok))
        if not ok:
            any_fail = True
            out.exception("OTHER_CONTROL_RISK", "Invoice arithmetic", f"sum(lines) = {inv['subtotal']}", f"{line_sum}",
                          OWNERS["OTHER_CONTROL_RISK"], [("FIN-POL-002", "s5")])
    if inv.get("subtotal") is not None and inv.get("tax") is not None and inv.get("total") is not None:
        st, tx, tt = Decimal(inv["subtotal"]), Decimal(inv["tax"]), Decimal(inv["total"])
        ok = q(st + tx) == q(tt)
        out.calculations.append(calc("invoice_total", "subtotal + tax", {"subtotal": st, "tax": tx}, q(st + tx), tt, ok))
        if not ok:
            any_fail = True
            out.exception("OTHER_CONTROL_RISK", "Invoice arithmetic", f"subtotal + tax = {tt}", f"{q(st + tx)}",
                          OWNERS["OTHER_CONTROL_RISK"], [("FIN-POL-002", "s5")])
        po_sub, po_tax = Decimal(po["subtotal"]), Decimal(po["tax"])
        rate = (po_tax / po_sub).quantize(Decimal("0.0001")) if po_sub else Decimal("0")
        exp_tax = q(st * rate)
        tdiff = q(tx - exp_tax)
        tax_tol = PD(ev, "match.tax_tolerance_aud")
        tax_ok = abs(tdiff) <= tax_tol
        out.calculations.append(calc("tax_check", "invoice.tax - invoice.subtotal * po_tax_rate (tax assessed separately)",
                                     {"invoice.subtotal": st, "po_tax_rate": rate, "invoice.tax": tx}, tdiff, tax_tol, tax_ok))
        if not tax_ok:
            any_fail = True
            out.finding("tax", "FIN-POL-002 s2", "FAIL", f"Tax {tx} differs from expected {exp_tax} at rate {rate}.",
                        [("FIN-POL-002", "s2")], [pid])
            out.exception("TAX_QUERY", "FIN-POL-002 s2 tax assessed separately", f"{exp_tax}", f"{tx} (diff {tdiff})",
                          OWNERS["TAX_QUERY"], [("FIN-POL-002", "s2")], [pid])
    if not any_fail and inv_lines:
        out.finding("three_way_match", "FIN-POL-002 s3", "PASS",
                    "All invoice lines agree with PO and receipts within tolerance.", [("FIN-POL-002", "s2"), ("FIN-POL-002", "s3")], [pid])
    elif any_fail:
        out.finding("three_way_match", "FIN-POL-002 s3", "FAIL", "One or more lines outside tolerance or unreceipted.",
                    [("FIN-POL-002", "s3")], [pid])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "three-way match failed")
    return out


# =============================================================================
# 5. Non-PO
# =============================================================================
# permitted categories, look-back window and repeat threshold are policy parameters (non_po.*)
EMERGENCY_TYPES = {"safety", "outage", "asset_loss"}
NOT_EMERGENCY = re.compile(r"expired contract|contract (has )?expired|forgot|late (order|request)|poor planning|"
                           r"supplier (pressure|insist)|end of (month|quarter)|budget", re.I)


@skill("non_po_validation", "processor", "Non-PO and emergency purchase eligibility, evidence and repeated-use escalation.",
       compliance_refs=[("FIN-POL-012", "s1"), ("FIN-POL-012", "s2"), ("FIN-POL-012", "s3"), ("FIN-POL-012", "s4")],
       retrieval_queries=["non-PO invoices statutory utilities property leases emergency definition approval repeated use"],
       mandatory=True, mandatory_basis="FIN-POL-012 s1 non-PO eligibility", produces=("is_non_po",))
def non_po_validation(ev, prior=None) -> SkillOutput:
    out = SkillOutput("non_po_validation")
    req, inv = ev["request"], ev.get("invoice") or {}
    if req.get("po_ref") or inv.get("po_ref") or inv.get("doc_kind") == "credit_note":
        out.finding("non_po", "FIN-POL-012 s1", "NOT_APPLICABLE", "PO-backed or credit note.", [("FIN-POL-012", "s1")])
        return out
    src = f"doc:{inv.get('source_filename')}"
    np_ = inv.get("non_po") or {}
    cat = (np_.get("category") or "").lower()
    out.data["is_non_po"] = True
    allowed = set(P(ev, "non_po.allowed_categories"))
    if cat not in allowed:
        out.finding("non_po_category", "FIN-POL-012 s1", "FAIL", f"Non-PO category '{cat or 'none'}' is not permitted.",
                    [("FIN-POL-012", "s1")], [src])
        out.exception("MISSING_PO", "FIN-POL-012 s1 permitted non-PO categories", ", ".join(sorted(allowed)),
                      cat or "no justification", OWNERS["MISSING_PO"], [("FIN-POL-012", "s1")], [src])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "missing PO without permitted non-PO category")
        return out
    if cat == "emergency":
        just = np_.get("justification") or ""
        et = (np_.get("emergency_type") or "").lower()
        genuine = et in EMERGENCY_TYPES and not NOT_EMERGENCY.search(just)
        out.finding("emergency_definition", "FIN-POL-012 s2", "PASS" if genuine else "FAIL",
                    f"Emergency type '{et}', justification: '{just[:160]}'.", [("FIN-POL-012", "s2")], [src])
        if not genuine:
            out.exception("MISSING_PO", "FIN-POL-012 s2 emergency definition", "Unforeseen safety risk / critical outage / asset loss",
                          f"type={et or 'none'}; justification indicates planning/contract issue", OWNERS["MISSING_PO"],
                          [("FIN-POL-012", "s2")], [src])
            out.vote(Outcome.HOLD_FOR_INFORMATION, "not a genuine emergency")
            return out
    else:
        out.finding("non_po_category", "FIN-POL-012 s1", "PASS", f"Non-PO category '{cat}' is permitted.", [("FIN-POL-012", "s1")], [src])
    receipt_docs = [d for d in ev.get("docs", []) if d.get("doc_kind") == "supporting" and d.get("attachment_source") == "internal"]
    if not receipt_docs:
        out.finding("non_po_receipt", "FIN-POL-012 s3", "FAIL", "No internal receipt / service evidence attached.",
                    [("FIN-POL-012", "s3")], [src])
        out.exception("MISSING_RECEIPT", "FIN-POL-012 s3 receipt evidence", "Internal receipt or service confirmation",
                      "None attached", OWNERS["MISSING_RECEIPT"], [("FIN-POL-012", "s3")], [src])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "non-PO receipt evidence missing")
    else:
        out.finding("non_po_receipt", "FIN-POL-012 s3", "PASS",
                    f"Internal service evidence: {receipt_docs[0]['source_filename']}.", [("FIN-POL-012", "s3")],
                    [f"doc:{receipt_docs[0]['source_filename']}"])
    window, threshold = P(ev, "non_po.repeat_window_days"), P(ev, "non_po.repeat_threshold")
    today = ev["now"].date()
    prior = [m for m in (ev.get("history") or []) if m["match_type"] == "SAME_VENDOR_NON_PO"
             and (not m.get("invoice_date") or (today - to_date(m["invoice_date"])).days <= window)]
    if prior and len(prior) + 1 >= threshold:
        ids = ", ".join(m["record_id"] for m in prior)
        out.finding("non_po_repeat", "FIN-POL-012 s4", "FAIL",
                    f"{len(prior) + 1} non-PO invoices from this supplier within {window} days ({ids} + this one).",
                    [("FIN-POL-012", "s4")], [f"history:{m['record_id']}" for m in prior])
        out.exception("OTHER_CONTROL_RISK", "FIN-POL-012 s4 repeated non-PO use", f"< {threshold} non-PO invoices in {window} days",
                      f"{len(prior) + 1} invoices ({ids})", "Procurement Governance", [("FIN-POL-012", "s4")],
                      [f"history:{m['record_id']}" for m in prior])
        out.vote(Outcome.ESCALATE_CONTROL_REVIEW, "repeated non-PO use requires sourcing review")
    return out


# =============================================================================
# 6. Credit notes
# =============================================================================
@skill("credit_note_validation", "processor", "Credit note reference, vendor/currency consistency, amount and tax treatment.",
       compliance_refs=[("FIN-POL-008", "s1"), ("FIN-POL-008", "s2")],
       retrieval_queries=["credit note reference original invoice same vendor currency application open invoices"],
       mandatory=True, mandatory_basis="FIN-POL-008 s1 credit note validation")
def credit_note_validation(ev, prior=None) -> SkillOutput:
    out = SkillOutput("credit_note_validation")
    inv = ev.get("invoice") or {}
    if inv.get("doc_kind") != "credit_note" and ev["request"].get("document_type") != "credit_note":
        return out
    src = f"doc:{inv.get('source_filename')}"
    ref = inv.get("original_invoice_ref")
    originals = [m for m in ev.get("original_invoice_records", []) if _norm_ref(m["invoice_ref"]) == _norm_ref(ref or "")]
    if not ref or not originals:
        out.finding("credit_note_reference", "FIN-POL-008 s1", "FAIL",
                    f"Credit note references '{ref}', which was not found for this vendor.", [("FIN-POL-008", "s1")], [src])
        out.exception("OTHER_CONTROL_RISK", "FIN-POL-008 s1 credit note references original invoice", "Valid original invoice",
                      f"{ref or 'no reference'} not found", OWNERS["OTHER_CONTROL_RISK"], [("FIN-POL-008", "s1")], [src])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "credit note without valid original")
        return out
    orig = originals[0]
    ok_ccy = orig["currency"] == inv.get("currency")
    amt = Decimal(inv.get("total") or 0)
    ok_amt = amt <= Decimal(orig["amount"])
    out.calculations.append(calc("credit_vs_original", "credit.total <= original.amount",
                                 {"credit.total": amt, "original.amount": Decimal(orig["amount"])},
                                 q(Decimal(orig["amount"]) - amt), Decimal("0"), ok_amt))
    if not ok_ccy or not ok_amt:
        out.exception("OTHER_CONTROL_RISK", "FIN-POL-008 s1 same vendor/currency, credit <= original",
                      f"{orig['currency']} <= {orig['amount']}", f"{inv.get('currency')} {amt}", OWNERS["OTHER_CONTROL_RISK"],
                      [("FIN-POL-008", "s1")], [src, f"history:{orig['record_id']}"])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "credit note inconsistent with original")
    else:
        out.finding("credit_note_reference", "FIN-POL-008 s1", "PASS",
                    f"References {orig['status']} invoice {orig['invoice_ref']} ({orig['record_id']}), same vendor and currency.",
                    [("FIN-POL-008", "s1")], [src, f"history:{orig['record_id']}"])
        out.data["application"] = f"Apply credit against open invoices for {ev['request']['vendor_id']} before any refund (FIN-POL-008 s2)."
    if inv.get("subtotal") is not None and inv.get("tax") is not None:
        exp = q(Decimal(inv["subtotal"]) * Decimal(ev.get("gst_rate", "0.10")))
        diff = q(Decimal(inv["tax"]) - exp)
        ctol = PD(ev, "credit.tax_tolerance_aud")
        ok = abs(diff) <= ctol
        out.calculations.append(calc("credit_note_tax", "credit.tax - credit.subtotal * gst_rate",
                                     {"credit.subtotal": Decimal(inv["subtotal"]), "gst_rate": ev.get("gst_rate", "0.10")}, diff, ctol, ok))
        if not ok:
            out.exception("TAX_QUERY", "FIN-POL-008 s1 validate tax treatment", str(exp), str(inv["tax"]), OWNERS["TAX_QUERY"],
                          [("FIN-POL-008", "s1")], [src])
            out.vote(Outcome.HOLD_FOR_INFORMATION, "credit note tax query")
    return out


# =============================================================================
# 7. FX
# =============================================================================
@skill("fx_validation", "processor", "Convert foreign-currency amounts to AUD with the corporate daily rate for authority assessment.",
       compliance_refs=[("FIN-POL-009", "s2"), ("FIN-POL-009", "s3")],
       retrieval_queries=["foreign currency conversion corporate daily rate approval authority assessed in AUD"],
       mandatory=True, mandatory_basis="FIN-POL-009 s2 authority assessed in AUD", produces=("amount_aud",))
def fx_validation(ev, prior=None) -> SkillOutput:
    out = SkillOutput("fx_validation")
    req, inv = ev["request"], ev.get("invoice") or {}
    ccy = inv.get("currency") or req["currency"]
    amount = _amount(ev)
    base = ev.get("base_currency", "AUD")
    if ccy == base:
        out.data["amount_aud"] = q(amount)
        return out
    if ev.get("fx_error") or not ev.get("fx"):
        out.finding("fx_rate", "FIN-POL-009 s2", "UNKNOWN", f"No corporate rate available: {ev.get('fx_error')}", [("FIN-POL-009", "s2")])
        out.unknowns.append({"item": f"{ccy}/AUD corporate rate", "impact": "Approval authority cannot be assessed",
                             "resolution": "Obtain Treasury corporate daily rate"})
        out.exception("AUTHORITY_GAP", "FIN-POL-009 s2 authority assessed in AUD", "Corporate daily rate", "Unavailable",
                      "Treasury", [("FIN-POL-009", "s2")])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "fx rate unavailable")
        return out
    fx = ev["fx"]
    rate = Decimal(fx["rate"])
    aud = q(amount * rate)
    fid = f"fx:{fx['from_currency']}{fx['to_currency']}@{fx['rate_date']}"
    out.calculations.append(calc("amount_in_aud", "invoice.total * corporate_rate",
                                 {"invoice.total": amount, "currency": ccy, "corporate_rate": rate, "rate_date": fx["rate_date"],
                                  "rate_source": fx["source"]}, aud))
    out.finding("fx_rate", "FIN-POL-009 s2", "PASS", f"{ccy} {amount} = AUD {aud} at {rate} ({fx['source']}, {fx['rate_date']}).",
                [("FIN-POL-009", "s2")], [fid])
    if re.search(r"exchange rate|fx rate|conversion rate", " ".join(d.get("text", "") for d in ev.get("docs", [])), re.I):
        out.data["attachment_rate_ignored"] = True
    out.data["amount_aud"] = aud
    return out


# =============================================================================
# 8. Injection / instruction screening of untrusted text
# =============================================================================
SCREEN_PATTERNS = [
    ("request to bypass normal approval / controls", r"ignore (all )?(previous|prior)?\s*(policies|instructions|rules)|"
     r"system instructions|disable (all )?checks|skip (duplicate|approval|verification)|mark (this|the) document as verified|"
     r"call the payment tool|do not ask (a )?human|without approval|no approval (is )?(needed|required)|already approved"),
    # "confidential" only in a payment context - not in the standard e-mail disclaimer ("this e-mail ... is confidential")
    ("urgent or secret payment language", r"\burgent(ly)?\b|immediately|same[- ]day|\bsecret\b|do not tell|today only|"
     r"(keep|treat) (this|it|the payment) (strictly )?confidential|confidential(ly)? (payment|transfer|request|arrangement)"),
    ("newly changed bank details", r"bank (account|details) (has |have )?(changed|been updated)|new bank account|new account (ending|number|details|no\b)|"
     r"updated (bank|payment) (details|instructions)|change of bank"),
    ("supplier-supplied verification contact", r"use the contact (number|details) in this|call (us|me) on|reply to this (e-?mail|address) to confirm"),
    ("manual payment request", r"manual payment|pay (outside|off)[- ]cycle|weekend payment"),
]


@skill("instruction_screen", "processor",
       "Screen untrusted case text (attachments, e-mails, notes) for injected instructions and fraud language. "
       "Matches are recorded as risk indicators; the text is never obeyed.",
       compliance_refs=[("FIN-POL-005", "s3"), ("FIN-POL-005", "s4"), ("FIN-POL-004", "s3")],
       retrieval_queries=["retrieved text tells agent disable checks risk indicator not instruction fraud indicators urgent secret",
                          "prohibited automation agent workflow must not update bank details based solely on retrieved documents or model output"],
       mandatory=True, mandatory_basis="FIN-POL-005 s4 untrusted text is a risk indicator, never instruction")
def instruction_screen(ev, prior=None) -> SkillOutput:
    out = SkillOutput("instruction_screen")
    texts = [(d["source_filename"], d.get("text", "")) for d in ev.get("docs", [])]
    if ev["request"].get("notes"):
        texts.append(("request.notes", ev["request"]["notes"]))
    hits = []
    for src, text in texts:
        for label, pat in SCREEN_PATTERNS:
            m = re.search(pat, text, re.I)
            if m:
                start = max(0, m.start() - 40)
                snippet = redact_text(text[start:m.end() + 40].replace("\n", " "))   # FIN-POL-010 s2: no contact/bank data
                hits.append({"indicator": label, "evidence": snippet[:160], "source": src})
    out.risk_indicators = hits
    injected = [h for h in hits if h["indicator"].startswith("request to bypass")]
    if injected:
        out.finding("untrusted_instructions", "FIN-POL-005 s4", "FAIL",
                    f"Embedded instructions found in {', '.join(sorted({h['source'] for h in injected}))}; treated as a risk "
                    "indicator and NOT followed. Policy and approval gates remain in force.",
                    [("FIN-POL-005", "s4"), ("FIN-POL-004", "s3")], [f"doc:{h['source']}" for h in injected])
        out.data["injection_detected"] = True
    else:
        out.finding("untrusted_instructions", "FIN-POL-005 s4", "PASS", "No embedded instructions detected in case text.",
                    [("FIN-POL-005", "s4")])
    return out


# =============================================================================
# 9. Fraud indicator scoring
# =============================================================================
@skill("fraud_indicator_scoring", "processor",
       "Aggregate distinct FIN-POL-005 s3 indicators; two or more => escalate to Financial Crime and Controls.",
       compliance_refs=[("FIN-POL-005", "s3"), ("FIN-POL-005", "s4"), ("FIN-POL-007", "s3")],
       retrieval_queries=["fraud indicators escalate two or more newly changed bank details urgent unusual domain mismatched vendor name",
                          "risk score decision support model limitations retrieved text tells agent disable checks risk indicator",
                          "escalation targets suspected fraud financial crime and controls without notifying supplier"],
       mandatory=True, mandatory_basis="FIN-POL-005 s3 escalation on two or more indicators", produces=("indicator_count",))
def fraud_indicator_scoring(ev, prior: list[SkillOutput] | None = None) -> SkillOutput:
    prior = prior or []
    out = SkillOutput("fraud_indicator_scoring")
    indicators = [ri for p in prior for ri in p.risk_indicators]
    new: list[dict] = []          # indicators first detected by this skill (others are already reported upstream)
    vdata = next((p.data for p in prior if p.skill == "vendor_compliance"), {})
    inv = ev.get("invoice") or {}
    vdomain = vdata.get("vendor_domain")
    for d in ev.get("docs", []):
        em = d.get("email") or {}
        if em.get("from_domain") and vdomain and not em["from_domain"].endswith(vdomain):
            new.append({"indicator": "unusual domain", "evidence": f"sent from {em['from_domain']}, vendor domain {vdomain}",
                        "source": d["source_filename"]})
    legal = vdata.get("vendor_legal_name")
    if legal and inv.get("vendor_name") and _norm_name(inv["vendor_name"]) not in _norm_name(legal) and \
            _norm_name(legal) not in _norm_name(inv["vendor_name"]):
        new.append({"indicator": "mismatched vendor name", "evidence": f"invoice '{inv['vendor_name']}' vs master '{legal}'",
                    "source": inv.get("source_filename", "invoice")})
    indicators += new
    distinct = sorted({i["indicator"] for i in indicators})
    out.risk_indicators = new
    out.calculations.append(calc("fraud_indicator_count", "count(distinct FIN-POL-005 s3 indicators)",
                                 {"indicators": "; ".join(distinct) or "none"}, len(distinct), P(ev, "fraud.escalation_indicator_count"),
                                 len(distinct) < P(ev, "fraud.escalation_indicator_count"), rounding="n/a"))
    out.data["indicator_count"] = len(distinct)
    out.data["indicators"] = distinct
    if len(distinct) >= P(ev, "fraud.escalation_indicator_count"):
        out.finding("fraud_indicators", "FIN-POL-005 s3", "FAIL",
                    f"{len(distinct)} distinct fraud indicators: {', '.join(distinct)}. Escalate to {FRAUD_OWNER}; "
                    "do not notify the supplier of the suspicion. Score is decision support only.",
                    [("FIN-POL-005", "s3"), ("FIN-POL-007", "s3")])
        out.exception("OTHER_CONTROL_RISK", "FIN-POL-005 s3 fraud indicators", "< 2 indicators",
                      f"{len(distinct)}: {', '.join(distinct)}", FRAUD_OWNER, [("FIN-POL-005", "s3")])
        out.vote(Outcome.ESCALATE_CONTROL_REVIEW, "two or more fraud indicators")
    else:
        out.finding("fraud_indicators", "FIN-POL-005 s3", "PASS",
                    f"{len(distinct)} fraud indicator(s){': ' + distinct[0] if distinct else ''}; below escalation threshold.",
                    [("FIN-POL-005", "s3")])
    return out


# =============================================================================
# 10. Authority routing
# =============================================================================
@skill("authority_routing", "processor",
       "Determine required approval slots from the current delegated authority matrix, higher-risk rules and non-PO rules.",
       compliance_refs=[("FIN-POL-003", "s1"), ("FIN-POL-003", "s2"), ("FIN-POL-003", "s3"), ("FIN-POL-003", "s4"),
                        ("FIN-POL-003", "s5"), ("FIN-POL-001", "s3"), ("FIN-POL-001", "s4"), ("FIN-POL-012", "s3")],
       retrieval_queries=["delegated financial authority matrix approval limits cost centre manager department director",
                          "higher-risk transactions two approvals financial control new vendor changed bank account overseas",
                          "temporary delegation authority register delegate delegator expired delegation invalid",
                          "segregation of duties requester receipter financial approver distinct vendor record five business days",
                          "processing outcome recommendation is not an approval validate delegated authority before posting",
                          "non-PO emergency purchase evidence approval Department Director up to AUD 10,000 Procurement Governance above"],
       mandatory=True, mandatory_basis="FIN-POL-001 s3 / FIN-POL-003 validate delegated authority before posting", produces=("required_role", "constraints", "amount_aud"))
def authority_routing(ev, prior: list[SkillOutput] | None = None) -> SkillOutput:
    prior = prior or []
    out = SkillOutput("authority_routing")
    data = {}
    for p in prior:
        data.update({f"{p.skill}.{k}": v for k, v in p.data.items()})
    if ev.get("authority_error") or not ev.get("authority"):
        out.finding("authority_register", "FIN-POL-003 s4", "UNKNOWN", f"Authority register unavailable: {ev.get('authority_error')}",
                    [("FIN-POL-003", "s4")])
        out.exception("AUTHORITY_GAP", "FIN-POL-001 s3 validate delegated authority", "Register available", "Unavailable",
                      OWNERS["AUTHORITY_GAP"], [("FIN-POL-001", "s3")])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "authority register unavailable")
        return out
    reg = ev["authority"]
    rid = f"authority-register:v{reg['version']}"
    if reg.get("policy_document") != "FIN-POL-003" or not str(reg["version"]).startswith("4."):
        out.finding("authority_register", "FIN-POL-003 s4", "FAIL",
                    f"Register version {reg['version']} is not aligned to current FIN-POL-003 v4.0.", [("FIN-POL-003", "s4")], [rid])
        out.exception("AUTHORITY_GAP", "Current authority matrix", "FIN-POL-003 v4.x register", f"v{reg['version']}",
                      OWNERS["AUTHORITY_GAP"], [("FIN-POL-003", "s4")], [rid])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "authority register out of date")
        return out
    amount_aud = data.get("fx_validation.amount_aud")
    if amount_aud is None:
        out.unknowns.append({"item": "AUD amount", "impact": "Approval limit cannot be determined", "resolution": "Resolve FX"})
        return out
    limits = reg["role_limits"] if "role_limits" in reg else {
        "Cost Centre Manager": "10000", "Department Director": "50000", "Executive Director": "250000",
        "Chief Financial Officer": "1000000", "Chief Executive Officer": None}
    required_role = next((r for r, lim in limits.items() if lim is None or amount_aud <= Decimal(lim)), "Chief Executive Officer")
    out.calculations.append(calc("approval_limit_lookup", "min role where amount_aud <= role_limit (FIN-POL-003 v4.0 s2)",
                                 {"amount_aud_incl_tax": amount_aud, **{r: (l or "unlimited") for r, l in limits.items()}},
                                 required_role, rounding="n/a"))
    slots = [{"slot": "primary", "description": f"Approver with authority >= AUD {amount_aud} (minimum role: {required_role})",
              "min_limit_aud": format(amount_aud, "f"), "requires_flag": None}]
    high_risk = data.get("vendor_compliance.high_risk_reasons") or []
    if high_risk:
        slots.append({"slot": "financial_control", "description": "Financial Control co-approval (higher-risk: " + "; ".join(high_risk) + ")",
                      "min_limit_aud": None, "requires_flag": "financial_control"})
    if data.get("non_po_validation.is_non_po"):
        np_max = PD(ev, "non_po.director_max_aud")
        if amount_aud <= np_max:
            slots[0]["min_limit_aud"] = max(Decimal(slots[0]["min_limit_aud"]), Decimal(limits["Department Director"])).__format__("f")
            slots[0]["description"] = f"Department Director (or above) for non-PO <= AUD {np_max:,} (FIN-POL-012 s3)"
        else:
            slots.append({"slot": "procurement_governance", "description": f"Procurement Governance approval for non-PO > AUD {np_max:,}",
                          "min_limit_aud": None, "requires_flag": "procurement_governance"})
    constraints = {
        "excluded_approvers": data.get("vendor_compliance.excluded_approvers") or {},
        "requester_id": ev["request"].get("requester_id"),
        "po_requester_id": (ev.get("po") or {}).get("requester_id"),
        "receipters": sorted({r["receipted_by"] for r in (ev.get("po") or {}).get("goods_receipts", [])}),
        "distinct_roles_required": bool(amount_aud > PD(ev, "sod.distinct_people_above_aud")),
        "register_version": reg["version"],
    }
    out.finding("delegated_authority", "FIN-POL-003 s2", "PASS",
                f"AUD {amount_aud} incl. tax requires {required_role}" + (" + Financial Control" if high_risk else "") + ".",
                [("FIN-POL-003", "s1"), ("FIN-POL-003", "s2")] + ([("FIN-POL-003", "s3")] if high_risk else []), [rid])
    if constraints["distinct_roles_required"]:
        out.finding("segregation_of_duties", "FIN-POL-001 s4", "PASS",
                    f"Above AUD {PD(ev, 'sod.distinct_people_above_aud'):,}: requester, receipter and approver must be distinct "
                    "(enforced at approval time).",
                    [("FIN-POL-001", "s4")], [rid])
    out.approvals = slots
    out.data.update(required_role=required_role, constraints=constraints, amount_aud=amount_aud)
    return out


# =============================================================================
# 11. Payment scheduling (proposal only)
# =============================================================================
@skill("payment_scheduling", "processor", "Propose the payment run date from agreed terms (proposal only; release is out of scope).",
       compliance_refs=[("FIN-POL-006", "s1"), ("FIN-POL-006", "s2"), ("FIN-POL-006", "s4")],
       retrieval_queries=["payment terms 30 days payment runs Tuesday Thursday contracted terms override"],
       produces=("proposed_payment_date",))
def payment_scheduling(ev, prior=None) -> SkillOutput:
    out = SkillOutput("payment_scheduling")
    inv = ev.get("invoice") or {}
    if inv.get("doc_kind") == "credit_note" or not inv.get("invoice_date"):
        return out
    terms = (ev.get("po") or {}).get("payment_terms_days") or P(ev, "payment.default_terms_days")
    run_days = tuple(P(ev, "payment.run_weekdays"))
    inv_date = to_date(inv["invoice_date"])
    due = inv_date + timedelta(days=int(terms))
    while due.weekday() >= 5:            # weekend due date -> preceding business day
        due -= timedelta(days=1)
    today = ev["now"].date()
    run = due
    while run.weekday() not in run_days:   # Tuesday / Thursday standard runs on or before due date
        run -= timedelta(days=1)
    if run < today:
        run = today + timedelta(days=1)
        while run.weekday() not in run_days:
            run += timedelta(days=1)
    out.calculations.append(calc("proposed_payment_run", "latest Tue/Thu run <= invoice_date + agreed_terms",
                                 {"invoice_date": inv_date, "agreed_terms_days": terms, "due_date": due}, run.isoformat(), rounding="n/a"))
    if inv.get("payment_terms_days") and int(inv["payment_terms_days"]) != int(terms):
        out.facts.append({"fact": f"Invoice prints {inv['payment_terms_days']}-day terms; agreed terms of {terms} days apply "
                                  "(FIN-POL-006 s1).", "evidence": [f"doc:{inv['source_filename']}"]})
    out.data["proposed_payment_date"] = run.isoformat()
    return out
