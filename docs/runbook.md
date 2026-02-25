# Runbook: Failure Recovery Patterns – Incident Response

## Service Map

```
[Client] → Gateway(:8000) → Orders(:8001) → Payments(:8002)
                                          → Inventory(:8003)
                          → Notifications(:8004) [via outbox]
```

## Health Checks

```bash
# All services
for port in 8000 8001 8002 8003 8004; do
  echo -n "Port $port: "
  curl -sf http://localhost:$port/health | jq -r .status
done

# Circuit breaker states
curl -s http://localhost:8000/status/breakers | jq .
```

---

## Incident: Payment Service Degradation

### Symptoms
- `order_success_rate` drops below 95%
- `downstream_errors_total{to_service=payments}` climbing
- `breaker_state{downstream=payments}` → 1 (open)
- Orders completing with status `payment_failed`

### Immediate Response

1. **Verify circuit breaker state**
   ```bash
   curl http://localhost:8000/status/breakers
   ```

2. **Check Payments service health**
   ```bash
   curl http://localhost:8002/health
   ```

3. **Check current chaos config** (may have been left enabled)
   ```bash
   curl http://localhost:8002/chaos/config  # GET if implemented
   # Or check Redis
   redis-cli get chaos:payments:error_rate
   redis-cli get chaos:payments:latency_ms
   ```

4. **Clear any lingering chaos injection**
   ```bash
   bash chaos/scripts/payment_errors.sh disable
   bash chaos/scripts/payment_slow.sh disable
   ```

5. **Monitor recovery** — breaker will probe after `open_duration` (30s)
   ```bash
   watch -n 2 'curl -s http://localhost:9090/api/v1/query?query=breaker_state | jq .'
   ```

### If Payment Service is Genuinely Down

- Orders continue to accept requests; status = `payment_failed`
- Notify customers via `/events` (outbox events still accumulate)
- When payments recover, circuit breaker will close automatically
- No manual intervention needed for order state (idempotency keys prevent duplicates on retry)

---

## Incident: Inventory Lock Contention

### Symptoms
- `bulkhead_rejections_total{downstream=inventory}` climbing
- P95 latency for orders with inventory > threshold
- `chaos:inventory:lock_contention_ms` set in Redis

### Response

1. **Check inventory chaos config**
   ```bash
   redis-cli get chaos:inventory:lock_contention_ms
   redis-cli get chaos:inventory:error_rate
   ```

2. **Clear if test artefact**
   ```bash
   bash chaos/scripts/inventory_lock.sh disable
   ```

3. **Scale inventory service** if production contention
   ```bash
   docker-compose up -d --scale inventory=3
   ```

---

## Incident: Gateway Overload (High 429 Rate)

### Symptoms
- `load_shed_total` counter climbing rapidly
- Clients receiving 429 with `Retry-After: 5`
- P99 latency for admitted requests still acceptable

### Response

1. **Check current inflight requests** (Prometheus)
   ```promql
   http_requests_total{service="gateway"} - http_requests_total{service="gateway"} offset 1m
   ```

2. **Identify traffic source**
   - Check `X-Correlation-ID` patterns
   - Check if a load test is running: `ps aux | grep k6`

3. **If legitimate traffic surge**:
   - Scale gateway: `docker-compose up -d --scale gateway=3`
   - Increase `max_inflight` in BackpressureMiddleware (requires redeploy)

4. **If runaway client / test**:
   - Stop the load test
   - Apply rate limiting at the network layer

---

## Incident: Outbox Events Not Being Published

### Symptoms
- `outbox_pending` gauge > 100 and not decreasing
- Notifications service not receiving events

### Response

1. **Check Notifications service**
   ```bash
   curl http://localhost:8004/health
   ```

2. **Check outbox queue depth in DB**
   ```sql
   SELECT COUNT(*), MIN(created_at) FROM outbox_events WHERE NOT published;
   ```

3. **Check outbox worker logs**
   ```bash
   docker-compose logs orders | grep outbox
   ```

4. **If Notifications is down**: outbox worker will retry automatically when it recovers.
   No action needed — events are durable.

5. **If outbox worker is stuck** (e.g., DB transaction deadlock):
   ```bash
   docker-compose restart orders
   ```

---

## Metrics Reference

| Metric | Alert Threshold | Meaning |
|--------|----------------|---------|
| `order_success_rate` | < 95% | Overall order health |
| `breaker_state{downstream=payments}` | = 1 | Payment circuit open |
| `http_req_failed_rate` | > 5% | Overall error rate |
| `bulkhead_rejections_total` | > 1% of requests | Resource saturation |
| `outbox_pending` | > 500 | Event delivery backlog |
| `idempotency_conflicts_total` | > 0.1% | Concurrent duplicate storm |
| `retry_attempts_total` | > 2x request rate | Retry amplification |

---

## Common Commands

```bash
# View all service logs
docker-compose logs -f --tail=100

# Prometheus query via CLI
curl -s 'http://localhost:9090/api/v1/query?query=retry_attempts_total' | jq .

# Reset all chaos
for url in http://localhost:8002 http://localhost:8003; do
  curl -s -X DELETE "$url/chaos/config"
done

# Full stack restart (preserves data volumes)
docker-compose restart gateway orders payments inventory notifications
```
