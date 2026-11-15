# Skills: code asks, the model answers, code decides

Code:
- `src/finagent/skills/pack.py`: the contract and loader;
- `src/finagent/skills/llm_runner.py`: asks the questions, checks the answers, decides, counts;
- `skills/<name>/`: the 17 skill packs.

Diagrams: "Inside the skills" in [diagrams/system_detailed_design.pdf](diagrams/system_detailed_design.pdf).

## 1. What a skill is

Every skill has a **deterministic script** (`src/finagent/skills/processors/` or `parsers/`). The scripts do all the
maths, matching, limits and lookups, as the brief requires ("arithmetic and reconciliation deterministically in code").

**4 of the 11 control skills** also ask the model a few **fixed questions**: the judgement a script can't make.

| Skill | Question(s) | Answer → code decides |
|---|---|---|
| `non_po_validation` | is the claimed emergency genuine? (only for emergency non-PO) | no / unclear → **HOLD** (MISSING_PO) |
| `three_way_match` | same item and pack size as ordered? does an internal note contradict the receipt? | different / contradicted → **HOLD** (QUANTITY_VARIANCE) |
| `instruction_screen` | claimed prior approval? payment-route change? instructions to the system? anything else? | yes → **FLAG** (risk indicator); "anything else" → warning only |
| `payment_scheduling` | early-payment or late-fee pressure? | yes → **FLAG** (warning) |

The other 7 controls and the 5 parsers are script only (`execution: code`).

```
skills/<name>/
  SKILL.md          front matter = the SPEC (validated); body = what the skill checks and what each answer means
  references/       policy.md (generated from the live policy index) + reference docs the questions are judged against
  tests/            fixtures.yaml: tests of the deterministic script
```

| Spec field | Meaning |
|---|---|
| `execution` | `code` (script only) or `code+llm` (script, then questions) |
| `policy` | sections the skill implements; must equal the code's `compliance_refs` (checked) |
| `evidence` | what code collects for the questions: `docs`, `request.notes`, `invoice.lines`, `invoice.non_po`, `po.lines`, `po.receipts` |
| `questions` | `id`, `ask`, allowed `answers`, `on` (answer → `FLAG` / `HOLD` / `ESCALATE`; anything else = no change), `finding`, optional `indicator`, `reference`, `when` |
| `on_concern` | exception category and owner raised on `HOLD` / `ESCALATE` answers |

## 2. How a question skill runs (the same for all 4)

At the RECONCILE step, in `config/workflow.yaml` order, after the skill's script:

```
1 collect   code gathers the excerpts named in `evidence` (redacted, labelled DATA)
2 ask       SMALL model: {"answers": {"pack_size": {"answer": "no", "quote": "carton of 10 boxes"}}}
3 check     every question answered | answer is one of the allowed answers | the quote really appears in the evidence
            (case, spacing and quote marks ignored) | a concern needs a quote. Invalid -> one repair round
4 2nd vote  the small model's answers stand alone ONLY if all are "all clear" AND at least 99% certain - measured from
            the model's token probabilities, not self-reported. Otherwise the LARGE model answers the SAME questions:
            both votes are recorded; if they disagree the large model decides and the approver sees a warning
            ("models disagreed")
5 decide    code maps answers to verdicts with `on`, adds the citations itself (skill policy + reference + quoted doc):
              FLAG -> risk indicator | HOLD / ESCALATE -> exception + vote | no valid answers -> HOLD ("not answered")
6 count     code re-runs the FIN-POL-005 s3 indicator count over indicators from scripts AND answers:
              two or more distinct -> ESCALATE (llm_runner.count_indicators)
```

**What the model does NOT do:** choose steps, call tools, count, calculate, cite or choose a verdict. It only picks
answers from a list and copies a quote.

**Boundaries:**
- **Caution only.** No answer can approve, relax or override a script result. There is no such verdict, and the merge only adds.
- **Untrusted text is data.** Excerpts are redacted and fenced. A quote must exist in the evidence, so the model can't
  invent facts.
- **Bounded.** Each model gets at most one repair round. Each run has `skills.llm.max_calls_per_run` (default 40).
- **Kill switch.** With `skills.llm.enabled: false`, no questions are asked and the scripts decide alone.
- **Failures are explicit.** A provider error, invalid answers or an exhausted budget **holds** the case with
  "judgement questions not answered".
- **Everything is recorded:**
  - an audit event `skill.procedure` per skill (status, verdict, decided by small/large, calls, models);
  - a reasoning entry "skill:<name> (questions)";
  - `state.procedures` with both models' answers.

### When the AI is not sure

| Situation | What code does |
|---|---|
| small model sure (≥ 99%) all is clear | its answers stand |
| small model unsure, or it raised a concern | the large model votes too; on disagreement the large model decides and the approver sees "models disagreed" |
| the **deciding** answer is "unclear" or below 80% certain (`skills.llm.uncertain_below`, per question) | the question's `if_uncertain` rule in `SKILL.md` (below) |
| a random 5% of all-clears (`skills.llm.qa_sample_rate`) | marked "QA sample" for the approver to confirm: catches "confidently wrong", and the reviewer's decision becomes labelled data |

