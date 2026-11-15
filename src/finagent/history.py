"""Test-history export: every scenario's inputs, outputs, reasoning, approvals and diagrams in one dated folder.

    finagent eval --history                     -> test_history/<timestamp>_<eval_id>/
    make test-history | .\\dev.ps1 test-history

Layout
------
test_history/<stamp>_<eval_id>/
  index.md, index.html, summary.json, environment.json
  <CASE>_<PASS|FAIL>/
    README.md                 what was tested, expected vs actual, exit step, decisions, failed checks
    checks.json               every check with pass/fail and detail
    input/                    request.json, scenario.json (expectations + steps), attachments/*
    output/<run_id>/          one folder per run attempt
      result.json             typed FinalResult (facts, calculations, findings, exceptions, unknowns, actions)
      reasoning.md / .json    every key step: actor (system / skill / AI / human), decision, reason
      approval.json / .md     approval request: explanation, required approvals, every decision and refusal
      audit.jsonl             hash-chained, redacted audit events for this run
      checkpoints.json        path through the workflow (exit step = last)
      webhooks.json           events emitted
      outcome.json            outcome-pool entry;  erp_decisions.json  simulated postings
      diagram.png / .pdf / .svg
"""
from __future__ import annotations

import html
import json
import shutil
from datetime import datetime
from pathlib import Path

from .rundiagram import render


def _w(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, (bytes, bytearray)):
        path.write_bytes(obj)
    elif isinstance(obj, str):
        path.write_text(obj, encoding="utf-8")
    else:
        path.write_text(json.dumps(obj, indent=2, default=str) + "\n", encoding="utf-8")


def _reasoning_md(case_id: str, run: dict) -> str:
    rec = (run.get("result") or {}).get("recommendation") or {}
    lines = [f"# Reasoning - {case_id} - {run['run_id']}", "",
             f"**Status:** {run['status']}  |  **Outcome:** {rec.get('outcome')}  |  **Primary exception:** "
             f"{rec.get('primary_exception') or '-'}  |  **Confidence:** {rec.get('confidence')}", "",
             f"**Next action:** {rec.get('next_action') or '-'}", "",
             "| # | Step | Actor | Decision | Why | Refs |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(run.get("reasoning") or [], 1):
        esc = lambda t: str(t).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {i} | {r['step']} | {esc(r['actor'])} | {esc(r['decision'])} | {esc(r['reason'])} | "
                     f"{esc(', '.join(map(str, r.get('refs') or []))[:160])} |")
    return "\n".join(lines) + "\n"


def _approval_md(approval: dict, reasoning: list[dict]) -> str:
    e = approval["explanation"]
    out = [f"# Approval {approval['approval_id']} - {approval['status']}", "",
           f"- **Case:** {approval['case_id']}  **Proposed:** {approval['proposed_outcome']} ({approval['decision_type']})",
           f"- **Amount:** {approval['currency']} {approval['amount']} (AUD {approval['amount_aud']})",
           f"- **What approving does:** {e.get('what_approval_does')}", f"- **Summary shown to approver:** {e.get('summary')}", "",
           "## Required approvals", ""] + [f"- `{s['slot']}`: {s['description']}" for s in approval["required_slots"]]
    out += ["", "## Exceptions shown to the approver", ""] + \
           ([f"- **{x['category']}** - {x['failed_rule']} (expected {x['expected']}, observed {x['observed']}) -> {x['owner']}"
             for x in e.get("exceptions", [])] or ["- none"])
    out += ["", "## Citations", ""] + [f"- `{c}`" for c in e.get("citations", [])]
    out += ["", "## Every human decision (incl. refusals, rejections and duplicates)", "",
            "| Approver | Decision | Reason / detail |", "|---|---|---|"]
    humans = [r for r in reasoning if r["step"] == "HUMAN_DECISION"]
    out += [f"| {r['actor'].replace('human:', '')} | {r['decision']} | {str(r['reason']).replace('|', '/')} |" for r in humans] or \
           ["| - | no decision yet | - |"]
    return "\n".join(out) + "\n"


