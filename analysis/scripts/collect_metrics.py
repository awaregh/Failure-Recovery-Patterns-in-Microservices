#!/usr/bin/env python3
"""
collect_metrics.py – Scrape Prometheus metrics and save a snapshot.

Usage:
    python analysis/scripts/collect_metrics.py --output results/snapshot.json
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path


PROMETHEUS_URL = "http://localhost:9090"

QUERIES = {
    "order_success_rate_1m": 'rate(http_requests_total{service="orders",status=~"2.."}[1m])',
    "order_error_rate_1m": 'rate(http_requests_total{service="orders",status=~"5.."}[1m])',
    "gateway_p95_latency": 'histogram_quantile(0.95, rate(request_duration_seconds_bucket{service="gateway"}[1m]))',
    "orders_p95_latency": 'histogram_quantile(0.95, rate(request_duration_seconds_bucket{service="orders"}[1m]))',
    "retry_attempts_rate": "rate(retry_attempts_total[1m])",
    "breaker_payments_state": 'breaker_state{downstream="payments"}',
    "breaker_inventory_state": 'breaker_state{downstream="inventory"}',
    "bulkhead_rejections_rate": "rate(bulkhead_rejections_total[1m])",
    "idempotency_hits_rate": "rate(idempotency_hits_total[1m])",
    "load_shed_rate": "rate(load_shed_total[1m])",
    "outbox_pending": "outbox_pending",
}


def query_prometheus(query: str) -> list[dict]:
    url = f"{PROMETHEUS_URL}/api/v1/query?query={urllib.parse.quote(query)}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("data", {}).get("result", [])
    except Exception as exc:
        return [{"error": str(exc)}]


import urllib.parse


def main():
    global PROMETHEUS_URL
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="results/metrics_snapshot.json")
    parser.add_argument("--prometheus", default=PROMETHEUS_URL)
    args = parser.parse_args()

    PROMETHEUS_URL = args.prometheus

    snapshot = {
        "timestamp": time.time(),
        "datetime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "metrics": {},
    }

    for name, query in QUERIES.items():
        results = query_prometheus(query)
        snapshot["metrics"][name] = results
        print(f"  {name}: {len(results)} series")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2))
    print(f"\nSnapshot saved to {out}")


if __name__ == "__main__":
    main()
