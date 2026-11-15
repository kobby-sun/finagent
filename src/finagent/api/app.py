"""HTTP API (FastAPI). Swagger UI: /docs   ReDoc: /redoc   OpenAPI JSON: /openapi.json

Logical operations required by the brief:
  Start run        POST /runs
  Get run          GET  /runs/{run_id}
  Approve / reject POST /approvals/{approval_id}/decision   (and signed POST /webhooks/inbound/approval)
  Evaluation       POST /evaluations/run, GET /evaluations/latest
"""
from __future__ import annotations

import json
from typing import Any, Literal, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from ..agent.graph import ApprovalError
from ..config import load_settings
from ..contracts import export_json_schemas
from ..governance.params import CATALOG as PARAM_CATALOG
from ..llm.providers import probe_pair
from ..schemas import ApprovalDecisionIn, EvidenceIn, ProcessingRequest, ReviewIn
from ..services import App, build_app
from ..skills.base import REGISTRY
from ..skills.parsers.registry import describe_parsers
from ..webhooks import EVENT_TYPES, verify
from .examples import (DECISION_EXAMPLES, EVAL_EXAMPLES, EVIDENCE_EXAMPLES, SUBSCRIPTION_EXAMPLES, run_examples)

TAGS = [
    {"name": "runs", "description": "Start, inspect and resume workflow runs."},
    {"name": "approvals", "description": "Human approval requests (with explanations) and decisions that resume the run."},
    {"name": "outcomes", "description": "Outcome pool: every case's current outcome / status."},
    {"name": "webhooks", "description": "Outbound status webhooks (HMAC-signed) and inbound signed approval callbacks."},
    {"name": "audit", "description": "Hash-chained audit trail."},
    {"name": "evaluation", "description": "Scenario evaluation and skill fixture tests."},
    {"name": "policy", "description": "Read-only: the policy numbers the controls enforce and whether the policy text matches the "
                                       "verified baseline. Policy changes are maintenance releases, never live."},
    {"name": "catalog", "description": "Tools, skills, parsers, workflow and data contracts (introspection)."},
    {"name": "ops", "description": "Health."},
]

class SubscriptionIn(BaseModel):
    url: str = Field(examples=["http://localhost:9000/hooks"])
    events: list[str] = Field(default=["*"], examples=[["approval.requested", "run.needs_information", "run.completed"]])


class InboundApproval(ApprovalDecisionIn):
    approval_id: str


class EvalRequest(BaseModel):
    cases: Optional[list[str]] = Field(default=None, examples=[["FIN-001", "FIN-003"]])


