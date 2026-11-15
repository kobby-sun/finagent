"""finagent CLI - every API operation plus ops commands.

    finagent ingest                         build BM25 index + vectors from rag.sources
    finagent start --case FIN-001           start a run from mock_data/cases/<case>/request.json (or --file)
    finagent get RUN_ID [--audit]           status, current node, result, approval, audit
    finagent explain RUN_ID                 reasoning for every key step (system, skills, model, humans)
    finagent diagram RUN_ID --format pdf png svg   draw the run (path, exit step, tools, skills, decision, approvals, warnings)
    finagent approvals [--status PENDING]   list approval requests (with explanations: --explain)
    finagent approve APPROVAL_ID --approver u-dd-01 [--callback-id X] [--comment ..]
    finagent reject  APPROVAL_ID --approver u-ccm-01 [--callback-id X]
    finagent resume RUN_ID | --all          resume crashed runs (idempotent)
    finagent evidence RUN_ID [--po-ref PO-..] [--file path] [--note ..]
    finagent outcomes [--status HELD]       outcome pool
    finagent audit RUN_ID | finagent audit-verify
    finagent eval [--cases FIN-001 FIN-003] [--json]
    finagent skills list|test [NAME]|coverage|docs
    finagent workflow show|validate [--file path]
    finagent webhooks subscribe URL [--events ..] | deliver | events | listen [--port 9099]
    finagent contracts export [--out contracts]
    finagent policy status|params           does the policy text match the verified baseline; the policy numbers
    finagent policy baseline --reviewer u-fc-01 --note "..."   (last step of a policy maintenance release)
    finagent serve [--host 0.0.0.0 --port 8080]
    finagent demo                           FIN-001 (approval flow) + FIN-003 (exception flow) transcript
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_settings


def _p(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _app():
    from .services import build_app
    return build_app(load_settings())


def _summary(run: dict) -> dict:
    res = run.get("result") or {}
    rec = res.get("recommendation") or {}
    return {"run_id": run["run_id"], "case_id": run["case_id"], "status": run["status"], "current_node": run["current_node"],
            "outcome": rec.get("outcome"), "primary_exception": rec.get("primary_exception"), "confidence": rec.get("confidence"),
            "next_action": rec.get("next_action"), "approval_id": (run.get("approval") or {}).get("approval_id"),
            "approval_status": (run.get("approval") or {}).get("status"), "error": run.get("error")}


def cmd_ingest(a):
    s = load_settings()
    from .rag.index import build_and_save
    from .rag.vectorstore import build_vectors
    r = build_and_save(s.path("rag.corpus_dir"), s.index_path, s.get("rag.bm25_k1", 1.4), s.get("rag.bm25_b", 0.7),
                       sources=s.get("rag.sources"), root=s.root)
    if s.get("rag.mode", "hybrid") != "lexical":
        from .rag.index import BM25Index
        idx = BM25Index.load(s.index_path)
        try:
            r["vectors"] = build_vectors(s, idx.chunks, idx.index_version)
        except (FileNotFoundError, ImportError, OSError) as exc:   # e.g. fresh clone: model not trained yet
            r["vectors"] = {"skipped": f"{type(exc).__name__}: {exc}",
                            "effect": "search falls back to keyword-only (BM25) until the model exists (make retrain)"}
            print(f"[finagent] vectors skipped - {exc}. Keyword search (BM25) will be used.", file=sys.stderr)
    _p(r)


def cmd_start(a):
    from .schemas import ProcessingRequest
    path = Path(a.file) if a.file else load_settings().root / "mock_data" / "cases" / a.case / "request.json"
    req = ProcessingRequest.model_validate(json.loads(path.read_text()))
    app = _app()
    run = app.engine.start_run(req, idempotency_key=a.idempotency_key)
    _p(run if a.full else _summary(run))
    if a.diagram:
        from .rundiagram import write
        for fmt in a.diagram:
            print(f"diagram: {write(app.engine, run['run_id'], fmt)}", file=sys.stderr)


def cmd_get(a):
    run = _app().engine.get_run(a.run_id, include_audit=a.audit)
    _p(run if (a.full or a.audit) else {**_summary(run), "result": run["result"]})


def cmd_explain(a):
    app = _app()
    if app.store.get_run(a.run_id) is None:
        sys.exit(f"run {a.run_id} not found")
    for r in app.engine.reasoning(a.run_id):
        print(f"{r['at'][11:19]}  {r['step']:<16} {r['actor']:<34} {r['decision']}")
        print(f"{'':<10}{'':<16} {'':<34} why: {r['reason']}")
        if r.get("refs"):
            print(f"{'':<10}{'':<16} {'':<34} refs: {', '.join(map(str, r['refs']))}")


def cmd_diagram(a):
    from .rundiagram import write
    app = _app()
    if app.store.get_run(a.run_id) is None:
        sys.exit(f"run {a.run_id} not found")
    for fmt in a.format:
        print(write(app.engine, a.run_id, fmt, a.out if len(a.format) == 1 else None))


def cmd_runs(a):
    _p(_app().store.list_runs(a.status, a.case))


def cmd_approvals(a):
    app = _app()
    rows = [app.engine._approval_view(x) for x in app.store.list_approvals(a.status)]
    if not a.explain:
        rows = [{k: v for k, v in r.items() if k != "explanation"} for r in rows]
    _p(rows)


def _decide(a, decision):
    from .agent.graph import ApprovalError
    from .schemas import ApprovalDecisionIn
    app = _app()
    try:
        code, body = app.engine.decide_approval(a.approval_id, ApprovalDecisionIn(decision=decision, approver_id=a.approver,
                                                comment=a.comment, callback_id=a.callback_id), channel="cli")
    except ApprovalError as exc:
        code, body = exc.http_status, exc.body
    _p({"http_status": code, **body})


def cmd_resume(a):
    app = _app()
    if a.all:
        _p({"resumed": app.engine.resume_all()})
    else:
        _p(_summary(app.engine.resume(a.run_id)))


def cmd_evidence(a):
    from .schemas import Attachment
    atts = []
    for f in a.file or []:
        p = Path(f)
        atts.append(Attachment(filename=p.name, content=p.read_text(), content_type="text/plain", source="internal"))
    _p(_summary(_app().engine.add_evidence(a.run_id, atts, a.note, a.po_ref)))


def cmd_outcomes(a):
    _p(_app().store.list_outcomes(a.status, a.case))


def cmd_audit(a):
    _p(_app().audit.events(a.run_id))


def cmd_audit_verify(a):
    _p(_app().audit.verify_chain())


def cmd_eval(a):
    from .evaluation import format_report, run_evaluation
    rep = run_evaluation(load_settings(), a.cases, diagrams=a.diagrams, history_dir=a.history)
    if a.json:
        _p(rep)
    else:
        print(format_report(rep))
    if rep.get("history_dir"):
        print(f"\ntest history: {rep['history_dir']}\n  open {rep['history_dir']}/index.html  (or index.md)")
    sys.exit(0 if rep["summary"]["failed"] == 0 else 1)


def cmd_skills(a):
    app = _app()
    from .skills.base import REGISTRY
    from .skills.testing import SkillHarness
    if a.action == "list":
        for s in REGISTRY.values():
            print(f"{s.name:<26}{s.kind:<10}v{s.version:<5}{'MANDATORY' if s.mandatory else '':<10}"
                  f"{', '.join(f'{d} {x}' for d, x in s.compliance_refs)}")
    elif a.action == "test":
        res = SkillHarness(app).run_all(a.name)
        for r in res:
            print(f"{'PASS' if r.passed else 'FAIL'}  {r.skill:<26}{r.fixture_id:<34}{r.description}")
            for f in r.failures:
                print(f"        x {f}")
        print(f"\n{sum(r.passed for r in res)}/{len(res)} fixtures passed")
        sys.exit(0 if all(r.passed for r in res) else 1)
    elif a.action == "coverage":
        m = SkillHarness(app).coverage_matrix()
        print("case     " + " ".join(f"{s[:10]:>11}" for s in m["skills"]))
        for r in m["rows"]:
            print(f"{r['case_id']:<9}" + " ".join(f"{r['cells'][s][:11]:>11}" for s in m["skills"]))
    elif a.action == "docs":
        import runpy
        runpy.run_path(str(app.settings.root / "scripts" / "skills" / "build_skill_docs.py"), run_name="__main__")


def cmd_workflow(a):
    from .workflow import WorkflowError, load_pipeline
    s = load_settings()
    try:
        p = load_pipeline(s, a.file)
    except WorkflowError as exc:
        print(str(exc)); sys.exit(2)
    if a.action == "validate":
        print(f"OK: {p.name} v{p.version} digest={p.digest} ({sum(st.enabled for st in p.steps)} enabled skills)")
    else:
        _p({**p.describe(), "digest": p.digest})


def cmd_webhooks(a):
    if a.action == "listen":
        return _listen(a.port)
    app = _app()
    if a.action == "subscribe":
        _p(app.webhooks.subscribe(a.url, a.events or ["*"]))
    elif a.action == "deliver":
        _p(app.webhooks.deliver_pending())
    elif a.action == "events":
        _p([{k: v for k, v in e.items() if k != "payload"} | {"data": e["payload"]["data"]} for e in app.webhooks.feed(a.run_id)])


def _listen(port: int):
    """Tiny local webhook receiver that verifies signatures and pretty-prints events (dev tool)."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from .webhooks import verify
    secret = str(load_settings().get("webhooks.signing_secret"))

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            ok = verify(secret, body, self.headers.get("X-Finagent-Signature"))
            print(f"--- {self.headers.get('X-Finagent-Event')} signature={'valid' if ok else 'INVALID'}")
            print(json.dumps(json.loads(body), indent=2)[:3000])
            self.send_response(200 if ok else 401); self.end_headers()

        def log_message(self, *args):
            pass
    print(f"listening on http://localhost:{port}/hooks")
    HTTPServer(("0.0.0.0", port), H).serve_forever()


