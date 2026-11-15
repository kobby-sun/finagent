#!/usr/bin/env bash
# Post-deploy smoke test: health, workflow digest, and the five required cases via the evaluation endpoint.
set -euo pipefail
. "$(dirname "$0")/_env.sh"
API=$(tf_out api_url)
KEY=$(aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "$(tf_out api_key_secret_arn)" --query SecretString --output text)
curl -fsS "$API/healthz" | jq .
curl -fsS -H "X-API-Key: $KEY" -H "Content-Type: application/json" -X POST "$API/evaluations/run" \
  --data '{"cases":["FIN-001","FIN-002","FIN-003","FIN-004","FIN-005"]}' | jq -e '.summary | {passed, failed, scenarios} | select(.failed == 0)'
