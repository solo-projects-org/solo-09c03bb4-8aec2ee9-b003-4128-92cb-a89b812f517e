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

echo "== schedule conflict (HTTP 409) =="
curl -sS -i -X POST "${BASE_URL}/api/v1/assessments" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request_conflict.json
