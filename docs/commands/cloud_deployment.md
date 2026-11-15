# Commands: cloud deployment changes (AWS)

All scripts are in `scripts/aws/`. They read Terraform outputs from `infra/terraform`. Environment overrides: `AWS_REGION`, `IMAGE_TAG`, `TF_DIR`.

| Change | Command | What happens |
|---|---|---|
| First deployment | `scripts/aws/bootstrap.sh` | `terraform init` → create ECR → build and push image → full apply → smoke test |
| Review infra changes | `scripts/aws/plan.sh` | `terraform plan -out=tfplan` (always before applying infra edits) |
| Release new app version | `IMAGE_TAG=v0.1.1 scripts/aws/deploy.sh` | build and push an immutable tag → apply new task definition → wait for stability → smoke test |
| Roll back | `scripts/aws/rollback.sh v0.1.0` | re-point the service to a previous immutable tag → wait → smoke test |
| Corpus/policy update | `scripts/aws/sync_corpus.sh` then `deploy.sh` | S3 versioned upload; the new image re-indexes on boot; the index version changes and is recorded on every run |
| Workflow change (`config/workflow.yaml`) | `make test` → `deploy.sh` | the pipeline is validated at start-up; the digest appears in `/healthz` and on every run |
| Model switch | edit `llm_provider` / `bedrock_model_id` in tfvars → `plan.sh` → `terraform apply` | new task definition env; the IAM policy follows the model id |
| Rotate secrets | `terraform -chdir=infra/terraform apply -replace=random_password.webhook` (or `api_key`) then `deploy.sh` | new secret version; update webhook consumers |
| Smoke test | `scripts/aws/smoke.sh` | `/healthz` + FIN-001..005 evaluation must report 0 failures |
| Logs | `SINCE=1h scripts/aws/logs.sh` | CloudWatch tail |
| Run CLI in the task | `scripts/aws/exec.sh "finagent audit-verify"` | ECS Exec |
| Pause / resume compute | `scripts/aws/scale.sh 0` / `scale.sh 1` | stops cost for Fargate; data on EFS kept |
| Tear down | `scripts/aws/destroy.sh` | type `destroy`; deletes everything including EFS data |

Make targets wrap the same scripts: `make aws-plan`, `aws-bootstrap`, `aws-deploy`, `aws-smoke`, `aws-stop`, `aws-start`, `aws-destroy`, `tf-validate`.

## Skill models in the cloud (not provisioned by these scripts)

`make aws-deploy` ships the API container (the `finagent` CLI is inside the same image). It does **not** provision
the skill-question models (small `qwen2.5:7b` + large `qwen2.5:14b`, local Ollama by default). Without a reachable
model, judgement skills cannot be answered and those cases are **held** ("judgement questions not answered").
Choose one before a real deployment, then gate the pair (`make skill-check SMALL=… LARGE=…`):

| Option | Settings |
|---|---|
| Ollama on a GPU instance / ECS GPU task | `FINAGENT__LLM__PROVIDERS__OLLAMA__BASE_URL=http://<host>:11434` |
| Small local + Bedrock as the large model | `FINAGENT__SKILLS__LLM__LARGE__PROVIDER=bedrock` (+ model id; only cases with a concern reach it) |
| Demo only | `FINAGENT__SKILLS__LLM__USE_MOCK=true` |

## Release checklist

1. `make test && make eval` are green locally (or in CI).
2. `make tf-validate`, then `scripts/aws/plan.sh` if the infra changed. Review the plan for replacements of EFS, the ALB or secrets.
3. `IMAGE_TAG=<semver> scripts/aws/deploy.sh`.
4. The smoke test passes. Confirm the `/healthz` workflow digest and index version are as expected.
5. Record the tag, digest and index version in the change ticket.
6. If anything regresses, run `scripts/aws/rollback.sh <previous tag>`.

## Safety notes

- Never set `desired_count > 1` while SQLite is the store. `scale.sh` refuses, and the deployment settings stop two tasks from overlapping.
- In-flight runs survive restarts:
  - checkpoints on EFS plus the scheduled `resume --all` make interrupted runs continue;
  - approvals stay pending and can be decided after redeploy.
- Image tags are immutable; rollbacks are exact.
