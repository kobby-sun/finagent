#!/usr/bin/env bash
# Roll back to a previously pushed tag:  scripts/aws/rollback.sh v0.1.0
set -euo pipefail
. "$(dirname "$0")/_env.sh"
TAG="${1:?usage: rollback.sh <image_tag>}"
terraform -chdir="$TF_DIR" apply -input=false -auto-approve -var "image_tag=$TAG"
aws ecs wait services-stable --region "$AWS_REGION" --cluster "$(tf_out ecs_cluster)" --services "$(tf_out ecs_service)"
"$(dirname "$0")/smoke.sh"
