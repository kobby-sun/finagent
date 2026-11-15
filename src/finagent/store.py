"""SQLite persistence: runs + checkpoints, approvals, callbacks, outcome pool,
webhook subscriptions/outbox, audit events and the simulated ERP ledger.

SQLite (WAL mode) is deliberately simple; every write that must be atomic uses
``BEGIN IMMEDIATE`` so concurrent approval callbacks serialise. The schema maps
1:1 onto Postgres/DynamoDB for production (see docs/architecture.md).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    request_idempotency_key TEXT UNIQUE,
    status TEXT NOT NULL,
    current_node TEXT,
    request_json TEXT NOT NULL,
    state_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    config_manifest TEXT,
    index_version TEXT,
    parent_run_id TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_runs_case ON runs(case_id);
CREATE INDEX IF NOT EXISTS ix_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS checkpoints (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    node TEXT NOT NULL,
    next_node TEXT,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    case_id TEXT NOT NULL,
    status TEXT NOT NULL,              -- PENDING | APPROVED | REJECTED
    decision_type TEXT NOT NULL,       -- POST | REJECT
    proposed_outcome TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    amount_aud TEXT NOT NULL,
    required_slots TEXT NOT NULL,
    decisions TEXT NOT NULL,
    explanation TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS approval_callbacks (
    callback_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    response_json TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outcomes (
    run_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    outcome TEXT,
    pool_status TEXT NOT NULL,         -- AWAITING_APPROVAL | SUBMITTED | HELD | ESCALATED | REJECTED_BY_APPROVER | FAILED | SUPERSEDED
    primary_exception TEXT,
    amount TEXT,
    currency TEXT,
    summary TEXT,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_outcomes_status ON outcomes(pool_status);

CREATE TABLE IF NOT EXISTS audit_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    run_id TEXT,
    correlation_id TEXT,
    ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    node TEXT,
    tool TEXT,
    outcome TEXT,
    duration_ms INTEGER,
    attempt INTEGER,
    actor TEXT,
    details TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_run ON audit_events(run_id);

CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    subscription_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    events TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_outbox (
    delivery_id TEXT PRIMARY KEY,
    subscription_id TEXT,
    event_type TEXT NOT NULL,
    run_id TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,              -- PENDING | DELIVERED | FAILED | DEAD
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT
);

-- Simulated ERP / posting system. Lives in the same file for convenience but is
-- only ever touched through the submit_finance_decision tool.
CREATE TABLE IF NOT EXISTS erp_decisions (
    idempotency_key TEXT PRIMARY KEY,
    posting_ref TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    payload TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_results (
    eval_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    summary TEXT NOT NULL,
    results TEXT NOT NULL
);
"""


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Store:
    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        self._local = threading.local()
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # One connection per thread; FastAPI runs sync handlers in a thread pool.
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        yield self._conn()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Serialised write transaction (BEGIN IMMEDIATE)."""
        conn = self._conn()
        if conn.in_transaction:  # nested use: join the outer transaction
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    # -- generic helpers ---------------------------------------------------
    def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        row = self._conn().execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self._conn().execute(sql, params).fetchall()]

    # -- runs ----------------------------------------------------------------
    def create_run(self, *, run_id: str, case_id: str, request: dict, state: dict, now: str,
                   idempotency_key: str | None, config_manifest: dict, index_version: str,
                   parent_run_id: str | None = None, attempt: int = 1) -> dict:
        with self.tx() as conn:
            if idempotency_key:
                existing = conn.execute("SELECT run_id FROM runs WHERE request_idempotency_key=?",
                                        (idempotency_key,)).fetchone()
                if existing:
                    return {"run_id": existing["run_id"], "created": False}
            conn.execute(
                """INSERT INTO runs(run_id, case_id, request_idempotency_key, status, current_node, request_json,
                   state_json, config_manifest, index_version, parent_run_id, attempt, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, case_id, idempotency_key, "CREATED", "RECEIVE", json.dumps(request), json.dumps(state),
                 json.dumps(config_manifest), index_version, parent_run_id, attempt, now, now),
            )
        return {"run_id": run_id, "created": True}

    def get_run(self, run_id: str) -> Optional[dict]:
        row = self.fetchone("SELECT * FROM runs WHERE run_id=?", (run_id,))
        if not row:
            return None
        for key in ("request_json", "state_json", "result_json", "config_manifest"):
            if row.get(key):
                row[key] = json.loads(row[key])
        return row

    def list_runs(self, status: str | None = None, case_id: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT run_id, case_id, status, current_node, parent_run_id, attempt, created_at, updated_at FROM runs"
        clauses, params = [], []
        if status:
            clauses.append("status=?"); params.append(status)
        if case_id:
            clauses.append("case_id=?"); params.append(case_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return self.fetchall(sql, tuple(params))

    def checkpoint(self, run_id: str, node: str, next_node: str | None, status: str, state: dict, now: str,
                   result: dict | None = None, error: str | None = None) -> None:
        with self.tx() as conn:
            seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM checkpoints WHERE run_id=?", (run_id,)).fetchone()[0]
            conn.execute("INSERT INTO checkpoints(run_id, seq, node, next_node, state_json, created_at) VALUES (?,?,?,?,?,?)",
                         (run_id, seq, node, next_node, json.dumps(state), now))
            conn.execute(
                """UPDATE runs SET status=?, current_node=?, state_json=?, result_json=COALESCE(?, result_json),
                   error=?, version=version+1, updated_at=? WHERE run_id=?""",
                (status, next_node, json.dumps(state), json.dumps(result) if result is not None else None,
                 error, now, run_id),
            )

    def set_run_status(self, run_id: str, status: str, now: str, error: str | None = None) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE runs SET status=?, error=COALESCE(?, error), version=version+1, updated_at=? WHERE run_id=?",
                         (status, error, now, run_id))

    def claim_run(self, run_id: str, expected_statuses: tuple[str, ...], now: str) -> bool:
        """Atomically move a run to RUNNING if it is in one of ``expected_statuses``.

        Prevents two workers (or a duplicate callback) from resuming the same run.
        """
        with self.tx() as conn:
            row = conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not row or row["status"] not in expected_statuses:
                return False
            conn.execute("UPDATE runs SET status='RUNNING', version=version+1, updated_at=? WHERE run_id=?", (now, run_id))
            return True

    def checkpoints(self, run_id: str) -> list[dict]:
        rows = self.fetchall("SELECT seq, node, next_node, created_at FROM checkpoints WHERE run_id=? ORDER BY seq", (run_id,))
        return rows

    # -- approvals -----------------------------------------------------------
    def create_approval(self, approval: dict) -> dict:
        with self.tx() as conn:
            existing = conn.execute("SELECT * FROM approvals WHERE run_id=?", (approval["run_id"],)).fetchone()
            if existing:  # approval creation is idempotent per run (resume safety)
                return self._approval_row(dict(existing))
            conn.execute(
                """INSERT INTO approvals(approval_id, run_id, case_id, status, decision_type, proposed_outcome, amount,
                   currency, amount_aud, required_slots, decisions, explanation, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (approval["approval_id"], approval["run_id"], approval["case_id"], "PENDING", approval["decision_type"],
                 approval["proposed_outcome"], approval["amount"], approval["currency"], approval["amount_aud"],
                 json.dumps(approval["required_slots"]), json.dumps([]), json.dumps(approval["explanation"]),
                 approval["created_at"]),
            )
        return self.get_approval(approval["approval_id"])

    @staticmethod
    def _approval_row(row: dict) -> dict:
        for key in ("required_slots", "decisions", "explanation"):
            row[key] = json.loads(row[key])
        return row

    def get_approval(self, approval_id: str) -> Optional[dict]:
        row = self.fetchone("SELECT * FROM approvals WHERE approval_id=?", (approval_id,))
        return self._approval_row(row) if row else None

    def get_approval_for_run(self, run_id: str) -> Optional[dict]:
        row = self.fetchone("SELECT * FROM approvals WHERE run_id=?", (run_id,))
        return self._approval_row(row) if row else None

    def list_approvals(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self.fetchall("SELECT * FROM approvals WHERE status=? ORDER BY created_at", (status,))
        else:
            rows = self.fetchall("SELECT * FROM approvals ORDER BY created_at")
        return [self._approval_row(r) for r in rows]

    # -- outcome pool --------------------------------------------------------
    def upsert_outcome(self, *, run_id: str, case_id: str, outcome: str | None, pool_status: str,
                       primary_exception: str | None, amount: str | None, currency: str | None,
                       summary: str | None, payload: dict, now: str) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO outcomes(run_id, case_id, outcome, pool_status, primary_exception, amount, currency,
                   summary, payload, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(run_id) DO UPDATE SET outcome=excluded.outcome, pool_status=excluded.pool_status,
                   primary_exception=excluded.primary_exception, amount=excluded.amount, currency=excluded.currency,
                   summary=excluded.summary, payload=excluded.payload, updated_at=excluded.updated_at""",
                (run_id, case_id, outcome, pool_status, primary_exception, amount, currency, summary,
                 json.dumps(payload), now),
            )

    def claim_for_new_attempt(self, run_id: str, now: str) -> bool:
        """Atomically mark a held / escalated / failed run as SUPERSEDED so it can be resumed only ONCE (two concurrent
        evidence or review calls cannot both start a new attempt). False = already superseded (or not resumable)."""
        with self.tx() as conn:
            cur = conn.execute("UPDATE outcomes SET pool_status='SUPERSEDED', updated_at=? WHERE run_id=? "
                               "AND pool_status IN ('HELD', 'ESCALATED', 'FAILED')", (now, run_id))
            return cur.rowcount == 1

    def set_superseded_by(self, run_id: str, new_run_id: str) -> None:
        with self.tx() as conn:
            row = conn.execute("SELECT payload FROM outcomes WHERE run_id=?", (run_id,)).fetchone()
            if row:
                payload = {**json.loads(row["payload"]), "superseded_by": new_run_id}
                conn.execute("UPDATE outcomes SET payload=? WHERE run_id=?", (json.dumps(payload), run_id))

    def latest_attempt(self, run_id: str) -> str | None:
        row = self.fetchone("SELECT run_id FROM runs WHERE parent_run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,))
        return row["run_id"] if row else None

    def list_outcomes(self, pool_status: str | None = None, case_id: str | None = None) -> list[dict]:
        sql = "SELECT run_id, case_id, outcome, pool_status, primary_exception, amount, currency, summary, updated_at FROM outcomes"
        clauses, params = [], []
        if pool_status:
            clauses.append("pool_status=?"); params.append(pool_status)
        if case_id:
            clauses.append("case_id=?"); params.append(case_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self.fetchall(sql + " ORDER BY updated_at DESC", tuple(params))

    def get_outcome(self, run_id: str) -> Optional[dict]:
        row = self.fetchone("SELECT * FROM outcomes WHERE run_id=?", (run_id,))
        if row:
            row["payload"] = json.loads(row["payload"])
        return row
