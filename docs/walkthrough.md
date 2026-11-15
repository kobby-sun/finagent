# Walkthrough

This guide has two parts:

- **Part A** is a hands-on tour, run in a terminal. In about 20 minutes you will process the five required cases, approve, replay a callback, crash and resume a run, add evidence, read the audit trail and change the workflow.
- **Part B** is a code tour that follows one request through the source.

High-level diagrams are in [`diagrams/architecture.pdf`](diagrams/architecture.pdf). The detailed service design (code map, inside each component, API walk-throughs) is in [`diagrams/system_detailed_design.pdf`](diagrams/system_detailed_design.pdf). Editable Mermaid versions are in [architecture.md](architecture.md).

---

## Part A: hands-on

### A0. Setup (one time)

```bash
scripts/dev/setup.sh && source .venv/bin/activate
export FINAGENT__APP__DATA_DIR=$(mktemp -d)        # throwaway state for this walkthrough
finagent ingest | head -12                          # 15 documents -> 58 chunks; 55 authoritative, 1 superseded, 2 untrusted
finagent workflow show | head -30                   # the control pipeline that will run (stages, conditions, mandatory flags)
```

### A1. FIN-001: happy path, stopping for approval

```bash
finagent start --case FIN-001
```

Expect:

- `status: AWAITING_APPROVAL`, `outcome: APPROVE_FOR_POSTING`, `confidence: 0.9`;
- `next_action` saying a **Department Director** is needed (AUD 18,150 is above the Cost Centre Manager limit of 10,000);
- an `approval_id`.

Nothing has been posted yet. Look at what the approver will see:

```bash
finagent approvals --status PENDING --explain
```

Check these fields in the explanation:

- `key_calculations`: per-line price variance with inputs, formula, threshold and pass flag, plus invoice total and tax check. All computed in Decimal by code.
- `citations`: policy chunks such as `FIN-POL-002@v2.4#s2` and evidence ids such as `po:PO-40001@…` and `vendor:V-1001@…`.
- `constraints.excluded_approvers`: the vendor's creator, excluded under segregation of duties.

Inspect the typed result: facts, calculations, inferences, unknowns, findings and actions are kept separate.

```bash
finagent get <run_id> | jq '.result | {facts: .sourced_facts|length, calcs: .calculations|length,
  findings: [.policy_findings[] | {control, status}], unknowns, actions: [.actions_taken[].tool]}'
```

### A2. Approving, and the duplicate callback (FIN-001 and FIN-005)

```bash
finagent approve <approval_id> --approver u-ccm-01 --callback-id cb-1   # 403: limit 10,000 < 18,150
finagent approve <approval_id> --approver u-dd-01  --callback-id cb-2   # 200: run resumes -> COMPLETED, SIM-POST-...
finagent approve <approval_id> --approver u-dd-01  --callback-id cb-2   # 200 replayed=true: same response, nothing re-executed
finagent approve <approval_id> --approver u-ed-01  --callback-id cb-3   # 409: already APPROVED, effect none
finagent outcomes --status SUBMITTED
```

Exactly one simulated posting exists, keyed by an idempotency key derived from the approval.

### A3. FIN-002: duplicate invoice

```bash
finagent start --case FIN-002
```

Expect `REJECT_DUPLICATE`, primary exception `DUPLICATE_RISK`, citing paid record `H-9001`.

- Rejection is still a financial decision, so it waits for approval too: `finagent approve <id> --approver u-ccm-01`.
- On approval it submits a **REJECT** decision. Payment is never proposed.

### A4. FIN-003: poisoned supplier attachment

```bash
finagent start --case FIN-003 --full | jq '{status, outcome: .result.recommendation.outcome,
  indicators: [.result.risk_indicators[].indicator] | unique, approval,
  guard: [.audit_events[] | select(.event_type=="model.override_blocked") | .details]}'
```

Expect:

