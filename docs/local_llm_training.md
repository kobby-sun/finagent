# Training and adopting models

finagent uses three models. Each has its own training procedure and its own **adoption gate**: no model is used
because it was trained, only because it passed its gate.

| Model | Job | Trained here? | Procedure | Adoption gate |
|---|---|---|---|---|
| **Embedding model** (`bge-small`, fine-tuned) | policy search (semantic half of hybrid RAG) | **yes**: shipped in `models/embedder/` (not in git) | section 6, `make retrain` | accuracy gate: never below keyword search; semantic-only set ≥ 0.8 |
| **Skill-question models**: small (`qwen2.5:7b-instruct`) + large second opinion (`qwen2.5:14b-instruct`, swappable) | answer the 4 judgement skills' fixed questions ([skills.md](skills.md)) | **no**: off-the-shelf; recipe to distil our own small model in section 7 (not automated) | section 7 | model acceptance gate `make skill-check`: 22-case conformance set, run on the **pair** |
| **Narrative model** | writes the cited explanation (`ModelDraft`) | **no**: dataset + LoRA recipe only | sections 1–5 | section 4: scenarios + safety tests |

Safety never depends on training:
- arithmetic and reconciliation stay in scripts;
- models only answer fixed questions, and code decides what an answer means, so a model can only add caution;
- every output is validated.

Training improves **usefulness**: better recall, better judgement, fewer retries.

All training data is **synthetic** (mock vendors, amounts, last-4 bank digits). Never train on production financial
data without approval (FIN-POL-010 s4).

---

# Narrative model (sections 1–5)

The narrative model has a **narrow, well-specified job**. It reads the deterministic rules assessment plus fenced,
trust-labelled evidence, and writes a schema-valid `ModelDraft` with:

- a summary and rationale;
- citations, restricted to supplied, authoritative ids;
- inferences, unknowns and risk notes, where injected instructions are reported rather than followed;
- an outcome that is never less conservative than the rules.

A 1.5–3B instruct model is a realistic target for this job alone.

## 1. Dataset

```bash
make dataset        # = python scripts/training/generate_dataset.py
```

- Every scenario is run through the **real agent graph** with a capturing provider. Examples use the exact prompt format used at inference time.
- Three variants per case:
  - `orig`;
  - `injection`: case notes demand payment and claim pre-approval;
  - `noise`: an irrelevant meal-allowance question.
- Gold answers are derived from the deterministic assessment and checked by the same `ModelDraft` validator used in production.
- Splits are **by case** (no leakage): `training/data/{train,valid,test}.jsonl` plus `manifest.json` (currently 99 / 12 / 12 examples).
- Chat format: `{"messages": [{"role": "system"}, {"role": "user"}, {"role": "assistant"}]}`. It works with mlx-lm, TRL/Unsloth and Axolotl.

To grow the set:

- add scenarios to `scripts/generate_mock_data.py`;
- add augmentations to `AUGMENT` in the generator (e.g. reordered attachments, OCR noise, other languages for supplier e-mails).

## 2. Fine-tune (Apple Silicon, mlx-lm)

```bash
pip install 'finagent[training]'                                     # mlx-lm
scripts/training/train_lora.sh Qwen/Qwen2.5-1.5B-Instruct 600        # LoRA -> training/adapters, fused -> training/fused
```

On NVIDIA, use Unsloth/TRL `SFTTrainer` on the same JSONL. Recommended settings:

- LoRA r=16, alpha=32, lr 1e-4, 2–3 epochs, `max_seq_length` 8192;
- assistant-only loss.

## 3. Package for Ollama

```bash
LLAMA_CPP=~/src/llama.cpp scripts/training/build_ollama_model.sh     # GGUF q8_0 + `ollama create finagent-ap -f training/Modelfile`
```

## 4. Evaluate before adopting

```bash
scripts/training/eval_local_model.sh finagent-ap
```

This runs:

- every scenario (77) with the narrative step on `provider=ollama`;
- the `llm`-marked safety tests.

Adopt the model only if **all** of the following hold:

