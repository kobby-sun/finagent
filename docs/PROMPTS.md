# Reusable spin-up prompt

One compact prompt that captures everything this project was built from. Fill in the `<…>` placeholders and paste it into an AI coding assistant to spin up the next agent project. It works for AP invoices, claims, onboarding or any document-driven approval workflow.

```text
Build a production-minded, document-driven RAG workflow agent in <Python>.

Inputs
- Requirements: <path/to/requirements.pdf> - follow it strictly; it wins on any conflict.
- Compliance corpus: <path/to/corpus/> - index it for RAG and inject each skill's governing
  policy sections into that skill (references/policy.md, validated against by the skill's procedure).
- Domain: <e.g. accounts-payable invoice review>; consequential action: <e.g. posting a payment>.

Core behaviour
- Bounded, checkpointed state graph (no free-form agent loop): validate request -> parse inputs ->
  retrieve cited policy -> call tools only when needed -> deterministic reconciliation -> model drafts
  narrative -> decide -> STOP for human approval before any consequential action -> resume the same
  run on approve/reject. Step/tool/model budgets; explicit FAILED states.
- Code decides, the model explains: decimal arithmetic and rules in code; model output is
  schema- and citation-validated, retried/repaired, and may only make outcomes MORE conservative.
- Safety: retrieved and case text are untrusted data (screen for injected instructions); trust
  labels from metadata; grounding gate; deny-by-default, approval-gated, idempotent consequential tool
  (simulated - nothing real moves); approver authority + segregation-of-duties checks; duplicate
  callbacks replay-safe; crash/resume exactly-once.

Architecture to deliver
- Skills as packs with ONE contract: SKILL.md (validated spec + fixed questions; code asks, model answers, code decides),
  deterministic scripts (the brief's reconciliation/arithmetic), references (injected policy +
  external docs). One runner for every skill: script first, then a bounded call/final protocol,
  validated (allowed verdicts, citations, numbers only from cited sources), merged caution-only;
  failure holds the case. Parsers stay code. Pipeline order in a YAML workflow (re-order / cut /
  add) with mandatory controls protected. Script fixtures + runner tests + judgement scenarios
  where scripts alone would approve; a real-model check script (local Ollama or hosted).
- Versioned, extensible data contracts (request, document, corpus) so new file types and corpus
  sources plug in as registrations; namespaced extensions; provenance on everything.
- RAG: section chunks with stable citation ids, BM25 + embeddings in local SQLite behind a
  VectorStore interface (adapter path to pgvector/OpenSearch/Qdrant); access filter; trust re-rank;
  measured recall on a hand-written held-out question set.
- Outcome pool, REST API with Swagger/OpenAPI, signed webhooks per status, approval requests with
  full explanations; CLI mirroring every operation.
- Hash-chained, redacted audit trail for every operation.
- Policy changes are system maintenance, never live: update controls/config with tests, re-baseline
  (`finagent policy baseline --reviewer <id>`), restart. At start-up the index is rebuilt if the corpus
  changed, and the search model is retrained (gated) or BM25 is used alone; if the corpus differs from
  the reviewed baseline, every case is held. Decisions and postings re-verify cited policy.
- Model adapter: deterministic mock (default, fault-injectable) + local (Ollama) + hosted providers,
  configured outside the orchestration code.
- Local-model kit: SFT dataset + LoRA recipe for the narrative model; embedding fine-tuning kit
  evaluated on held-out questions; all runnable in local Docker.

Data, tests, environments, docs
- Mock data covering every input type, all required scenarios and edge cases, plus fault injection
  (timeouts, transient errors, malformed model output, crashes).
- Tests: unit, contract, API, skill fixtures, end-to-end scenarios (stable), and model-dependent
  integration tests kept separate. Report real numbers only.
- Local: setup script, pinned deps, Dockerfile + docker compose. Cloud: <AWS> Terraform
  (validated), deploy/rollback/smoke/destroy scripts, cost and cleanup notes.
- Docs: README (real vs simulated), architecture (diagrams + PDF), full API reference PDF generated
  from OpenAPI, one-page-per-scenario workflow PDF, design note, env (dev/cloud),
  command references, one doc per scenario, hand-over (runbook, limitations), extending guide,
  walkthrough (hands-on + code tour).

Constraints: no secrets or personal data in the repo; synthetic data only; state assumptions and
limitations; keep prompts and generated artefacts reproducible (make targets).
```

Example fill-in for this repo:

- Requirements: `Agentic_AI_Engineer_Take_Home_Test.pdf`
- Corpus: `finance_rag_corpus/`
- Domain: accounts-payable invoice review
- Consequential action: posting or rejecting an invoice
- Language: Python
- Cloud: AWS