- `ESCALATE_CONTROL_REVIEW` with status `NEEDS_INFORMATION`, and `approval: null`;
- five distinct fraud indicators: bypass instructions, urgency, new bank account, supplier-supplied contact, look-alike domain.

The mock model in this case is fault-injected to behave as if the injection worked: it proposes payment. The `model.override_blocked` event shows the policy guard rejecting that proposal. Phone and bank numbers in the evidence snippets are masked.

### A5. FIN-004: missing evidence and tool timeout

```bash
finagent start --case FIN-004 | jq '{status, outcome, primary_exception, next_action}'
finagent get <run_id> --audit | jq '[.audit_events[] | select(.tool=="get_purchase_order") | {attempt, outcome, duration_ms}]'
```

Expect:

- three bounded attempts on the PO lookup, then `HOLD_FOR_INFORMATION` / `MISSING_PO`;
- unknowns that list the PO and the goods receipt;
- the invoice's "goods delivered" wording explicitly *not* accepted as a receipt (FIN-POL-002 s4).

### A6. Crash and resume (FIN-022)

```bash
FINAGENT_CRASH_AFTER_NODE=RECONCILE finagent start --case FIN-022   # simulated crash -> traceback, run left RUNNING
finagent runs --status RUNNING                                       # find it
finagent resume <run_id>                                             # continues from DRAFT -> AWAITING_APPROVAL
finagent get <run_id> | jq '.checkpoints[] | [.seq, .node, .next_node] | @tsv' -r
```

Crashing *after* `SUBMIT_DECISION` and resuming also leaves exactly one ERP decision. `tests/contract/test_orchestration.py::test_crash_and_resume_is_exactly_once` proves it.

### A7. Hold, then new evidence (FIN-038)

```bash
finagent start --case FIN-038                       # HOLD_FOR_INFORMATION / MISSING_PO
finagent evidence <run_id> --po-ref PO-40038 --note "Requester confirms PO-40038"
```

This creates a **new attempt** (`attempt: 2`, `parent_run_id`) that revalidates every control (FIN-POL-007 s5) and reaches `AWAITING_APPROVAL`. The first attempt's result is kept.

### A8. Webhooks

```bash
finagent webhooks listen --port 9099 &              # local receiver; verifies HMAC signatures
finagent webhooks subscribe http://localhost:9099/hooks --events '*'
finagent start --case FIN-007                       # receiver prints run.created, approval.requested (with explanation), ...
finagent webhooks events | jq '.[-3:]'
```

### A9. Audit

```bash
finagent audit <run_id> | jq '.[] | [.ts, .event_type, .node // .tool, .outcome, .duration_ms] | @tsv' -r | head -40
finagent audit-verify                               # {"valid": true, "events": N, "head": "..."}
```

### A10. Evaluation and skill tests

```bash
finagent eval                                       # 42 scenarios, pass/fail, RAG metrics
finagent skills test three_way_match                # 15 fixtures incl. one-cent boundary
finagent skills coverage                            # which skill fired on which scenario
make test                                           # full stable suite
```

### A11. Change the workflow safely

```bash
cp config/workflow.yaml /tmp/wf.yaml
# disable an optional control:
sed -i '' '/skill: payment_scheduling/{n;n;s/enabled: true/enabled: false/;}' /tmp/wf.yaml
finagent workflow validate --file /tmp/wf.yaml      # OK
# try removing a policy-mandatory control:
sed -i '' '/skill: duplicate_detection/d' /tmp/wf.yaml
finagent workflow validate --file /tmp/wf.yaml      # refused: mandatory control 'duplicate_detection' ... is missing
```

### A12. The same flow over HTTP

```bash
make serve                                          # Swagger UI: http://127.0.0.1:8080/docs
scripts/dev/api_walkthrough.sh                      # curl + jq: start, explain, approve twice, outcomes, audit, eval
```

You can also open [`api/finagent.http`](api/finagent.http) in VS Code (REST Client) or JetBrains and click through.

### A13. Switch to a real model (optional)

