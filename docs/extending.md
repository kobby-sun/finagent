# Extending finagent (future-proof data contracts and re-organisable skills)

Every extension is a **registration plus config**. None of them change the agent graph:

- new document types and file formats;
- new corpus sources and API feeds;
- new controls;
- re-ordering or cutting down the workflow.

The example plugin in [`plugins/example_extensions.py`](../plugins/example_extensions.py) exercises every extension point, and `tests/unit/test_plugins.py` proves it.

## 1. Data contracts and versioning rules

| Contract | Schema | File |
|---|---|---|
| Case document | `finagent.document/1` | `contracts/normalized_document.v1.schema.json` |
| Corpus document | `finagent.corpus/1` | `contracts/corpus_document.v1.schema.json` |
| Processing request / final result / model draft / approval decision | v1 | `contracts/*.schema.json` |

Rules:

1. **Additive within a major version.** New fields are optional with safe defaults; consumers must ignore what they don't use.
2. **Source-specific data goes in `extensions`** under a namespaced key `x-<source>.<field>`, e.g. `x-pdf.pages` or `x-coupa.requisition_id`. Un-namespaced keys are rejected by the validator.
3. **Breaking change → new major** (`finagent.document/2`) plus a converter. Parsers declare the schema they emit.
4. **Provenance is mandatory** on everything produced: producer, version, source URI, content hash, connector. This lets derived index entries be traced and deleted (FIN-POL-010 s5).
5. **Trust is never self-declared.** A corpus source may set `trust_ceiling` to *lower* trust; nothing in a document can raise it. Bank details in documents may only carry the last 4 digits; the contract enforces it.
6. **Export and publish** with `finagent contracts export`. Commit `contracts/` so producers can validate before sending.

## 2. New input type (document / file format)

```python
from finagent.skills.parsers.registry import register_parser

@register_parser("docx_parser", content_types=("application/vnd.openxmlformats-officedocument.wordprocessingml.document",),
                 extensions=(".docx",), priority=70, binary=True, version="1.0",
                 description="Word invoices", compliance_refs=[("FIN-POL-001", "s2")])
def parse_docx(filename, content, *, raw=None) -> dict:
    text = ...  # python-docx over raw bytes
    from finagent.skills.parsers.documents import parse_invoice_text
    doc = parse_invoice_text(filename, text); doc["parser"] = "docx_parser"; return doc
```

- **Routing:** `sniff` > specific content type > extension > generic content type > text fallback.
- **Output is validated** against `finagent.document/1`. A contract violation degrades to a `supporting` document with a warning; it never crashes a run.
- **Binary inputs** arrive base64 (`Attachment.encoding="base64"`). This is how PDFs work (`pdf_parser`, scenario FIN-042).
- **Add fixtures** in `skills/docx_parser/tests/fixtures.yaml` (`tests/skills` requires at least 3), then run `finagent skills docs`.

## 3. New corpus source (RAG)

Declarative:

```yaml
rag:
  sources:
    - {name: finance-policy-corpus, type: local_dir, path: ./finance_rag_corpus}
    - {name: policy-portal, type: http_feed, url: https://policies.internal/api/corpus,
       headers: {Authorization: "env:POLICY_FEED_TOKEN"}}
    - {name: supplier-notices, type: http_feed, url: https://suppliers.example/api/notices, trust_ceiling: untrusted}
```

New loader or connector:

```python
from finagent.rag.ingest import register_loader, register_connector

@register_loader(".html")
def load_html(path): ...            # -> CorpusDocument

@register_connector("s3")
def s3(cfg, root): ...              # yield CorpusDocument(...) for each object
```

Then run `finagent ingest`. The index version changes and is recorded on runs.

## 4. New case channel (another system sending invoices)

There is no separate feed endpoint. Another system submits a case by calling `POST /runs` with a `ProcessingRequest`
(case fields + attachments, `source` = `supplier` | `internal` | `email`) and an `Idempotency-Key` header. A
re-delivered message with the same key returns the same run. If the sender cannot produce that shape, put a small
mapping service in front of the API (middleware, or a script); finagent itself stays unchanged.

## 5. Re-organising the skill pipeline

`config/workflow.yaml` defines stages, order, conditions (`when`, `retrieve_when`), dependencies (`requires`) and `enabled`.

| You want to… | Do |
|---|---|
| Cut down an optional control | `enabled: false` (e.g. `payment_scheduling`) |
| Re-order | move entries; `requires` must still point to earlier enabled skills |
| Run a control only sometimes | `when: <condition>`; add conditions with `@condition("name")` in a plugin |
| Add a control | write a processor skill (below), list it in a stage, add fixtures |
| Swap an implementation | register a skill with the **same name** in a plugin (last registration wins) and bump `version` |

