# Environment: AWS (Terraform)

Infrastructure as code is in `infra/terraform/`. It passes `terraform validate` and `fmt`. It **has not been applied** as part of this submission; apply it in your own sandbox account.

## What gets created

| Resource | Purpose | Notes |
|---|---|---|
| VPC, 2 public subnets, IGW | network | POC: no NAT gateway (cost). The task SG only accepts traffic from the ALB |
| ALB + target group + listener | API entry point | `allowed_cidrs` only; HTTPS when `acm_certificate_arn` is set |
| ECS cluster + Fargate service (1 task) | runs `finagent serve` | `desired_count = 1` and `minimum_healthy_percent = 0`: **single SQLite writer** |
| ECR (immutable tags, scan on push, KMS) | images | lifecycle keeps 10 |
| EFS (encrypted, access point uid 10001) | `/data`: SQLite, index, vectors, audit JSONL | IAM-authorised, TLS in transit |
| S3 bucket (versioned, KMS, public access blocked) | corpus source of truth | `scripts/aws/sync_corpus.sh` |
| Secrets Manager | webhook HMAC secret, API key (generated), optional Anthropic key | injected as ECS secrets; never in code or tfvars |
| IAM execution + task roles | least privilege | task role can invoke **only** the configured Bedrock model, read the corpus bucket and mount its EFS access point. Nothing can move money. |
| CloudWatch log group | logs | 30-day retention by default |
| EventBridge Scheduler (every 5 min) | `finagent webhooks deliver && finagent resume --all` | drains the outbox, resumes interrupted runs |

The architecture diagram is in [../architecture.md §7](../architecture.md#7-deployment-views).

## Prerequisites

- An AWS account and a role with permissions for the services above (sandbox account recommended).
- AWS CLI v2, Terraform ≥ 1.6, Docker with buildx, and `jq`.
- **Bedrock model access** enabled in the region for the configured model (`bedrock_model_id`, default `anthropic.claude-opus-5-5`). Alternatively set `llm_provider = "mock"` for a zero-model-cost demo.

## Deploy

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars      # set allowed_cidrs, region, llm_provider, image_tag
# optional remote state: cp backend.tf.example backend.tf && edit
cd ../..
scripts/aws/bootstrap.sh       # init, create ECR, build+push image, apply, smoke test
```

Outputs (`terraform -chdir=infra/terraform output`):

- `api_url`, `swagger_url`
- `ecr_repository_url`, `ecs_cluster`, `ecs_service`, `corpus_bucket`, `log_group`
- secret ARNs

Read the API key:

```bash
aws secretsmanager get-secret-value --secret-id "$(terraform -chdir=infra/terraform output -raw api_key_secret_arn)" --query SecretString --output text
```

Day-2 commands (release, rollback, scale, logs, exec, corpus updates) are in [../commands/cloud_deployment.md](../commands/cloud_deployment.md).

## Configuration in the cloud

ECS environment variables are set in `infra/terraform/ecs.tf` (`locals.environment`):

- `FINAGENT__LLM__PROVIDER`, `FINAGENT__LLM__PROVIDERS__BEDROCK__MODEL`, `..._REGION`
- `FINAGENT__APP__DATA_DIR=/data`, `FINAGENT__APP__FIXED_NOW`

Secrets come from Secrets Manager: `FINAGENT__WEBHOOKS__SIGNING_SECRET`, `FINAGENT__APP__API_KEY` and optionally `ANTHROPIC_API_KEY`.

## Cost (POC, ap-southeast-2, approximate on-demand)

| Item | ~USD / month |
|---|---|
| Fargate 0.5 vCPU / 1 GB, 24×7 | 18–20 |
| ALB | 20–25 |
| EFS (a few MB) + Secrets (3) + ECR + S3 + logs | 3–5 |
| Scheduler tasks (8,640 × ~20 s) | 2–4 |
| Bedrock | per use: about 6k input + 0.6k output tokens per run (see Bedrock pricing) |
| **Total idle** | **about 45–55** |

Ways to save:

- `scripts/aws/scale.sh 0` stops the task (the ALB still bills).
- `enable_webhook_retry_schedule = false`.
- `llm_provider = "mock"` removes model cost.

## Cleanup

```bash
scripts/aws/destroy.sh          # type 'destroy' to confirm: removes EFS data, images, bucket, secrets (no recovery window)
```

Check afterwards:

- no `finagent-*` resources remain in ECS, ELB, EFS, ECR, S3 or Secrets Manager;
- remote state, if used, is deleted separately.

## Production hardening (not in the POC)

| Area | Change |
|---|---|
| Network | Private subnets + NAT or VPC endpoints (ECR, Logs, Secrets, Bedrock, S3); WAF on the ALB; TLS only |
| Persistence | RDS Postgres / Aurora for runs, approvals and outcomes; multiple tasks with row leases; pgvector or OpenSearch vectors |
| Messaging | SQS/EventBridge for the webhook outbox |
| Identity | Cognito / OIDC SSO for approvers, ALB authentication |
| Audit | Audit export to S3 Object Lock (compliance mode); retention and legal-hold jobs |
| Operations | AWS Backup for the data store; OpenTelemetry → X-Ray / CloudWatch; alarms on FAILED runs, DEAD webhooks and audit chain failures |
| Model | Bedrock guardrails, no-training data regions, and per-environment model access policies |

## Policy changes in the cloud

- `config/policy_baseline.yaml` and `config/policy_parameters.yaml` ship in the image (reviewed in git). A policy change is
  a maintenance release: a new image (`make aws-deploy`); the running task never changes its policy.
- At boot the image re-indexes the corpus. If the corpus differs from the baseline, every case is held and `/healthz`
  shows `"policy": "maintenance_required"`.
