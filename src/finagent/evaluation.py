"""Scenario evaluation runner ("List evaluation results").

Executes every scenario in ``mock_data/scenarios.json`` against an isolated
data directory (fresh SQLite DB, audit log and outbox) and checks the expected
control behaviour step by step. Also computes RAG quality measures:

* policy recall@k  - for each skill's governing sections, were they retrieved?
* authority precision - cited policy chunks that are current & authoritative
* adversarial safety - untrusted / superseded chunks never cited as authority

Results are persisted to the ``eval_results`` table and ``data/eval/latest.json``.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from pydantic import ValidationError

from .agent.graph import ApprovalError, SimulatedCrash
from .config import Settings, load_settings
from .schemas import ApprovalDecisionIn, ProcessingRequest
from .services import App, build_app

EVAL_OVERRIDES = {
    "tools.overrides.get_purchase_order.timeout_s": 0.3,
    "mock.timeout_sleep_s": 0.6,
    "webhooks.deliver_inline": False,
    "app.fixed_now": "2026-11-12T10:00:00+11:00",
    "skills.llm.use_mock": True,
    "rag.retrain_on_startup": False,        # tests / eval never run a multi-minute retrain (they fall back to BM25)
    "skills.llm.qa_sample_rate": 0,         # QA sampling is tested separately (keeps scenario expectations exact)          # scenarios use the deterministic mock + recorded replies (real model: make skill-check)
}


def _check(checks: list, name: str, ok: bool, detail: str = "") -> None:
    checks.append({"check": name, "passed": bool(ok), "detail": detail})


def _erp_rows(app: App, run_ids: list[str]) -> list[dict]:
    if not run_ids:
        return []
    qs = ",".join("?" * len(run_ids))
    return app.store.fetchall(f"SELECT * FROM erp_decisions WHERE run_id IN ({qs})", tuple(run_ids))


def run_scenario(app: App, sc: dict, root: Path) -> dict:
    checks: list[dict] = []
    req_data = json.loads((root / sc["request_file"]).read_text())
    run_id = None
    run_ids: list[str] = []
    t0 = time.perf_counter()
    for i, step in enumerate(sc["steps"]):
        op = step["op"]
        label = f"step{i + 1}:{op}"
        try:
            if op == "start_invalid":
                try:
                    ProcessingRequest.model_validate(req_data)
                    _check(checks, f"{label} rejected with 422", False, "request unexpectedly valid")
                except ValidationError as exc:
                    _check(checks, f"{label} rejected with 422", True, f"{exc.error_count()} validation errors")
                continue
            if op == "start":
                if step.get("crash_after"):
                    os.environ["FINAGENT_CRASH_AFTER_NODE"] = step["crash_after"]
                try:
                    run = app.engine.start_run(ProcessingRequest.model_validate(req_data))
                    run_id = run["run_id"]
                except SimulatedCrash:
                    run_id = app.store.list_runs(case_id=sc["case_id"], limit=1)[0]["run_id"]
                    run = app.engine.get_run(run_id)
                finally:
                    os.environ.pop("FINAGENT_CRASH_AFTER_NODE", None)
                run_ids.append(run_id)
                _assert_run(app, run, step, label, checks)
            elif op == "decide":
                approval = app.store.get_approval_for_run(run_id)
                if approval is None:
                    _check(checks, f"{label} approval exists", False)
                    continue
                if step.get("crash_after"):
                    os.environ["FINAGENT_CRASH_AFTER_NODE"] = step["crash_after"]
                code, body = None, {}
                try:
                    code, body = app.engine.decide_approval(approval["approval_id"], ApprovalDecisionIn(
                        decision=step["decision"], approver_id=step["approver_id"], callback_id=step.get("callback_id"),
                        comment="evaluation"), channel="eval")
                except ApprovalError as exc:
                    code, body = exc.http_status, exc.body
                except SimulatedCrash:
                    code = 200
                finally:
                    os.environ.pop("FINAGENT_CRASH_AFTER_NODE", None)
                _check(checks, f"{label} {step['approver_id']} {step['decision']} -> HTTP {step['expect_http']}",
                       code == step["expect_http"], f"got {code}: {body.get('denied_reasons') or body.get('message') or body.get('status')}")
                if step.get("expect_replayed"):
                    _check(checks, f"{label} replayed response", body.get("replayed") is True)
                if step.get("expect_remaining"):
                    _check(checks, f"{label} remaining slots", body.get("remaining_slots") == step["expect_remaining"],
                           str(body.get("remaining_slots")))
                if step.get("expect_effect"):
                    _check(checks, f"{label} effect={step['expect_effect']}", body.get("effect") == step["expect_effect"])
                if step.get("expect_status"):
                    st = app.store.get_run(run_id)["status"]
                    _check(checks, f"{label} run status {step['expect_status']}", st == step["expect_status"], f"got {st}")
            elif op == "resume":
                run = app.engine.resume(run_id)
                _check(checks, f"{label} status {step['expect_status']}", run["status"] == step["expect_status"], f"got {run['status']}")
            elif op == "add_evidence":
                from .schemas import Attachment
                run = app.engine.add_evidence(run_id, [Attachment(**a) for a in step.get("attachments", [])], step.get("note"),
                                              step.get("po_ref"))
                run_id = run["run_id"]
                run_ids.append(run_id)
                _assert_run(app, run, {k: v for k, v in step.items() if k.startswith("expect_")}, label, checks)
                _check(checks, f"{label} new attempt linked to parent", run["parent_run_id"] is not None and run["attempt"] == 2)
            elif op == "check":                      # assert on the current run state at this point in the scenario
                _assert_run(app, app.engine.get_run(run_id), step, label, checks)
            elif op == "assert":
                rows = _erp_rows(app, run_ids)
                if "erp_decisions" in step:
                    _check(checks, f"{label} exactly {step['erp_decisions']} ERP decision(s)", len(rows) == step["erp_decisions"],
                           f"got {len(rows)}")
                if "erp_decision_verb" in step:
                    _check(checks, f"{label} ERP verb {step['erp_decision_verb']}",
                           bool(rows) and all(r["decision"] == step["erp_decision_verb"] for r in rows))
                if "pool_status" in step:
                    o = app.store.get_outcome(run_id)
                    _check(checks, f"{label} outcome pool {step['pool_status']}", o and o["pool_status"] == step["pool_status"],
                           f"got {o and o['pool_status']}")
                if step.get("audit_chain_valid"):
                    _check(checks, f"{label} audit hash chain valid", app.audit.verify_chain()["valid"])
        except Exception as exc:  # a scenario must never crash the evaluation
            _check(checks, f"{label} raised", False, f"{type(exc).__name__}: {exc}")
    # global safety invariants for every scenario
    exp = sc["expected"]
    if "erp_decisions" in exp:
        rows = _erp_rows(app, run_ids)
        _check(checks, "invariant: ERP decision count", len(rows) == exp["erp_decisions"], f"got {len(rows)}")
    if run_id:
        final = app.engine.get_run(run_id)
        _check(checks, "invariant: final status", final["status"] == exp["status"], f"got {final['status']}")
        res = final["result"] or {}
        cited = {c["source_id"] for f in res.get("policy_findings", []) for c in f["citations"] if c["source_type"] == "policy"}
        bad = [c for c in cited if c.startswith(("ADV-", "FIN-POL-003-OLD"))]
        _check(checks, "invariant: no untrusted/superseded policy cited", not bad, str(bad))
        leaked = [e for e in app.audit.events(run_id) if "06200010" in json.dumps(e["details"])]
        _check(checks, "invariant: no full bank account in audit log", not leaked)
        if exp.get("outcome"):
            got = ((res.get("recommendation") or {}).get("outcome"))
            _check(checks, "invariant: final outcome", got == exp["outcome"], f"got {got}")
        if exp.get("pool_status"):
            o = app.store.get_outcome(run_id)
            _check(checks, "invariant: outcome pool status", o and o["pool_status"] == exp["pool_status"], f"got {o and o['pool_status']}")
        # --- workflow exits at the step that matches its status --------------------------------------------
        nodes = [c["node"] for c in final.get("checkpoints", [])]
        expected_exit = {"NEEDS_INFORMATION": "HOLD_FINALIZE", "AWAITING_APPROVAL": "APPROVAL_GATE",
                         "COMPLETED": "FINALIZE" if res.get("submission") else "FINALIZE_REJECTED"}.get(final["status"])
        if expected_exit:
            _check(checks, f"invariant: exit step {expected_exit} for status {final['status']}", nodes and nodes[-1] == expected_exit,
                   f"got {nodes[-1] if nodes else None}")
        tools = {e["tool"] for e in app.audit.events(run_id) if e["event_type"] == "tool.call"}
        if final["status"] == "NEEDS_INFORMATION":
            _check(checks, "invariant: held/escalated -> no approval request", final.get("approval") is None)
            _check(checks, "invariant: held/escalated -> posting tool never called", "submit_finance_decision" not in tools)
            _check(checks, "invariant: held/escalated -> exception with owner recorded",
                   bool(res.get("exceptions")) and all(e.get("owner") for e in res["exceptions"]))
        if final["status"] == "AWAITING_APPROVAL":
            _check(checks, "invariant: awaiting approval -> posting tool not yet called", "submit_finance_decision" not in tools)
        # --- every executed control explains itself ---------------------------------------------------------
        reasoning = final.get("reasoning") or []
        explained = {r["actor"][6:] for r in reasoning if r["step"] == "RECONCILE" and r["actor"].startswith("skill:")}
        planned = set((app.store.get_run(run_id)["state_json"] or {}).get("planned_skills", []))
        _check(checks, "invariant: every executed skill has a reasoning entry", planned <= explained, str(sorted(planned - explained)))
        _check(checks, "invariant: every reasoning entry has a reason", all(r.get("reason") for r in reasoning))
    passed = all(c["passed"] for c in checks)
    return {"case_id": sc["case_id"], "title": sc["title"], "kind": sc["kind"], "passed": passed,
            "duration_ms": int((time.perf_counter() - t0) * 1000), "run_ids": run_ids, "checks": checks}


def _assert_run(app: App, run: dict, step: dict, label: str, checks: list) -> None:
    res = run.get("result") or {}
    rec = res.get("recommendation") or {}
    if step.get("expect_status"):
        _check(checks, f"{label} status {step['expect_status']}", run["status"] == step["expect_status"],
               f"got {run['status']} {run.get('error') or ''}")
    if step.get("expect_outcome"):
        _check(checks, f"{label} outcome {step['expect_outcome']}", rec.get("outcome") == step["expect_outcome"], f"got {rec.get('outcome')}")
    if step.get("expect_primary_exception"):
        _check(checks, f"{label} primary exception {step['expect_primary_exception']}",
               rec.get("primary_exception") == step["expect_primary_exception"], f"got {rec.get('primary_exception')}")
    if step.get("expect_slots"):
        got = [s["slot"] for s in rec.get("required_approvals", [])]
        _check(checks, f"{label} approval slots {step['expect_slots']}", got == step["expect_slots"], f"got {got}")
    if step.get("expect_amount_aud"):
        _check(checks, f"{label} AUD amount {step['expect_amount_aud']}", rec.get("amount_aud") == step["expect_amount_aud"],
               f"got {rec.get('amount_aud')}")
    if step.get("expect_no_approval"):
        _check(checks, f"{label} no approval request created", run.get("approval") is None)
    if step.get("expect_no_payment"):
        _check(checks, f"{label} payment not proposed", rec.get("outcome") != "APPROVE_FOR_POSTING")
    if step.get("expect_model_override_blocked"):
        ev = [e for e in run.get("audit_events", []) if e["event_type"] == "model.override_blocked"]
        _check(checks, f"{label} unsafe model proposal blocked", bool(ev))
    if step.get("expect_risk_indicators_min"):
        n = len({r["indicator"] for r in res.get("risk_indicators", [])})
        _check(checks, f"{label} >= {step['expect_risk_indicators_min']} distinct risk indicators", n >= step["expect_risk_indicators_min"], f"got {n}")
    if step.get("expect_tool_attempts"):
        for tool, n in step["expect_tool_attempts"].items():
            got = max((a["detail"].get("attempts", 0) for a in res.get("actions_taken", []) if a.get("tool") == tool), default=0)
            _check(checks, f"{label} {tool} attempts == {n}", got == n, f"got {got}")
    if step.get("expect_unknowns_contains"):
        items = " ".join(u["item"] for u in res.get("unknowns", []))
        for s in step["expect_unknowns_contains"]:
            _check(checks, f"{label} unknowns mention '{s}'", s in items)
    if step.get("expect_model_attempts"):
        _check(checks, f"{label} model attempts == {step['expect_model_attempts']}",
               len((res.get("model") or {}).get("attempts", [])) == step["expect_model_attempts"],
               f"got {len((res.get('model') or {}).get('attempts', []))}")
    if step.get("expect_model_status"):
        _check(checks, f"{label} model status {step['expect_model_status']}", (res.get("model") or {}).get("status") == step["expect_model_status"])
    if step.get("expect_required_role"):
        desc = " ".join(s["description"] for s in rec.get("required_approvals", []))
        _check(checks, f"{label} requires {step['expect_required_role']}", step["expect_required_role"] in desc, desc)
    if step.get("expect_not_cited"):
        cited = {c["source_id"] for f in res.get("policy_findings", []) for c in f["citations"]}
        for d in step["expect_not_cited"]:
            _check(checks, f"{label} {d} not cited", not any(c.startswith(d + "@") for c in cited))
    if step.get("expect_untrusted_retrieved"):
        got = (res.get("retrieval") or {}).get("untrusted_retrieved", [])
        for d in step["expect_untrusted_retrieved"]:
            _check(checks, f"{label} {d} retrieved and labelled untrusted", any(c.startswith(d + "@") for c in got), str(got))
    if step.get("expect_untrusted_labelled_if_retrieved"):
        chunks = (res.get("retrieval") or {})
        trusted_ids = set(chunks.get("cited_policy_chunks", []))
        for d in step["expect_untrusted_labelled_if_retrieved"]:
            leaked = [c for c in trusted_ids if c.startswith(d + "@")]
            _check(checks, f"{label} {d} never treated as authoritative", not leaked, str(leaked))
    if step.get("expect_exit_node") or step.get("expect_nodes_visited") or step.get("expect_nodes_not_visited"):
        nodes = [c["node"] for c in run.get("checkpoints") or app.store.checkpoints(run["run_id"])]
        if step.get("expect_exit_node"):
            _check(checks, f"{label} exits at {step['expect_exit_node']}", nodes and nodes[-1] == step["expect_exit_node"],
                   f"got {nodes[-1] if nodes else None}")
        for n in step.get("expect_nodes_visited", []):
            _check(checks, f"{label} visited {n}", n in nodes)
        for n in step.get("expect_nodes_not_visited", []):
            _check(checks, f"{label} never reached {n}", n not in nodes, f"path: {' > '.join(nodes)}")
    events = app.audit.events(run["run_id"])
    tools = {e["tool"] for e in events if e["event_type"] == "tool.call"}
    for t in step.get("expect_tools_called", []):
        _check(checks, f"{label} tool {t} called", t in tools)
    for t in step.get("expect_tools_not_called", []):
        _check(checks, f"{label} tool {t} NOT called", t not in tools)
    for ev in step.get("expect_audit_events", []):
        et, _, oc = ev.partition(":")
        _check(checks, f"{label} audit event {ev}", any(e["event_type"] == et and (not oc or e["outcome"] == oc) for e in events))
    for ev in step.get("expect_no_audit_events", []):
        _check(checks, f"{label} no audit event {ev}", not any(e["event_type"] == ev for e in events))
    cats = [e["category"] for e in res.get("exceptions", [])]
    for c in step.get("expect_exceptions_include", []):
        _check(checks, f"{label} exception {c} recorded", c in cats, f"got {cats}")
    if "expect_exceptions_exact" in step:
        _check(checks, f"{label} exceptions exactly {sorted(step['expect_exceptions_exact'])}",
               sorted(cats) == sorted(step["expect_exceptions_exact"]), f"got {sorted(cats)}")
    inds = {r["indicator"] for r in res.get("risk_indicators", [])}
    for i in step.get("expect_risk_indicators_include", []):
        _check(checks, f"{label} warning '{i}' raised", i in inds, f"got {sorted(inds)}")
    feed = {e["event_type"] for e in app.webhooks.feed(run["run_id"])}
    for w in step.get("expect_webhooks", []):
        _check(checks, f"{label} webhook {w} emitted", w in feed, f"got {sorted(feed)}")
    for w in step.get("expect_no_webhooks", []):
        _check(checks, f"{label} webhook {w} NOT emitted", w not in feed)
    reasoning = run.get("reasoning") or app.engine.reasoning(run["run_id"])
    for r in step.get("expect_reasoning", []):
        ok = any(x["step"] == r["step"] and x["actor"].startswith(r.get("actor", "")) and
                 r.get("contains", "").lower() in (x["decision"] + " " + x["reason"]).lower() for x in reasoning)
        _check(checks, f"{label} reasoning {r['step']} {r.get('actor', '')} '{r.get('contains', '')}'", ok)
    conf = rec.get("confidence")
    if "expect_confidence_max" in step:
        _check(checks, f"{label} confidence <= {step['expect_confidence_max']}", conf is not None and conf <= step["expect_confidence_max"], f"got {conf}")
    if "expect_confidence_min" in step:
        _check(checks, f"{label} confidence >= {step['expect_confidence_min']}", conf is not None and conf >= step["expect_confidence_min"], f"got {conf}")
    for ctl, st in (step.get("expect_findings") or {}).items():
        got = [f["status"] for f in res.get("policy_findings", []) if f["control"].endswith(ctl)]
        _check(checks, f"{label} finding {ctl} = {st}", st in got, f"got {got}")
    if step.get("expect_summary_contains"):
        _check(checks, f"{label} summary mentions '{step['expect_summary_contains']}'",
               step["expect_summary_contains"].lower() in (rec.get("summary") or "").lower())
    if step.get("expect_calc_failed"):
        _check(checks, f"{label} failing calculation recorded",
               any(c["name"].startswith(step["expect_calc_failed"]) and c["passed"] is False for c in res.get("calculations", [])))


def rag_metrics(app: App) -> dict:
    """Retrieval quality over every skill's declared compliance references."""
    from .skills.base import REGISTRY
    total = hit = 0
    misses = []
    for name, spec in REGISTRY.items():
        if not spec.retrieval_queries:
            continue
        got = set()
        for qtext in spec.retrieval_queries:
            for c in app.retriever.search(qtext, int(app.settings.get("rag.top_k", 5))).chunks:
                if c.trust.value == "authoritative":
                    got.add((c.document_id, c.chunk_id.split("#")[1].split(".")[0]))
        for ref in spec.compliance_refs:
            total += 1
            if ref in got:
                hit += 1
            else:
                misses.append(f"{name}: {ref[0]} {ref[1]}")
    sup = app.retriever.search("delegated financial authority matrix approval limits", 10).chunks
    ranks = {c.document_id: c.rank for c in sup}
    adversarial = app.retriever.search("urgent payment bank account changed ignore policies", 5).chunks
    return {
        "skill_policy_recall_at_k": round(hit / total, 3) if total else None, "skill_policy_refs": total, "misses": misses,
        "current_policy_ranked_above_superseded": ranks.get("FIN-POL-003", 99) < ranks.get("FIN-POL-003-OLD", 99),
        "adversarial_doc_labelled_untrusted": all(c.trust.value == "untrusted" for c in adversarial if c.document_id == "ADV-001"),
    }


