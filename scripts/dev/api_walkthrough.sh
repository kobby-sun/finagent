#!/usr/bin/env bash
# End-to-end HTTP walkthrough with curl + jq against a running server (scripts/dev/serve.sh).
set -euo pipefail
cd "$(dirname "$0")/../.."
API="${API:-http://127.0.0.1:8080}"; H=(-H "Content-Type: application/json"); [ -n "${API_KEY:-}" ] && H+=(-H "X-API-Key: $API_KEY")
echo "# start FIN-001"; RUN=$(curl -s "${H[@]}" -X POST "$API/runs" --data @mock_data/cases/FIN-001/request.json)
echo "$RUN" | jq '{run_id, status, outcome: .result.recommendation.outcome, approval: .approval.approval_id}'
APR=$(echo "$RUN" | jq -r .approval.approval_id); RID=$(echo "$RUN" | jq -r .run_id)
echo "# approval explanation"; curl -s "${H[@]}" "$API/approvals/$APR" | jq '.explanation | {summary, amount_aud, required_approvals, citations}'
echo "# approve (twice, same callback id => replay)"
for i in 1 2; do curl -s "${H[@]}" -X POST "$API/approvals/$APR/decision" \
  --data '{"decision":"APPROVE","approver_id":"u-dd-01","callback_id":"walkthrough-1"}' | jq '{status, effect, replayed, run}'; done
echo "# outcome pool"; curl -s "${H[@]}" "$API/outcomes?case_id=FIN-001" | jq .
echo "# audit chain"; curl -s "${H[@]}" "$API/audit/verify" | jq .
echo "# evaluation"; curl -s "${H[@]}" -X POST "$API/evaluations/run" --data '{"cases":["FIN-001","FIN-002","FIN-003","FIN-004","FIN-005"]}' | jq .summary
