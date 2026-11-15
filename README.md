# finagent: Financial Processing RAG Workflow Agent

`finagent` is an internal accounts-payable assistant. For each invoice-processing request it:

- retrieves **cited** finance-policy evidence (RAG);
- reconciles invoice, PO, receipt, vendor and history data with **deterministic** code;
- detects mismatches, duplicates, missing approvals and fraud indicators;
- produces a typed recommendation;
- **stops for human approval** before posting or rejecting, then resumes the same run safely.

Every step is checkpointed and recorded in a hash-chained, redacted audit trail.

> Built for `Agentic_AI_Engineer_Take_Home_Test.pdf`. **No real money can move**: posting is a simulated ERP sandbox.

## Quick start: the whole environment in one command (Docker)

Needs only **Docker** (Docker Desktop on macOS / Windows, Docker Engine + compose on Linux) and Python 3 to run the
launcher (standard library only, no setup). Everything else is in the repository, including the trained search model.

```bash
git clone https://github.com/kobby-sun/finagent.git
cd finagent
make up                  # or: make up MODELS=mock   (no LLM download, starts in seconds)
```

| macOS / Linux | Windows (PowerShell) | Any OS |
|---|---|---|
| `make up` | `.\dev.ps1 up` | `python scripts/dev/docker_up.py` |

It picks where the two skill models run, pulls any that are missing, builds and starts the agent, waits until it is
healthy, then prints what it is using (search mode, policy check, models). Choose the models with
`MODELS=…` (make) or `--models …` (Windows / Python):

| Option | Models | Notes |
|---|---|---|
| `auto` (default) | `host` if Ollama is running on this machine, else `docker` | |
| `host` | Ollama on this machine (`qwen2.5:7b-instruct` + `qwen2.5:14b-instruct`, pulled if missing) | fastest on a Mac: Ollama in Docker has no GPU there |
| `docker` | an Ollama container; models pulled into a Docker volume | first run downloads ~14 GB |
| `mock` | none: deterministic answers | quick demo; same as the test suite |

Then:

| Goal | macOS / Linux | Windows |
|---|---|---|
| Open the API | http://127.0.0.1:8080/docs (Swagger, ready-made examples) | same |
| Submit the scenario cases | `make docker-load` (`CASES="FIN-001 FIN-003"`, `APPROVE=1` to replay approvals) | `.\dev.ps1 docker-load FIN-001` |
| Every test inside the container | `make docker-test` | `.\dev.ps1 docker-test` |
| All 80 scenarios inside the container | `make docker-eval` | `.\dev.ps1 docker-eval` |
| Stop / remove data and pulled models | `make down` / `make down-clean` | `.\dev.ps1 down` / `down-clean` |

Without Docker, `make bootstrap` (Windows `.\dev.ps1 bootstrap`) sets up a local Python environment instead; see below.

## Results at a glance

| | |
|---|---|
| Required cases FIN-001..005 | ✅ all pass |
| Scenarios (40 edge + 21 combined + 9 judgement + 5 noisy-input cases) | **80/80**, 1,411/1,411 checks (`finagent eval`): outcome, exit step, tools, warnings, audit events, reasoning |
| Stable tests | **352 passing** (`make test`): unit, contract, API, 98 skill fixtures, skill packs + runner, all scenarios, search-accuracy gate, policy freshness and start-up checks |
| RAG | **semantic search on** (hybrid BM25 + fine-tuned embedder): curated recall 43/43, held-out R@5 0.90 vs 0.71 BM25, never below BM25 on any measure (accuracy gate); 0 untrusted citations; index and search model checked against the corpus at start-up |
| Real models | stock `qwen2.5:7b` + `qwen2.5:14b` (Ollama): gate **22/22**, **22/22 with maximum noise**, **70/70** scenarios; approved pair. Code only: 71/80 (the 9 judgement cases need the model) |
| Skills | **code asks, the model answers, code decides**: every skill runs its deterministic script; 4 judgement skills ask fixed questions answered by a small local model; a larger model gives a second vote unless the small one is ≥ 99% sure all is clear (caution only); judgement scenarios FIN-064..072 catch cases the scripts alone would approve ([docs/skills.md](docs/skills.md)) |
| Policy changes | **maintenance releases, never live**: start-up rebuilds the index, retrains the search model if needed, and holds every case if the policy text differs from the verified baseline (`make policy`) |
| Container | `docker build .` → `/healthz`, `POST /runs` verified |
| AWS IaC | Terraform `validate` clean (not applied) |

