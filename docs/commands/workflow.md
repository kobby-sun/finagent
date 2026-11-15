# Commands: controlling the workflow

Every operation is available through the **CLI** (`finagent …`) and the **HTTP API**. Swagger UI is at `/docs` and the spec at [`docs/api/openapi.yaml`](../api/openapi.yaml). A ready-to-run request collection is in [`docs/api/finagent.http`](../api/finagent.http).

## Run lifecycle

| Goal | CLI | HTTP |
|---|---|---|
| Start a run | `finagent start --case FIN-001` / `--file req.json` [`--idempotency-key K`] | `POST /runs` (header `Idempotency-Key`) |
| Get a run (status, node, result, audit) | `finagent get RUN [--audit]` | `GET /runs/{run_id}` |
| List runs | `finagent runs [--status AWAITING_APPROVAL] [--case FIN-001]` | `GET /runs?status=&case_id=` |
| Resume after a crash | `finagent resume RUN` / `finagent resume --all` | `POST /runs/{run_id}/resume` |
| Add evidence to a held case | `finagent evidence RUN --po-ref PO-40038 --file note.txt --note "..."` | `POST /runs/{run_id}/evidence` |

Run statuses:

| Status | Meaning |
|---|---|
| `RUNNING` | in progress, or interrupted and resumable |
| `AWAITING_APPROVAL` | stopped at the approval gate |
| `NEEDS_INFORMATION` | hold or escalation; resumable with new evidence |
| `COMPLETED` | finished |
| `FAILED` | explicit reason in `error` |

## Approvals (human in the loop)

| Goal | CLI | HTTP |
|---|---|---|
| List pending approvals (+ explanation) | `finagent approvals --status PENDING --explain` | `GET /approvals?status=PENDING`, `GET /approvals/{id}` |
| Approve | `finagent approve APR --approver u-dd-01 --callback-id cb-1 --comment "..."` | `POST /approvals/{id}/decision` `{"decision":"APPROVE",...}` |
| Reject | `finagent reject APR --approver u-ccm-01 --callback-id cb-2` | same, `"decision":"REJECT"` |
| External approval system | — | `POST /webhooks/inbound/approval` with `X-Finagent-Signature` |

Decision semantics:

| Response | When |
|---|---|
| `200 effect=recorded` | Decision recorded. Shows `remaining_slots` until all slots are filled, then the run resumes **in the same call**. |
| `200 replayed=true` | Same `callback_id` delivered again: the identical stored response, with no side effect. |
| `200 effect=none` | This approver already decided. |
| `403` | Insufficient authority, expired delegation, or segregation of duties (vendor creator, recent vendor changer, requester, receipter above AUD 25k). `denied_reasons` explains which. |
| `409` | Approval already resolved; no effect. |

## Outcome pool

| Goal | CLI | HTTP |
|---|---|---|
| List outcomes | `finagent outcomes [--status HELD]` | `GET /outcomes?pool_status=HELD` |
| Counts per status | — | `GET /outcomes/summary` |
| One run's outcome | — | `GET /outcomes/{run_id}` |

Pool statuses: `AWAITING_APPROVAL`, `SUBMITTED`, `HELD`, `ESCALATED`, `REJECTED_BY_APPROVER`, `FAILED`.

## Webhooks

| Goal | CLI | HTTP |
|---|---|---|
| Subscribe | `finagent webhooks subscribe http://host/hooks --events approval.requested run.needs_information` | `POST /webhooks/subscriptions` |
| Event feed / delivery log | `finagent webhooks events [--run-id RUN]` | `GET /webhooks/events` |
| Retry deliveries | `finagent webhooks deliver` | `POST /webhooks/deliver` |
| Local receiver (verifies signatures) | `finagent webhooks listen --port 9099` | — |

Event types: `run.created`, `approval.requested` (carries the full explanation), `run.awaiting_approval`, `approval.decision_recorded`, `approval.resolved`, `decision.submitted`, `run.completed`, `run.needs_information`, `run.failed`, `evidence.received`.

## Audit

| Goal | CLI | HTTP |
|---|---|---|
| Audit events for a run | `finagent audit RUN` | `GET /runs/{run_id}/audit` |
| Verify the hash chain | `finagent audit-verify` | `GET /audit/verify` |

## Evaluation and skills

| Goal | Command |
|---|---|
| All scenarios (pass/fail) | `finagent eval` / `POST /evaluations/run`, `GET /evaluations/latest` |
| Subset | `finagent eval --cases FIN-001 FIN-003` |
| Skill fixtures | `finagent skills test [name]` / `POST /skills/{name}/test` |
| Skill catalogue | `finagent skills list` / `GET /catalog/skills` |
| Coverage matrix | `finagent skills coverage` |
| Regenerate skill docs (injected compliance) | `finagent skills docs` |

## Workflow (skill pipeline) changes

```bash
$EDITOR config/workflow.yaml        # re-order / disable / add skills, conditions, plugins
finagent workflow validate          # refuses unknown skills, bad dependencies, removed mandatory controls
finagent workflow show              # resolved pipeline + digest (recorded on every run)
make test                           # fixtures + scenarios must stay green
```

See [../extending.md](../extending.md) for adding parsers, skills, corpus sources and feeds.

## Corpus / index

```bash
finagent ingest                     # rebuild the BM25 index + vectors from rag.sources
```

## Policy (maintenance releases, never live)

```bash
finagent policy status                           # does the policy text match the reviewed baseline? (make policy)
finagent policy params                           # every policy number the controls enforce, with its source section
finagent policy baseline --reviewer u-fc-01 --note "annual review"   # last step of a policy maintenance release
```

A policy change: edit the policy, update controls / skills / `config/policy_parameters.yaml` with tests, `make test eval`,
`finagent policy baseline ...`, then restart ([governance.md](../governance.md)).

## Typical session

```bash
finagent start --case FIN-001                      # -> AWAITING_APPROVAL, approval_id apr-...
finagent approvals --status PENDING --explain      # read the explanation, citations, required slots
finagent approve apr-... --approver u-dd-01 --callback-id cb-1
finagent get run-...                               # COMPLETED, submission.posting_ref SIM-POST-...
finagent outcomes --status SUBMITTED
finagent audit-verify
```
