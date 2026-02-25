# Recommendations

Engineering playbook for implementing failure recovery patterns in microservices.

## Decision Tree: Which Patterns to Apply

```
Does your service call any external dependency?
├── YES → Apply ALL of: timeouts, retries with backoff, circuit breakers
│         (this is the minimum viable resilience set)
└── NO  → Still apply: timeouts on DB queries, bulkhead for DB pool

Is the operation idempotent by nature? (GET, DELETE)
├── YES → No special action needed for idempotency
└── NO  → Add idempotency key middleware (POST /orders, POST /payments/charge)

Does failure of operation X affect unrelated operation Y?
├── YES → Add a bulkhead (separate semaphore per downstream)
└── NO  → Optional bulkhead (still good practice)

Does your service emit async events that must be delivered?
├── YES → Use outbox pattern (not direct HTTP/queue publish in same transaction)
└── NO  → Direct publish acceptable

Does your service face variable/unpredictable load?
├── YES → Add backpressure/load shedding at gateway
└── NO  → Lower priority
```

## Retry Configuration Recommendations

| Scenario | max_attempts | base_delay | max_delay | jitter |
|----------|-------------|------------|-----------|--------|
| Payment processor | 3 | 200ms | 10s | full |
| Internal service | 2 | 100ms | 5s | full |
| DB query | 2 | 50ms | 2s | full |
| Idempotent read | 3 | 100ms | 3s | equal |

**Rules**:
1. Only retry on 429, 500, 502, 503, 504 — never on 400, 401, 409
2. Set a retry budget (e.g., max 5 retries per incoming request) to prevent amplification
3. Always use jitter to prevent synchronized retry storms
4. Respect `Retry-After` headers from downstream services

## Circuit Breaker Configuration Recommendations

| Downstream Type | failure_threshold | open_duration | success_threshold |
|----------------|-------------------|---------------|-------------------|
| Critical payment processor | 5 failures / 60s | 30s | 2 |
| Internal service | 10 failures / 60s | 15s | 1 |
| Non-critical (notifications) | 20 failures / 60s | 60s | 3 |

**Rules**:
1. One circuit breaker per downstream service (not per endpoint)
2. Share breaker state across replicas via Redis for consistent behaviour
3. Always have a fallback: return cached data, serve degraded response, or fail fast
4. Instrument breaker state changes as metrics (breaker_state gauge)

## Bulkhead Configuration Recommendations

| Downstream Type | max_concurrent | max_wait |
|----------------|----------------|----------|
| Payment processor (external) | 20 | 1s |
| Internal service | 50 | 500ms |
| Database | 10 per service | 200ms |

**Rules**:
1. Size bulkheads to your steady-state concurrent load + 50% headroom
2. Reject immediately when at capacity — do not queue indefinitely
3. Monitor bulkhead_rejections_total; alert if > 1% of requests

## Idempotency Key Implementation Checklist

- [ ] All POST/PUT endpoints that create/modify resources require `Idempotency-Key` header
- [ ] Key is stored with the response (TTL: 24h minimum)
- [ ] Lock mechanism prevents concurrent duplicate processing (use Redis SET NX)
- [ ] Only cache successful (2xx) responses — let failures retry
- [ ] Document key format and TTL in API spec
- [ ] Test: same key + same body → same response
- [ ] Test: same key + different body → 409 Conflict

## Timeout Ladder

Define timeouts at each layer, decreasing from user to database:

```
User request (browser/client)     : 30s
└── Gateway timeout                : 25s (X-Request-Deadline propagated)
    └── Orders per-hop timeout     : 8s per downstream call
        └── Payments timeout       : 8s (respects deadline)
        └── Inventory timeout      : 8s (respects deadline)
            └── DB query timeout   : 5s
```

**Key principle**: Each hop timeout must be less than its parent's remaining deadline.

## Outbox Pattern Implementation Checklist

- [ ] Outbox table in same DB as business data
- [ ] Insert outbox event in the same transaction as business write
- [ ] Background worker polls outbox with `FOR UPDATE SKIP LOCKED` (prevents double processing)
- [ ] Consumer implements dedup (event_id + idempotent processing)
- [ ] Monitor outbox_pending gauge; alert if > 100 unpublished events
- [ ] Periodic cleanup of published events (e.g., delete events older than 7 days)

## Anti-Patterns to Avoid

1. **Retry all status codes** — never retry 4xx (client errors are not transient)
2. **Retry without backoff** — synchronised retries cause retry storms
3. **Shared thread pool for all downstreams** — one slow downstream starves others
4. **Infinite retries** — set max_attempts and retry budget
5. **Circuit breaker without fallback** — design what to return when breaker is open
6. **Idempotency without concurrency control** — concurrent duplicates cause race conditions
7. **Fire-and-forget for critical events** — use outbox pattern for guaranteed delivery