def create_app(app_ctx: App | None = None) -> FastAPI:
    ctx = app_ctx or build_app(load_settings())
    api = FastAPI(title="finagent - Financial Processing RAG Workflow Agent", version="0.1.0", openapi_tags=TAGS,
                  description="Accounts-payable agent: RAG over finance policy, deterministic reconciliation, approval-gated "
                              "simulated posting, resumable runs, hash-chained audit. **No real money movement.**<br/><br/>"
                              "**Testing in Swagger:** 1) `POST /runs` -> *Try it out* -> pick a scenario from the **Examples** "
                              "dropdown (each lists the expected result) -> Execute. 2) Copy `approval.approval_id` from the "
                              "response. 3) `POST /approvals/{approval_id}/decision` -> pick an approver example -> Execute. "
                              "4) Check `GET /runs/{run_id}`, `GET /outcomes`, `GET /audit/verify`. Full guide: docs/api/swagger_testing.md",
                  swagger_ui_parameters={"tryItOutEnabled": True, "displayRequestDuration": True, "docExpansion": "list"})
    api.state.ctx = ctx
    RUN_EXAMPLES = run_examples(ctx.settings.root)          # every evaluated scenario, selectable in Swagger

    def auth(x_api_key: Optional[str] = Header(default=None)):
        expected = ctx.settings.get("app.api_key")
        if expected and x_api_key != expected:
            raise HTTPException(401, "missing or invalid X-API-Key")

    @api.exception_handler(KeyError)
    async def _nf(request: Request, exc: KeyError):
        return JSONResponse(status_code=404, content={"error": f"not found: {exc}"})

    @api.exception_handler(ApprovalError)
    async def _ae(request: Request, exc: ApprovalError):
        return JSONResponse(status_code=exc.http_status, content=exc.body)

    dep = [Depends(auth)]

    # ------------------------------------------------------------------ ops
    @api.get("/healthz", tags=["ops"])
    def healthz():
        return {"status": "ok", "index_version": ctx.retriever.index_version, "retrieval": ctx.retriever.describe(), "llm_provider": ctx.settings.get("llm.provider"),
                "model": ctx.llm.model, "workflow": {"name": ctx.pipeline.name, "version": ctx.pipeline.version, "digest": ctx.pipeline.digest},
                "policy": ctx.policy.status()["state"],
                "skill_questions": ({"enabled": True, **probe_pair(ctx.engine.skill_runner.small, ctx.engine.skill_runner.large)}
                                     if ctx.settings.get("skills.llm.enabled", True) else {"enabled": False})}

    # ------------------------------------------------------------------ runs
    @api.post("/runs", status_code=201, tags=["runs"], dependencies=dep,
              summary="Start run: execute until completion, failure or approval required")
    def start_run(req: ProcessingRequest = Body(openapi_examples=RUN_EXAMPLES),
                  idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
                  include_audit: bool = False):
        run = ctx.engine.start_run(req, idempotency_key=idempotency_key)
        if not include_audit:
            run.pop("audit_events", None)
        return run

    @api.get("/runs", tags=["runs"], dependencies=dep)
    def list_runs(status: Optional[str] = None, case_id: Optional[str] = None, limit: int = Query(100, le=500)):
        return ctx.store.list_runs(status, case_id, limit)

    @api.get("/runs/{run_id}", tags=["runs"], dependencies=dep, summary="Get run: status, current state, result and audit events")
    def get_run(run_id: str, include_audit: bool = True):
        return ctx.engine.get_run(run_id, include_audit=include_audit)

    @api.get("/runs/{run_id}/reasoning", tags=["runs"], dependencies=dep,
             summary="Why each key step went the way it did (system, skills, model, humans)")
    def reasoning(run_id: str):
        if ctx.store.get_run(run_id) is None:
            raise KeyError(run_id)
        return ctx.engine.reasoning(run_id)

    @api.get("/runs/{run_id}/diagram", tags=["runs"], dependencies=dep,
             summary="Diagram of this run: path, exit step, tools, skill verdicts, decision, approvals, warnings",
             responses={200: {"content": {"image/svg+xml": {}, "image/png": {}, "application/pdf": {}}}})
    def diagram(run_id: str, format: Literal["svg", "png", "pdf"] = "svg", download: bool = False):
        from fastapi.responses import Response
        from ..rundiagram import MEDIA, render
        run = ctx.engine.get_run(run_id)
        run["state"] = (ctx.store.get_run(run_id) or {}).get("state_json") or {}
        headers = {"Content-Disposition": f'attachment; filename="{run["case_id"]}_{run_id}.{format}"'} if download else {}
        return Response(render(run, format), media_type=MEDIA[format], headers=headers)

    @api.post("/runs/{run_id}/resume", tags=["runs"], dependencies=dep, summary="Resume a crashed/interrupted run (idempotent)")
    def resume(run_id: str):
        r = ctx.engine.resume(run_id)
        r.pop("audit_events", None)
        return r

    @api.post("/runs/{run_id}/evidence", status_code=201, tags=["runs"], dependencies=dep,
              summary="Add evidence to a held run; re-runs every control as a new attempt (FIN-POL-007 s5)")
    def add_evidence(run_id: str, body: EvidenceIn = Body(openapi_examples=EVIDENCE_EXAMPLES)):
        r = ctx.engine.add_evidence(run_id, body.attachments, body.note, body.po_ref)
        r.pop("audit_events", None)
        return r

    @api.post("/runs/{run_id}/review", status_code=201, tags=["runs"], dependencies=dep,
              summary="A reviewer decides a judgement question the AI could not settle; re-runs as a new attempt")
    def review(run_id: str, body: ReviewIn):
        r = ctx.engine.review_judgement(run_id, body)
        r.pop("audit_events", None)
        return r

    # ------------------------------------------------------------------ approvals
    @api.get("/approvals", tags=["approvals"], dependencies=dep)
    def list_approvals(status: Optional[Literal["PENDING", "APPROVED", "REJECTED"]] = None):
        return [ctx.engine._approval_view(a) for a in ctx.store.list_approvals(status)]

    @api.get("/approvals/{approval_id}", tags=["approvals"], dependencies=dep, summary="Approval request with full explanation")
    def get_approval(approval_id: str):
        a = ctx.store.get_approval(approval_id)
        if not a:
            raise KeyError(approval_id)
        return ctx.engine._approval_view(a)

    @api.post("/approvals/{approval_id}/decision", tags=["approvals"], dependencies=dep,
              summary="Approve / reject; resumes the same run. Duplicate callback_id replays the stored response.",
              responses={403: {"description": "Approver lacks authority or violates segregation of duties"},
                         409: {"description": "Approval already resolved; no effect"}})
    def decide(approval_id: str, body: ApprovalDecisionIn = Body(openapi_examples=DECISION_EXAMPLES),
               x_callback_id: Optional[str] = Header(default=None)):
        if x_callback_id and not body.callback_id:
            body.callback_id = x_callback_id
        code, resp = ctx.engine.decide_approval(approval_id, body, channel="api")
        return JSONResponse(status_code=code, content=json.loads(json.dumps(resp, default=str)))

    # ------------------------------------------------------------------ webhooks
    @api.post("/webhooks/inbound/approval", tags=["webhooks"],
              summary="Signed approval callback from an external approval system (X-Finagent-Signature)")
    async def inbound_approval(request: Request, x_finagent_signature: Optional[str] = Header(default=None)):
        raw = await request.body()
        if not verify(ctx.webhooks.secret, raw, x_finagent_signature):
            ctx.audit.record("webhook.inbound_rejected", outcome="bad_signature", details={"path": "/webhooks/inbound/approval"})
            raise HTTPException(401, "invalid signature")
        try:
            body = InboundApproval.model_validate_json(raw)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_url=False))
        decision = ApprovalDecisionIn(**body.model_dump(exclude={"approval_id"}))
        code, resp = ctx.engine.decide_approval(body.approval_id, decision, channel="webhook")
        return JSONResponse(status_code=code, content=json.loads(json.dumps(resp, default=str)))

    @api.post("/webhooks/subscriptions", status_code=201, tags=["webhooks"], dependencies=dep)
    def subscribe(body: SubscriptionIn = Body(openapi_examples=SUBSCRIPTION_EXAMPLES)):
        try:
            return ctx.webhooks.subscribe(body.url, body.events)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @api.get("/webhooks/subscriptions", tags=["webhooks"], dependencies=dep)
    def subscriptions():
        return ctx.webhooks.subscriptions()

    @api.delete("/webhooks/subscriptions/{subscription_id}", tags=["webhooks"], dependencies=dep)
    def unsubscribe(subscription_id: str):
        return {"removed": ctx.webhooks.unsubscribe(subscription_id)}

    @api.get("/webhooks/events", tags=["webhooks"], dependencies=dep, summary="Event feed / delivery log (outbox)")
    def events(run_id: Optional[str] = None, limit: int = 200):
        return ctx.webhooks.feed(run_id, limit)

    @api.get("/webhooks/event-types", tags=["webhooks"])
    def event_types():
        return EVENT_TYPES

    @api.post("/webhooks/deliver", tags=["webhooks"], dependencies=dep, summary="Retry pending/failed deliveries")
    def deliver():
        return ctx.webhooks.deliver_pending()

    # ------------------------------------------------------------------ outcomes
    @api.get("/outcomes", tags=["outcomes"], dependencies=dep)
    def outcomes(pool_status: Optional[Literal["AWAITING_APPROVAL", "SUBMITTED", "HELD", "ESCALATED", "REJECTED_BY_APPROVER", "FAILED", "SUPERSEDED"]] = None,
                 case_id: Optional[str] = None):
        return ctx.store.list_outcomes(pool_status, case_id)

    @api.get("/outcomes/summary", tags=["outcomes"], dependencies=dep)
    def outcome_summary():
        rows = ctx.store.fetchall("SELECT pool_status, COUNT(*) AS n FROM outcomes GROUP BY pool_status")
        return {r["pool_status"]: r["n"] for r in rows}

    @api.get("/outcomes/{run_id}", tags=["outcomes"], dependencies=dep)
    def outcome(run_id: str):
        o = ctx.store.get_outcome(run_id)
        if not o:
            raise KeyError(run_id)
        return o

    # ------------------------------------------------------------------ audit
    @api.get("/audit/verify", tags=["audit"], dependencies=dep)
    def audit_verify():
        return ctx.audit.verify_chain()

    @api.get("/runs/{run_id}/audit", tags=["audit"], dependencies=dep)
    def audit_events(run_id: str):
        return ctx.audit.events(run_id)

    # ------------------------------------------------------------------ evaluation
    @api.post("/evaluations/run", tags=["evaluation"], dependencies=dep, summary="Run scenario evaluation; returns pass/fail per case")
    def run_eval(body: EvalRequest = Body(default=EvalRequest(), openapi_examples=EVAL_EXAMPLES)):
        from ..evaluation import run_evaluation
        return run_evaluation(ctx.settings, body.cases)

    @api.get("/evaluations", tags=["evaluation"], dependencies=dep)
    def list_evals():
        rows = ctx.store.fetchall("SELECT eval_id, created_at, summary FROM eval_results ORDER BY created_at DESC LIMIT 50")
        return [{**r, "summary": json.loads(r["summary"])} for r in rows]

    @api.get("/evaluations/latest", tags=["evaluation"], dependencies=dep)
    def latest_eval():
        p = ctx.settings.data_dir / "eval" / "latest.json"
        if not p.exists():
            raise HTTPException(404, "no evaluation has been run yet")
        return json.loads(p.read_text())

    @api.post("/skills/{name}/test", tags=["evaluation"], dependencies=dep, summary="Run a skill's fixture tests")
    def skill_test(name: str):
        from ..skills.testing import SkillHarness
        res = SkillHarness(ctx).run_all(name)
        return {"skill": name, "passed": sum(r.passed for r in res), "total": len(res),
                "results": [r.__dict__ for r in res]}

    # ------------------------------------------------------------------ policy (read-only)
    @api.get("/policy/status", tags=["policy"], dependencies=dep,
             summary="Does the policy text match the verified baseline? If not, every case is held until maintenance")
    def policy_status():
        return ctx.policy.status()

    @api.get("/policy/parameters", tags=["policy"], dependencies=dep,
             summary="Every policy number the controls enforce, with its source section (config/policy_parameters.yaml)")
    def policy_parameters():
        return [{"name": n, "value": ctx.policy.params[n], "cite": s.cite, "unit": s.unit, "description": s.description}
                for n, s in PARAM_CATALOG.items()]

    # ------------------------------------------------------------------ catalog
    @api.get("/catalog/tools", tags=["catalog"])
    def tools():
        return ctx.tools.describe()

    @api.get("/catalog/skills", tags=["catalog"])
    def skills():
        from ..skills.pack import SkillPackError, load_pack

        def pack(n):
            try:
                p = load_pack(n).spec
                return {"execution": p.execution, "spec_version": p.version, "evidence": p.evidence,
                        "questions": [q.model_dump(exclude_none=True) for q in p.questions], "references": p.references}
            except SkillPackError:
                return {"execution": "code (no pack)"}
        return [{"name": s.name, "kind": s.kind, "version": s.version, **pack(s.name), "mandatory": s.mandatory, "mandatory_basis": s.mandatory_basis,
                 "description": s.description, "compliance_refs": [f"{d} {x}" for d, x in s.compliance_refs],
                 "retrieval_queries": s.retrieval_queries, "produces": list(s.produces)} for s in REGISTRY.values()]

    @api.get("/catalog/parsers", tags=["catalog"])
    def parsers():
        return describe_parsers()

    @api.get("/catalog/workflow", tags=["catalog"])
    def workflow():
        return {**ctx.pipeline.describe(), "digest": ctx.pipeline.digest}

    @api.get("/catalog/contracts", tags=["catalog"])
    def contracts():
        return export_json_schemas()

    return api


def get_app() -> FastAPI:  # uvicorn factory: uvicorn finagent.api.app:get_app --factory
    return create_app()
