"""Swagger "Try it out" test cases, generated from mock_data/scenarios.json so they never drift from the
evaluated scenarios. Every scenario becomes a selectable example on POST /runs, with the expected
result and the approver(s) to use next in its description.
"""
from __future__ import annotations

import json
from pathlib import Path


def _approver_hint(steps: list[dict]) -> str:
    hints = []
    for st in steps:
        if st["op"] != "decide":
            continue
        verb = "approve" if st["decision"] == "APPROVE" else "reject"
        expect = {200: "accepted", 403: "refused (403)", 409: "no effect (409)"}.get(st["expect_http"], str(st["expect_http"]))
        hints.append(f"{verb} as `{st['approver_id']}` -> {expect}")
    return "; ".join(hints)


def run_examples(root: Path) -> dict:
    data = json.loads((root / "mock_data" / "scenarios.json").read_text())
    out = {}
    for sc in data["scenarios"]:
        req = json.loads((root / sc["request_file"]).read_text())
        exp = sc["expected"]
        expected = (f"expect HTTP {exp['http']} (request rejected by validation)" if exp.get("http")
                    else f"expect status **{sc['steps'][0].get('expect_status', exp.get('status'))}**, outcome "
                         f"**{exp.get('outcome')}**")
        if sc["steps"][0].get("expect_primary_exception"):
            expected += f", primary exception {sc['steps'][0]['expect_primary_exception']}"
        hint = _approver_hint(sc["steps"])
        desc = f"{sc['signals']}<br/>**Expected:** {expected}." + (f"<br/>**Next (POST /approvals/{{id}}/decision):** {hint}." if hint else "")
        if any(st["op"] == "add_evidence" for st in sc["steps"]):
            desc += "<br/>**Next:** POST /runs/{run_id}/evidence with example `supply PO`."
        if any(st.get("crash_after") for st in sc["steps"]):
            desc += "<br/>Crash simulation needs the CLI/env var (FINAGENT_CRASH_AFTER_NODE); via Swagger it runs normally."
        out[f"{sc['case_id']} {sc['title']}"[:80]] = {"summary": f"{sc['case_id']} - {sc['title']}", "description": desc, "value": req}
    return out


DECISION_EXAMPLES = {
    "approve - Cost Centre Manager (limit AUD 10k)": {
        "summary": "Approve as u-ccm-01 (Cost Centre Manager, AUD 10,000)",
        "description": "Use for cases <= AUD 10k (FIN-005, FIN-007, FIN-017 ...). Refused (403) above the limit.",
        "value": {"decision": "APPROVE", "approver_id": "u-ccm-01", "comment": "Checked", "callback_id": "swagger-ccm-1"}},
    "approve - Department Director (limit AUD 50k)": {
        "summary": "Approve as u-dd-01 (Department Director, AUD 50,000)",
        "description": "FIN-001 (AUD 18,150), FIN-013, FIN-015, FIN-024, FIN-029.",
        "value": {"decision": "APPROVE", "approver_id": "u-dd-01", "comment": "Checked PO and receipt", "callback_id": "swagger-dd-1"}},
    "approve - Executive Director (limit AUD 250k)": {
        "summary": "Approve as u-ed-01 - refused for FIN-018 (AUD 330,000)",
        "value": {"decision": "APPROVE", "approver_id": "u-ed-01", "callback_id": "swagger-ed-1"}},
    "approve - CFO (limit AUD 1M)": {
        "summary": "Approve as u-cfo-01 (FIN-018)",
        "value": {"decision": "APPROVE", "approver_id": "u-cfo-01", "callback_id": "swagger-cfo-1"}},
    "co-approve - Financial Control": {
        "summary": "Second approval by Financial Control (u-fc-01 / u-fc-02)",
        "description": "Higher-risk cases need it: FIN-012 new vendor, FIN-013 overseas account, FIN-034 first payment after bank change.",
        "value": {"decision": "APPROVE", "approver_id": "u-fc-02", "comment": "Financial Control co-approval", "callback_id": "swagger-fc-1"}},
    "refused - vendor creator (segregation of duties)": {
        "summary": "u-dd-02 created vendor V-1013 -> 403 for FIN-024",
        "value": {"decision": "APPROVE", "approver_id": "u-dd-02", "callback_id": "swagger-sod-1"}},
    "refused - expired delegation": {
        "summary": "u-ccm-02's Department Director delegation expired -> 403 for FIN-029",
        "value": {"decision": "APPROVE", "approver_id": "u-ccm-02", "callback_id": "swagger-del-1"}},
    "duplicate click (send twice)": {
        "summary": "Send this twice: the second response is replayed (replayed=true), nothing executes again",
        "value": {"decision": "APPROVE", "approver_id": "u-ccm-01", "callback_id": "swagger-duplicate-1"}},
    "reject": {
        "summary": "Reject - nothing is posted; outcome pool REJECTED_BY_APPROVER (FIN-027)",
        "value": {"decision": "REJECT", "approver_id": "u-ccm-01", "comment": "Query with supplier", "callback_id": "swagger-reject-1"}},
}

EVIDENCE_EXAMPLES = {
    "supply PO": {"summary": "FIN-038: supply the missing PO reference",
                  "value": {"po_ref": "PO-40038", "note": "Requester confirms PO-40038 covers this training."}},
    "add internal note": {"summary": "Attach an internal confirmation document",
                          "value": {"note": "Receipt confirmed by site manager",
                                    "attachments": [{"filename": "receipt_confirmation.txt", "content_type": "text/plain",
                                                     "source": "internal", "content": "Goods received in full on 2026-11-10. Signed: site manager."}]}},
}

EVAL_EXAMPLES = {
    "required cases": {"summary": "FIN-001..FIN-005 (brief's required cases)",
                       "value": {"cases": ["FIN-001", "FIN-002", "FIN-003", "FIN-004", "FIN-005"]}},
    "all scenarios": {"summary": "All 42 scenarios (about 5 s)", "value": {"cases": None}},
    "safety cases": {"summary": "Injection, duplicate, missing evidence, crash/resume",
                     "value": {"cases": ["FIN-003", "FIN-002", "FIN-004", "FIN-022", "FIN-040"]}},
}

SUBSCRIPTION_EXAMPLES = {
    "all events to local listener": {"summary": "Run `finagent webhooks listen --port 9099` first",
                                     "value": {"url": "http://localhost:9099/hooks", "events": ["*"]}},
    "approvals only": {"value": {"url": "http://localhost:9099/hooks", "events": ["approval.requested", "approval.resolved"]}},
}
