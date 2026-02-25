#!/usr/bin/env bash
# payment_slow.sh – inject/clear payment latency
set -euo pipefail

PAYMENTS_URL="${PAYMENTS_URL:-http://localhost:8002}"
ACTION="${1:-enable}"
LATENCY_MS="${2:-3000}"

case "$ACTION" in
  enable)
    echo "Enabling payment slowness: ${LATENCY_MS}ms latency"
    curl -s -X POST "${PAYMENTS_URL}/chaos/config?latency_ms=${LATENCY_MS}" | jq .
    ;;
  spike)
    echo "Spiking payment latency to 20000ms"
    curl -s -X POST "${PAYMENTS_URL}/chaos/config?latency_ms=20000" | jq .
    ;;
  disable)
    echo "Disabling payment slowness"
    curl -s -X DELETE "${PAYMENTS_URL}/chaos/config" | jq .
    ;;
  *)
    echo "Usage: $0 [enable|spike|disable] [latency_ms]"
    exit 1
    ;;
esac
