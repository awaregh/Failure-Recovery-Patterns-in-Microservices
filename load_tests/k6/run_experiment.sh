#!/usr/bin/env bash
# run_experiment.sh – run all configurations and collect results
#
# Usage:
#   ./load_tests/k6/run_experiment.sh [scenario] [duration]
#
# Example:
#   ./load_tests/k6/run_experiment.sh payment_errors 120s

set -euo pipefail

SCENARIO="${1:-baseline}"
DURATION="${2:-60s}"
GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"
PAYMENTS_URL="${PAYMENTS_URL:-http://localhost:8002}"
INVENTORY_URL="${INVENTORY_URL:-http://localhost:8003}"
RESULTS_DIR="results/${SCENARIO}"

mkdir -p "${RESULTS_DIR}"

run_config() {
  local config="$1"
  local description="$2"
  echo ""
  echo "═══════════════════════════════════════════════════════"
  echo " Config ${config}: ${description}"
  echo "═══════════════════════════════════════════════════════"

  local out_dir="${RESULTS_DIR}/${config}"
  mkdir -p "${out_dir}"

  k6 run \
    --env GATEWAY_URL="${GATEWAY_URL}" \
    --env CLIENT_CONFIG="${config}" \
    --env DURATION="${DURATION}" \
    --env VUS=20 \
    --env USE_IDEMPOTENCY_KEYS="$([ "${config}" = "F" ] && echo true || echo false)" \
    --out json="${out_dir}/k6_results.json" \
    load_tests/k6/steady_state.js \
    2>&1 | tee "${out_dir}/k6_output.txt"

  echo "Results saved to ${out_dir}/"
}

# Apply / clear chaos scenario
enable_chaos() {
  case "$SCENARIO" in
    payment_errors)
      bash chaos/scripts/payment_errors.sh enable 0.20
      ;;
    payment_slow)
      bash chaos/scripts/payment_slow.sh enable 3000
      ;;
    inventory_lock)
      bash chaos/scripts/inventory_lock.sh enable 2000
      ;;
    retry_storm)
      bash chaos/scripts/payment_errors.sh enable 0.20
      ;;
    baseline)
      ;;
    *)
      echo "Unknown scenario: ${SCENARIO}"
      ;;
  esac
}

disable_chaos() {
  bash chaos/scripts/payment_errors.sh disable 2>/dev/null || true
  bash chaos/scripts/payment_slow.sh disable 2>/dev/null || true
  bash chaos/scripts/inventory_lock.sh disable 2>/dev/null || true
}

echo "Scenario: ${SCENARIO} | Duration: ${DURATION}"
echo "Results directory: ${RESULTS_DIR}"

disable_chaos
enable_chaos

# Config A: baseline (no resilience) – would require deploying without libs
# For now we simulate by running with VUS=1 and no retries
run_config "A" "Baseline (no resilience)"

# Config B: retries only
run_config "B" "Retries only"

# Config C: retries + timeouts
run_config "C" "Retries + Timeouts"

# Config D: + circuit breakers
run_config "D" "Retries + Timeouts + Circuit Breakers"

# Config E: + bulkheads + load shedding
run_config "E" "+ Bulkheads + Load Shedding"

# Config F: + idempotency keys + outbox
run_config "F" "+ Idempotency Keys + Outbox"

disable_chaos

echo ""
echo "All configs complete. Analyze with:"
echo "  python analysis/scripts/analyze_results.py ${RESULTS_DIR}"