## Local dev: control the service and load test data

### What `make` is

`make` is a standard command-line tool that runs named shortcuts defined in the [`Makefile`](Makefile) at the repo root.

- `make start` simply runs `scripts/dev/ctl.sh start`; `make test` runs `pytest`.
- It saves remembering long commands. Nothing magic happens: each target is one line you could type yourself.
- It is preinstalled on macOS and most Linux distributions. **Windows doesn't have it**, so use the equivalent commands below. They call the same cross-platform controller, [`scripts/dev/devctl.py`](scripts/dev/devctl.py).

### Same commands on every OS

| Goal | macOS / Linux | Windows (PowerShell) | Any OS (plain Python) |
|---|---|---|---|
| **New machine: everything in one command** (setup → all tests → all scenarios + test history → start → load cases) | `make bootstrap` (`TRAIN=1` also trains the search model) | `.\dev.ps1 bootstrap` (`--train`) | `python scripts/dev/devctl.py bootstrap` |
| One-time setup | `make setup` | `.\dev.ps1 setup` | `python scripts/dev/devctl.py setup` |
| Start the service | `make start` | `.\dev.ps1 start` | `python scripts/dev/devctl.py start` |
| Stop / restart | `make stop` / `make restart` | `.\dev.ps1 stop` / `restart` | `… devctl.py stop` / `restart` |
| Status / logs | `make status` / `make logs` | `.\dev.ps1 status` / `logs -f` | `… devctl.py status` / `logs -f` |
| **Whole environment in Docker** (models + agent, one command) / stop | `make up` (`MODELS=host\|docker\|mock`) / `make down` | `.\dev.ps1 up` (`--models …`) / `down` | `python scripts/dev/docker_up.py` / `… devctl.py down` |
| Tests / scenarios / load cases **inside the container** | `make docker-test` / `docker-eval` / `docker-load` | `.\dev.ps1 docker-test` / `docker-eval` / `docker-load` | `… devctl.py docker-test` / … |
| Docker wipe data | `make down-clean` | `.\dev.ps1 down-clean` | `… devctl.py down-clean` |
| Regenerate test data + index | `make seed` | `.\dev.ps1 seed` | `… devctl.py seed` |
| Load all cases | `make load` | `.\dev.ps1 load` | `… devctl.py load` |
| Load some cases | `make load CASES="FIN-001 FIN-003"` | `.\dev.ps1 load FIN-001 FIN-003` | `… devctl.py load FIN-001 FIN-003` |
| Load + approve | `make load-approve CASES="FIN-005"` | `.\dev.ps1 load-approve FIN-005` | `… devctl.py load-approve FIN-005` |
| Webhook approval demo (one command) | `make webhook-demo` | `.\dev.ps1 webhook-demo` | `… devctl.py webhook-demo` |
| Approval inbox (you approve via webhooks) | `make inbox` | `.\dev.ps1 inbox` | `… devctl.py inbox` |
| Draw a diagram of one run (PDF / PNG / SVG) | `make diagram RUN=<run_id> FMT=png` | `.\dev.ps1 diagram <run_id> --format png` | `finagent diagram <run_id> --format png pdf` |
| Run all scenarios **and** draw each one | `make eval-diagrams FMT=png` | `.\dev.ps1 eval-diagrams png` | `finagent eval --diagrams png` |
| **Model acceptance gate** before switching models (17-case conformance set, PASS/FAIL) | `make skill-check` (`PROVIDER=ollama MODEL=qwen2.5:7b-instruct`) | `.\dev.ps1 skill-check` | `… devctl.py skill-check` |
| Policy matches the verified baseline? | `make policy` | `.venv\Scripts\finagent policy status` | `finagent policy status` |
| **Re-train after adding policy docs / skills** | `make retrain` | `.\dev.ps1 retrain` | `… devctl.py retrain` |
| Scaffold a new skill | `make new-skill NAME=my_check POLICY="FIN-POL-013 s2"` | `py scripts\skills\new_skill.py my_check --policy FIN-POL-013 s2` | same |
| Tests / evaluation / demo | `make test` / `make eval` / `make demo` | `.\dev.ps1 test` / `eval` / `demo` | `… devctl.py test` / `eval` / `demo` |
| Wipe local data | `make reset` | `.\dev.ps1 reset` | `… devctl.py reset` |

Notes:

