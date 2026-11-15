#!/usr/bin/env python3
"""One-page call chain: which function calls which, from the HTTP request to the database and the models
-> docs/source/call_chain.html. Rendered to PDF by scripts/build_control_pdf.sh (make control-pdf).

Function names are checked against the source: the build fails if any named function no longer exists.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "finagent"

# layer -> (background, border)
L = {"api": ("#e6e9f2", "#1d2a4d"), "eng": ("#e3edfb", "#3b6bc4"), "cap": ("#fdf1d8", "#c98a10"),
     "llm": ("#efe6fb", "#7a4cc2"), "db": ("#f1f3f6", "#5a6475"), "hum": ("#e4f4e8", "#2f8a4b"),
     "hold": ("#fbe4e4", "#c0392b")}

# (depth, layer, file, function, note)   function is verified to exist in file
A = [
    (0, "api", "api/app.py", "start_run", "POST /runs · schema-validated ProcessingRequest"),
    (1, "eng", "agent/graph.py", "Engine.start_run", "Idempotency-Key replay returns the existing run"),
    (2, "db", "store.py", "create_run", "runs row; audit run.created; webhook run.created"),
    (2, "eng", "agent/graph.py", "_execute", "LOOP: step = nodes[step](); checkpoint; next"),
    (3, "eng", "agent/graph.py", "n_parse", "PARSE_INPUTS"),
    (4, "cap", "skills/parsers/registry.py", "parse_attachment", "best parser → validated document"),
    (3, "eng", "agent/graph.py", "n_retrieve", "RETRIEVE_POLICY"),
    (4, "eng", "agent/graph.py", "_planned_skills", "workflow.yaml: which controls run"),
    (4, "eng", "agent/graph.py", "_retrieve", "→ _tool(retrieve_finance_documents)"),
    (5, "cap", "tools/base.py", "ToolRunner.call", "generic: budget · schema · permission · timeout · retries · audit"),
    (6, "cap", "tools/finance_tools.py", "retrieve", "the tool's func (ToolSpec) → calls the retriever"),
    (7, "cap", "rag/retriever.py", "search", "BM25 + vectors → trust-labelled chunks"),
    (3, "eng", "agent/graph.py", "n_gather", "GATHER_EVIDENCE: vendor, PO, history, FX, authority"),
    (4, "cap", "tools/base.py", "ToolRunner.call", "same generic runtime for every tool"),
    (5, "cap", "tools/finance_tools.py", "vendor", "also purchase_order · history · fx · authority → mock_backend"),
    (3, "eng", "agent/graph.py", "n_reconcile", "RECONCILE"),
    (4, "eng", "agent/graph.py", "run_skills", "per planned control, in order"),
    (5, "cap", "skills/processors/controls.py", "REGISTRY[name].func", "script: maths, matching, limits"),
    (5, "eng", "agent/graph.py", "_skill_procedure", "only execution: code+llm (4 skills)"),
    (6, "llm", "skills/llm_runner.py", "SkillRunner.run", "scope · collect · ask · check · vote"),
    (7, "llm", "llm/providers.py", "complete", "small (7B), then large (14B) unless sure"),
    (6, "llm", "skills/llm_runner.py", "merge", "code: answer → verdict (caution only)"),
    (3, "eng", "agent/graph.py", "n_draft", "DRAFT"),
    (4, "llm", "llm/drafting.py", "build_user_prompt", "→ llm.complete → validate_draft"),
    (3, "eng", "agent/graph.py", "n_decide", "DECIDE: most cautious; policy + grounding gates"),
    (3, "eng", "agent/graph.py", "n_approval_gate", "post / reject → approval request"),
    (4, "db", "store.py", "create_approval", "STOP: AWAITING_APPROVAL; webhook approval.requested"),
    (3, "eng", "agent/graph.py", "n_hold_finalize", "or hold / escalate → STOP: NEEDS_INFORMATION"),
    (2, "db", "store.py", "checkpoint", "after EVERY step (state + next step)"),
]
B = [
    (0, "api", "api/app.py", "decide", "POST /approvals/{id}/decision (or signed webhook: inbound_approval)"),
    (1, "hum", "agent/graph.py", "Engine.decide_approval", "an approver's decision"),
    (2, "cap", "tools/base.py", "ToolRunner.call", "get_authority_register: role, limit, active"),
    (2, "hum", "agent/graph.py", "_apply_decision", "slot decision; SoD; replay-safe by callback id"),
    (2, "db", "store.py", "claim_run", "atomic: resume the run exactly once"),
    (2, "eng", "agent/graph.py", "_execute", "continue from APPROVAL_GATE"),
    (3, "eng", "agent/graph.py", "n_submit", "SUBMIT_DECISION"),
    (4, "eng", "agent/graph.py", "_policy_drift", "cited policy unchanged? else hold"),
    (4, "cap", "tools/base.py", "ToolRunner.call", "submit_finance_decision: deny-by-default"),
    (5, "cap", "tools/finance_tools.py", "guard", "APPROVED · values match · idempotency key"),
    (5, "cap", "tools/finance_tools.py", "submit", "one decision per invoice; INSERT erp_decisions"),
    (3, "eng", "agent/graph.py", "n_finalize", "result · outcome pool · webhook run.completed"),
    (3, "eng", "agent/graph.py", "n_finalize_rejected", "or approver rejected: nothing posted"),
]
C = [
    (0, "api", "api/app.py", "add_evidence", "POST /runs/{id}/evidence (or review: /runs/{id}/review)"),
    (1, "eng", "agent/graph.py", "add_evidence", "held / failed runs only"),
    (2, "db", "store.py", "claim_for_new_attempt", "atomic: resumed ONCE (else 409)"),
    (2, "eng", "agent/graph.py", "Engine.start_run", "attempt 2, parent_run_id → chain A again"),
    (0, "api", "api/app.py", "resume", "POST /runs/{id}/resume: interrupted run"),
    (1, "eng", "agent/graph.py", "resume", "continue from the last checkpoint"),
]


def verify(rows) -> None:
    missing = []
    for _, _, f, fn, _ in rows:
        name = fn.split(".")[-1]
        if "[" in fn:            # REGISTRY[name].func: the registered control functions
            continue
        if not re.search(rf"def {re.escape(name)}\b", (SRC / f).read_text()):
            missing.append(f"{f}: {fn}")
    if missing:
        sys.exit("call chain names no longer in the code: " + "; ".join(missing))


# chain row -> the workflow step it carries out (drawn as a line from the side diagram to that row)
LINK = {("A", "Engine.start_run"): "RECEIVE", ("A", "n_parse"): "PARSE_INPUTS", ("A", "n_retrieve"): "RETRIEVE_POLICY",
        ("A", "n_gather"): "GATHER_EVIDENCE", ("A", "n_reconcile"): "RECONCILE", ("A", "n_draft"): "DRAFT",
        ("A", "n_decide"): "DECIDE", ("A", "n_approval_gate"): "APPROVAL_GATE", ("A", "n_hold_finalize"): "HOLD_FINALIZE",
        ("B", "Engine.decide_approval"): "APPROVER", ("B", "n_submit"): "SUBMIT_DECISION", ("B", "n_finalize"): "FINALIZE",
        ("B", "n_finalize_rejected"): "FINALIZE_REJECTED", ("C", "Engine.start_run"): "NEW_ATTEMPT"}
# side diagram: (id, title, kind, note, arrow from the previous box)
STEPS = [("RECEIVE", "RECEIVE", "eng", "validate, create run", False),
         ("PARSE_INPUTS", "PARSE_INPUTS", "eng", "documents → data", True),
         ("RETRIEVE_POLICY", "RETRIEVE_POLICY", "eng", "plan + search policy", True),
         ("GATHER_EVIDENCE", "GATHER_EVIDENCE", "eng", "tools", True),
         ("RECONCILE", "RECONCILE", "llm", "scripts + questions", True),
         ("DRAFT", "DRAFT", "llm", "cited narrative", True),
         ("DECIDE", "DECIDE", "eng", "most cautious wins", True),
         ("APPROVAL_GATE", "APPROVAL_GATE", "hum", "post / reject: stop", True),
         ("HOLD_FINALIZE", "HOLD_FINALIZE", "hold", "hold / escalate: stop", False),
         ("APPROVER", "approver decides", "hum", "resumes the run", False),
         ("SUBMIT_DECISION", "SUBMIT_DECISION", "eng", "the only write", True),
         ("FINALIZE", "FINALIZE", "hum", "completed", True),
         ("FINALIZE_REJECTED", "FINALIZE_REJECTED", "hold", "approver rejected", False),
         ("NEW_ATTEMPT", "new attempt", "hold", "HOLD → RECEIVE", False)]


def tree(key, title, rows) -> str:
    out = [f'<div class="chain"><h3>{title}</h3>']
    for depth, layer, f, fn, note in rows:
        bg, st = L[layer]
        step = LINK.get((key, fn)) if depth else None
        step = step or (LINK.get((key, fn)) if key == "A" and fn == "Engine.start_run" else step)
        dot = f'<span class="dot" style="border-color:{st}"></span>' if step else ""
        out.append(f'<div class="row"{f" data-step={chr(34)}{step}{chr(34)}" if step else ""}><span class="gut">{dot}</span>'
                   f'<span style="width:{depth * 13}pt;flex:none"></span>'
                   + ('<span class="elbow"></span>' if depth else "")
                   + f'<span class="call" style="background:{bg};border-color:{st}"><b style="color:{st}">{fn}()</b>'
                   f'<i>{f}</i></span><span class="note">{note}</span></div>')
    out.append("</div>")
    return "".join(out)


for rows in (A, B, C):
    verify(rows)

legend = "".join(f'<span class="lg" style="background:{L[k][0]};border-color:{L[k][1]}">{t}</span>'
                 for k, t in (("api", "API"), ("eng", "engine (agent/graph.py)"), ("cap", "tools · skills · RAG"),
                              ("llm", "model calls"), ("hum", "approval"), ("db", "SQLite (store.py)")))
side = "".join(f'<div class="step" id="s-{i}" data-kind="{k}" data-arrow="{1 if a else 0}" '
               f'style="background:{L[k][0]};border-color:{L[k][1]}"><b style="color:{L[k][1]}">{t}</b><span>{n}</span></div>'
               for i, t, k, n, a in STEPS)
script = """
(function () {
  const wrap = document.getElementById('wrap'), svg = document.getElementById('links');
  const W = wrap.getBoundingClientRect();
  svg.setAttribute('width', W.width); svg.setAttribute('height', W.height);
  let prevBottom = -1e9, prev = null, html = '';
  for (const box of document.querySelectorAll('.step')) {
    const id = box.id.slice(2), row = document.querySelector('.row[data-step="' + id + '"]');
    if (!row) { box.style.display = 'none'; continue; }
    const dot = row.querySelector('.dot').getBoundingClientRect();
    const target = dot.top + dot.height / 2 - W.top, h = box.offsetHeight;
    const top = Math.max(target - h / 2, prevBottom + (box.dataset.arrow === '1' ? 9 : 4));
    box.style.top = top + 'px';
    const color = getComputedStyle(box).borderTopColor, y = top + h / 2, x1 = box.offsetLeft + box.offsetWidth;
    const x2 = dot.left - W.left;
    html += '<path d="M' + x1 + ',' + y + ' C' + (x1 + 18) + ',' + y + ' ' + (x2 - 18) + ',' + target + ' ' + x2 + ',' + target +
            '" fill="none" stroke="' + color + '" stroke-width="1.1" stroke-dasharray="3 2"/>';
    if (prev && box.dataset.arrow === '1') {
      const cx = box.offsetLeft + 12;
      html += '<path d="M' + cx + ',' + prevBottom + ' L' + cx + ',' + (top - 1) + '" stroke="#1d2a4d" stroke-width="1.2" marker-end="url(#ah)"/>';
    }
    prevBottom = top + h; prev = box;
  }
  svg.innerHTML = '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto">' +
                  '<path d="M0,0 L10,5 L0,10 z" fill="#1d2a4d"/></marker></defs>' + html;
})();
"""
html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>finagent - Call chain</title>
<style>
  @page {{ size: A4; margin: 8mm 8mm 8mm 8mm; }}
  body {{ font: 7.6pt/1.25 -apple-system, "Helvetica Neue", Arial, sans-serif; color: #1b2333; margin: 0; }}
  h2 {{ font-size: 12.5pt; color: #fff; background: #1d2a4d; padding: 4pt 8pt; margin: 0 0 3pt; border-radius: 3pt; }}
  h3 {{ font-size: 8.8pt; color: #1d2a4d; margin: 0 0 2pt; }}
  p {{ margin: 0 0 3pt; color: #5a6475; font-size: 7.4pt; }}
  #wrap {{ position: relative; }}
  #side {{ position: absolute; left: 0; top: 0; width: 104pt; height: 100%; }}
  #links {{ position: absolute; left: 0; top: 0; pointer-events: none; }}
  .step {{ position: absolute; left: 0; width: 98pt; border: 1pt solid; border-radius: 3pt; padding: 1pt 4pt; }}
  .step b {{ display: block; font: 600 7pt Menlo, monospace; }}
  .step span {{ font-size: 6.6pt; color: #3d4657; }}
  #main {{ margin-left: 128pt; }}
  .chain {{ border: 1px solid #d5dbe5; border-radius: 4pt; padding: 3pt 5pt; margin-bottom: 4pt; }}
  .row {{ display: flex; align-items: center; gap: 3pt; margin: 1.3pt 0; }}
  .gut {{ width: 9pt; flex: none; display: flex; justify-content: center; }}
  .dot {{ width: 6pt; height: 6pt; border-radius: 50%; border: 1.5pt solid; background: #fff; }}
  .elbow {{ width: 7pt; height: 8pt; border-left: 1.1pt solid #9aa5b8; border-bottom: 1.1pt solid #9aa5b8; margin-top: -8pt; flex: none; }}
  .call {{ border: 1pt solid; border-radius: 3pt; padding: 0.3pt 3pt; white-space: nowrap; flex: none; }}
  .call b {{ font: 600 7.2pt Menlo, monospace; }}
  .call i {{ font: normal 6.3pt Menlo, monospace; color: #5a6475; margin-left: 3pt; }}
  .note {{ color: #3d4657; font-size: 6.9pt; min-width: 0; }}
  .lg {{ display: inline-block; border: 1pt solid; border-radius: 3pt; padding: 0 3pt; margin-right: 3pt; font-size: 6.8pt; }}
</style></head><body>
<h2>finagent call chain: from the request to the database and the models</h2>
<p>Left: the workflow steps. Each dashed line links a step to the function that runs it (●). Right: <b>function()</b>
<i>file</i> under <code>src/finagent/</code>; indented = called by the row above. Every step runs inside
<code>Engine._execute</code>, which saves a checkpoint after each one. {legend}</p>
<div id="wrap"><div id="side">{side}</div><svg id="links"></svg><div id="main">
{tree("A", "A · Start a case: POST /runs → stops at approval or hold", A)}
{tree("B", "B · Approval decision → post once", B)}
{tree("C", "C · Resume: new evidence / review, or an interrupted run", C)}
</div></div>
<p>Generated by <code>scripts/build_call_chain_page.py</code>, which fails if a named function no longer exists
(<code>make control-pdf</code>).</p>
<script>{script}</script>
</body></html>
"""
out = ROOT / "docs/source/call_chain.html"
out.write_text(html, encoding="utf-8")
print(f"wrote {out.relative_to(ROOT)}")
