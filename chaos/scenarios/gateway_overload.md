# Chaos Scenario: Gateway Overload

## What
Drive the gateway at 10x normal RPS to trigger the load-shedding middleware.

## Why
Tests backpressure admission control.  Without load shedding, all workers block,
and latency for all users degrades.  With load shedding, excess requests receive
429 quickly, preserving latency for the requests that are admitted.

## How to Enable
```bash
./chaos/scripts/gateway_overload.sh enable
# Runs k6 spike test at 500 VUs
```

## Expected Behavior
- Admitted requests: p99 latency < 500ms
- Shed requests: immediate 429 with Retry-After
- load_shed_total counter climbs during overload
- Service remains responsive after overload clears