- **Windows Command Prompt:** use `dev start`, `dev load FIN-001` (via `dev.cmd`).
- **PowerShell blocks scripts?** Run `powershell -ExecutionPolicy Bypass -File .\dev.ps1 start`.
- **The `finagent` CLI** itself is `.venv/bin/finagent` on macOS/Linux and `.venv\Scripts\finagent.exe` on Windows (or `python -m finagent.cli`).

### Prerequisites by OS

| OS | Install |
|---|---|
| macOS | `brew install python@3.13` (make and curl are built in). Optional: Docker Desktop |
| Linux (Debian/Ubuntu) | `sudo apt install make curl python3 python3-venv python3-pip` (Python ≥ 3.11). Optional: Docker Engine + compose plugin |
| Linux (Fedora/RHEL) | `sudo dnf install make curl python3 python3-pip`. Optional: Docker or Podman with compose |
| Windows 10/11 | Python 3.11+ from python.org or `winget install Python.Python.3.13` (tick "Add to PATH"). Optional: Docker Desktop. No `make` needed. |
| Docker only (any OS) | Docker with compose, plus Python 3 for the launcher. Run `make up` / `.\dev.ps1 up` / `python scripts/dev/docker_up.py` (see Quick start) |

### Local AI model (used by the running service)

The service answers the skills' judgement questions with **two real local models**:
- a **small** one for every question: Ollama `qwen2.5:7b-instruct`, about 5 GB;
- a **large** one as a second vote, unless the small one is at least 99% sure everything is clear: `qwen2.5:14b-instruct`, about 9 GB.
  It is swappable (a bigger local model, Claude, or `none`).

Install them once:

```bash
ollama serve &                        # Windows: start the Ollama app
ollama pull qwen2.5:7b-instruct && ollama pull qwen2.5:14b-instruct
```

`GET /healthz` → `skill_questions` shows whether the models are reachable and whether the pair is approved. Without
them, cases are **held** ("judgement questions not answered"), never passed.

**In Docker:** `make up` handles this (Ollama on your machine if it is running, else an Ollama container with the
models pulled into it; `MODELS=mock` for none). See Quick start.

Fast demo mode without a model: `FINAGENT__SKILLS__LLM__USE_MOCK=true make start`. Tests, `finagent eval` and
`make bootstrap` always use the deterministic mock.

### Run the service

| Goal | Local (Python venv) | Docker |
|---|---|---|
| Start | `make start` | `make up` |
| Stop | `make stop` | `make down` |
| Restart | `make restart` | `make down && make up` |
| Status / health | `make status` | `make dstatus` |
| Follow logs | `make logs` | `make dlogs` |
| Wipe all data | `make reset` | `make down-clean` |

Both modes serve **http://127.0.0.1:8080**:

- Swagger UI at `/docs`. Every endpoint has ready-made test examples; see [Swagger testing guide](docs/api/swagger_testing.md).
- Health at `/healthz`.

### Load test data and cases

| Goal | Command |
|---|---|
| (Re)generate mock data + skill fixtures + policy index | `make seed` |
| Submit **all 42 scenario cases** to the running service | `make load` |
| Submit selected cases | `make load CASES="FIN-001 FIN-003"` |
| Submit cases **and** replay their approval decisions | `make load-approve CASES="FIN-005 FIN-012"` |
| See what is waiting for approval | `curl -s localhost:8080/approvals?status=PENDING` or `finagent approvals --explain` |

`make load` prints a table of each case's run id, status, outcome and approval id. Cases live in `mock_data/cases/<CASE>/` (request + attachments). The full list with expected results is in [docs/scenarios](docs/scenarios/README.md).

### Test

| Goal | Command |
|---|---|
| All stable tests (unit, contract, API, skill fixtures, scenarios) | `make test` |
| Scenario evaluation report | `make eval` (or `finagent eval --cases FIN-001 FIN-003`) |
| One skill's tests | `finagent skills test three_way_match` |
| Guided demo (approval flow + exception flow) | `make demo` |
| curl walkthrough against the running API | `scripts/dev/api_walkthrough.sh` |

### Test the approval-required webhook flow

`make webhook-demo` (Windows: `.\dev.ps1 webhook-demo`) runs the round trip below. The local approval inbox:

1. receives signed `approval.requested` webhooks;
2. picks valid approvers for every required slot;
3. answers through the signed inbound callback, sending each answer twice to prove replay safety;
4. shows `decision.submitted` and `run.completed` coming back.

