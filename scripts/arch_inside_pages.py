"""'Inside the component' pages and API walk-through pages for docs/diagrams/architecture.pdf.
Content is written from the code (file names given on each box) - keep in step when the code changes."""
from build_arch_pdf import (AMBER, AMBER_S, BLUE, BLUE_S, GREEN, GREEN_S, GREY, GREY_S, INK, MUTED, NAVY_S, PURPLE,
                            PURPLE_S, RED, RED_S, Page, chip)

KC = {"in": (GREY, GREY_S), "out": (GREEN, GREEN_S), "warn": (RED, RED_S), "sim": (AMBER, AMBER_S), "code": (PURPLE, PURPLE_S)}


def _side(p: Page, x, title, items, color):
    p.rect(x, 80, 176, 478, (0.985, 0.988, 0.995), color, 0.8, True, r=8)
    p.text(x + 10, 96, title, 8.4, True, NAVY_S)
    y = 104
    for t, b, k in items:
        h = 22 + 7.6 * len(p.wrap(b, 148, 6.3))
        chip(p, x + 8, y, 160, h, t, b, {"in": "opt", "out": "real", "warn": "out", "sim": "sim", "code": "code"}[k])
        y += h + 5


def inside_page(p: Page, inputs, steps, outputs, notes, center_title):
    _side(p, 20, "Inputs", inputs, GREY_S)
    _side(p, 650, "Outputs / audit", outputs, GREEN_S)
    p.text(236, 96, center_title, 8.4, True, NAVY_S)
    # heights
    hs = [max(34, 24 + 8 * len(p.wrap(b, 360 if len(steps) <= 7 else 160, 6.6))) for _, b, _ in steps]
    two = sum(hs) + 6 * len(hs) > 450
    width = 400 if not two else 196
    if two:
        hs = [max(34, 24 + 8 * len(p.wrap(b, 168, 6.6))) for _, b, _ in steps]
    half = (len(steps) + 1) // 2 if two else len(steps)
    y0 = 106
    for i, ((t, b, tag), h) in enumerate(zip(steps, hs)):
        col = 0 if i < half else 1
        if i == half:
            y0 = 106
        x = 236 + col * 206
        p.rect(x, y0, width, h, BLUE, BLUE_S, 1.0, r=6)
        p.rect(x - 6, y0 + 4, 14, 14, NAVY_S, None, r=7)
        p.text(x + 1, y0 + 14, str(i + 1), 6.8, True, (1, 1, 1), "center")
        p.text(x + 14, y0 + 13, t, 7.8, True, INK)
        p.text(x + width - 6, y0 + 13, tag, 6.0, False, BLUE_S, "right")
        for j, ln in enumerate(p.wrap(b, width - 24, 6.6)):
            p.text(x + 14, y0 + 24 + j * 8, ln, 6.6, False, INK)
        nxt = i + 1 < len(steps) and (i + 1 != half)
        if nxt:
            p.arrow(x + width / 2, y0 + h, x + width / 2, y0 + h + 5)
        y0 += h + 5
    for j, n in enumerate(notes):
        p.text(236, 548 + j * 9.5, n, 6.6, False, MUTED)


