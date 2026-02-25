#!/usr/bin/env bash
# payment_errors.sh – inject/clear payment 5xx errors
set -euo pipefail

PAYMENTS_URL="${PAYMENTS_URL:-http://localhost:8002}"
ACTION="${1:-enable}"
ERROR_RATE="${2:-0.20}"

case "$ACTION" in
  enable)
    echo "Enabling payment errors: ${ERROR_RATE} error rate"
    curl -s -X POST "${PAYMENTS_URL}/chaos/config?error_rate=${ERROR_RATE}" | jq .
    ;;
  disable)
    echo "Disabling payment errors"
    curl -s -X DELETE "${PAYMENTS_URL}/chaos/config" | jq .
    ;;
  *)
    echo "Usage: $0 [enable|disable] [error_rate 0.0-1.0]"
    exit 1
    ;;
esac
