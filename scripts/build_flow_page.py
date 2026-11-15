#!/usr/bin/env python3
"""One-page decision flow: every path a case can take, who acts at each step (code / LLM / person) and the code file
for each box -> docs/source/code_llm_flow.html. Rendered to PDF by scripts/build_control_pdf.sh (make control-pdf).

The paths mirror src/finagent/agent/graph.py (step table, DECIDE routing, SUBMIT checks, _fail) and
schemas.CONSEQUENTIAL_OUTCOMES (the outcomes that need a person).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
C = {"code": ("#e3edfb", "#3b6bc4"), "llm": ("#efe6fb", "#7a4cc2"), "human": ("#e4f4e8", "#2f8a4b"),
     "hold": ("#fbe4e4", "#c0392b"), "fail": ("#f1f3f6", "#5a6475"), "done": ("#e4f4e8", "#2f8a4b")}
o: list[str] = []


def T(x, y, t, size=13, fill="#1b2333", anchor="start", bold=False, mono=False):
    o.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" text-anchor="{anchor}"'
             + (' font-weight="700"' if bold else "") + (' font-family="Menlo,monospace"' if mono else "") + f">{t}</text>")


def box(x, y, w, title, lines, files, kind="code", tag=None):
    bg, st = C[kind]
    h = 30 + 17 * len(lines) + 18
    o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{bg}" stroke="{st}" stroke-width="2"/>')
    T(x + 10, y + 21, title, 15, st, bold=True, mono=True)
    if tag:
        T(x + w - 10, y + 20, tag, 11, st, "end", bold=True)
    for i, line in enumerate(lines):
        T(x + 10, y + 40 + i * 17, line)
    T(x + 10, y + 40 + len(lines) * 17, files, 11.5, "#5a6475", mono=True)
    return h


def arrow(d, dash=False, color="#1d2a4d"):
    o.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"' + (' stroke-dasharray="6 4"' if dash else "")
             + ' marker-end="url(#ah)"/>')


def num(n, x, y):
    o.append(f'<circle cx="{x}" cy="{y}" r="11" fill="#1d2a4d"/>')
    T(x, y + 5, n, 13, "#fff", "middle", bold=True)


X, W = 150, 350
cx = X + W // 2
LX, LW = 640, 350
T(cx, 22, "CODE runs every step", 15, "#3b6bc4", "middle", bold=True)
T(LX + LW / 2, 22, "LLM answers when code asks · end states", 15, "#7a4cc2", "middle", bold=True)

# ---- main column
y, pos = 48, {}
for t, lines, f in (("RECEIVE", ["validate request, create run"], "agent/graph.py · contracts.py"),
                    ("PARSE_INPUTS", ["parsers: documents → exact data"], "skills/parsers/"),
                    ("RETRIEVE_POLICY", ["plan controls, search policy"], "workflow.py · rag/retriever.py"),
                    ("GATHER_EVIDENCE", ["tools: vendor, PO, history, FX, authority"], "tools/finance_tools.py · tools/base.py")):
    pos[t] = y
    h = box(X, y, W, t, lines, f)
    arrow(f"M{cx},{y+h} L{cx},{y+h+20}")
    y += h + 22
rec_y = y
h = box(X, y, W, "RECONCILE", ["11 control scripts: all maths, matching,", "  duplicates, vendor, bank, authority",
                               "skill questions → model (4 skills)", "code: answer → verdict, counts indicators",
                               "an answer can only add caution"], "processors/controls.py · skills/llm_runner.py", tag="CODE + LLM")
arrow(f"M{cx},{y+h} L{cx},{y+h+20}")
y += h + 22
draft_y = y
h = box(X, y, W, "DRAFT", ["model writes the cited explanation", "code validates it (or uses a fallback)",
                           "↺ may loop once: one extra policy search"], "llm/drafting.py", tag="CODE + LLM")
arrow(f"M{cx},{y+h} L{cx},{y+h+24}")
y += h + 26
dy, dh = y, 104
o.append(f'<polygon points="{cx},{dy} {X+W},{dy+dh/2} {cx},{dy+dh} {X},{dy+dh/2}" fill="#e6e9f2" stroke="#1d2a4d" stroke-width="2"/>')
T(cx, dy + 40, "DECIDE", 15, "#1d2a4d", "middle", bold=True, mono=True)
T(cx, dy + 58, "most cautious outcome wins", 12, anchor="middle")
T(cx, dy + 74, "agent/graph.py", 11.5, "#5a6475", "middle", mono=True)
y = dy + dh
arrow(f"M{cx},{y} L{cx},{y+40}")
T(cx + 10, y + 16, "post or reject", 12, "#2f8a4b", bold=True)
T(cx + 10, y + 31, "→ needs a person", 12, "#2f8a4b")
y += 42
ag_y = y
h = box(X, y, W, "APPROVAL_GATE", ["APPROVE_FOR_POSTING · REJECT_INVALID ·", "REJECT_DUPLICATE: every required approver",
                                   "(amount, authority register; SoD)"], "agent/graph.py · api/app.py /approvals",
        kind="human", tag="AWAITING_APPROVAL")
ag_h = h
arrow(f"M{cx},{y+h} L{cx},{y+h+28}")
T(cx + 10, y + h + 18, "all approved", 12, "#5a6475")
y += h + 30
sub_y = y
h = box(X, y, W, "SUBMIT_DECISION", ["the only write: sandbox, idempotent", "re-checks policy; one decision per invoice"],
        "tools/finance_tools.py (submit)")
sub_h = h
arrow(f"M{cx},{y+h} L{cx},{y+h+20}")
y += h + 22
fin_y = y
h = box(X, y, W, "FINALIZE", ["posted; result, audit, webhooks"], "store.py · audit.py · webhooks.py", kind="done", tag="COMPLETED")
end = y + h

# checkpoint bracket
o.append(f'<path d="M135,48 L122,48 L122,{end} L135,{end}" fill="none" stroke="#3b6bc4" stroke-width="2"/>')
mid = (48 + end) / 2
o.append(f'<text x="104" y="{mid}" font-size="13" fill="#3b6bc4" text-anchor="middle" transform="rotate(-90 104 {mid})">'
         "each step returns the next step · checkpoint saved after every step (store.py) · budgets cap calls</text>")
num("1", 104, 38)
num("2", X - 10, pos["PARSE_INPUTS"] + 30)

# ---- right lane
vy = pos["RECEIVE"]
box(LX, vy + 4, LW, "REJECTED AT VALIDATION", ["invalid request → HTTP 422, no run created"], "contracts.py · schemas.py",
    kind="fail")
arrow(f"M{X+W},{vy+30} L{LX-4},{vy+30}")
T(X + W + 10, vy + 22, "invalid", 12, "#5a6475")
num("11", X + W + 70, vy + 46)

ry = rec_y - 72
h1 = box(LX, ry, LW, "small model (qwen 7B)", ["picks one allowed answer per question", "+ copies an exact quote from the evidence",
                                               "+ how sure (token probabilities)"], "llm/providers.py → Ollama", kind="llm")
box(LX, ry + h1 + 20, LW, "large model (qwen 14B)", ["second vote unless small is ≥ 99% sure", "disagree → large decides, flagged"],
    "llm/providers.py → Ollama", kind="llm")
arrow(f"M{LX+LW/2},{ry+h1} L{LX+LW/2},{ry+h1+18}", color="#7a4cc2")
a1, a2 = rec_y + 8, rec_y + 92
arrow(f"M{X+W},{a1} L{LX-4},{a1}", color="#7a4cc2")
T(X + W + 10, a1 - 8, "questions + evidence", 12, "#7a4cc2")
arrow(f"M{LX},{a2} L{X+W+4},{a2}", color="#7a4cc2")
T(X + W + 10, a2 + 18, "answers", 12, "#7a4cc2")
num("3", X + W + 70, (a1 + a2) / 2)
num("4", X + W + 110, a2 + 14)

box(LX, draft_y, LW, "narrative model", ["explains the result, cites policy ids", "may propose a MORE cautious outcome only"],
    "llm/providers.py", kind="llm")
b1, b2 = draft_y + 22, draft_y + 58
arrow(f"M{X+W},{b1} L{LX-4},{b1}", color="#7a4cc2")
T(X + W + 10, b1 - 8, "result + policy", 12, "#7a4cc2")
arrow(f"M{LX},{b2} L{X+W+4},{b2}", color="#7a4cc2")
T(X + W + 10, b2 + 18, "draft → code checks", 12, "#7a4cc2")
num("5", 560, (b1 + b2) / 2)
num("6", cx, dy - 12)

hy = dy - 6
hh = box(LX, hy, LW, "HOLD_FINALIZE", ["HOLD_FOR_INFORMATION · ESCALATE_CONTROL_", "REVIEW: nothing is posted, so nothing to approve",
                                       "resume = NEW attempt from RECEIVE:", "  add evidence, or a reviewer decides"],
         "graph.py add_evidence · review_judgement", kind="hold", tag="NEEDS_INFORMATION")
arrow(f"M{X+W},{dy+dh/2} L{LX-4},{dy+dh/2}")
T(X + W + 12, dy + dh / 2 - 8, "hold / escalate", 12, "#c0392b", bold=True)
num("7", LX + LW - 20, hy + hh - 16)
# new attempt: back to RECEIVE (top)
rx = LX + LW + 8
arrow(f"M{LX+LW},{hy+40} L{rx},{hy+40} L{rx},34 L{X+W-40},34 L{X+W-40},{pos['RECEIVE']-3}", dash=True, color="#c0392b")
T(X + W - 30, 30, "new attempt (2, 3 ...)", 11.5, "#c0392b")

rj_y = ag_y + 4
box(LX, rj_y, LW, "FINALIZE_REJECTED", ["an approver rejected: nothing submitted"], "agent/graph.py", kind="done", tag="COMPLETED")
arrow(f"M{X+W},{rj_y+26} L{LX-4},{rj_y+26}")
T(X + W + 12, rj_y + 18, "rejected", 12, "#5a6475")
num("8", cx, ag_y - 14)

# SUBMIT: policy changed after approval -> HOLD
px = 610
arrow(f"M{X+W},{sub_y+16} L{px},{sub_y+16} L{px},{hy+hh-14} L{LX-4},{hy+hh-14}", color="#c0392b")
T(X + W + 8, sub_y - 2, "policy changed /", 11, "#c0392b")
T(X + W + 8, sub_y + 10, "already decided", 11, "#c0392b")
num("9", X - 10, sub_y + 30)

fy = sub_y - 6
box(LX, fy, LW, "FAILED", ["from ANY step: budget exceeded, permission", "denied, error; submit failed after retries.",
                           "Nothing posted. Crash → resume checkpoint;", "else new attempt with evidence"],
    "agent/graph.py _fail · POST /runs/{id}/resume", kind="fail", tag="FAILED")
arrow(f"M{X+W},{sub_y+sub_h-26} L{LX-4},{sub_y+sub_h-26}", color="#5a6475")
T(X + W + 8, sub_y + sub_h - 10, "error after retries", 11, "#5a6475")
num("10", 612, sub_y + sub_h - 26)

H = max(end, fy + 140) + 10
svg = (f'<svg viewBox="0 0 1010 {H}" width="88%" style="display:block;margin:0 auto" '
       'font-family="-apple-system, Helvetica Neue, Arial, sans-serif" xmlns="http://www.w3.org/2000/svg">'
       '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
       '<path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker></defs>'
       f'<line x1="570" y1="40" x2="570" y2="{H}" stroke="#d5dbe5" stroke-width="2" stroke-dasharray="4 4"/>' + "".join(o) + "</svg>")

KEY = """<table class="legend">
<tr><td><b>1</b> Code moves the run: each step returns the next one; a checkpoint after every step; budgets cap tool and model calls</td>
    <td><b>7</b> Hold / escalate posts nothing, so there is nothing to approve (never an auto-approval). New evidence or a reviewer's answer starts attempt 2 from RECEIVE (once: the old run becomes SUPERSEDED)</td></tr>
