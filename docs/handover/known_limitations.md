# Known limitations and deliberate trade-offs

| Area | Limitation | Why / mitigation |
|---|---|---|
| Approver identity | `approver_id` is taken from the request body; there is no SSO | POC scope. Authority, delegation and segregation-of-duties checks are real. Production: OIDC identity bound to the decision. |
| Persistence | SQLite + single task | Demonstrates checkpoint/resume, idempotency and serialised approvals. Production: Postgres + leases. |
| Systems of record | Vendor, PO, history, FX and authority data are mock JSON | Contracts are explicit; swap implementations behind the same tools. Fault injection exercises timeouts and transient errors. |
| Business days | Weekends only, no public holidays | Used for bank-change hold, SoD window, review dates and payment runs. Add a holiday calendar. |
| Split invoices | No aggregation across invoices for authority | Duplicate/fuzzy matching covers re-submissions only. Planned next step. |
| Repeated round-dollar / weekend manual-payment fraud indicators | Partially implemented (language patterns only) | Other FIN-POL-005 s3 indicators are covered. |
| Credit-note application | Validates and posts the credit; does not allocate against specific open invoices | FIN-POL-008 s2 guidance is returned as the next action. |
| Accruals / month-end (FIN-POL-011), write-offs, refunds | Not implemented | Out of scope for invoice processing; corpus still retrieved where relevant. |
| Tax | GST check uses the PO's implied rate (tax/subtotal) | No jurisdictional tax engine. |
| Embeddings | Fine-tuned `bge-small` (hybrid with BM25) is the default; tests use lexical or the shipped model | Falls back to BM25 automatically if the model is missing. `VectorStore` adapter for a dedicated DB. |
| Policy changes | Never live: a maintenance release (code / config + tests, re-baseline, restart) | Until the release is done, a changed corpus holds every case. |
| Invoice channels | Cases enter through `POST /runs` (or the CLI) in our own `ProcessingRequest` format only. No feed adapters, drop-folder, SFTP or cloud-drive poller | By decision (POC scope). Another system can call `POST /runs` with an `Idempotency-Key`, or sit behind a small mapping service. |
| Policy baseline | Produced by the build, not reviewed by a finance owner | Review the docs/governance.md interpretations, then `finagent policy baseline --reviewer <id> --note ...`. |
| Retrieval | Curated per-skill queries; no re-ranker | Recall measured at 1.0 for the policy sections the controls depend on. |
| Model role | Answers fixed judgement questions (4 skills) + narrative; caution only | Deliberate: maths, reconciliation, counting, limits and posting stay in code (brief); code decides what an answer means. An answer can't clear a false hold from a script; that needs a person. |
| Only what is asked | The model judges only what the questions cover | A catch-all "anything else?" question raises a warning only. New judgement needs a new question in `SKILL.md`. |
| Mock model | Safe answer to every question; scripted answers for FIN-064..072 | Keeps tests deterministic. Real-model judgement is measured with `make skill-check`; not run in CI. |
| Speed | 3-6 small-model calls per case; the large model only on concerns | See the gate report (`data/eval/model_gate/`) for seconds per case. |
| Service needs models | The running service uses Ollama `qwen2.5:7b-instruct` + `qwen2.5:14b-instruct` | Not running or not pulled → cases are held (visible in `/healthz`). Demo mode: `FINAGENT__SKILLS__LLM__USE_MOCK=true`. |
| Confidence | Heuristic (unknowns, grounding, model validity) | Explainable, not calibrated. |
| PDF | Text PDFs only; scanned PDFs flagged | OCR would be a new parser plugin. |
| Clock | Frozen at 2026-11-12 by default so mock data is consistent | `FINAGENT__APP__FIXED_NOW=null` for wall-clock time. |
| Terraform | Validated, not applied; public subnets, no NAT | Cost-optimised POC; hardening list in `docs/env/aws.md`. |
| Local LLM | Dataset + recipe only; no trained weights shipped | Training needs a GPU / Apple Silicon and model licences. |
| Timebox | The brief asks for about 8 hours | Optional extensions chosen: fault-injection tests (via `faults.json`) and a run timeline (checkpoints + audit). |
