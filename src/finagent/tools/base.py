"""Tool runtime: schema validation, permissions, budgets, timeouts, bounded retries
and an audit event for every attempt.

Every tool is declared with a :class:`ToolSpec`. The runner is the *only* way
the agent can call a tool, so these guarantees hold for all of them:

* inputs are validated against the input model before execution (invalid
  arguments fail fast and are never "repaired" by guessing);
* outputs are validated against the output model (a backend returning garbage
  is a tool failure, not evidence);
* each attempt runs with a hard timeout; ``TransientToolError`` and timeouts
  are retried with exponential backoff up to ``max_attempts``;
* consequential tools are deny-by-default: they require an ``authorization``
  object and the tool-specific guard to confirm it (approval granted,
  arguments identical to what was approved);
* per-run tool-call budget is enforced.
"""
from __future__ import annotations

import concurrent.futures as cf
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Type

from pydantic import BaseModel, ValidationError

_EXECUTOR = cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="tool")


class ToolError(Exception):
    retryable = False


class TransientToolError(ToolError):
    retryable = True


class ToolTimeout(ToolError):
    retryable = True


class ToolPermissionDenied(ToolError):
    pass


class ToolBudgetExceeded(ToolError):
    pass


class ToolValidationError(ToolError):
    pass


class ToolNotFound(ToolError):
    pass


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]
    func: Callable[[BaseModel, "ToolContext"], Any]
    consequential: bool = False
    permissions: tuple[str, ...] = ("read",)
    guard: Optional[Callable[[BaseModel, "ToolContext"], None]] = None


@dataclass
class ToolContext:
    run_id: str
    case_id: str
    budget: dict
    authorization: Optional[dict] = None
    extras: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    tool: str
    ok: bool
    output: Optional[BaseModel] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    attempts: int = 0
    duration_ms: int = 0
    evidence_id: Optional[str] = None


class ToolRunner:
    def __init__(self, settings, audit):
        self.settings = settings
        self.audit = audit
        self.specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self.specs[spec.name] = spec

    def describe(self) -> list[dict]:
        return [{
            "name": s.name, "description": s.description, "consequential": s.consequential,
            "permissions": list(s.permissions),
            "timeout_s": self.settings.tool_setting(s.name, "timeout_s"),
            "max_attempts": self.settings.tool_setting(s.name, "max_attempts"),
            "input_schema": s.input_model.model_json_schema(),
            "output_schema": s.output_model.model_json_schema(),
        } for s in self.specs.values()]

    def call(self, name: str, args: dict | BaseModel, ctx: ToolContext) -> ToolResult:
        spec = self.specs.get(name)
        if spec is None:
            raise ToolNotFound(name)
        start = time.perf_counter()

        # 1. budget --------------------------------------------------------
        ctx.budget["tool_calls"] = ctx.budget.get("tool_calls", 0) + 1
        max_calls = int(self.settings.get("agent.max_tool_calls", 25))
        if ctx.budget["tool_calls"] > max_calls:
            self.audit.record("tool.denied", run_id=ctx.run_id, tool=name, outcome="budget_exceeded",
                              details={"tool_calls": ctx.budget["tool_calls"], "max": max_calls})
            raise ToolBudgetExceeded(f"tool-call budget {max_calls} exhausted")

        # 2. input validation ---------------------------------------------------
        try:
            parsed = args if isinstance(args, spec.input_model) else spec.input_model.model_validate(
                args.model_dump() if isinstance(args, BaseModel) else args)
        except ValidationError as exc:
            self.audit.record("tool.invalid_arguments", run_id=ctx.run_id, tool=name, outcome="rejected",
                              details={"errors": exc.errors(include_url=False, include_input=False)})
            return ToolResult(tool=name, ok=False, error=str(exc), error_type="ToolValidationError")

        # 3. permission gate (deny-by-default for consequential tools) ------------
        if spec.consequential:
            if not ctx.authorization:
                self.audit.record("tool.denied", run_id=ctx.run_id, tool=name, outcome="no_authorization",
                                  details={"reason": "consequential tool called without approval"})
                raise ToolPermissionDenied(f"{name} requires an approved authorization")
            if spec.guard:
                try:
                    spec.guard(parsed, ctx)
                except ToolPermissionDenied as exc:
                    self.audit.record("tool.denied", run_id=ctx.run_id, tool=name, outcome="guard_rejected",
                                      details={"reason": str(exc)})
                    raise

        # 4. execute with timeout + bounded retries --------------------------------
        timeout = float(self.settings.tool_setting(name, "timeout_s"))
        max_attempts = int(self.settings.tool_setting(name, "max_attempts"))
        backoff = float(self.settings.get("tools.backoff_base_s", 0.05))
        last_err: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            t0 = time.perf_counter()
            fut = _EXECUTOR.submit(spec.func, parsed, ctx)
            try:
                raw = fut.result(timeout=timeout)
                output = raw if isinstance(raw, spec.output_model) else spec.output_model.model_validate(raw)
                dur = int((time.perf_counter() - t0) * 1000)
                evidence_id = getattr(output, "_evidence_id", None) or _evidence_id(name, parsed, output)
                self.audit.record("tool.call", run_id=ctx.run_id, tool=name, outcome="ok", attempt=attempt,
                                  duration_ms=dur, details={"args": _summarise_args(parsed), "evidence_id": evidence_id,
                                                            "result": _summarise_output(name, output)})
                return ToolResult(tool=name, ok=True, output=output, attempts=attempt,
                                  duration_ms=int((time.perf_counter() - start) * 1000), evidence_id=evidence_id)
            except cf.TimeoutError:
                fut.cancel()
                last_err = ToolTimeout(f"{name} timed out after {timeout}s")
            except ValidationError as exc:
                last_err = ToolValidationError(f"{name} returned output violating its schema: {exc.error_count()} errors")
            except ToolError as exc:
                last_err = exc
            except Exception as exc:  # unexpected backend error: not retried
                last_err = ToolError(f"{type(exc).__name__}: {exc}")
            dur = int((time.perf_counter() - t0) * 1000)
            retryable = getattr(last_err, "retryable", False)
            self.audit.record("tool.call", run_id=ctx.run_id, tool=name, attempt=attempt, duration_ms=dur,
                              outcome="retrying" if retryable and attempt < max_attempts else "failed",
                              details={"args": _summarise_args(parsed), "error": str(last_err),
                                       "error_type": type(last_err).__name__})
            if not retryable:
                break
            if attempt < max_attempts:
                time.sleep(backoff * (2 ** (attempt - 1)))
        return ToolResult(tool=name, ok=False, error=str(last_err), error_type=type(last_err).__name__,
                          attempts=attempt, duration_ms=int((time.perf_counter() - start) * 1000))


