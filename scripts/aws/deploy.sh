#!/usr/bin/env bash
# Application release: build/push a new immutable tag, update the task definition, wait for stability, smoke test.
#   IMAGE_TAG=v0.1.1 scripts/aws/deploy.sh
set -euo pipefail
. "$(dirname "$0")/_env.sh"
"$(dirname "$0")/build_push.sh"
terraform -chdir="$TF_DIR" apply -input=false -auto-approve -var "image_tag=$IMAGE_TAG"
aws ecs wait services-stable --region "$AWS_REGION" --cluster "$(tf_out ecs_cluster)" --services "$(tf_out ecs_service)"
"$(dirname "$0")/smoke.sh"
