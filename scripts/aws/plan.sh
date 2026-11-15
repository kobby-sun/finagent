#!/usr/bin/env bash
# Review infrastructure changes before applying (always run before deploy.sh for infra edits).
set -euo pipefail
. "$(dirname "$0")/_env.sh"
terraform -chdir="$TF_DIR" plan -input=false -var "image_tag=${IMAGE_TAG}" -out=tfplan
