#!/usr/bin/env bash
# Tear everything down (EFS data, ECR images, S3 corpus bucket, secrets). Irreversible.
set -euo pipefail
. "$(dirname "$0")/_env.sh"
read -r -p "Destroy ALL finagent AWS resources in $AWS_REGION (including EFS data)? type 'destroy': " a
[ "$a" = "destroy" ] || { echo "aborted"; exit 1; }
terraform -chdir="$TF_DIR" destroy -input=false
