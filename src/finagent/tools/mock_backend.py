"""Simulated enterprise systems (vendor master, ERP purchasing, AP history,
treasury FX, authority register) backed by JSON fixtures in ``mock_data/``.

Fault injection (``mock_data/faults.json``) lets scenarios exercise timeouts
and transient failures deterministically:

    {"get_purchase_order": {"PO-40004": {"mode": "timeout", "attempts": "always"}},
     "get_vendor_record":  {"V-1020":   {"mode": "transient", "attempts": 2}}}

Counters are kept per (run_id, tool, key) so a resumed run sees the same
behaviour as a fresh one would for the same attempt number.
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from .base import TransientToolError


class MockBackend:
    def __init__(self, data_dir: Path, timeout_sleep_s: float = 1.5):
        self.data_dir = Path(data_dir)
        self.timeout_sleep_s = timeout_sleep_s
        self._lock = threading.Lock()
        self._counters: dict[tuple, int] = defaultdict(int)
        self.reload()

    def reload(self) -> None:
        def load(name, default):
            p = self.data_dir / name
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
        self.vendors = {v["vendor_id"]: v for v in load("vendors.json", [])}
        self.purchase_orders = {p["po_ref"]: p for p in load("purchase_orders.json", [])}
        self.history = load("invoice_history.json", [])
        self.fx_rates = load("fx_rates.json", [])
        self.authority = load("authority_register.json", {})
        self.faults = load("faults.json", {})

    # -- fault injection ---------------------------------------------------------
    def maybe_fault(self, tool: str, key: str, run_id: str) -> None:
        spec = (self.faults.get(tool) or {}).get(key)
        if not spec:
            return
        with self._lock:
            self._counters[(run_id, tool, key)] += 1
            n = self._counters[(run_id, tool, key)]
        attempts = spec.get("attempts", 1)
        active = attempts == "always" or n <= int(attempts)
        if not active:
            return
        if spec["mode"] == "timeout":
            time.sleep(self.timeout_sleep_s)
            raise TransientToolError(f"{tool} backend did not respond (simulated hang)")
        if spec["mode"] == "transient":
            raise TransientToolError(f"{tool} backend returned HTTP 503 (simulated, attempt {n})")

    def llm_fault(self, case_id: str) -> dict:
        return (self.faults.get("llm") or {}).get(case_id, {})

    # -- read APIs ---------------------------------------------------------------
    def vendor(self, vendor_id: str) -> dict | None:
        return self.vendors.get(vendor_id)

    def purchase_order(self, po_ref: str) -> dict | None:
        return self.purchase_orders.get(po_ref)

    def fx(self, from_ccy: str, to_ccy: str, rate_date: str) -> dict | None:
        for r in self.fx_rates:
            if r["from_currency"] == from_ccy and r["to_currency"] == to_ccy and r["rate_date"] == rate_date:
                return r
        return None

    def authority_register(self) -> dict[str, Any]:
        return self.authority

    def history_records(self) -> list[dict]:
        return self.history


def dec(v: Any) -> Decimal:
    return Decimal(str(v))
