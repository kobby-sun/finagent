"""Per-run diagram: one picture of what happened in a single workflow run.

    finagent diagram <run_id> --format pdf|png|svg [--out file]
    GET /runs/{run_id}/diagram?format=svg|png|pdf
    finagent start --case FIN-001 --diagram png        |  finagent eval --diagrams pdf
    app.run_diagrams: [svg]   (settings) -> auto-render every run when it stops / completes

Sections: workflow path (exit step highlighted), evidence & tool calls (attempts, failures), each control's
verdict, the decision chain (rules -> model -> final), people & approvals, and warnings/logs (risk indicators,
exceptions, unknowns, notable audit events). One layout is built as primitives and rendered to SVG / PDF
(no dependencies) or PNG (Pillow).
"""
from __future__ import annotations

import io
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any

W, H = 1400, 1000
INK, MUTED, LINE = "#212836", "#616b80", "#737f94"
PAL = {
    "blue": ("#e0edff", "#4d78cc"), "green": ("#e3f7e8", "#38995c"), "amber": ("#fff2d9", "#cc8c26"),
    "red": ("#ffe8e8", "#c74040"), "grey": ("#f2f4f7", "#99a1ad"), "purple": ("#f0e8ff", "#8061c7"), "white": ("#ffffff", "#c5cbd6"),
}
NODE_KIND = {"APPROVAL_GATE": "red", "SUBMIT_DECISION": "red", "HOLD_FINALIZE": "amber", "FINALIZE": "green",
             "FINALIZE_REJECTED": "grey", "DRAFT": "amber", "DECIDE": "purple", "RECONCILE": "purple"}
OUTCOME_KIND = {"APPROVE_FOR_POSTING": "green", "HOLD_FOR_INFORMATION": "amber", "ESCALATE_CONTROL_REVIEW": "red",
                "REJECT_DUPLICATE": "grey", "REJECT_INVALID": "grey"}


class Canvas:
    def __init__(self):
        self.items: list[tuple] = []

    def rect(self, x, y, w, h, kind="white", lw=1.2, dash=False):
        f, s = PAL[kind]
        self.items.append(("rect", x, y, w, h, f, s, lw, dash))

    def text(self, x, y, s, size=13, bold=False, color=INK, align="left", maxw=None):
        s = str(s)
        if maxw:
            cap = max(4, int(maxw / (size * 0.56)))
            if len(s) > cap:
                s = s[: cap - 1] + "…"
        self.items.append(("text", x, y, s, size, bold, color, align))

    def arrow(self, x1, y1, x2, y2, color=LINE):
        self.items.append(("arrow", x1, y1, x2, y2, color))

    def chip(self, x, y, w, h, title, sub="", kind="blue", bold_border=False):
        self.rect(x, y, w, h, kind, 2.6 if bold_border else 1.2)
        self.text(x + w / 2, y + (h / 2 - 2 if sub else h / 2 + 5), title, 13, True, INK, "center", w - 10)
        if sub:
            self.text(x + w / 2, y + h / 2 + 15, sub, 11, False, MUTED, "center", w - 10)

    def section(self, y, label):
        self.text(24, y, label, 15, True, PAL["blue"][1])
        self.items.append(("line", 24, y + 6, W - 24, y + 6, "#dde3ec"))


