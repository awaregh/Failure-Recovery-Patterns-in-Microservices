#!/usr/bin/env bash
# retry_storm.sh – enable retry storm scenario
set -euo pipefail

PAYMENTS_URL="${PAYMENTS_URL:-http://localhost:8002}"
ACTION="${1:-enable}"

case "$ACTION" in
  enable)
    echo "=== Enabling retry storm scenario ==="
    echo "1. Setting 20% payment error rate"
    curl -s -X POST "${PAYMENTS_URL}/chaos/config?error_rate=0.20" | jq .
    echo ""
    echo "2. Run load test with aggressive retries:"
    echo "   k6 run --env AGGRESSIVE_RETRIES=true load_tests/k6/steady_state.js"
    ;;
  disable)
    echo "=== Disabling retry storm scenario ==="
    curl -s -X DELETE "${PAYMENTS_URL}/chaos/config" | jq .
    ;;
  *)
    echo "Usage: $0 [enable|disable]"
    exit 1
    ;;
esac