def cmd_contracts(a):
    from .contracts import export_json_schemas
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, schema in export_json_schemas().items():
        (out / name).write_text(json.dumps(schema, indent=2) + "\n")
        print(out / name)


def cmd_policy(a):
    """Policy changes are maintenance releases (docs/governance.md); this only reports and re-baselines."""
    from .config import load_settings as _ls
    from .governance.baseline import PolicyBaseline
    from .governance.params import CATALOG
    from .rag.retriever import Retriever
    settings = _ls()
    pb = PolicyBaseline(settings, Retriever(settings))
    if a.action == "status":
        _p(pb.status())
    elif a.action == "params":
        for n, s in CATALOG.items():
            print(f"{n:<38} {str(pb.params[n]):<28} {s.cite or 'interpretation'}")
    elif a.action == "baseline":
        if not a.reviewer or not a.note:
            sys.exit("baseline needs --reviewer and --note (it records that the controls were checked against every current section)")
        from .services import build_app
        app = build_app(settings)                       # audit trail + authority register
        try:
            print(app.policy.rebaseline(a.reviewer, a.note, app.backend.authority_register()["approvers"], app.audit))
        except PermissionError as exc:
            sys.exit(f"refused (recorded in the audit trail): {exc}")
        print("recorded in the audit trail as policy.baseline_recorded")