**Guard-rails.** `finagent workflow validate`, application start-up and `tests/unit/test_workflow_config.py` refuse:

- unknown skills or conditions;
- duplicate entries;
- dependencies on later or disabled skills;
- disabling or removing a **mandatory** control.

Mandatory status is declared in code with its policy basis (`mandatory_basis`, e.g. "FIN-POL-001 s5 every invoice checked for duplicates"), not in YAML. Cutting a policy-required control is therefore a code-reviewed change.

Every run records the resolved pipeline name, version and digest (`/healthz`, run state, audit `workflow.planned`).

## 5b. Policy change (maintenance release)

A policy change is never live ([governance.md](governance.md)):
1. edit the policy file in `finance_rag_corpus/`;
2. update the affected controls / skills (section 6) and `config/policy_parameters.yaml`, with tests;
3. `make test eval`;
4. `finagent policy baseline --reviewer <id> --note "<why>"`;
5. deploy / restart. Until then the service holds every case (`/healthz` shows `"policy": "maintenance_required"`).

## 6. New control skill (a pack: procedure + script + references)

Every control skill is a pack with the same contract (see [skills.md](skills.md)):
- `make new-skill NAME=my_check POLICY="FIN-POL-013 s2"` scaffolds the deterministic script (plugin), `skills/my_check/SKILL.md`
  (spec + procedure template) and fixtures;
- write the procedure (what to read, what to judge, which policy section or reference decides it), add reference docs
  and any read-only scripts;
- the script does the maths; the LLM follows the procedure; the runner validates and merges caution-only.

The details of the deterministic script follow.

### 6a. The deterministic script

```python
from finagent.skills.base import SkillOutput, skill
from finagent.schemas import Outcome

@skill("contract_rate_check", "processor", "Unit price must match the contract rate card.",
       compliance_refs=[("FIN-POL-002", "s2")], retrieval_queries=["unit price contract rate tolerance"],
       version="1.0", mandatory=False, produces=("rate_card_id",))
def contract_rate_check(ev, prior=None) -> SkillOutput:
    out = SkillOutput("contract_rate_check")
    ...  # read ev["invoice"], ev["po"]; never call tools or the model
    out.finding("rate_card", "FIN-POL-002 s2", "PASS", "...", [("FIN-POL-002", "s2")], ["po:..."])
    return out
```

Contract for processors:

- Pure function of `(evidence bundle, prior outputs)`. No I/O.
- Every finding and exception carries `policy_refs`. The grounding gate requires them to be retrieved.
- Votes may be any outcome. The engine takes the most conservative one, and the model can never relax it.

Checklist:

1. Fixtures: at least 3, with a pass path and a fail path.
2. `finagent skills test contract_rate_check`.
3. `finagent skills docs`, which refreshes `references/policy.md` (injected policy text) and the catalogue `skills/README.md` (never the authored `SKILL.md`).
4. `make test && make eval`.

## 7. Add a document or skill, then re-train (end to end)

```bash
# 1. policy document
cp my_policy.md finance_rag_corpus/13_contractor_timesheets.md      # front matter: document_id FIN-POL-013, status: current
# 2. skill
make new-skill NAME=contractor_timesheet_check POLICY="FIN-POL-013 s2" WHEN=has_po
#    -> edit plugins/skills/contractor_timesheet_check.py and skills/contractor_timesheet_check/tests/fixtures.yaml
#    -> add the printed plugins entry + stage entry to config/workflow.yaml
finagent workflow validate && finagent skills test contractor_timesheet_check
# 3. held-out questions for the new sections -> training/embeddings/natural_questions.yaml
# 4. everything else
make retrain            # EMB=docker|host|skip ; FORCE: python scripts/training/retrain.py --force
```

What `retrain` does, and what it refuses:

- **Refuses** to continue if a skill has no passing fixtures, or if the workflow is invalid (e.g. a mandatory control was removed).
- The **new skill's retrieval queries** and **new policy sections** automatically become embedding training data. Plugin skills are loaded through `config/workflow.yaml`.
- The previous embedding model is archived in `training/embeddings/archive/`. A new model that scores lower on held-out recall@5 (or ranks a non-authoritative document first) is **rejected and the old one restored**, unless you pass `--force`.
- Fixture patches can append items with `docs.+`, e.g. to add a supporting document.
- **If a new control changes existing outcomes, the scenario evaluation fails on purpose.** For example, a timesheet rule holds every hourly-services invoice without a timesheet. Either update the affected cases (`scripts/generate_mock_data.py`, then `make seed`) or confirm their new expected outcome, then re-run. Any failure restores the previous embedding model, so an un-gated model is never left in place.