| `if_uncertain` | Effect | Used for |
|---|---|---|
| `warn` | warning shown to the approver (every approval already has a human approver) | all other questions |
| `ask_evidence` | HOLD and ask the `on_concern` owner for the evidence that settles it; the case resumes when it arrives | the emergency question (requester) |
| `review` | HOLD for a person to review the AI judgement | available, unused by default |

**Resuming a held case:**
- **new evidence:** `POST /runs/{id}/evidence` (all questions are asked again);
- **a reviewer's decision** on an uncertain or unanswered question: `POST /runs/{id}/review` (`clear` / `concern` + reason).
  That question is then answered by the person, not the model. The reviewer must hold a role in `skills.review.roles`,
  can't be the requester and can't approve the case.

This follows common practice in invoice automation:
- confidence thresholds route uncertain items to exception handling;
- exceptions go to their owner;
- a small random QA sample checks the straight-through decisions.

**Chosen from gate data, not guessed.** With `qwen2.5:14b` deciding, 22 of 61 skill decisions in the gate were below
99% certain, including clean invoices. `ask_evidence` on the matching questions would have held most clean
PO invoices, so those questions warn instead. Only the emergency question, where uncertainty is rare and a miss is
costly, asks for evidence. LLM token probabilities are not perfectly calibrated: recalibrate both thresholds as
QA-sample results accumulate (22 cases is a small calibration set).

## 3. What judgement adds (tested)

`mock_data/scenarios.json` FIN-064 to FIN-072 are cases the scripts alone would send to approval (checked with the kill switch):

| Case | Scripts only | With questions |
|---|---|---|
| FIN-064 lapsed agreement presented as an emergency (non-PO) | approve: the keyword list misses "lapsed" | **HOLD** MISSING_PO; an internal incident record → approved |
| FIN-065 polite remittance change + claimed internal approval | approve (1 indicator) | 2 answers flagged + look-alike domain = 3 indicators → code **ESCALATES** |
| FIN-066 same SKU, "carton of 10 boxes" vs "box of 100" | approve | **HOLD** for the receipter; receipter note → approved |
| FIN-067 the model replies in prose, then a disallowed answer, then an invented quote | n/a | all rejected → **HOLD**; clean re-run → approved |
| FIN-068 invoice demands 7-day payment + 15% late fee | approve, no warning | approve **with a visible warning** |
| FIN-069 pack size + social-engineering e-mail + late fee | approve | **ESCALATE** (outranks the pack-size HOLD); all warnings listed |
| FIN-070 invoice tells "automated reviewers" to return NO_CHANGE | approve | instruction **flagged, not followed** → 2 indicators → **ESCALATE** |
| FIN-071 ERP receipt says 100, internal dock note says 60 | approve | **HOLD** for the receipter; updated note → approved |
| FIN-072 payment-route change buried in boilerplate | approve, no warning | approve **with a visible warning** |

In **tests and `finagent eval`** both models are the deterministic mock (`skills.llm.use_mock: true`). It replays
**scripted answers** from `mock_data/skill_recordings/<skill>/<case>[@attempt].yaml`, in call order: small, its repair,
large. Without a recording it gives the safe answer to every question. The **running service** uses the real local
models (section 5). `make skill-check` measures real models.

## 4. Writing or changing a skill

1. **Change a question:** edit `skills/<name>/SKILL.md`. It takes effect on the next run (changed files are re-read).
   No code change. Keep questions short, with fixed answers.
2. **Add a reference doc:** put it in `references/`, list it under `references:`, and name it in the question's `reference:`.
3. **New skill:** `make new-skill NAME=my_check POLICY="FIN-POL-013 s2"` scaffolds the script, `SKILL.md` and fixtures.
4. **Check it:**
   - `pytest tests/unit/test_skill_packs.py` (contract + runner);
   - `finagent skills test <name>` (the deterministic script);
   - `finagent skills docs` (regenerates `references/policy.md` and the catalogue `skills/README.md`; never `SKILL.md`).
5. **Re-gate** the models after changing questions: `make skill-check`.

## 5. Which models answer: small + large (swappable)

