#!/usr/bin/env python3
"""One-page database chart: every table, its columns and how the tables relate -> docs/source/db_schema.html.

Columns come from the real CREATE TABLE statements (src/finagent/store.py SCHEMA, rag/vectorstore.py), so the page
cannot drift from the code. Purposes and relations are described here. Rendered to PDF by scripts/build_control_pdf.sh.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from finagent.store import SCHEMA  # noqa: E402

VEC = re.search(r'CREATE TABLE IF NOT EXISTS vectors \((.*?)\)"""', (ROOT / "src/finagent/rag/vectorstore.py").read_text(), re.S)

PURPOSE = {
    "runs": "one row per workflow run (attempt): request, live state, result",
    "checkpoints": "state saved after every step; resume continues from the last",
    "approvals": "the human approval request for a run; slots + decisions",
    "approval_callbacks": "inbound approval callbacks; replay-safe by callback id",
    "outcomes": "outcome pool: each run's current outcome / status",
    "audit_events": "append-only, hash-chained audit trail (mirrored to audit.jsonl)",
    "webhook_subscriptions": "who receives which outbound events",
    "webhook_outbox": "outbound webhooks, sent after the checkpoint; retried",
    "erp_decisions": "SIMULATED ERP: posted decisions; only the submit tool writes",
    "eval_results": "scenario evaluation reports",
    "vectors": "policy chunk embeddings (separate file: data/index/vectors.db)",
}
FK = {  # (table, column) -> what it points to
    ("runs", "parent_run_id"): "runs (previous attempt)",
    ("checkpoints", "run_id"): "runs", ("approvals", "run_id"): "runs (one per run)",
    ("approval_callbacks", "approval_id"): "approvals", ("outcomes", "run_id"): "runs (one per run)",
    ("audit_events", "run_id"): "runs (null = system event)", ("audit_events", "prev_hash"): "prev. hash",
    ("webhook_outbox", "subscription_id"): "subscriptions", ("webhook_outbox", "run_id"): "runs",
    ("erp_decisions", "run_id"): "runs", ("erp_decisions", "approval_id"): "approvals",
    ("vectors", "chunk_id"): "bm25 chunk",
}


def parse(sql: str) -> dict[str, list[dict]]:
    tables = {}
    for name, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n?\);", sql, re.S):
        cols, pk = [], set()
        for line in body.split("\n"):
            line = line.split("--")[0].strip().rstrip(",")
            if not line:
                continue
            m = re.match(r"PRIMARY KEY \(([^)]*)\)", line)
            if m:
                pk |= {c.strip() for c in m.group(1).split(",")}
                continue
            parts = line.split()
            cols.append({"name": parts[0], "type": parts[1], "pk": "PRIMARY KEY" in line, "uq": "UNIQUE" in line,
                         "nn": "NOT NULL" in line or "PRIMARY KEY" in line})
        for c in cols:
            c["pk"] = c["pk"] or c["name"] in pk
        tables[name] = cols
    return tables


T = parse(SCHEMA)
if VEC:
    T.update(parse("CREATE TABLE IF NOT EXISTS vectors (\n" + VEC.group(1).replace(", ", ",\n") + "\n);"))

o: list[str] = []
RH, HH, W = 15, 40, 300


def text(x, y, t, size=11, fill="#1b2333", bold=False, mono=False, anchor="start"):
    o.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" text-anchor="{anchor}"'
             + (' font-weight="700"' if bold else "") + (' font-family="Menlo,monospace"' if mono else "")
             + f">{t}</text>")


def table(name, x, y, color=("#e3edfb", "#3b6bc4")):
    bg, st = color
    cols = T[name]
    h = HH + RH * len(cols) + 6
    o.append(f'<rect x="{x}" y="{y}" width="{W}" height="{h}" rx="6" fill="#fff" stroke="{st}" stroke-width="1.6"/>')
    o.append(f'<rect x="{x}" y="{y}" width="{W}" height="{HH}" rx="6" fill="{bg}"/>')
    text(x + 8, y + 16, name, 13, st, bold=True, mono=True)
    text(x + 8, y + 31, PURPOSE.get(name, ""), 9.5, "#5a6475")
    for i, c in enumerate(cols):
        cy = y + HH + 12 + i * RH
        if i % 2:
            o.append(f'<rect x="{x+1}" y="{cy-11}" width="{W-2}" height="{RH}" fill="#f6f8fb"/>')
        tag = "PK" if c["pk"] else ("UQ" if c["uq"] else "")
        fk = FK.get((name, c["name"]))
        text(x + 8, cy, tag, 8.5, "#c0392b" if tag == "PK" else "#c98a10", bold=True)
        text(x + 28, cy, c["name"], 10, "#1b2333", bold=c["pk"], mono=True)
        text(x + 178, cy, c["type"].lower() + ("" if c["nn"] else "?"), 9, "#5a6475", mono=True)
        if fk:
            text(x + W - 6, cy, "→ " + fk.split(" (")[0], 9, "#7a4cc2", anchor="end")
    return h