def export_scenario(app, sc: dict, result: dict, root: Path, case_dir: Path, formats=("png", "pdf", "svg")) -> dict:
    """Write one scenario's inputs and outputs. Returns a row for the index."""
    eng = app.engine
    # ---- inputs
    case_src = root / "mock_data" / "cases" / sc["case_id"]
    (case_dir / "input").mkdir(parents=True, exist_ok=True)
    if case_src.exists():
        shutil.copy(case_src / "request.json", case_dir / "input" / "request.json")
        if (case_src / "attachments").exists() and any((case_src / "attachments").iterdir()):
            shutil.copytree(case_src / "attachments", case_dir / "input" / "attachments", dirs_exist_ok=True)
    _w(case_dir / "input" / "scenario.json", {k: sc[k] for k in ("case_id", "title", "kind", "signals", "expected", "steps",
                                                                "policies", "input_format") if k in sc})
    _w(case_dir / "checks.json", result["checks"])
    # ---- outputs per run attempt
    runs_info = []
    for rid in result.get("run_ids", []):
        run = eng.get_run(rid)
        run["state"] = (app.store.get_run(rid) or {}).get("state_json") or {}
        od = case_dir / "output" / rid
        res = run.get("result") or {}
        _w(od / "result.json", res)
        _w(od / "reasoning.json", run.get("reasoning") or [])
        _w(od / "reasoning.md", _reasoning_md(sc["case_id"], run))
        _w(od / "checkpoints.json", run.get("checkpoints") or [])
        _w(od / "audit.jsonl", "".join(json.dumps(e, default=str) + "\n" for e in run.get("audit_events") or []))
        _w(od / "webhooks.json", app.webhooks.feed(rid))
        _w(od / "outcome.json", app.store.get_outcome(rid) or {})
        _w(od / "erp_decisions.json", app.store.fetchall("SELECT * FROM erp_decisions WHERE run_id=?", (rid,)))
        appr = app.store.get_approval_for_run(rid)
        if appr:
            _w(od / "approval.json", eng._approval_view(appr))
            _w(od / "approval.md", _approval_md(appr, run.get("reasoning") or []))
        for fmt in formats:
            try:
                _w(od / f"diagram.{fmt}", render(run, fmt))
            except Exception as exc:  # pragma: no cover - a diagram must never break the export
                _w(od / f"diagram.{fmt}.error.txt", str(exc))
        rec = res.get("recommendation") or {}
        nodes = [c["node"] for c in run.get("checkpoints") or []]
        runs_info.append({"run_id": rid, "attempt": run.get("attempt"), "status": run["status"], "outcome": rec.get("outcome"),
                          "primary_exception": rec.get("primary_exception"), "exit_step": nodes[-1] if nodes else None,
                          "posting": (res.get("submission") or {}).get("posting_ref"),
                          "approval": appr["status"] if appr else None})
    # ---- case README
    failed = [c for c in result["checks"] if not c["passed"]]
    exp = sc["expected"]
    md = [f"# {sc['case_id']} - {sc['title']}  ({'PASS' if result['passed'] else 'FAIL'})", "",
          f"**Kind:** {sc['kind']}  |  **Input format:** {sc.get('input_format')}  |  **Policies:** {', '.join(sc.get('policies', []))}", "",
          f"**What is tested:** {sc['signals']}", "",
          f"**Expected:** status `{exp.get('status')}`, outcome `{exp.get('outcome', '-')}`, pool `{exp.get('pool_status', exp.get('http', '-'))}`"
          + (f", ERP decisions {exp['erp_decisions']}" if "erp_decisions" in exp else ""), "",
          "## Runs", "", "| Run | Attempt | Status | Outcome | Exit step | Approval | Posting |", "|---|---|---|---|---|---|---|"]
    md += [f"| [{r['run_id']}](output/{r['run_id']}/reasoning.md) | {r['attempt']} | {r['status']} | {r['outcome']} | {r['exit_step']} | "
           f"{r['approval'] or '-'} | {r['posting'] or '-'} |" for r in runs_info] or ["| - | - | rejected at validation (no run) | - | - | - | - |"]
    if runs_info:
        last = runs_info[-1]["run_id"]
        md += ["", f"![flow](output/{last}/diagram.png)", "",
               f"Files per run: `result.json`, `reasoning.md`, `approval.md`, `audit.jsonl`, `checkpoints.json`, `webhooks.json`, "
               f"`outcome.json`, `erp_decisions.json`, `diagram.png|pdf|svg`."]
    md += ["", f"## Checks: {sum(c['passed'] for c in result['checks'])}/{len(result['checks'])} passed", ""]
    md += [f"- FAILED: {c['check']} - {c['detail']}" for c in failed] or ["All checks passed (details in `checks.json`)."]
    _w(case_dir / "README.md", "\n".join(md) + "\n")
    last = runs_info[-1] if runs_info else {}
    return {"case_id": sc["case_id"], "title": sc["title"], "kind": sc["kind"], "passed": result["passed"],
            "checks": f"{sum(c['passed'] for c in result['checks'])}/{len(result['checks'])}", "status": last.get("status", "422"),
            "outcome": last.get("outcome") or "-", "exit_step": last.get("exit_step") or "-", "folder": case_dir.name,
            "diagram": f"{case_dir.name}/output/{last['run_id']}/diagram.png" if last else None}