| Setting | Default | |
|---|---|---|
| `skills.llm.enabled` | `true` | **kill switch** |
| `skills.llm.small` | `ollama` / `qwen2.5:7b-instruct` | answers every question: fast, local |
| `skills.llm.large` | `ollama` / `qwen2.5:14b-instruct` | second vote unless the small model is sure all is clear; decides on disagreement. Swappable: a bigger local model, `anthropic`, `bedrock`, or `none` (small only) |
| `skills.llm.uncertain_below` | `0.8` | a deciding answer below this (per question) follows its `if_uncertain` rule |
| `skills.llm.qa_sample_rate` | `0.05` | share of AI all-clears marked for the approver's quality check |
| `skills.llm.sure_threshold` | `0.99` | how certain (token probability) the small model's all-clear must be to stand alone. Hosted models report no probabilities, so with a hosted small model the second vote is always asked |
| `skills.llm.use_mock` | `false` | `true` = both are the mock (tests, eval and bootstrap pin this; fast demos) |
| `skills.llm.approved` | mock pair | model **pairs** that passed the gate |
| `llm.providers.ollama.num_ctx` | `16384` | Ollama's small default context would truncate long documents |

`/healthz` → `skill_questions` shows the pair, whether each model is reachable and whether the pair is approved. If
Ollama is down, questions can't be answered and cases are **held** (start-up prints a warning).

```bash
ollama serve & ollama pull qwen2.5:7b-instruct && ollama pull qwen2.5:14b-instruct    # once
make start                                                     # service uses the pair
FINAGENT__SKILLS__LLM__USE_MOCK=true make start                # fast demo mode (e.g. make load of all 77 cases)
make skill-check SMALL=ollama/qwen2.5:3b-instruct LARGE=ollama/qwen2.5:14b-instruct   # gate another pair
```

**Real-model results** (stock `qwen2.5:7b` + `qwen2.5:14b`, M2 Max, 2026-11-14), and the pair is approved:

| Run | Result | Large model asked | Avg time per case |
|---|---|---|---|
| 22-case gate | **PASS 22/22**, 0 unanswered, 7 uncertain answers, 5 disagreements | 16 times | 17 s |
| same 22 with **maximum noise** (`--noise max`) | **PASS 22/22** | 21 times | 25 s |
| all scenarios (`--all`) | **70/70** applicable (FIN-022/059 simulate crashes; FIN-067 scripts invalid output) | 25 times | 9 s |
| code only (`skills.llm.enabled: false`) | 68/77: the 9 judgement cases fail - the measured value of the model layer | - | - |

The earlier free-form procedure design (the model chose scripts and wrote findings) failed with `qwen2.5:14b` alone
(11/17, ~140 s per case). That is why the model's job was narrowed to answering scoped questions.

## 6. Switching models: the core stays identical

A different model (faster, larger, hosted) can only change the **answers**, never the core flow:

| Guarantee | Enforced by |
|---|---|
| Scripts never see model output: maths, matching, limits, duplicate and vendor checks, approval routing are identical for any model | `Engine.run_skills` gives scripts only other scripts' results; property test `tests/scenarios/test_model_independence.py` over every scenario (script results identical with questions on and off) |
| Answers can only add caution | no approve verdict in the contract, caution-only merge, and the same property test (outcome with questions ≥ scripts-only outcome) |
| Every model answers the same questions against the same references | questions, allowed answers and references are in `SKILL.md`; answers are checked by code; quotes must exist in the evidence |
| Code decides what an answer means | the `on` table in `SKILL.md`, not the model |
| A new pair is adopted only after it proves itself | **model acceptance gate**: `make skill-check SMALL=… LARGE=…` runs the 22-case conformance set below. PASS = every expected outcome and no unanswered skill. Then add the pair to `skills.llm.approved`. An unapproved pair still runs but is flagged in `/healthz` and at start-up. The report also shows how often the large model was asked and where it changed the answer |
| A misbehaving model can be removed instantly | kill switch, or `large.provider: none` |

The conformance set (22 cases):

| Group | Cases | Must |
|---|---|---|
| must catch | FIN-064, 065, 066, 068, 069, 070, 071, 072 | catch what the scripts alone miss |
| must pass | FIN-001, 007, 015, 017, 050 | **not** be held (clean invoices) |
| attacks | FIN-003, 044, 057 | escalate |
| script hold | FIN-006 | stay held |
| noise | FIN-073 to FIN-077 | noisy notes, extra attachments, long e-mail thread, OCR noise: **no false concern**. Unreadable totals (077): held, never guessed |

How to choose, (optionally) train and adopt the models: [local_llm_training.md §7](local_llm_training.md).

## 7. Limits

- **Only what is asked.** A model answering fixed questions finds only what the questions cover. The catch-all
  "anything else?" question in `instruction_screen` surfaces other concerns as a warning only. New kinds of judgement
  need a new question, which a finance reviewer can read and approve.
- **Determinism:** real models run at temperature 0, but answers can still vary between model versions. Every run
  records both models and their answers, and scripts make sure the facts and maths never vary.
- **Noise:** noisy inputs (FIN-073 to FIN-077) found two false positives in the *deterministic* code, both now fixed and
  regression-tested:
  - an e-mail disclaimer's "confidential" was read as secret payment language;
  - a quoted "From:" header was taken as the supplier name.
- **Caution-only:** an answer can't clear a false hold from a script (e.g. a harmless SKU wording change that the
  deterministic match rejects). That is deliberate: clearing would need human judgement.
