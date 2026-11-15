"""HTTP API contract tests (FastAPI TestClient) - mirrors the Swagger spec."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from finagent.api.app import create_app
from finagent.webhooks import sign
from tests.conftest import load_case


@pytest.fixture()
def client(fresh_app):
    return TestClient(create_app(fresh_app))


def test_openapi_exposes_required_operations(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert "post" in paths["/runs"] and "get" in paths["/runs/{run_id}"]
    assert "post" in paths["/approvals/{approval_id}/decision"] and "post" in paths["/evaluations/run"]
    assert "ProcessingRequest" in spec["components"]["schemas"]
    assert client.get("/docs").status_code == 200


def test_start_get_approve_flow(client, fresh_app):
    r = client.post("/runs", json=load_case(fresh_app.settings, "FIN-001"))
    assert r.status_code == 201 and r.json()["status"] == "AWAITING_APPROVAL"
    run_id, apr = r.json()["run_id"], r.json()["approval"]["approval_id"]
    g = client.get(f"/runs/{run_id}").json()
    assert g["audit_events"] and g["result"]["recommendation"]["outcome"] == "APPROVE_FOR_POSTING"
    assert client.get("/outcomes", params={"pool_status": "AWAITING_APPROVAL"}).json()[0]["run_id"] == run_id
    d = client.post(f"/approvals/{apr}/decision", json={"decision": "APPROVE", "approver_id": "u-ccm-01", "callback_id": "c0"})
    assert d.status_code == 403 and d.json()["denied_reasons"]
    d = client.post(f"/approvals/{apr}/decision", json={"decision": "APPROVE", "approver_id": "u-dd-01", "callback_id": "c1"})
    assert d.status_code == 200 and d.json()["run"]["status"] == "COMPLETED"
    again = client.post(f"/approvals/{apr}/decision", json={"decision": "APPROVE", "approver_id": "u-dd-01", "callback_id": "c1"})
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert client.get(f"/outcomes/{run_id}").json()["pool_status"] == "SUBMITTED"


def test_invalid_request_422(client, fresh_app):
    assert client.post("/runs", json=load_case(fresh_app.settings, "FIN-028")).status_code == 422


def test_unknown_run_404(client):
    assert client.get("/runs/run-nope").status_code == 404


def test_hold_then_evidence_resume(client, fresh_app):
    r = client.post("/runs", json=load_case(fresh_app.settings, "FIN-038")).json()
    assert r["status"] == "NEEDS_INFORMATION"
    e = client.post(f"/runs/{r['run_id']}/evidence", json={"po_ref": "PO-40038", "note": "PO confirmed"})
    assert e.status_code == 201 and e.json()["status"] == "AWAITING_APPROVAL" and e.json()["parent_run_id"] == r["run_id"]


def test_inbound_signed_webhook(client, fresh_app):
    r = client.post("/runs", json=load_case(fresh_app.settings, "FIN-007")).json()
    body = json.dumps({"approval_id": r["approval"]["approval_id"], "decision": "APPROVE", "approver_id": "u-ccm-01",
                       "callback_id": "ext-1"}).encode()
    bad = client.post("/webhooks/inbound/approval", content=body, headers={"X-Finagent-Signature": "t=1,v1=00"})
    assert bad.status_code == 401
    ok = client.post("/webhooks/inbound/approval", content=body,
                     headers={"X-Finagent-Signature": sign(fresh_app.webhooks.secret, body), "Content-Type": "application/json"})
    assert ok.status_code == 200 and ok.json()["run"]["status"] == "COMPLETED"


def test_webhook_subscription_and_event_feed(client, fresh_app):
    s = client.post("/webhooks/subscriptions", json={"url": "http://127.0.0.1:9/unreachable", "events": ["approval.requested"]})
    assert s.status_code == 201
    client.post("/runs", json=load_case(fresh_app.settings, "FIN-007"))
    feed = client.get("/webhooks/events").json()
    assert any(e["event_type"] == "approval.requested" and e["subscription_id"] for e in feed)
    assert "explanation" in next(e for e in feed if e["event_type"] == "approval.requested")["payload"]["data"]
    assert client.post("/webhooks/subscriptions", json={"url": "http://x", "events": ["nope"]}).status_code == 422


def test_catalog_endpoints(client):
    assert {t["name"] for t in client.get("/catalog/tools").json()} >= {"submit_finance_decision"}
    assert client.get("/catalog/workflow").json()["digest"]
    assert "normalized_document.v1.schema.json" in client.get("/catalog/contracts").json()
    assert client.get("/healthz").json()["status"] == "ok"


def test_api_key_enforced(fresh_app):
    fresh_app.settings.raw["app"]["api_key"] = "k1"
    c = TestClient(create_app(fresh_app))
    assert c.get("/runs").status_code == 401 and c.get("/runs", headers={"X-API-Key": "k1"}).status_code == 200
    fresh_app.settings.raw["app"]["api_key"] = None