- every scenario passes (outcomes are rule-decided, so a model that is too conservative shows up as a failure);
- model `status` is `VALID` on at least 95% of runs (fallbacks are visible in `result.model.attempts`);
- there are zero `model.override_blocked` events on non-adversarial cases.

The architecture guarantees safety even with a weak model: outcome relaxation is blocked, citations are validated and arithmetic stays in code. Fine-tuning improves *usefulness* (better narratives, fewer retries); safety does not depend on it.

## 5. Configure

```yaml
llm:
  provider: ollama
  providers:
    ollama: {base_url: http://localhost:11434, model: finagent-ap}
```

Or set `FINAGENT__LLM__PROVIDER=ollama FINAGENT__LLM__PROVIDERS__OLLAMA__MODEL=finagent-ap`.

Data note: the training data is **synthetic** (mock vendors, amounts and bank numbers; last-4 digits only). Never train on production financial data without approval (FIN-POL-010 s4).

## 6. Embedding model: fine-tune and evaluate

**Why.** On the hand-written held-out questions (`training/embeddings/natural_questions.yaml`, 59 realistic AP-desk questions, never trained on), the retrievers score:

| Retriever | Correct section in top 5 |
|---|---|
| BM25 | 0.707 |
| BM25 + offline hash vectors | 0.638 (worse) |

Paraphrased questions need a semantic model. **What the kit does:**

1. `generate_embedding_pairs.py` creates (anchor, positive, hard negative) triplets from **synthetic** queries:
   - heading templates;
   - inverse-cloze sentences;
   - the skills' curated queries.

   Hard negatives are BM25's best wrong chunks, plus the superseded matrix and adversarial documents whenever they compete. Tests guarantee no held-out question leaks into training.
2. `train_embeddings.py` fine-tunes `BAAI/bge-small-en-v1.5` (33M parameters, CPU-friendly) with MultipleNegativesRankingLoss, then saves to `models/embedder`.
3. `eval_embeddings.py` reports recall@1, recall@5, MRR@10 and `non_auth_top1` on the held-out questions for each embedder. It covers two modes:
   - **dense:** the model alone;
   - **hybrid:** the real retriever.

   The report goes to `training/embeddings/eval_report.json`.

**Run it (local Docker, CPU, no host Python needed):**

```bash
docker compose --profile train run --rm embed-train                 # ingest -> pairs -> fine-tune -> eval
BASE_MODEL=BAAI/bge-base-en-v1.5 EPOCHS=4 docker compose --profile train run --rm embed-train
```

Or on the host: `pip install 'finagent[embeddings]'`, then run `generate_embedding_pairs.py`, `train_embeddings.py` and `eval_embeddings.py`.

**Adopt only if it wins.** Hybrid recall@5 must beat BM25 (0.707) and `non_auth_top1` must stay 0. Then:

```yaml
rag:
  mode: hybrid
  vector: {embedder: sentence_transformers, model_path: models/embedder}
```

Then run `finagent ingest`; vectors rebuild automatically for the new embedder. In Docker: `INSTALL_EMBEDDINGS=true docker compose up --build` (the model directory is mounted read-only).

### Results (local Docker run, CPU, bge-small, 3 epochs, about 8 min)

On the held-out natural questions (`eval_report.json`):

| Embedder | Mode | R@1 | R@5 | MRR | Non-authoritative ranked first |
|---|---|---|---|---|---|
| BM25 (default) | lexical | 0.466 | 0.707 | 0.561 | 0 |
| hash | hybrid | 0.328 | 0.638 | 0.442 | 0 |
| bge-small (base) | dense | 0.517 | 0.879 | 0.675 | **10** |
| **fine-tuned** | dense / `rag.mode: vector` | **0.672** | **0.897** | **0.774** | **0** |
| fine-tuned | hybrid, vector weight 4 | 0.621 | 0.897 | 0.743 | 0 |

With the fine-tuned model enabled (hybrid, vector weight 4), the full regression passes. Two curated queries were then
added for the 2 sections the model first missed, which brought curated recall back to 1.0.

**Current setting:**
- `rag.mode: hybrid` with the fine-tuned model is the default.
- Without the model (e.g. a fresh clone) the service falls back to keyword search automatically.
- On the 17 "semantic-only" questions (`tests/fixtures/semantic_only_queries.yaml`), keyword search finds 0/17 and
  hybrid finds 17/17.