<tr><td><b>2</b> No LLM here: parsing, policy search and tool calls are code (permission, timeout, retries, audit)</td>
    <td><b>8</b> Post or reject always needs a person (no auto-approval): every required approver (by amount / authority); any rejection ends it</td></tr>
<tr><td><b>3</b> Code chooses the evidence and the questions; the model picks one allowed answer and quotes the evidence</td>
    <td><b>9</b> SUBMIT is the only write (deny-by-default, needs the approval, sandbox, idempotent); policy changed since approval, or this invoice already decided by another run → hold</td></tr>
<tr><td><b>4</b> Code checks every answer (allowed? quote really there? sure enough?); unsure → warn, ask for evidence or review</td>
    <td><b>10</b> Any step can fail safe: budget, permission, error, or submit failing after retries → FAILED, nothing posted</td></tr>
<tr><td><b>5</b> The narrative is checked by code: schema, citations, numbers; invalid → retry, then a flagged fallback</td>
    <td><b>11</b> An invalid request never becomes a run (HTTP 422); a repeated Idempotency-Key returns the existing run</td></tr>
<tr><td><b>6</b> DECIDE: most cautious outcome wins; the model may escalate, never relax. Policy not matching the verified baseline → hold</td>
    <td>Blue = code · purple = model · green = person / completed · red = held · grey = not processed. Tags = run status</td></tr>
