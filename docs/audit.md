# Audit trail

Every operation writes an audit event to `audit_events` (SQLite) and `data/audit.jsonl` (append-only JSONL for SIEM / CloudWatch shipping).

## Event shape

```json
{"event_id": "evt-…", "run_id": "run-…", "correlation_id": "run-…", "ts": "2026-11-12T10:00:00+11:00",
 "event_type": "tool.call", "node": null, "tool": "get_purchase_order", "outcome": "retrying", "duration_ms": 301,
 "attempt": 1, "actor": "system", "details": {"args": {"po_ref": "PO-40004"}, "error": "get_purchase_order timed out after 0.3s"},
 "prev_hash": "…", "hash": "…"}
```

## What is recorded

| Area | Event types |
|---|---|
| Run lifecycle | `run.created`, `run.start_replayed`, `node.completed` (every node, with duration), `run.resumed`, `run.resume_noop`, `run.simulated_crash`, `run.needs_information`, `run.completed`, `run.failed` |
| Workflow | `workflow.planned` (pipeline name, version, digest, planned skills) |
| Parsing / skills | `skill.parser` (file, parser, version, schema, sha256, warnings), `skill.processor` (findings, exceptions, votes, compliance refs), `skill.procedure` (per control skill: status ok / invalid / failed / budget, verdict, steps, scripts called, model, spec version, duration) |
| Retrieval and tools | `tool.call` per attempt (args summary, evidence id, result summary, error, attempt, duration), `tool.invalid_arguments`, `tool.denied` |
| Model | `model.call` (provider, model, request id, token usage, validation errors, prompt size), `model.fallback`, `model.override_blocked`, `model.escalation_adopted` |
| Decision | `grounding.gap`, `policy.changed_during_run`, `policy.unverified` (corpus differs from the reviewed baseline; case held), `decision.recommended` (outcome, primary exception, confidence, rules outcome) |
| Posting | `policy.changed_since_decision` (cited policy changed after approval; posting blocked), `decision.already_decided` (this invoice already has an ERP decision from another run; posting blocked, case held) |
| Resume | `evidence.received`, `review.recorded`, `resume.refused` (the run was already resumed; 409 with the newer attempt) |
| Policy maintenance | `policy.baseline_recorded` (who, role, note, every changed section with old and new hash), `policy.baseline_denied` (not an active Financial Controller / CFO, or no note; nothing written) |
| Approvals | `approval.requested`, `approval.decision_recorded` (approver, role, slot, register version, channel), `approval.denied` (reasons), `approval.duplicate_ignored`, `approval.callback_replayed`, `approval.resolved` |
| Evidence / webhooks | `evidence.received`, `webhook.subscribed`, `webhook.enqueued`, `webhook.delivery`, `webhook.inbound_rejected` |

Together with the `checkpoints` table (full state after every node), the stored request, the config manifest and the index version, this is enough to **explain**, **reproduce** (same corpus index + workflow digest + frozen clock) and **safely resume** any run.

## Reasoning trace (the "why")

The audit trail records *what* happened. The reasoning trace records *why*: one entry per key step, each with `step`, `actor`, `decision`, `reason` and `refs`. It covers:

- documents parsed;
- controls planned or skipped (with the condition);
- policy retrieval;
- each evidence tool called or skipped and why;
- each skill's verdict and reason;
- how the rules outcome was derived (most conservative vote and from which skill);
- the model's proposal and rationale;
- guard adjustments, the final recommendation and the approval requirement;
- every human decision (approver, role, limit, register version, channel, comment);
- refused approval attempts with reasons;
- ignored duplicates;
- the simulated posting.

Read it with `finagent explain <run_id>`, `GET /runs/{run_id}/reasoning`, or `result.reasoning` in `GET /runs/{run_id}`. Rejections require a reason (`comment`), so human "no" decisions are always explained.

### Skill questions in the reasoning trace

A skill whose answers add caution appears as actor `skill:<name> (questions)`, with the verdict, which model decided and the answers, e.g.
`skill:non_po_validation (procedure) | HOLD (confidence 0.85) | A lapsed maintenance agreement is a planning failure…`.
A procedure that can't be completed appears as `NOT COMPLETED (invalid) -> hold`. The full step log (scripts called,
model, errors) is in `state.procedures` and the `skill.procedure` audit event.

## Safe logging (FIN-POL-001 s6, FIN-POL-004 s2, FIN-POL-010 s2)

Every event's `details` passes through `redaction.redact`:

- Account numbers and IBANs become `****1234`. BSBs are masked.
- E-mail addresses are reduced to `j***@domain`. Phone numbers and secrets (`sk-…`, `AKIA…`) are removed.
- Sensitive keys (`account_number`, `tax_id`, `api_key`, `signature`, …) are always masked.

Beyond redaction:

- Tool outputs are summarised (minimum necessary): the vendor tool returns only masked payment details in the first place.
- Prompts are redacted before they reach any model.
- Tests assert that the full synthetic account numbers never appear in the audit (`test_audit_has_no_secrets_and_chain_verifies` and an invariant in every evaluation scenario).

## Tamper evidence

Each event stores `prev_hash` and `hash = sha256(prev_hash + canonical_json(event))`.

- `finagent audit-verify` or `GET /audit/verify` recomputes the chain and reports the first broken sequence number.
- `test_audit_tamper_detected` proves a modified row is detected.

For production, export to S3 Object Lock (compliance mode) and anchor the chain head periodically.
