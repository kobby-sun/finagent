"""Append-only, hash-chained audit trail.

Every run, retrieval, tool, model, approval, webhook and API event is written
both to SQLite (queryable per run) and to a JSONL file (shippable to a SIEM /
CloudWatch). Each event carries ``prev_hash`` and ``hash`` so tampering or
deletion is detectable with ``finagent audit verify``.
Details are always passed through :func:`finagent.redaction.redact`.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from .redaction import redact
from .store import Store

GENESIS = "0" * 64
_lock = threading.Lock()


def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)


class AuditLog:
    def __init__(self, store: Store, jsonl_path: Path | None, clock):
        self.store = store
        self.jsonl_path = jsonl_path
        self.clock = clock

    def record(self, event_type: str, *, run_id: str | None = None, correlation_id: str | None = None,
               node: str | None = None, tool: str | None = None, outcome: str | None = None,
               duration_ms: int | None = None, attempt: int | None = None, actor: str = "system",
               details: Optional[dict[str, Any]] = None) -> dict:
        safe_details = redact(details or {})
        with _lock, self.store.tx() as conn:
            prev = conn.execute("SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = prev["hash"] if prev else GENESIS
            event = {
                "event_id": f"evt-{uuid.uuid4().hex[:16]}",
                "run_id": run_id,
                "correlation_id": correlation_id or run_id,
                "ts": self.clock().isoformat(),
                "event_type": event_type,
                "node": node,
                "tool": tool,
                "outcome": outcome,
                "duration_ms": duration_ms,
                "attempt": attempt,
                "actor": actor,
                "details": safe_details,
            }
            digest = hashlib.sha256((prev_hash + _canonical(event)).encode()).hexdigest()
            conn.execute(
                """INSERT INTO audit_events(event_id, run_id, correlation_id, ts, event_type, node, tool, outcome,
                   duration_ms, attempt, actor, details, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event["event_id"], run_id, event["correlation_id"], event["ts"], event_type, node, tool, outcome,
                 duration_ms, attempt, actor, _canonical(safe_details), prev_hash, digest),
            )
        event.update(prev_hash=prev_hash, hash=digest)
        if self.jsonl_path:
            with _lock, open(self.jsonl_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, default=str) + "\n")
        return event

    @contextmanager
    def timed(self, event_type: str, **kw) -> Iterator[dict]:
        """Record ``<event_type>`` with duration; callers may set ctx['outcome'] / ctx['details']."""
        ctx: dict[str, Any] = {"outcome": "ok", "details": kw.pop("details", {}) or {}}
        start = time.perf_counter()
        try:
            yield ctx
        except BaseException as exc:
            ctx["outcome"] = "error"
            ctx["details"] = {**ctx["details"], "error": f"{type(exc).__name__}: {exc}"}
            raise
        finally:
            self.record(event_type, duration_ms=int((time.perf_counter() - start) * 1000),
                        outcome=ctx["outcome"], details=ctx["details"], **kw)

    def events(self, run_id: str) -> list[dict]:
        rows = self.store.fetchall("SELECT * FROM audit_events WHERE run_id=? ORDER BY seq", (run_id,))
        for r in rows:
            r["details"] = json.loads(r["details"])
        return rows

    def verify_chain(self) -> dict:
        rows = self.store.fetchall("SELECT * FROM audit_events ORDER BY seq")
        prev_hash = GENESIS
        for r in rows:
            event = {k: r[k] for k in ("event_id", "run_id", "correlation_id", "ts", "event_type", "node", "tool",
                                       "outcome", "duration_ms", "attempt", "actor")}
            event["details"] = json.loads(r["details"])
            if r["prev_hash"] != prev_hash:
                return {"valid": False, "events": len(rows), "broken_at_seq": r["seq"], "reason": "prev_hash mismatch"}
            digest = hashlib.sha256((prev_hash + _canonical(event)).encode()).hexdigest()
            if digest != r["hash"]:
                return {"valid": False, "events": len(rows), "broken_at_seq": r["seq"], "reason": "hash mismatch"}
            prev_hash = digest
        return {"valid": True, "events": len(rows), "head": prev_hash}
