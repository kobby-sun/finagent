#!/usr/bin/env python3
"""Render docs/diagrams/workflow_scenarios.pdf (one page per workflow scenario) and docs/workflows.md
from scripts/workflow_catalog.py. Run: python scripts/build_workflow_pdf.py  (or make diagrams)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_arch_pdf as B  # noqa: E402
from workflow_catalog import SCENARIOS  # noqa: E402

KIND = {"step": (B.BLUE, B.BLUE_S), "check": (B.PURPLE, B.PURPLE_S), "ai": (B.AMBER, B.AMBER_S), "person": (B.RED, B.RED_S),
        "ok": (B.GREEN, B.GREEN_S), "hold": (B.AMBER, B.AMBER_S), "stop": (B.RED, B.RED_S), "fail": (B.RED, B.RED_S),
        "blocked": (B.GREY, B.GREY_S)}
ENDS = {"ok", "hold", "stop", "fail", "blocked"}


def draw_path(p: B.Page, path, y0):
    per_row = 6
    bw, bh, gap = 118, 54, 16
    rows = [path[i:i + per_row] for i in range(0, len(path), per_row)]
    y = y0
    n = 0
    for r, row in enumerate(rows):
        for j, (title, body, kind) in enumerate(row):
            x = 30 + j * (bw + gap)
            f, s = KIND[kind]
            last = (r == len(rows) - 1 and j == len(row) - 1)
            p.rect(x, y, bw, bh, f, s, 2.4 if last else 1.2, r=9)
            n += 1
            p.rect(x - 6, y - 6, 15, 15, (0.11, 0.17, 0.30), None, r=7)
            p.text(x + 1.5, y + 4.5, str(n), 7, True, (1, 1, 1), "center")
            p.text(x + bw / 2, y + 22, title, 8.6, True, B.INK, "center")
            for k, ln in enumerate(p.wrap(body, bw - 12, 7)):
                p.text(x + bw / 2, y + 34 + k * 8.5, ln, 7, False, B.MUTED, "center")
            if j:
                p.arrow(x - gap, y + bh / 2, x, y + bh / 2)
        if r < len(rows) - 1:
            lx = 30 + (len(row) - 1) * (bw + gap) + bw / 2
            p.poly_arrow([(lx, y + bh), (lx, y + bh + 14), (30 + bw / 2, y + bh + 14), (30 + bw / 2, y + bh + 30)])
        y += bh + 30
    return y


def section(p: B.Page, y, label, text, width=780, size=9):
    p.text(30, y, label, 9.6, True, B.BLUE_S)
    lines = []
    for para in (text if isinstance(text, list) else [text]):
        wl = p.wrap(para, width, size)
        lines += [("- " if isinstance(text, list) else "") + wl[0]] + ["  " + w for w in wl[1:]]
    for i, ln in enumerate(lines):
        p.text(30, y + 14 + i * (size + 3), ln, size, False, B.INK)
    return y + 14 + len(lines) * (size + 3) + 10


def scenario_page(sc, n, total) -> B.Page:
    p = B.Page()
    p.header(f"{sc['id']}. {sc['title']}", "", n, total)
    p.rect(24, 74, 794, 46, (0.95, 0.97, 1.0), B.BLUE_S, 0.8, r=8)
    p.text(34, 89, "Business meaning", 9.4, True, B.BLUE_S)
    for i, ln in enumerate(p.wrap(sc["meaning"], 760, 9.2)[:3]):
        p.text(34, 102 + i * 11, ln, 9.2, False, B.INK)
    y = section(p, 140, "When it happens", sc["triggers"])
    p.text(30, y + 2, "Flow (numbered in order; outlined box = where it ends)", 9.6, True, B.BLUE_S)
    y = draw_path(p, sc["path"], y + 20)
    y = section(p, y + 4, "Outcome", sc["outcome"])
    y = section(p, y, "Example test cases", sc["examples"])
    section(p, y, "Evidence in the logs", sc["logs"])
    x = 30
    for lab_, k in (("system step", "step"), ("automatic check", "check"), ("AI", "ai"), ("person", "person"),
                    ("finished", "ok"), ("held / waiting", "hold"), ("refused / no effect", "blocked")):
        f, s = KIND[k]
        p.rect(x, 566, 12, 10, f, s, 0.8, r=2)
        p.text(x + 16, 574, lab_, 7.4, False, B.MUTED)
        x += 28 + B.text_w(lab_, 7.4)
    return p


def index_page(total) -> B.Page:
    p = B.Page()
    p.header("Workflow scenarios - index", "", 1, total)
    p.text(30, 70, "Pages 2-3: every step and flow on one map. Then one page per scenario: business meaning, when it happens, the flow, the outcome, test cases, log evidence.",
           9, False, B.MUTED)
    for i, sc in enumerate(SCENARIOS):
        per_col = -(-len(SCENARIOS) // 3)
        col, row = divmod(i, per_col)
        x, y = 24 + col * 268, 88 + row * 66
        p.rect(x, y, 258, 58, (1, 1, 1), B.GREY_S, 0.8, r=8)
        title = p.wrap(f"{sc['id']}.  {sc['title']}", 214, 8.2)[0]
        p.text(x + 8, y + 14, title, 8.2, True, B.INK)
        for k, ln in enumerate(p.wrap(sc["meaning"], 242, 6.6)[:4]):
            p.text(x + 8, y + 25 + k * 8, ln, 6.6, False, B.MUTED)
        p.text(x + 250, y + 14, f"p.{i + FIRST}", 7.6, False, B.BLUE_S, "right")
    return p


def build_md():
    md = ["# Workflow scenarios", "", "_Generated from `scripts/workflow_catalog.py` by `python scripts/build_workflow_pdf.py`; the PDF version "
          "(one page per scenario) is [diagrams/workflow_scenarios.pdf](diagrams/workflow_scenarios.pdf)._", "",
          "| # | Scenario | Business meaning |", "|---|---|---|"]
    md += [f"| {s['id']} | [{s['title']}](#{s['id'].lower()}-{'-'.join(s['title'].lower().replace(':', '').replace('(', '').replace(')', '').replace('/', '').split())}) | {s['meaning']} |" for s in SCENARIOS]
    for s in SCENARIOS:
        md += ["", f"## {s['id']}. {s['title']}", "", f"**Business meaning.** {s['meaning']}", "", f"**When it happens.** {s['triggers']}", "",
               "```mermaid", "flowchart LR"]
        for i, (t, b, k) in enumerate(s["path"]):
            label = f"{i + 1}. {t}" + (f"<br/><small>{b}</small>" if b else "")
            shape = f'(["{label}"])' if k in ENDS and i == len(s["path"]) - 1 else f'["{label}"]'
            md.append(f"  N{i}{shape}")
            if i:
                md.append(f"  N{i - 1} --> N{i}")
        md += ["```", "", "**Outcome.**", ""] + [f"- {o}" for o in s["outcome"]] + \
              ["", f"**Example test cases.** {s['examples']}", "", f"**Evidence in the logs.** {s['logs']}"]
    (B.ROOT / "docs" / "workflows.md").write_text("\n".join(md) + "\n")


def overview_page(title, sub, fn, n, total) -> B.Page:
    p = B.Page()
    p.header(title, "", n, total)
    p.text(28, 64, sub, 9, False, B.MUTED)
    fn(p)
    return p


OVERVIEW = [("Workflow: every step and flow (1/2) - processing a case", "From request to the stop point: every step, every decision, every exit.",
             B.page_flow_processing),
            ("Workflow: every step and flow (2/2) - after the stop", "Approval desk, exactly-once posting with policy re-check, new evidence, recovery.",
             B.page_flow_after)]
FIRST = 2 + len(OVERVIEW)      # page number of the first scenario


def main():
    total = len(SCENARIOS) + FIRST - 1
    pages = [index_page(total)] + [overview_page(t, sub, fn, i + 2, total) for i, (t, sub, fn) in enumerate(OVERVIEW)] \
        + [scenario_page(sc, i + FIRST, total) for i, sc in enumerate(SCENARIOS)]
    out = B.ROOT / "docs" / "diagrams" / "workflow_scenarios.pdf"
    out.write_bytes(B.build(pages))
    build_md()
    print(f"wrote {out} ({len(pages)} pages) and docs/workflows.md")


if __name__ == "__main__":
    main()