`make inbox` lets you make the decisions yourself. Guide: [docs/api/webhook_testing.md](docs/api/webhook_testing.md).

### Add policy documents or skills, then re-train (one command)

1. **New policy document:** drop a Markdown file with front matter (`document_id`, `version`, `status: current`, …) into `finance_rag_corpus/`. Policy changes are maintenance releases: update the controls and tests, re-baseline, restart (see the next section). Other formats and API feeds: [docs/extending.md](docs/extending.md).
2. **New skill:** `make new-skill NAME=my_check POLICY="FIN-POL-013 s2"` creates a plugin template plus a fixtures file. Implement it, fill in the fixtures, and add the printed snippet to `config/workflow.yaml`.
3. **Held-out questions:** add 1–2 realistic questions per new policy section to `training/embeddings/natural_questions.yaml`. `retrain` lists any sections still missing.
4. Run **`make retrain`** (Windows: `.\dev.ps1 retrain`). It:
   1. validates the workflow and runs every skill's tests;
   2. rebuilds the policy index and the skills' injected compliance;
   3. reports held-out coverage gaps;
   4. regenerates both training datasets;
   5. retrains the embedding model (Docker by default; `EMB=host` or `EMB=skip`);
   6. evaluates on held-out questions and all scenarios;
   7. **keeps the new model only if it is at least as good** as the previous one. The old model is archived, then restored if the new one is worse.

   A summary is written to `training/retrain_report.json`.

### Policy changes are maintenance releases (checked at start-up)

A running service never changes its policy. A policy change is a reviewed release:

1. edit `finance_rag_corpus/`;
2. update the controls, skills and `config/policy_parameters.yaml`, with tests;
3. run `make test eval`;
4. run `finagent policy baseline --reviewer <id> --note "<why>"` (Financial Controller / CFO only; recorded in the audit trail);
5. restart.

At start-up the service:

| Checks | If it doesn't match |
|---|---|
| index vs corpus files | rebuilds the BM25 index and vectors |
| search model vs corpus (`models/embedder/trained_on.json`) | runs the gated retrain; if that isn't possible, uses keyword search, never an unverified model |
| policy text vs `config/policy_baseline.yaml` | **holds every case** until maintenance is done; `/healthz` shows `maintenance_required` |

```bash
make policy                                   # = finagent policy status
finagent policy params                        # every policy number the controls enforce, with its source section
```

Each run records the baseline it used (`result.policy`). Details and the interpretations to review are in
**[docs/governance.md](docs/governance.md)**.

### Draw what happened in a run (PDF / PNG / SVG)

Every run can be drawn as a one-page diagram. It shows:

- the path through the workflow, with the **exit step** outlined;
- each evidence lookup (attempts, failures, skipped);
- each control's verdict;
- the decision chain (rules → model → guard → final);
- approvals and refusals;
- all warnings, exceptions, unknowns and notable audit events.

| Where | How |
|---|---|
| Live run | `finagent start --case FIN-044 --diagram png pdf` · `finagent diagram <run_id> --format png` · Swagger `GET /runs/{run_id}/diagram?format=png` |
| Every run automatically | set `app.run_diagrams: [svg]` (or `FINAGENT__APP__RUN_DIAGRAMS`) → `data/diagrams/` |
| Test runs | `make eval-diagrams FMT=png` → `data/eval/diagrams/<CASE>_<PASS/FAIL>.png` |
| Loaded cases | `python scripts/dev/load_cases.py FIN-001 FIN-044 --diagrams png,pdf` |

### See the reasoning behind every step

`finagent explain <run_id>` (or `GET /runs/{run_id}/reasoning`) lists every key step: who decided (system, a skill, the model or a human), what they decided, and **why**. It includes skipped controls, tool calls, each skill's verdict, how the outcome was derived, model proposals that were blocked, approvals and refused approval attempts. Rejections must include a reason.

### Approve or reject from the command line

```bash
finagent approvals --status PENDING --explain                          # pick an approval_id
finagent approve <approval_id> --approver u-dd-01 --callback-id cb-1    # test approvers are listed in docs/api/swagger_testing.md
finagent reject  <approval_id> --approver u-ccm-01
```

More commands: [docs/commands/workflow.md](docs/commands/workflow.md). Guided tour: [docs/walkthrough.md](docs/walkthrough.md).

## Environment choice