# three columns of tables
XA, XB, XC, Y0, GAP = 10, 350, 690, 10, 22
y = Y0
for n in ("runs", "checkpoints", "outcomes"):
    y += table(n, XA, y) + GAP
ya = y
y = Y0
for n in ("approvals", "approval_callbacks", "erp_decisions"):
    y += table(n, XB, y, ("#e4f4e8", "#2f8a4b") if n != "erp_decisions" else ("#fbe4e4", "#c0392b")) + GAP
yb = y
y = Y0
for n in ("audit_events", "webhook_outbox", "webhook_subscriptions"):
    y += table(n, XC, y, ("#f1f3f6", "#5a6475") if n == "audit_events" else ("#fdf1d8", "#c98a10")) + GAP
yc = y
y = max(ya, yb, yc)
hb = table("eval_results", XA, y, ("#f1f3f6", "#5a6475"))
if "vectors" in T:
    table("vectors", XB, y, ("#efe6fb", "#7a4cc2"))
# files box
o.append(f'<rect x="{XC}" y="{y}" width="{W}" height="140" rx="6" fill="#f6f8fb" stroke="#d5dbe5" stroke-width="1.6"/>')
text(XC + 8, y + 18, "Files (all under data/)", 12, "#1d2a4d", bold=True)
for i, (f, d) in enumerate((("finagent.db", "every table on this page except vectors"), ("index/vectors.db", "vectors (SQLite, cosine search)"),
                            ("index/bm25_index.json", "keyword index + policy chunks"), ("audit.jsonl", "copy of audit_events, one JSON per line"))):
    text(XC + 8, y + 42 + i * 25, f, 10, "#1b2333", bold=True, mono=True)
    text(XC + 8, y + 54 + i * 25, d, 9, "#5a6475")
H = y + max(hb, 160) + 10
svg = (f'<svg viewBox="0 0 1000 {H}" width="100%" font-family="-apple-system, Helvetica Neue, Arial, sans-serif" '
       f'xmlns="http://www.w3.org/2000/svg">' + "".join(o) + "</svg>")

# relations mini-diagram
r: list[str] = []


def node(x, y, t, w=150, c="#3b6bc4", bg="#e3edfb"):
    r.append(f'<rect x="{x-w/2}" y="{y-14}" width="{w}" height="28" rx="5" fill="{bg}" stroke="{c}" stroke-width="1.6"/>')
    r.append(f'<text x="{x}" y="{y+5}" font-size="12" font-weight="700" font-family="Menlo,monospace" fill="{c}" text-anchor="middle">{t}</text>')


def pill(x, y, t, color="#c0392b", size=10.5):
    w = 7 * len(t) + 8
    r.append(f'<rect x="{x-w/2}" y="{y-9}" width="{w}" height="16" rx="3" fill="#fff"/>')
    r.append(f'<text x="{x}" y="{y+3}" font-size="{size}" font-weight="700" fill="{color}" text-anchor="middle">{t}</text>')


def edge(a, b, ca, cb, label="", t=(0.3, 0.7), dash=False, lt=0.5, dx=0):
    (x1, y1), (x2, y2) = a, b
    r.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#1d2a4d" stroke-width="1.4"'
             + (' stroke-dasharray="5 3"' if dash else '') + '/>')
    if label:
        mx, my = x1 + (x2 - x1) * lt + dx, y1 + (y2 - y1) * lt
        w = 5.6 * len(label) + 8
        r.append(f'<rect x="{mx-w/2}" y="{my-8}" width="{w}" height="15" rx="3" fill="#fff"/>')
        r.append(f'<text x="{mx}" y="{my+3}" font-size="9.5" fill="#5a6475" text-anchor="middle">{label}</text>')
    pill(x1 + (x2 - x1) * t[0], y1 + (y2 - y1) * t[0], ca)
    pill(x1 + (x2 - x1) * t[1], y1 + (y2 - y1) * t[1], cb)


R = (500, 110)
P = {"checkpoints": (170, 35), "audit_events": (170, 110), "outcomes": (170, 185), "approvals": (700, 35),
     "approval_callbacks": (900, 35), "erp_decisions": (700, 185), "webhook_outbox": (560, 228),
     "webhook_subscriptions": (300, 228)}
