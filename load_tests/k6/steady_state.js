/**
 * k6 Steady-State Load Test
 *
 * Runs a realistic order creation flow against the gateway.
 * Supports configuration via environment variables:
 *   VUS=10 (virtual users)
 *   DURATION=60s
 *   GATEWAY_URL=http://localhost:8000
 *   USE_IDEMPOTENCY_KEYS=true
 *   AGGRESSIVE_RETRIES=false  (for retry storm scenario)
 *   CLIENT_CONFIG=D  (A=none, B=retries, C=+timeouts, D=+breaker, E=+bulkhead, F=+idempotency)
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";
import { uuidv4 } from "https://jslib.k6.io/k6-utils/1.4.0/index.js";

// ── Custom metrics ─────────────────────────────────────────────────────────
const orderSuccessRate = new Rate("order_success_rate");
const orderDuration = new Trend("order_duration_ms", true);
const retryCount = new Counter("client_retries_total");
const idempotencyHits = new Counter("client_idempotency_hits");
const errorsByType = new Counter("errors_by_type");

// ── Config ─────────────────────────────────────────────────────────────────
const GATEWAY_URL = __ENV.GATEWAY_URL || "http://localhost:8000";
const USE_IDEMPOTENCY = (__ENV.USE_IDEMPOTENCY_KEYS || "true") === "true";
const AGGRESSIVE_RETRIES = (__ENV.AGGRESSIVE_RETRIES || "false") === "true";
const CONFIG = __ENV.CLIENT_CONFIG || "D";

// ── k6 options ─────────────────────────────────────────────────────────────
export const options = {
  scenarios: {
    steady_state: {
      executor: "constant-vus",
      vus: parseInt(__ENV.VUS || "10"),
      duration: __ENV.DURATION || "60s",
    },
  },
  thresholds: {
    order_success_rate: ["rate>0.95"],
    order_duration_ms: ["p(95)<2000"],
    http_req_failed: ["rate<0.05"],
  },
};

// ── Products ───────────────────────────────────────────────────────────────
const PRODUCTS = ["prod-001", "prod-002", "prod-003", "prod-004"];

function randomItem() {
  const product = PRODUCTS[Math.floor(Math.random() * PRODUCTS.length)];
  return {
    product_id: product,
    quantity: Math.floor(Math.random() * 3) + 1,
    unit_price: (Math.random() * 100 + 10).toFixed(2),
  };
}

function createOrderPayload() {
  const numItems = Math.floor(Math.random() * 3) + 1;
  const items = Array.from({ length: numItems }, randomItem);
  return {
    customer_id: `customer-${Math.floor(Math.random() * 100)}`,
    items: items,
  };
}

// ── Retry logic ────────────────────────────────────────────────────────────
function makeRequestWithRetry(url, payload, headers, maxRetries, backoffBase) {
  let attempt = 0;
  let response;

  while (attempt <= maxRetries) {
    response = http.post(url, JSON.stringify(payload), { headers, timeout: "15s" });

    if (response.status < 500 || attempt >= maxRetries) {
      return { response, attempts: attempt + 1 };
    }

    attempt++;
    retryCount.add(1);

    if (AGGRESSIVE_RETRIES) {
      // No backoff – worst case retry storm
      sleep(0);
    } else {
      // Exponential backoff with jitter
      const delay = Math.min(backoffBase * Math.pow(2, attempt - 1), 5);
      const jitter = Math.random() * delay;
      sleep((delay + jitter) / 1000);
    }
  }

  return { response, attempts: attempt + 1 };
}

// ── Main VU loop ───────────────────────────────────────────────────────────
export default function () {
  const payload = createOrderPayload();
  const idempotencyKey = USE_IDEMPOTENCY ? uuidv4() : null;

  const headers = {
    "Content-Type": "application/json",
    "X-Correlation-ID": uuidv4(),
  };

  if (idempotencyKey) {
    headers["Idempotency-Key"] = idempotencyKey;
  }

  // Config-based retry settings
  let maxRetries = 0;
  let backoffBase = 0.1;

  if (CONFIG === "B" || AGGRESSIVE_RETRIES) {
    maxRetries = AGGRESSIVE_RETRIES ? 3 : 2;
    backoffBase = AGGRESSIVE_RETRIES ? 0 : 0.1;
  } else if (["C", "D", "E", "F"].includes(CONFIG)) {
    maxRetries = 2;
    backoffBase = 0.1;
  }

  const start = Date.now();
  const { response, attempts } = makeRequestWithRetry(
    `${GATEWAY_URL}/orders`,
    payload,
    headers,
    maxRetries,
    backoffBase
  );
  const elapsed = Date.now() - start;

  orderDuration.add(elapsed);

  const success = response.status === 201 || response.status === 202;
  orderSuccessRate.add(success);

  // Track idempotency replays
  if (response.headers["X-Idempotency-Replayed"] === "true") {
    idempotencyHits.add(1);
  }

  if (!success) {
    const errType = response.status >= 500 ? "5xx" : response.status === 429 ? "429" : "4xx";
    errorsByType.add(1, { type: errType });
  }

  check(response, {
    "status is 2xx": (r) => r.status >= 200 && r.status < 300,
    "response has order_id": (r) => {
      try {
        return JSON.parse(r.body).order_id !== undefined;
      } catch {
        return false;
      }
    },
  });

  // Optionally test idempotency by replaying the same key
  if (USE_IDEMPOTENCY && idempotencyKey && Math.random() < 0.1) {
    const replayResp = http.post(
      `${GATEWAY_URL}/orders`,
      JSON.stringify(payload),
      { headers, timeout: "10s" }
    );
    if (replayResp.headers["X-Idempotency-Replayed"] === "true") {
      idempotencyHits.add(1);
    }
  }

  sleep(Math.random() * 1 + 0.5); // 0.5–1.5s think time
}