# ============================================================================== engine
def page_inside_engine(p: Page):
    inside_page(p, [
        ("API / CLI calls", "POST /runs, /runs/{id}/resume, /runs/{id}/evidence, /approvals/{id}/decision, inbound approval webhook", "in"),
        ("Scheduler", "every 5 min: resume_all (crashed runs, resolved approvals not yet resumed)", "in"),
        ("Settings", "agent.max_steps 40, max_tool_calls 50, max_llm_calls 6, base currency, gst", "in"),
        ("Collaborators (injected)", "Store, AuditLog, ToolRunner, LLMProvider, Retriever, Webhooks, Pipeline, PolicyBaseline - built in services.py", "code"),
    ], [
        ("start_run", "Idempotency-Key -> same run returned (run.start_replayed). New run row with config manifest + index version; "
         "audit run.created; webhook run.created; claim_run CREATED->RUNNING (atomic)", "agent/graph.py"),
        ("_execute loop", "per step: step budget (40) -> node function -> audit node.completed (ms) -> CHECKPOINT state + result -> "
         "flush deferred webhooks (only after persist) -> continue while RUNNING", "graph.py"),
        ("12 nodes", "RECEIVE, PARSE_INPUTS (parsers), RETRIEVE_POLICY (plan, policy snapshot, queries via tool runtime), "
         "GATHER_EVIDENCE (tools), RECONCILE (skills, most-cautious vote), DRAFT (model), DECIDE, APPROVAL_GATE | HOLD_FINALIZE, "
         "SUBMIT_DECISION, FINALIZE | FINALIZE_REJECTED", "nodes"),
        ("DECIDE gates", "grounding (cited policy retrieved), freshness (policy unchanged), policy gate (text matches the reviewed baseline), "
         "model guard (may only escalate), deterministic confidence", "n_decide"),
        ("Approval desk", "decide_approval: callback replay (approval_id:callback_id) -> BEGIN IMMEDIATE -> authority limit, delegation "
         "dates, segregation of duties, one person per slot -> resolved? claim_run -> SUBMIT or FINALIZE_REJECTED", "_apply_decision"),
        ("Submit", "policy re-check before posting -> submit_finance_decision through tool runtime (guard + idempotency key)", "n_submit"),
        ("Failure + recovery", "exception / budget / permission -> _fail: FAILED + reason, pool FAILED, run.failed. resume() repairs "
         "resolved approvals; add_evidence -> new attempt (parent_run_id)", "graph.py"),
        ("Explainability", "_trace (step, actor, decision, reason, refs) per key step; reasoning() merges human decisions from audit; "
         "_auto_diagram (app.run_diagrams)", "graph.py"),
    ], [
        ("runs + checkpoints", "status, current node, full state after every step (resume point)", "out"),
        ("approvals + callbacks", "request with explanation, slots, decisions", "out"),
        ("outcome pool", "AWAITING_APPROVAL / HELD / ESCALATED / SUBMITTED / REJECTED_BY_APPROVER / FAILED", "out"),
        ("webhook outbox", "run.*, approval.*, decision.submitted (signed)", "out"),
        ("audit", "run.created, node.completed, decision.recommended, approval.*, run.failed ...", "out"),
        ("FinalResult", "typed result + reasoning; GET /runs/{id}, /reasoning, /diagram", "out"),
    ], ["The engine owns order, state and safety. It never computes finance rules (skills) or talks to systems directly (tool runtime).",
        "Exactly-once posting = checkpoint after every step + atomic claim_run + idempotency key in the ERP sandbox."],
        "Engine (agent/graph.py)  -  run lifecycle, from first call to final state")


# ============================================================================== skills
def page_inside_skills(p: Page):
    inside_page(p, [
        ("Skill pack (skills/<name>/)", "SKILL.md = spec (validated): evidence, fixed questions, allowed answers, "
         "answer -> verdict; references/ (policy.md injected + reference docs); tests/fixtures.yaml", "code"),
        ("Evidence bundle", "request, invoice, docs, vendor, po, history, fx, authority, params", "in"),
        ("config/workflow.yaml", "order + conditions (has_po, no_po, is_credit_note ...); mandatory controls protected", "in"),
        ("Models", "skills.llm.small (qwen2.5:7b) answers every question; skills.llm.large (qwen2.5:14b, swappable: "
         "32B / Claude / none) = second opinion", "in"),
    ], [
        ("Parsers (code)", "5 parsers (json, text, csv, email, pdf) -> finagent.document/1", "parsers/registry.py"),
        ("Scripts (all 11 controls)", "deterministic: Decimal maths, matching, limits, votes. Scripts see ONLY other "
         "scripts' results - never model output", "processors/controls.py"),
        ("Collect (code)", "4 judgement skills: code gathers the excerpts the spec names (redacted, labelled DATA)",
         "llm_runner.collect_evidence"),
        ("Ask small model", "fixed questions -> {answer from the list, verbatim quote}", "llm_runner.SkillRunner"),
        ("Check (code)", "all answered; answer allowed; quote really in the evidence; one repair round", "SkillRunner.check"),
        ("Large model (2nd vote)", "unless small is >= 99% sure all is clear (token probabilities): same questions; "
         "on disagreement large decides + warning", "SkillRunner.run"),
        ("Decide (code)", "answer -> verdict via the spec's 'on' table; FLAG -> indicator, HOLD -> exception + vote; "
         "unanswered -> HOLD", "llm_runner.merge"),
        ("Count (code)", "FIN-POL-005 s3 count over indicators from scripts AND answers: >= 2 -> ESCALATE",
         "llm_runner.count_indicators"),
    ], [
        ("state.skills", "findings, calculations, exceptions, votes (script + answers)", "out"),
        ("state.procedures", "per skill: status, verdict, decided by small / large, both models' answers", "out"),
        ("rules_outcome", "most cautious vote wins (APPROVE / HOLD / REJECT_* / ESCALATE)", "out"),
        ("Holds on failure", "model down / invalid answers / budget -> case held, never passed", "warn"),
        ("audit", "skill.processor, skill.procedure (status, verdict, decided_by, models)", "out"),
    ], ["The model only picks answers and copies quotes - it never counts, calculates, cites or chooses a verdict.",
        "104 script fixtures + runner tests + 9 judgement scenarios (FIN-064..072) + 22-case model gate (make skill-check)."],
        "Skills  -  code asks, the model answers, code decides")


