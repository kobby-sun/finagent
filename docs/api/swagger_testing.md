# Testing in Swagger UI

Start the service (`make up` for Docker, or `make start` locally), then open **http://localhost:8080/docs**. *Try it out* is pre-enabled on every endpoint.

Every request body has an **Examples** dropdown. The `POST /runs` examples are generated from `mock_data/scenarios.json`: all 42 evaluated scenarios, each showing the expected result and which approver to use next.

## 1. Happy path: FIN-001

1. `POST /runs` → Examples: **FIN-001 Valid three-way match** → *Execute*.
   - Expect `201`, `status: AWAITING_APPROVAL`, `result.recommendation.outcome: APPROVE_FOR_POSTING`.
   - Copy `approval.approval_id` and `run_id`.
2. `GET /approvals/{approval_id}` shows the explanation the approver sees: summary, calculations, citations, required roles.
3. `POST /approvals/{approval_id}/decision`, using the decision examples:
   - **approve - Cost Centre Manager** → `403`, with `denied_reasons` "insufficient authority" (AUD 18,150 > 10,000).
   - **approve - Department Director** → `200`, with `run.status: COMPLETED` and `submission.posting_ref: SIM-POST-…`.
   - Execute the Department Director example **again** → `200`, `replayed: true`. Nothing runs twice.
4. Check the results:
   - `GET /runs/{run_id}` returns the full result plus checkpoints and audit events.
   - `GET /outcomes?pool_status=SUBMITTED` lists the case.
   - `GET /audit/verify` returns `valid: true`.

## 2. Exception cases (no approval needed)

| Example | Expect |
|---|---|
| FIN-002 Duplicate invoice | `AWAITING_APPROVAL` with `REJECT_DUPLICATE`. Approve as Cost Centre Manager → rejection recorded. |
| FIN-003 Poisoned supplier attachment | `NEEDS_INFORMATION`, `ESCALATE_CONTROL_REVIEW`, `approval: null`, 5 risk indicators |
| FIN-004 Missing evidence | `NEEDS_INFORMATION`, `HOLD_FOR_INFORMATION`. Takes about 3 s: the PO lookup is retried 3 times before giving up. |
| FIN-006 / 008 / 032 | holds for price, quantity and tax |
| FIN-028 Invalid request | `422` validation error |

## 3. Approval rules

| Example case | Decisions to try (in order) |
|---|---|
| FIN-012 new vendor | Cost Centre Manager → `remaining_slots: ["financial_control"]`; **co-approve - Financial Control** → `COMPLETED` |
| FIN-018 AUD 330k | **Executive Director** → `403`; **CFO** → `COMPLETED` |
| FIN-024 | **refused - vendor creator** → `403` (segregation of duties); Department Director → `COMPLETED` |
| FIN-029 | **refused - expired delegation** → `403`; Department Director → `COMPLETED` |
| FIN-027 | **reject** → `COMPLETED`, nothing posted, pool `REJECTED_BY_APPROVER` |

Each example has a fixed `callback_id`. Re-sending it **to the same approval** is a replay. Using it on another approval is processed normally, because the replay cache is scoped per approval.

## 4. Hold → evidence → resume (FIN-038)

1. `POST /runs` with **FIN-038** → `HOLD_FOR_INFORMATION / MISSING_PO`.
2. `POST /runs/{run_id}/evidence` with example **supply PO** → a new attempt (`attempt: 2`, `parent_run_id`) at `AWAITING_APPROVAL`.

## 5. Webhooks, evaluation

- `POST /webhooks/subscriptions` with example **all events to local listener**:
  1. run `finagent webhooks listen --port 9099` first;
  2. start any run and watch the listener;
  3. `GET /webhooks/events` shows the feed.
- `POST /evaluations/run` with example **required cases** or **all scenarios** returns pass/fail per case.

## 6. Policy (read-only)

- `GET /policy/status`: with the shipped corpus, `state` is `ok` and `unverified_sections` is empty.
- `GET /policy/parameters`: every policy number the controls use, with its source section.
- Policy changes are never live. If you edit a file in `finance_rag_corpus/` and restart, `GET /policy/status` shows
  `maintenance_required` and every new case is held (`policy.unverified`). Undo the edit (`git checkout finance_rag_corpus/`)
  and restart.

## Notes

- When `FINAGENT__APP__API_KEY` is set, click **Authorize** and enter the key (`X-API-Key`).
- `/webhooks/inbound/approval` needs an HMAC signature, so it is easier to test with `scripts/dev/api_walkthrough.sh` or the pytest `test_inbound_signed_webhook`.
- To reset all data between sessions: `make reset` (local) or `make down-clean` (Docker).
