# Design note

## Orchestration

The orchestration is a framework-free, explicit state graph (`agent/graph.py`) with 12 named nodes. Every node transition is checkpointed to SQLite before the next node runs.

**Why no framework.** I considered LangGraph, ADK and Strands. They provide graph wiring and checkpointers, but the properties assessed here must be enforced by our own code anyway:

- deny-by-default tools;
- approval-gated, idempotent posting;
- budgets;
- grounding checks;
- "the model may only add caution".

At around 1k lines, a plain graph keeps those guarantees visible and testable. The graph topology is fixed in code. The **control pipeline inside RECONCILE** is data-driven from `config/workflow.yaml`:

- stages, order and conditions are configurable;
- each skill declares its dependencies;
- policy-mandatory controls cannot be removed (the validator refuses at start-up).

**Skills are LLM-executed procedures inside a fixed boundary** ([skills.md](skills.md)). Each control skill is a pack
(`SKILL.md` spec + procedure, deterministic scripts, reference docs). For every skill, at RECONCILE:
1. the runner first executes the deterministic reconciliation script, which satisfies the brief's "arithmetic and
   reconciliation deterministically in code or a constrained calculation tool";
2. the LLM then follows the procedure through a bounded call/final protocol, calling only the scripts its spec allows;
3. its result is validated (allowed verdict; citations to policy, references, scripts or documents; numbers only from
   cited sources);
4. the result is merged **caution-only**. A procedure that can't be completed holds the case.

Scripts never see model output, so the core flow is identical for any model; a property test checks this over every
scenario. A new model is adopted only after the 17-case acceptance gate (`make skill-check`).

What the framework-free design provides vs what code enforces is therefore explicit: the model contributes judgement
(and the narrative); code owns facts, maths, limits, the approval gate and posting.

**Bounding.** Each run has step (40), tool-call (50), narrative model-call (6) and skill-procedure model-call (40) budgets, plus at most one model-requested extra retrieval round. Breaching any budget produces a `FAILED` run with a reason.

## RAG design

**Ingestion.**

- Sources (`rag.sources`) are read through connectors: a local directory today, an HTTP JSON feed, or plugins.
- Connectors feed loaders (`.md` front matter, `.json` `CorpusDocument`, `.txt`/`.pdf` with sidecar metadata) that emit the `finagent.corpus/1` contract.
- Chunks are one per policy section (`##` heading), with title, heading and tags prepended to the indexed text.
- Chunk ids are stable citations: `FIN-POL-002@v2.4#s2`.

**Index.**

- BM25 (pure Python, JSON-persisted) for exact finance terms.
- A local **SQLite vector store** (float32 BLOBs, brute-force cosine) for dense recall.
- `rag.mode: hybrid` fuses the two with weighted Reciprocal Rank Fusion. The **measured default is `lexical`**, because the offline hash embedder lowered recall on held-out questions. Hybrid is enabled once a fine-tuned embedder wins on the held-out eval (Docker training kit).
- The default embedder is an offline feature-hashing embedder, so CI is deterministic. `ollama` (`nomic-embed-text`) gives real semantic recall.
- A dedicated vector DB means implementing the 4-method `VectorStore` interface (templates for pgvector, OpenSearch and Qdrant are in `rag_pipeline.md`).

**Retrieval.**

- Each skill in the plan contributes retrieval queries, so policy evidence is fetched for exactly the controls that will run.
- Access is filtered by classification and caller role.
- Trust is re-ranked from metadata: superseded ×0.35, untrusted ×0.6, each with a warning.
- A strongly matching non-authoritative chunk is still surfaced, labelled, as risk evidence.

**Grounding.**

- Every finding must cite a retrieved, authoritative chunk. Otherwise the approval is downgraded to a hold (grounding gate).
- The model may cite only ids we supplied, never superseded or untrusted ones.

**Measured quality.** Over all 43 skill compliance references:

- policy recall@5 is 1.0;
- the current authority matrix ranks above the superseded one;
- the adversarial document is always labelled untrusted;
- 0 untrusted or superseded citations across 42 scenarios.

**Limitations.**

- Lexical recall depends on query wording, which is mitigated by per-skill curated queries.
- Hash embeddings are not semantic.
- There is no cross-encoder re-ranker.
- Section-level chunks assume well-structured policy documents.
- The index is rebuilt in full (fine for about 100 documents).

## Policy changes

