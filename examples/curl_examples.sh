#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"

echo "== health =="
curl -sS "${BASE_URL}/health"
echo

echo "== main bound example =="
curl -sS -X POST "${BASE_URL}/api/v1/assessments" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request.json
echo

echo "== 100000-shift boundary =="
curl -sS -X POST "${BASE_URL}/evaluate" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request_boundary.json
echo

echo "== missing-measurement rejection (HTTP 422) =="
curl -sS -i -X POST "${BASE_URL}/api/v1/assessments" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request_blocked.json

echo "== 月末批量试算：缺实测格退回（HTTP 422，只做 1 次可行性求解，不取界） =="
curl -sS -i -X POST "${BASE_URL}/api/v1/assessments" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request_missing_measurement.json

echo "== 补报实测后正常出数（HTTP 200，精确分数与达界见证表） =="
curl -sS -X POST "${BASE_URL}/api/v1/assessments" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request_resupplied.json
echo

echo "== schedule conflict (HTTP 409) =="
curl -sS -i -X POST "${BASE_URL}/api/v1/assessments" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request_conflict.json
