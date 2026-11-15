#!/usr/bin/env bash
# First deploy: create ECR + everything else, build/push the image, roll the service.
set -euo pipefail
. "$(dirname "$0")/_env.sh"
[ -f "$TF_DIR/terraform.tfvars" ] || { echo "create $TF_DIR/terraform.tfvars from terraform.tfvars.example"; exit 1; }
terraform -chdir="$TF_DIR" init -input=false
terraform -chdir="$TF_DIR" apply -input=false -target=aws_ecr_repository.app -auto-approve
"$(dirname "$0")/build_push.sh"
terraform -chdir="$TF_DIR" apply -input=false -var "image_tag=$IMAGE_TAG"
"$(dirname "$0")/smoke.sh"
