"""Outbound webhooks (status notifications) and inbound approval callbacks.

Outbound: every lifecycle event is written to ``webhook_outbox`` (transactional
outbox) for each matching subscription and delivered with an HMAC-SHA256
signature header::

    X-Finagent-Event: approval.requested
    X-Finagent-Delivery: dlv-...
    X-Finagent-Signature: t=<unix>,v1=<hex hmac_sha256(secret, f"{t}.{body}")>

Failed deliveries are retried by ``finagent webhooks deliver`` / the
``POST /webhooks/deliver`` endpoint (or a scheduled worker in AWS), with
exponential attempts up to ``webhooks.max_delivery_attempts`` -> DEAD.

Inbound: ``POST /webhooks/inbound/approval`` accepts the same signature scheme
so an external approval system (ServiceNow, Slack workflow, e-mail gateway)
can resume a run.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import httpx

EVENT_TYPES = [
    "run.created", "run.awaiting_approval", "approval.requested", "approval.decision_recorded", "approval.resolved",
    "decision.submitted", "run.completed", "run.needs_information", "run.failed", "evidence.received",

]


def sign(secret: str, body: bytes, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify(secret: str, body: bytes, header: str | None, tolerance_s: int = 300) -> bool:
    if not header:
        return False
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs(time.time() - ts) > tolerance_s:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts.get("v1", ""))


class Webhooks:
    def __init__(self, settings, store, audit, clock):
        self.settings, self.store, self.audit, self.clock = settings, store, audit, clock

    @property
    def secret(self) -> str:
        return str(self.settings.get("webhooks.signing_secret") or "")

    # -- subscriptions -----------------------------------------------------------
    def subscribe(self, url: str, events: list[str]) -> dict:
        bad = [e for e in events if e != "*" and e not in EVENT_TYPES]
        if bad:
            raise ValueError(f"unknown event types: {bad}")
        sid = f"sub-{uuid.uuid4().hex[:10]}"
        with self.store.tx() as conn:
            conn.execute("INSERT INTO webhook_subscriptions(subscription_id, url, events, active, created_at) VALUES (?,?,?,?,?)",
                         (sid, url, json.dumps(events), 1, self.clock().isoformat()))
        self.audit.record("webhook.subscribed", details={"subscription_id": sid, "url": url, "events": events})
        return {"subscription_id": sid, "url": url, "events": events, "active": True}

    def unsubscribe(self, sid: str) -> bool:
        with self.store.tx() as conn:
            n = conn.execute("UPDATE webhook_subscriptions SET active=0 WHERE subscription_id=?", (sid,)).rowcount
        self.audit.record("webhook.unsubscribed", details={"subscription_id": sid})
        return bool(n)

    def subscriptions(self) -> list[dict]:
        rows = self.store.fetchall("SELECT * FROM webhook_subscriptions WHERE active=1")
        for r in rows:
            r["events"] = json.loads(r["events"])
        return rows

    # -- emit ----------------------------------------------------------------------
    def emit(self, event_type: str, run_id: str | None, data: dict) -> list[str]:
        envelope = {"event_type": event_type, "event_id": f"whe-{uuid.uuid4().hex[:12]}", "run_id": run_id,
                    "occurred_at": self.clock().isoformat(), "data": data}
        ids = []
        subs = [s for s in self.subscriptions() if "*" in s["events"] or event_type in s["events"]]
        with self.store.tx() as conn:
            # a subscription-less row is always written so the event feed is queryable
            for sub in subs or [None]:
                did = f"dlv-{uuid.uuid4().hex[:12]}"
                conn.execute("""INSERT INTO webhook_outbox(delivery_id, subscription_id, event_type, run_id, payload, status,
                                created_at) VALUES (?,?,?,?,?,?,?)""",
                             (did, sub["subscription_id"] if sub else None, event_type, run_id, json.dumps(envelope, default=str),
                              "PENDING" if sub else "NO_SUBSCRIBER", self.clock().isoformat()))
                if sub:
                    ids.append(did)
        self.audit.record("webhook.enqueued", run_id=run_id, outcome="ok",
                          details={"event_type": event_type, "deliveries": ids})
        if ids and self.settings.get("webhooks.deliver_inline", True):
            self.deliver_pending(only_ids=ids)
        return ids

    def deliver_pending(self, only_ids: list[str] | None = None) -> dict:
        max_attempts = int(self.settings.get("webhooks.max_delivery_attempts", 5))
        rows = self.store.fetchall("""SELECT o.*, s.url FROM webhook_outbox o JOIN webhook_subscriptions s
                                      ON o.subscription_id = s.subscription_id
                                      WHERE o.status IN ('PENDING','FAILED') AND s.active=1""")
        if only_ids:
            rows = [r for r in rows if r["delivery_id"] in only_ids]
        stats = {"delivered": 0, "failed": 0, "dead": 0}
        for r in rows:
            body = r["payload"].encode()
            headers = {"Content-Type": "application/json", "X-Finagent-Event": r["event_type"],
                       "X-Finagent-Delivery": r["delivery_id"], "X-Finagent-Signature": sign(self.secret, body)}
            t0 = time.perf_counter()
            try:
                resp = httpx.post(r["url"], content=body, headers=headers,
                                  timeout=float(self.settings.get("webhooks.delivery_timeout_s", 2.0)))
                ok, err = 200 <= resp.status_code < 300, f"HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                ok, err = False, f"{type(exc).__name__}"
            attempts = r["attempts"] + 1
            status = "DELIVERED" if ok else ("DEAD" if attempts >= max_attempts else "FAILED")
            with self.store.tx() as conn:
                conn.execute("UPDATE webhook_outbox SET status=?, attempts=?, last_error=?, delivered_at=? WHERE delivery_id=?",
                             (status, attempts, None if ok else err, self.clock().isoformat() if ok else None, r["delivery_id"]))
            self.audit.record("webhook.delivery", run_id=r["run_id"], outcome=status.lower(), attempt=attempts,
                              duration_ms=int((time.perf_counter() - t0) * 1000),
                              details={"delivery_id": r["delivery_id"], "event_type": r["event_type"], "error": None if ok else err})
            stats["delivered" if ok else ("dead" if status == "DEAD" else "failed")] += 1
        return stats

    def feed(self, run_id: str | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT delivery_id, subscription_id, event_type, run_id, status, attempts, created_at, payload FROM webhook_outbox"
        params: tuple = ()
        if run_id:
            sql += " WHERE run_id=?"; params = (run_id,)
        rows = self.store.fetchall(sql + " ORDER BY created_at LIMIT ?", params + (limit,))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows
