#!/usr/bin/env python3
"""Approval inbox: an easy way to test the approval-required webhook flow end to end.

It plays the role of an external approval system (ServiceNow / Slack / e-mail gateway):
  1. subscribes to finagent webhooks (approval.requested, approval.resolved, decision.submitted, run.* ...)
  2. receives each event, verifies its HMAC signature and prints it readably
  3. for approval.requested: shows amount, outcome, exceptions, required approvals and SUGGESTED approvers
  4. sends the decision back through the SIGNED inbound webhook  POST /webhooks/inbound/approval
  5. the run resumes; decision.submitted / run.completed arrive back here

Usage (service must be running: make start / make up):
  python scripts/dev/approval_inbox.py                     # interactive: you approve / reject each request
  python scripts/dev/approval_inbox.py --auto approve      # approve every request automatically (fills every slot)
  python scripts/dev/approval_inbox.py --auto reject
  python scripts/dev/approval_inbox.py --demo              # self-contained demo: auto-approve + load sample cases, then exit
  python scripts/dev/approval_inbox.py --demo FIN-012 FIN-018 --duplicate   # also re-send every callback (replay test)
Service in Docker? add  --public-url http://host.docker.internal:9099/hooks
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import queue
import sys
import threading
import time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]
EVENTS = ["approval.requested", "approval.decision_recorded", "approval.resolved", "decision.submitted",
          "run.completed", "run.needs_information", "run.failed"]
C = {"b": "\033[1m", "g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "c": "\033[36m", "d": "\033[2m", "x": "\033[0m"}
if os.name == "nt" or not sys.stdout.isatty():
    C = {k: "" for k in C}


def signing_secret() -> str:
    if os.environ.get("FINAGENT__WEBHOOKS__SIGNING_SECRET"):
        return os.environ["FINAGENT__WEBHOOKS__SIGNING_SECRET"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line.startswith("FINAGENT__WEBHOOKS__SIGNING_SECRET="):
                return line.split("=", 1)[1].strip()
    return str(yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())["webhooks"]["signing_secret"])


def sign(secret: str, body: bytes) -> str:
    ts = int(time.time())
    return f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, header: str | None) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in (header or "").split(","))
        mac = hmac.new(secret.encode(), f"{parts['t']}.".encode() + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(mac, parts["v1"])
    except (KeyError, ValueError):
        return False


def suggest_approvers(expl: dict) -> list[tuple[str, str]]:
    """Pick test approvers that satisfy each required slot (lowest sufficient limit, respecting SoD)."""
    reg = json.loads((ROOT / "mock_data" / "authority_register.json").read_text())
    cons = expl.get("constraints") or {}
    blocked = set((cons.get("excluded_approvers") or {}).keys()) | {cons.get("requester_id"), cons.get("po_requester_id")}
    if cons.get("distinct_roles_required"):
        blocked |= set(cons.get("receipters") or [])
    users = [u for u in reg["approvers"] if u.get("active", True) and u["user_id"] not in blocked]
    lim = lambda u: Decimal("Infinity") if u["limit_aud"] is None else Decimal(u["limit_aud"])
    picks, used = [], set()
    slots = sorted(expl.get("required_approvals") or [], key=lambda s: s.get("requires_flag") is None)
    for s in slots:
        if s.get("requires_flag"):
            cands = sorted([u for u in users if s["requires_flag"] in u.get("flags", [])], key=lim)
        else:
            need = Decimal(s.get("min_limit_aud") or "0.01")
            cands = sorted([u for u in users if lim(u) >= need and lim(u) > 0 and not u.get("flags")], key=lim)
        cands = [u for u in cands if u["user_id"] not in used]
        if cands:
            picks.append((s["slot"], cands[0]["user_id"]))
            used.add(cands[0]["user_id"])
    return picks


class Inbox:
    def __init__(self, a):
        self.a = a
        self.api = a.api.rstrip("/")
        self.secret = signing_secret()
        self.http = httpx.Client(base_url=self.api, timeout=60,
                                 headers={"X-API-Key": os.environ["API_KEY"]} if os.environ.get("API_KEY") else {})
        self.requests: queue.Queue = queue.Queue()
        self.sub_id = None
        self.terminal: dict[str, str] = {}

    # ------------------------------------------------------------ receiving
    def handle(self, event: str, body: bytes, sig: str | None):
        ok = verify(self.secret, body, sig)
        msg = json.loads(body)
        data, rid = msg.get("data", {}), msg.get("run_id")
        tag = f"{C['g']}signature ok{C['x']}" if ok else f"{C['r']}SIGNATURE INVALID{C['x']}"
        print(f"\n{C['c']}<- {event}{C['x']}  run {rid}  ({tag})")
        if event == "approval.requested":
            self.show_request(data)
            self.requests.put(data)
        elif event == "approval.decision_recorded":
            print(f"   slot {data['slot']}: {data['decision']} -> approval {data['status']}")
        elif event == "approval.resolved":
            print(f"   approval {data['approval_id']} {C['b']}{data['status']}{C['x']}")
        elif event == "decision.submitted":
            print(f"   {C['g']}posted (simulated): {data['posting_ref']} [{data['decision']}, {data['status']}]{C['x']}")
        elif event in ("run.completed", "run.needs_information", "run.failed"):
            print(f"   case {data.get('case_id')} -> {C['b']}{data.get('outcome') or data.get('reason')}{C['x']}"
                  + (f"   next: {data.get('next_action')}" if data.get("next_action") else ""))
            self.terminal[rid] = event

    def show_request(self, d: dict):
        e = d["explanation"]
        print(f"   {C['b']}APPROVAL REQUEST {d['approval_id']}{C['x']}  case {e['case_id']}  vendor {e['vendor_id']}  "
              f"invoice {e['invoice_ref']}  {e['currency']} {e['amount']} (AUD {e['amount_aud']})")
        print(f"   proposed: {C['y']}{e['proposed_outcome']}{C['x']}  confidence {e['confidence']}  - {e['what_approval_does']}")
        print(f"   {C['d']}{e['summary'][:300]}{C['x']}")
        for x in e.get("exceptions", [])[:4]:
            print(f"   ! {x['category']}: {x['failed_rule']} (observed {x['observed']})")
        for s in e.get("required_approvals", []):
            print(f"   needs: [{s['slot']}] {s['description']}")
        print(f"   cites: {', '.join(e.get('citations', [])[:5])}{' ...' if len(e.get('citations', [])) > 5 else ''}")
        print(f"   suggested approvers: {', '.join(f'{slot}={u}' for slot, u in suggest_approvers(e)) or 'none found'}")

    # ------------------------------------------------------------ deciding
    def send(self, approval_id: str, decision: str, approver: str, slot: str):
        body = json.dumps({"approval_id": approval_id, "decision": decision, "approver_id": approver,
                           "comment": f"approval inbox ({slot})", "callback_id": f"inbox-{approval_id}-{approver}"}).encode()
        for attempt in range(2 if self.a.duplicate else 1):
            r = self.http.post("/webhooks/inbound/approval", content=body,
                               headers={"Content-Type": "application/json", "X-Finagent-Signature": sign(self.secret, body)})
            j = r.json()
            label = "duplicate re-send" if attempt else "sent"
            print(f"{C['c']}-> {decision} as {approver} [{slot}] ({label}){C['x']}  HTTP {r.status_code}  "
                  f"status={j.get('status')} effect={j.get('effect')} replayed={j.get('replayed')}"
                  + (f"  denied: {j['denied_reasons']}" if j.get("denied_reasons") else ""))

    def decide(self, d: dict):
        picks = suggest_approvers(d["explanation"])
        mode = self.a.auto
        if mode is None:
            ans = input(f"\n{C['b']}[a]pprove (suggested approvers) / [r]eject / [s]kip / or type an approver id: {C['x']}").strip()
            if ans in ("s", ""):
                print("   skipped - still pending (decide later in Swagger or CLI)")
                return
            if ans == "r":
                mode = "reject"
            elif ans == "a":
                mode = "approve"
            else:
                return self.send(d["approval_id"], "APPROVE", ans, "manual")
        if mode == "reject":
            return self.send(d["approval_id"], "REJECT", picks[0][1] if picks else "u-ccm-01", "reject")
        for slot, user in picks:
            self.send(d["approval_id"], "APPROVE", user, slot)
            time.sleep(0.2)

    # ------------------------------------------------------------ lifecycle
    def serve(self):
        inbox = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(200); self.end_headers()        # acknowledge first, process after
                threading.Thread(target=inbox.handle, args=(self.headers.get("X-Finagent-Event"), body,
                                                            self.headers.get("X-Finagent-Signature")), daemon=True).start()

            def log_message(self, *args):
                pass
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", self.a.port), H)
        except OSError:
            srv = ThreadingHTTPServer(("0.0.0.0", 0), H)          # port busy: take any free port
            print(f"port {self.a.port} is busy - using {srv.server_address[1]} instead")
        self.a.port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = self.a.public_url or f"http://127.0.0.1:{self.a.port}/hooks"
        r = self.http.post("/webhooks/subscriptions", json={"url": url, "events": EVENTS})
        r.raise_for_status()
        self.sub_id = r.json()["subscription_id"]
        print(f"{C['b']}approval inbox{C['x']} listening on :{self.a.port}  subscribed {self.sub_id} -> {url}")
        print(f"mode: {self.a.auto or 'interactive'}   decisions go back via signed POST {self.api}/webhooks/inbound/approval")

    def close(self):
        if self.sub_id:
            self.http.delete(f"/webhooks/subscriptions/{self.sub_id}")
            print(f"\nunsubscribed {self.sub_id}")

    def demo(self, cases: list[str]):
        req_dir = ROOT / "mock_data" / "cases"
        run_ids = []
        for cid in cases:
            print(f"\n{C['b']}=== start {cid} ==={C['x']}")
            r = self.http.post("/runs", json=json.loads((req_dir / cid / "request.json").read_text()))
            run_ids.append(r.json()["run_id"])
            deadline = time.time() + 30
            while time.time() < deadline:
                try:
                    self.decide(self.requests.get(timeout=1.5))
                except queue.Empty:
                    if r.json()["status"] != "AWAITING_APPROVAL" or self.terminal.get(r.json()["run_id"]):
                        break
            time.sleep(1)
        print(f"\n{C['b']}=== final state ==={C['x']}")
        for rid in run_ids:
            g = self.http.get(f"/runs/{rid}", params={"include_audit": False}).json()
            rec = (g.get("result") or {}).get("recommendation") or {}
            sub = (g.get("result") or {}).get("submission") or {}
            print(f"{g['case_id']:<9}{g['status']:<20}{rec.get('outcome', '-'):<26}{sub.get('posting_ref', '-')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=os.environ.get("API", "http://127.0.0.1:8080"))
    ap.add_argument("--port", type=int, default=9099)
    ap.add_argument("--public-url", help="URL the service uses to reach this inbox (Docker: http://host.docker.internal:9099/hooks)")
    ap.add_argument("--auto", choices=["approve", "reject"])
    ap.add_argument("--duplicate", action="store_true", help="send every callback twice to demonstrate replay safety")
    ap.add_argument("--demo", nargs="*", metavar="CASE", help="auto-approve and load cases (default FIN-001 FIN-012 FIN-002 FIN-003)")
    a = ap.parse_args()
    if a.demo is not None and a.auto is None:
        a.auto = "approve"
    inbox = Inbox(a)
    try:
        inbox.http.get("/healthz").raise_for_status()
    except httpx.HTTPError:
        sys.exit(f"finagent API not reachable at {a.api} - start it first (make start / make up)")
    inbox.serve()
    try:
        if a.demo is not None:
            inbox.demo(a.demo or ["FIN-001", "FIN-012", "FIN-002", "FIN-003"])
        else:
            print("waiting for approval requests... start a case (make load CASES=FIN-001, Swagger POST /runs). Ctrl+C to stop.")
            while True:
                inbox.decide(inbox.requests.get())
    except KeyboardInterrupt:
        pass
    finally:
        inbox.close()


if __name__ == "__main__":
    main()