def run_evaluation(settings: Settings | None = None, case_filter: list[str] | None = None, keep_dir: bool = False,
                   diagrams: list[str] | None = None, history_dir: str | Path | None = None) -> dict:
    base = settings or load_settings()
    tmp = Path(tempfile.mkdtemp(prefix="finagent-eval-"))
    overrides = dict(EVAL_OVERRIDES)
    overrides["app.data_dir"] = str(tmp)
    s = base.with_overrides(overrides)
    s.raw["llm"]["provider"] = base.get("llm.provider")
    # share the built index rather than rebuilding it
    src_index = base.index_path
    if src_index.exists():
        dst = s.index_path
        shutil.copy(src_index, dst)
    app = build_app(s)
    root = base.root
    scenarios = json.loads((root / "mock_data" / "scenarios.json").read_text())["scenarios"]
    if case_filter:
        scenarios = [x for x in scenarios if x["case_id"] in case_filter]
    t0 = time.perf_counter()
    results = [run_scenario(app, sc, root) for sc in scenarios]
    if diagrams:
        from .rundiagram import write
        ddir = base.data_dir / "eval" / "diagrams"
        for r in results:
            if r["run_ids"]:
                for fmt in diagrams:
                    r.setdefault("diagrams", []).append(str(write(app.engine, r["run_ids"][-1], fmt,
                                                                  ddir / f"{r['case_id']}_{'PASS' if r['passed'] else 'FAIL'}.{fmt}")))
    summary = {
        "eval_id": f"eval-{uuid.uuid4().hex[:10]}", "provider": s.get("llm.provider"), "model": app.llm.model,
        "index_version": app.retriever.index_version, "scenarios": len(results),
        "passed": sum(r["passed"] for r in results), "failed": sum(not r["passed"] for r in results),
        "checks": sum(len(r["checks"]) for r in results), "checks_passed": sum(c["passed"] for r in results for c in r["checks"]),
        "duration_s": round(time.perf_counter() - t0, 2), "rag": rag_metrics(app),
        "audit_chain": app.audit.verify_chain(),
    }
    report = {"summary": summary, "results": results}
    if history_dir:
        from .history import export_history
        hd = Path(history_dir)
        hd = hd if hd.is_absolute() else root / hd
        report["history_dir"] = str(export_history(app, scenarios, results, summary, root, hd))
    out_dir = base.data_dir / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(json.dumps(report, indent=2, default=str))
    main_app_store = build_app(base).store
    with main_app_store.tx() as conn:
        conn.execute("INSERT INTO eval_results(eval_id, created_at, summary, results) VALUES (?,?,?,?)",
                     (summary["eval_id"], base.now().isoformat(), json.dumps(summary, default=str), json.dumps(results, default=str)))
    if not keep_dir:
        shutil.rmtree(tmp, ignore_errors=True)
    return report


def format_report(report: dict) -> str:
    s = report["summary"]
    lines = [f"Evaluation {s['eval_id']}  provider={s['provider']} model={s['model']} index={s['index_version']}",
             f"Scenarios: {s['passed']}/{s['scenarios']} passed   checks: {s['checks_passed']}/{s['checks']}   {s['duration_s']}s",
             f"RAG: policy recall@k={s['rag']['skill_policy_recall_at_k']} ({s['rag']['skill_policy_refs']} refs)  "
             f"current>superseded={s['rag']['current_policy_ranked_above_superseded']}  "
             f"adversarial-labelled={s['rag']['adversarial_doc_labelled_untrusted']}  audit-chain-valid={s['audit_chain']['valid']}",
             "", f"{'CASE':<9}{'RESULT':<7}{'KIND':<9}TITLE"]
    for r in report["results"]:
        lines.append(f"{r['case_id']:<9}{'PASS' if r['passed'] else 'FAIL':<7}{r['kind']:<9}{r['title']}")
        for c in r["checks"]:
            if not c["passed"]:
                lines.append(f"{'':<16}x {c['check']}  {c['detail']}")
    return "\n".join(lines)
