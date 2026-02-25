# Chaos Scenario: Payment 5xx Burst

## What
Inject a 20% HTTP 500 error rate into the Payments service for 2 minutes,
simulating a partial outage of the external payment processor.

## Why
Tests the interaction between client-side retries and circuit breakers.
Without a circuit breaker, naive retries amplify load on an already-degraded service
(retry storm). With a circuit breaker, the breaker opens quickly and prevents amplification.

## How to Enable
```bash
./chaos/scripts/payment_errors.sh enable
curl -X POST "http://localhost:8002/chaos/config?error_rate=0.20"
```

## How to Disable
```bash
./chaos/scripts/payment_errors.sh disable
```

## Expected Behavior

### Config B (retries only):
- Each 500 triggers up to 3 retries
- Effective RPS to payments = 3x incoming RPS (retry amplification)
- Payments service becomes more degraded under amplified load
- Cascading failure risk

### Config D (retries + circuit breaker):
- First 5 failures open the circuit breaker
- Subsequent calls fast-fail without hitting payments
- Retry amplification factor drops to ~1x
- Orders processed with `payment_failed` status

## Expected Metrics Impact
| Metric | Config B | Config D |
|--------|---------|---------|
| downstream_requests_total{to=payments} | 3x amplified | 1x until breaker opens |
| retry_attempts_total | high | moderate then zero |
| breaker_state{downstream=payments} | N/A | 1 (open) |
| orders success rate | ~24% (0.8^3) | ~80% (0.8 × fast-fail path) |
