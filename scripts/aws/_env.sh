# shellcheck shell=bash
# Shared settings for AWS scripts. Override via environment.
export AWS_REGION="${AWS_REGION:-ap-southeast-2}"
export TF_DIR="${TF_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../infra/terraform" && pwd)}"
export IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
tf_out() { terraform -chdir="$TF_DIR" output -raw "$1"; }