def cmd_serve(a):
    import uvicorn
    uvicorn.run("finagent.api.app:get_app", factory=True, host=a.host, port=a.port, log_level="info")


def cmd_demo(a):
    from .schemas import ApprovalDecisionIn, ProcessingRequest
    app = _app()
    root = app.settings.root
    print("=== FIN-001: valid three-way match -> approval -> exactly-once submission ===")
    req = ProcessingRequest.model_validate(json.loads((root / "mock_data/cases/FIN-001/request.json").read_text()))
    run = app.engine.start_run(req)
    _p(_summary(run))
    apr = run["approval"]
    print("\napproval explanation (abridged):")
    _p({k: apr["explanation"][k] for k in ("summary", "amount", "amount_aud", "required_approvals", "citations", "key_calculations")})
    for user, cb in (("u-ccm-01", "demo-cb-1"), ("u-dd-01", "demo-cb-2"), ("u-dd-01", "demo-cb-2")):
        code, body = app.engine.decide_approval(apr["approval_id"], ApprovalDecisionIn(decision="APPROVE", approver_id=user, callback_id=cb))
        print(f"\n> approve as {user} (callback {cb}) -> HTTP {code}")
        _p({k: body.get(k) for k in ("status", "effect", "replayed", "denied_reasons", "run")})
    print("\n=== FIN-003: poisoned supplier attachment -> escalation, no approval, no payment ===")
    req = ProcessingRequest.model_validate(json.loads((root / "mock_data/cases/FIN-003/request.json").read_text()))
    run = app.engine.start_run(req)
    _p(_summary(run))
    res = run["result"]
    _p({"risk_indicators": res["risk_indicators"], "exceptions": [{k: e[k] for k in ("category", "failed_rule", "observed", "owner")}
                                                                  for e in res["exceptions"]],
        "model_guard": [e["details"] for e in run["audit_events"] if e["event_type"] == "model.override_blocked"]})


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="finagent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest").set_defaults(fn=cmd_ingest)
    p = sub.add_parser("start"); g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--case"); g.add_argument("--file"); p.add_argument("--idempotency-key"); p.add_argument("--full", action="store_true")
    p.add_argument("--diagram", nargs="+", choices=["svg", "pdf", "png"], help="also draw a run diagram (data/diagrams/)")
    p.set_defaults(fn=cmd_start)
    p = sub.add_parser("get"); p.add_argument("run_id"); p.add_argument("--audit", action="store_true"); p.add_argument("--full", action="store_true")
    p.set_defaults(fn=cmd_get)
    p = sub.add_parser("diagram"); p.add_argument("run_id"); p.add_argument("--format", nargs="+", default=["pdf"], choices=["svg", "pdf", "png"])
    p.add_argument("--out"); p.set_defaults(fn=cmd_diagram)
    p = sub.add_parser("explain"); p.add_argument("run_id"); p.set_defaults(fn=cmd_explain)
    p = sub.add_parser("runs"); p.add_argument("--status"); p.add_argument("--case"); p.set_defaults(fn=cmd_runs)
    p = sub.add_parser("approvals"); p.add_argument("--status"); p.add_argument("--explain", action="store_true"); p.set_defaults(fn=cmd_approvals)
    for name, dec in (("approve", "APPROVE"), ("reject", "REJECT")):
        p = sub.add_parser(name); p.add_argument("approval_id"); p.add_argument("--approver", required=True)
        p.add_argument("--comment", required=(dec == "REJECT"), help="reason (required for reject)")
        p.add_argument("--callback-id"); p.set_defaults(fn=lambda a, d=dec: _decide(a, d))
    p = sub.add_parser("resume"); p.add_argument("run_id", nargs="?"); p.add_argument("--all", action="store_true"); p.set_defaults(fn=cmd_resume)
    p = sub.add_parser("evidence"); p.add_argument("run_id"); p.add_argument("--po-ref"); p.add_argument("--file", action="append")
    p.add_argument("--note"); p.set_defaults(fn=cmd_evidence)
    p = sub.add_parser("outcomes"); p.add_argument("--status"); p.add_argument("--case"); p.set_defaults(fn=cmd_outcomes)
    p = sub.add_parser("audit"); p.add_argument("run_id"); p.set_defaults(fn=cmd_audit)
    sub.add_parser("audit-verify").set_defaults(fn=cmd_audit_verify)
    p = sub.add_parser("eval"); p.add_argument("--cases", nargs="*"); p.add_argument("--json", action="store_true")
    p.add_argument("--diagrams", nargs="+", choices=["svg", "pdf", "png"], help="draw each scenario's final run (data/eval/diagrams/)")
    p.add_argument("--history", nargs="?", const="test_history", default=None, metavar="DIR",
                   help="export every scenario's inputs, outputs, reasoning, approvals and diagrams to DIR/<stamp>_<eval_id>/"),; p.set_defaults(fn=cmd_eval)
    p = sub.add_parser("skills"); p.add_argument("action", choices=["list", "test", "coverage", "docs"]); p.add_argument("name", nargs="?")
    p.set_defaults(fn=cmd_skills)
    p = sub.add_parser("workflow"); p.add_argument("action", choices=["show", "validate"]); p.add_argument("--file"); p.set_defaults(fn=cmd_workflow)
    p = sub.add_parser("webhooks"); p.add_argument("action", choices=["subscribe", "deliver", "events", "listen"])
    p.add_argument("url", nargs="?"); p.add_argument("--events", nargs="*"); p.add_argument("--run-id"); p.add_argument("--port", type=int, default=9099)
    p.set_defaults(fn=cmd_webhooks)
    p = sub.add_parser("contracts"); p.add_argument("action", choices=["export"]); p.add_argument("--out", default="contracts")
    p.set_defaults(fn=cmd_contracts)
    p = sub.add_parser("policy", help="policy baseline check and policy numbers (changes are maintenance releases)")
    p.add_argument("action", choices=["status", "params", "baseline"])
    p.add_argument("--reviewer"); p.add_argument("--note"); p.set_defaults(fn=cmd_policy)
    p = sub.add_parser("serve"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8080)
    p.set_defaults(fn=cmd_serve)
    sub.add_parser("demo").set_defaults(fn=cmd_demo)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