# ============================================================================== RAG
def page_inside_rag(p: Page):
    inside_page(p, [
        ("Policy sources", "finance_rag_corpus/*.md, HTTP feed, connector plugins", "in"),
        ("Queries", "planned skills' queries + case notes (data only) + model's extra queries (max 2)", "in"),
        ("Caller role", "ap_agent / ap_clerk_readonly -> allowed classifications", "in"),
        ("Embedding model", "fine-tuned bge-small (models/embedder)", "code"),
    ], [
        ("Ingest", "connectors + loaders -> CorpusDocument (finagent.corpus/1); trust from front matter: current / superseded / untrusted", "rag/ingest.py"),
        ("Chunk", "one chunk per section, stable id FIN-POL-002@v2.4#s2, title + heading + tags + text", "ingest.py"),
        ("BM25 index", "tokenise + query expansion; k1 1.4, b 0.7; JSON file + in memory", "rag/index.py"),
        ("Vectors", "Embedder (sentence_transformers | ollama | hash) -> VectorStore (SQLite; pgvector / OpenSearch / Qdrant adapters); "
         "stale vectors removed", "rag/vectorstore.py"),
        ("Start-up only", "content hash of files; changed -> rebuild index + vectors; search model trained on an older corpus -> gated retrain, else BM25 only. Never reloads while running", "services.py"),
        ("Rank", "BM25 top 20 + vector top 20 -> weighted RRF fusion (lexical 1.0, vector 4.0, k 60)", "retriever._search"),
        ("Access filter", "drop chunks whose classification the caller may not see (FIN-POL-010 s3)", "retriever.py"),
        ("Trust re-rank", "superseded x0.35, untrusted x0.6 + warnings; strong non-authoritative hits shown labelled as risk evidence", "retriever.py"),
        ("Citations", "Chunk: id, document, version, section, status, trust, score, rank, citation, warnings -> RetrieveDocsOutput", "schemas.py"),
    ], [
        ("state.retrieval", "queries + best-scored chunk per id, index_version", "out"),
        ("Citations in findings", "only retrieved AUTHORITATIVE chunks (grounding gate)", "out"),
        ("current_chunk(id)", "used by the freshness gate at DECIDE and before posting", "out"),
        ("Policy baseline", "current sections + hashes, compared with config/policy_baseline.yaml", "out"),
        ("Fallback", "model missing -> BM25 only (degraded shown in /healthz)", "sim"),
        ("audit", "tool.call retrieve_finance_documents per query (chunk ids, ranks, trust)", "out"),
    ], ["Called only through the tool runtime (retrieve_finance_documents). Policy text is data for citations and the model -",
        "the rules themselves run in skills; a policy change is a maintenance release (tests + re-baseline + restart)."], "RAG retriever  -  ingestion (left half of the flow) and search")