# ---------------------------------------------------------------------------- layout
def build(run: dict) -> Canvas:
    c = Canvas()
    res = run.get("result") or {}
    rec = res.get("recommendation") or {}
    reasoning = run.get("reasoning") or []
    events = run.get("audit_events") or []
    outcome = rec.get("outcome") or "-"
    # header
    c.items.append(("rect", 0, 0, W, 64, "#1c2b4d", "#1c2b4d", 0, False))
    c.text(24, 40, f"finagent run {run['run_id']}  -  case {run['case_id']}", 22, True, "#ffffff")
    c.text(W - 24, 40, f"{run['status']}  |  {outcome}", 16, True, "#ffffff", "right")
    wf = ((run.get("state") or {}).get("workflow") or {})
    c.text(24, 88, f"attempt {run.get('attempt')}  -  index {run.get('index_version')}  -  started {run.get('created_at', '')[:19]}  -  "
                   f"updated {run.get('updated_at', '')[:19]}" + (f"  -  workflow {wf.get('version')} ({wf.get('digest')})" if wf else ""),
           12, False, MUTED)

    # 1. workflow path
    c.section(118, "1  Workflow path (exit step outlined)")
    path = [cp["node"] for cp in run.get("checkpoints", [])]
    seq = [n for i, n in enumerate(path) if i == 0 or n != path[i - 1]]
    n = max(len(seq), 1)
    per_row = min(n, 9)
    bw = (W - 48 - (per_row - 1) * 18) / per_row
    for i, node in enumerate(seq):
        row, col = divmod(i, per_row)
        x, y = 24 + col * (bw + 18), 132 + row * 62
        last = i == len(seq) - 1
        c.chip(x, y, bw, 46, node, "EXIT" if last else "", NODE_KIND.get(node, "blue"), bold_border=last)
        if col:
            c.arrow(x - 18, y + 23, x, y + 23)
    y0 = 132 + ((len(seq) - 1) // per_row + 1) * 62 + 14

    # 2. tools
    c.section(y0, "2  Evidence & tool calls")
    tools: "OrderedDict[str, dict]" = OrderedDict()
    for e in events:
        if e["event_type"] != "tool.call":
            continue
        t = tools.setdefault(e["tool"], {"calls": 0, "attempts": 0, "fail": 0, "retry": 0, "ms": 0})
        t["attempts"] += 1
        t["ms"] += e.get("duration_ms") or 0
        t["fail"] += e["outcome"] == "failed"
        t["retry"] += e["outcome"] == "retrying"
        t["calls"] += e["outcome"] in ("ok", "failed")
    skipped = [r["decision"].split(":")[0] for r in reasoning if r["step"] == "GATHER_EVIDENCE" and "skipped" in r["decision"]]
    cw, x, y = 196, 24, y0 + 16
    for name, t in tools.items():
        kind = "red" if t["fail"] else ("amber" if t["retry"] else "green")
        sub = (f"{t['calls']} call(s)" if name == "retrieve_finance_documents" else f"{t['attempts']} attempt(s)") + \
              (f", {t['fail']} failed" if t["fail"] else "") + (f", {t['retry']} retried" if t["retry"] else "") + f", {t['ms']} ms"
        if x + cw > W - 24:
            x, y = 24, y + 58
        c.chip(x, y, cw, 48, name, sub, kind)
        x += cw + 10
    for name in skipped:
        if x + cw > W - 24:
            x, y = 24, y + 58
        c.chip(x, y, cw, 48, name, "skipped (not needed)", "grey")
        x += cw + 10
    y1 = y + 72

    # 3. controls
    c.section(y1, "3  Controls (skills) - verdicts")
    skills = [r for r in reasoning if r["step"] == "RECONCILE" and r["actor"].startswith("skill:")]
    plan_skips = [r["decision"][5:] for r in reasoning if r["step"] == "PLAN" and r["decision"].startswith("skip ")]
    cw, x, y = 262, 24, y1 + 16
    for r in skills:
        d = r["decision"]
        kind = "green" if d == "pass" else ("amber" if "flags" in d or "HOLD" in d else ("red" if "ESCALATE" in d else "grey"))
        if x + cw > W - 24:
            x, y = 24, y + 58
        c.chip(x, y, cw, 48, r["actor"][6:], d, kind)
        x += cw + 10
    for s_ in plan_skips:
        if x + cw > W - 24:
            x, y = 24, y + 58
        c.chip(x, y, cw, 48, s_, "not applicable", "white")
        x += cw + 10
    y2 = y + 72

    # 4. decision chain
    c.section(y2, "4  Decision")
    rules = next((r for r in reasoning if r["step"] == "RECONCILE" and r["actor"] == "system"), None)
    model = next((r for r in reasoning if r["step"] == "DRAFT"), None)
    adjust = [r for r in reasoning if r["step"] == "DECIDE" and r["decision"] == "adjustment"]
    boxes = [("Rules outcome", (rules or {}).get("decision", "-").replace("rules outcome ", ""), "purple"),
             ("Model", (model or {}).get("decision", "not run"), "amber"),
             ("Guard", (adjust[0]["reason"][:70] if adjust else "no adjustment"), "red" if adjust else "white"),
             ("Final recommendation", f"{outcome}  ({rec.get('primary_exception') or 'no exception'}, conf {rec.get('confidence')})",
              OUTCOME_KIND.get(outcome, "grey"))]
    bw = (W - 48 - 3 * 24) / 4
    for i, (t, sub, kind) in enumerate(boxes):
        x = 24 + i * (bw + 24)
        c.chip(x, y2 + 16, bw, 52, t, sub, kind, bold_border=(i == 3))
        if i:
            c.arrow(x - 24, y2 + 42, x, y2 + 42)
    y3 = y2 + 92

    # 5. people & approvals
    c.section(y3, "5  Approval & people")
    gate = next((r for r in reasoning if r["step"] == "APPROVAL_GATE"), None)
    humans = [r for r in reasoning if r["step"] == "HUMAN_DECISION"]
    sub = next((r for r in reasoning if r["step"] == "SUBMIT_DECISION"), None)
    fin = next((r for r in reasoning if r["step"] == "FINALIZE"), None)
    x, y = 24, y3 + 16
    if not gate:
        c.chip(x, y, 420, 48, "No approval requested", "protective outcome (hold / escalate) - nothing can be posted", "grey")
    else:
        c.chip(x, y, 300, 48, "Approval requested", gate["reason"].replace("consequential outcome requires: ", "")[:60], "red")
        x += 310
        for h in humans:
            kind = "grey" if "duplicate" in h["decision"] else ("red" if "REFUSED" in h["decision"] or "REJECT" in h["decision"] else "green")
            if x + 230 > W - 24:
                x, y = 24, y + 58
            c.chip(x, y, 230, 48, h["actor"].replace("human:", ""), h["decision"][:34], kind)
            c.arrow(x - 10, y + 24, x, y + 24)
            x += 240
        if sub or fin:
            if x + 260 > W - 24:
                x, y = 24, y + 58
            last = sub or fin
            c.chip(x, y, 260, 48, "Posted (simulated)" if sub else "Nothing submitted", last["decision"], "green" if sub else "grey",
                   bold_border=True)
        elif run["status"] == "AWAITING_APPROVAL":
            c.chip(x, y, 260, 48, "Waiting", "pending approver decision", "amber", bold_border=True)
    y4 = y + 72

    # 6. warnings & logs
    c.section(y4, "6  Warnings & logs")
    lines: list[tuple[str, str]] = []
    for r_ in res.get("risk_indicators", []):
        lines.append(("warning", f"{r_['indicator']} - {r_['source']}"))
    for e in res.get("exceptions", []):
        lines.append(("exception", f"{e['category']}: {e['failed_rule']} (observed {e['observed']}) -> {e['owner']}"))
    for u in res.get("unknowns", []):
        lines.append(("unknown", f"{u['item']}: {u['impact']}"))
    notable = Counter(e["event_type"] + (f":{e['outcome']}" if e["event_type"] == "tool.call" and e["outcome"] != "ok" else "")
                      for e in events if e["event_type"] in ("tool.call", "model.override_blocked", "model.escalation_adopted",
                                                               "model.fallback", "grounding.gap", "approval.denied",
                                                               "approval.callback_replayed", "approval.duplicate_ignored",
                                                               "run.simulated_crash", "run.resumed", "run.failed")
                      and not (e["event_type"] == "tool.call" and e["outcome"] == "ok"))
    if notable:
        lines.append(("audit", ", ".join(f"{k} x{v}" for k, v in notable.items())))
    if not lines:
        lines.append(("ok", "no warnings, exceptions or unknowns"))
    colours = {"warning": PAL["red"][1], "exception": PAL["amber"][1], "unknown": PAL["purple"][1], "audit": PAL["blue"][1], "ok": PAL["green"][1]}
    y = y4 + 26
    room = max(1, int((H - 14 - y) / 19))
    for kind, txt in lines[:room]:
        c.text(34, y, kind.upper(), 11, True, colours[kind])
        c.text(130, y, txt, 12, False, INK, maxw=W - 160)
        y += 19
    if len(lines) > room:
        c.text(W - 24, H - 10, f"+{len(lines) - room} more (see GET /runs/{{id}}/reasoning)", 11, False, MUTED, "right")
    return c


# ---------------------------------------------------------------------------- renderers
def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_svg(c: Canvas) -> bytes:
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'font-family="Helvetica, Arial, sans-serif"><rect width="{W}" height="{H}" fill="#ffffff"/>',
           '<defs><marker id="a" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto">'
           f'<path d="M0,0 L10,4 L0,8 z" fill="{LINE}"/></marker></defs>']
    for it in c.items:
        if it[0] == "rect":
            _, x, y, w, h, f, s, lw, dash = it
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="8" fill="{f}" stroke="{s}" '
                       f'stroke-width="{lw}"{" stroke-dasharray=\"5 4\"" if dash else ""}/>')
        elif it[0] == "text":
            _, x, y, s, size, bold, col, align = it
            anchor = {"left": "start", "center": "middle", "right": "end"}[align]
            out.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{col}" text-anchor="{anchor}"'
                       f'{" font-weight=\"bold\"" if bold else ""}>{_esc(s)}</text>')
        elif it[0] == "arrow":
            _, x1, y1, x2, y2, col = it
            out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2 - 2:.1f}" y2="{y2:.1f}" stroke="{col}" stroke-width="1.6" marker-end="url(#a)"/>')
        elif it[0] == "line":
            _, x1, y1, x2, y2, col = it
            out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{col}" stroke-width="1"/>')
    out.append("</svg>")
    return "\n".join(out).encode()


def _rgb(h: str) -> tuple[float, float, float]:
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


def to_pdf(c: Canvas) -> bytes:
    k = 0.6                                   # 1400x1000 units -> 840x600 pt page
    pw, ph = W * k, H * k
    ops = []

    def col(h, stroke=False):
        r, g, b = _rgb(h)
        ops.append(f"{r:.3f} {g:.3f} {b:.3f} {'RG' if stroke else 'rg'}")
    for it in c.items:
        if it[0] == "rect":
            _, x, y, w, h, f, s, lw, dash = it
            col(f); col(s, True)
            ops.append(f"{lw * k:.2f} w {'[3 2] 0 d' if dash else '[] 0 d'} {x * k:.1f} {ph - (y + h) * k:.1f} {w * k:.1f} {h * k:.1f} re B")
        elif it[0] == "text":
            _, x, y, s, size, bold, colr, align = it
            sz = size * k
            tw = len(s) * sz * (0.56 if bold else 0.52)
            xx = x * k - (tw / 2 if align == "center" else tw if align == "right" else 0)
            col(colr)
            safe = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").replace("…", "...")
            ops.append(f"BT /{'F2' if bold else 'F1'} {sz:.1f} Tf {xx:.1f} {ph - y * k:.1f} Td ({safe}) Tj ET")
        elif it[0] in ("arrow", "line"):
            _, x1, y1, x2, y2, colr = it
            col(colr, True); col(colr)
            ops.append(f"0.9 w [] 0 d {x1 * k:.1f} {ph - y1 * k:.1f} m {x2 * k:.1f} {ph - y2 * k:.1f} l S")
            if it[0] == "arrow":
                ex, ey = x2 * k, ph - y2 * k
                ops.append(f"{ex:.1f} {ey:.1f} m {ex - 6:.1f} {ey + 3:.1f} l {ex - 6:.1f} {ey - 3:.1f} l f")
    stream = "\n".join(ops).encode("latin-1", errors="replace")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [5 0 R] /Count 1 >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.0f} {ph:.0f}] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents 6 0 R >>".encode(),
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    return bytes(out)


