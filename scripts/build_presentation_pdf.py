#!/usr/bin/env python3
"""Render docs/diagrams/finagent_design_presentation.pdf - a simplified, slide-style (16:9) version of the
detailed design for presentations. Same dependency-free PDF writer as build_arch_pdf.py.
Run:  python scripts/build_presentation_pdf.py   (or: make diagrams)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_arch_pdf as B  # noqa: E402

B.W, B.H = 960, 540          # 16:9 slides
W, H = B.W, B.H
NAVY = (0.11, 0.17, 0.30)
WHITE = (1, 1, 1)


def slide(title: str, n: int, total: int, subtitle: str = "") -> B.Page:
    p = B.Page()
    p.rect(0, 0, W, 64, NAVY, None, r=0)
    p.text(36, 41, title, 24, True, WHITE)
    p.text(W - 36, 40, f"{n} / {total}", 11, False, (0.75, 0.80, 0.92), "right")
    if subtitle:
        p.text(36, 92, subtitle, 13, False, B.MUTED)
    return p


def big_box(p, x, y, w, h, title, body, fill, stroke, ts=15, bs=11):
    p.rect(x, y, w, h, fill, stroke, 1.4, r=10)
    lines = p.wrap(body, w - 24, bs) if body else []
    total = ts + 8 + len(lines) * (bs + 3)
    ty = y + (h - total) / 2 + ts
    p.text(x + w / 2, ty, title, ts, True, B.INK, "center")
    for i, ln in enumerate(lines):
        p.text(x + w / 2, ty + ts * 0.6 + 10 + i * (bs + 3), ln, bs, False, B.MUTED, "center")


def s_title(total, n=1):
    p = B.Page()
    p.rect(0, 0, W, H, NAVY, None, r=0)
    p.text(70, 210, "finagent", 52, True, WHITE)
    p.text(70, 255, "Accounts-payable RAG workflow agent", 22, False, (0.85, 0.89, 0.97))
    p.text(70, 300, "Reads the invoice. Checks it against policy and systems of record.", 15, False, (0.80, 0.85, 0.95))
    p.text(70, 322, "Explains the recommendation with citations. Waits for a human before any money decision.", 15, False, (0.80, 0.85, 0.95))
    p.text(70, 470, "Design overview - architecture and workflows", 12, True, (0.70, 0.76, 0.88))
    return p


def s_glance(total, n):
    p = slide("finagent at a glance", n, total, "Five layers inside one service")
    layers = [
        ("Entry", "API (Swagger) - CLI - approver decisions", B.GREY, B.GREY_S),
        ("Engine", "Step-by-step workflow - saves progress after every step - hard limits on work per case", B.PURPLE, B.PURPLE_S),
        ("Skills", "Readers for JSON, text, CSV, e-mail, PDF - 12 checks with exact maths in code; 4 ask the AI fixed questions", B.PURPLE, B.PURPLE_S),
        ("Evidence", "Policy search (RAG) - vendor, PO, receipts, history, FX, approval limits - AI model writes the explanation", B.GREEN, B.GREEN_S),
        ("Records", "Case store - tamper-evident audit trail - status notifications (webhooks)", B.BLUE, B.BLUE_S),
    ]
    y = 118
    for i, (name, body, f, s) in enumerate(layers):
        p.rect(36, y, 150, 70, s, None, r=10)
        p.text(111, y + 42, name, 17, True, WHITE, "center")
        p.rect(196, y, 728, 70, f, s, 1.3, r=10)
        p.text(216, y + 41, body, 13, False, B.INK)
        if i < len(layers) - 1:
            p.arrow(111, y + 70, 111, y + 82, rgb=B.LINE)
        y += 82
    return p


def s_flow(total, n):
    p = slide("One invoice, end to end", n, total, "Code decides - the model explains - a person approves")
    steps = [("1", "Receive", "request + invoice files in any format"),
             ("2", "Find policy", "search the finance policy library"),
             ("3", "Gather facts", "vendor, PO, receipts, history, rates"),
             ("4", "Check", "11 skills: code does the maths; the AI answers fixed questions, code decides"),
             ("5", "Recommend", "cited summary; model may only be more cautious"),
             ("6", "Approve", "authorised person decides; post exactly once")]
    x, w, gap = 30, 140, 14
    for i, (n, t, b) in enumerate(steps):
        fill, stroke = (B.RED, B.RED_S) if n == "6" else ((B.AMBER, B.AMBER_S) if n == "5" else (B.BLUE, B.BLUE_S))
        p.rect(x, 130, w, 150, fill, stroke, 1.4, r=12)
        p.rect(x + w / 2 - 17, 115, 34, 34, stroke, None, r=17)
        p.text(x + w / 2, 138, n, 16, True, WHITE, "center")
        p.text(x + w / 2, 182, t, 15, True, B.INK, "center")
        for j, ln in enumerate(p.wrap(b, w - 18, 10.5)):
            p.text(x + w / 2, 208 + j * 14, ln, 10.5, False, B.MUTED, "center")
        if i < len(steps) - 1:
            p.arrow(x + w, 205, x + w + gap, 205, rgb=B.LINE, lw=1.6)
        x += w + gap
    p.text(36, 330, "Four possible outcomes", 15, True, B.INK)
    outs = [("Approve for posting", "all checks pass - goes to an approver", B.GREEN, B.GREEN_S),
            ("Hold", "something is missing or out of tolerance - routed to its owner", B.AMBER, B.AMBER_S),
            ("Reject", "duplicate or invalid invoice - approver confirms", B.GREY, B.GREY_S),
            ("Escalate", "fraud signals - sent to the controls team", B.RED, B.RED_S)]
    x = 36
    for t, b, f, s in outs:
        big_box(p, x, 345, 214, 92, t, b, f, s, 14, 10.5)
        x += 224
    p.text(36, 475, "Paused cases resume where they stopped - after an approval, new evidence, or a restart.", 12, False, B.MUTED)
    return p


def s_safety(total, n):
    p = slide("Built-in safety", n, total, "Six guarantees enforced in code, each covered by automated tests")
    tiles = [("Code does the maths", "Totals, tolerances and limits are calculated exactly in code. The AI judges, but can only add caution."),
             ("Text never gives orders", "Instructions hidden in invoices or e-mails are flagged as fraud signals, not followed."),
             ("Every claim is cited", "Findings must point to a current policy section that was actually retrieved."),
             ("Approval before money", "Posting is blocked unless an authorised, independent approver says yes."),
             ("Once and only once", "Duplicate approvals and crashes cannot cause a second posting."),
             ("Tamper-evident trail", "Every step is logged, chained by hash, with bank details masked.")]
    for i, (t, b) in enumerate(tiles):
        col, row = i % 3, i // 3
        big_box(p, 36 + col * 300, 120 + row * 175, 288, 160, t, b, (1, 1, 1), B.PURPLE_S, 16, 12)
    return p


KIND = {"step": (B.BLUE, B.BLUE_S), "check": (B.PURPLE, B.PURPLE_S), "ai": (B.AMBER, B.AMBER_S),
        "person": (B.RED, B.RED_S), "good": (B.GREEN, B.GREEN_S), "stop": (B.GREY, B.GREY_S), "warn": (B.AMBER, B.AMBER_S)}


def flow(p, y, steps, x0=30, w=None, gap=14, h=96, ts=12.5, bs=9.6):
    """Horizontal flow of (title, body, kind) boxes; returns box centre x positions."""
    n = len(steps)
    w = w or (W - 2 * x0 - gap * (n - 1)) / n
    xs = []
    for i, (t, b, k) in enumerate(steps):
        x = x0 + i * (w + gap)
        f, st = KIND[k]
        p.rect(x, y, w, h, f, st, 1.3, r=10)
        p.text(x + w / 2, y + 24, t, ts, True, B.INK, "center")
        for j, ln in enumerate(p.wrap(b, w - 14, bs)):
            p.text(x + w / 2, y + 42 + j * (bs + 3), ln, bs, False, B.MUTED, "center")
        if i:
            p.arrow(x - gap, y + h / 2, x, y + h / 2, rgb=B.LINE, lw=1.5)
        xs.append((x, w))
    return xs


def note(p, y, label, text):
    p.text(36, y, label, 11, True, B.INK)
    p.text(36 + B.text_w(label, 11, True) + 8, y, text, 11, False, B.MUTED)


def legend(p):
    x = 36
    for lab, k in (("system step", "step"), ("automatic check", "check"), ("AI writes", "ai"), ("a person decides", "person"),
                   ("finished", "good"), ("waiting / held", "stop")):
        f, s_ = KIND[k]
        p.rect(x, 512, 13, 11, f, s_, 0.9, r=2)
        p.text(x + 18, 521, lab, 9, False, B.MUTED)
        x += 30 + B.text_w(lab, 9)


def s_arch(total, n):
    p = slide("Architecture", n, total, "The main parts, in everyday terms")
    # who sends work
    p.zone(20, 108, 150, 390, "Who sends work", B.GREY, B.GREY_S)
    for i, (t, b) in enumerate([("Accounts payable team", "submits invoices to check"), ("Client systems", "send invoices through the API in our format"),
                                ("Approvers", "managers who say yes or no")]):
        big_box(p, 30, 135 + i * 120, 130, 100, t, b, (1, 1, 1), B.GREY_S, 11, 9.5)
    # finagent
    p.zone(186, 108, 560, 390, "finagent (one application)", (0.98, 0.985, 1.0), B.BLUE_S)
    big_box(p, 200, 132, 532, 46, "Front door", "website / API / command line - checks every request is complete", B.BLUE, B.BLUE_S, 12, 9.5)
    big_box(p, 200, 192, 532, 50, "Case manager", "runs each invoice through the steps, saves progress after every step, can pick up after a restart", B.PURPLE, B.PURPLE_S, 12, 9.5)
    parts = [("Document reader", "reads PDF, e-mail, spreadsheet, JSON invoices", "step"),
             ("Policy library search", "finds the finance rules that apply", "good"),
             ("Skill checks", "exact maths in code; fixed questions for judgement", "check"),
             ("AI", "answers the questions, then explains the result with sources", "ai")]
    for i, (t, b, k) in enumerate(parts):
        f, st = KIND[k]
        big_box(p, 200 + i * 135, 256, 127, 92, t, b, f, st, 11, 9)
    big_box(p, 200, 362, 260, 58, "Approval desk", "asks the right person, waits, checks they are allowed", B.RED, B.RED_S, 12, 9.5)
    big_box(p, 472, 362, 260, 58, "Notifications", "tells other systems when status changes", B.BLUE, B.BLUE_S, 12, 9.5)
    big_box(p, 200, 432, 532, 52, "Case records + audit log", "every case, decision and step stored; log is tamper-evident; bank details masked", B.GREY, B.GREY_S, 12, 9.5)
    # company systems
    p.zone(762, 108, 180, 390, "Company systems", (1.0, 0.98, 0.95), B.AMBER_S)
    for i, (t, b) in enumerate([("Supplier list", "status, bank (masked)"), ("Purchase orders", "+ delivery records"), ("Past invoices", "paid / held / rejected"),
                                ("Exchange rates", "daily corporate rate"), ("Approval limits", "who can approve what")]):
        big_box(p, 772, 133 + i * 56, 160, 48, t, b, B.AMBER, B.AMBER_S, 10.5, 8.5)
    big_box(p, 772, 418, 160, 66, "Accounting system", "posting - only after approval (simulated)", B.RED, B.RED_S, 10.5, 8.5)
    p.text(36, 92, "", 1)
    # arrows
    for yy in (185, 305, 412):
        p.arrow(160, yy, 200, min(max(yy, 150), 400), rgb=B.LINE, lw=1.3)
    p.arrow(466, 178, 466, 192, rgb=B.LINE)
    p.arrow(466, 242, 466, 256, rgb=B.LINE)
    p.arrow(732, 300, 772, 230, rgb=B.LINE, lw=1.3)
    p.arrow(732, 390, 772, 450, rgb=B.RED_S, lw=1.3)
    p.text(36, 520, "Read-only access to company systems. The only action that changes money records is posting, and it is blocked until a person approves.",
           10, False, B.MUTED)
    return p


def s_workflow_map(total, n):
    p = slide("All workflows", n, total, "Every path an invoice (or the system) can take")
    tiles = [("A  Standard invoice", "checks pass -> approver says yes -> posted once"),
             ("B  Duplicate invoice", "already paid -> approver confirms rejection"),
             ("C  Problem invoice", "price, quantity, tax, missing order or delivery -> held"),
             ("D  Supplier problem", "blocked, dormant, sanctions, new bank details -> held"),
             ("E  Suspicious invoice", "fraud signals or hidden instructions -> escalated"),
             ("F  Approvals", "right authority, two approvers, duplicate clicks, rejection"),
             ("G  Special invoices", "no purchase order, credit notes, foreign currency"),
             ("H  Recovery", "system down, crash, restart, new evidence -> resume"),
             ("I  Running & changing it", "policy changes by tested release; new file types, deploy")]
    for i, (t, b) in enumerate(tiles):
        col, row = i % 3, i // 3
        big_box(p, 36 + col * 300, 112 + row * 128, 288, 112, t, b, (1, 1, 1), B.BLUE_S, 15, 11)
    return p


def s_wf_standard(total, n):
    p = slide("A  Standard invoice", n, total, "Everything matches -> a person approves -> posted exactly once")
    flow(p, 125, [("Invoice arrives", "request + invoice file", "step"), ("Read it", "document reader pulls out lines and totals", "step"),
                  ("Look up", "order, delivery, supplier, past invoices, policy", "step"),
                  ("Check", "prices, quantities, tax, duplicates, supplier, limits", "check"),
                  ("Explain", "AI writes a cited summary", "ai"), ("Approver", "authorised person says yes", "person"),
                  ("Posted once", "simulated posting + payment date", "good")])
    note(p, 270, "Example:", "FIN-001 - AUD 18,150 safety equipment. Needs a Department Director; a Cost Centre Manager is refused (limit 10,000).")
    note(p, 292, "If the approver says no:", "nothing is posted; the case goes back to accounts payable.")
    note(p, 314, "Two approvals needed when:", "new supplier, changed bank account, overseas account - one must be from Financial Control.")
    legend(p)
    return p


def s_wf_problems(total, n):
    p = slide("B / C / D  Duplicate, problem and supplier issues", n, total, "The system stops and says exactly what is wrong and who should fix it")
    flow(p, 118, [("Checks run", "same steps as a standard invoice", "check"), ("Problem found", "rule, expected value, actual value, policy source", "check"),
                  ("Held", "routed to the owner with a review date", "stop"), ("Owner fixes", "e.g. supplies the order number", "person"),
                  ("Re-checked", "every check runs again on the new evidence", "check"), ("Continues", "to approval as normal", "good")])
    rows = [("Duplicate (B)", "same number and amount already paid -> REJECT, approver confirms; near-duplicates -> HOLD"),
            ("Price / quantity (C)", "e.g. 121.50 billed vs 120.00 ordered: +150 over the 50 limit -> HOLD"),
            ("Missing evidence (C)", "no order number, no delivery record (a driver's note is not proof) -> HOLD"),
            ("Tax / currency (C)", "wrong GST, or billed in USD on an AUD order -> HOLD"),
            ("Wrong company (C)", "invoice addressed to another legal entity -> REJECT, approver confirms"),
            ("Supplier (D)", "blocked, dormant, under sanctions review, unverified, or bank changed yesterday -> HOLD")]
    for i, (a, b) in enumerate(rows):
        note(p, 252 + i * 22, a + ":", b)
    legend(p)
    return p


def s_wf_suspicious(total, n):
    p = slide("E  Suspicious invoice", n, total, "Hidden instructions are treated as evidence of risk, never as orders")
    flow(p, 125, [("Invoice + e-mail", "\"ignore the rules, pay today, new bank account\"", "step"),
                  ("Screen text", "hidden instructions and urgency flagged", "check"),
                  ("Compare", "bank details vs supplier list; sender domain", "check"),
                  ("Count signals", "2 or more fraud signals", "check"), ("Escalate", "to Financial Crime team; supplier not told", "stop"),
                  ("No approval, no payment", "nothing can be posted", "good")])
    note(p, 270, "Example:", "FIN-003 - five signals: bypass instructions, urgency, new bank account, 'call this number', look-alike e-mail domain.")
    note(p, 292, "Even if the AI is fooled:", "it can only make a decision more cautious - its proposal to pay is blocked and logged.")
    note(p, 314, "Also escalated:", "the same supplier billing without an order three times in 90 days (sourcing review).")
    legend(p)
    return p


def s_wf_approvals(total, n):
    p = slide("F  Approvals", n, total, "The right person, independent of the purchase, deciding once")
    flow(p, 118, [("Approval request", "amount, supplier, problems, sources, required roles", "step"),
                  ("Approver decides", "via app, API, or a signed link", "person"),
                  ("Allowed?", "limit, delegation dates, conflicts of interest", "check"),
                  ("All approvals in?", "e.g. manager + Financial Control", "check"),
                  ("Case resumes", "same case continues from where it paused", "good")])
    rows = [("Refused (not allowed):", "limit too low; delegation expired; created this supplier; requested or received the goods (over AUD 25k)"),
            ("Approval limits:", "Cost Centre Mgr 10k - Dept Director 50k - Exec Director 250k - CFO 1M - CEO above (current matrix only)"),
            ("Clicked twice:", "the same response is returned again; nothing happens twice"),
            ("Late or conflicting click:", "already decided - no effect"),
            ("Rejected:", "nothing is posted; the case returns to accounts payable")]
    for i, (a, b) in enumerate(rows):
        note(p, 252 + i * 24, a, b)
    legend(p)
    return p


def s_wf_special(total, n):
    p = slide("G  Special invoices", n, total, "Different rules apply automatically, based on the invoice")
    cols = [("No purchase order", ["Allowed only for utilities, leases, statutory charges, memberships, genuine emergencies",
                                   "Needs internal proof of service", "Expired contract or supplier pressure is NOT an emergency",
                                   "Up to 10k: Department Director; above: + Procurement", "3rd time in 90 days: escalated"]),
            ("Credit note", ["Must refer to a real earlier invoice", "Same supplier and currency; not more than the original",
                             "Tax checked separately", "Applied against open invoices before any refund"]),
            ("Foreign currency", ["Invoice currency must match the order", "Converted to AUD at the company's daily rate for the approval limit",
                                  "Rates in supplier documents are ignored", "Overseas bank account: Financial Control co-approves + fresh sanctions check"])]
    for i, (t, items) in enumerate(cols):
        x = 36 + i * 300
        p.rect(x, 112, 288, 330, (1, 1, 1), B.PURPLE_S, 1.3, r=12)
        p.text(x + 144, 140, t, 16, True, B.INK, "center")
        yy = 168
        for it in items:
            for j, ln in enumerate(p.wrap(it, 252, 11)):
                p.text(x + 18, yy, ("- " if j == 0 else "  ") + ln, 11, False, B.INK if j == 0 else B.MUTED)
                yy += 16
            yy += 6
    return p


def s_wf_recovery(total, n):
    p = slide("H  Recovery", n, total, "Failures pause the case safely; nothing is guessed and nothing is done twice")
    flow(p, 112, [("Company system slow", "tried 3 times, then marked unknown", "warn"), ("Held", "missing evidence listed", "stop"),
                  ("Evidence added", "e.g. order number supplied", "person"), ("Fresh attempt", "all checks re-run; old result kept", "good")], h=88)
    flow(p, 232, [("App crashes", "mid-case or right after posting", "warn"), ("Restart", "automatic every 5 minutes in the cloud", "step"),
                  ("Resume", "continues from the last saved step", "step"), ("Exactly once", "posting reference reused, never repeated", "good")], h=88)
    flow(p, 352, [("AI answer garbled", "fails the format or cites unknown sources", "warn"), ("Retry", "asked again with the errors", "ai"),
                  ("Still bad", "safe standard wording, flagged", "stop"), ("Decision unaffected", "rules decided the outcome anyway", "good")], h=88)
    return p


def s_wf_operate(total, n):
    p = slide("I  Running and changing it", n, total, "Change without code where possible - and every change is tested")
    flow(p, 112, [("New policy", "update the policy and the checks it affects", "step"), ("Tested", "full tests + evaluation pass (slide J)", "check"),
                  ("Signed off", "finance owner records the new baseline", "person"), ("Released", "restart: index rebuilt at start-up", "good")], h=84)
    flow(p, 222, [("New input type", "e.g. Word, XML e-invoice, scanned PDF", "step"), ("Add a reader", "small plug-in, standard format", "step"),
                  ("Add its tests", "at least 3 examples", "check"), ("Live", "flows through the same checks", "good")], h=84)
    flow(p, 332, [("Change the checks", "re-order, switch off optional ones", "step"), ("One config file", "workflow settings", "step"),
                  ("Safety guard", "refuses to remove required checks", "check"), ("Recorded", "version stamped on every case", "good")], h=84)
    p.text(36, 452, "Runs on a laptop with Docker, or in AWS (set up from code). Posting is simulated: no real money can move.", 11, False, B.MUTED)
    return p


def s_wf_policy_change(total, n):
    p = slide("J  When the policy changes", n, total, "A policy change is a tested maintenance release - never a live change")
    flow(p, 112, [("Policy updated", "finance changes a policy document", "step"), ("Checks updated", "code, settings and tests changed to match", "step"),
                  ("Tested", "full tests + evaluation pass", "check"),
                  ("Signed off", "finance owner records the new baseline", "person"), ("Restart", "applies to the next invoice", "good")], h=92)
    flow(p, 236, [("Start-up", "the system checks itself", "step"), ("Library changed?", "search index rebuilt", "step"),
                  ("Search model older?", "retrained, kept only if it passes the accuracy test", "check"), ("Cannot retrain", "keyword search only", "stop")], h=92)
    flow(p, 360, [("Policy not signed off", "differs from the reviewed baseline", "warn"), ("Every invoice held", "health check: maintenance required", "stop"),
                  ("Release finished", "sign-off + restart", "person"), ("Back to normal", "cases decided again", "good")], h=92)
    p.text(36, 478, "Why not let the policy change live? A running system never changes its rules mid-way: same answer every time,", 11, False, B.MUTED)
    p.text(36, 494, "tested checks, and a named person behind every interpretation.", 11, False, B.MUTED)
    return p


def s_infra(total, n):
    """Plain-language infrastructure with the reason for each part and the alternative if things change."""
    p = slide("Infrastructure: what we use and why", n, total, "Every part has a reason - and a planned replacement if circumstances change")
    rows = [("Runs on", "Docker on a laptop; AWS Fargate in the cloud", "same package everywhere, no servers to patch",
             "Kubernetes if the company runs one"),
            ("Case records", "SQLite file database", "zero setup; safe for one instance", "PostgreSQL when several copies run"),
            ("Policy search", "Built-in search + small trained AI search model", "accurate on our policy wording; no extra service",
             "OpenSearch / Bedrock Knowledge Bases for thousands of documents"),
            ("AI writer", "Claude via Bedrock or API; local model option", "best explanations; data can stay in-region",
             "local model if no data may leave the network"),
            ("Notifications", "Signed webhooks with retry", "works with ServiceNow, Slack, e-mail tools", "AWS SQS / EventBridge at high volume"),
            ("Audit log", "Tamper-evident log, bank details masked", "every step provable", "S3 Object Lock for regulator retention"),
            ("Secrets & access", "AWS Secrets Manager + least-privilege roles", "no secrets in code; nothing can move money",
             "Vault in multi-cloud setups"),
            ("Set-up", "Terraform scripts", "repeatable, reviewable environments", "AWS CDK if developers prefer code")]
    xs, ws = [36, 176, 446, 696], [136, 266, 246, 228]
    heads = ["Part", "What we use", "Why", "Switch to ... when"]
    p.rect(30, 108, 900, 26, NAVY, None, r=6)
    for x, h_ in zip(xs, heads):
        p.text(x, 126, h_, 12, True, WHITE)
    y = 140
    for k, row in enumerate(rows):
        lines = [p.wrap(c, w - 10, 10.5) for c, w in zip(row, ws)]
        h = max(len(l) for l in lines) * 14 + 12
        if k % 2:
            p.rect(30, y, 900, h, (0.96, 0.97, 0.99), None, r=0)
        for x, ls, bold in zip(xs, lines, [True, False, False, False]):
            for j, ln in enumerate(ls):
                p.text(x, y + 18 + j * 14, ln, 10.5, bold, B.INK if bold or x < 600 else B.MUTED)
        y += h
    p.text(36, 512, "Whatever we swap, the checks, approvals, audit trail and the 63 test scenarios stay the same.", 11, True, B.PURPLE_S)
    return p


def main():
    builders = [s_title, s_arch, s_infra, s_glance, s_flow, s_workflow_map, s_wf_standard, s_wf_problems, s_wf_suspicious,
                s_wf_approvals, s_wf_special, s_wf_recovery, s_wf_operate, s_wf_policy_change, s_safety]
    pages = [b(len(builders), i) for i, b in enumerate(builders, start=1)]
    out = B.ROOT / "docs" / "diagrams" / "finagent_design_presentation.pdf"
    out.write_bytes(B.build(pages))
    print(f"wrote {out} ({len(pages)} slides)")


if __name__ == "__main__":
    main()
