#!/usr/bin/env python3
"""Render docs/diagrams/architecture.pdf - the architecture diagrams as vector PDF pages.

No third-party dependencies or downloads: a tiny PDF writer draws boxes, arrows and text with
Helvetica. The Mermaid sources in docs/architecture.md remain the editable form; this script is
the printable / shareable export. Run:  python scripts/build_arch_pdf.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
W, H = 842, 595  # A4 landscape (points)

# palette
INK = (0.13, 0.16, 0.22)
MUTED = (0.38, 0.42, 0.50)
LINE = (0.45, 0.50, 0.58)
BLUE = (0.88, 0.93, 1.00)
BLUE_S = (0.30, 0.47, 0.80)
GREEN = (0.89, 0.97, 0.91)
GREEN_S = (0.22, 0.60, 0.36)
AMBER = (1.00, 0.95, 0.85)
AMBER_S = (0.80, 0.55, 0.15)
RED = (1.00, 0.91, 0.91)
RED_S = (0.78, 0.25, 0.25)
GREY = (0.95, 0.96, 0.97)
GREY_S = (0.60, 0.63, 0.68)
PURPLE = (0.94, 0.91, 1.00)
PURPLE_S = (0.50, 0.38, 0.78)


def esc(t: str) -> str:
    return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text_w(s: str, size: float, bold: bool = False) -> float:
    return len(s) * size * (0.56 if bold else 0.52)


class Page:
    def __init__(self):
        self.ops: list[str] = []

    def _y(self, y):
        return H - y

    def color(self, rgb, stroke=False):
        self.ops.append(f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} {'RG' if stroke else 'rg'}")

    def rect(self, x, y, w, h, fill=None, stroke=None, lw=1.0, dash=False, r=6):
        self.ops.append(f"{lw} w")
        self.ops.append("[4 3] 0 d" if dash else "[] 0 d")
        if fill:
            self.color(fill)
        if stroke:
            self.color(stroke, True)
        # rounded rectangle via bezier corners
        k = 0.5523 * r
        x0, y0, x1, y1 = x, self._y(y + h), x + w, self._y(y)
        p = [f"{x0 + r} {y0} m", f"{x1 - r} {y0} l", f"{x1 - r + k} {y0} {x1} {y0 + r - k} {x1} {y0 + r} c",
             f"{x1} {y1 - r} l", f"{x1} {y1 - r + k} {x1 - r + k} {y1} {x1 - r} {y1} c", f"{x0 + r} {y1} l",
             f"{x0 + r - k} {y1} {x0} {y1 - r + k} {x0} {y1 - r} c", f"{x0} {y0 + r} l",
             f"{x0} {y0 + r - k} {x0 + r - k} {y0} {x0 + r} {y0} c"]
        self.ops += p
        self.ops.append("B" if fill and stroke else "f" if fill else "S")

    def text(self, x, y, s, size=9, bold=False, rgb=INK, align="left"):
        if align == "center":
            x -= text_w(s, size, bold) / 2
        elif align == "right":
            x -= text_w(s, size, bold)
        self.color(rgb)
        self.ops.append(f"BT /{'F2' if bold else 'F1'} {size} Tf {x:.1f} {self._y(y):.1f} Td ({esc(s)}) Tj ET")

    def wrap(self, s, width, size):
        words, lines, cur = s.split(), [], ""
        for w_ in words:
            cand = (cur + " " + w_).strip()
            if text_w(cand, size) > width and cur:
                lines.append(cur); cur = w_
            else:
                cur = cand
        if cur:
            lines.append(cur)
        return lines

    def box(self, x, y, w, h, title, body="", fill=BLUE, stroke=BLUE_S, size=9, dash=False, tech=""):
        """tech: the framework / library the block uses, drawn as a final line in the block's colour."""
        self.rect(x, y, w, h, fill, stroke, 1.1, dash)
        lines = self.wrap(body, w - 12, size - 1.5) if body else []
        tlines = self.wrap(tech, w - 12, 6.6) if tech else []
        total = (size + 3) + len(lines) * (size + 0.5) + len(tlines) * 8.2 + (2 if tlines else 0)
        ty = y + (h - total) / 2 + size
        self.text(x + w / 2, ty, title, size, True, INK, "center")
        for i, ln in enumerate(lines):
            self.text(x + w / 2, ty + (size + 3) + i * (size + 0.5), ln, size - 1.5, False, MUTED, "center")
        t0 = ty + (size + 3) + len(lines) * (size + 0.5) + 2
        for i, ln in enumerate(tlines):
            self.text(x + w / 2, t0 + i * 8.2, ln, 6.6, True, stroke, "center")

    def panel(self, x, y, w, h, title, lines, fill=BLUE, stroke=BLUE_S, size=7.2, mono_first=False):
        self.rect(x, y, w, h, fill, stroke, 1.0)
        self.text(x + 7, y + 13, title, 8.6, True, INK)
        yy = y + 25
        for ln in lines:
            for i, part in enumerate(self.wrap(ln, w - 18, size)):
                self.text(x + (9 if i == 0 else 15), yy, ("- " if i == 0 else "") + part, size, False, MUTED if i else INK)
                yy += size + 2.2

    def zone(self, x, y, w, h, label, fill=GREY, stroke=GREY_S, dash=True):
        self.rect(x, y, w, h, fill, stroke, 0.9, dash, r=10)
        self.text(x + 10, y + 15, label, 9.5, True, stroke)

    def arrow(self, x1, y1, x2, y2, label="", dash=False, rgb=LINE, lw=1.1, label_dx=0, label_dy=-4):
        self.ops.append(f"{lw} w")
        self.ops.append("[4 3] 0 d" if dash else "[] 0 d")
        self.color(rgb, True)
        self.ops.append(f"{x1:.1f} {self._y(y1):.1f} m {x2:.1f} {self._y(y2):.1f} l S")
        ang = math.atan2(self._y(y2) - self._y(y1), x2 - x1)
        a, L = 0.42, 7
        p1 = (x2 - L * math.cos(ang - a), self._y(y2) - L * math.sin(ang - a))
        p2 = (x2 - L * math.cos(ang + a), self._y(y2) - L * math.sin(ang + a))
        self.ops.append("[] 0 d")
        self.color(rgb)
        self.ops.append(f"{x2:.1f} {self._y(y2):.1f} m {p1[0]:.1f} {p1[1]:.1f} l {p2[0]:.1f} {p2[1]:.1f} l f")
        if label:
            mx, my = (x1 + x2) / 2 + label_dx, (y1 + y2) / 2 + label_dy
            self.text(mx, my, label, 7, False, MUTED, "center")

    def poly_arrow(self, pts, label="", dash=False, rgb=LINE, label_at=0):
        for (a, b), (c, d) in zip(pts[:-2], pts[1:-1]):
            self.ops.append(f"1.1 w {'[4 3] 0 d' if dash else '[] 0 d'}")
            self.color(rgb, True)
            self.ops.append(f"{a:.1f} {self._y(b):.1f} m {c:.1f} {self._y(d):.1f} l S")
        self.arrow(*pts[-2], *pts[-1], dash=dash, rgb=rgb)
        if label:
            (a, b), (c, d) = pts[label_at], pts[label_at + 1]
            self.text((a + c) / 2, (b + d) / 2 - 4, label, 7, False, MUTED, "center")

    def header(self, title, subtitle, n, total):
        self.rect(0, 0, W, 46, (0.11, 0.17, 0.30), None, r=0)
        self.text(28, 29, title, 16, True, (1, 1, 1))
        self.text(W - 28, 29, "finagent" + (f"  |  {n}/{total}" if total and total > 1 else ""), 9, False, (0.80, 0.85, 0.95), "right")
        self.text(28, 64, subtitle, 9, False, MUTED)

    def legend(self, items, x=28, y=H - 22):
        for label, f, s in items:
            self.rect(x, y - 9, 12, 10, f, s, 0.8, r=2)
            self.text(x + 17, y - 1, label, 7.5, False, MUTED)
            x += 24 + text_w(label, 7.5)


