#!/usr/bin/env python3
"""Load test cases into a RUNNING finagent API (local or Docker) and print a summary table.

    python scripts/dev/load_cases.py                         # all scenarios
    python scripts/dev/load_cases.py FIN-001 FIN-003         # selected cases
    python scripts/dev/load_cases.py --required              # FIN-001..FIN-005
    python scripts/dev/load_cases.py --with-approvals        # also replay each scenario's approval decisions
    API=http://localhost:8080 API_KEY=... python scripts/dev/load_cases.py

Uses only the public HTTP API (same as any client), so it also works against AWS.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--required", action="store_true", help="only FIN-001..FIN-005")
    ap.add_argument("--with-approvals", action="store_true", help="replay each scenario's approval decisions")
    ap.add_argument("--diagrams", type=lambda v: [x for x in v.split(",") if x in ("svg", "pdf", "png")], default=[],
                    help="comma list, e.g. png,pdf - save a diagram of each run to data/diagrams/")
    a = ap.parse_args()
    api = os.environ.get("API", f"http://127.0.0.1:{os.environ.get('PORT', '8080')}").rstrip("/")
    headers = {"X-API-Key": os.environ["API_KEY"]} if os.environ.get("API_KEY") else {}
    client = httpx.Client(base_url=api, headers=headers, timeout=60)
    try:
        client.get("/healthz").raise_for_status()
    except httpx.HTTPError as exc:
        print(f"API not reachable at {api} ({exc}). Start it with `make start` or `make up`.")
        return 2
    scen = json.loads((ROOT / "mock_data" / "scenarios.json").read_text())["scenarios"]
    wanted = set(a.cases) or ({f"FIN-00{i}" for i in range(1, 6)} if a.required else None)
    rows = []
    for sc in scen:
        if wanted and sc["case_id"] not in wanted:
            continue
        req = json.loads((ROOT / sc["request_file"]).read_text())
        r = client.post("/runs", json=req)
        if r.status_code != 201:
            rows.append((sc["case_id"], f"HTTP {r.status_code}", "-", "-", sc["title"]))
            continue
        run = r.json()
        rec = (run.get("result") or {}).get("recommendation") or {}
        status, apr = run["status"], (run.get("approval") or {}).get("approval_id")
        if a.with_approvals and apr:
            for st in sc["steps"]:
                if st["op"] != "decide":
                    continue
                d = client.post(f"/approvals/{apr}/decision", json={"decision": st["decision"], "approver_id": st["approver_id"],
                                                                   "comment": f"load_cases: scenario step for {sc['case_id']}",
                                                                   "callback_id": st.get("callback_id") or f"load-{sc['case_id']}-{st['approver_id']}"})
                status = (d.json().get("run") or {}).get("status", status)
        rows.append((sc["case_id"], status, rec.get("outcome") or "-", apr or "-", sc["title"]))
        for fmt in a.diagrams or []:
            out = ROOT / "data" / "diagrams" / f"{sc['case_id']}_{run['run_id']}.{fmt}"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(client.get(f"/runs/{run['run_id']}/diagram", params={"format": fmt}).content)
    print(f"{'CASE':<9}{'STATUS':<20}{'OUTCOME':<26}{'APPROVAL':<18}TITLE")
    for row in rows:
        print(f"{row[0]:<9}{row[1]:<20}{row[2]:<26}{row[3]:<18}{row[4][:50]}")
    if a.diagrams:
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "scripts" / "dev" / "diagram_gallery.py"), str(ROOT / "data" / "diagrams")], check=False)
        print(f"diagrams saved to {ROOT / 'data' / 'diagrams'}  (open index.html)")
    print(f"\n{len(rows)} case(s) loaded into {api}. Pending approvals: GET {api}/approvals?status=PENDING  |  Swagger: {api}/docs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
