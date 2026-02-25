# Chaos Scenario: Retry Storm

## What
Configure aggressive client-side retries (3 attempts, 0ms backoff, all status codes)
combined with a 20% payment error rate.  This maximises retry amplification.

## Why
Retry storms occur when many clients retry simultaneously after a partial outage,
amplifying load by 2-10x and turning a partial degradation into a complete outage.
This scenario quantifies the amplification factor and demonstrates how circuit breakers
and retry budgets limit it.

## How to Enable
```bash
./chaos/scripts/retry_storm.sh enable
```
This sets:
- Payments error_rate=0.20
- k6 load test with aggressive_retries=true (no backoff, all status codes)

## Expected Metrics
| Config | Amplification Factor | Success Rate |
|--------|---------------------|-------------|
| B (naive retries) | ~3x | ~51% |
| C (retries + timeout) | ~3x | ~60% |
| D (+ circuit breaker) | ~1x after open | ~80% |

## Recovery Indicator
MTTR = time from fault injection until success_rate > 95% again.
