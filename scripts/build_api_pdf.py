#!/usr/bin/env python3
"""Render docs/api/finagent_api_reference.pdf: the FULL API specification, generated from docs/api/openapi.json
(itself exported from the running FastAPI app by scripts/export_openapi.py), so the PDF can never drift from the code.

    python scripts/export_openapi.py && python scripts/build_api_pdf.py      (or: make api-pdf / make diagrams)

Contents: overview (servers, auth, idempotency, errors, statuses, webhooks + signatures), every endpoint grouped by tag
(parameters, request body fields with types / constraints / descriptions, example, responses, error codes), and an
appendix with every schema.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_arch_pdf as B  # noqa: E402

ROOT = B.ROOT
SPEC = json.loads((ROOT / "docs" / "api" / "openapi.json").read_text())
SCHEMAS = dict(SPEC.get("components", {}).get("schemas", {}))
sys.path.insert(0, str(ROOT / "src"))
from finagent.contracts import export_json_schemas  # noqa: E402

CONTRACTS = {}
for _file, _sch in export_json_schemas().items():      # versioned data contracts (incl. FinalResult) + their sub-schemas
    CONTRACTS[_sch.get("title", _file)] = _file
    for _n, _d in (_sch.get("$defs") or {}).items():
        SCHEMAS.setdefault(_n, _d)
    SCHEMAS.setdefault(_sch.get("title", _file), {k: v for k, v in _sch.items() if k != "$defs"})

RETURNS = {   # endpoints that return plain JSON (no response model): what the body contains
    ("post", "/runs"): "Run view: run_id, case_id, status, current_node, attempt, parent_run_id, result (FinalResult), "
                       "approval (approval request incl. explanation, required slots, decisions), error; audit_events if include_audit=true.",
    ("get", "/runs"): "List of run summaries (run_id, case_id, status, current_node, attempt, created_at, updated_at).",
    ("get", "/runs/{run_id}"): "Run view (as POST /runs) incl. checkpoints and audit_events unless include_audit=false.",
    ("get", "/runs/{run_id}/reasoning"): "List of reasoning entries: {at, step, actor (system | skill:<name> | model | human:<id>), "
                                         "decision, reason, refs}.",
    ("get", "/runs/{run_id}/diagram"): "image/svg+xml, image/png or application/pdf of the run (path, exit step, tools, skills, decision, approvals).",
    ("post", "/runs/{run_id}/resume"): "Run view after resuming (no-op if nothing to resume).",
    ("post", "/runs/{run_id}/evidence"): "Run view of the NEW attempt (attempt + 1, parent_run_id = this run).",
    ("get", "/approvals"): "List of approval requests with explanation, required_slots, decisions, status.",
    ("get", "/approvals/{approval_id}"): "Approval request: approval_id, run_id, case_id, proposed_outcome, decision_type, amount, "
                                         "amount_aud, explanation {summary, what_approval_does, exceptions, citations}, required_slots, decisions, status.",
    ("post", "/approvals/{approval_id}/decision"): "{status: PENDING | APPROVED | REJECTED, approval, run} - the run resumes when the "
                                                  "approval resolves; a replayed callback_id returns the stored response.",
    ("get", "/outcomes"): "Outcome pool rows: run_id, case_id, outcome, pool_status, primary_exception, next_action, updated_at.",
    ("get", "/policy/status"): "{state: ok | maintenance_required, unverified_sections [{section, change}], baseline_reviewed_by, "
                               "baseline_reviewed_at}. maintenance_required = every case is held.",
    ("get", "/policy/parameters"): "List: {name, value, cite, unit, description} - every policy number the controls enforce.",
}
LEFT, RIGHT, TOP, BOTTOM = 28, 814, 72, 562
METHOD_RGB = {"get": B.BLUE_S, "post": B.GREEN_S, "delete": B.RED_S, "put": B.AMBER_S, "patch": B.AMBER_S}
MONO = (0.96, 0.96, 0.97)

ERRORS = {   # documented behaviour per path prefix (from the handlers) - shown under each endpoint
    "/runs": "401 bad/missing X-API-Key | 404 run not found | 422 invalid request (strict schema, unknown fields rejected)",
    "/approvals": "403 approver not allowed (limit, delegation, SoD, refused) | 404 approval not found | 409 already resolved "
                  "(no effect) | 422 invalid body (REJECT needs a comment >= 5 chars)",
    "/webhooks/inbound": "401 bad HMAC signature / stale timestamp | same callback id replays the stored response",
}


def clean(t) -> str:
    s = str(t)
    for a, b in {"→": "->", "←": "<-", "—": "-", "–": "-", "‘": "'", "’": "'", "“": '"',
                 "”": '"', "…": "...", "≤": "<=", "≥": ">=", "×": "x", "<br/>": " ", "**": "", "`": ""}.items():
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


class Doc:
    def __init__(self):
        self.pages: list[tuple[B.Page, str]] = []
        self.section = ""
        self.y = TOP
        self.toc: list[tuple[str, int, int]] = []    # (title, page index, level)

    @property
    def p(self) -> B.Page:
        return self.pages[-1][0]

    def new_page(self, section: str | None = None):
        if section is not None:
            self.section = section
        self.pages.append((B.Page(), self.section))
        self.y = TOP

    def need(self, h: float):
        if self.y + h > BOTTOM:
            self.new_page()

    def heading(self, text: str, level: int = 1):
        size = {1: 15, 2: 11.5, 3: 9.6}[level]
        self.need(size + 30 if level < 3 else size + 40)
        if level == 1:
            self.toc.append((text, len(self.pages) - 1, 1))
        elif level == 2:
            self.toc.append((text, len(self.pages) - 1, 2))
        self.p.text(LEFT, self.y + size, clean(text), size, True, B.NAVY_S if level == 1 else B.BLUE_S)
        self.y += size + (10 if level == 1 else 7)
        if level == 1:
            self.p.rect(LEFT, self.y - 4, RIGHT - LEFT, 1.2, B.BLUE_S, None, r=0)
            self.y += 6

    def para(self, text: str, size: float = 8.4, bold: bool = False, rgb=B.INK, indent: float = 0, gap: float = 4):
        lines = self.p.wrap(clean(text), RIGHT - LEFT - indent, size)
        for ln in lines:
            self.need(size + 3)
            self.p.text(LEFT + indent, self.y + size, ln, size, bold, rgb)
            self.y += size + 2.6
        self.y += gap

    def bullets(self, items: list[str], size: float = 8.2):
        for it in items:
            lines = self.p.wrap(clean(it), RIGHT - LEFT - 16, size)
            for i, ln in enumerate(lines):
                self.need(size + 3)
                self.p.text(LEFT + (6 if i == 0 else 14), self.y + size, ("- " if i == 0 else "") + ln, size, False, B.INK)
                self.y += size + 2.6
        self.y += 4

    def table(self, header: list[str], rows: list[list[str]], widths: list[float], size: float = 7.2):
        total = RIGHT - LEFT
        ws = [w * total for w in widths]

        def row_h(cells, bold=False):
            return max(len(self.p.wrap(clean(c), w - 8, size)) for c, w in zip(cells, ws)) * (size + 2.4) + 6

        def draw(cells, fill, bold=False):
            h = row_h(cells, bold)
            self.need(h)
            x = LEFT
            for c, w in zip(cells, ws):
                self.p.rect(x, self.y, w, h, fill, B.GREY_S, 0.4, r=0)
                for i, ln in enumerate(self.p.wrap(clean(c), w - 8, size)):
                    self.p.text(x + 4, self.y + 4 + size + i * (size + 2.4), ln, size, bold, B.INK)
                x += w
            self.y += h

        draw(header, (0.90, 0.93, 0.98), True)
        for i, r in enumerate(rows):
            if self.y + row_h(r) > BOTTOM:
                self.new_page()
                draw(header, (0.90, 0.93, 0.98), True)
            draw(r, (1, 1, 1) if i % 2 == 0 else (0.975, 0.98, 0.99))
        self.y += 8

    def code(self, text: str, size: float = 6.8, max_lines: int = 34):
        lines = clean(text).splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines - see docs/api/openapi.json)"]
        wrapped = []
        for ln in lines:
            wrapped += self.p.wrap(ln.replace(" ", " ") if False else ln, RIGHT - LEFT - 16, size) or [""]
        i = 0
        while i < len(wrapped):
            room = int((BOTTOM - self.y - 8) // (size + 2.2))
            if room < 3:
                self.new_page()
                continue
            chunk = wrapped[i:i + room]
            h = len(chunk) * (size + 2.2) + 8
            self.p.rect(LEFT, self.y, RIGHT - LEFT, h, MONO, B.GREY_S, 0.4, r=3)
            for j, ln in enumerate(chunk):
                indent = (len(ln) - len(ln.lstrip(" "))) * size * 0.5
                self.p.text(LEFT + 8 + indent, self.y + 4 + size + j * (size + 2.2), ln.strip(), size, False, B.INK)
            self.y += h + 6
            i += len(chunk)


# ============================================================================== schema helpers
def ref_name(s: dict) -> str | None:
    r = s.get("$ref")
    return r.split("/")[-1] if r else None


def type_of(s: dict) -> str:
    if not s:
        return "any"
    if ref_name(s):
        return ref_name(s)
    for k in ("anyOf", "oneOf", "allOf"):
        if k in s:
            parts = [type_of(x) for x in s[k] if x.get("type") != "null"]
            opt = any(x.get("type") == "null" for x in s[k])
            return " | ".join(parts) + (" (nullable)" if opt else "")
    t = s.get("type", "object")
    if t == "array":
        return f"array[{type_of(s.get('items', {}))}]"
    if t == "object" and "additionalProperties" in s and isinstance(s["additionalProperties"], dict):
        return f"map[string, {type_of(s['additionalProperties'])}]"
    if s.get("format"):
        return f"{t} ({s['format']})"
    return t


def constraints(s: dict) -> str:
    out = []
    src = s
    for k in ("anyOf", "oneOf"):
        if k in s:
            src = next((x for x in s[k] if x.get("type") != "null"), s)
    if "enum" in src:
        out.append("one of: " + ", ".join(map(str, src["enum"])))
    if "const" in src:
        out.append(f"= {src['const']}")
    for k, lab in (("pattern", "pattern"), ("minLength", "min len"), ("maxLength", "max len"), ("minimum", ">="),
                   ("maximum", "<="), ("exclusiveMinimum", ">"), ("minItems", "min items"), ("maxItems", "max items")):
        if k in src:
            out.append(f"{lab} {src[k]}")
    if "default" in s and s["default"] not in (None, [], {}):
        out.append(f"default {json.dumps(s['default'])[:40]}")
    if s.get("examples"):
        out.append(f"e.g. {json.dumps(s['examples'][0])[:60]}")
    return "; ".join(out)


def field_rows(schema: dict) -> list[list[str]]:
    s = SCHEMAS.get(ref_name(schema), schema) if ref_name(schema) else schema
    req = set(s.get("required", []))
    return [[name, type_of(p), "yes" if name in req else "", clean((p.get("description") or p.get("title") or "")), constraints(p)]
            for name, p in (s.get("properties") or {}).items()]


# ============================================================================== sections
def overview(d: Doc):
    d.new_page("Overview")
    d.heading("1. Overview")
    d.para(SPEC["info"].get("description", ""), 8.4)
    d.heading("Servers", 2)
    d.table(["URL", "Description"], [[s["url"], s.get("description", "")] for s in SPEC.get("servers", [])], [0.4, 0.6])
    d.heading("Authentication", 2)
    d.para("Header X-API-Key: <key>. Required on every endpoint except GET /healthz when app.api_key is set (AWS: injected from "
           "Secrets Manager). Locally the key is unset, so calls work without it. Approval callbacks from external systems use "
           "an HMAC signature instead (see Webhooks).")
    d.heading("Conventions", 2)
    d.bullets(["JSON in and out (application/json). Money values are strings with 2 decimals (e.g. \"4433.00\"); never floats.",
               "Strict request schemas: unknown fields are rejected with 422 (e.g. an injected \"approved_by_cfo\").",
               "Idempotency: POST /runs accepts an Idempotency-Key header; repeating it returns the SAME run, never a second one. "
               "Approval decisions carry a callback_id: a repeat replays the stored response with no effect.",
               "Timestamps are ISO-8601 with offset. Ids: run-<12 hex>, apr-<12 hex>.",
               "No real money movement: SUBMIT_DECISION writes to a simulated ERP sandbox table only."])
    d.heading("Errors", 2)
    d.table(["Code", "Meaning", "Body"], [
        ["401", "missing / invalid X-API-Key, or bad webhook signature", '{"detail": "..."}'],
        ["403", "approver not allowed (authority, delegation, segregation of duties)", '{"error": "...", ...}'],
        ["404", "run / approval not found", '{"error": "not found: ..."}'],
        ["409", "conflict: approval already resolved", '{"error": "..."}'],
        ["422", "request validation failed (field, type, pattern, unknown field, too many attachments)", '{"detail": [{"loc", "msg", "type"}]}']],
        [0.07, 0.6, 0.33])
    d.heading("Run statuses and outcome pool", 2)
    d.table(["Run status", "Meaning", "Outcome pool"], [
        ["CREATED / RUNNING", "in progress (checkpointed after every step)", "-"],
        ["AWAITING_APPROVAL", "stopped at the approval gate; approval request created", "AWAITING_APPROVAL"],
        ["NEEDS_INFORMATION", "held or escalated; owner + review date; resume with POST /runs/{id}/evidence", "HELD / ESCALATED"],
        ["COMPLETED", "approved and posted (simulated) or rejected by the approver", "SUBMITTED / REJECTED_BY_APPROVER"],
        ["FAILED", "budget exceeded or unexpected error (reason recorded; nothing posted)", "FAILED"]], [0.2, 0.55, 0.25])
    d.heading("Webhooks", 2)
    from finagent.webhooks import EVENT_TYPES
    d.para("Outbound events are written to a transactional outbox after the step is checkpointed and delivered with retries. "
           "Every delivery is signed: header X-Finagent-Signature: t=<unix>,v1=<hex HMAC-SHA256 of '<t>.<raw body>'> with "
           "webhooks.signing_secret. Inbound approval callbacks (POST /webhooks/inbound/approval) must carry the same header.")
    d.table(["Event type", "When"], [[e, {
        "run.created": "a run was created", "run.awaiting_approval": "run stopped at the approval gate",
        "approval.requested": "approval request with explanation (send to approvers)", "approval.decision_recorded": "one approver slot filled",
        "approval.resolved": "approval APPROVED or REJECTED", "decision.submitted": "simulated ERP posting done",
        "run.completed": "run finished", "run.needs_information": "case held / escalated", "run.failed": "run failed",
        "evidence.received": "new evidence started a new attempt"}.get(e, "")] for e in EVENT_TYPES], [0.3, 0.7])
    d.para("Envelope: {event_type, event_id, run_id, occurred_at, data}.", 8)


def endpoints(d: Doc):
    tags = [t["name"] for t in SPEC.get("tags", [])]
    by_tag: dict[str, list] = {t: [] for t in tags}
    for path, ops in SPEC["paths"].items():
        for method, op in ops.items():
            by_tag.setdefault((op.get("tags") or ["other"])[0], []).append((path, method, op))
    n = 2
    for tag in list(by_tag):
        ops = by_tag[tag]
        if not ops:
            continue
        d.new_page(f"Endpoints: {tag}")
        d.heading(f"{n}. {tag} endpoints")
        desc = next((t.get("description") for t in SPEC.get("tags", []) if t["name"] == tag), "")
        if desc:
            d.para(desc, 8.4, rgb=B.MUTED)
        d.table(["Method", "Path", "Summary"], [[m.upper(), p, o.get("summary", "")] for p, m, o in ops], [0.08, 0.34, 0.58])
        for path, method, op in ops:
            endpoint(d, path, method, op)
        n += 1
    return n


def endpoint(d: Doc, path: str, method: str, op: dict):
    d.need(70)
    d.y += 4
    d.p.rect(LEFT, d.y, 52, 16, METHOD_RGB.get(method, B.GREY_S), None, r=3)
    d.p.text(LEFT + 26, d.y + 11.5, method.upper(), 8.4, True, (1, 1, 1), "center")
    d.p.text(LEFT + 60, d.y + 12, clean(path), 10.4, True, B.INK)
    d.toc.append((f"{method.upper()} {path}", len(d.pages) - 1, 3))
    d.y += 22
    if op.get("summary"):
        d.para(op["summary"], 8.6, True)
    if op.get("description"):
        d.para(op["description"], 8.2, rgb=B.MUTED)
    params = op.get("parameters") or []
    if params:
        d.para("Parameters", 8.4, True, B.BLUE_S, gap=2)
        d.table(["Name", "In", "Type", "Required", "Description / constraints"],
                [[p["name"], p["in"], type_of(p.get("schema", {})), "yes" if p.get("required") else "",
                  "; ".join(x for x in (p.get("description", ""), constraints(p.get("schema", {}))) if x)] for p in params],
                [0.17, 0.08, 0.17, 0.08, 0.5])
    body = (op.get("requestBody") or {}).get("content", {}).get("application/json")
    if body:
        sch = body.get("schema", {})
        d.para(f"Request body: {type_of(sch)}" + (" (required)" if op["requestBody"].get("required") else ""), 8.4, True, B.BLUE_S, gap=2)
        rows = field_rows(sch)
        if rows:
            d.table(["Field", "Type", "Req.", "Description", "Constraints"], rows, [0.16, 0.16, 0.05, 0.37, 0.26])
        ex = body.get("examples") or {}
        if ex:
            names = list(ex)
            d.para(f"{len(names)} example(s) in Swagger: " + "; ".join(clean(ex[k].get("summary", k)) for k in names[:6])
                   + (" ..." if len(names) > 6 else ""), 7.6, rgb=B.MUTED, gap=2)
            first = ex[names[0]].get("value")
            if first is not None:
                txt = json.dumps(first, indent=2)
                d.code(txt[:4000], max_lines=22)
    resp = op.get("responses") or {}
    if resp:
        d.para("Responses", 8.4, True, B.BLUE_S, gap=2)
        rows = []
        for code, r in resp.items():
            sch = (r.get("content", {}).get("application/json") or {}).get("schema", {})
            rows.append([code, r.get("description", ""), type_of(sch) if sch else "-"])
        d.table(["Code", "Description", "Schema"], rows, [0.08, 0.52, 0.40])
    if (method, path) in RETURNS:
        d.para("Returns: " + RETURNS[(method, path)], 7.8)
    err = next((v for k, v in ERRORS.items() if path.startswith(k)), None)
    if err:
        d.para("Errors: " + err, 7.8, rgb=B.MUTED)
    d.y += 6


def appendix(d: Doc, n: int):
    d.new_page("Schemas")
    d.heading(f"{n}. Schemas (data contracts)")
    d.para("Versioned contract files (docs/contracts/*.schema.json): " + ", ".join(f"{t} = {f}" for t, f in CONTRACTS.items()), 7.8)
    d.para(f"{len(SCHEMAS)} schemas. Field tables list type, whether required, description and constraints. Versioned contracts: "
           "finagent.document/1, finagent.corpus/1 (docs/extending.md).", 8.2, rgb=B.MUTED)
    for name in sorted(SCHEMAS):
        s = SCHEMAS[name]
        d.heading(name, 3)
        if s.get("description"):
            d.para(s["description"], 7.8, rgb=B.MUTED, gap=2)
        if s.get("enum"):
            d.para("Enum: " + ", ".join(map(str, s["enum"])), 7.8, gap=4)
            continue
        rows = field_rows(s)
        if rows:
            d.table(["Field", "Type", "Req.", "Description", "Constraints"], rows, [0.16, 0.16, 0.05, 0.37, 0.26], size=6.9)


def cover(d: Doc):
    p = d.pages[0][0]
    p.rect(0, 0, B.W, 150, B.NAVY_S, None, r=0)
    p.text(40, 70, "finagent API reference", 26, True, (1, 1, 1))
    p.text(40, 100, clean(f"{SPEC['info']['title']}  -  version {SPEC['info']['version']}  -  OpenAPI {SPEC.get('openapi')}"),
           10, False, (0.85, 0.9, 1.0))
    p.text(40, 122, f"{sum(len(v) for v in SPEC['paths'].values())} operations, {len(SPEC['paths'])} paths, {len(SCHEMAS)} schemas. "
           "Generated from docs/api/openapi.json (scripts/build_api_pdf.py).", 9, False, (0.85, 0.9, 1.0))
    p.text(40, 176, "Contents", 13, True, B.NAVY_S)
    y, col = 196, 0
    for title, page_i, level in d.toc:
        if level == 3:
            continue
        x = 40 + col * 390
        p.text(x + (0 if level == 1 else 14), y, clean(title), 9.4 if level == 1 else 8.4, level == 1, B.INK)
        p.text(x + 360, y, str(page_i + 1), 8.4, False, B.MUTED, "right")
        y += 15 if level == 1 else 12
        if y > 560:
            y, col = 196, col + 1
    p.text(40, 575, "Live, interactive version: http://localhost:8080/docs (Swagger). Testing guide: docs/api/swagger_testing.md. "
           "No real money movement - posting is simulated.", 8, False, B.MUTED)


def main():
    d = Doc()
    d.new_page("")          # cover (filled at the end with the table of contents)
    overview(d)
    n = endpoints(d)
    appendix(d, n)
    cover(d)
    total = len(d.pages)
    pages = []
    for i, (pg, section) in enumerate(d.pages):
        if i:
            hdr = B.Page()
            hdr.header(clean(f"finagent API - {section}"), "", i + 1, total)
            pg.ops = hdr.ops + pg.ops
        pages.append(pg)
    out = ROOT / "docs" / "api" / "finagent_api_reference.pdf"
    out.write_bytes(B.build(pages))
    print(f"wrote {out} ({total} pages, {sum(len(v) for v in SPEC['paths'].values())} operations)")


if __name__ == "__main__":
    main()