- **Local first.** Everything runs offline with the deterministic `mock` model, so reviewers get reproducible results with zero setup or cost.
- **Real models** are a config switch: `ollama` (local), `anthropic` (Claude API) or `bedrock`.
- **AWS** is provided as Terraform: ECS Fargate, ALB, EFS, ECR, S3, Secrets Manager, Bedrock IAM, CloudWatch and an EventBridge Scheduler task.

Details: [docs/env/dev.md](docs/env/dev.md) · [docs/env/aws.md](docs/env/aws.md)

## Commands

| Logical operation | CLI | HTTP |
|---|---|---|
| Start run | `finagent start --case FIN-001` | `POST /runs` |
| Get run | `finagent get <run_id> [--audit]` | `GET /runs/{run_id}` |
| Approve / reject | `finagent approve|reject <approval_id> --approver <user> --callback-id <id>` | `POST /approvals/{id}/decision` · signed `POST /webhooks/inbound/approval` |
| List evaluation results | `finagent eval` | `POST /evaluations/run` · `GET /evaluations/latest` |

Full reference: [docs/commands/workflow.md](docs/commands/workflow.md) · cloud changes: [docs/commands/cloud_deployment.md](docs/commands/cloud_deployment.md) · API: [docs/api.md](docs/api.md), [OpenAPI](docs/api/openapi.yaml), [request collection](docs/api/finagent.http)

## Model and API requirements

| Provider | Needs | Config |
|---|---|---|
| `mock` (default) | nothing | deterministic, fault-injectable |
| `ollama` | Ollama + `qwen2.5:14b-instruct` (configured default; ~9 GB) - narrative model | `FINAGENT__LLM__PROVIDER=ollama` (skill questions: `skills.llm.small` / `skills.llm.large`) |
| `anthropic` | `pip install anthropic`, `ANTHROPIC_API_KEY` | model in `llm.providers.anthropic.model` (default `claude-opus-5-5`) |
| `bedrock` | AWS credentials + Bedrock model access | `llm.providers.bedrock.{region,model}` |

The provider and model live in `config/settings.yaml` or environment variables, never in orchestration code.

## Real vs simulated

| Integration | Status |
|---|---|
| RAG retrieval: BM25 + **local SQLite vector store** over `finance_rag_corpus/` | **Real** (local) |
| LLM | `mock` is simulated; `ollama`, `anthropic` and `bedrock` are **real** (external access) |
| `get_vendor_record`, `get_purchase_order`, `check_invoice_history`, `get_fx_rate`, `get_authority_register` | **Simulated** (`mock_data/`, with fault injection) |
| `submit_finance_decision` | **Simulated** ERP sandbox (approval-gated, deny-by-default, idempotent) |
| Webhooks (outbound, inbound signed) | **Real** HTTP |
| AWS | **Real** Terraform, not applied |

## How it works

```
RECEIVE -> PARSE_INPUTS -> RETRIEVE_POLICY -> GATHER_EVIDENCE -> RECONCILE -> DRAFT -> DECIDE
        -> APPROVAL_GATE (stop: AWAITING_APPROVAL) -> SUBMIT_DECISION -> FINALIZE
        -> HOLD_FINALIZE (stop: NEEDS_INFORMATION, resumable with evidence)
```

- **Skills: code asks, the model answers, code decides.**
  - Every skill runs its **deterministic script**: exact maths, matching and limits, as the brief requires.
  - 4 judgement skills (`non_po_validation`, `three_way_match`, `instruction_screen`, `payment_scheduling`) then ask
    **fixed questions** from their `SKILL.md`. Code collects the evidence, and the **small model** picks an allowed
    answer plus a verbatim quote. Code checks the quote exists, then maps the answer to a verdict.
  - Only when the small model raises a concern (or fails) does the **large model** answer the same questions; its
    answers decide.
  - **Code counts** fraud indicators from scripts and answers (two or more → escalate).
  - Answers can only **add caution** (warning, hold, escalate), never approve. Unanswered questions **hold** the case.
  - See [docs/skills.md](docs/skills.md).
- **Model vs code.** Code does all arithmetic and reconciliation and owns the approval gate. The models answer fixed
  judgement questions and explain (narrative). Every model output is schema- and citation-validated and can only make the
  outcome more cautious.