# ============================================================================== model
def page_inside_model(p: Page):
    inside_page(p, [
        ("Rules payload", "case fields + rules_assessment (outcome, exceptions, findings, calculations, unknowns, risks) + allowed_source_ids", "in"),
        ("Policy chunks", "top 16 retrieved chunks with trust / status labels", "in"),
        ("Untrusted text", "attachment text + request notes (fenced, redacted)", "in"),
        ("Config", "llm.provider, model id, max_validation_retries 2, on_invalid_output, max_llm_calls 6", "in"),
    ], [
        ("Prompt", "SYSTEM_PROMPT = only source of instructions (6 hard rules). <case_payload>, <retrieved_documents>, "
         "<untrusted_case_text>; bank / contact data redacted first", "llm/drafting.py"),
        ("Budget", "llm_calls + 1 per attempt; > 6 per run -> run FAILED", "n_draft"),
        ("Provider", "LLMProvider.complete(system, user, meta) -> LLMResponse(text, provider, model, request_id, latency, usage). "
         "mock (deterministic + fault injection) | ollama | anthropic (Claude API) | bedrock", "llm/providers.py"),
        ("Validate", "extract JSON -> ModelDraft schema (summary, proposed outcome, rationale, cited ids, inferences, unknowns, risk "
         "notes, <=2 extra queries, confidence) -> cited ids must be supplied ids; none superseded / untrusted", "validate_draft"),
        ("Repair loop", "invalid -> re-ask with the validator errors (up to 2) -> still invalid: flagged standard narrative (fallback) "
         "or run FAILED (on_invalid_output: fail)", "n_draft"),
        ("Extra retrieval", "additional_queries -> one more retrieval round, then redraft (max_extra_retrieval_rounds 1)", "n_draft"),
        ("Policy guard", "at DECIDE: model may only escalate (APPROVE -> HOLD / ESCALATE, HOLD -> ESCALATE). Relaxing -> "
         "model.override_blocked, confidence <= 0.5", "n_decide"),
        ("Also: skill questions", "the skill runner asks the judgement skills' questions through this interface at "
         "RECONCILE (skills.llm.small / large)", "skills/llm_runner.py"),
    ], [
        ("state.draft", "validated ModelDraft", "out"),
        ("Narrative", "summary + rationale shown to approvers (cited)", "out"),
        ("Escalation", "model.escalation_adopted when it is more cautious", "warn"),
        ("state.model", "provider, model, attempts, request ids, usage, status", "out"),
        ("audit", "model.call (valid / invalid_output / provider_error), model.fallback, model.override_blocked", "out"),
    ], ["Models answer the skills' fixed questions (caution only) and write the narrative; scripts do all maths and matching.",
        "Every output is schema- and citation-checked and can only make a decision more cautious. Provider = configuration only."],
        "Model adapter  -  narrative step (skill questions: previous page)")


# ============================================================================== policy baseline
def page_inside_governance(p: Page):
    inside_page(p, [
        ("Policy index", "current authoritative sections (built at start-up)", "in"),
        ("config/policy_baseline.yaml", "section hashes the hand-written controls were verified against, reviewer, date", "in"),
        ("config/policy_parameters.yaml", "values of the policy numbers the controls enforce + source sections", "in"),
    ], [
        ("params.py", "catalog: value, cite, unit; coerce; load_baseline; P(ev, name) for controls", "governance/"),
        ("baseline: compare", "current_sections (normalised hash per section) vs the baseline -> changed / new / removed", "baseline.py"),
        ("baseline: status", "ok | maintenance_required, unverified sections, reviewed by / at", "baseline.py"),
        ("Policy gate (DECIDE)", "any unverified section -> every case HELD (OTHER_CONTROL_RISK, policy.unverified)", "agent/graph.py"),
        ("Maintenance release", "edit policy -> update controls / skills / parameters + tests -> make test eval -> "
         "finagent policy baseline --reviewer <id> --note -> restart", "write_baseline"),
    ], [
        ("Run result", "result.policy: baseline_reviewed_by, baseline_reviewed_at, unverified_sections", "out"),
        ("Holds", "policy.unverified when the corpus differs from the baseline", "warn"),
        ("/healthz", '"policy": "ok" | "maintenance_required"', "out"),
        ("API / CLI", "GET /policy/status, /policy/parameters  -  finagent policy status | params | baseline", "out"),
    ], ["Policy changes are maintenance releases, never live: a running service never changes its policy or index.",
        "The baseline is a deliberate human act: a named reviewer records the texts the controls were verified against."],
        "Policy baseline (src/finagent/governance/)  -  modules")


