/**
 * k6 Spike Test
 *
 * Sudden traffic spike to test load shedding and backpressure.
 * Ramps from 1 VU to 500 VUs in 30s, holds for 60s, then ramps down.
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend, Counter } from "k6/metrics";
import { uuidv4 } from "https://jslib.k6.io/k6-utils/1.4.0/index.js";

const GATEWAY_URL = __ENV.GATEWAY_URL || "http://localhost:8000";

const successRate = new Rate("spike_success_rate");
const shedRate = new Rate("spike_shed_rate");
const latency = new Trend("spike_latency_ms", true);

export const options = {
  scenarios: {
    spike: {
      executor: "ramping-vus",
      startVUs: 1,
      stages: [
        { duration: "10s", target: 10 },    // warm up
        { duration: "30s", target: 500 },   // spike
        { duration: "60s", target: 500 },   // sustained overload
        { duration: "30s", target: 10 },    // recovery
        { duration: "30s", target: 10 },    // post-recovery stability
      ],
    },
  },
  thresholds: {
    // During spike, success rate may drop; check recovery
    spike_success_rate: ["rate>0.5"],
  },
};

export default function () {
  const headers = {
    "Content-Type": "application/json",
    "Idempotency-Key": uuidv4(),
    "X-Correlation-ID": uuidv4(),
  };

  const payload = {
    customer_id: `customer-${__VU}`,
    items: [{ product_id: "prod-001", quantity: 1, unit_price: 29.99 }],
  };

  const start = Date.now();
  const resp = http.post(
    `${GATEWAY_URL}/orders`,
    JSON.stringify(payload),
    { headers, timeout: "10s" }
  );
  latency.add(Date.now() - start);

  successRate.add(resp.status >= 200 && resp.status < 300);
  shedRate.add(resp.status === 429);

  check(resp, {
    "not server error": (r) => r.status < 500,
  });

  sleep(0.1);
}