- **Pipeline.** `config/workflow.yaml` orders the controls. Policy-mandatory controls cannot be removed.
- **Policy changes are maintenance releases.** The controls read every policy number from `config/policy_parameters.yaml`. At start-up the index and search model are checked against the corpus, and cases are held if the policy text differs from the verified baseline. See [docs/governance.md](docs/governance.md).
- **Data contracts** are versioned (`ProcessingRequest`, `finagent.document/1`, `finagent.corpus/1`), so new file types and corpus sources plug in as registrations.

Read next:

- [Walkthrough](docs/walkthrough.md): 20-minute hands-on tour + code tour
- [Architecture](docs/architecture.md) ([PDF diagrams](docs/diagrams/architecture.pdf)): diagrams, manifest, trust boundaries
- [Design note](docs/design_note.md)
- [RAG pipeline](docs/rag_pipeline.md)
- [Extending](docs/extending.md)
- [Skills](skills/README.md)
- [Audit](docs/audit.md)
- [Policy changes (maintenance, start-up checks)](docs/governance.md)
- [Skills: code asks, the model answers, code decides](docs/skills.md)
- **[Code map (PDF, one page)](docs/code_map.pdf)**: every code file, what it does, how they relate, and the libraries used
- **[Decision flow (PDF, one page)](docs/code_llm_flow.pdf)**: every path a case can take (approval, hold / escalate, rejected, failed, invalid request), who acts at each step, and its code file
- **[Database (PDF, one page)](docs/db_schema.pdf)**: every table and column, keys, and how the tables relate
- **[Call chain (PDF, one page)](docs/call_chain.pdf)**: which function calls which, from `POST /runs` and an approval decision down to the tools, models and database (all four rebuilt by `make control-pdf`)

## Supported flows

1. **Valid PO invoice:**
   1. Three-way match within tolerance, recommendation, approval with authority slots (+ Financial Control for higher-risk cases).
   2. Exactly-once simulated posting.
2. **Holds:**
   - price, quantity, tax and freight variances;
   - missing PO or receipt; PO timeout;
   - blocked, dormant, pending-verification or sanctions-review vendors;
   - bank-change hold; currency mismatch;
   - fuzzy duplicates; resubmissions of rejected invoices;
   - request/document mismatch; missing documents.
3. **Rejections** (approval-gated): exact duplicate of a paid invoice; invoice billed to another legal entity.
4. **Escalations:** two or more fraud indicators (prompt injection, urgency, new bank account, look-alike domain …); repeated non-PO use.
5. **Non-PO** utilities and emergencies; **credit notes**; **foreign currency** (AUD authority at the corporate rate).
6. **Approval mechanics:**
   - authority limits per register v4.x (the superseded matrix is never used);
   - expired delegations;
   - segregation of duties: vendor creator, requester, and receipter above AUD 25k;
   - duplicate callbacks; approver rejection.
7. **Reliability:** tool timeout, transient failure, malformed model output, crash/resume mid-graph and after posting, new-evidence resumption.
8. **Judgement by skill questions** (FIN-064..072, multi-step):
   - a planning failure described as an "emergency" → held, then approved once an incident record arrives;
   - polite social engineering → escalated;
   - same SKU but a different pack size → held, then confirmed by the receipter;
   - late-fee pressure → approved with a visible warning;
   - a model that gives invalid answers or invents a quote → the case is held, never passed;
   - harder: three concerns in one invoice, instructions aimed at the AI reviewer, conflicting receipt evidence, a concern buried in boilerplate.
9. **Policy changes (maintenance):**
   - a running service keeps its policy; a restart picks edits up;
   - changed policy without maintenance → every case held;
   - re-baselined after maintenance → normal processing;
   - policy changed after an approval request → posting blocked.

Every flow is a documented scenario with live results: [docs/scenarios](docs/scenarios/README.md).

## Assumptions

- The clock is frozen at `2026-11-12T10:00+11:00` so the synthetic data is consistent. Set `FINAGENT__APP__FIXED_NOW=null` for wall-clock time.
- Business days exclude weekends only.
- The brief names the evidence types (invoices, purchase orders, goods receipts, vendor master data, approval delegations, finance policy) but not file formats. The attachment types (`invoice`, `credit_note`, `purchase_order`, `receipt`, `email`, `statement`, `supporting`, `other`), the attachment formats (JSON, text, CSV, e-mail, PDF) and the policy formats (Markdown, JSON, text, PDF) are our choice. New ones are added as plug-ins that emit the same contracts (`docs/extending.md`); nothing else changes.
- A held case waits: the hold result and the `run.needs_information` webhook say what is missing and who owns it. The agent never contacts suppliers or people itself; reminders belong in the system that receives the webhook.
- A held / escalated / failed run is resumed at most once (a second resume gets 409 and the newer run id), and each vendor invoice gets at most one ERP decision, whichever run submits it.
- Approver identity is trusted from the request (no SSO in the POC). Authority and segregation-of-duties checks are enforced.
- AUD is the base currency. GST is checked against the PO's implied tax rate.
- HOLD and ESCALATE are protective states that change nothing financially, so they don't require approval. Posting and rejection always require approval.