LANES = ["API", "Engine", "Skills", "Policy", "Tool runtime", "RAG", "Systems*", "Model", "Store+hooks"]


def walk_page(p: Page, steps, notes):
    x0, lw = 352, 52
    for i, name in enumerate(LANES):
        x = x0 + i * lw
        p.rect(x + 2, 78, lw - 4, 18, (0.93, 0.95, 0.99), BLUE_S, 0.6, r=4)
        p.text(x + lw / 2, 90, name, 5.9, True, NAVY_S, "center")
        p.ops.append("0.4 w [2 2] 0 d"); p.color((0.8, 0.82, 0.86), True)
        p.ops.append(f"{x + lw / 2:.1f} {p._y(98):.1f} m {x + lw / 2:.1f} {p._y(560):.1f} l S"); p.ops.append("[] 0 d")
    p.text(24, 90, "Step  -  what happens (and what is passed)", 7.6, True, NAVY_S)
    y = 104
    prev = None
    for n, (lanes, text, color) in enumerate(steps, 1):
        lines = p.wrap(text, 318, 6.6)
        h = max(16, 6 + 8 * len(lines))
        p.text(24, y + 9, f"{n:>2}", 6.8, True, BLUE_S)
        for j, ln in enumerate(lines):
            p.text(38, y + 9 + j * 8, ln, 6.6, False, INK)
        xs = [x0 + LANES.index(l) * lw + lw / 2 for l in lanes]
        for x in xs:
            p.rect(x - 5, y + 3, 10, 10, color, None, r=5)
        if len(xs) > 1:
            p.ops.append(f"0.9 w"); p.color(color, True)
            p.ops.append(f"{min(xs):.1f} {p._y(y + 8):.1f} m {max(xs):.1f} {p._y(y + 8):.1f} l S")
        if prev is not None:
            p.arrow(prev[0], prev[1] + 6, xs[0], y + 3, rgb=(0.6, 0.63, 0.68), lw=0.6)
        prev = (xs[-1], y + 8)
        y += h + 2
    for j, t in enumerate(notes):
        p.text(24, y + 10 + j * 9, t, 6.6, False, MUTED)


B, G, R, A, P_ = BLUE_S, GREEN_S, RED_S, AMBER_S, PURPLE_S


