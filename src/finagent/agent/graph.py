"""Explicit, bounded, checkpointed state graph for one financial case.

    RECEIVE -> PARSE_INPUTS -> RETRIEVE_POLICY -> GATHER_EVIDENCE -> RECONCILE -> DRAFT -> DECIDE
                                                                                              |
                     +----------------------------------------+-------------------------------+
                     v                                        v
              APPROVAL_GATE  --(stop: AWAITING_APPROVAL)      HOLD_FINALIZE (stop: NEEDS_INFORMATION)
                     |
        approve -> SUBMIT_DECISION -> FINALIZE (COMPLETED)
        reject  -> FINALIZE_REJECTED (COMPLETED, nothing submitted)

* Every node is a pure-ish function of persisted state; after each node the
  full state is checkpointed (SQLite) before the next node starts, so a crash
  resumes at the next node (``resume``) with no repeated side effects.
* The only side-effecting node is SUBMIT_DECISION; it is approval-gated and
  idempotent (idempotency key derived from run + approval + outcome + amount).
* Budgets: ``agent.max_steps`` nodes, ``agent.max_tool_calls`` tool calls,
  ``agent.max_llm_calls`` model calls per run.
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from ..audit import AuditLog
from ..governance.params import P
from ..llm.drafting import SYSTEM_PROMPT, build_user_prompt, validate_draft
from ..llm.providers import LLMError, LLMProvider
from ..schemas import (CONSEQUENTIAL_OUTCOMES, OUTCOME_PRECEDENCE, ActionTaken, ApprovalDecisionIn, Calculation, Citation,
                       ExceptionRecord, FinalResult, FindingStatus, Inference, Outcome, PolicyFinding, ProcessingRequest,
                       Recommendation, RiskIndicator, RunStatus, SourcedFact, TrustLevel, Unknown, to_jsonable)
from ..skills.base import REGISTRY, SkillOutput
from ..skills.parsers.registry import parse_attachment
from ..store import Store, new_id
from ..tools.base import ToolBudgetExceeded, ToolContext, ToolPermissionDenied, ToolRunner
from ..tools.finance_tools import idempotency_key_for
from .calc import add_business_days, q

TERMINAL = {RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.NEEDS_INFORMATION.value}
NORTHSTAR_ENTITIES = ["northstar"]


class SimulatedCrash(BaseException):
    """Raised to emulate a process crash between nodes (tests / demo only)."""


class BudgetExceeded(Exception):
    pass


class ApprovalError(Exception):
    def __init__(self, http_status: int, message: str, body: dict | None = None):
        super().__init__(message)
        self.http_status, self.body = http_status, body or {"error": message}


def _evidence_source_type(eid: str) -> str:
    for prefix, kind in (("vendor:", "vendor_master"), ("po:", "purchase_order"), ("history:", "invoice_history"),
                         ("fx:", "fx_rate"), ("authority-register:", "authority_register"), ("doc:", "case_document")):
        if eid.startswith(prefix):
            return kind
    return "request"


class Engine:
    def __init__(self, settings, store: Store, audit: AuditLog, tools: ToolRunner, llm: LLMProvider, retriever, webhooks,
                 pipeline, policy=None, skill_runner=None):
        self.settings, self.store, self.audit, self.tools, self.llm = settings, store, audit, tools, llm
        self.retriever, self.webhooks, self.pipeline = retriever, webhooks, pipeline
        self.policy = policy                  # policy numbers + baseline check (governance/baseline.py); None = defaults
        self.skill_runner = skill_runner      # asks the judgement skills' SKILL.md questions (small + large model)
        self.nodes: dict[str, Callable[[str, dict], tuple[str | None, str]]] = {
            "INTAKE": self.n_receive,                    # old name of RECEIVE, for runs stored before the rename
            "RECEIVE": self.n_receive, "PARSE_INPUTS": self.n_parse, "RETRIEVE_POLICY": self.n_retrieve,
            "GATHER_EVIDENCE": self.n_gather, "RECONCILE": self.n_reconcile, "DRAFT": self.n_draft,
            "DECIDE": self.n_decide, "APPROVAL_GATE": self.n_approval_gate, "HOLD_FINALIZE": self.n_hold_finalize,
            "SUBMIT_DECISION": self.n_submit, "FINALIZE": self.n_finalize, "FINALIZE_REJECTED": self.n_finalize_rejected,
        }
        self._deferred: dict[str, list] = {}

    def now(self):
        return self.settings.now()

    def _trace(self, state: dict, step: str, actor: str, decision: str, reason: str, refs: list | None = None) -> None:
        """Reasoning trace: WHY each key step went the way it did. actor = system | skill:<name> | model | human:<id>."""
        state.setdefault("trace", []).append({"at": self.iso(), "step": step, "actor": actor, "decision": decision,
                                              "reason": reason, "refs": refs or []})

    def _emit(self, event_type: str, run_id: str, data: dict) -> None:
        """Queue a webhook from inside a node; it is sent only AFTER the node's checkpoint is committed, so an
        external system reacting instantly (e.g. an approval callback) always sees the persisted state."""
        self._deferred.setdefault(run_id, []).append((event_type, data))

    def _auto_diagram(self, run_id: str) -> None:
        """settings app.run_diagrams: [svg|pdf|png] -> render a run diagram whenever the run stops or completes."""
        for fmt in self.settings.get("app.run_diagrams") or []:
            try:
                from ..rundiagram import write
                path = write(self, run_id, fmt)
                self.audit.record("run.diagram", run_id=run_id, outcome="ok", details={"format": fmt, "path": str(path.name)})
            except Exception as exc:  # a diagram must never break a run
                self.audit.record("run.diagram", run_id=run_id, outcome="error", details={"format": fmt, "error": str(exc)})

    def _flush_events(self, run_id: str) -> None:
        for event_type, data in self._deferred.pop(run_id, []):
            self.webhooks.emit(event_type, run_id, data)

    def iso(self) -> str:
        return self.now().isoformat()

    # ===================================================================== public API
    def start_run(self, request: ProcessingRequest, idempotency_key: str | None = None,
                  parent_run_id: str | None = None, attempt: int = 1, human_answers: dict | None = None) -> dict:
        run_id = new_id("run")
        state = {"request": request.model_dump(mode="json"), "budget": {"steps": 0, "tool_calls": 0, "llm_calls": 0, "skill_llm_calls": 0},
                 "actions": [], "notes": [], "attempt": attempt, "human_answers": human_answers or {}}
        created = self.store.create_run(
            run_id=run_id, case_id=request.case_id, request=request.model_dump(mode="json"), state=state, now=self.iso(),
            idempotency_key=idempotency_key, config_manifest=self.settings.public_manifest(),
            index_version=self.retriever.index_version, parent_run_id=parent_run_id, attempt=attempt)
        if not created["created"]:
            self.audit.record("run.start_replayed", run_id=created["run_id"], details={"idempotency_key": idempotency_key})
            return self.get_run(created["run_id"])
        self.audit.record("run.created", run_id=run_id, actor=request.requester_id or "api",
                          details={"case_id": request.case_id, "invoice_ref": request.invoice_ref, "vendor_id": request.vendor_id,
                                   "amount": str(request.amount), "currency": request.currency, "attachments": len(request.attachments),
                                   "parent_run_id": parent_run_id, "index_version": self.retriever.index_version,
                                   "model": {"provider": self.settings.get("llm.provider"), "model": self.llm.model}})
        self.webhooks.emit("run.created", run_id, {"case_id": request.case_id})
        self.store.claim_run(run_id, ("CREATED",), self.iso())
        self._execute(run_id)
        self._auto_diagram(run_id)
        return self.get_run(run_id)

    def resume(self, run_id: str) -> dict:
        """Resume a run that crashed mid-graph (status RUNNING/CREATED). Safe to call repeatedly."""
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] == RunStatus.AWAITING_APPROVAL.value:
            approval = self.store.get_approval_for_run(run_id)
            if approval and approval["status"] in ("APPROVED", "REJECTED"):
                # decision recorded but the run never resumed (crash or race between the two steps): repair it
                next_node = "SUBMIT_DECISION" if approval["status"] == "APPROVED" else "FINALIZE_REJECTED"
                if self.store.claim_run(run_id, (RunStatus.AWAITING_APPROVAL.value,), self.iso()):
                    self.store.checkpoint(run_id, "APPROVAL_GATE", next_node, RunStatus.RUNNING.value, run["state_json"], self.iso())
                    self.audit.record("run.resumed", run_id=run_id, details={"from_node": next_node, "trigger": "repair_resolved_approval"})
                    self._execute(run_id)
                return self.get_run(run_id)
        if run["status"] in TERMINAL or run["status"] == RunStatus.AWAITING_APPROVAL.value:
            self.audit.record("run.resume_noop", run_id=run_id, details={"status": run["status"]})
            return self.get_run(run_id)
        self.audit.record("run.resumed", run_id=run_id, details={"from_node": run["current_node"], "status": run["status"]})
        self.store.claim_run(run_id, ("CREATED", "RUNNING"), self.iso())
        self._execute(run_id)
        self._auto_diagram(run_id)
        return self.get_run(run_id)

    def resume_all(self) -> list[str]:
        ids = [r["run_id"] for r in self.store.list_runs(status="RUNNING")] + \
              [r["run_id"] for r in self.store.list_runs(status="CREATED")] + \
              [a["run_id"] for a in self.store.list_approvals() if a["status"] in ("APPROVED", "REJECTED")
               and (self.store.get_run(a["run_id"]) or {}).get("status") == RunStatus.AWAITING_APPROVAL.value]
        for rid in ids:
            self.resume(rid)
        return ids

    def get_run(self, run_id: str, include_audit: bool = True) -> dict:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        state = run["state_json"]
        out = {
            "run_id": run_id, "case_id": run["case_id"], "status": run["status"], "current_node": run["current_node"],
            "attempt": run["attempt"], "parent_run_id": run["parent_run_id"], "error": run["error"],
            "created_at": run["created_at"], "updated_at": run["updated_at"], "index_version": run["index_version"],
            "budget": state.get("budget"), "result": run["result_json"],
            "approval": self._approval_view(self.store.get_approval_for_run(run_id)),
            "checkpoints": self.store.checkpoints(run_id),
        }
        out["reasoning"] = self.reasoning(run_id, state)
        if out["result"]:
            out["result"]["reasoning"] = out["reasoning"]
        if include_audit:
            out["audit_events"] = self.audit.events(run_id)
        return out

    def reasoning(self, run_id: str, state: dict | None = None) -> list[dict]:
        """Ordered reasoning for every key step: system + skills + model (run trace) and humans (approval decisions,
        refusals with reasons, ignored duplicates - from the audit log, in the order they happened)."""
        if state is None:
            state = (self.store.get_run(run_id) or {}).get("state_json") or {}
        trace = list(state.get("trace", []))
        human, tail = [], []
        for e in self.audit.events(run_id):          # audit is append-only and ordered by sequence
            d = e["details"]
            if e["event_type"] == "approval.decision_recorded":
                human.append({"at": e["ts"], "step": "HUMAN_DECISION", "actor": f"human:{e['actor']}",
                              "decision": f"{e['outcome'].upper()} ({d.get('slot')} slot) -> approval {d.get('status')}",
                              "reason": (d.get("comment") or "(no comment)") + f" - role {d.get('role')}, limit {d.get('limit_aud')}, "
                                        f"register v{d.get('register_version')}, via {d.get('channel')}", "refs": []})
            elif e["event_type"] == "approval.denied":
                human.append({"at": e["ts"], "step": "HUMAN_DECISION", "actor": f"human:{e['actor']}",
                              "decision": f"{d.get('decision')} REFUSED by system", "reason": "; ".join(d.get("reasons", [])), "refs": []})
            elif e["event_type"] in ("approval.callback_replayed", "approval.duplicate_ignored"):
                human.append({"at": e["ts"], "step": "HUMAN_DECISION", "actor": f"human:{e['actor']}",
                              "decision": "duplicate ignored", "reason": "same decision already processed - no effect", "refs": []})
            elif e["event_type"] == "run.completed" and e["outcome"] == "approval_rejected":
                tail.append({"at": e["ts"], "step": "FINALIZE", "actor": "system", "decision": "nothing submitted",
                             "reason": "approver rejected the recommendation; case returns to Accounts Payable", "refs": []})
        gate = max((i for i, t in enumerate(trace) if t["step"] == "APPROVAL_GATE"), default=len(trace) - 1)
        return trace[:gate + 1] + human + trace[gate + 1:] + tail

    def add_evidence(self, run_id: str, attachments: list, note: str | None, po_ref: str | None) -> dict:
        """FIN-POL-007 s5: new evidence resumes the case as a new attempt that re-validates every control
        (including time-sensitive vendor and delegation data); the previous result stays in history."""
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] not in (RunStatus.NEEDS_INFORMATION.value, RunStatus.FAILED.value):
            raise ApprovalError(409, f"run is {run['status']}; evidence can only be added to held or failed runs")
        req = dict(run["request_json"])
        req["attachments"] = list(req.get("attachments", [])) + [a.model_dump(mode="json") for a in attachments]
        if po_ref:
            req["po_ref"] = po_ref
        if note:
            req["notes"] = ((req.get("notes") or "") + f"\n[evidence {self.iso()}] {note}").strip()[:5000]
        new_req = ProcessingRequest.model_validate(req)
        self._claim_resume(run_id)
        self.audit.record("evidence.received", run_id=run_id, details={"attachments": [a.filename for a in attachments],
                                                                        "po_ref": po_ref, "note": bool(note)})
        self.webhooks.emit("evidence.received", run_id, {"case_id": run["case_id"]})
        child = self.start_run(new_req, parent_run_id=run_id, attempt=run["attempt"] + 1,
                               human_answers=(run["state_json"] or {}).get("human_answers"))
        self.store.set_superseded_by(run_id, child["run_id"])
        return child

    def _claim_resume(self, run_id: str) -> None:
        """A held / failed run is resumed at most ONCE: later evidence or reviews go to its newest attempt."""
        if self.store.claim_for_new_attempt(run_id, self.iso()):
            return
        newer = self.store.latest_attempt(run_id)
        if newer is not None or self.store.get_outcome(run_id) is not None:
            self.audit.record("resume.refused", run_id=run_id, outcome="superseded", details={"newer_attempt": newer})
            raise ApprovalError(409, f"run {run_id} was already resumed as {newer}; add evidence or a review to that run",
                                {"error": "superseded", "run_id": run_id, "newer_attempt": newer})

    def review_judgement(self, run_id: str, body) -> dict:
        """A person decides a judgement question the AI could not settle (if_uncertain / not answered). The case is
        re-assessed as a new attempt with that question answered by the reviewer; the reviewer may not approve it."""
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] != RunStatus.NEEDS_INFORMATION.value:
            raise ApprovalError(409, f"run is {run['status']}; only held runs can be reviewed")
        state = run["state_json"] or {}
        proc = (state.get("procedures") or {}).get(body.skill)
        if proc is None:
            raise ApprovalError(422, f"skill {body.skill} asked no questions in this run")
        open_q = set(proc.get("uncertain") or [])
        if proc.get("status") != "ok":                       # questions not answered at all: any of the skill's questions
            from ..skills.pack import load_pack
            open_q |= {q.id for q in load_pack(body.skill).spec.questions}
        if body.question not in open_q:
            raise ApprovalError(422, f"question {body.question} is not open for review (open: {sorted(open_q) or 'none'})")
        res = self.tools.call("get_authority_register", {"as_of": self.now().date().isoformat()},
                              ToolContext(run_id=run_id, case_id=run["case_id"], budget={"tool_calls": 0}))
        if not res.ok:
            raise ApprovalError(503, "authority register unavailable; review not recorded")
        user = next((u for u in res.output.model_dump(mode="json")["approvers"]
                     if u["user_id"] == body.reviewer_id and u.get("active", True)), None)
        roles = self.settings.get("skills.review.roles") or ["Financial Controller", "Financial Control Analyst"]
        if user is None or user.get("role") not in roles:
            self.audit.record("review.denied", run_id=run_id, actor=body.reviewer_id, outcome="forbidden",
                              details={"skill": body.skill, "question": body.question, "allowed_roles": roles})
            raise ApprovalError(403, f"reviewer must be an active {' or '.join(roles)}")
        if body.reviewer_id == (run["request_json"] or {}).get("requester_id"):
            raise ApprovalError(403, "segregation of duties: the requester cannot review their own case (FIN-POL-001 s4)")
        human = {k: dict(v) for k, v in (state.get("human_answers") or {}).items()}
        human.setdefault(body.skill, {})[body.question] = {"decision": body.decision, "reviewer_id": body.reviewer_id,
                                                          "role": user["role"], "reason": body.reason, "at": self.iso()}
        self._claim_resume(run_id)
        self.audit.record("review.recorded", run_id=run_id, actor=body.reviewer_id,
                          details={"skill": body.skill, "question": body.question, "decision": body.decision,
                                   "reason": body.reason[:300]})
        child = self.start_run(ProcessingRequest.model_validate(run["request_json"]), parent_run_id=run_id,
                               attempt=run["attempt"] + 1, human_answers=human)
        self.store.set_superseded_by(run_id, child["run_id"])
        return child

    # ===================================================================== executor
    def _execute(self, run_id: str) -> None:
        max_steps = int(self.settings.get("agent.max_steps", 40))
        crash_after = os.environ.get("FINAGENT_CRASH_AFTER_NODE") or self.settings.get("app.crash_after_node")
        while True:
            run = self.store.get_run(run_id)
            node, state = run["current_node"], run["state_json"]
            if node is None or run["status"] in TERMINAL or run["status"] == RunStatus.AWAITING_APPROVAL.value:
                return
            state["budget"]["steps"] += 1
            if state["budget"]["steps"] > max_steps:
                self._fail(run_id, state, node, f"step budget {max_steps} exceeded")
                return
            t0 = time.perf_counter()
            try:
                next_node, status = self.nodes[node](run_id, state)
            except (BudgetExceeded, ToolBudgetExceeded) as exc:
                self._fail(run_id, state, node, f"budget exceeded: {exc}")
                return
            except ToolPermissionDenied as exc:
                self._fail(run_id, state, node, f"permission denied: {exc}")
                return
            except SimulatedCrash:
                raise
            except Exception as exc:  # any unexpected failure becomes an explicit FAILED state
                self._fail(run_id, state, node, f"{type(exc).__name__}: {exc}")
                return
            self.audit.record("node.completed", run_id=run_id, node=node, outcome=status,
                              duration_ms=int((time.perf_counter() - t0) * 1000), details={"next_node": next_node})
            self.store.checkpoint(run_id, node, next_node, status, state, self.iso(), result=state.get("result"))
            self._flush_events(run_id)
            if crash_after and crash_after == node and node not in state.setdefault("_crashed_after", []):
                state["_crashed_after"].append(node)
                self.store.checkpoint(run_id, node, next_node, status, state, self.iso())
                self.audit.record("run.simulated_crash", run_id=run_id, node=node, outcome="crash")
                raise SimulatedCrash(f"simulated crash after {node}")
            if status != RunStatus.RUNNING.value:
                return

    def _fail(self, run_id: str, state: dict, node: str, reason: str) -> None:
        self._deferred.pop(run_id, None)
        result = state.get("result") or FinalResult(run_id=run_id, case_id=state["request"]["case_id"],
                                                    status=RunStatus.FAILED).model_dump(mode="json")
        result["status"] = RunStatus.FAILED.value
        result.setdefault("unknowns", []).append({"item": f"Run failed at {node}", "impact": reason,
                                                  "resolution": "Investigate and re-run; no financial decision was submitted"})
        state["result"] = result
        self.store.checkpoint(run_id, node, None, RunStatus.FAILED.value, state, self.iso(), result=result, error=reason)
        self.audit.record("run.failed", run_id=run_id, node=node, outcome="failed", details={"reason": reason})
        self.store.upsert_outcome(run_id=run_id, case_id=state["request"]["case_id"], outcome=None, pool_status="FAILED",
                                  primary_exception=None, amount=state["request"]["amount"], currency=state["request"]["currency"],
                                  summary=reason, payload={"error": reason}, now=self.iso())
        self.webhooks.emit("run.failed", run_id, {"case_id": state["request"]["case_id"], "reason": reason, "node": node})

    def _ctx(self, run_id: str, state: dict, authorization: dict | None = None) -> ToolContext:
        return ToolContext(run_id=run_id, case_id=state["request"]["case_id"], budget=state["budget"],
                           authorization=authorization, extras={"now": self.now})

    def _tool(self, run_id: str, state: dict, name: str, args: dict, authorization: dict | None = None):
        res = self.tools.call(name, args, self._ctx(run_id, state, authorization))
        state["actions"].append({"action": "tool_call", "tool": name, "at": self.iso(),
                                 "detail": {"ok": res.ok, "attempts": res.attempts, "duration_ms": res.duration_ms,
                                            "evidence_id": res.evidence_id, "error": res.error, "error_type": res.error_type}})
        return res

    # ===================================================================== nodes
    def n_receive(self, run_id, state):
        req = state["request"]
        state["notes"].append("Case text, notes and attachments are treated as untrusted data.")
        self.audit.record("node.receive", run_id=run_id, node="RECEIVE",
                          details={"case_id": req["case_id"], "document_type": req["document_type"]})
        return "PARSE_INPUTS", RunStatus.RUNNING.value

    def n_parse(self, run_id, state):
        docs = []
        for a in state["request"].get("attachments", []):
            t0 = time.perf_counter()
            d = parse_attachment(a)   # registry-routed, validated NormalizedDocument (finagent.document/1)
            docs.append(d)
            self.audit.record("skill.parser", run_id=run_id, node="PARSE_INPUTS", tool=d["parser"],
                              duration_ms=int((time.perf_counter() - t0) * 1000), outcome="ok",
                              details={"file": a["filename"], "doc_kind": d["doc_kind"], "sha256": d["content_sha256"],
                                       "parser_version": d["parser_version"], "schema": d["schema"],
                                       "lines": len(d["lines"]), "warnings": d["parse_warnings"]})
        for d in docs:
            self._trace(state, "PARSE_INPUTS", f"skill:{d['parser']}", f"read as {d['doc_kind']}",
                        f"{d['source_filename']}: {len(d.get('lines') or [])} line(s), total {d.get('total')} {d.get('currency') or ''}".strip()
                        + (f"; warnings: {'; '.join(d['parse_warnings'])}" if d.get("parse_warnings") else ""), [f"doc:{d['source_filename']}"])
        primary = next((d for d in docs if d["doc_kind"] in ("invoice", "credit_note")), None)
        if primary is None:
            primary = next((d for d in docs if d["doc_kind"] == "email" and d.get("invoice_number")), None)
        state["docs"] = docs
        state["invoice"] = primary
        self._trace(state, "PARSE_INPUTS", "system", "primary document selected" if primary else "no invoice document",
                    f"using {primary['source_filename']} ({primary['doc_kind']})" if primary else
                    "no attachment parsed as an invoice or credit note - minimum-evidence control will hold the case")
        return "RETRIEVE_POLICY", RunStatus.RUNNING.value

    def _planned_skills(self, state) -> list[str]:
        """Skills to execute for this case, from config/workflow.yaml (order preserved)."""
        return [st.skill for st in self.pipeline.plan(state, self.settings)]

    def _retrieve(self, run_id, state, queries: list[str], reason: str):
        bucket = state.setdefault("retrieval", {"queries": [], "chunks": {}})
        for qtext in queries:
            res = self._tool(run_id, state, "retrieve_finance_documents",
                             {"query": qtext, "top_k": int(self.settings.get("rag.top_k", 5)),
                              "caller_role": self.settings.get("rag.caller_role", "ap_agent")})
            entry = {"query": qtext, "reason": reason, "ok": res.ok, "error": res.error, "returned": []}
            if res.ok:
                bucket["index_version"] = res.output.index_version
                for c in res.output.chunks:
                    cd = c.model_dump(mode="json")
                    entry["returned"].append({"chunk_id": c.chunk_id, "rank": c.rank, "score": c.score, "trust": c.trust.value})
                    prev = bucket["chunks"].get(c.chunk_id)
                    if not prev or prev["score"] < c.score:
                        bucket["chunks"][c.chunk_id] = cd
            bucket["queries"].append(entry)

    def n_retrieve(self, run_id, state):
        names = self._planned_skills(state)
        state["planned_skills"] = names
        state["workflow"] = {"name": self.pipeline.name, "version": self.pipeline.version, "digest": self.pipeline.digest,
                             "planned_skills": names}
        self.audit.record("workflow.planned", run_id=run_id, node="RETRIEVE_POLICY", details=state["workflow"])
        self._policy_snapshot(run_id, state)
        from ..workflow import CONDITIONS
        for st in self.pipeline.steps:
            if not st.enabled:
                self._trace(state, "PLAN", "system", f"skip {st.skill}", "disabled in config/workflow.yaml")
            elif st.skill in names:
                self._trace(state, "PLAN", "system", f"run {st.skill}",
                            "always runs" if st.when == "always" else f"condition '{st.when}' is true for this case")
            else:
                self._trace(state, "PLAN", "system", f"skip {st.skill}", f"condition '{st.when}' is false for this case")
        queries = []
        for n in [st.skill for st in self.pipeline.retrieval_plan(state, self.settings)]:
            for qtext in REGISTRY[n].retrieval_queries:
                if qtext not in queries:
                    queries.append(qtext)
        notes = state["request"].get("notes")
        if notes:  # case text is used as a search query only; results are trust-labelled
            queries.append(re.sub(r"\s+", " ", notes)[:200])
        self._retrieve(run_id, state, queries, "planned")
        ch = state["retrieval"]["chunks"]
        self._trace(state, "RETRIEVE_POLICY", "system", f"{len(ch)} policy chunks retrieved",
                    f"{len(queries)} queries from the planned skills{' + case notes' if notes else ''}; "
                    f"{sum(c['trust'] != 'authoritative' for c in ch.values())} non-authoritative chunk(s) labelled and excluded as authority")
        if not state["retrieval"]["chunks"]:
            raise RuntimeError("retrieval returned no policy evidence")
        return "GATHER_EVIDENCE", RunStatus.RUNNING.value

    def _policy_snapshot(self, run_id, state) -> None:
        """Record the policy numbers this run uses and whether the policy text still matches the verified baseline.
        Policy changes are maintenance releases, never live: a mismatch holds the case at DECIDE."""
        if self.policy is None:
            state["policy"] = {"params": {}, "unverified": [], "reviewed_at": None}
            return
        st = self.policy.status()
        state["policy"] = {"params": self.policy.params, "unverified": st["unverified_sections"],
                           "reviewed_by": st["baseline_reviewed_by"], "reviewed_at": st["baseline_reviewed_at"]}
        if st["unverified_sections"]:
            self._trace(state, "PLAN", "system", "policy differs from verified baseline",
                        f"{len(st['unverified_sections'])} section(s) changed since the controls were verified; "
                        "the case will be held until policy maintenance is done",
                        [c["section"] for c in st["unverified_sections"]][:8])

    def n_gather(self, run_id, state):
        req, inv = state["request"], state.get("invoice") or {}
        gp = {"params": (state.get("policy") or {}).get("params")}
        ev: dict[str, Any] = {"errors": {}}
        r = self._tool(run_id, state, "get_vendor_record", {"vendor_id": req["vendor_id"]})
        ev["vendor"] = r.output.model_dump(mode="json") if r.ok else None
        if not r.ok:
            ev["errors"]["vendor"] = f"{r.error_type}: {r.error} (after {r.attempts} attempt(s))"
        po_ref = req.get("po_ref") or inv.get("po_ref")
        if po_ref and "three_way_match" in state["planned_skills"]:   # PO only fetched when needed
            r = self._tool(run_id, state, "get_purchase_order", {"po_ref": po_ref})
            ev["po"] = r.output.model_dump(mode="json") if r.ok else None
            if not r.ok:
                ev["errors"]["po"] = f"{r.error_type}: {r.error} (after {r.attempts} attempt(s))"
        r = self._tool(run_id, state, "check_invoice_history", {
            "vendor_id": req["vendor_id"], "invoice_ref": inv.get("invoice_number") or req["invoice_ref"],
            "amount": inv.get("total") or req["amount"], "currency": inv.get("currency") or req["currency"],
            "invoice_date": inv.get("invoice_date"), "po_ref": po_ref, "attachment_hash": inv.get("content_sha256"),
            "exclude_case_id": req["case_id"],
            "fuzzy_amount_pct": str(P(gp, "duplicates.fuzzy_amount_pct")),
            "fuzzy_date_window_days": P(gp, "duplicates.fuzzy_date_window_days"),
            "non_po_window_days": P(gp, "non_po.repeat_window_days")})
        ev["history"] = [m.model_dump(mode="json") for m in r.output.matches] if r.ok else None
        if not r.ok:
            ev["errors"]["history"] = f"{r.error_type}: {r.error}"
        if "credit_note_validation" in state["planned_skills"] and inv.get("original_invoice_ref"):
            r = self._tool(run_id, state, "check_invoice_history", {
                "vendor_id": req["vendor_id"], "invoice_ref": inv["original_invoice_ref"], "amount": inv.get("total") or req["amount"],
                "currency": inv.get("currency") or req["currency"], "lookup_mode": "reference"})
            ev["original_invoice_records"] = [m.model_dump(mode="json") for m in r.output.matches] if r.ok else []
        foreign = (inv.get("currency") or req["currency"]) != self.settings.get("agent.base_currency", "AUD")
        if "fx_validation" in state["planned_skills"] and foreign:     # FX only when the invoice is not in AUD
            r = self._tool(run_id, state, "get_fx_rate", {"from_currency": inv.get("currency") or req["currency"],
                                                          "to_currency": "AUD",
                                                          "rate_date": inv.get("invoice_date") or self.now().date().isoformat()})
            ev["fx"] = r.output.model_dump(mode="json") if r.ok else None
            if not r.ok:
                ev["errors"]["fx"] = f"{r.error_type}: {r.error}"
        r = self._tool(run_id, state, "get_authority_register", {"as_of": self.now().date().isoformat()})
        ev["authority"] = r.output.model_dump(mode="json") if r.ok else None
        if not r.ok:
            ev["errors"]["authority"] = f"{r.error_type}: {r.error}"
        state["evidence"] = ev
        why = {
            "vendor master": "always needed (FIN-POL-001 s5 confirm vendor status)",
            "purchase order": (f"invoice references {po_ref}" if po_ref and "three_way_match" in state["planned_skills"]
                               else "skipped - no PO reference (non-PO or credit-note path)"),
            "invoice history": "always needed (FIN-POL-005 duplicate check)",
            "FX rate": ("invoice is in a foreign currency" if "fx_validation" in state["planned_skills"] and foreign
                        else "skipped - invoice is in AUD"),
            "authority register": "always needed (FIN-POL-003 approval limits)",
        }
        errs = {"vendor master": "vendor", "purchase order": "po", "invoice history": "history", "FX rate": "fx",
                "authority register": "authority"}
        for name, reason in why.items():
            err = ev["errors"].get(errs[name])
            self._trace(state, "GATHER_EVIDENCE", "system", f"{name}: {'FAILED' if err else ('skipped' if reason.startswith('skipped') else 'retrieved')}",
                        reason + (f"; error: {err}" if err else ""))
        return "RECONCILE", RunStatus.RUNNING.value

    def evidence_bundle(self, state) -> dict:
        ev = state.get("evidence", {})
        return {
            "request": state["request"], "invoice": state.get("invoice"), "docs": state.get("docs", []),
            "vendor": ev.get("vendor"), "vendor_error": ev.get("errors", {}).get("vendor"),
            "po": ev.get("po"), "po_error": ev.get("errors", {}).get("po"),
            "history": ev.get("history"), "history_error": ev.get("errors", {}).get("history"),
            "original_invoice_records": ev.get("original_invoice_records", []),
            "fx": ev.get("fx"), "fx_error": ev.get("errors", {}).get("fx"),
            "authority": ev.get("authority"), "authority_error": ev.get("errors", {}).get("authority"),
            "now": self.now(), "base_currency": self.settings.get("agent.base_currency", "AUD"),
            "gst_rate": self.settings.get("agent.gst_rate", "0.10"), "northstar_entities": NORTHSTAR_ENTITIES,
            "params": (state.get("policy") or {}).get("params") or {},
        }

    def run_skills(self, state, procedure=None) -> list[SkillOutput]:
        """Execute the planned pipeline in order; each skill sees the outputs of the skills before it.
        ``procedure(name, out, ev, prior)`` runs the skill's LLM procedure on top of its deterministic script result."""
        ev = self.evidence_bundle(state)
        scripts: list[SkillOutput] = []      # what scripts see: ONLY other scripts' results -> core flow is model-independent
        outputs: list[SkillOutput] = []      # merged results (script + answers) -> what DECIDE sees
        for name in state["planned_skills"]:
            out = REGISTRY[name].func(ev, scripts)            # step 0: deterministic reconciliation script
            scripts.append(copy.deepcopy(out))
            if procedure is not None:
                out = procedure(name, out, ev, outputs)
            outputs.append(out)
        return outputs

    def _skill_procedure(self, run_id, state):
        """Closure used at RECONCILE: code asks the skill's fixed questions, the model answers, code decides."""
        from ..skills.llm_runner import count_indicators, merge
        from ..skills.pack import SkillPackError, load_pack

        def procedure(name, out, ev, prior):
            if not self.settings.get("skills.llm.enabled", True) or self.skill_runner is None:
                return out                    # kill switch: the deterministic scripts decide alone
            if name == "fraud_indicator_scoring":
                return count_indicators(out, ev, prior)   # code counts indicators from scripts AND answers
            try:
                pack = load_pack(name)
            except SkillPackError as exc:
                self.audit.record("skill.procedure", run_id=run_id, node="RECONCILE", tool=name, outcome="no_pack",
                                  details={"error": str(exc)[:300]})
                return out
            if pack.spec.execution != "code+llm":
                return out
            answers, questions, rec = self.skill_runner.run(pack, ev, run_id, state["budget"], attempt=state.get("attempt", 1),
                                                            human=(state.get("human_answers") or {}).get(name))
            merged = merge(out, pack, answers, questions, rec)
            if rec.status == "skipped":
                return merged
            self.audit.record("skill.procedure", run_id=run_id, node="RECONCILE", tool=name, outcome=rec.status,
                              duration_ms=rec.duration_ms,
                              details={"verdict": rec.verdict, "decided_by": rec.decided_by, "calls": rec.calls,
                                       "certainty": rec.certainty, "disagreed": rec.disagreed, "uncertain": rec.uncertain,
                                       "qa_sample": rec.qa_sample,
                                       "models": [t["model"] for t in rec.tiers], "version": pack.spec.version,
                                       "errors": rec.errors[:3]})
            reason = "; ".join(f"{q.finding}: {answers[q.id].answer}" for q in questions) if answers else None
            state.setdefault("procedures", {})[name] = {"status": rec.status, "verdict": rec.verdict, "calls": rec.calls,
                                                        "decided_by": rec.decided_by, "tiers": rec.tiers, "reason": reason,
                                                        "certainty": rec.certainty, "disagreed": rec.disagreed,
                                                        "uncertain": rec.uncertain, "qa_sample": rec.qa_sample}
            if answers is not None and (rec.verdict != "NO_CHANGE" or rec.uncertain or rec.disagreed):
                self._trace(state, "RECONCILE", f"skill:{name} (questions)", f"{rec.verdict} (answered by {rec.decided_by} model"
                            + (f"; uncertain: {', '.join(rec.uncertain)}" if rec.uncertain else "")
                            + (f"; models disagreed: {', '.join(rec.disagreed)}" if rec.disagreed else "") + ")",
                            reason, [f"ref:{name}/{q.reference}" for q in questions if q.reference][:6])
            elif answers is None:
                self._trace(state, "RECONCILE", f"skill:{name} (questions)", f"NOT ANSWERED ({rec.status}) -> hold",
                            "; ".join(rec.errors[:2])[:240])
            return merged
        return procedure

    def n_reconcile(self, run_id, state):
        outputs = self.run_skills(state, self._skill_procedure(run_id, state))
        for o in outputs:
            self.audit.record("skill.processor", run_id=run_id, node="RECONCILE", tool=o.skill, outcome="ok",
                              details={"findings": [(f.control, f.status) for f in o.findings],
                                       "exceptions": [e.category for e in o.exceptions],
                                       "votes": [v.outcome.value for v in o.votes],
                                       "compliance_refs": [f"{d} {s}" for d, s in REGISTRY[o.skill].compliance_refs]
                                       if o.skill in REGISTRY else []})
        state["skills"] = [to_jsonable({
            "skill": o.skill, "findings": [f.__dict__ for f in o.findings], "calculations": o.calculations,
            "exceptions": [e.__dict__ for e in o.exceptions], "facts": o.facts, "unknowns": o.unknowns,
            "risk_indicators": o.risk_indicators, "votes": [{"outcome": v.outcome.value, "reason": v.reason, "skill": v.skill} for v in o.votes],
            "approvals": o.approvals, "data": o.data}) for o in outputs]
        for o in state["skills"]:
            fails = [f for f in o["findings"] if f["status"] in ("FAIL", "UNKNOWN")]
            if o["votes"]:
                decision = "votes " + ", ".join(sorted({v["outcome"] for v in o["votes"]}))
                reason = "; ".join(v["reason"] for v in o["votes"])
            elif fails:
                decision, reason = "flags (no vote)", "; ".join(f["detail"][:160] for f in fails[:3])
            else:
                decision = "pass"
                produced = {k: v for k, v in o["data"].items() if k in ("amount_aud", "proposed_payment_date", "required_role")}
                reason = ("; ".join(f["detail"][:140] for f in o["findings"][:2])
                          or "; ".join(f"{k} = {v}" for k, v in produced.items()) or "nothing to check for this case")
            refs = sorted({r for f in (fails or o["findings"]) for r in [f["rule"]]})[:6]
            self._trace(state, "RECONCILE", f"skill:{o['skill']}", decision, reason, refs)
        votes = [v for o in state["skills"] for v in o["votes"]]
        outcome = max((Outcome(v["outcome"]) for v in votes), key=lambda o: OUTCOME_PRECEDENCE[o],
                      default=Outcome.APPROVE_FOR_POSTING)
        state["rules_outcome"] = outcome.value
        voters = sorted({v["skill"] for v in votes if v["outcome"] == outcome.value})
        self._trace(state, "RECONCILE", "system", f"rules outcome {outcome.value}",
                    f"most conservative vote, from {', '.join(voters)}" if voters else "no control voted against approval")
        return "DRAFT", RunStatus.RUNNING.value

    # ---------------------------------------------------------------- model step
    def _rules_payload(self, state) -> dict:
        sk = state["skills"]
        exceptions = [e for o in sk for e in o["exceptions"]]
        primary = self._primary_exception(state, Outcome(state["rules_outcome"]))
        chunks = state["retrieval"]["chunks"]
        evidence_ids = sorted({e for o in sk for f in o["findings"] for e in f["evidence"]} |
                              {e for o in sk for f in o["facts"] for e in f["evidence"]})
        return {
            "case": {k: state["request"][k] for k in ("case_id", "invoice_ref", "vendor_id", "amount", "currency", "po_ref", "document_type")},
            "rules_assessment": {
                "outcome": state["rules_outcome"], "primary_exception": primary,
                "exceptions": [{k: e[k] for k in ("category", "failed_rule", "expected", "observed", "owner")} for e in exceptions],
                "findings": [{k: f[k] for k in ("control", "rule", "status", "detail")} for o in sk for f in o["findings"]],
                "calculations": [{k: c[k] for k in ("name", "result", "threshold", "passed")} for o in sk for c in o["calculations"]],
                "unknowns": [u for o in sk for u in o["unknowns"]],
                "risk_indicators": [{"indicator": r["indicator"], "source": r["source"]} for o in sk for r in o["risk_indicators"]],
                "notes": [o["data"]["inference"] for o in sk if o["data"].get("inference")],
            },
            "allowed_source_ids": sorted(chunks.keys()) + evidence_ids,
        }

    def n_draft(self, run_id, state):
        max_calls = int(self.settings.get("agent.max_llm_calls", 6))
        retries = int(self.settings.get("llm.max_validation_retries", 2))
        rounds_left = int(self.settings.get("agent.max_extra_retrieval_rounds", 1)) - state.get("extra_rounds", 0)
        payload = self._rules_payload(state)
        chunks = sorted(state["retrieval"]["chunks"].values(), key=lambda c: -c["score"])[:16]
        forbidden = {cid for cid, c in state["retrieval"]["chunks"].items() if c["trust"] != TrustLevel.AUTHORITATIVE.value}
        allowed = set(payload["allowed_source_ids"])
        untrusted = [{"source": d["source_filename"], "text": d.get("text", "")} for d in state.get("docs", [])]
        if state["request"].get("notes"):
            untrusted.append({"source": "request.notes", "text": state["request"]["notes"]})
        errors: list[str] | None = None
        draft, attempts_log = None, []
        for attempt in range(1, retries + 2):
            state["budget"]["llm_calls"] += 1
            if state["budget"]["llm_calls"] > max_calls:
                raise BudgetExceeded(f"llm-call budget {max_calls} exhausted")
            user = build_user_prompt(payload, chunks, untrusted, errors)
            t0 = time.perf_counter()
            try:
                resp = self.llm.complete(SYSTEM_PROMPT, user, {"run_id": run_id, "case_id": state["request"]["case_id"],
                                                               "attempt": attempt})
            except LLMError as exc:
                errors = [f"provider error: {exc}"]
                self.audit.record("model.call", run_id=run_id, node="DRAFT", outcome="provider_error", attempt=attempt,
                                  duration_ms=int((time.perf_counter() - t0) * 1000), details={"error": str(exc)})
                attempts_log.append({"attempt": attempt, "valid": False, "errors": errors})
                continue
            draft, errors = validate_draft(resp.text, allowed, forbidden)
            self.audit.record("model.call", run_id=run_id, node="DRAFT", outcome="valid" if draft else "invalid_output",
                              attempt=attempt, duration_ms=resp.latency_ms,
                              details={"provider": resp.provider, "model": resp.model, "request_id": resp.request_id,
                                       "usage": resp.usage, "errors": errors, "prompt_chars": len(user)})
            attempts_log.append({"attempt": attempt, "valid": bool(draft), "errors": errors, "provider": resp.provider,
                                 "model": resp.model, "request_id": resp.request_id, "usage": resp.usage})
            if draft:
                break
        state["model"] = {"provider": self.settings.get("llm.provider"), "model": self.llm.model, "attempts": attempts_log,
                          "status": "VALID" if draft else "FAILED_VALIDATION"}
        if draft is not None:
            self._trace(state, "DRAFT", "model", f"proposes {draft.proposed_outcome.value} (confidence {draft.confidence})",
                        " | ".join(draft.rationale[:4]) + (f" | risks: {'; '.join(draft.risk_notes[:3])}" if draft.risk_notes else ""),
                        draft.cited_source_ids[:6])
        else:
            self._trace(state, "DRAFT", "model", "output rejected", f"invalid after {len(attempts_log)} attempt(s): "
                        + "; ".join((errors or [])[:3]) + " - deterministic narrative used")
        if draft is None:
            if self.settings.get("llm.on_invalid_output", "fallback") == "fail":
                raise RuntimeError(f"model output invalid after {len(attempts_log)} attempt(s): {errors}")
            self.audit.record("model.fallback", run_id=run_id, node="DRAFT", outcome="deterministic_narrative",
                              details={"errors": errors})
            state["draft"] = None
            return "DECIDE", RunStatus.RUNNING.value
        if draft.additional_queries and rounds_left > 0:   # bounded agentic loop: one extra retrieval round
            state["extra_rounds"] = state.get("extra_rounds", 0) + 1
            self._retrieve(run_id, state, draft.additional_queries[:2], "model_requested")
            return "DRAFT", RunStatus.RUNNING.value
        state["draft"] = draft.model_dump(mode="json")
        return "DECIDE", RunStatus.RUNNING.value

    # ---------------------------------------------------------------- decision
    def _primary_exception(self, state, outcome: Outcome) -> str | None:
        winning = [v["skill"] for o in state["skills"] for v in o["votes"] if v["outcome"] == outcome.value]
        for o in state["skills"]:
            if o["skill"] in winning and o["exceptions"]:
                return o["exceptions"][0]["category"]
        allx = [e for o in state["skills"] for e in o["exceptions"]]
        return allx[0]["category"] if allx else None

    def _policy_drift(self, state: dict, cited: set[str]) -> list[tuple[str, str]]:
        """Re-verify cited policy sections against the CURRENT index: still present, identical text, still current.
        Returns [(chunk_id, problem)] - empty when every cited section is unchanged."""
        used = state.get("retrieval", {}).get("chunks", {})
        problems = []
        for cid in sorted(cited):
            now = self.retriever.current_chunk(cid)
            if now is None:
                problems.append((cid, "removed from the policy library"))
            elif now["text"] != (used.get(cid) or {}).get("text"):
                problems.append((cid, "text changed"))
            elif now["trust"] != TrustLevel.AUTHORITATIVE.value:
                problems.append((cid, f"no longer current (now {now['status']})"))
        return problems

    def _cited_policy_ids(self, result: dict) -> set[str]:
        return {c["source_id"] for group in ("policy_findings", "exceptions") for f in result.get(group, [])
                for c in f.get("citations", []) if c.get("source_type") == "policy"}

    def _cite_policy(self, state, refs) -> tuple[list[Citation], list[tuple[str, str]]]:
        chunks = state["retrieval"]["chunks"]
        cites, missing = [], []
        for doc_id, sec in refs:
            hit = next((c for cid, c in chunks.items() if c["document_id"] == doc_id and
                        cid.split("#")[1].split(".")[0] == sec and c["trust"] == TrustLevel.AUTHORITATIVE.value), None)
            if hit:
                cites.append(Citation(source_id=hit["chunk_id"], source_type="policy", document_id=doc_id, version=hit["version"],
                                      section=hit["section"], trust=TrustLevel.AUTHORITATIVE, quote=hit["text"][:240]))
            else:
                missing.append((doc_id, sec))
        return cites, missing

    def _cite_evidence(self, ids: list[str]) -> list[Citation]:
        out = []
        for e in ids:
            kind = _evidence_source_type(e)
            out.append(Citation(source_id=e, source_type=kind,
                                trust=TrustLevel.UNTRUSTED if kind == "case_document" else TrustLevel.AUTHORITATIVE))
        return out

    def n_decide(self, run_id, state):
        req = state["request"]
        sk = state["skills"]
        outcome = Outcome(state["rules_outcome"])
        inferences: list[Inference] = [Inference(statement=n, basis="rule", produced_by="rules") for n in state.get("notes", [])]
        for o in sk:
            if o["data"].get("inference"):
                inferences.append(Inference(statement=o["data"]["inference"], basis=o["skill"], produced_by="rules"))
        unknowns = [Unknown(**u) for o in sk for u in o["unknowns"]]
        findings, ungrounded = [], []
        for o in sk:
            for f in o["findings"]:
                cites, missing = self._cite_policy(state, [tuple(r) for r in f["policy_refs"]])
                if f["status"] != "NOT_APPLICABLE":      # a check that did not apply did not shape the decision
                    ungrounded += [(f["rule"], m) for m in missing]
                findings.append(PolicyFinding(control=f"{o['skill']}.{f['control']}", rule=f["rule"], status=FindingStatus(f["status"]),
                                              detail=f["detail"], citations=cites + self._cite_evidence(f["evidence"])))
        next_review = add_business_days(self.now().date(), 3).isoformat()
        exceptions = []
        for o in sk:
            for e in o["exceptions"]:
                cites, missing = self._cite_policy(state, [tuple(r) for r in e["policy_refs"]])
                ungrounded += [(e["failed_rule"], m) for m in missing]
                exceptions.append(ExceptionRecord(category=e["category"], failed_rule=e["failed_rule"], expected=e["expected"],
                                                  observed=e["observed"], citations=cites + self._cite_evidence(e["evidence"]),
                                                  owner=e["owner"], next_review_date=next_review))
        # grounding gate: a decision may not rest on policy we did not retrieve
        if ungrounded:
            uniq = sorted({f"{d} {s}" for _, (d, s) in ungrounded})
            unknowns.append(Unknown(item="Policy evidence not retrieved: " + ", ".join(uniq),
                                    impact="Findings depending on these sections are not grounded in retrieved sources",
                                    resolution="Re-index corpus / refine retrieval queries"))
            if outcome == Outcome.APPROVE_FOR_POSTING:
                outcome = Outcome.HOLD_FOR_INFORMATION
                inferences.append(Inference(statement="Approval downgraded to hold: governing policy not retrieved.",
                                            basis="grounding gate", produced_by="rules"))
            self.audit.record("grounding.gap", run_id=run_id, node="DECIDE", outcome="ungrounded", details={"sections": uniq})
        # freshness gate: the policy text relied on must still be the CURRENT text right now (it may have been edited or
        # deleted while this run was in progress, e.g. between retrieval and decision or across a crash/resume)
        cited_now = {c.source_id for f in findings + exceptions for c in f.citations if c.source_type == "policy"}
        drift = self._policy_drift(state, cited_now)
        if drift:
            desc = "; ".join(f"{c} {p}" for c, p in drift)
            unknowns.append(Unknown(item=f"Governing policy changed during this run: {desc}",
                                    impact="Findings were assessed against policy text that is no longer current",
                                    resolution="Re-evaluate the case under the current policy (POST /runs/{id}/evidence)"))
            if outcome == Outcome.APPROVE_FOR_POSTING:
                outcome = Outcome.HOLD_FOR_INFORMATION
                inferences.append(Inference(statement="Approval downgraded to hold: governing policy changed during the run.",
                                            basis="grounding gate", produced_by="rules"))
            self.audit.record("policy.changed_during_run", run_id=run_id, node="DECIDE", outcome="drift",
                              details={"sections": [f"{c}: {p}" for c, p in drift]})
            self._trace(state, "DECIDE", "system", "policy changed during run", desc)

        # policy gate: policy changes are maintenance releases. If the policy text differs from the baseline the controls
        # were verified against, nothing is approved until maintenance is done (docs/governance.md).
        unverified = (state.get("policy") or {}).get("unverified") or []
        if unverified:
            desc = ", ".join(f"{c['section']} ({c['change']})" for c in unverified)[:300]
            unknowns.append(Unknown(item=f"Policy text differs from the verified baseline: {desc}",
                                    impact="The controls may not reflect the current policy",
                                    resolution="Policy maintenance: update the controls, test, re-baseline "
                                               "(finagent policy baseline), restart; then POST /runs/{id}/evidence"))
            exceptions.append(ExceptionRecord(category="OTHER_CONTROL_RISK", failed_rule="Controls verified against current policy",
                                              expected="policy text matches config/policy_baseline.yaml", observed=desc,
                                              owner="Financial Control", next_review_date=next_review))
            if outcome == Outcome.APPROVE_FOR_POSTING:
                outcome = Outcome.HOLD_FOR_INFORMATION
                inferences.append(Inference(statement="Approval downgraded to hold: policy changed and maintenance is not done.",
                                            basis="policy gate", produced_by="rules"))
            self.audit.record("policy.unverified", run_id=run_id, node="DECIDE", outcome="held",
                              details={"sections": [c["section"] for c in unverified]})
            self._trace(state, "DECIDE", "system", "policy maintenance required", desc)

        # model contribution: may only make the outcome more conservative
        draft = state.get("draft")
        model_conf = None
        if draft:
            proposed = Outcome(draft["proposed_outcome"])
            model_conf = float(draft["confidence"])
            for s in draft.get("inferences", []):
                inferences.append(Inference(statement=s, basis="model narrative over cited evidence", produced_by="model"))
            for u in draft.get("unknowns", []):
                if not any(u.lower() in x.item.lower() for x in unknowns):
                    unknowns.append(Unknown(item=u, impact="Raised by model review", resolution="Reviewer to confirm"))
            if proposed != outcome:
                allowed_escalation = (outcome == Outcome.APPROVE_FOR_POSTING and proposed in
                                      (Outcome.HOLD_FOR_INFORMATION, Outcome.ESCALATE_CONTROL_REVIEW)) or \
                                     (outcome == Outcome.HOLD_FOR_INFORMATION and proposed == Outcome.ESCALATE_CONTROL_REVIEW)
                if allowed_escalation and self.settings.get("llm.allow_model_escalation", True):
                    self.audit.record("model.escalation_adopted", run_id=run_id, node="DECIDE",
                                      details={"rules": outcome.value, "model": proposed.value})
                    inferences.append(Inference(statement=f"Model review escalated {outcome.value} -> {proposed.value}: "
                                                          + "; ".join(draft.get("risk_notes", [])[:3]),
                                                basis="model is allowed to be more conservative", produced_by="model"))
                    exceptions.append(ExceptionRecord(category="OTHER_CONTROL_RISK", failed_rule="Model review concern",
                                                      expected="No unresolved concern", observed="; ".join(draft["rationale"][:2]),
                                                      owner="Accounts Payable", next_review_date=next_review))
                    outcome = proposed
                else:
                    self.audit.record("model.override_blocked", run_id=run_id, node="DECIDE", outcome="blocked",
                                      details={"rules": outcome.value, "model": proposed.value})
                    inferences.append(Inference(statement=f"Model proposed {proposed.value}; blocked by policy guard "
                                                          f"(model may not relax or invent {outcome.value}).",
                                                basis="policy guard", produced_by="rules"))
                    model_conf = min(model_conf, 0.5)

        # confidence: deterministic, explainable
        conf = 0.95 - 0.08 * min(len(unknowns), 5) - (0.1 if not draft else 0) - (0.1 if ungrounded else 0)
        if model_conf is not None:
            conf = min(conf, model_conf)
        conf = round(max(0.05, min(0.99, conf)), 2)

        auth = next((o for o in sk if o["skill"] == "authority_routing"), {"data": {}, "approvals": []})
        amount_aud = auth["data"].get("amount_aud")
        sched = next((o for o in sk if o["skill"] == "payment_scheduling"), {"data": {}})
        primary = self._primary_exception(state, outcome)
        slots = auth["approvals"] if outcome == Outcome.APPROVE_FOR_POSTING else (
            [{"slot": "primary", "description": "Delegated approver confirms rejection (no payment)", "min_limit_aud": "0.01",
              "requires_flag": None}] if outcome in CONSEQUENTIAL_OUTCOMES else [])
        owners = sorted({e.owner for e in exceptions})
        summary = (draft or {}).get("summary") or self._fallback_summary(req, outcome, primary, exceptions)
        if outcome == Outcome.APPROVE_FOR_POSTING:
            next_action = ("Request approval from: " + "; ".join(s["description"] for s in slots) +
                           f". On approval submit simulated posting; proposed payment run {sched['data'].get('proposed_payment_date')}.")
        elif outcome in (Outcome.REJECT_DUPLICATE, Outcome.REJECT_INVALID):
            next_action = "Request approver confirmation of rejection. Do not pay. Notify Accounts Payable of the rejected record."
        elif outcome == Outcome.ESCALATE_CONTROL_REVIEW:
            next_action = ("Escalate to " + ", ".join(owners or ["Financial Control"]) +
                           ". Do not notify the supplier of any fraud suspicion; open a vendor-verification task; no payment.")
        else:
            next_action = ("Hold. Route to " + ", ".join(owners or ["Accounts Payable"]) +
                           f" by {next_review}. Resume with new evidence via POST /runs/{run_id}/evidence.")
        rec = Recommendation(outcome=outcome, primary_exception=primary, confidence=conf, next_action=next_action,
                             summary=summary[:1500], required_approvals=slots,
                             amount_aud=format(amount_aud, "f") if isinstance(amount_aud, Decimal) else amount_aud,
                             proposed_payment_date=sched["data"].get("proposed_payment_date") if outcome == Outcome.APPROVE_FOR_POSTING else None)
        facts = [SourcedFact(fact=f["fact"], citations=self._cite_evidence(f["evidence"])) for o in sk for f in o["facts"]]
        calcs = [Calculation(**c) for o in sk for c in o["calculations"]]
        risks = [RiskIndicator(**r) for o in sk for r in o["risk_indicators"]]
        chunks = state["retrieval"]["chunks"]
        result = FinalResult(
            run_id=run_id, case_id=req["case_id"], status=RunStatus.RUNNING, recommendation=rec, sourced_facts=facts,
            calculations=calcs, inferences=inferences, unknowns=unknowns, policy_findings=findings, exceptions=exceptions,
            risk_indicators=risks, actions_taken=[ActionTaken(**a) for a in state["actions"]],
            model=state.get("model", {}),
            retrieval={"index_version": state["retrieval"].get("index_version"), "queries": len(state["retrieval"]["queries"]),
                       "chunks_retrieved": len(chunks),
                       "untrusted_retrieved": sorted(c for c, v in chunks.items() if v["trust"] == "untrusted"),
                       "superseded_retrieved": sorted(c for c, v in chunks.items() if v["trust"] == "superseded"),
                       "cited_policy_chunks": sorted({c.source_id for f in findings for c in f.citations if c.source_type == "policy"})},
            policy=self._policy_result(state),
        )
        state["result"] = result.model_dump(mode="json")
        state["outcome"] = outcome.value
        if ungrounded:
            self._trace(state, "DECIDE", "system", "grounding gap", "policy sections not retrieved: "
                        + ", ".join(sorted({f"{d} {x}" for _, (d, x) in ungrounded})))
        for inf in inferences:
            if inf.basis in ("policy guard", "model is allowed to be more conservative", "grounding gate", "policy gate"):
                self._trace(state, "DECIDE", "system" if inf.produced_by == "rules" else "model", "adjustment", inf.statement)
        self._trace(state, "DECIDE", "system", f"recommend {outcome.value}",
                    (f"primary exception {primary}; " if primary else "") + f"confidence {conf}; " + next_action[:220],
                    sorted({c.source_id for f in findings for c in f.citations if c.source_type == "policy"})[:8])
        self.audit.record("decision.recommended", run_id=run_id, node="DECIDE", outcome=outcome.value,
                          details={"primary_exception": primary, "confidence": conf, "exceptions": [e.category for e in exceptions],
                                   "rules_outcome": state["rules_outcome"]})
        if outcome in CONSEQUENTIAL_OUTCOMES:
            return "APPROVAL_GATE", RunStatus.RUNNING.value
        return "HOLD_FINALIZE", RunStatus.RUNNING.value

    @staticmethod
    def _policy_result(state) -> dict:
        p = state.get("policy") or {}
        return {"baseline_reviewed_by": p.get("reviewed_by"), "baseline_reviewed_at": p.get("reviewed_at"),
                "unverified_sections": [c["section"] for c in p.get("unverified") or []]}

    @staticmethod
    def _fallback_summary(req, outcome, primary, exceptions) -> str:
        base = f"Case {req['case_id']}: invoice {req['invoice_ref']} from {req['vendor_id']} for {req['currency']} {req['amount']}. "
        if not exceptions:
            return base + f"All controls passed; recommendation {outcome.value} pending human approval. (Deterministic narrative.)"
        return base + f"Recommendation {outcome.value}; primary exception {primary}; " + \
            "; ".join(f"{e.category}: {e.failed_rule}" for e in exceptions[:4]) + ". (Deterministic narrative.)"

    # ---------------------------------------------------------------- approval gate
    def n_approval_gate(self, run_id, state):
        req, result = state["request"], state["result"]
        rec = result["recommendation"]
        auth = next((o for o in state["skills"] if o["skill"] == "authority_routing"), {"data": {}})
        explanation = {
            "case_id": req["case_id"], "vendor_id": req["vendor_id"], "invoice_ref": req["invoice_ref"],
            "amount": req["amount"], "currency": req["currency"], "amount_aud": rec["amount_aud"],
            "proposed_outcome": rec["outcome"], "what_approval_does":
                ("Authorises a SIMULATED ERP posting of this invoice (no money moves)." if rec["outcome"] == "APPROVE_FOR_POSTING"
                 else "Confirms rejection of this invoice; nothing will be paid."),
            "summary": rec["summary"], "confidence": rec["confidence"], "primary_exception": rec["primary_exception"],
            "exceptions": result["exceptions"],
            "failed_or_unknown_findings": [f for f in result["policy_findings"] if f["status"] in ("FAIL", "UNKNOWN")],
            "key_findings": [{"control": f["control"], "status": f["status"], "detail": f["detail"]} for f in result["policy_findings"]],
            "key_calculations": [c for c in result["calculations"] if c["name"].startswith(("price_variance", "invoice_total", "tax_check",
                                                                                              "amount_in_aud", "approval_limit"))],
            "risk_indicators": result["risk_indicators"], "unknowns": result["unknowns"],
            "citations": sorted({c["source_id"] for f in result["policy_findings"] for c in f["citations"]}),
            "required_approvals": rec["required_approvals"], "constraints": auth["data"].get("constraints", {}),
            "proposed_payment_date": rec.get("proposed_payment_date"),
        }
        approval = self.store.create_approval({
            "approval_id": new_id("apr"), "run_id": run_id, "case_id": req["case_id"],
            "decision_type": "POST" if rec["outcome"] == "APPROVE_FOR_POSTING" else "REJECT",
            "proposed_outcome": rec["outcome"], "amount": req["amount"], "currency": req["currency"],
            "amount_aud": rec["amount_aud"] or req["amount"], "required_slots": rec["required_approvals"],
            "explanation": explanation, "created_at": self.iso()})
        state["approval_id"] = approval["approval_id"]
        self._trace(state, "APPROVAL_GATE", "system", "waiting for human approval",
                    "consequential outcome requires: " + "; ".join(s["description"] for s in rec["required_approvals"]))
        state["actions"].append({"action": "approval_requested", "tool": None, "at": self.iso(),
                                 "detail": {"approval_id": approval["approval_id"], "slots": [s["slot"] for s in rec["required_approvals"]]}})
        result["status"] = RunStatus.AWAITING_APPROVAL.value
        result["approval"] = {"approval_id": approval["approval_id"], "status": "PENDING"}
        result["actions_taken"] = state["actions"]
        self.audit.record("approval.requested", run_id=run_id, node="APPROVAL_GATE", outcome="pending",
                          details={"approval_id": approval["approval_id"], "proposed_outcome": rec["outcome"],
                                   "slots": rec["required_approvals"]})
        self.store.upsert_outcome(run_id=run_id, case_id=req["case_id"], outcome=rec["outcome"], pool_status="AWAITING_APPROVAL",
                                  primary_exception=rec["primary_exception"], amount=req["amount"], currency=req["currency"],
                                  summary=rec["summary"], payload={"approval_id": approval["approval_id"], "next_action": rec["next_action"]},
                                  now=self.iso())
        self._emit("approval.requested", run_id, {"approval_id": approval["approval_id"], "explanation": explanation,
                                                          "decide_url": f"/approvals/{approval['approval_id']}/decision"})
        self._emit("run.awaiting_approval", run_id, {"case_id": req["case_id"], "approval_id": approval["approval_id"]})
        return None, RunStatus.AWAITING_APPROVAL.value

    def n_hold_finalize(self, run_id, state):
        req, result = state["request"], state["result"]
        rec = result["recommendation"]
        result["status"] = RunStatus.NEEDS_INFORMATION.value
        state["actions"].append({"action": "case_held" if rec["outcome"] == "HOLD_FOR_INFORMATION" else "case_escalated",
                                 "tool": None, "at": self.iso(), "detail": {"owners": sorted({e["owner"] for e in result["exceptions"]})}})
        result["actions_taken"] = state["actions"]
        pool = "ESCALATED" if rec["outcome"] == "ESCALATE_CONTROL_REVIEW" else "HELD"
        self.store.upsert_outcome(run_id=run_id, case_id=req["case_id"], outcome=rec["outcome"], pool_status=pool,
                                  primary_exception=rec["primary_exception"], amount=req["amount"], currency=req["currency"],
                                  summary=rec["summary"], payload={"next_action": rec["next_action"], "exceptions": result["exceptions"]},
                                  now=self.iso())
        self.audit.record("run.needs_information", run_id=run_id, node="HOLD_FINALIZE", outcome=rec["outcome"],
                          details={"primary_exception": rec["primary_exception"]})
        self._emit("run.needs_information", run_id, {"case_id": req["case_id"], "outcome": rec["outcome"],
                                                             "primary_exception": rec["primary_exception"],
                                                             "next_action": rec["next_action"],
                                                             "exceptions": result["exceptions"]})
        return None, RunStatus.NEEDS_INFORMATION.value

    def n_submit(self, run_id, state):
        req, result = state["request"], state["result"]
        approval = self.store.get_approval(state["approval_id"])
        # last line of defence: approval may have come hours later - never post under policy that has since changed
        drift = self._policy_drift(state, self._cited_policy_ids(result))
        if drift:
            desc = "; ".join(f"{c} {p}" for c, p in drift)
            rec = result["recommendation"]
            rec.update(outcome=Outcome.HOLD_FOR_INFORMATION.value, primary_exception="OTHER_CONTROL_RISK",
                       next_action=(f"Governing policy changed after approval ({desc}). Nothing was posted. Re-evaluate under the "
                                    f"current policy: POST /runs/{run_id}/evidence (re-runs every control)."))
            result["exceptions"].append({"category": "OTHER_CONTROL_RISK", "failed_rule": "Policy unchanged between decision and posting",
                                         "expected": "cited policy sections identical to those assessed", "observed": desc,
                                         "citations": [], "owner": "Accounts Payable",
                                         "next_review_date": add_business_days(self.now().date(), 1).isoformat()})
            result["unknowns"].append({"item": f"Governing policy changed after approval: {desc}",
                                       "impact": "approval was given against superseded policy text",
                                       "resolution": "re-evaluate the case under the current policy"})
            state["actions"].append({"action": "submission_blocked_policy_changed", "tool": None, "at": self.iso(),
                                     "detail": {"sections": [c for c, _ in drift], "approval_id": approval["approval_id"]}})
            self.audit.record("policy.changed_since_decision", run_id=run_id, node="SUBMIT_DECISION", outcome="blocked",
                              details={"sections": [f"{c}: {p}" for c, p in drift], "approval_id": approval["approval_id"]})
            self._trace(state, "SUBMIT_DECISION", "system", "posting BLOCKED - policy changed after approval", desc)
            return "HOLD_FINALIZE", RunStatus.RUNNING.value
        key = idempotency_key_for(run_id, approval["approval_id"], approval["proposed_outcome"], approval["amount"])
        args = {"run_id": run_id, "case_id": req["case_id"], "approval_id": approval["approval_id"],
                "decision": approval["decision_type"], "outcome": approval["proposed_outcome"], "vendor_id": req["vendor_id"],
                "invoice_ref": req["invoice_ref"], "amount": approval["amount"], "currency": approval["currency"],
                "idempotency_key": key}
        res = self._tool(run_id, state, "submit_finance_decision", args,
                         authorization={"approval_id": approval["approval_id"], "status": approval["status"]})
        if not res.ok:
            raise RuntimeError(f"submit_finance_decision failed: {res.error}")
        if res.output.status == "ALREADY_DECIDED":
            # one decision per invoice: another run already posted / rejected this vendor's invoice number
            prev = f"{res.output.decision} {res.output.posting_ref} by run {res.output.previous_run_id}"
            rec = result["recommendation"]
            rec.update(outcome=Outcome.HOLD_FOR_INFORMATION.value, primary_exception="DUPLICATE_RISK",
                       next_action=f"This invoice was already decided ({prev}). Nothing was posted by this run. "
                                   "Check the earlier decision; do not re-submit.")
            result["exceptions"].append({"category": "DUPLICATE_RISK", "failed_rule": "One decision per invoice",
                                         "expected": "no earlier ERP decision for this vendor invoice number", "observed": prev,
                                         "citations": [], "owner": "Accounts Payable",
                                         "next_review_date": add_business_days(self.now().date(), 1).isoformat()})
            state["actions"].append({"action": "submission_blocked_already_decided", "tool": "submit_finance_decision",
                                     "at": self.iso(), "detail": {"previous_run_id": res.output.previous_run_id,
                                                                  "posting_ref": res.output.posting_ref}})
            self.audit.record("decision.already_decided", run_id=run_id, node="SUBMIT_DECISION", outcome="blocked",
                              details={"previous_run_id": res.output.previous_run_id, "posting_ref": res.output.posting_ref})
            self._trace(state, "SUBMIT_DECISION", "system", "posting BLOCKED - invoice already decided", prev)
            return "HOLD_FINALIZE", RunStatus.RUNNING.value
        state["submission"] = res.output.model_dump(mode="json")
        self._trace(state, "SUBMIT_DECISION", "system", f"{res.output.decision} {res.output.status}",
                    f"approval {approval['approval_id']} APPROVED; arguments matched the approved values; "
                    f"idempotency key reused on any retry -> posting_ref {res.output.posting_ref} (simulated)")
        state["actions"].append({"action": "decision_submitted", "tool": "submit_finance_decision", "at": self.iso(),
                                 "detail": {"posting_ref": res.output.posting_ref, "status": res.output.status,
                                            "idempotency_key": key, "simulated": True}})
        self._emit("decision.submitted", run_id, {"case_id": req["case_id"], "posting_ref": res.output.posting_ref,
                                                          "decision": res.output.decision, "status": res.output.status})
        return "FINALIZE", RunStatus.RUNNING.value

    def n_finalize(self, run_id, state):
        req, result = state["request"], state["result"]
        result["status"] = RunStatus.COMPLETED.value
        result["actions_taken"] = state["actions"]
        result["approval"] = self._approval_view(self.store.get_approval(state["approval_id"]))
        result["submission"] = state.get("submission")
        self.store.upsert_outcome(run_id=run_id, case_id=req["case_id"], outcome=result["recommendation"]["outcome"],
                                  pool_status="SUBMITTED", primary_exception=result["recommendation"]["primary_exception"],
                                  amount=req["amount"], currency=req["currency"], summary=result["recommendation"]["summary"],
                                  payload={"submission": state.get("submission")}, now=self.iso())
        self.audit.record("run.completed", run_id=run_id, node="FINALIZE", outcome=result["recommendation"]["outcome"],
                          details={"posting_ref": (state.get("submission") or {}).get("posting_ref")})
        self._emit("run.completed", run_id, {"case_id": req["case_id"], "outcome": result["recommendation"]["outcome"],
                                                     "posting_ref": (state.get("submission") or {}).get("posting_ref")})
        return None, RunStatus.COMPLETED.value

    def n_finalize_rejected(self, run_id, state):
        req, result = state["request"], state["result"]
        approval = self.store.get_approval(state["approval_id"])
        result["status"] = RunStatus.COMPLETED.value
        state["actions"].append({"action": "approval_rejected_no_submission", "tool": None, "at": self.iso(),
                                 "detail": {"approval_id": approval["approval_id"]}})
        result["actions_taken"] = state["actions"]
        result["approval"] = self._approval_view(approval)
        result["inferences"].append({"statement": "Approver rejected the recommendation; nothing was submitted. "
                                                  "Case returns to Accounts Payable for review.",
                                     "basis": "approval decision", "produced_by": "rules"})
        self.store.upsert_outcome(run_id=run_id, case_id=req["case_id"], outcome=result["recommendation"]["outcome"],
                                  pool_status="REJECTED_BY_APPROVER", primary_exception=result["recommendation"]["primary_exception"],
                                  amount=req["amount"], currency=req["currency"], summary="Approver rejected the recommendation",
                                  payload={"decisions": approval["decisions"]}, now=self.iso())
        self.audit.record("run.completed", run_id=run_id, node="FINALIZE_REJECTED", outcome="approval_rejected")
        self._emit("run.completed", run_id, {"case_id": req["case_id"], "outcome": "APPROVAL_REJECTED"})
        return None, RunStatus.COMPLETED.value

    # ===================================================================== approvals
    @staticmethod
    def _approval_view(a: dict | None) -> dict | None:
        if not a:
            return None
        return {k: a[k] for k in ("approval_id", "run_id", "case_id", "status", "decision_type", "proposed_outcome", "amount",
                                  "currency", "amount_aud", "required_slots", "decisions", "created_at", "resolved_at")} | \
               {"explanation": a["explanation"]}

    def _approver_authority(self, register: dict, user_id: str, today: date) -> tuple[dict | None, Decimal | None, list[str], str | None]:
        user = next((u for u in register["approvers"] if u["user_id"] == user_id), None)
        if not user or not user.get("active", True):
            return None, None, [], "approver not found or inactive in authority register"
        def lim(v):
            return Decimal("Infinity") if v is None else Decimal(str(v))
        limit = lim(user.get("limit_aud"))
        note = None
        for d in register.get("delegations", []):
            if d["delegate"] != user_id:
                continue
            start, end = date.fromisoformat(d["start_date"]), date.fromisoformat(d["end_date"])
            if start <= today <= end:
                limit = max(limit, lim(d["limit_aud"]))
            else:
                note = f"delegation from {d['delegator']} expired/not active ({d['start_date']}..{d['end_date']}) (FIN-POL-003 s4)"
        return user, limit, user.get("flags", []), note

    def decide_approval(self, approval_id: str, body: ApprovalDecisionIn, channel: str = "api") -> tuple[int, dict]:
        # 1. replay protection: same callback id FOR THE SAME APPROVAL -> identical stored response, no side effects.
        #    The cache key is scoped to the approval so a reused id on another approval is never mistaken for a replay.
        cb_key = f"{approval_id}:{body.callback_id}" if body.callback_id else None
        if cb_key:
            prior = self.store.fetchone("SELECT * FROM approval_callbacks WHERE callback_id=?", (cb_key,))
            if prior:
                resp = json.loads(prior["response_json"])
                resp["replayed"] = True
                self.audit.record("approval.callback_replayed", run_id=resp.get("run_id"), actor=body.approver_id,
                                  details={"approval_id": approval_id, "callback_id": body.callback_id})
                return prior["http_status"], resp
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise ApprovalError(404, "approval not found")
        run_id = approval["run_id"]
        status_code, resp, resolved = self._apply_decision(approval, body, channel)
        if cb_key:
            with self.store.tx() as conn:
                conn.execute("INSERT OR IGNORE INTO approval_callbacks(callback_id, approval_id, request_fingerprint, response_json, "
                             "http_status, received_at) VALUES (?,?,?,?,?,?)",
                             (cb_key, approval_id, f"{body.decision}:{body.approver_id}", json.dumps(resp, default=str),
                              status_code, self.iso()))
        if resolved:
            # resume the same run exactly once (claim_run is atomic)
            next_node = "SUBMIT_DECISION" if resolved == "APPROVED" else "FINALIZE_REJECTED"
            run = self.store.get_run(run_id)
            if self.store.claim_run(run_id, (RunStatus.AWAITING_APPROVAL.value,), self.iso()):
                state = run["state_json"]
                self.store.checkpoint(run_id, "APPROVAL_GATE", next_node, RunStatus.RUNNING.value, state, self.iso())
                self.audit.record("run.resumed", run_id=run_id, details={"from_node": next_node, "trigger": f"approval.{resolved.lower()}"})
                self._execute(run_id)
                self._auto_diagram(run_id)
            final = self.get_run(run_id, include_audit=False)
            resp["run"] = {"status": final["status"], "outcome": ((final["result"] or {}).get("recommendation") or {}).get("outcome"),
                           "submission": (final["result"] or {}).get("submission")}
            if cb_key:
                with self.store.tx() as conn:
                    conn.execute("UPDATE approval_callbacks SET response_json=? WHERE callback_id=?",
                                 (json.dumps(resp, default=str), cb_key))
        return status_code, resp

    def _apply_decision(self, approval: dict, body: ApprovalDecisionIn, channel: str) -> tuple[int, dict, str | None]:
        run_id = approval["run_id"]
        res = self.tools.call("get_authority_register", {"as_of": self.now().date().isoformat()},
                              ToolContext(run_id=run_id, case_id=approval["case_id"], budget={"tool_calls": 0}))
        if not res.ok:
            raise ApprovalError(503, "authority register unavailable; decision not recorded")
        register = res.output.model_dump(mode="json")
        with self.store.tx() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval["approval_id"],)).fetchone()
            approval = self.store._approval_row(dict(row))
            base = {"approval_id": approval["approval_id"], "run_id": run_id, "replayed": False}
            if approval["status"] != "PENDING":
                self.audit.record("approval.duplicate_ignored", run_id=run_id, actor=body.approver_id, outcome="conflict",
                                  details={"status": approval["status"], "decision": body.decision, "channel": channel})
                return 409, base | {"status": approval["status"], "effect": "none",
                                    "message": f"approval already {approval['status']}; this decision had no effect"}, None
            decisions = approval["decisions"]
            if any(d["approver_id"] == body.approver_id for d in decisions):
                self.audit.record("approval.duplicate_ignored", run_id=run_id, actor=body.approver_id, outcome="already_recorded")
                return 200, base | {"status": approval["status"], "effect": "none",
                                    "message": "decision from this approver already recorded"}, None
            user, limit, flags, note = self._approver_authority(register, body.approver_id, self.now().date())
            constraints = approval["explanation"].get("constraints", {})
            reasons = []
            if user is None:
                reasons.append(note or "unknown approver")
            excluded = constraints.get("excluded_approvers", {})
            if body.approver_id in excluded:
                reasons.append(f"segregation of duties: approver {excluded[body.approver_id]}")
            run_state = (self.store.get_run(run_id) or {}).get("state_json") or {}
            reviewers = {h["reviewer_id"] for qs in (run_state.get("human_answers") or {}).values() for h in qs.values()}
            if body.approver_id in reviewers:
                reasons.append("segregation of duties: the reviewer of the AI judgement cannot approve (FIN-POL-001 s4)")
            if body.approver_id in {constraints.get("requester_id"), constraints.get("po_requester_id")}:
                reasons.append("segregation of duties: requester cannot approve (FIN-POL-001 s4)")
            if constraints.get("distinct_roles_required") and body.approver_id in constraints.get("receipters", []):
                reasons.append("segregation of duties: receipter cannot approve above AUD 25,000 (FIN-POL-001 s4)")
            filled = {d["slot"] for d in decisions}
            slot = None
            if not reasons:
                # flagged slots (e.g. Financial Control) first, so a specialist fills their own slot
                for s in sorted(approval["required_slots"], key=lambda x: x.get("requires_flag") is None):
                    if s["slot"] in filled:
                        continue
                    if s.get("requires_flag"):
                        if s["requires_flag"] in flags:
                            slot = s; break
                    elif limit is not None and limit >= Decimal(s["min_limit_aud"] or "0") and limit > 0:
                        slot = s; break
                if slot is None:
                    reasons.append(f"insufficient authority for remaining slots (limit {limit}, flags {flags})"
                                   + (f"; {note}" if note else ""))
            if reasons:
                self.audit.record("approval.denied", run_id=run_id, actor=body.approver_id, outcome="forbidden",
                                  details={"reasons": reasons, "decision": body.decision, "channel": channel})
                return 403, base | {"status": approval["status"], "effect": "none", "denied_reasons": reasons}, None
            decision = {"slot": slot["slot"], "approver_id": body.approver_id, "role": user["role"],
                        "applicable_limit_aud": "unlimited" if limit == Decimal("Infinity") else format(limit, "f"),
                        "register_version": register["version"], "decision": body.decision, "comment": body.comment,
                        "channel": channel, "decided_at": self.iso(), "case_id": approval["case_id"]}
            decisions.append(decision)
            new_status = "PENDING"
            if body.decision == "REJECT":
                new_status = "REJECTED"
            elif {s["slot"] for s in approval["required_slots"]} <= {d["slot"] for d in decisions if d["decision"] == "APPROVE"}:
                new_status = "APPROVED"
            conn.execute("UPDATE approvals SET decisions=?, status=?, resolved_at=? WHERE approval_id=?",
                         (json.dumps(decisions), new_status, self.iso() if new_status != "PENDING" else None, approval["approval_id"]))
        self.audit.record("approval.decision_recorded", run_id=run_id, actor=body.approver_id, outcome=body.decision.lower(),
                          details={"slot": slot["slot"], "role": user["role"], "status": new_status, "channel": channel,
                                   "register_version": register["version"], "comment": body.comment,
                                   "limit_aud": decision["applicable_limit_aud"]})
        self.webhooks.emit("approval.decision_recorded", run_id, {"approval_id": approval["approval_id"], "slot": slot["slot"],
                                                                  "decision": body.decision, "status": new_status})
        resolved = None if new_status == "PENDING" else new_status
        if resolved:
            self.audit.record("approval.resolved", run_id=run_id, outcome=new_status.lower(),
                              details={"approval_id": approval["approval_id"]})
            self.webhooks.emit("approval.resolved", run_id, {"approval_id": approval["approval_id"], "status": new_status})
        remaining = [s["slot"] for s in approval["required_slots"] if s["slot"] not in {d["slot"] for d in decisions}]
        return 200, base | {"status": new_status, "effect": "recorded", "slot": slot["slot"], "remaining_slots": remaining}, resolved