def write_index(out: Path, summary: dict, rows: list[dict], env: dict) -> None:
    _w(out / "summary.json", summary)
    _w(out / "environment.json", env)
    md = [f"# Test history - {summary['eval_id']}", "",
          f"**When:** {env['exported_at']}  |  **Result:** {summary['passed']}/{summary['scenarios']} scenarios, "
          f"{summary['checks_passed']}/{summary['checks']} checks  |  **Model:** {env['model']}  |  **Search:** {env['retrieval']}", "",
          f"**Workflow:** {env['workflow']}  |  **Policy index:** {env['index_version']}  |  **Audit chain valid:** {summary['audit_chain']['valid']}", "",
          "| Case | Result | Kind | Title | Status | Outcome | Exit step | Checks |", "|---|---|---|---|---|---|---|---|"]
    md += [f"| [{r['case_id']}]({r['folder']}/README.md) | {'PASS' if r['passed'] else '**FAIL**'} | {r['kind']} | {r['title']} | "
           f"{r['status']} | {r['outcome']} | {r['exit_step']} | {r['checks']} |" for r in rows]
    _w(out / "index.md", "\n".join(md) + "\n")
    cards = "".join(
        f'<div class="card {"" if r["passed"] else "fail"}"><a href="{html.escape(r["folder"])}/README.md">'
        + (f'<img loading="lazy" src="{html.escape(r["diagram"])}">' if r["diagram"] else '<div class="noimg">rejected by request validation (422)</div>')
        + f'</a><div class="cap"><b>{r["case_id"]}</b> {"PASS" if r["passed"] else "FAIL"} &middot; {html.escape(r["outcome"])}<br>'
        f'{html.escape(r["title"])}<br><a href="{html.escape(r["folder"])}">folder</a> &middot; checks {r["checks"]}</div></div>' for r in rows)
    page = (f"<!doctype html><html><head><meta charset='utf-8'><title>finagent test history {summary['eval_id']}</title><style>"
            "body{font-family:Helvetica,Arial,sans-serif;margin:24px;background:#f6f7f9;color:#212836}"
            ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}"
            ".card{background:#fff;border:1px solid #d5dae3;border-radius:10px;overflow:hidden}.card.fail{border:2px solid #c74040}"
            ".card img{width:100%;display:block}.noimg{padding:60px;text-align:center;color:#888}.cap{padding:9px;font-size:13px}</style>"
            f"</head><body><h2>Test history {summary['eval_id']}</h2><p>{summary['passed']}/{summary['scenarios']} scenarios passed, "
            f"{summary['checks_passed']}/{summary['checks']} checks &middot; model {html.escape(env['model'])} &middot; "
            f"search {html.escape(env['retrieval'])} &middot; exported {env['exported_at']}</p><div class='grid'>{cards}</div></body></html>")
    _w(out / "index.html", page)


def export_history(app, scenarios: list[dict], results: list[dict], summary: dict, root: Path, base_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = base_dir / f"{stamp}_{summary['eval_id']}"
    by_id = {s["case_id"]: s for s in scenarios}
    rows = []
    for r in results:
        sc = by_id[r["case_id"]]
        rows.append(export_scenario(app, sc, r, root, out / f"{r['case_id']}_{'PASS' if r['passed'] else 'FAIL'}"))
    ret = app.retriever.describe()
    env = {"exported_at": datetime.now().isoformat(timespec="seconds"), "eval_id": summary["eval_id"],
           "model": f"{summary['provider']} / {summary['model']}",
           "retrieval": f"{ret['mode']} ({ret.get('embedder') or 'keyword only'})" + (f" [fallback: {ret['degraded']}]" if ret.get("degraded") else ""),
           "index_version": summary["index_version"], "workflow": f"{app.pipeline.name} v{app.pipeline.version} ({app.pipeline.digest})",
           "clock": str(app.settings.get("app.fixed_now")), "config": app.settings.public_manifest()}
    write_index(out, summary, rows, env)
    latest = base_dir / "LATEST.txt"
    latest.write_text(out.name + "\n", encoding="utf-8")
    return out