def page_walk_run(p: Page):
    walk_page(p, [
        (["API"], "POST /runs  body = ProcessingRequest (FIN-001: invoice JSON attachment), header Idempotency-Key. X-API-Key checked; "
                  "pydantic strict validation (unknown field / bad amount -> 422, nothing stored).", B),
        (["API", "Engine", "Store+hooks"], "engine.start_run: same key -> return the existing run. Else insert run (CREATED, config "
                  "manifest, index version); audit run.created; outbox run.created; claim_run -> RUNNING.", B),
        (["Engine", "Store+hooks"], "RECEIVE: request recorded; checkpoint #1 (every node below ends with node.completed + checkpoint).", P_),
        (["Engine", "Skills"], "PARSE_INPUTS: each attachment -> parser router -> invoice_json_parser -> NormalizedDocument; primary invoice "
                  "chosen; audit skill.parser.", P_),
        (["Engine", "Skills", "Policy"], "RETRIEVE_POLICY (1/2): pipeline.plan(state) -> 12 planned / skipped skills (reasoning per skill). "
                  "Policy snapshot -> parameters + baseline status frozen into the run.", P_),
        (["Engine", "Tool runtime", "RAG"], "RETRIEVE_POLICY (2/2): ~24 queries; each = tool call retrieve_finance_documents -> budget, schema, "
                  "timeout -> retriever (BM25 + vectors, filters) -> ranked, trust-labelled chunks.", G),
        (["Engine", "Tool runtime", "Systems*"], "GATHER_EVIDENCE: get_vendor_record, get_purchase_order (PO-backed only), check_invoice_history "
                  "(with policy params), get_fx_rate (foreign only), get_authority_register -> backend (JSON mock today) -> typed outputs.", A),
        (["Engine", "Skills", "Model"], "RECONCILE: for each of the 11 control skills in order: deterministic script (Decimal maths, "
                  "policy refs) -> LLM follows SKILL.md, may call scripts -> validated -> merged (caution only); most cautious "
                  "vote -> rules_outcome; audit skill.processor + skill.procedure.", P_),
        (["Engine", "Model"], "DRAFT: prompt (rules payload + 16 chunks + fenced, redacted case text) -> provider.complete -> ModelDraft "
                  "validated (schema, cited ids); retry with errors if invalid; audit model.call.", A),
        (["Engine", "Policy", "RAG"], "DECIDE: grounding gate (cited sections retrieved), freshness gate (current_chunk unchanged), "
                  "policy gate (text differs from baseline -> held, policy.unverified), model guard (escalate only), confidence -> FinalResult.", P_),
        (["Engine", "Store+hooks"], "APPROVAL_GATE: approval row with explanation + required slots (primary: CCM limit); "
                  "outcome pool AWAITING_APPROVAL; checkpoint; THEN webhooks approval.requested + run.awaiting_approval.", R),
        (["Engine", "API"], "Run stops (status AWAITING_APPROVAL). Response 201 = run view: result, approval (approval_id, explanation), "
                  "reasoning; optional diagram files.", B),
    ], ["* Systems = reference systems behind the tool runtime (vendor master, ERP purchasing, AP history, FX, authority register;",
        "JSON mock today, REST / MCP / DB in production). Dot colours: blue API/engine, purple code, green search, amber external/model, red approval."])


def page_walk_decision(p: Page):
    walk_page(p, [
        (["API"], "POST /approvals/{approval_id}/decision  body = {decision: APPROVE, approver_id: u-ccm-01, callback_id, comment} "
                  "(or POST /webhooks/inbound/approval with HMAC signature; or finagent approve).", B),
        (["API", "Engine", "Store+hooks"], "decide_approval: callback cache (approval_id:callback_id) seen? -> replay the stored "
                  "response, no effect (approval.callback_replayed).", B),
        (["Engine", "Tool runtime", "Systems*"], "_apply_decision in BEGIN IMMEDIATE: still PENDING? (else 409). get_authority_register via tool runtime (unavailable -> 503, "
                  "nothing recorded): "
                  "approver limit >= amount AUD, delegation dates, flags; SoD: not vendor creator / recent changer / requester / receipter.", A),
        (["Engine", "Store+hooks"], "Refused -> 403 + approval.denied (reasons), approval stays pending. Allowed -> slot filled, "
                  "approval.decision_recorded; all slots filled -> approval.resolved APPROVED (or REJECTED with comment).", R),
        (["Engine", "Store+hooks"], "claim_run AWAITING_APPROVAL -> RUNNING (atomic: a second decision cannot resume it twice).", P_),
        (["Engine", "RAG"], "SUBMIT_DECISION: freshness re-check of every cited policy section (current_chunk). Changed? -> posting BLOCKED, "
                  "HOLD_FINALIZE (policy.changed_since_decision).", G),
        (["Engine", "Tool runtime", "Systems*"], "submit_finance_decision: guard (approval APPROVED, outcome / amount / currency / verb match, "
                  "idempotency key) -> ERP sandbox insert (same key -> DUPLICATE_IGNORED) -> posting_ref SIM-POST-...", R),
        (["Engine", "Store+hooks"], "FINALIZE: run COMPLETED; outcome pool SUBMITTED; checkpoint; webhooks decision.submitted, run.completed.", G),
        (["Engine", "API"], "Response 200 {status APPROVED, approval, run}. GET /runs/{id}/reasoning shows the human decision in order; "
                  "GET /runs/{id}/diagram draws the path.", B),
        (["API", "Policy"], "Separate read-only calls: GET /policy/status, GET /policy/parameters. Policy never changes while "
                  "the service runs (maintenance release + restart).", P_),
        (["API", "Store+hooks"], "Read-only calls (GET /runs, /outcomes, /approvals, /audit/verify, /webhooks/events) read the "
                  "store; no workflow component runs.", B),
    ], ["A rejection (decision REJECT + comment >= 5 chars) skips steps 6-7: FINALIZE_REJECTED -> COMPLETED, pool REJECTED_BY_APPROVER, nothing posted.",
        "* Systems = authority register for step 3, ERP posting sandbox for step 7 (both simulated today)."])


