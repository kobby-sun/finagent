# Environment: local development

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Python | 3.13 (3.11+ supported) | everything |
| make, bash, curl, jq | any | scripts |
| Docker | 24+ | optional: container run, `docker compose` |
| Ollama | 0.12+ | optional: real local model / semantic embeddings |
| Terraform | 1.6+ | optional: `make tf-validate`, AWS deploy |

No API keys are needed for the default setup. The model is the deterministic `mock` provider, and every system integration except retrieval is simulated from `mock_data/`.

## Setup (about 1 minute)

```bash
scripts/dev/setup.sh          # venv + pinned deps (requirements.lock) + mock data + index + workflow validation
source .venv/bin/activate
finagent workflow validate    # OK: accounts-payable-invoice-review v2026.11.1 digest=...
```

## Run

Service control and test-data commands (`make start|stop|status|logs|up|down|seed|load|load-approve|reset`) are summarised at the top of the [README](../../README.md#local-dev-control-the-service-and-load-test-data).

```bash
make test                     # 352 stable tests: unit, contract, API, 98 skill fixtures, all 80 scenarios
make eval                     # scenario evaluation report (pass/fail per case + RAG metrics)
make demo                     # FIN-001 approval flow + FIN-003 exception flow transcript
make serve                    # API on http://127.0.0.1:8080 - Swagger UI at /docs, ReDoc at /redoc
scripts/dev/api_walkthrough.sh   # curl walkthrough against the running API
```

Docker (everything runs locally in containers):

One command (macOS / Linux / Windows) starts everything: the skill models and the agent, with the trained search model
built into the image.

```bash
make up                        # Windows: .\dev.ps1 up    any OS: python scripts/dev/docker_up.py
MODELS=docker make up          # auto (default) | host (Ollama on this machine) | docker (Ollama container) | mock (no models)
make docker-test               # every test inside the container      (Windows: .\dev.ps1 docker-test)
make docker-eval               # all 80 scenarios inside the container
make docker-load CASES="FIN-001"   # submit cases to the container
make down                      # stop;  make down-clean also removes data and pulled models
```

By hand:

```bash
docker compose --profile ollama up --build                 # agent + Ollama container (pull the models into it)
docker compose --profile train run --rm embed-train         # fine-tune + evaluate the embedding model (CPU)
INSTALL_EMBEDDINGS=false docker compose up --build          # slim image: keyword search only (BM25)
```

## Environment variables

Every key in `config/settings.yaml` can be overridden as `FINAGENT__<SECTION>__<KEY>`. Nested keys use extra `__`, e.g. `FINAGENT__LLM__PROVIDERS__OLLAMA__MODEL`.

| Variable | Default | Meaning |
|---|---|---|
| `FINAGENT_CONFIG` | `config/settings.yaml` | Alternate config file |
| `FINAGENT__APP__DATA_DIR` | `./data` | SQLite DB, index, vectors, audit JSONL, eval reports |
| `FINAGENT__APP__FIXED_NOW` | `2026-11-12T10:00:00+11:00` | Frozen clock the mock data is anchored to; `null` = wall clock |
| `FINAGENT__APP__API_KEY` | unset | Require `X-API-Key` on the HTTP API |
| `FINAGENT__LLM__PROVIDER` | `mock` | `mock` \| `ollama` \| `anthropic` \| `bedrock` |
| `FINAGENT__LLM__ON_INVALID_OUTPUT` | `fallback` | `fallback` (flagged deterministic narrative) or `fail` |
| `ANTHROPIC_API_KEY` | unset | Only for `provider=anthropic` |
| `FINAGENT__RAG__MODE` | `hybrid` | `lexical` \| `hybrid` \| `vector`. Falls back to `lexical` if the model/library is missing (see `/healthz`) |
| `FINAGENT__RAG__VECTOR__EMBEDDER` | `sentence_transformers` | fine-tuned model at `rag.vector.model_path` \| `ollama` \| `hash` (tests only) |
| `FINAGENT__RAG__RETRAIN_ON_STARTUP` | `true` | start-up: search model trained on an older corpus -> gated retrain; `false` / fails -> BM25 only |
| `SEMANTIC` (setup only) | `1` | `SEMANTIC=0 make setup` skips the ~700 MB semantic-search libraries |
| `FINAGENT__RAG__VECTOR__STORE` | `sqlite` | or a registered adapter (pgvector, …) |
| `FINAGENT__WEBHOOKS__SIGNING_SECRET` | dev value | HMAC secret for outbound and inbound webhooks |
| `FINAGENT__SKILLS__LLM__ENABLED` | `true` | kill switch: `false` = no skill questions, deterministic scripts decide alone |
| `FINAGENT__SKILLS__LLM__SMALL__PROVIDER` / `__MODEL` | `ollama` / `qwen2.5:7b-instruct` | answers every skill question. **Needs `ollama serve` + `ollama pull qwen2.5:7b-instruct`** |
| `FINAGENT__SKILLS__LLM__LARGE__PROVIDER` / `__MODEL` | `ollama` / `qwen2.5:14b-instruct` | second opinion on concerns only; `none` = small only |
| `FINAGENT__SKILLS__LLM__USE_MOCK` | `false` | `true` = both are the deterministic mock (fast demo mode). Tests and eval always use the mock |
| `FINAGENT__SKILLS__LLM__MAX_CALLS_PER_RUN` | `40` | budget for all skill-question model calls in one run |
| `FINAGENT__LLM__PROVIDERS__OLLAMA__NUM_CTX` | `16384` | Ollama context window (its small default silently truncates long documents) |
| `FINAGENT_CRASH_AFTER_NODE` | unset | **Test only:** simulate a crash after a graph node |

## Model configuration

The provider and model live only in config; the orchestration code never names a model.

```bash
# Real local model (Ollama)
ollama pull qwen2.5:7b-instruct && ollama pull qwen2.5:14b-instruct    # skill questions: small + large (~5 + 9 GB)
FINAGENT__LLM__PROVIDER=ollama FINAGENT__RAG__VECTOR__EMBEDDER=ollama finagent ingest
FINAGENT__LLM__PROVIDER=ollama finagent eval

# Claude API
pip install anthropic
FINAGENT__LLM__PROVIDER=anthropic ANTHROPIC_API_KEY=... finagent eval     # model: llm.providers.anthropic.model

# Model-dependent integration tests (safety invariants for any model)
scripts/dev/test_llm.sh ollama
```

Fine-tuning a light local model on this workflow is covered in [../local_llm_training.md](../local_llm_training.md).

## Ingestion and indexing

```bash
finagent ingest      # reads rag.sources -> CorpusDocument -> section chunks -> BM25 JSON + SQLite vectors
```

- Re-run after any corpus change, or delete `data/index/` (the retriever also rebuilds automatically when the index is missing).
- The index version (a corpus content hash) is recorded on every run.

## Local data layout

```
data/
  finagent.db            runs, checkpoints, approvals, callbacks, outcomes, outbox, audit, ERP sandbox, eval history
  audit.jsonl            same audit events, append-only JSONL (ship to a SIEM)
  index/bm25_index.json  chunks + metadata
  index/vectors.db       local vector store
  eval/latest.json       last evaluation report
```

Reset with `scripts/dev/reset.sh`.

## Regenerating artefacts

```bash
make data          # mock data + skill fixtures
make docs          # skill docs (with injected compliance), OpenAPI, contracts, scenario docs, sample transcript
make dataset       # local-LLM training set
```
