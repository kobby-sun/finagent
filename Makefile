# Convenience targets. All real logic lives in scripts/ and the finagent CLI.
.PHONY: api-smoke docker-test docker-eval docker-load control-pdf skill-check bootstrap policy api-pdf test-history diagram eval-diagrams new-skill retrain inbox webhook-demo start stop restart status logs up down down-clean dstatus dlogs seed load load-approve reset diagrams setup data ingest serve test test-llm eval skills-test skills-docs docs demo openapi contracts dataset docker-up \
        tf-validate aws-plan aws-bootstrap aws-deploy aws-smoke aws-stop aws-start aws-destroy

bootstrap:    ; scripts/dev/setup.sh && .venv/bin/python scripts/dev/devctl.py bootstrap --skip-setup $(if $(TRAIN),--train,)
setup:        ; scripts/dev/setup.sh
# --- service control (local venv) ---
start:        ; scripts/dev/ctl.sh start
stop:         ; scripts/dev/ctl.sh stop
restart:      ; scripts/dev/ctl.sh restart
status:       ; scripts/dev/ctl.sh status
logs:         ; scripts/dev/ctl.sh logs
# --- service control (docker) ---
up:           ; scripts/dev/ctl.sh up
down:         ; scripts/dev/ctl.sh down
down-clean:   ; scripts/dev/ctl.sh down-clean
dstatus:      ; scripts/dev/ctl.sh dstatus
dlogs:        ; scripts/dev/ctl.sh dlogs
# --- webhook approval testing ---
inbox:        ; .venv/bin/python scripts/dev/approval_inbox.py
webhook-demo: ; .venv/bin/python scripts/dev/approval_inbox.py --demo $(CASES) --duplicate
# --- test data ---
reset:        ; scripts/dev/ctl.sh reset
seed:         ; scripts/dev/ctl.sh seed
load:         ; scripts/dev/ctl.sh load $(CASES)
load-approve: ; scripts/dev/ctl.sh load-approve $(CASES)
data:         ; .venv/bin/python scripts/generate_mock_data.py && .venv/bin/python scripts/skills/write_fixtures.py
ingest:       ; scripts/dev/ingest.sh
serve:        ; scripts/dev/serve.sh
test:         ; scripts/dev/test.sh
test-llm:     ; scripts/dev/test_llm.sh $(PROVIDER)
eval:         ; scripts/dev/eval.sh
skills-test:  ; .venv/bin/finagent skills test
skills-docs:  ; .venv/bin/python scripts/skills/build_skill_docs.py
openapi:      ; .venv/bin/python scripts/export_openapi.py
contracts:    ; .venv/bin/finagent contracts export --out contracts
diagrams:     ; .venv/bin/python scripts/build_arch_pdf.py && .venv/bin/python scripts/build_presentation_pdf.py && .venv/bin/python scripts/build_workflow_pdf.py && .venv/bin/python scripts/build_infra_doc.py && .venv/bin/python scripts/build_db_doc.py && .venv/bin/python scripts/build_api_pdf.py
docs: skills-docs openapi contracts diagrams ; .venv/bin/python scripts/build_scenario_docs.py && scripts/dev/demo.sh > /dev/null
demo:         ; scripts/dev/demo.sh
new-skill:    ; .venv/bin/python scripts/skills/new_skill.py $(NAME) $(if $(POLICY),--policy $(POLICY),) $(if $(STAGE),--stage $(STAGE),) $(if $(WHEN),--when $(WHEN),)
skill-check:  ; .venv/bin/python scripts/dev/skill_model_check.py $(if $(SMALL),--small $(SMALL),) $(if $(LARGE),--large $(LARGE),) $(if $(CASES),--cases $(CASES),)
policy:       ; .venv/bin/finagent policy status
control-pdf:  ; scripts/build_control_pdf.sh
api-pdf:      ; .venv/bin/python scripts/export_openapi.py && .venv/bin/python scripts/build_api_pdf.py
test-history: ; .venv/bin/finagent eval --history $(or $(DIR),test_history) $(if $(CASES),--cases $(CASES),)
diagram:      ; .venv/bin/finagent diagram $(RUN) --format $(or $(FMT),pdf)
eval-diagrams:; .venv/bin/finagent eval --diagrams $(or $(FMT),pdf) $(if $(CASES),--cases $(CASES),)
retrain:      ; .venv/bin/python scripts/training/retrain.py $(if $(EMB),--embeddings $(EMB),)
dataset:      ; .venv/bin/python scripts/training/generate_dataset.py
docker-up:    ; python3 scripts/dev/docker_up.py   # same as make up
docker-test:  ; docker compose exec finagent python -m pytest -q -p no:cacheprovider   # every test, inside the running container
docker-eval:  ; docker compose exec finagent finagent eval                              # all scenarios, inside the running container
api-smoke:    ; python3 scripts/dev/api_smoke.py   # every API operation + Swagger examples against the running service
docker-load:  ; docker compose exec finagent python scripts/dev/load_cases.py $(if $(APPROVE),--with-approvals,) $(CASES)   # submit cases to the container
tf-validate:  ; cd infra/terraform && terraform fmt -check && terraform init -backend=false -input=false >/dev/null && terraform validate
aws-plan:     ; scripts/aws/plan.sh
aws-bootstrap:; scripts/aws/bootstrap.sh
aws-deploy:   ; scripts/aws/deploy.sh
aws-smoke:    ; scripts/aws/smoke.sh
aws-stop:     ; scripts/aws/scale.sh 0
aws-start:    ; scripts/aws/scale.sh 1
aws-destroy:  ; scripts/aws/destroy.sh