## Known limitations

See [docs/handover/known_limitations.md](docs/handover/known_limitations.md). Headlines:

- SQLite with a single writer; no approver SSO.
- Mock systems of record.
- No holiday calendar or split-invoice aggregation.
- Terraform not applied. The fine-tuned embedding model ships in `models/embedder/` (fp16, 64 MB; same accuracy-gate scores as fp32), so search works straight after download. The narrative LLM is not fine-tuned (dataset + recipe provided).
- Policy changes need a maintenance release (no live changes). The policy baseline has not yet been reviewed by a finance owner.

## Repository docs

| Topic | Location |
|---|---|
| Architecture design | [docs/architecture.md](docs/architecture.md) · **PDF (one page, system context with each block's library): [docs/diagrams/architecture.pdf](docs/diagrams/architecture.pdf)** · detailed design: [system_detailed_design.pdf](docs/diagrams/system_detailed_design.pdf) · presentation: [finagent_design_presentation.pdf](docs/diagrams/finagent_design_presentation.pdf) |
| Walkthrough (hands-on + code tour) | [docs/walkthrough.md](docs/walkthrough.md) |
| **Workflow scenarios: one page per flow, with business meaning** | [workflow_scenarios.pdf](docs/diagrams/workflow_scenarios.pdf) · [docs/workflows.md](docs/workflows.md) · all-flows overview: pages 2–3 of the same PDF |
| **Database structure (tables, columns, JSON, queries)** | [docs/database.md](docs/database.md) |
| **Infrastructure: every component, why, alternatives** | [docs/infrastructure.md](docs/infrastructure.md) · sections 19–24 of [system_detailed_design.pdf](docs/diagrams/system_detailed_design.pdf) |
| **System detailed design (code map, module design, integration, inside each component, API walk-throughs)** | **[system_detailed_design.pdf](docs/diagrams/system_detailed_design.pdf)** · [docs/architecture.md §10](docs/architecture.md) · [docs/extending.md](docs/extending.md) |
| **Policy changes (maintenance, start-up checks)** | [docs/governance.md](docs/governance.md) |
| **Full API reference (PDF)** | [docs/api/finagent_api_reference.pdf](docs/api/finagent_api_reference.pdf) (generated from the OpenAPI spec: `make api-pdf`) |
| Design note | [docs/design_note.md](docs/design_note.md) |
| Environments | [dev](docs/env/dev.md), [aws](docs/env/aws.md) + `infra/terraform/` |
| Commands and scripts | [workflow](docs/commands/workflow.md), [cloud](docs/commands/cloud_deployment.md), `scripts/`, `Makefile` |
| Scenarios | [docs/scenarios/](docs/scenarios/README.md) |
| Hand-over | [HANDOVER](docs/handover/HANDOVER.md), [runbook](docs/handover/runbook.md), [limitations](docs/handover/known_limitations.md) |
| Skills | [skills/README.md](skills/README.md), [coverage matrix](docs/skills/coverage.md) |
| RAG pipeline | [docs/rag_pipeline.md](docs/rag_pipeline.md) |
| Outcome pool, approvals, webhooks | [docs/api.md](docs/api.md) · full reference PDF: [finagent_api_reference.pdf](docs/api/finagent_api_reference.pdf) |
| Audit trail | [docs/audit.md](docs/audit.md) |
| Data contracts / extensibility | [docs/extending.md](docs/extending.md), `contracts/` |
| **Training and adopting models** (embedding, skill-procedure, narrative; gates; distillation recipe) | [docs/local_llm_training.md](docs/local_llm_training.md) |
| Sample transcripts | [docs/samples/](docs/samples/README.md) |
| Spin-up prompts / AI usage | [docs/PROMPTS.md](docs/PROMPTS.md), [HANDOVER §9](docs/handover/HANDOVER.md#9-ai-tool-usage-declaration) |
