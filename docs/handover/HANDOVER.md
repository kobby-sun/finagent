# Hand-over

## 1. What this is

`finagent` is a production-minded accounts-payable agent built against `Agentic_AI_Engineer_Take_Home_Test.pdf`.

- It retrieves cited policy evidence and reconciles invoice, PO, receipt, vendor and history data with deterministic code.
- It recommends one of the FIN-POL-001 s3 outcomes and **stops for human approval** before any consequential decision.
- It resumes the same run on approve or reject, with a hash-chained audit trail.
- **Posting is simulated.** Nothing can move money.

## 2. State at hand-over

| Item | Status |
|---|---|
| Required cases FIN-001..FIN-005 | passing |
| Edge-case, combined, judgement and noisy-input scenarios FIN-006..FIN-080 | 80/80 scenarios, 1,411/1,411 checks passing (`finagent eval`) |
| Skills | every skill runs its deterministic script; 4 judgement skills ask fixed questions (small model answers, large model second opinion on concerns, code decides, caution only) ([skills.md](../skills.md)) |
| Stable test suite | all passing (`make test`): unit, tool/orchestration contracts, API, skill fixtures, scenarios, policy freshness, policy baseline |
| Policy changes | system maintenance releases (tests + reviewed baseline + restart), never live; unreviewed corpus changes hold every case. Baseline **not yet reviewed by a finance owner** ([governance.md](../governance.md)) |
| Model-dependent tests | `tests/integration` (marker `llm`), not run in CI. They need Ollama, the Claude API or Bedrock. |
| RAG quality | policy recall@5 = 1.0 over 43 skill compliance references; 0 untrusted or superseded citations |
| Docker image | builds and serves (`docker build .`; smoke-tested `/healthz` and `POST /runs`) |
| Terraform | `fmt` + `validate` clean; **not applied** (no AWS account used) |
| Embedding model | fine-tuned `bge-small` shipped and used by hybrid search (accuracy gate: never below BM25) |
| Local narrative LLM | dataset + LoRA/Ollama recipe documented; **no narrative model trained in this submission** |

## 3. Where to start reading

1. [`docs/architecture.md`](../architecture.md): diagrams, manifest, trust boundaries.
2. [`docs/design_note.md`](../design_note.md): 1–2 page rationale.
3. `src/finagent/agent/graph.py`: the state graph, decision rules and approval logic.
4. `src/finagent/skills/processors/controls.py`: every control, each referencing its policy section.
5. `config/workflow.yaml` and `config/settings.yaml`: everything tunable.
6. [`docs/scenarios/README.md`](../scenarios/README.md): what each case proves, with live results.

## 4. Repository map

```
config/            settings.yaml (provider/model/RAG/budgets), workflow.yaml (skill pipeline)
src/finagent/      agent/ (graph, calc)  rag/ (ingest, index, retriever, vectorstore)  skills/ (parsers, processors, harness)
                   tools/ (runtime, finance tools, mock backend)  llm/ (providers, drafting/validation)  api/ (FastAPI)
                   store.py audit.py webhooks.py workflow.py contracts.py governance/ evaluation.py cli.py
skills/<name>/     SKILL.md (spec + fixed questions), references/ (policy.md injected + external docs), tests/
mock_data/         vendors, POs+receipts, invoice history, FX, authority register, faults, 42 cases, scenarios.json
finance_rag_corpus/  policy corpus (given)
contracts/         JSON Schemas for all data contracts
plugins/           example extension plugin
tests/             unit / contract / skills / scenarios / integration(llm)
scripts/           dev/, aws/, training/, generators (mock data, fixtures, docs, openapi)
infra/terraform/   AWS IaC
training/          local-LLM dataset + Modelfile
docs/              architecture, design note, env, commands, scenarios, handover, api, rag, audit, extending, samples
```

## 5. Operating it

- **Runbook:** [runbook.md](runbook.md).
- **Commands:** [../commands/workflow.md](../commands/workflow.md), [../commands/cloud_deployment.md](../commands/cloud_deployment.md).
- **Environments:** [../env/dev.md](../env/dev.md), [../env/aws.md](../env/aws.md).

## 6. Real vs simulated

| Integration | Status |
|---|---|
| RAG retrieval (BM25 + SQLite vectors over the corpus) | **Real** (local) |
| Model | `mock` (default, deterministic, offline). **Real** when configured: `ollama` (local), `anthropic` (Claude API, needs a key), `bedrock` (AWS) |
| Vendor master, PO/receipts, invoice history, FX rates, authority register | **Simulated** from `mock_data/*.json`, with fault injection (`faults.json`) |
| `submit_finance_decision` (posting / rejection) | **Simulated** ERP sandbox table `erp_decisions`; references `SIM-POST-…` / `SIM-REJECT-…` |
| Webhooks (outbound HTTP, inbound signed callbacks) | **Real** HTTP |
| AWS infrastructure | **Real IaC**, not applied |
| PDF parsing | **Real** (pypdf); scanned PDFs flagged for OCR |

## 7. Known limitations and deliberate trade-offs

See [known_limitations.md](known_limitations.md).

## 8. Suggested next steps (priority order)

1. **Approver identity.** Replace the `approver_id` field with OIDC/SSO identity on the decision endpoint (the authority checks already exist).
2. **Persistence.** Move to Postgres with row leases; scale to N workers; drain the outbox through SQS.
3. **Real connectors.** Read-only service accounts for the vendor master, ERP and authority register; the posting API via the ERP sandbox.
4. **Semantic recall.** Turn on Ollama/Bedrock embeddings plus a cross-encoder re-ranker. Track recall with `finagent eval` in CI.
5. **Local model.** Run the training recipe, then `scripts/training/eval_local_model.sh`. Adopt only if the safety invariants hold.
6. **Business days.** Public-holiday calendar for business-day maths (FIN-POL-006 s2, FIN-POL-007 s4).
7. **Split-invoice detection.** Aggregate same-PO invoices within a window for authority (FIN-POL-003 s1).
8. **Policy sign-off.** A finance owner reviews the interpretations in docs/governance.md, then re-baselines with `finagent policy baseline --reviewer <id> --note ...`.

## 9. AI-tool usage declaration

This repository was produced with an AI coding assistant (Claude Code), directed by the candidate.

- **Generated with AI assistance:** code, tests, mock data, Terraform and documentation.
- **Reviewed and run:** every number quoted in the docs (tests, scenarios, recall) comes from actually running the commands in this repository.
- **Prompts:** the spin-up prompts are recorded in [`docs/PROMPTS.md`](../PROMPTS.md) (reusable spin-up prompt).
- **Third-party libraries:** pydantic, FastAPI, uvicorn, httpx, PyYAML, pytest, pypdf (all pinned in `requirements.lock`).
- **Not authored here:** the corpus in `finance_rag_corpus/` was supplied with the brief.
- **Time:** record the actual time spent here before submission.
