#!/usr/bin/env python3
"""API smoke test: call EVERY operation in the live OpenAPI spec against a running finagent (local or Docker), using
the Swagger "Try it out" examples from /openapi.json where an operation has a body. Standard library only - any OS.

    python scripts/dev/api_smoke.py                    (make api-smoke / .\\dev.ps1 api-smoke)
    API=http://127.0.0.1:8080 API_KEY=... SIGNING_SECRET=... python scripts/dev/api_smoke.py

Fails (exit 1) if any call returns an unexpected status, or if any operation in the spec was not called - so a new
endpoint cannot be added without being exercised here.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

API = os.environ.get("API", "http://127.0.0.1:8080").rstrip("/")
KEY = os.environ.get("API_KEY")


def _secret() -> str:
    """Same source as the service: SIGNING_SECRET env, else .env (FINAGENT__WEBHOOKS__SIGNING_SECRET), else settings."""
    if os.environ.get("SIGNING_SECRET"):
        return os.environ["SIGNING_SECRET"]
    env = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            k, _, v = line.strip().partition("=")
            if k == "FINAGENT__WEBHOOKS__SIGNING_SECRET" and v:
                return v.split("#")[0].strip()
    return "dev-only-change-me"


SECRET = _secret()
results: list[tuple[str, str, int, str]] = []
covered: set[tuple[str, str]] = set()


def http(method: str, path: str, body=None, headers=None, raw: bytes | None = None, timeout=600):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": "application/json", **({"x-api-key": KEY} if KEY else {}), **(headers or {})}
    req = urllib.request.Request(API + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if "json" in (r.headers.get("content-type") or "") and txt else txt)
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except ValueError:
            return e.code, txt


def call(method, template, expect, path=None, **kw):
    """Call one operation; `template` is its OpenAPI path, used for the coverage check."""
    covered.add((method, template))
    code, body = http(method, path or template, **kw)
    ok = code in expect
    results.append(("PASS" if ok else "FAIL", f"{method} {template}", code, "" if ok else str(body)[:160]))
    return code, body


def example(spec, template, method, name_prefix):
    ex = spec["paths"][template][method.lower()]["requestBody"]["content"]["application/json"]["examples"]
    return next(v["value"] for k, v in ex.items() if k.startswith(name_prefix))


def main() -> None:
    t0 = time.time()
    code, _ = http("GET", "/docs")
    results.append(("PASS" if code == 200 else "FAIL", "GET /docs (Swagger UI)", code, ""))
    code, spec = http("GET", "/openapi.json")
    if code != 200:
        sys.exit(f"cannot read {API}/openapi.json ({code}) - is the service running?")
    ops = {(m.upper(), p) for p, v in spec["paths"].items() for m in v if m in ("get", "post", "put", "patch", "delete")}

    call("GET", "/healthz", {200})
    # --- a case that needs approval (FIN-001), started from its Swagger example, with an Idempotency-Key replay
    fin001 = example(spec, "/runs", "POST", "FIN-001")
    idem = f"smoke-{uuid.uuid4().hex[:8]}"
    _, run = call("POST", "/runs", {201}, body=fin001, headers={"Idempotency-Key": idem})
    rid, aid = run["run_id"], (run.get("approval") or {}).get("approval_id")
    _, again = http("POST", "/runs", body=fin001, headers={"Idempotency-Key": idem})
    results.append(("PASS" if again.get("run_id") == rid else "FAIL", "POST /runs (Idempotency-Key replay)", 0, ""))
    call("GET", "/runs", {200}, path="/runs?limit=5")
    call("GET", "/runs/{run_id}", {200}, path=f"/runs/{rid}")
    call("GET", "/runs/{run_id}/reasoning", {200}, path=f"/runs/{rid}/reasoning")
    call("GET", "/runs/{run_id}/diagram", {200}, path=f"/runs/{rid}/diagram?format=svg")
    call("GET", "/runs/{run_id}/audit", {200}, path=f"/runs/{rid}/audit")
    call("GET", "/approvals", {200}, path="/approvals?status=PENDING")
    if aid:
        call("GET", "/approvals/{approval_id}", {200}, path=f"/approvals/{aid}")
        # Swagger example: a Cost Centre Manager is below FIN-001's limit -> 403 (correctly refused)
        call("POST", "/approvals/{approval_id}/decision", {403, 200}, path=f"/approvals/{aid}/decision",
             body=example(spec, "/approvals/{approval_id}/decision", "POST", "approve - Cost Centre Manager"))
        # the Department Director approves through the SIGNED inbound webhook -> run completes, posted once
        raw = json.dumps({"approval_id": aid, "decision": "APPROVE", "approver_id": "u-dd-01",
                          "comment": "smoke test", "callback_id": f"cb-{uuid.uuid4().hex[:8]}"}).encode()
        ts = int(time.time())
        sig = f"t={ts},v1=" + hmac.new(SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
        call("POST", "/webhooks/inbound/approval", {200}, raw=raw, headers={"x-finagent-signature": sig})
    else:
        results.append(("FAIL", "FIN-001 did not request approval", 0, run.get("status")))
    call("POST", "/runs/{run_id}/resume", {200, 409}, path=f"/runs/{rid}/resume")
    call("GET", "/outcomes", {200})
    call("GET", "/outcomes/summary", {200})
    call("GET", "/outcomes/{run_id}", {200}, path=f"/outcomes/{rid}")

    # --- a held case (FIN-004) resumed with evidence (Swagger example "supply PO")
    _, held = call("POST", "/runs", {201}, body=example(spec, "/runs", "POST", "FIN-004"))
    call("POST", "/runs/{run_id}/evidence", {200, 201}, path=f"/runs/{held['run_id']}/evidence",
         body=example(spec, "/runs/{run_id}/evidence", "POST", "supply PO"))
    # --- a reviewer decision on a held case (FIN-003); 409/422 = correctly refused when no question is open
    _, held3 = http("POST", "/runs", body=example(spec, "/runs", "POST", "FIN-003"))
    call("POST", "/runs/{run_id}/review", {200, 201, 409, 422}, path=f"/runs/{held3['run_id']}/review",
         body={"reviewer_id": "u-fc-01", "skill": "instruction_screen", "question": "payment_route_change",
               "decision": "concern", "reason": "smoke test: reviewer confirms the concern"})

    # --- webhooks
    _, sub = call("POST", "/webhooks/subscriptions", {200, 201},
                  body=example(spec, "/webhooks/subscriptions", "POST", "approvals only"))
    call("GET", "/webhooks/subscriptions", {200})
    if isinstance(sub, dict) and sub.get("subscription_id"):
        call("DELETE", "/webhooks/subscriptions/{subscription_id}", {200, 204},
             path=f"/webhooks/subscriptions/{sub['subscription_id']}")
    call("GET", "/webhooks/events", {200})
    call("GET", "/webhooks/event-types", {200})
    call("POST", "/webhooks/deliver", {200})

    # --- audit, evaluation, skills, policy, catalog
    call("GET", "/audit/verify", {200})
    _, ev = call("POST", "/evaluations/run", {200}, body=example(spec, "/evaluations/run", "POST", "required cases"))
    if isinstance(ev, dict):
        sm = ev.get("summary") or {}
        results.append(("PASS" if sm.get("failed") == 0 and sm.get("passed") else "FAIL", "evaluation: required cases pass",
                        0, f"{sm.get('passed')} passed, {sm.get('failed')} failed"))
    call("GET", "/evaluations", {200})
    call("GET", "/evaluations/latest", {200})
    call("POST", "/skills/{name}/test", {200}, path="/skills/three_way_match/test")
    call("GET", "/policy/status", {200})
    call("GET", "/policy/parameters", {200})
    for p in ("tools", "skills", "parsers", "workflow", "contracts"):
        call("GET", f"/catalog/{p}", {200})

    missing = sorted(ops - covered)
    for m in missing:
        results.append(("FAIL", f"{m[0]} {m[1]}", 0, "operation in the spec was not exercised"))
    for status, op, code, note in results:
        print(f"{status}  {op:<46} {code or '':<4} {note}")
    fails = sum(r[0] == "FAIL" for r in results)
    print(f"\n{len(covered & ops)}/{len(ops)} operations exercised, {len(results) - fails}/{len(results)} checks passed "
          f"in {time.time() - t0:.0f}s against {API}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