def _summarise_args(args: BaseModel) -> dict:
    d = args.model_dump(mode="json")
    return {k: v for k, v in d.items() if k not in {"query"}} | ({"query": d["query"][:120]} if "query" in d else {})


def _summarise_output(name: str, out: BaseModel) -> dict:
    """Minimum-necessary summary for the audit log (no bank details, no full text)."""
    d = out.model_dump(mode="json")
    if name == "retrieve_finance_documents":
        return {"index_version": d["index_version"], "filtered_by_access": d["filtered_by_access"],
                "chunks": [{"chunk_id": c["chunk_id"], "rank": c["rank"], "score": c["score"], "trust": c["trust"]}
                           for c in d["chunks"]]}
    if name == "get_vendor_record":
        return {"vendor_id": d["vendor_id"], "status": d["status"], "risk_flags": d["risk_flags"],
                "last_updated": d["last_updated"]}
    if name == "get_purchase_order":
        return {"po_ref": d["po_ref"], "currency": d["currency"], "total": d["total"],
                "approval_status": d["approval_status"], "lines": len(d["lines"]), "receipts": len(d["goods_receipts"])}
    if name == "check_invoice_history":
        return {"matches": [{"record_id": m["record_id"], "match_type": m["match_type"], "status": m["status"]}
                            for m in d["matches"]]}
    return d


def _evidence_id(name: str, args: BaseModel, out: BaseModel) -> str:
    d = out.model_dump(mode="json")
    if name == "get_vendor_record":
        return f"vendor:{d['vendor_id']}@{d['last_updated']}"
    if name == "get_purchase_order":
        return f"po:{d['po_ref']}@{d['last_updated']}"
    if name == "check_invoice_history":
        return "history:" + ",".join(m["record_id"] for m in d["matches"]) if d["matches"] else "history:none"
    if name == "get_fx_rate":
        return f"fx:{d['from_currency']}{d['to_currency']}@{d['rate_date']}"
    if name == "get_authority_register":
        return f"authority-register:v{d['version']}"
    if name == "submit_finance_decision":
        return f"erp:{d['posting_ref']}"
    return name