# ============================================================================ pages
def page_context(p: Page):
    # ---------------- INPUTS (left): who sends work in, and the read-only evidence finagent looks up
    p.zone(14, 80, 172, 432, "INPUTS", (0.97, 0.97, 0.98), GREY_S)
    p.box(24, 104, 152, 62, "AP processor / client", "POST /runs or CLI: case fields + documents (PDF, CSV, e-mail, JSON)",
          GREY, GREY_S, size=8, tech="REST (JSON) or finagent CLI")
    p.box(24, 176, 152, 60, "Approvers", "decisions via API, CLI or signed approval-system callbacks", GREY, GREY_S, size=8, tech="REST, CLI, HMAC-signed webhook")
    p.rect(20, 248, 160, 258, (1.0, 0.98, 0.95), AMBER_S, 1.0, True, r=8)
    p.text(100, 262, "Reference systems", 8.6, True, AMBER_S, "center")
    p.text(100, 272, "read-only via the tool runtime; mock JSON", 6.6, False, AMBER_S, "center")
    for i, (t, b) in enumerate([("Vendor master", "status, masked bank, risk"), ("ERP purchasing", "PO lines + goods receipts"),
                                ("AP history", "paid / posted / held / rejected"), ("Treasury FX", "corporate daily rates"),
                                ("Authority register", "limits, delegations v4.x")]):
        p.box(28, 280 + i * 45, 144, 39, t, b, AMBER, AMBER_S, size=7.6)

    # ---------------- SERVICE (centre)
    p.zone(200, 80, 430, 432, "finagent service  (FastAPI + CLI, one container)", (0.97, 0.98, 1.0), BLUE_S)
    # layer 1: entry
    p.box(212, 102, 406, 38, "HTTP API / CLI", "validation, X-API-Key, Swagger /docs", BLUE, BLUE_S,
          tech="FastAPI + Uvicorn, Pydantic v2; CLI: argparse")
    # layer 2: the engine runs the steps and calls everything below it
    p.box(212, 152, 406, 46, "Agent engine", "explicit state graph: checkpoint after every step, budgets; decides the outcome",
          PURPLE, PURPLE_S, tech="plain Python, no agent framework")
    # layer 3: what the engine calls (left to right)
    p.box(212, 226, 96, 76, "Skills", "11 control scripts; 4 also ask fixed questions; 5 parsers", PURPLE, PURPLE_S, size=8,
          tech="Python scripts; PyYAML; pypdf")
    p.box(315, 226, 96, 76, "Model adapter", "mock | Ollama | Claude | Bedrock", AMBER, AMBER_S, size=8,
          tech="httpx (Ollama); anthropic SDK")
    p.box(418, 226, 96, 76, "RAG retriever", "BM25 + vectors, built at start-up", GREEN, GREEN_S, size=8,
          tech="own BM25; sentence-transformers; sqlite3")
    p.box(522, 226, 96, 76, "Tool runtime", "schemas, timeouts, retries, deny-by-default", BLUE, BLUE_S, size=8,
          tech="Pydantic; ThreadPoolExecutor")
    # layer 4: data the layer above uses
    p.box(315, 340, 96, 86, "Policy baseline", "policy text vs reviewed baseline; differs -> every case held",
          GREEN, GREEN_S, size=7.6, tech="PyYAML; SHA-256")
    p.box(418, 340, 96, 86, "Finance policy corpus", "*.md; FIN-POL current, 003-OLD superseded, ADV untrusted",
          GREEN, GREEN_S, size=7.6, tech="Markdown + YAML")
    p.box(522, 340, 96, 86, "Webhook outbox", "status events, sent after the checkpoint; retried", BLUE, BLUE_S, size=7.6,
          tech="httpx; hmac")
    # layer 5: storage
    p.box(212, 436, 406, 44, "SQLite store", "runs, checkpoints, approvals, outcomes, outbox, audit (hash chain), ERP sandbox",
          GREY, GREY_S, size=8, tech="sqlite3 (std lib), WAL, no ORM; JSON stored as TEXT")

    # ---------------- OUTPUTS (right)
    p.zone(646, 80, 182, 432, "OUTPUTS", (0.97, 0.99, 0.97), GREEN_S)
    # each output sits level with the block that produces it, so every arrow is short and horizontal
    p.box(656, 150, 162, 50, "Approval requests", "with explanation, to approvers / approval systems", RED, RED_S, size=8,
          tech="REST + webhooks")
    p.box(656, 239, 162, 50, "ERP posting sandbox", "approved decisions only; idempotent; SIMULATED", RED, RED_S, size=8,
          tech="SQLite table erp_decisions")
    p.box(656, 358, 162, 50, "Status webhooks", "run.completed, needs_information, approval.*", BLUE, BLUE_S, size=8,
          tech="HTTP POST (httpx), HMAC-SHA256")
    p.box(656, 414, 162, 44, "Outcome pool + results", "outcome, reasoning, diagrams (API / CLI)", GREEN, GREEN_S, size=8,
          tech="REST / CLI; Pillow (PNG)")
    p.box(656, 464, 162, 44, "Audit trail", "hash-chained, redacted; JSONL mirror", GREY, GREY_S, size=8,
          tech="SQLite + JSONL; SHA-256 chain")

    # ---------------- arrows
    p.arrow(176, 130, 212, 118, "cases")
    p.arrow(176, 206, 212, 136, "")
    p.arrow(415, 140, 415, 152)
    # engine -> what it calls (labelled)
    p.arrow(260, 198, 260, 226, "run scripts", label_dx=26, label_dy=4)
    p.arrow(363, 198, 363, 226, "DRAFT", label_dx=16, label_dy=4)
    p.arrow(570, 198, 570, 226, "tools", label_dx=14, label_dy=4)
    # the two indirect links, drawn under layer 3
    p.poly_arrow([(260, 302), (260, 320), (363, 320), (363, 302)], "skill questions", label_at=1)
    p.poly_arrow([(570, 302), (570, 320), (494, 320), (494, 302)], "retrieve tool", label_at=1)
    p.arrow(440, 340, 440, 302)                                   # corpus -> retriever (indexed at start-up)
    p.arrow(411, 383, 418, 383)                                   # baseline compares the corpus
    p.poly_arrow([(212, 175), (205, 175), (205, 458), (212, 458)])  # engine -> store: checkpoints, results
    # outputs: one short horizontal arrow from each producer
    p.arrow(618, 175, 656, 175, "")                                # engine -> approval requests
    p.arrow(618, 264, 656, 264, "gated", rgb=RED_S, label_dy=-5)   # tool runtime -> ERP sandbox (approval required)
    p.arrow(618, 383, 656, 383, "")                                # webhook outbox -> status webhooks
    p.arrow(618, 446, 656, 436, "")                                # store -> outcome pool
    p.arrow(618, 470, 656, 486, "")                                # store -> audit trail
    # reference systems are read only through the tool runtime
    p.arrow(180, 300, 200, 300, "")
    p.legend([("trusted code", PURPLE, PURPLE_S), ("RAG / policy", GREEN, GREEN_S), ("model / reference data", AMBER, AMBER_S),
              ("consequential (simulated)", RED, RED_S), ("platform", BLUE, BLUE_S)])


def page_graph(p: Page):
    row1 = [("RECEIVE", "validate request"), ("PARSE_INPUTS", "parser registry -> finagent.document/1"),
            ("RETRIEVE_POLICY", "workflow plan -> RAG queries"), ("GATHER_EVIDENCE", "vendor, PO*, history, FX*, authority"),
            ("RECONCILE", "12 scripts; 4 ask questions, code decides; caution only")]
    x0, y1, bw, bh, gap = 28, 100, 140, 66, 22
    for i, (t, b) in enumerate(row1):
        p.box(x0 + i * (bw + gap), y1, bw, bh, t, b, PURPLE, PURPLE_S, 8.5)
        if i:
            p.arrow(x0 + i * (bw + gap) - gap, y1 + bh / 2, x0 + i * (bw + gap), y1 + bh / 2)
    # DRAFT / DECIDE
    p.box(650, 210, 150, 66, "DRAFT", "model narrative; schema + citation validation; retry/repair", AMBER, AMBER_S, 8.5)
    p.poly_arrow([(x0 + 4 * (bw + gap) + bw / 2, y1 + bh), (x0 + 4 * (bw + gap) + bw / 2, 190), (725, 190), (725, 210)])
    p.box(650, 320, 150, 66, "DECIDE", "most conservative vote; grounding gate; model may only escalate", PURPLE, PURPLE_S, 8.5)
    p.arrow(725, 276, 725, 320)
    p.text(642, 292, "<= 1 extra retrieval round", 7, False, MUTED, "right")
    # branches
    p.box(420, 320, 170, 66, "APPROVAL_GATE", "APPROVE_FOR_POSTING / REJECT_*: create approval with explanation", RED, RED_S, 8.5)
    p.box(420, 440, 170, 66, "HOLD_FINALIZE", "HOLD / ESCALATE: route to owner; resumable with new evidence", AMBER, AMBER_S, 8.5)
    p.arrow(650, 353, 590, 353, "consequential")
    p.poly_arrow([(700, 386), (700, 473), (590, 473)], "protective", label_at=1)
    p.box(200, 320, 160, 66, "SUBMIT_DECISION", "submit_finance_decision: approved args only, idempotency key", RED, RED_S, 8.5)
    p.arrow(420, 345, 360, 345, "all slots approved")
    p.box(28, 320, 130, 66, "FINALIZE", "COMPLETED; outcome pool SUBMITTED", GREEN, GREEN_S, 8.5)
    p.arrow(200, 353, 158, 353)
    p.box(200, 425, 160, 56, "FINALIZE_REJECTED", "COMPLETED; nothing submitted", GREY, GREY_S, 8.5)
    p.poly_arrow([(470, 386), (470, 405), (280, 405), (280, 425)], "approver rejects", label_at=1)
    # stop markers
    p.rect(420, 395, 170, 20, (1, 1, 1), RED_S, 0.8, True, r=4)
    p.text(505, 409, "STOP: AWAITING_APPROVAL", 7.5, True, RED_S, "center")
    p.rect(420, 515, 170, 20, (1, 1, 1), AMBER_S, 0.8, True, r=4)
    p.text(505, 529, "STOP: NEEDS_INFORMATION", 7.5, True, AMBER_S, "center")
    notes = ["Checkpoint (full state) after every node -> crash resumes at next node (finagent resume / scheduled resume --all).",
             "Budgets per run: 40 steps, 50 tool calls, 6 model calls - breach = explicit FAILED, never a partial decision.",
             "claim_run moves AWAITING_APPROVAL -> RUNNING atomically: duplicate callbacks cannot resume twice.",
             "* PO fetched only for PO-backed invoices; FX only for foreign currency (tools called only when needed)."]
    for i, n in enumerate(notes):
        p.text(28, 210 + i * 14, "- " + n, 8, False, INK)
    p.legend([("deterministic node", PURPLE, PURPLE_S), ("model step", AMBER, AMBER_S), ("approval / consequential", RED, RED_S),
              ("terminal", GREEN, GREEN_S)])


def page_trust(p: Page):
    p.zone(28, 85, 786, 70, "TRUSTED: code + configuration (reviewed, versioned)", PURPLE, PURPLE_S, False)
    p.text(40, 125, "Control skills and thresholds, config/workflow.yaml (mandatory controls protected), system prompt, tool guards,", 8.5)
    p.text(40, 139, "authority / segregation-of-duties checks, redaction. Instructions come ONLY from here.", 8.5)
    p.zone(28, 170, 786, 70, "AUTHORITATIVE DATA: trusted for facts, never for instructions", GREEN, GREEN_S, False)
    p.text(40, 210, "Current internal policy chunks (status=current) - systems of record via tools: vendor master, PO + receipts,", 8.5)
    p.text(40, 224, "invoice history, FX rates, authority register (all schema-validated, timestamps recorded).", 8.5)
    p.zone(28, 255, 786, 70, "UNTRUSTED DATA", RED, RED_S, False)
    p.text(40, 295, "Request notes, attachments, e-mails, PDFs - supplier / external documents (ADV-001, ADV-002) -", 8.5)
    p.text(40, 309, "superseded policy (FIN-POL-003-OLD) - model output.", 8.5)
    pts = [("1 Ingestion", "trust from metadata only; feeds may lower, never raise (trust_ceiling); classification access filter"),
           ("2 Prompt", "untrusted text fenced + trust-labelled; bank / contact data redacted before any model"),
           ("3 instruction_screen", "embedded instructions become risk indicators (FIN-POL-005 s4), never actions"),
           ("4 Output validator", "JSON schema, citations must be supplied ids, no superseded/untrusted citations, retry/repair"),
           ("5 Policy guard", "model may only make the outcome MORE conservative (model.override_blocked)"),
           ("6 Grounding gate", "findings must cite retrieved authoritative chunks, else approval -> hold"),
           ("7 Tool guard", "submit only with APPROVED approval; outcome/amount/currency/verb/idempotency key must match"),
           ("8 Approver checks", "authority limits v4.x, delegation dates, SoD (creator, changer, requester, receipter >25k)"),
           ("9 Skill runner", "code collects excerpts as DATA; answers from a fixed list; quotes must exist; may only add caution")]
    p.text(28, 352, "Enforcement points", 11, True, INK)
    for i, (t, b) in enumerate(pts):
        col, row = i % 2, i // 2
        x, y = 28 + col * 398, 365 + row * 50
        p.box(x, y, 388, 42, t, b, (1, 1, 1), GREY_S, 8.5)