</table>"""

HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>finagent - Code and LLM decision flow</title>
<style>
  @page { size: A4; margin: 10mm 10mm 10mm 10mm; }
  body { font: 9pt/1.4 -apple-system, "Helvetica Neue", Arial, sans-serif; color: #1b2333; margin: 0; }
  h2 { font-size: 13.5pt; color: #fff; background: #1d2a4d; padding: 5pt 8pt; margin: 0 0 5pt; border-radius: 3pt; }
  p.small { font-size: 8.4pt; color: #5a6475; margin: 0 0 4pt; }
  code { font: 7.8pt Menlo, monospace; background: #f2f4f8; padding: 0 2pt; }
  table.legend { width: 100%; border-collapse: collapse; font-size: 7.4pt; margin-top: 3pt; }
  table.legend td { width: 50%; border: 1px solid #d5dbe5; padding: 2pt 4pt; vertical-align: top; }
</style></head><body>"""
html = (HEAD + "<h2>How code and the LLM move a case and make the decision: every path</h2>"
        '<p class="small">Code runs every step and makes every decision. The model is asked fixed questions at two points and '
        "its replies are checked before they count. A person approves only outcomes that <b>post or reject</b> an invoice; "
        "holds and escalations post nothing. All files are under <code>src/finagent/</code>.</p>"
        + svg + KEY + "</body></html>\n")
out = ROOT / "docs/source/code_llm_flow.html"
out.write_text(html, encoding="utf-8")
print(f"wrote {out.relative_to(ROOT)}")