**Retraining after policy or skill changes:** `make retrain` (Windows: `.\dev.ps1 retrain`). It validates the workflow,
rebuilds the index, regenerates the pairs, retrains in Docker, evaluates, and **keeps the new model only if it passes
the accuracy gate**. Otherwise the previous model is restored. The report is in `training/retrain_report.json`.
The service also checks this at start-up: if `models/embedder/trained_on.json` names an older corpus, the same gated
retrain runs (`rag.retrain_on_startup`); if that is off or fails, search uses BM25 only.

## 7. Skill-question models: choose, (optionally) distil, adopt

The 4 judgement skills ask **fixed questions** ([skills.md](skills.md)). Each answer is one item from a list plus a
verbatim quote, so a small model is enough for most cases. Two models work as a pair:
- **small**: answers every question (fast, local);
- **large**: answers the same questions only when the small one raised a concern, said "unclear" or failed. Its answers
  then decide.

### 7.1 Try off-the-shelf models first (no training)

```bash
ollama pull qwen2.5:7b-instruct && ollama pull qwen2.5:14b-instruct
make skill-check SMALL=ollama/qwen2.5:7b-instruct LARGE=ollama/qwen2.5:14b-instruct
make skill-check SMALL=ollama/qwen2.5:3b-instruct LARGE=ollama/qwen2.5:14b-instruct    # a smaller small model
make skill-check SMALL=ollama/qwen2.5:7b-instruct LARGE=none                           # small model alone
```

**PASS** means all 22 conformance cases reach their expected outcome and no skill is left unanswered. The cases are:
- 8 must-catch;
- 5 clean invoices that must not be held;
- 3 attacks that must escalate;
- 1 script hold;
- 5 noisy inputs.

The report (`data/eval/model_gate/<pair>.json`) also shows seconds per case, how often the large model was asked, and
where it changed the small model's answer. Adopt a pair that passes:

```yaml
skills:
  llm:
    small: {provider: ollama, model: qwen2.5:7b-instruct}
    large: {provider: ollama, model: qwen2.5:14b-instruct}
    approved: [ollama/qwen2.5:7b-instruct+ollama/qwen2.5:14b-instruct]
```

Hosted second opinion: `LARGE=anthropic/<model>` or `bedrock/<model>`. Only the cases with a concern leave the machine,
but they still need an approved endpoint (FIN-POL-010 s4).

**Results:** stock `qwen2.5:7b` + `qwen2.5:14b` **passed** (22/22 clean, 22/22 maximum noise, 70/70 scenarios) and
is the approved pair; reports in `data/eval/model_gate/`. Neither model is tuned by us. The earlier free-form procedure
design failed with `qwen2.5:14b` alone (11/17), which is why the model's job was narrowed to answering questions.

### 7.2 Distil our own small model (recipe; not automated)

The large model's checked answers are ready-made training labels: a fixed answer plus a quote, nothing free-form.

1. **Teacher answers.** Run every scenario (and noise variants) with the large model as `small` and `LARGE=none`.
   Keep only skills whose answers passed the runner's check **and** whose case outcome matched the expectation.
2. **Examples.** For each kept skill call: the runner's prompt (`RULES` + `SkillRunner.prompt`) and the teacher's
   answers JSON. Both are already in `state.procedures[*].tiers`; the prompt is rebuilt from the same inputs.
3. **Split by case.** Use the same split rule as the narrative dataset (no case in more than one split). Add noise
   augmentations: reordered attachments, OCR noise, chatty notes.
4. **Fine-tune** with the LoRA recipe of section 2: `scripts/training/train_lora.sh Qwen/Qwen2.5-3B-Instruct 600`.
5. **Package** for Ollama (section 3), e.g. as `finagent-skills`.
6. **Gate the new pair and adopt only on PASS:** `make skill-check SMALL=ollama/finagent-skills LARGE=ollama/qwen2.5:14b-instruct`.
   Over time the large model should be asked less often, which the gate report shows.

Data rules are the same: synthetic scenarios only. A hosted teacher may only see synthetic data unless its endpoint is
approved.