def page_aws(p: Page):
    p.box(28, 250, 110, 54, "Allowed CIDRs", "office / VPN only", GREY, GREY_S)
    p.zone(160, 85, 520, 420, "AWS account  -  VPC (2 public subnets, no NAT in POC)", (0.98, 0.99, 1.0), BLUE_S)
    p.box(180, 250, 110, 54, "ALB", "HTTP / HTTPS (ACM)", BLUE, BLUE_S)
    p.box(330, 230, 160, 94, "ECS Fargate task", "finagent:<immutable tag>, desired_count 1 (single SQLite writer), ECS Exec", PURPLE, PURPLE_S)
    p.box(330, 380, 160, 64, "EFS (encrypted)", "/data: SQLite, index, vectors, audit JSONL", GREY, GREY_S)
    p.box(530, 110, 130, 50, "Secrets Manager", "webhook HMAC, API key", GREEN, GREEN_S)
    p.box(530, 180, 130, 50, "Amazon Bedrock", "Claude (least-privilege IAM)", AMBER, AMBER_S)
    p.box(530, 250, 130, 50, "CloudWatch Logs", "30-day retention", GREY, GREY_S)
    p.box(530, 320, 130, 56, "ECR", "immutable tags, scan on push", BLUE, BLUE_S)
    p.box(530, 395, 130, 64, "S3 corpus bucket", "versioned, KMS, public access blocked", GREEN, GREEN_S)
    p.box(180, 110, 130, 64, "EventBridge Scheduler", "every 5 min: webhooks deliver + resume --all", BLUE, BLUE_S)
    p.arrow(138, 277, 180, 277)
    p.arrow(290, 277, 330, 277, "8080")
    p.arrow(410, 324, 410, 380, "NFS/TLS", label_dx=20)
    p.arrow(490, 245, 530, 140)
    p.arrow(490, 260, 530, 205)
    p.arrow(490, 277, 530, 275)
    p.arrow(530, 348, 490, 300, "pull")
    p.poly_arrow([(595, 395), (595, 385), (470, 385), (470, 324)], "", dash=True)
    p.text(500, 396, "corpus synced at build", 7, False, MUTED, "center")
    p.poly_arrow([(245, 174), (245, 200), (360, 200), (360, 230)], "RunTask", label_at=1)
    notes = ["IaC: infra/terraform (validate + fmt clean). Scripts: scripts/aws/{bootstrap,plan,deploy,rollback,smoke,scale,destroy}.sh",
             "Idle cost ~USD 45-55/month (Fargate + ALB). scale.sh 0 pauses compute; destroy.sh removes everything.",
             "Production: private subnets + VPC endpoints, WAF, RDS Postgres + workers, SQS outbox, SSO approvers, S3 Object Lock audit."]
    for i, n in enumerate(notes):
        p.text(28, 530 + i * 13, "- " + n, 8, False, INK)
    p.zone(695, 85, 127, 420, "Not in POC", GREY, GREY_S)
    for i, s in enumerate(["NAT / VPC endpoints", "WAF", "RDS Postgres", "SQS outbox", "Cognito SSO", "S3 Object Lock", "AWS Backup"]):
        p.text(707, 120 + i * 18, "- " + s, 8, False, MUTED)


def page_rag(p: Page):
    p.zone(20, 85, 150, 300, "rag.sources", GREY, GREY_S)
    for i, (t, b) in enumerate([("local_dir", "finance_rag_corpus/"), ("http_feed", "JSON CorpusDocument API"), ("plugins", "S3, SharePoint, ...")]):
        p.box(32, 112 + i * 88, 126, 68, t, b, GREY, GREY_S, 8.5)
    p.box(195, 180, 120, 70, "Loaders", ".md .json .txt .pdf + plugins", BLUE, BLUE_S, 8.5)
    p.box(335, 170, 130, 90, "CorpusDocument", "finagent.corpus/1 + provenance + trust_ceiling; trust from metadata", PURPLE, PURPLE_S, 8.5)
    p.box(485, 180, 120, 70, "Section chunker", "id = DOC@vVER#sN", BLUE, BLUE_S, 8.5)
    p.box(630, 110, 180, 60, "BM25 index", "data/index/bm25_index.json", GREEN, GREEN_S, 8.5)
    p.box(630, 270, 180, 60, "VectorStore", "SQLite (POC) | pgvector | OpenSearch | Qdrant", GREEN, GREEN_S, 8.5)
    p.box(630, 190, 180, 50, "Embedder", "hash (offline) | ollama | plugin", AMBER, AMBER_S, 8.5)
    for i in range(3):
        p.arrow(158, 146 + i * 88, 195, 215)
    p.arrow(315, 215, 335, 215)
    p.arrow(465, 215, 485, 215)
    p.arrow(605, 205, 630, 140)
    p.arrow(605, 215, 630, 215)
    p.arrow(720, 240, 720, 270)
    p.box(28, 420, 150, 60, "Queries", "per-skill curated queries + case notes", PURPLE, PURPLE_S, 8.5)
    steps = [("Hybrid fusion", "weighted RRF (lexical 1.0, vector 0.5)"), ("Access filter", "classification x caller role"),
             ("Trust re-rank", "superseded x0.35, untrusted x0.6, labelled; surfaced as risk evidence"),
             ("Grounding gate", "findings cite retrieved authoritative chunks")]
    for i, (t, b) in enumerate(steps):
        x = 205 + i * 152
        p.box(x, 420, 138, 60, t, b, BLUE if i < 3 else RED, BLUE_S if i < 3 else RED_S, 8.5)
        p.arrow(x - 14 if i else 178, 450, x, 450)
    p.arrow(720, 330, 340, 420, "vector hits", dash=True)
    p.arrow(720, 170, 274, 420, "BM25 hits", dash=True)
    p.text(28, 515, "Measured (finagent eval): policy recall@5 = 1.0 over 43 skill compliance refs; current > superseded; adversarial "
                     "docs always labelled; 0 untrusted/superseded citations in 42 scenarios.", 8, False, INK)


