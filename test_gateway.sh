#!/usr/bin/env bash
set -euo pipefail

: "${BASE_URL:?Set BASE_URL, e.g. https://your-service.up.railway.app}"
: "${APP_API_KEY:?Set APP_API_KEY}"
: "${EXTERNAL_USER_ID:=guru-001}"
: "${MODEL:=gpt-5.6-luna}"

BASE_URL="${BASE_URL%/}"

echo "== health =="
curl -fsS "$BASE_URL/health"; echo

echo "== wallet =="
curl -fsS "$BASE_URL/v1/wallet?external_user_id=$EXTERNAL_USER_ID" \
  -H "X-API-Key: $APP_API_KEY"; echo

echo "== configured models =="
curl -fsS "$BASE_URL/v1/ai/models" \
  -H "X-API-Key: $APP_API_KEY"; echo

echo "== AI chat =="
curl -fsS -X POST "$BASE_URL/v1/ai/chat" \
  -H "X-API-Key: $APP_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"external_user_id\":\"$EXTERNAL_USER_ID\",\"model\":\"$MODEL\",\"input\":\"Jawab satu ayat: server AI berjaya.\",\"max_output_tokens\":100,\"idempotency_key\":\"smoke-$(date +%s)-$$\",\"metadata\":{\"feature\":\"smoke_test\"}}"; echo