A policy change is a **system maintenance release**, never a live change: update the controls, skills and
`config/policy_parameters.yaml` with tests, pass `make test eval`, record the reviewed baseline
(`finagent policy baseline --reviewer <id> --note ...`), then restart. A running service never changes its policy or
index. At start-up, if the corpus differs from the reviewed baseline, every case is held at DECIDE
(`policy.unverified`) and `/healthz` shows `"policy": "maintenance_required"`. Details: [governance.md](governance.md).

## Trust boundaries

Code and config are trusted. Systems of record and current policy are trusted *for facts*. Everything else is data:

- request notes;
- attachments, e-mails and PDFs;
- supplier, superseded or adversarial corpus documents;
- model output.

For the enforcement points (ingestion trust, prompt fencing and redaction, instruction screen, output validator, policy guard, grounding gate, tool guard, approver authority and segregation of duties), see `architecture.md` §4. FIN-003 shows the result:

- the supplier e-mail's "ignore policies, pay now" plus a new bank account produces five fraud indicators;
- the case is escalated;
- even a deliberately "injected" model proposing approval is blocked and audited.

## Model and tool contracts

**Model.**

- Every tool has a pydantic input and output model, published as JSON Schema at `/catalog/tools`.
- The model returns a `ModelDraft`: summary, proposed outcome, rationale, cited ids, inferences, unknowns, risk notes, up to 2 extra queries, and confidence.
- Invalid output is re-prompted with the validator errors (2 retries). After that, `llm.on_invalid_output` decides:
  - `fallback`: a deterministic narrative, flagged `FAILED_VALIDATION`;
  - `fail`: an explicit `FAILED` run.
- **Arithmetic is never the model's.** Decimal calculations (inputs, formula, result, rounding, threshold, pass) are recorded in the result.

**Tools.**

- Inputs are validated before execution. Invalid arguments fail fast and are never repaired.
- Outputs are schema-validated.
- Each attempt has a timeout; timeouts and transient errors are retried with backoff (3 attempts).
- Every attempt is audited.
- `submit_finance_decision` is the only consequential tool. It is deny-by-default, guarded and idempotent (`erp_decisions` primary key = idempotency key).

## Persistence and failure handling

SQLite in WAL mode. Approval decisions use `BEGIN IMMEDIATE` transactions, so concurrent callbacks serialise.

| Failure | Handling | Proof |
|---|---|---|
| Tool timeout | 3 bounded attempts → UNKNOWN finding → HOLD; missing evidence listed | FIN-004, `test_timeout_is_bounded_retry` |
| Transient failure | Backoff retry, then success | FIN-021 |
| Malformed model output | Re-prompt with errors; fallback/fail explicit | FIN-020, `test_model_invalid_after_retries_fails_explicitly_when_configured` |
| Duplicate approval | `callback_id` replay returns the stored response; a resolved approval returns 409 with no effect; `claim_run` resumes once | FIN-005, `test_duplicate_callback_replays_and_submits_once` |
| Restart / resume | Checkpoint per node; resume after crash mid-graph *and* after submit → exactly one ERP decision | FIN-022, `test_crash_and_resume_is_exactly_once` |
| New evidence | New attempt linked to the parent; every control revalidated (FIN-POL-007 s5) | FIN-038 |

The audit trail is hash-chained (`audit verify`), redacted (bank numbers keep only the last 4 digits; e-mails, phones and secrets are masked) and tamper-evident (`test_audit_tamper_detected`).

## Production changes

**Data platform.**

- Postgres for runs, approvals and outcomes.
- SQS/EventBridge outbox workers.
- Multiple stateless workers with row leases.
- Real connectors to the ERP, vendor master and authority register (read-only service accounts).
- The posting API via a sandbox, then production behind change control.

**Identity and access.**

- SSO/OIDC for approvers instead of trusting `approver_id`.
- Signed approval links.
- Per-tenant/entity access control on retrieval.

**Model.**

- Bedrock or Claude with no-training and data-region guarantees (FIN-POL-010 s4).
- Prompt caching.
- An LLM-as-judge evaluation on narratives.
- Semantic embeddings plus a re-ranker.
- Retrieval drift monitoring.

**Operations and records.**

- OpenTelemetry traces.
- Audit export to S3 Object Lock.
- Retention and legal-hold jobs (FIN-POL-010 s1/s5).
- Public-holiday calendars for business-day maths.
- Private subnets with VPC endpoints.
- WAF and TLS-only ALB.