def page_service_detail(p: Page):
    p.zone(14, 74, 814, 506, "finagent service - module design (src/finagent/)", (0.985, 0.99, 1.0), BLUE_S)
    # row A: interfaces
    p.panel(26, 96, 250, 74, "api/app.py  (FastAPI, Swagger /docs)", [
        "/runs  /runs/{id}  /resume  /evidence", "/approvals/{id}/decision   /webhooks/inbound/approval (HMAC)",
        "/outcomes  /webhooks/*  /policy/*  /audit/verify  /evaluations  /catalog/*", "X-API-Key dependency; KeyError->404, ApprovalError->4xx"], BLUE, BLUE_S)
    p.panel(286, 96, 180, 74, "cli.py  (finagent ...)", [
        "start get runs approve reject resume evidence", "outcomes audit audit-verify eval skills workflow",
        "webhooks (subscribe/deliver/listen) policy serve demo"], BLUE, BLUE_S)
    p.panel(476, 96, 160, 74, "governance/", ["params.py: policy numbers catalog",
        "baseline.py: PolicyBaseline check", "/policy/* + finagent policy"], GREEN, GREEN_S)
    p.panel(646, 96, 170, 74, "config.py + config/*.yaml", ["settings.yaml: llm, rag, agent budgets, tools, webhooks",
        "FINAGENT__SECTION__KEY env overrides", "workflow.yaml: skill pipeline (+ plugins)"], GREY, GREY_S)
    # row B: composition root
    p.rect(26, 180, 790, 26, GREEN, GREEN_S, 1.0)
    p.text(421, 197, "services.py  build_app(Settings) -> Store, AuditLog, Retriever, MockBackend, ToolRunner, LLMProvider, Webhooks, Pipeline, PolicyBaseline, Engine",
           8, True, INK, "center")
    # row C: engine + workflow
    p.rect(26, 216, 560, 150, PURPLE, PURPLE_S, 1.1)
    p.text(36, 231, "agent/graph.py  Engine  (bounded state graph)", 9, True, INK)
    nodes = ["RECEIVE", "PARSE_INPUTS", "RETRIEVE_POLICY", "GATHER_EVIDENCE", "RECONCILE", "DRAFT", "DECIDE",
             "APPROVAL_GATE", "HOLD_FINALIZE", "SUBMIT_DECISION", "FINALIZE", "FINALIZE_REJECTED"]
    for i, n in enumerate(nodes):
        cx, cy = 36 + (i % 6) * 91, 240 + (i // 6) * 24
        p.rect(cx, cy, 86, 18, (1, 1, 1), PURPLE_S, 0.7, r=4)
        p.text(cx + 43, cy + 12, n, 6.6, True, RED_S if n in ("APPROVAL_GATE", "SUBMIT_DECISION") else PURPLE_S, "center")
    p.panel(36, 292, 268, 68, "run lifecycle", ["start_run (idempotency key) / resume / resume_all / add_evidence",
        "_execute: budget -> node -> audit -> checkpoint -> (sim. crash)", "_fail -> FAILED + reason; evidence_bundle(state)"], (1, 1, 1), PURPLE_S)
    p.panel(312, 292, 268, 68, "decision + approvals", ["n_decide: max vote, grounding + policy gates, model may only escalate",
        "decide_approval: callback replay -> _apply_decision (BEGIN IMMEDIATE)", "authority/SoD/delegation -> slots -> claim_run -> resume"], (1, 1, 1), PURPLE_S)
    p.panel(596, 216, 220, 150, "workflow.py  Pipeline", ["load_pipeline(): YAML + plugins",
        "validate(): unknown skill/condition, dependency order, mandatory controls present & enabled",
        "plan(state) / retrieval_plan(state) via CONDITIONS (has_po, no_po, is_credit_note, foreign_currency ...)",
        "digest recorded on every run", "agent/calc.py: Decimal ROUND_HALF_UP, calc records, business days"], PURPLE, PURPLE_S)
    # row D: capability modules
    p.panel(26, 378, 196, 132, "skills/", ["base.py: @skill, SkillSpec (version, mandatory, compliance_refs, queries), SkillOutput",
        "parsers/registry.py: route (sniff>ctype>ext) + NormalizedDocument validation",
        "parsers/documents.py json/text/csv/email; pdf.py", "processors/controls.py: 11 controls -> findings, calcs, exceptions, votes",
        "testing.py: fixture harness + coverage"], PURPLE, PURPLE_S)
    p.panel(230, 378, 196, 132, "rag/", ["ingest.py: loaders (.md .json .txt .pdf), connectors (local_dir, http_feed), trust, section chunker",
        "index.py: BM25 (JSON)", "vectorstore.py: embedders (hash, ollama, sentence_transformers) + stores (sqlite | adapters)",
        "retriever.py: lexical/hybrid RRF -> access filter -> trust re-rank -> Chunk citations"], GREEN, GREEN_S)
    p.panel(434, 378, 186, 132, "llm/", ["providers.py: LLMProvider.complete(); mock (fault injection), ollama, anthropic, bedrock",
        "drafting.py: SYSTEM_PROMPT, fenced untrusted text, redaction", "extract_json + validate_draft: schema, allowed ids, no non-authoritative citations",
        "retry with validator errors -> fallback | fail"], AMBER, AMBER_S)
    p.panel(628, 378, 188, 132, "tools/", ["base.py ToolRunner.call: budget -> input schema -> deny-by-default gate -> timeout + retries -> output schema -> audit",
        "finance_tools.py: 7 tools (retrieve_finance_documents -> rag.Retriever); submit guard: approval APPROVED, args match, idempotency key",
        "mock_backend.py: JSON fixtures + faults.json (timeout/transient)"], BLUE, BLUE_S)
    # row E: cross-cutting
    p.panel(26, 520, 196, 52, "schemas.py + contracts.py", ["typed request/tool/result/model contracts", "finagent.document/1, finagent.corpus/1"], GREY, GREY_S)
    p.panel(230, 520, 196, 52, "store.py (SQLite WAL)", ["runs, checkpoints, approvals, callbacks,", "outcomes, outbox, audit, erp_decisions"], GREY, GREY_S)
    p.panel(434, 520, 186, 52, "audit.py + redaction.py", ["hash-chained events, JSONL mirror", "mask bank/e-mail/phone/secrets"], RED, RED_S)
    p.panel(628, 520, 188, 52, "webhooks.py", ["outbox + HMAC-SHA256 delivery/retry", "verify() for inbound callbacks"], BLUE, BLUE_S)
    # arrows
    for x in (150, 376, 556):
        p.arrow(x, 170, x, 180)
    p.arrow(300, 206, 300, 216)
    p.arrow(586, 290, 596, 290, "")
    p.arrow(124, 366, 124, 378, "")
    p.arrow(330, 366, 330, 378, "")
    p.arrow(526, 366, 526, 378, "")
    p.arrow(586, 350, 700, 378, "")


def page_sequence(p: Page):
    lanes = ["Caller /\nApprover", "API", "Engine", "Pipeline +\nSkills", "Tool\nruntime", "RAG", "Systems\n(simulated)", "Model", "Store +\nAudit", "Webhooks"]
    x0, step = 46, 83
    xs = [x0 + i * step for i in range(len(lanes))]
    for x, name in zip(xs, lanes):
        p.rect(x - 36, 76, 72, 26, BLUE if name.split()[0] not in ("Model",) else AMBER, BLUE_S, 0.9, r=5)
        parts = name.split("\n")
        for j, part in enumerate(parts):
            p.text(x, 87 + j * 9 - (4 if len(parts) == 1 else 0) + (4 if len(parts) == 1 else 0), part, 7.4, True, INK, "center")
        p.ops.append("0.6 w [2 3] 0 d")
        p.color(GREY_S, True)
        p.ops.append(f"{x:.1f} {H - 102:.1f} m {x:.1f} {H - 572:.1f} l S")
        p.ops.append("[] 0 d")
    L = {n.split()[0].split("\n")[0].replace("\n", ""): i for i, n in enumerate(lanes)}
    C, A, E, S, T, R, SYS, M, ST, WH = range(10)
    msgs = [
        (C, A, "1 POST /runs (ProcessingRequest)"), (A, E, "2 start_run: create run + idempotency"),
        (E, ST, "3 checkpoint RECEIVE, PARSE_INPUTS"), (E, S, "4 parse_attachment / plan(state)"),
        (E, T, "5 retrieve_finance_documents x N"), (T, R, "6 search: BM25/RRF, access, trust"),
        (E, T, "7 vendor, PO*, history, FX*, authority"), (T, SYS, "8 call (timeout, retry, schema)"),
        (E, S, "9 run pipeline -> findings, calcs, votes"), (E, M, "10 complete(system, fenced prompt)"),
        (M, E, "11 ModelDraft -> validate / retry"), (E, E, "12 DECIDE: grounding gate, escalate-only"),
        (E, ST, "13 approval + outcome AWAITING_APPROVAL"), (E, WH, "14 approval.requested (explanation)"),
        (A, C, "15 201 AWAITING_APPROVAL"), (C, A, "16 POST /approvals/{id}/decision"),
        (A, E, "17 decide_approval: callback replay?"), (E, T, "18 authority register; limits, SoD"),
        (E, ST, "19 record slot; claim_run (atomic)"), (E, T, "20 submit_finance_decision (guard)"),
        (T, SYS, "21 ERP sandbox: idempotency key"), (E, ST, "22 FINALIZE: COMPLETED, pool SUBMITTED"),
        (E, WH, "23 decision.submitted, run.completed"), (A, C, "24 200 {status, run, posting_ref}"),
    ]
    y = 118
    for a, b, label in msgs:
        if a == b:
            p.rect(xs[a] - 4, y - 6, 8, 12, PURPLE, PURPLE_S, 0.6, r=2)
            p.text(xs[a] + 8, y + 3, label, 7, False, INK)
        else:
            rgb = RED_S if label.startswith(("20", "21")) else (AMBER_S if label.startswith(("10", "11")) else LINE)
            p.arrow(xs[a], y, xs[b], y, rgb=rgb, lw=0.9)
            lx = (xs[a] + xs[b]) / 2
            p.text(lx, y - 3, label, 6.8, False, INK, "center")
        y += 18.5
        if label.startswith("15"):
            p.ops.append("0.6 w [6 3] 0 d"); p.color(RED_S, True)
            p.ops.append(f"20 {H - (y - 7):.1f} m 822 {H - (y - 7):.1f} l S"); p.ops.append("[] 0 d")
            p.text(822, y - 10, "run STOPPED at approval gate (state checkpointed)", 7, True, RED_S, "right")
            y += 6
    p.text(28, 582, "Every message above also writes a redacted, hash-chained audit event. A crash between any two steps resumes "
                    "from the last checkpoint; step 21 is idempotent, so a replay cannot post twice.", 7.5, False, MUTED)


def page_data_model(p: Page):
    T = [
        (24, 84, 250, 158, "runs", ["run_id PK", "case_id, status, current_node", "request_idempotency_key UNIQUE",
         "request_json, state_json, result_json", "config_manifest, index_version", "parent_run_id (evidence attempts)", "attempt, version, created_at, updated_at"], PURPLE, PURPLE_S),
        (24, 258, 250, 78, "checkpoints", ["(run_id FK, seq) PK", "node, next_node, state_json, created_at"], PURPLE, PURPLE_S),
        (296, 84, 250, 130, "approvals", ["approval_id PK, run_id FK UNIQUE", "status PENDING|APPROVED|REJECTED", "decision_type POST|REJECT, proposed_outcome",
         "amount, currency, amount_aud", "required_slots, decisions (approver, role, limit, register version, ts)", "explanation (shown to approver)"], RED, RED_S),
        (296, 230, 250, 66, "approval_callbacks", ["callback_id PK, approval_id FK", "request_fingerprint, response_json, http_status"], RED, RED_S),
        (296, 312, 250, 72, "erp_decisions  (SIMULATED ERP)", ["idempotency_key PK, posting_ref UNIQUE", "run_id, case_id, approval_id, decision", "payload, recorded_at"], RED, RED_S),
        (568, 84, 250, 92, "outcomes  (outcome pool)", ["run_id PK/FK, case_id", "pool_status AWAITING_APPROVAL|SUBMITTED|HELD|", "ESCALATED|REJECTED_BY_APPROVER|FAILED", "outcome, primary_exception, summary, payload"], GREEN, GREEN_S),
        (568, 192, 250, 110, "audit_events", ["seq PK, event_id UNIQUE, run_id, correlation_id", "ts, event_type, node, tool, outcome", "duration_ms, attempt, actor, details (redacted)", "prev_hash, hash  (sha256 chain)"], AMBER, AMBER_S),
        (568, 318, 120, 66, "webhook_subscriptions", ["subscription_id PK", "url, events, active"], BLUE, BLUE_S),
        (698, 318, 120, 66, "webhook_outbox", ["delivery_id PK, sub FK", "event, payload, status, attempts"], BLUE, BLUE_S),
        (24, 352, 250, 32, "eval_results", ["eval_id PK, summary, results"], GREY, GREY_S),
    ]
    for x, y, w, h, title, cols, f, s_ in T:
        p.panel(x, y, w, h, title, cols, f, s_)
    p.arrow(149, 242, 149, 258, "1:N")
    p.arrow(274, 140, 296, 140, "1:1")
    p.arrow(421, 214, 421, 230, "1:N")
    p.arrow(421, 296, 421, 312, "1:1 per approval")
    p.arrow(546, 120, 568, 120, "")
    p.arrow(274, 222, 568, 222, "", dash=True)
    p.text(480, 219, "run_id (every event)", 6.8, False, MUTED, "center")
    p.arrow(698, 351, 688, 351, "")
    p.text(28, 412, "Files next to the database (data/)", 10, True, INK)
    p.panel(24, 420, 250, 70, "index/bm25_index.json", ["chunks: chunk_id DOC@vVER#sN, document_id, version, section,", "status, classification, trust, text, index_text, provenance hash"], GREEN, GREEN_S)
    p.panel(296, 420, 250, 70, "index/vectors.db  (SQLite VectorStore)", ["vectors: chunk_id PK, document_id, classification, trust,", "index_version, embedder, dim, vector BLOB float32"], GREEN, GREEN_S)
    p.panel(568, 420, 250, 70, "audit.jsonl", ["append-only mirror of audit_events", "(ship to SIEM / CloudWatch / S3 Object Lock)"], AMBER, AMBER_S)
    p.text(28, 515, "Transactions: approvals use BEGIN IMMEDIATE (serialised callbacks); claim_run atomically moves AWAITING_APPROVAL -> RUNNING; "
                    "checkpoint() writes checkpoints + runs in one transaction.", 7.6, False, INK)
    p.text(28, 529, "Production mapping: runs/approvals/outcomes -> Postgres with row leases; outbox -> SQS/EventBridge; audit -> append-only table + "
                    "S3 Object Lock; vectors -> pgvector/OpenSearch via the VectorStore adapter.", 7.6, False, INK)


# ============================================================================ infrastructure
def _badge(p: Page, x, y, n):
    p.rect(x - 9, y - 9, 18, 18, NAVY_S, None, r=9)
    p.text(x, y + 3.2, str(n), 7.5, True, (1, 1, 1), "center")


NAVY_S = (0.11, 0.17, 0.30)


def page_infra(p: Page):
    # external
    p.zone(14, 76, 150, 494, "Outside", GREY, GREY_S)
    ext = [("AP team / CLI", "browser, Swagger, terminal", 2), ("Webhook subscribers", "status events, approval requests", 14),
           ("Approval systems", "ServiceNow, Slack, e-mail", 14), ("Model providers", "Claude API / Ollama", 12),
           ("Systems of record", "vendor, ERP, history (mock)", 15)]
    for i, (t, b, n) in enumerate(ext):
        y = 100 + i * 92
        p.box(24, y, 130, 74, t, b, (1, 1, 1), GREY_S, 8.5)
        _badge(p, 150, y + 4, n)
    # local
    p.zone(178, 76, 250, 494, "Local / laptop  (Docker Compose)", (0.97, 0.98, 1.0), BLUE_S)
    loc = [("finagent container", "FastAPI + engine + skills + RAG + policy check", (1, 3, 4, 29)), ("Ollama container (optional)", "local LLM + embeddings", (12, 11)),
           ("Training container", "embeddings fine-tune (CPU)", (13,)), ("./data volume", "SQLite store, audit, index, vectors", (6, 7, 9, 10)),
           ("Policy corpus", "finance_rag_corpus/*.md", (8,))]
    for i, (t, b, ns) in enumerate(loc):
        y = 100 + i * 92
        p.box(190, y, 226, 74, t, b, BLUE if i < 3 else GREEN, BLUE_S if i < 3 else GREEN_S, 8.5)
        for j, n in enumerate(ns):
            _badge(p, 404 - j * 20, y + 4, n)
    _badge(p, 416, 84, 16)
    # AWS
    p.zone(442, 76, 386, 494, "AWS  (Terraform)", (1.0, 0.985, 0.96), AMBER_S)
    _badge(p, 816, 84, 17)
    p.rect(452, 100, 366, 300, (1, 1, 1), AMBER_S, 0.8, True, r=8)
    p.text(460, 113, "VPC", 8, True, AMBER_S)
    _badge(p, 806, 108, 18)
    aws = [(462, 122, 110, 50, "ALB", "HTTPS, allow-list", 19, BLUE, BLUE_S),
           (590, 122, 130, 62, "ECS Fargate task", "finagent image, 1 task", 20, PURPLE, PURPLE_S),
           (590, 206, 130, 50, "EFS (encrypted)", "/data", 21, GREY, GREY_S),
           (462, 206, 110, 50, "Scheduler", "every 5 min", 28, BLUE, BLUE_S),
           (462, 290, 110, 50, "IAM roles", "least privilege", 25, GREEN, GREEN_S),
           (590, 290, 130, 50, "Secrets Manager", "HMAC, API key", 24, GREEN, GREEN_S),
           (736, 122, 74, 62, "Bedrock", "Claude", 26, AMBER, AMBER_S),
           (736, 206, 74, 50, "CloudWatch", "logs", 27, GREY, GREY_S),
           (736, 290, 74, 50, "ECR", "images", 22, BLUE, BLUE_S),
           (462, 420, 160, 54, "S3 corpus bucket", "versioned, KMS", 23, GREEN, GREEN_S)]
    for x, y, w, h, t, b, n, f, st in aws:
        p.box(x, y, w, h, t, b, f, st, 8.2)
        _badge(p, x + w - 6, y + 4, n)
    p.arrow(572, 147, 590, 147)
    p.arrow(655, 184, 655, 206)
    p.arrow(572, 231, 590, 180, "RunTask")
    p.arrow(720, 150, 736, 150)
    p.arrow(720, 165, 736, 225)
    p.arrow(736, 312, 720, 175)
    p.arrow(542, 420, 610, 256, "sync at build", dash=True)
    p.text(456, 500, "Numbers refer to the component catalogue on the next pages: what it does, why it was chosen,", 8, False, INK)
    p.text(456, 512, "and which alternative to switch to when circumstances change.", 8, False, INK)
    p.text(456, 534, "Production hardening: private subnets + VPC endpoints, WAF, RDS Postgres, SQS outbox, approver SSO.", 7.6, False, MUTED)
    # connectors from outside
    p.arrow(154, 137, 190, 137)
    p.arrow(154, 229, 190, 150)
    p.arrow(154, 321, 190, 160)
    p.arrow(154, 413, 190, 210, "", dash=True)
    p.arrow(416, 137, 462, 147, "deploy same image", dash=True)


def _table(p: Page, rows, y0, widths, size=7.7):
    heads = ["#", "Component", "What it does", "Why we chose it", "Alternatives -> when to switch"]
    xs = [28]
    for w_ in widths[:-1]:
        xs.append(xs[-1] + w_)
    p.rect(24, y0, 794, 16, (0.11, 0.17, 0.30), None, r=2)
    for x, h in zip(xs, heads):
        p.text(x + 2, y0 + 11.5, h, 7.6, True, (1, 1, 1))
    y = y0 + 20
    for k, (no, layer, comp, what, why, alt) in enumerate(rows):
        cells = [str(no), f"{comp} [{layer}]", what, why, alt]
        wrapped = [p.wrap(c, w_ - 6, size) for c, w_ in zip(cells, widths)]
        h = max(len(w) for w in wrapped) * (size + 1.6) + 5
        if k % 2:
            p.rect(24, y - 2, 794, h, (0.97, 0.975, 0.985), None, r=0)
        for x, lines, bold in zip(xs, wrapped, [True, True, False, False, False]):
            for j, ln in enumerate(lines):
                p.text(x + 2, y + 7 + j * (size + 1.6), ln, size, bold, INK)
        y += h
    return y


def page_infra_table(rows):
    def fn(p: Page):
        _table(p, rows, 76, [22, 150, 190, 200, 232])
    return fn


def page_infra_scenarios(p: Page):
    from infra_catalog import SCENARIOS
    p.rect(24, 76, 794, 16, (0.11, 0.17, 0.30), None, r=2)
    p.text(30, 87.5, "If this changes ...", 8, True, (1, 1, 1))
    p.text(300, 87.5, "... change these components (numbers = catalogue)", 8, True, (1, 1, 1))
    y = 98
    for k, (when, what) in enumerate(SCENARIOS):
        lines = p.wrap(what, 510, 8.4)
        h = len(lines) * 11 + 12
        if k % 2:
            p.rect(24, y - 2, 794, h, (0.97, 0.975, 0.985), None, r=0)
        p.text(30, y + 9, when, 8.6, True, INK)
        for j, ln in enumerate(lines):
            p.text(300, y + 9 + j * 11, ln, 8.4, False, INK)
        y += h
    p.text(28, y + 20, "What stays the same in every case: skills, data contracts, the approval gate, the audit trail, and the scenario tests "
                       "- so any swap is verified by the same 80 scenarios.", 8.5, True, PURPLE_S)


# ============================================================================ workflow flowcharts
def diamond(p: Page, cx, cy, w, h, text, fill=AMBER, stroke=AMBER_S, size=7.2):
    p.ops.append("1 w [] 0 d")
    p.color(fill); p.color(stroke, True)
    pts = [(cx, cy - h / 2), (cx + w / 2, cy), (cx, cy + h / 2), (cx - w / 2, cy)]
    p.ops.append(" ".join(f"{x:.1f} {p._y(y):.1f} {'m' if i == 0 else 'l'}" for i, (x, y) in enumerate(pts)) + " h B")
    lines = p.wrap(text, w * 0.62, size)
    for i, ln in enumerate(lines):
        p.text(cx, cy - (len(lines) - 1) * (size + 1) / 2 + i * (size + 1) + size * 0.35, ln, size, True, INK, "center")


def node(p: Page, x, y, w, h, n, title, body, fill=BLUE, stroke=BLUE_S, size=8):
    p.box(x, y, w, h, title, body, fill, stroke, size)
    if n:
        p.rect(x - 6, y - 6, 14, 14, NAVY_S, None, r=7)
        p.text(x + 1, y + 4, str(n), 6.8, True, (1, 1, 1), "center")


def stop(p: Page, x, y, w, h, title, body, kind="hold"):
    f, s_ = {"hold": (AMBER, AMBER_S), "ok": (GREEN, GREEN_S), "gate": (RED, RED_S), "fail": (RED, RED_S),
             "none": (GREY, GREY_S)}[kind]
    p.rect(x, y, w, h, f, s_, 2.2, r=10)
    p.text(x + w / 2, y + 13, title, 8.4, True, INK, "center")
    for i, ln in enumerate(p.wrap(body, w - 10, 6.8)):
        p.text(x + w / 2, y + 24 + i * 8, ln, 6.8, False, MUTED, "center")


def lab(p: Page, x, y, t, rgb=MUTED, size=6.8, bold=True):
    p.text(x, y, t, size, bold, rgb, "center")


def page_flow_processing(p: Page):
    W1, H1 = 92, 44
    row = 92
    xs = [24 + i * 112 for i in range(7)]
    steps = [("Request", "POST /runs or CLI", BLUE, BLUE_S), ("RECEIVE", "create run; same Idempotency-Key = same run", BLUE, BLUE_S),
             ("PARSE_INPUTS", "JSON, text, CSV, e-mail, PDF -> one document format", BLUE, BLUE_S),
             ("RETRIEVE_POLICY", "plan controls by conditions; policy search (files re-checked first)", GREEN, GREEN_S),
             ("GATHER_EVIDENCE", "vendor, PO*, history, FX*, authority - only when needed", BLUE, BLUE_S),
             ("RECONCILE", "11 controls: findings, exact maths, exceptions, votes", PURPLE, PURPLE_S),
             ("DRAFT", "AI writes cited narrative (may only be more cautious)", AMBER, AMBER_S)]
    for i, ((t, b, f, s_), x) in enumerate(zip(steps, xs)):
        node(p, x, row, W1, H1 + 6, i + 1, t, b, f, s_, 7.6)
        if i:
            p.arrow(x - 20, row + 25, x, row + 25)
    # request validation exit
    diamond(p, 70, 196, 74, 46, "valid request?")
    p.arrow(70, row + 50, 70, 173)
    stop(p, 24, 238, 92, 40, "422 rejected", "no run created; bad field, unknown field, too many files", "none")
    p.arrow(70, 219, 70, 238); lab(p, 78, 230, "no")
    lab(p, 120, 190, "yes -> 2")
    # gather failure branch
    diamond(p, 518, 196, 84, 46, "tool fails / times out?")
    p.arrow(518, row + 50, 518, 173)
    node(p, 470, 238, 96, 42, 0, "retry x3, then UNKNOWN", "missing evidence listed; control votes HOLD", GREY, GREY_S, 7)
    p.arrow(518, 219, 518, 238); lab(p, 526, 230, "yes")
    # model validation
    diamond(p, 742, 196, 84, 46, "AI output valid?")
    p.arrow(742, row + 50, 742, 173)
    node(p, 694, 238, 110, 42, 0, "re-ask with errors (x2)", "still invalid -> safe standard wording (flagged) or FAILED if configured", GREY, GREY_S, 6.8)
    p.arrow(742, 219, 742, 238); lab(p, 750, 230, "no")
    # DECIDE
    node(p, 600, 304, 150, 54, 8, "DECIDE", "most cautious vote wins; grounding + freshness gate; AI may escalate, never relax", PURPLE, PURPLE_S, 7.4)
    p.arrow(742, 280, 700, 304); lab(p, 760, 292, "yes / fallback")
    diamond(p, 675, 400, 120, 60, "gates OK? policy retrieved + unchanged")
    p.arrow(675, 358, 675, 370)
    lab(p, 760, 404, "no: approve -> hold", RED_S)
    diamond(p, 470, 400, 110, 56, "recommended outcome")
    p.arrow(615, 400, 525, 400); lab(p, 570, 395, "yes")
    # outcome branches
    stop(p, 300, 470, 160, 66, "APPROVAL_GATE -> STOP", "APPROVE_FOR_POSTING / REJECT_DUPLICATE / REJECT_INVALID: approval request with explanation. "
         "Pool AWAITING_APPROVAL, webhook approval.requested", "gate")
    stop(p, 480, 470, 160, 66, "HOLD_FINALIZE -> STOP", "HOLD_FOR_INFORMATION: routed to owner + review date. Pool HELD, "
         "webhook run.needs_information. Resume with evidence", "hold")
    stop(p, 660, 470, 160, 66, "HOLD_FINALIZE -> STOP", "ESCALATE_CONTROL_REVIEW: fraud / control team, supplier not told. "
         "Pool ESCALATED, nothing can be posted", "fail")
    p.arrow(430, 410, 380, 470, "approve / reject")
    p.arrow(470, 428, 560, 470, "hold")
    p.arrow(510, 412, 720, 470, "escalate")
    p.arrow(735, 400, 790, 470)
    # global rails
    p.rect(24, 300, 250, 150, (0.99, 0.97, 0.97), RED_S, 1, True, r=8)
    p.text(34, 316, "Can happen at ANY step", 8.6, True, RED_S)
    rails = ["Budget exceeded (40 steps / 50 tool calls / 6 AI calls) -> FAILED, pool FAILED, webhook run.failed",
             "Unexpected error -> FAILED with reason; nothing posted",
             "Crash / restart -> state saved after every step; resume continues at the next step (scheduler every 5 min)",
             "Every step -> audit event (hash chain), reasoning entry, checkpoint",
             "Policy never changes while running; at restart a policy differing from the reviewed baseline holds every case"]
    yy = 332
    for r_ in rails:
        for j, ln in enumerate(p.wrap(r_, 226, 7.2)):
            p.text(34, yy, ("- " if j == 0 else "  ") + ln, 7.2, False, INK)
            yy += 9.5
        yy += 3
    p.text(24, 560, "* PO only for PO-backed invoices, FX only for foreign currency. Numbers = step order. Next page: what happens after the stop.",
           7.6, False, MUTED)


def page_flow_after(p: Page):
    # approval desk
    p.text(24, 86, "A. Approval decision arrives  (POST /approvals/{id}/decision, CLI, or signed webhook)", 9.5, True, BLUE_S)
    node(p, 24, 98, 104, 46, 0, "Decision in", "approve / reject (+ reason) with callback id", BLUE, BLUE_S, 7.4)
    diamond(p, 190, 121, 92, 50, "same callback seen before?")
    p.arrow(128, 121, 144, 121)
    stop(p, 140, 160, 100, 44, "replayed", "identical stored response; no effect", "none")
    p.arrow(190, 146, 190, 160); lab(p, 198, 154, "yes")
    diamond(p, 320, 121, 92, 50, "approval still pending?")
    p.arrow(236, 121, 274, 121); lab(p, 255, 116, "no")
    stop(p, 270, 160, 100, 44, "409 no effect", "already approved / rejected", "none")
    p.arrow(320, 146, 320, 160); lab(p, 328, 154, "no")
    diamond(p, 456, 121, 100, 54, "approver allowed? limit, delegation, conflicts")
    p.arrow(366, 121, 406, 121); lab(p, 386, 116, "yes")
    stop(p, 406, 164, 100, 44, "403 refused", "reason logged; stays pending", "fail")
    p.arrow(456, 148, 456, 164); lab(p, 464, 158, "no")
    diamond(p, 592, 121, 80, 46, "approve or reject?")
    p.arrow(506, 121, 552, 121); lab(p, 529, 116, "yes")
    stop(p, 552, 164, 120, 52, "FINALIZE_REJECTED", "COMPLETED, nothing posted; pool REJECTED_BY_APPROVER", "none")
    p.arrow(592, 144, 600, 164); lab(p, 610, 156, "reject")
    diamond(p, 730, 121, 92, 50, "all required approvals in?")
    p.arrow(632, 121, 684, 121); lab(p, 658, 116, "approve")
    stop(p, 690, 164, 130, 44, "wait for next approver", "e.g. Financial Control slot still open", "hold")
    p.arrow(730, 146, 740, 164); lab(p, 748, 156, "no")
    # posting
    p.text(24, 248, "B. Posting  (only after every required approval)", 9.5, True, BLUE_S)
    node(p, 24, 260, 124, 46, 9, "claim_run", "resumes the SAME run exactly once (atomic)", PURPLE, PURPLE_S, 7.4)
    p.poly_arrow([(776, 146), (840, 146), (840, 236), (86, 236), (86, 260)], "yes", label_at=1)
    diamond(p, 230, 283, 108, 56, "cited policy unchanged since decision?")
    p.arrow(148, 283, 176, 283)
    stop(p, 172, 324, 118, 52, "posting BLOCKED", "HOLD_FINALIZE: policy changed after approval; re-evaluate with new attempt", "hold")
    p.arrow(230, 311, 230, 324); lab(p, 238, 318, "no")
    node(p, 320, 260, 132, 46, 10, "SUBMIT_DECISION", "simulated ERP posting; idempotency key = never twice", RED, RED_S, 7.4)
    p.arrow(284, 283, 320, 283); lab(p, 302, 278, "yes")
    stop(p, 486, 256, 150, 54, "FINALIZE -> COMPLETED", "pool SUBMITTED; webhooks decision.submitted, run.completed", "ok")
    p.arrow(452, 283, 486, 283)
    # evidence + recovery
    p.text(24, 410, "C. New evidence  (held / escalated / failed cases)", 9.5, True, BLUE_S)
    node(p, 24, 422, 130, 46, 0, "POST /runs/{id}/evidence", "PO number, documents, notes", BLUE, BLUE_S, 7.4)
    node(p, 180, 422, 150, 46, 0, "new attempt (attempt + 1)", "linked to parent; old result kept; ALL controls re-run", PURPLE, PURPLE_S, 7.2)
    p.arrow(154, 445, 180, 445)
    stop(p, 356, 420, 120, 50, "back to step 2", "same flow as page 1; may approve, hold or escalate again", "ok")
    p.arrow(330, 445, 356, 445)
    p.text(500, 410, "D. Recovery", 9.5, True, BLUE_S)
    rec = ["Crash mid-run -> resume from the last saved step (manual or every 5 min)",
           "Crash after posting -> resume reuses the posting reference: still exactly one posting",
           "Approval recorded but run not resumed -> resume repairs it",
           "Resume on a stopped / finished run -> no-op (logged)",
           "Webhook delivery failed -> retried until DELIVERED or DEAD"]
    yy = 428
    for r_ in rec:
        for j, ln in enumerate(p.wrap(r_, 310, 7.4)):
            p.text(506, yy, ("- " if j == 0 else "  ") + ln, 7.4, False, INK)
            yy += 9.6
        yy += 2
    p.text(24, 560, "Every arrow writes an audit event and a reasoning entry; finagent explain <run> and finagent diagram <run> show the path one run took.",
           7.6, False, MUTED)


def page_governance(p: Page):
    # top row: the maintenance release
    node(p, 24, 92, 140, 58, 1, "Policy file changes", "finance edits a .md in finance_rag_corpus/", GREEN, GREEN_S, 7.2)
    node(p, 188, 92, 150, 58, 2, "Update the checks", "controls / skills / config/policy_parameters.yaml, with tests", PURPLE, PURPLE_S, 7.2)
    node(p, 362, 92, 130, 58, 3, "make test eval", "unit, contract, skill and scenario tests + evaluation pass", PURPLE, PURPLE_S, 7.2)
    node(p, 516, 92, 150, 58, 4, "Re-baseline", "finagent policy baseline --reviewer <id> --note <why> (a named person)", RED, RED_S, 7.2)
    node(p, 690, 92, 130, 58, 5, "Deploy / restart", "the running service never changes its policy or index", BLUE, BLUE_S, 7.2)
    for x0, x1 in ((164, 188), (338, 362), (492, 516), (666, 690)):
        p.arrow(x0, 121, x1, 121)
    # start-up box
    p.rect(24, 176, 796, 112, (0.98, 0.98, 1.0), BLUE_S, 1.0, True, r=8)
    p.text(36, 192, "At start-up (services.build_app)", 9, True, BLUE_S)
    rows = [("1  Index", "corpus files changed -> BM25 index + vectors rebuilt (rag.auto_reindex)"),
            ("2  Search model", "models/embedder/trained_on.json must match the current corpus; if not, the gated retrain runs "
                                "(scripts/training/retrain.py, rag.retrain_on_startup). Off, impossible or failed -> keyword search (BM25) only, "
                                "never an unverified model"),
            ("3  Policy baseline", "section hashes vs config/policy_baseline.yaml. Any difference -> every case HELD at DECIDE; "
                                   "/healthz shows \"policy\": \"maintenance_required\"")]
    y = 210
    for step, txt in rows:
        p.text(36, y, step, 7.6, True, PURPLE_S)
        for j, ln in enumerate(p.wrap(txt, 640, 7.4)):
            p.text(150, y + j * 9.5, ln, 7.4, False, INK)
        y += 9.5 * len(p.wrap(txt, 640, 7.4)) + 10
    # run-time box
    p.rect(24, 304, 796, 124, (0.98, 0.98, 1.0), BLUE_S, 1.0, True, r=8)
    p.text(36, 320, "Every case (run time)", 9, True, BLUE_S)
    rows = [("RETRIEVE_POLICY", "policy numbers + baseline status frozen into the run"),
            ("DECIDE: policy gate", "policy text differs from the baseline -> approval downgraded to HOLD (OTHER_CONTROL_RISK, "
                                    "audit policy.unverified, result.policy lists the unverified sections)"),
            ("DECIDE: freshness", "a cited section changed during the run -> HOLD (policy.changed_during_run; matters across a restart)"),
            ("SUBMIT_DECISION", "cited policy re-checked right before posting; changed since approval -> nothing posted "
                                "(policy.changed_since_decision)")]
    y = 338
    for step, txt in rows:
        p.text(36, y, step, 7.6, True, PURPLE_S)
        for j, ln in enumerate(p.wrap(txt, 640, 7.4)):
            p.text(150, y + j * 9.5, ln, 7.4, False, INK)
        y += 9.5 * len(p.wrap(txt, 640, 7.4)) + 10
    p.text(24, 452, "Why not apply policy changes live? A running service never changes its rules mid-way: same answer every time, "
           "tested checks, and a named reviewer behind every interpretation.", 7.6, False, MUTED)
    p.text(24, 474, "Commands: finagent policy status | params | baseline --reviewer <id> --note <why>   (make policy).   "
           "API: GET /policy/status, /policy/parameters.   Doc: docs/governance.md", 7.6, True, BLUE_S)


REAL_F, REAL_S = GREEN, GREEN_S          # real in the POC
SIM_F, SIM_S = AMBER, AMBER_S            # simulated today
OPT_F = (1, 1, 1)                        # production option (dashed)


def chip(p: Page, x, y, w, h, title, body, kind):
    f, s_ = {"real": (REAL_F, REAL_S), "sim": (SIM_F, SIM_S), "opt": (OPT_F, GREY_S), "gate": (BLUE, BLUE_S),
             "code": (PURPLE, PURPLE_S), "out": (RED, RED_S)}[kind]
    p.rect(x, y, w, h, f, s_, 1.0, kind == "opt", r=5)
    p.text(x + 6, y + 11, title, 7.4, True, INK)
    for i, ln in enumerate(p.wrap(body, w - 12, 6.3)[:max(1, int((h - 16) // 7.6))]):
        p.text(x + 6, y + 20 + i * 7.6, ln, 6.3, False, MUTED)


def page_integration(p: Page):
    # column headers
    cols = [(24, 196, "SOURCE (where data lives)"), (230, 168, "PLUG-IN POINT (what you write)"),
            (408, 136, "CONTRACT (fixed shape)"), (554, 148, "RUNTIME CHECKS"), (712, 108, "WORKFLOW STEP")]
    for x, w, t in cols:
        p.text(x + w / 2, 80, t, 7.6, True, NAVY_S, "center")
    lanes = [(88, 112, "1  Case request & documents  (arrive WITH the case; untrusted)"),
             (206, 142, "2  Reference systems  (looked up DURING the case; authoritative)"),
             (354, 104, "3  Policy sources  (searched DURING the case; trust from metadata)"),
             (464, 92, "4  Outputs  (only after checks / approval)")]
    for y, h, t in lanes:
        p.rect(18, y, 806, h, (0.985, 0.988, 0.995), GREY_S, 0.6, True, r=8)
        p.text(26, y + 10, t, 7.6, True, BLUE_S)

    # ---- lane 1: case request and documents
    chip(p, 24, 102, 94, 44, "API / CLI request", "POST /runs + attachments: PDF, CSV, e-mail, JSON, text", "real")
    chip(p, 122, 102, 98, 44, "+ new formats", "XML / UBL, Word, scanned PDF (OCR)", "opt")
    chip(p, 24, 150, 196, 44, "Approver callbacks", "API / CLI decision or signed webhook (HMAC) from an approval system", "real")
    chip(p, 230, 102, 168, 44, "Parser registry", "@register_parser: routes by content sniff > type > extension (5 built-in + plugins)", "code")
    chip(p, 230, 150, 168, 44, "Signature + replay check", "HMAC verify, callback id replay cache (approval_id:callback_id)", "code")
    chip(p, 408, 102, 136, 44, "ProcessingRequest + document/1", "strict request (no unknown fields) + NormalizedDocument per file", "gate")
    chip(p, 408, 150, 136, 44, "ApprovalDecisionIn", "APPROVE / REJECT (+ reason), approver, callback id", "gate")
    chip(p, 554, 102, 148, 92, "Request & parse checks", "schema validation (422), max 10 files, idempotency key, text fenced as DATA "
         "(never instructions), redaction, parse warnings", "gate")
    chip(p, 712, 102, 106, 92, "RECEIVE  /  PARSE_INPUTS", "case documents become evidence; parsing is real in the POC", "code")
    for y in (124, 172):
        p.arrow(220, y, 230, y); p.arrow(398, y, 408, y); p.arrow(544, y, 554, y if y == 124 else 172)
    p.arrow(702, 148, 712, 148)

    # ---- lane 2: reference systems
    srcs = [("JSON mock files", "mock_data/*.json - TODAY", "sim"), ("REST APIs", "SAP / Oracle / Coupa / treasury", "opt"),
            ("MCP servers", "e.g. an ERP MCP server's tools", "opt"), ("Database", "read-only SQL views", "opt"),
            ("File exports", "CSV / Excel drops on a share", "opt")]
    for i, (t, b, k) in enumerate(srcs):
        chip(p, 24 + (i % 2) * 98, 220 + (i // 2) * 40, 94 if i < 4 else 192, 36, t, b, k)
    chip(p, 230, 220, 168, 56, "Backend adapter (ONE class)", "vendor(), purchase_order(), history_records(), fx(), "
         "authority_register(). Today MockBackend; swap via build_tools(..., backend)", "code")
    chip(p, 230, 282, 168, 58, "7 tool functions", "get_vendor_record, get_purchase_order, check_invoice_history, get_fx_rate, "
         "get_authority_register, retrieve_finance_documents, submit_finance_decision", "code")
    chip(p, 408, 220, 136, 120, "Typed tool outputs", "VendorRecord, PurchaseOrder (+ receipts), InvoiceHistoryOutput, FxRate, "
         "AuthorityRegister. Any source must return exactly this shape - the rest of the system never sees the source.", "gate")
    chip(p, 554, 220, 148, 120, "Tool runtime (next page)", "1 budget  2 input schema  3 deny-by-default guard  "
         "4 timeout + retries  5 output schema  6 audit per attempt. Failure -> UNKNOWN -> case HELD, never guessed", "gate")
    chip(p, 712, 220, 106, 120, "GATHER_EVIDENCE", "vendor, PO only if PO-backed, history, FX only if foreign currency, "
         "authority register", "code")
    p.arrow(220, 248, 230, 248); p.arrow(314, 276, 314, 282); p.arrow(398, 300, 408, 300)
    p.arrow(544, 280, 554, 280); p.arrow(702, 280, 712, 280)

    # ---- lane 3: policy sources
    chip(p, 24, 368, 94, 40, "Markdown folder", "finance_rag_corpus/ - indexed at start-up", "real")
    chip(p, 122, 368, 98, 40, "HTTP feed", "JSON CorpusDocument API", "real")
    chip(p, 24, 412, 196, 40, "+ S3 / SharePoint / Confluence", "@register_connector plugins", "opt")
    chip(p, 230, 368, 168, 84, "Connectors + loaders", "@register_connector (local_dir, http_feed), @register_loader (.md .json .txt .pdf); "
         "trust from front matter (current / superseded / untrusted)", "code")
    chip(p, 408, 368, 136, 84, "finagent.corpus/1", "CorpusDocument -> section chunks with stable ids (FIN-POL-002@v2.4#s2)", "gate")
    chip(p, 554, 368, 148, 84, "Index + checks", "BM25 + vectors, rebuilt at start-up when the corpus changed; access filter; "
         "trust re-rank; tool runtime on every search", "gate")
    chip(p, 712, 368, 106, 84, "RETRIEVE_POLICY", "citations; grounding + policy gates at DECIDE", "code")
    p.arrow(220, 400, 230, 400); p.arrow(398, 410, 408, 410); p.arrow(544, 410, 554, 410); p.arrow(702, 410, 712, 410)

    # ---- lane 4: outputs
    chip(p, 712, 478, 106, 70, "APPROVAL_GATE -> SUBMIT", "after every required approval", "code")
    chip(p, 554, 478, 148, 70, "submit guard", "approval APPROVED; outcome, amount, currency must match; idempotency key "
         "= never twice", "gate")
    chip(p, 408, 478, 136, 70, "SubmitDecisionOutput", "posting_ref, status (typed)", "gate")
    chip(p, 230, 478, 168, 32, "ERP posting sandbox", "TODAY: erp_decisions table (no money moves)", "sim")
    chip(p, 230, 514, 168, 34, "+ ERP sandbox / UAT API", "production: same adapter pattern", "opt")
    chip(p, 24, 478, 196, 32, "Webhooks + outcome pool + audit", "signed events, API / CLI, hash-chained audit", "real")
    chip(p, 24, 514, 196, 34, "+ SQS / EventBridge / SIEM", "swap the delivery channel", "opt")
    p.arrow(712, 512, 702, 512); p.arrow(554, 512, 544, 512); p.arrow(408, 496, 398, 496)
    p.legend([("real in the POC", REAL_F, REAL_S), ("simulated today (JSON / SQLite)", SIM_F, SIM_S),
              ("production option (dashed)", OPT_F, GREY_S), ("plug-in code", PURPLE, PURPLE_S), ("contract / checks", BLUE, BLUE_S)])


def page_tool_runtime(p: Page):
    # left: registry
    p.rect(20, 80, 196, 470, (0.985, 0.988, 0.995), GREY_S, 0.8, True, r=8)
    p.text(30, 96, "ToolSpec registry (finance_tools.py)", 8.4, True, NAVY_S)
    tools = [("retrieve_finance_documents", "read:corpus", "RetrieveDocsInput -> RetrieveDocsOutput"),
             ("get_vendor_record", "read:vendor_master", "VendorInput -> VendorRecord"),
             ("get_purchase_order", "read:erp_purchasing", "POInput -> PurchaseOrder"),
             ("check_invoice_history", "read:ap_ledger", "InvoiceHistoryInput -> InvoiceHistoryOutput"),
             ("get_fx_rate", "read:treasury_rates", "FxInput -> FxRate"),
             ("get_authority_register", "read:authority_register", "AuthorityInput -> AuthorityRegister"),
             ("submit_finance_decision", "write:erp_decision  CONSEQUENTIAL", "SubmitDecisionInput -> SubmitDecisionOutput  + guard")]
    for i, (n, perm, io) in enumerate(tools):
        y = 106 + i * 58
        last = i == len(tools) - 1
        chip(p, 28, y, 180, 52, n, f"{perm}. {io}", "out" if last else "code")
    p.text(30, 520, "Each spec: name, description, input model,", 6.6, False, MUTED)
    p.text(30, 529, "output model, func, consequential, guard,", 6.6, False, MUTED)
    p.text(30, 538, "permissions (labels; see note).", 6.6, False, MUTED)

    # centre: the call pipeline
    p.text(236, 96, "ToolRunner.call(name, args, ctx)  - tools/base.py: every tool call goes through these steps, in order", 8.4, True, NAVY_S)
    steps = [("0", "Look up tool", "unknown name -> ToolNotFound", "registry", None),
             ("1", "Budget", "ctx.budget.tool_calls + 1 > agent.max_tool_calls (50) -> tool.denied, run FAILED (budget exceeded)",
              "settings", "tool.denied"),
             ("2", "Input schema", "pydantic model_validate(args). Invalid -> ok=False, tool.invalid_arguments; arguments are never "
              "repaired or guessed", "contract", "tool.invalid_arguments"),
             ("3", "Permission gate", "consequential tool: no authorization -> DENIED; then the tool's guard (7 checks below). "
              "Read tools pass", "deny-by-default", "tool.denied"),
             ("4", "Execute with timeout", "runs spec.func in a worker pool; waits tools.<name>.timeout_s (default 2 s, PO 1 s, "
              "search 5 s)", "isolation", None),
             ("5", "Output schema", "result must validate against the output model. Garbage from a backend = failure, never evidence "
              "(not retried)", "contract", None),
             ("6", "Retry policy", "only retryable errors (timeout, TransientToolError e.g. HTTP 503): up to max_attempts (3) with "
              "exponential backoff 0.05 / 0.1 s; others stop at once", "resilience", "tool.call retrying"),
             ("7", "Evidence id + audit", "evidence id e.g. vendor:V-1001@<last_updated>, po:PO-40007@..., fx:USDAUD@date; "
              "audit tool.call per ATTEMPT with redacted args/result summary + duration", "traceability", "tool.call ok / failed")]
    y = 108
    for n, t, b, tag, ev in steps:
        h = 48 if len(b) > 110 else 40
        p.rect(236, y, 400, h, BLUE, BLUE_S, 1.0, r=6)
        p.rect(230, y + h / 2 - 8, 16, 16, NAVY_S, None, r=8)
        p.text(238, y + h / 2 + 3.5, n, 7.2, True, (1, 1, 1), "center")
        p.text(254, y + 13, t, 8.2, True, INK)
        p.text(630, y + 13, tag, 6.6, False, BLUE_S, "right")
        for j, ln in enumerate(p.wrap(b, 372, 6.6)[:3]):
            p.text(254, y + 24 + j * 8, ln, 6.6, False, INK)
        if ev:
            p.text(630, y + h - 5, "audit: " + ev, 6.2, True, RED_S, "right")
        if n != "7":
            p.arrow(436, y + h, 436, y + h + 6)
        y += h + 6

    # right: results
    p.rect(650, 80, 176, 300, (0.985, 0.995, 0.985), GREEN_S, 0.8, True, r=8)
    p.text(660, 96, "What the agent gets back", 8.4, True, NAVY_S)
    chip(p, 658, 104, 160, 74, "ToolResult (always)", "tool, ok, output (typed), error, error_type, attempts, duration_ms, "
         "evidence_id. The engine never sees a raw backend response.", "gate")
    chip(p, 658, 184, 160, 60, "ok = True", "typed output becomes evidence; its evidence id is cited in findings and facts", "real")
    chip(p, 658, 250, 160, 70, "ok = False", "GATHER_EVIDENCE records the error; the control reports UNKNOWN + HOLD with what is "
         "missing (FIN-POL-001 s5). Nothing is guessed.", "sim")
    chip(p, 658, 326, 160, 48, "Raised (stop the run)", "budget exceeded, permission denied -> run FAILED / submit refused", "out")
    for yy in (178, 244, 320):
        p.arrow(738, yy, 738, yy + 6)

    # bottom right: submit guard
    p.rect(650, 388, 176, 162, (1.0, 0.97, 0.97), RED_S, 0.8, True, r=8)
    p.text(660, 404, "submit_finance_decision guard", 8.2, True, RED_S)
    checks = ["approval exists for THIS run", "approval status = APPROVED", "authorization names this approval",
              "outcome = approved outcome", "amount + currency = approved", "verb POST / REJECT matches outcome",
              "idempotency key = hash(run, approval, ...)", "same key again -> DUPLICATE_IGNORED"]
    for i, c in enumerate(checks):
        p.text(660, 418 + i * 15.5, f"{i + 1 if i < 7 else '+'}  {c}", 6.8, i == 7, INK)
    p.arrow(636, 214, 650, 214, "")

    # config + notes
    p.text(236, 548, "Config (config/settings.yaml): tools.default_timeout_s 2.0, tools.max_attempts 3, tools.backoff_base_s 0.05, "
           "tools.overrides.<tool>.timeout_s / max_attempts, agent.max_tool_calls 50.", 6.8, False, MUTED)
    p.text(236, 558, "Notes: permissions are declared labels (shown in /catalog/tools; map to IAM / service accounts in production) - "
           "enforcement here is the consequential flag + guard.", 6.8, False, MUTED)
    p.text(236, 568, "A timed-out call is abandoned, not killed (Python threads): real backends should also set their own HTTP / DB "
           "timeouts. Faults are injected in the mock via mock_data/faults.json.", 6.8, False, MUTED)


def page_cover(p: Page, title="architecture diagrams", contents=(), notes=()):
    p.rect(0, 0, W, H, (0.11, 0.17, 0.30), None, r=0)
    p.text(60, 120, "finagent", 40, True, (1, 1, 1))
    p.text(60, 152, f"Financial Processing RAG Workflow Agent - {title}", 16, False, (0.82, 0.87, 0.96))
    per_col = -(-len(contents) // 2) if len(contents) > 12 else len(contents)
    for i, item in enumerate(contents):
        col, row = divmod(i, per_col)
        p.text(60 + col * 380, 192 + row * 17, item, 10.5, False, (1, 1, 1))
    for j, n in enumerate(notes):
        p.text(60, 520 + j * 16, n, 9, False, (0.75, 0.80, 0.90))


def build(pages: list[Page]) -> bytes:
    objs: list[bytes] = []
    kids = []
    font_ids = (3, 4)
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"")  # pages placeholder
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    for pg in pages:
        stream = "\n".join(pg.ops).encode("latin-1", errors="replace")
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        cid = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W} {H}] /Resources << /Font << /F1 {font_ids[0]} 0 R "
                    f"/F2 {font_ids[1]} 0 R >> >> /Contents {cid} 0 R >>".encode())
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info << /Title (finagent architecture) >> >>\nstartxref\n{x}\n%%EOF\n".encode()
    return bytes(out)


def _render(specs, title, notes, out_name, cover_page=True):
    pages = []
    if cover_page:
        cover = Page()
        page_cover(cover, title, [f"{n}  {t}" for n, (t, _, _) in enumerate(specs, start=1)], notes)
        pages.append(cover)
    for n, (t, sub, fn) in enumerate(specs, start=1):
        heading = f"{n}. {t}" if len(specs) > 1 else t
        pg = Page(); pg.header(heading, "", n, len(specs)); pg.text(28, 64, sub, 9, False, MUTED); fn(pg); pages.append(pg)
    out = ROOT / "docs" / "diagrams" / out_name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build(pages))
    print(f"wrote {out} ({len(pages)} pages)")


def main():
    import arch_inside_pages as X
    from infra_catalog import COMPONENTS
    # architecture.pdf is ONE page: the system context (inputs -> service blocks with their libraries -> outputs).
    # Flows: code_llm_flow.pdf, workflow_scenarios.pdf · code: code_map.pdf, call_chain.pdf · data: db_schema.pdf
    # · infrastructure: docs/infrastructure.md · internals: system_detailed_design.pdf
    high = [("finagent architecture: system context", "Who sends work in, what runs inside the service (and with which library), what comes out "
             "(real vs simulated).", page_context)]
    _render(high, "architecture (high level)", [], "architecture.pdf", cover_page=False)
    design_front = [
        ("Agent state graph", "Explicit, bounded, checkpointed graph. Code decides; the model drafts; consequential steps stop for approval.", page_graph),
        ("Trust boundaries", "Instructions come only from code, config and skill questions. Everything else is data, enforced at nine points.", page_trust),
        ("RAG pipeline", "Sources -> contract -> chunks -> BM25 + local vectors -> access filter -> trust re-rank -> grounded citations.", page_rag),
        ("Policy change: a maintenance release, never live", "Update checks + tests, re-baseline, restart. At start-up the index "
         "and search model are brought up to date; an unreviewed policy change holds every case.", page_governance)]
    design_back = [
        ("AWS deployment", "Terraform in infra/terraform: ALB -> ECS Fargate (1 task) -> EFS, Secrets Manager, Bedrock, CloudWatch, ECR, S3.", page_aws),
        ("Infrastructure components", "Every component in use - local and AWS - numbered; details and alternatives on the next pages.", page_infra),
        ("Component catalogue (1/3): application, data", "What each component does, why it was chosen, and what to switch to when circumstances change.",
         page_infra_table(COMPONENTS[:9])),
        ("Component catalogue (2/3): retrieval, models, integration, local", "Continued.", page_infra_table(COMPONENTS[9:17])),
        ("Component catalogue (3/3): AWS", "Continued.", page_infra_table(COMPONENTS[17:])),
        ("When circumstances change", "Which components to swap for common changes in volume, residency, cost, scale or audit needs.", page_infra_scenarios)]
    detail = [("Code map (1/2): entry, core, skills, tools", "Main code components, where they live, their key classes / functions, and how they are used.",
               X.page_code_map(1)),
              ("Code map (2/2): RAG, model, policy, data, quality", "Continued.", X.page_code_map(2)),
              ("Module design (src/finagent/)", "Modules, classes and responsibilities inside the container; arrows show call direction.", page_service_detail),
              ("Integration: where external data plugs in", "Four routes in and out. Only the plug-in point changes per source "
               "(JSON mock -> REST API, MCP server, database, files); contracts and runtime checks stay fixed.", page_integration),
              ("Inside the tool runtime", "Every call from the agent to a reference system, the policy index or the posting sandbox passes "
               "these steps. The agent only ever receives a typed ToolResult.", page_tool_runtime),
              ("Inside the agent engine", "How a run is created, executed step by step, checkpointed, gated, approved, failed and recovered.",
               X.page_inside_engine),
              ("Inside the skills", "Skill packs (spec + fixed questions + references): code asks, small / large model answers, code decides.", X.page_inside_skills),
              ("Inside the RAG retriever", "From policy files to ranked, trust-labelled, citable chunks - built at start-up.",
               X.page_inside_rag),
              ("Inside the model adapter", "Prompt with fenced data, any provider, strict validation, repair loop, and a guard that only allows caution.",
               X.page_inside_model),
              ("Inside the policy baseline", "Policy numbers and the start-up check that the policy text matches what the controls were verified against.", X.page_inside_governance),
              ("API walk-through (1/2): POST /runs", "One request traced through every component, in order, until the run stops at the approval gate.",
               X.page_walk_run),
              ("API walk-through (2/2): POST /approvals/{id}/decision", "The approval decision resumes the same run: checks, posting, completion.",
               X.page_walk_decision),
              ("Request lifecycle sequence", "Start run -> stop at approval gate -> approval decision resumes the same run -> exactly-once "
               "simulated posting.", page_sequence),
              ("Persistence data model", "SQLite tables (WAL) and index files under data/; all state needed to explain, reproduce and resume a run.",
               page_data_model)]
    _render(design_front + detail + design_back, "system detailed design", [
        "High-level architecture: docs/diagrams/architecture.pdf.  API reference: docs/api/finagent_api_reference.pdf.  "
        "Database: docs/database.md.",
        "Each box names the source file it describes. Regenerate: make diagrams."], "system_detailed_design.pdf")


if __name__ == "__main__":
    main()
