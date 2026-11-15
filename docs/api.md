# API, outcome pool, approvals and webhooks

- **Swagger UI:** `http://localhost:8080/docs`. ReDoc: `/redoc`.
- **Spec:** [`api/openapi.yaml`](api/openapi.yaml) / [`api/openapi.json`](api/openapi.json), regenerated with `make openapi`.
- **Request collection:** [`api/finagent.http`](api/finagent.http), for the VS Code REST Client or JetBrains HTTP client.
- **Auth:** when `app.api_key` is set, send `X-API-Key`. The inbound approval webhook uses an HMAC signature instead.

## Required logical operations

| Operation (brief) | Endpoint |
|---|---|
| Start run | `POST /runs` → executes until `COMPLETED`, `FAILED`, `AWAITING_APPROVAL` or `NEEDS_INFORMATION` |
| Get run | `GET /runs/{run_id}`: status, current node, checkpoints, typed result, approval, audit events, **reasoning** |
| Reasoning | `GET /runs/{run_id}/reasoning`: why each key step went the way it did (actor = system / skill / model / human) |
| Approve / reject | `POST /approvals/{approval_id}/decision`, or signed `POST /webhooks/inbound/approval`. Resumes the **same** run. |
| List evaluation results | `POST /evaluations/run`, `GET /evaluations/latest`, `GET /evaluations` |

The full endpoint list is in Swagger (paths across runs, approvals, outcomes, webhooks, policy, audit, evaluation and catalog).

## Approval request (with explanation)

When a run reaches a consequential outcome (`APPROVE_FOR_POSTING`, `REJECT_DUPLICATE`, `REJECT_INVALID`), it creates one approval per run and stops. `GET /approvals/{id}` returns:

```json
{
  "approval_id": "apr-…", "run_id": "run-…", "status": "PENDING", "decision_type": "POST",
  "proposed_outcome": "APPROVE_FOR_POSTING", "amount": "18150.00", "currency": "AUD", "amount_aud": "18150.00",
  "required_slots": [{"slot": "primary", "description": "Approver with authority >= AUD 18150.00 (minimum role: Department Director)", "min_limit_aud": "18150.00"}],
  "decisions": [],
  "explanation": {
    "what_approval_does": "Authorises a SIMULATED ERP posting of this invoice (no money moves).",
    "summary": "…", "confidence": 0.9, "primary_exception": null, "exceptions": [], "failed_or_unknown_findings": [],
    "key_findings": [{"control": "three_way_match.three_way_match", "status": "PASS", "detail": "…"}],
    "key_calculations": [{"name": "price_variance_line 1 (HLM-200)", "inputs": {…}, "result": "0.00", "threshold": "50.00", "passed": true}],
    "risk_indicators": [], "unknowns": [],
    "citations": ["FIN-POL-002@v2.4#s2", "FIN-POL-003@v4.0#s2", "po:PO-40001@…", "vendor:V-1001@…"],
    "constraints": {"excluded_approvers": {"u-vg-01": "created the vendor record (FIN-POL-003 s1)"}, "distinct_roles_required": false},
    "proposed_payment_date": "2026-12-03"
  }
}
```

This satisfies FIN-POL-003 s5: the approver sees amount, vendor, exceptions and source citations. Each recorded decision stores approver, role, applicable limit, register version, timestamp and case id.

**Slots.**

- `primary`: monetary authority.
- `financial_control`: higher-risk co-approval.
- `procurement_governance`: non-PO above AUD 10k.

One person fills one slot. Rejection by any authorised approver resolves the approval as `REJECTED`, and nothing is submitted.

## Resume semantics

| Event | Effect |
|---|---|
| Last required slot approved | Run → `SUBMIT_DECISION` (idempotent simulated posting) → `COMPLETED`, pool `SUBMITTED`, `decision.submitted` webhook |
| Rejected | Run → `FINALIZE_REJECTED` → `COMPLETED`, pool `REJECTED_BY_APPROVER`, no ERP call |
| Same `callback_id` again | Stored response replayed (`replayed: true`); nothing executes |
| Different callback after resolution | `409`, `effect: none` |
| Crash between approval and posting | `resume` re-runs `SUBMIT_DECISION` with the same idempotency key, so the ERP sandbox returns `DUPLICATE_IGNORED` and there is still exactly one decision |

