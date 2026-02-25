# Chaos Scenario: Payment Dependency Slow

## What
Inject artificial latency into the Payments service, simulating a slow external payment processor.

## Configuration
- **Baseline**: p95 latency ~100ms
- **Chaos active**: p95 latency ~3,000ms (3s), spike to 20,000ms (20s) during burst

## Why
Payment processors commonly degrade under load or during rolling deployments. This tests whether
the Orders service's bulkhead and timeout configuration prevents slow payments from exhausting
the connection pool and causing cascading failure across all order processing.

## How to Enable
```bash
./chaos/scripts/payment_slow.sh enable
# or via API:
curl -X POST "http://localhost:8002/chaos/config?latency_ms=3000"
```

## How to Disable
```bash
./chaos/scripts/payment_slow.sh disable
```

## Expected Behavior

### Without resilience (Config A):
- Orders service threads block on payment calls
- P95 latency climbs to match payment latency (3-20s)
- Thread pool exhaustion after ~30s
- Total service failure

### With circuit breaker + timeout (Config D+):
- Payments timeout after configured per-hop timeout (~8s)
- After 5 failures, circuit breaker opens
- Orders continue to process with `payment_failed` status
- P95 latency stabilizes at timeout value, then drops to fast-fail once breaker opens
- MTTR: ~30s (breaker open duration) + probe time

## Expected Metrics Impact
| Metric | Without CB | With CB |
|--------|-----------|---------|
| request_duration_seconds p95 | 3-20s | <8s then <100ms |
| downstream_errors_total{to=payments} | climbing | plateaus then fast-fails |
| breaker_state{downstream=payments} | N/A | transitions 0→1→2→0 |
| http_requests_total{status=5xx} | ~100% | <10% after stabilization |