def to_png(c: Canvas) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PNG needs Pillow: pip install pillow (or use format=svg/pdf)") from exc
    img = Image.new("RGB", (W, H), "#ffffff")
    d = ImageDraw.Draw(img)
    fonts: dict[int, Any] = {}

    def font(size):
        if size not in fonts:
            fonts[size] = ImageFont.load_default(size=size)
        return fonts[size]
    for it in c.items:
        if it[0] == "rect":
            _, x, y, w, h, f, s, lw, _dash = it
            d.rounded_rectangle([x, y, x + w, y + h], radius=8, fill=f, outline=s, width=max(1, round(lw)))
        elif it[0] == "text":
            _, x, y, s, size, bold, colr, align = it
            fnt = font(size)
            tw = d.textlength(s, font=fnt)
            xx = x - (tw / 2 if align == "center" else tw if align == "right" else 0)
            d.text((xx, y - size), s, fill=colr, font=fnt, stroke_width=1 if bold and size >= 18 else 0, stroke_fill=colr)
        elif it[0] in ("arrow", "line"):
            _, x1, y1, x2, y2, colr = it
            d.line([x1, y1, x2, y2], fill=colr, width=2 if it[0] == "arrow" else 1)
            if it[0] == "arrow":
                d.polygon([(x2, y2), (x2 - 9, y2 - 5), (x2 - 9, y2 + 5)], fill=colr)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


RENDERERS = {"svg": to_svg, "pdf": to_pdf, "png": to_png}
MEDIA = {"svg": "image/svg+xml", "pdf": "application/pdf", "png": "image/png"}


def render(run: dict, fmt: str = "svg") -> bytes:
    if fmt not in RENDERERS:
        raise ValueError(f"format must be one of {sorted(RENDERERS)}")
    return RENDERERS[fmt](build(run))


def write(engine, run_id: str, fmt: str, out: Path | None = None) -> Path:
    run = engine.get_run(run_id)
    run["state"] = (engine.store.get_run(run_id) or {}).get("state_json") or {}
    path = Path(out) if out else engine.settings.data_dir / "diagrams" / f"{run['case_id']}_{run_id}.{fmt}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render(run, fmt))
    return path
