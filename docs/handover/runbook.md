# Runbook

## Health

- `GET /healthz` returns the model provider, index version and workflow digest. Alert if the digest or index version changes unexpectedly.
- `finagent audit-verify` must report `valid: true`.
- `GET /outcomes/summary`: watch for `FAILED` > 0.
- `GET /webhooks/events`: watch for `DEAD` deliveries.

## Incidents

| Symptom | Likely cause | Action |
|---|---|---|
| Runs stuck in `RUNNING` | process crashed or redeployed mid-run | `finagent resume --all` (idempotent; scheduled every 5 min in AWS). Check `checkpoints` for the last node. |
| Many `HOLD_FOR_INFORMATION` with `MISSING_PO` + "timed out" | PO service down or slow | Check tool audit events (`tool.call` outcome `retrying`/`failed`). Fix the upstream, then add evidence or re-start the cases. Raise `tools.overrides.get_purchase_order.timeout_s` only with care. |
| `FAILED: budget exceeded` | retrieval queries grew (new skills) or a loop | `finagent workflow show`; raise `agent.max_tool_calls` deliberately; add a test |
| `FAILED: model output invalid` | provider outage or prompt drift (only with `on_invalid_output: fail`) | check `model.call` audit events (`errors`); switch provider; or use `fallback` |
| `grounding.gap` events / unexpected holds | corpus changed and a referenced section no longer exists or is not retrieved | `finagent ingest`; `finagent eval` (RAG misses list); update the skill's `compliance_refs` or `retrieval_queries` |
| Approver gets 403 | authority, expired delegation or segregation of duties | `denied_reasons` in the response / `approval.denied` audit event. Correct the approver; don't bypass. |
| Webhook consumers not receiving | endpoint down or bad secret | `GET /webhooks/events`, `POST /webhooks/deliver`; rotate the secret on both sides |
| `audit-verify` invalid | tampering or manual DB edit | **Security incident.** Preserve the DB and JSONL; compare with the JSONL / exported copy; escalate. |
| `/healthz` shows `llm_provider=mock` in production | config not applied | check task definition env / `FINAGENT__LLM__PROVIDER` |

## Routine tasks

- **Policy change** (maintenance release, never live):
  1. Update the corpus file (new version in front matter) and the controls / skills / `config/policy_parameters.yaml` it affects, with tests.
  2. `make test eval`, then `finagent skills docs` to refresh the injected policy snapshots.
  3. `finagent policy baseline --reviewer <id> --note "<why>"` (Financial Controller / CFO; audited as `policy.baseline_recorded`).
  4. Deploy / restart. At start-up the index is rebuilt and the search model retrained (gated; BM25 only if that fails).
  5. Check `finagent policy status` / `/healthz`: `"policy": "maintenance_required"` means the corpus differs from the baseline and every case is held.
- **Model misbehaving (many procedure holds, odd warnings):** flip the kill switch `skills.llm.enabled: false` (or env `FINAGENT__SKILLS__LLM__ENABLED=false`) and restart. The deterministic scripts then decide alone; nothing else changes. Investigate with `make skill-check` and the `skill.procedure` audit events, then switch back on.
- **Health:** `GET /healthz` → `skill_questions` shows the model pair, each model's `reachable` / `model_pulled`, and whether the pair is `approved` (with hints).
- **Switching a skill model:** run `make skill-check SMALL=<p>/<m> LARGE=<p>/<m>` (the acceptance gate, `LARGE=none` for small only). Only on PASS, set `skills.llm.small` / `skills.llm.large` and add the printed pair id to `skills.llm.approved`. The core flow can't change (scripts never see model output); only the answers can.
- **Large model misbehaving or too slow:** set `skills.llm.large.provider: none`; the small model's answers then decide (re-gate that pair).
- **Model unavailable (Ollama down, provider outage):** every case touching a judgement skill is **held** with "judgement questions not answered" - nothing is passed. Restore the model, then re-run held cases with `POST /runs/{id}/evidence` (note: "re-run after model outage").
- **Change a skill's behaviour:** edit `skills/<name>/SKILL.md` (questions, answers, answer → verdict) or its `references/`; takes effect on the next run. Check with `pytest tests/unit/test_skill_packs.py` and `make skill-check` (real models).
- **Authority register change:** update the source system (`mock_data/authority_register.json` in the POC). Approvals re-validate on every decision.
- **New approver:** add to the register (role, limit, flags). Delegations need start and end dates (FIN-POL-003 s4).
- **Held case gets new evidence:** `POST /runs/{id}/evidence`. This creates a new attempt that revalidates every control; the old result stays.
- **AI could not settle a judgement question** (uncertain, or held again after evidence): a Financial Controller / Financial Control Analyst records a decision with `POST /runs/{id}/review` (`clear` or `concern` + reason). The case re-runs with that question answered by the person; everything else is re-checked; that reviewer cannot approve the case. Every decision is audited (`review.recorded`) and is labelled data for recalibrating thresholds.
- **Data retention:** the records are the system of record for 7 years (FIN-POL-010 s1). Do not delete `data/` in an environment holding real data; legal hold pauses deletion.

## Backups (POC)

- **Local:** copy `data/finagent.db*` and `data/audit.jsonl` while the service is stopped, or use `sqlite3 .backup`.
- **AWS:** EFS. Enable AWS Backup for production.
