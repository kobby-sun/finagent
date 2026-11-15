#!/usr/bin/env bash
# Tail application logs (CloudWatch). Audit events are also queryable via GET /runs/{id}/audit.
set -euo pipefail
. "$(dirname "$0")/_env.sh"
aws logs tail "$(tf_out log_group)" --region "$AWS_REGION" --follow --since "${SINCE:-15m}"
