#!/usr/bin/env bash
# Run a finagent CLI command inside the running task (ECS Exec), e.g. scripts/aws/exec.sh "finagent audit-verify"
set -euo pipefail
. "$(dirname "$0")/_env.sh"
CLUSTER=$(tf_out ecs_cluster); SERVICE=$(tf_out ecs_service)
TASK=$(aws ecs list-tasks --region "$AWS_REGION" --cluster "$CLUSTER" --service-name "$SERVICE" --query 'taskArns[0]' --output text)
aws ecs execute-command --region "$AWS_REGION" --cluster "$CLUSTER" --task "$TASK" --container finagent --interactive --command "${1:-sh}"
