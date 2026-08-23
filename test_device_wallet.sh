#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-https://aibilingserver-production.up.railway.app}"
APP_NAME="${APP_NAME:-OPR Android}"
DEVICE_ID="${DEVICE_ID:-android:test-device-v09}"

printf '\n== HEALTH ==\n'
curl -sS "$BASE_URL/health"; echo

printf '\n== DEVICE BOOTSTRAP ==\n'
curl -sS -X POST "$BASE_URL/v1/device/bootstrap" \
  -H 'Content-Type: application/json' \
  -d "{\"app_name\":\"$APP_NAME\",\"device_id\":\"$DEVICE_ID\"}"; echo

printf '\nRun bootstrap again with the same DEVICE_ID: balance should be the same and no second trial bonus should be added.\n'