```bash
ollama pull qwen2.5:3b-instruct
FINAGENT__LLM__PROVIDER=ollama finagent start --case FIN-001 | jq .
FINAGENT__LLM__PROVIDER=ollama pytest -m llm tests/integration      # safety invariants must hold for any model
```

---

### Check the policy baseline

```bash
make policy          # = finagent policy status
```

Shows whether the corpus matches the reviewed baseline. A policy change is a maintenance release (update controls and
tests, `make test eval`, `finagent policy baseline --reviewer <id> --note ...`, restart); see [governance.md](governance.md).

## Part B: code tour (following FIN-001 through the source)

| Step | What happens | Where |
|---|---|---|
| 1 | `POST /runs` validates `ProcessingRequest` (pydantic) and calls the engine | `src/finagent/api/app.py:100`, `src/finagent/schemas.py` (`ProcessingRequest`) |
| 2 | Run row + initial state created (idempotency key honoured), `run.created` audited and emitted | `src/finagent/agent/graph.py:88` |
| 3 | Executor loop: budget check → node → audit → **checkpoint** → optional simulated crash | `src/finagent/agent/graph.py:167` |
| 4 | PARSE_INPUTS: each attachment is routed by the parser registry and validated as `finagent.document/1` | `graph.py:237` → `src/finagent/skills/parsers/registry.py:71` (route), `:94` (parse + contract) |
| 5 | RETRIEVE_POLICY: the pipeline plan from `config/workflow.yaml` picks the skills; each contributes RAG queries | `graph.py:276`, `src/finagent/workflow.py:134` |
| 6 | Retriever: BM25 + SQLite vectors → RRF → access filter → trust re-rank → labelled chunks | `src/finagent/rag/retriever.py:55`, `src/finagent/rag/vectorstore.py` |
| 7 | GATHER_EVIDENCE: tools called only as needed (PO only if PO-backed, FX only if foreign) | `graph.py:295` |
| 8 | Tool runtime: budget → input schema → deny-by-default gate → timeout + retries → output schema → audit | `src/finagent/tools/base.py:110` (gate at `:133`) |
| 9 | RECONCILE: skills run in pipeline order, each seeing prior outputs. Per skill: deterministic script, then the LLM follows `SKILL.md` via `SkillRunner` (validated, caution-only merge) | `graph.py` `run_skills` + `_skill_procedure`; runner `src/finagent/skills/llm_runner.py`; packs `skills/<name>/`; scripts in `src/finagent/skills/processors/controls.py` (e.g. `three_way_match` `:321`, `fraud_indicator_scoring` `:713`, `authority_routing` `:765`) |
| 10 | DRAFT: fenced prompt → model → `validate_draft` (schema, allowed citations) → retry with errors → fallback/fail | `graph.py:399`, `src/finagent/llm/drafting.py:30` (system prompt), `:98` (validator), `src/finagent/llm/providers.py` |
| 11 | DECIDE: most conservative vote; **grounding gate**; model may only add caution; deterministic confidence; typed `FinalResult` | `graph.py:482` (grounding `:507`, model guard `:519`) |
| 12 | APPROVAL_GATE: approval + explanation stored, outcome pool updated, `approval.requested` webhook; run stops | `graph.py:615` |
| 13 | Decision: callback replay check → authority, delegation and SoD checks in a serialised transaction → slot filled → `claim_run` → resume | `graph.py:762` (`decide_approval`), `:801` (`_apply_decision`) |
| 14 | SUBMIT_DECISION: guarded, idempotent simulated posting | `graph.py:680` → `src/finagent/tools/finance_tools.py:165` (guard), `:185` (submit) |
| 15 | Every step above wrote redacted, hash-chained audit events | `src/finagent/audit.py:37` (record), `:93` (verify) |

Then read `config/workflow.yaml` and `config/settings.yaml` (everything tunable) and [extending.md](extending.md) (how to add inputs, sources and controls).