# ============================================================================== code map
CODE_MAP = [
    # (layer, component, files, key classes / functions, used by / how to use)
    ("Entry", "HTTP API", "api/app.py, api/examples.py", "create_app(App), get_app() (uvicorn factory); 35 operations; Swagger examples",
     "finagent serve / make start -> http://localhost:8080/docs"),
    ("Entry", "CLI", "cli.py", "main(); cmd_* per command (start, get, explain, diagram, approve, eval, policy ...)",
     "finagent <command> (.venv/bin/finagent; Windows .venv\\Scripts\\finagent.exe)"),
    ("Core", "Composition root", "services.py", "build_app(Settings) -> App(store, audit, retriever, backend, tools, llm, webhooks, "
     "pipeline, policy, engine)", "API, CLI, scripts and tests all start here"),
    ("Core", "Configuration", "config.py, config/*.yaml", "load_settings(), Settings.get / path / with_overrides / public_manifest; "
     "FINAGENT__SECTION__KEY env", "every component reads Settings; tests use with_overrides"),
    ("Core", "Agent engine", "agent/graph.py", "Engine.start_run, resume, resume_all, add_evidence, decide_approval, get_run, "
     "reasoning; n_* nodes; _execute", "API / CLI handlers; scheduler"),
    ("Core", "Deterministic maths", "agent/calc.py", "q() (Decimal half-up), calc() record, add_business_days, business_days_between",
     "skills (controls), engine"),
    ("Core", "Workflow pipeline", "workflow.py, config/workflow.yaml", "Pipeline (validate, plan, retrieval_plan, digest), "
     "load_pipeline, @condition, load_plugins", "engine at RETRIEVE_POLICY / RECONCILE; finagent workflow validate"),
    ("Skills", "Skill framework", "skills/base.py", "@skill, SkillSpec, SkillOutput (findings, calcs, exceptions, votes ...), REGISTRY",
     "every parser / control; plugins; make new-skill"),
    ("Skills", "Skill packs + runner", "skills/pack.py, skills/llm_runner.py, skills/<name>/",
     "SkillPackSpec (questions), load_pack, validate_all; SkillRunner.run (collect, ask, check, cascade), merge, count_indicators",
     "engine RECONCILE (4 judgement skills + indicator count); make new-skill; pytest test_skill_packs"),
    ("Skills", "Parsers", "skills/parsers/registry.py, documents.py, pdf.py", "@register_parser, route(), parse_attachment(); 5 parsers",
     "engine PARSE_INPUTS"),
    ("Skills", "Control scripts", "skills/processors/controls.py",
     "11 deterministic control scripts (maths, matching, limits, votes)",
     "engine RECONCILE, before any question"),
    ("Skills", "Skill tests", "skills/testing.py, skills/*/tests/fixtures.yaml", "SkillHarness.run_fixture / run_all, load_fixtures",
     "pytest tests/skills, finagent skills test [name], POST /skills/{name}/test"),
    ("Tools", "Tool runtime + tools", "tools/base.py, tools/finance_tools.py", "ToolRunner.call, ToolSpec, ToolResult; build_tools() "
     "registers 7 tools + submit guard", "engine _tool(); GET /catalog/tools"),
    ("Tools", "Reference-system backend", "tools/mock_backend.py, mock_data/", "MockBackend.vendor / purchase_order / fx / "
     "authority_register / history_records, maybe_fault", "tool functions; replace with REST / MCP / DB backend"),
    ("RAG", "Ingestion", "rag/ingest.py", "@register_connector, @register_loader, load_sources, classify_trust, chunk_corpus_document",
     "finagent ingest; start-up reindex"),
    ("RAG", "Indexes", "rag/index.py, rag/vectorstore.py", "BM25Index (score, save/load), build_and_save; Embedder / VectorStore "
     "registries, build_vectors", "retriever; make retrain"),
    ("RAG", "Retriever", "rag/retriever.py", "Retriever.search, current_chunk, describe",
     "tool retrieve_finance_documents; freshness gate; policy baseline"),
    ("Model", "Model adapter", "llm/providers.py, llm/drafting.py", "make_provider, LLMProvider.complete; SYSTEM_PROMPT, "
     "build_user_prompt, validate_draft", "engine DRAFT; skill questions"),
    ("Policy", "Policy numbers + baseline", "governance/params.py, baseline.py",
     "CATALOG, load_baseline, P(); PolicyBaseline.status / changes / write_baseline",
     "engine; /policy/*; finagent policy status|params|baseline; make policy"),
    ("Data", "Contracts", "schemas.py, contracts.py", "ProcessingRequest, FinalResult, ModelDraft, tool I/O models; NormalizedDocument, "
     "CorpusDocument; export_json_schemas", "every boundary; finagent contracts export; API PDF"),
    ("Data", "Store", "store.py", "Store.create_run, checkpoint, claim_run, create_approval, upsert_outcome, tx (BEGIN IMMEDIATE); SCHEMA",
     "engine, webhooks, evaluation"),
    ("Data", "Audit + redaction", "audit.py, redaction.py", "AuditLog.record / events / verify_chain; redact, redact_text, mask_account",
     "all components; GET /audit/verify, finagent audit-verify"),
    ("Data", "Webhooks", "webhooks.py", "Webhooks.emit (outbox), deliver_pending, subscribe, feed; sign / verify (HMAC)",
     "engine; /webhooks/*; make inbox"),
    ("Quality", "Evaluation + history", "evaluation.py, history.py, mock_data/scenarios.json", "run_evaluation, run_scenario, "
     "rag_metrics, format_report; export_history", "finagent eval [--history], make test-history"),
    ("Quality", "Run diagrams", "rundiagram.py", "render(run, svg|png|pdf), write(engine, run_id, fmt)", "finagent diagram, GET /runs/{id}/diagram"),
]