## Outcome pool

`GET /outcomes?pool_status=…` and `GET /outcomes/summary`:

| pool_status | Meaning | Next step |
|---|---|---|
| `AWAITING_APPROVAL` | Recommendation made, approval pending | approver decides |
| `SUBMITTED` | Approved and simulated posting/rejection recorded | none |
| `HELD` | `HOLD_FOR_INFORMATION`; exception owner and review date in the payload | add evidence: `POST /runs/{id}/evidence`; or, for a judgement question the AI could not settle, a reviewer decision: `POST /runs/{id}/review` |
| `ESCALATED` | `ESCALATE_CONTROL_REVIEW` (fraud indicators, repeated non-PO) | control team; supplier not notified |
| `REJECTED_BY_APPROVER` | Human rejected the recommendation | AP review |
| `FAILED` | Explicit failure (budget, invalid model output in `fail` mode, unexpected error) | investigate; re-run |
| `SUPERSEDED` | A held / escalated / failed run that was resumed; `payload.superseded_by` names the newer attempt | use the newer run |

A held, escalated or failed run is resumed **once**. A second `POST /runs/{id}/evidence` or `/review` on the same run
returns **409** `{"error": "superseded", "newer_attempt": "run-…"}`: add evidence or the review to that newer run instead.
The check is a single atomic update, so concurrent calls start exactly one new attempt.

**One decision per invoice.** `submit_finance_decision` refuses a second ERP decision for the same vendor and
normalised invoice number, whichever run sends it (a second attempt, or the same invoice sent again as a new request).
That run is held as `DUPLICATE_RISK` (audit `decision.already_decided`), and nothing is posted.

## Webhooks

**Outbound.** Subscribe with `POST /webhooks/subscriptions {"url": "...", "events": ["approval.requested", "run.needs_information"]}` (or `"*"`). Each delivery carries these headers:

```
X-Finagent-Event: approval.requested
X-Finagent-Delivery: dlv-…
X-Finagent-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>
```

Body: `{"event_type", "event_id", "run_id", "occurred_at", "data": {...}}`.

- `approval.requested.data` contains `approval_id`, `explanation` and `decide_url`.
- Deliveries use a transactional outbox: retried by `POST /webhooks/deliver` (or the AWS schedule), and marked `DEAD` after `webhooks.max_delivery_attempts`.
- Even without subscribers, every event is written to the feed (`GET /webhooks/events`).

**Inbound (external approval systems).** `POST /webhooks/inbound/approval`:

- Body: `{"approval_id", "decision", "approver_id", "comment", "callback_id"}`.
- It must be signed the same way.
- Timestamps older than 5 minutes are rejected, and bad signatures return `401` and are audited.

**Testing the approval loop:** `make webhook-demo` or `make inbox` runs a local approval inbox that receives signed events and answers through the signed inbound callback. See [api/webhook_testing.md](api/webhook_testing.md). For a passive receiver that only verifies and prints events: `finagent webhooks listen --port 9099`.

## Policy (read-only)

Policy changes are maintenance releases, never live ([governance.md](governance.md)). The API only reports:

| Endpoint | Purpose |
|---|---|
| `GET /policy/status` | `state` (`ok` \| `maintenance_required`), `unverified_sections`, `baseline_reviewed_by`, `baseline_reviewed_at`. `maintenance_required` = every case is held |
| `GET /policy/parameters` | every policy number the controls enforce: name, value, source section, unit, description |

Every run result has a `policy` block (`baseline_reviewed_by`, `baseline_reviewed_at`, `unverified_sections`).

**Human review:** `POST /runs/{id}/review` with `{"reviewer_id": "u-fc-01", "skill": "non_po_validation", "question": "emergency_genuine", "decision": "clear" | "concern", "reason": "..."}`. Only for questions the AI left uncertain or unanswered in a held run. Reviewer role per `skills.review.roles`; never the requester; the reviewer cannot approve that case. Re-runs as a new attempt with the question answered by the person.

Full reference PDF, generated from the OpenAPI spec: [api/finagent_api_reference.pdf](api/finagent_api_reference.pdf).
