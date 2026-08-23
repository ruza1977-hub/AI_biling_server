#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-https://aibilingserver-production.up.railway.app}"
APP_NAME="${APP_NAME:-OPR Android}"
DEVICE_ID="${DEVICE_ID:-android:test-device-v10}"
EMAIL="${EMAIL:-test-device-v10@example.com}"

printf '\n== HEALTH ==\n'
curl -sS "$BASE_URL/health"; echo

printf '\n== DEVICE BOOTSTRAP + EMAIL ==\n'
curl -sS -X POST "$BASE_URL/v1/device/bootstrap" \
  -H 'Content-Type: application/json' \
  -d "{\"app_name\":\"$APP_NAME\",\"device_id\":\"$DEVICE_ID\",\"email\":\"$EMAIL\"}"; echo

printf '\nBootstrap kali kedua dengan DEVICE_ID yang sama tidak patut memberi bonus kedua.\n'
