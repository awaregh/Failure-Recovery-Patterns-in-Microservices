#!/usr/bin/env bash
# inventory_lock.sh – inject/clear inventory DB lock contention
set -euo pipefail

INVENTORY_URL="${INVENTORY_URL:-http://localhost:8003}"
ACTION="${1:-enable}"
LOCK_MS="${2:-2000}"

case "$ACTION" in
  enable)
    echo "Enabling inventory lock contention: ${LOCK_MS}ms"
    curl -s -X POST "${INVENTORY_URL}/chaos/config?lock_contention_ms=${LOCK_MS}" | jq .
    ;;
  disable)
    echo "Disabling inventory lock contention"
    curl -s -X DELETE "${INVENTORY_URL}/chaos/config" | jq .
    ;;
  *)
    echo "Usage: $0 [enable|disable] [lock_ms]"
    exit 1
    ;;
esac