def page_code_map(part: int):
    def fn(p: Page):
        rows = CODE_MAP[:13] if part == 1 else CODE_MAP[13:]
        xs = [20, 74, 170, 316, 590]
        ws = [54, 96, 146, 274, 238]
        hdr = ["Layer", "Component", "Files (src/finagent/)", "Key classes / functions", "Used by / how to use"]
        y = 82
        for x, w, h in zip(xs, ws, hdr):
            p.rect(x, y, w, 16, (0.90, 0.93, 0.98), GREY_S, 0.4, r=0)
            p.text(x + 4, y + 11, h, 7.4, True, NAVY_S)
        y += 16
        colors = {"Entry": BLUE, "Core": PURPLE, "Skills": PURPLE, "Tools": BLUE, "RAG": GREEN, "Model": AMBER,
                  "Policy": GREEN, "Data": GREY, "Quality": GREY}
        for r in rows:
            cells = [p.wrap(c, w - 8, 6.7) for c, w in zip(r, ws)]
            h = max(len(c) for c in cells) * 8.2 + 7
            for i, (x, w, c) in enumerate(zip(xs, ws, cells)):
                p.rect(x, y, w, h, colors[r[0]] if i == 0 else (1, 1, 1), GREY_S, 0.4, r=0)
                for j, ln in enumerate(c):
                    p.text(x + 4, y + 10 + j * 8.2, ln, 6.7, i in (0, 1), INK)
            y += h
        p.text(20, 566, "Call direction: Entry -> Core (engine) -> Skills / Tools / RAG / Model / Policy -> Data. Skills never do I/O; "
               "the engine never computes rules; tools are the only way out to other systems.", 6.8, False, MUTED)
    return fn
