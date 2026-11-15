#!/usr/bin/env bash
# Stop (0) or start (1) the service to save cost without destroying data. SQLite => never more than 1.
set -euo pipefail
. "$(dirname "$0")/_env.sh"
N="${1:?usage: scale.sh 0|1}"; [ "$N" -le 1 ] || { echo "max 1 task while using SQLite"; exit 1; }
aws ecs update-service --region "$AWS_REGION" --cluster "$(tf_out ecs_cluster)" --service "$(tf_out ecs_service)" --desired-count "$N" >/dev/null
echo "desired count = $N"