edge(R, P["checkpoints"], "1", "N", "every step")
edge(R, P["audit_events"], "1", "N", "events (or none)")
edge(R, P["outcomes"], "1", "1", "current outcome")
edge(R, P["approvals"], "1", "0..1", "approval request")
edge(P["approvals"], P["approval_callbacks"], "1", "N", t=(0.31, 0.5))
edge(R, P["erp_decisions"], "1", "0..1", "posted at most once", lt=0.62, t=(0.3, 0.86))
edge(P["approvals"], P["erp_decisions"], "1", "0..1", "", t=(0.25, 0.75))
edge(R, P["webhook_outbox"], "1", "N", "", t=(0.3, 0.72))
edge(P["webhook_subscriptions"], P["webhook_outbox"], "1", "N", t=(0.48, 0.66))
r.append(f'<path d="M{R[0]-30},{R[1]-14} C{R[0]-30},{R[1]-60} {R[0]+30},{R[1]-60} {R[0]+30},{R[1]-14}" fill="none" '
         f'stroke="#1d2a4d" stroke-width="1.4"/>')
r.append(f'<text x="{R[0]}" y="{R[1]-52}" font-size="9.5" fill="#5a6475" text-anchor="middle">parent_run_id: attempt 2, 3 ...</text>')
for n, (x, y) in P.items():
    c = {"approvals": ("#2f8a4b", "#e4f4e8"), "approval_callbacks": ("#2f8a4b", "#e4f4e8"), "erp_decisions": ("#c0392b", "#fbe4e4"),
         "audit_events": ("#5a6475", "#f1f3f6"), "webhook_outbox": ("#c98a10", "#fdf1d8"),
         "webhook_subscriptions": ("#c98a10", "#fdf1d8")}.get(n, ("#3b6bc4", "#e3edfb"))
    node(x, y, n, w=9 * len(n) + 22, c=c[0], bg=c[1])
# search index (separate files): each vector is the embedding of one policy chunk; runs cite chunks by chunk_id
CH, VE = (800, 292), (240, 292)
edge(R, CH, "N", "N", "cited in results (chunk_id); vectors are read-only", t=(0.2, 0.86), dash=True, lt=0.72, dx=150)
edge(VE, CH, "1", "1", "embedding of (chunk_id)", t=(0.3, 0.62))
node(CH[0], CH[1], "policy chunk (index/bm25_index.json)", w=330, c="#2f8a4b", bg="#e4f4e8")
node(VE[0], VE[1], "vectors (index/vectors.db)", w=240, c="#7a4cc2", bg="#efe6fb")
node(R[0], R[1], "runs", w=90)
r.append('<text x="990" y="312" font-size="9.5" fill="#5a6475" text-anchor="end">eval_results: standalone</text>')
r.append('<line x1="10" y1="258" x2="990" y2="258" stroke="#d5dbe5" stroke-dasharray="3 3"/>')
r.append('<text x="12" y="272" font-size="9" fill="#5a6475">below: search index files, rebuilt from the corpus at start-up (above: finagent.db)</text>')
rel = ('<svg viewBox="0 0 1000 318" width="100%" font-family="-apple-system, Helvetica Neue, Arial, sans-serif" '
       'xmlns="http://www.w3.org/2000/svg">' + "".join(r) + "</svg>")

html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>finagent - Database tables and relations</title>
<style>
  @page {{ size: A4; margin: 10mm 10mm 10mm 10mm; }}
  body {{ font: 9pt/1.35 -apple-system, "Helvetica Neue", Arial, sans-serif; color: #1b2333; margin: 0; }}
  h2 {{ font-size: 13pt; color: #fff; background: #1d2a4d; padding: 5pt 8pt; margin: 0 0 5pt; border-radius: 3pt; }}
  p {{ margin: 0 0 4pt; color: #5a6475; font-size: 8pt; }}
  code {{ font: 7.8pt Menlo, monospace; background: #f2f4f8; padding: 0 2pt; }}
  .rel {{ border: 1px solid #d5dbe5; border-radius: 4pt; padding: 3pt 4pt 0; margin-top: 4pt; }}
  .rel b {{ font-size: 8.5pt; color: #1d2a4d; }}
</style></head><body>
<h2>finagent database: tables, columns and relations</h2>
<p>SQLite (<code>sqlite3</code>, no ORM) - <code>src/finagent/store.py</code>. <b style="color:#c0392b">PK</b> primary key ·
<b style="color:#c98a10">UQ</b> unique · <code>?</code> nullable · <span style="color:#7a4cc2">→</span> refers to (by id).
No FOREIGN KEY constraints are declared: links are ids, written and checked in code. JSON is stored as TEXT
(<code>*_json</code>, <code>payload</code>, <code>details</code>). Writes use <code>BEGIN IMMEDIATE</code>; one writer.</p>
{svg}
<div class="rel"><b>Relations</b> (1 = one, N = many, 0..1 = at most one)
{rel}
</div>
<p style="margin-top:4pt">Generated from the CREATE TABLE statements by <code>scripts/build_db_schema_page.py</code> (<code>make control-pdf</code>).
Full column notes: <code>docs/database.md</code>.</p>
</body></html>
"""
out = ROOT / "docs/source/db_schema.html"
out.write_text(html, encoding="utf-8")
print(f"wrote {out.relative_to(ROOT)}: {len(T)} tables")
