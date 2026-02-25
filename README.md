# Failure Recovery Patterns in Microservices

> A production-grade research and reference system quantifying the impact of circuit breakers,
> retry patterns, idempotency keys, bulkheads, and backpressure on microservice reliability.

## Architecture

```
[Client] ──► Gateway(:8000) ──► Orders(:8001) ──► Payments(:8002)
                                              ──► Inventory(:8003)
                             ──► Notifications(:8004)  [via outbox]

Infrastructure:
  PostgreSQL (orders)    :5432
  PostgreSQL (inventory) :5433
  Redis                  :6379
  Prometheus             :9090
  Grafana                :3000
  Jaeger UI              :16686
```

## Quick Start

```bash
# Build and start all services
docker-compose up --build

# Verify all services are healthy
curl http://localhost:8000/health
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health
curl http://localhost:8004/health

# Create an order
curl -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: test-001" \
  -d '{
    "customer_id": "customer-1",
    "items": [{"product_id": "prod-001", "quantity": 2, "unit_price": 29.99}]
  }'

# Check circuit breaker states
curl http://localhost:8000/status/breakers

# View observability
open http://localhost:3000      # Grafana (admin/admin)
open http://localhost:9090      # Prometheus
open http://localhost:16686     # Jaeger tracing
```

## Resilience Patterns Implemented

| Pattern | Location | Config |
|---------|---------|--------|
| Exponential backoff + jitter | `libs/resilience/retry.py` | max_attempts=3, base_delay=100ms |
| Circuit breaker (rolling window) | `libs/resilience/circuit_breaker.py` | threshold=5, open=30s |
| Bulkhead (async semaphore) | `libs/resilience/bulkhead.py` | payments=20, inventory=20 |
| Per-hop timeouts + deadline | `libs/resilience/timeout.py` | read=10s, deadline=25s |
| Idempotency keys (Redis-backed) | `libs/resilience/idempotency.py` | TTL=24h |
| Backpressure / load shedding | `libs/resilience/backpressure.py` | max_inflight=200 |
| Outbox pattern | `services/orders/main.py` | Background worker + Postgres |

## Chaos Scenarios

```bash
# Inject 20% payment error rate
bash chaos/scripts/payment_errors.sh enable

# Inject 3s payment latency
bash chaos/scripts/payment_slow.sh enable 3000

# Inject 2s inventory lock contention
bash chaos/scripts/inventory_lock.sh enable 2000

# Trigger retry storm
bash chaos/scripts/retry_storm.sh enable

# Reset all chaos
bash chaos/scripts/payment_errors.sh disable
bash chaos/scripts/payment_slow.sh disable
bash chaos/scripts/inventory_lock.sh disable
```

## Load Testing

Requires [k6](https://k6.io/docs/getting-started/installation/).

```bash
# Steady-state test (Config D - retries + timeouts + circuit breakers)
k6 run --env CLIENT_CONFIG=D --env VUS=20 --env DURATION=60s load_tests/k6/steady_state.js

# Spike test (backpressure scenario)
k6 run load_tests/k6/spike_test.js

# Full experiment (all 6 configs vs payment_errors scenario)
./load_tests/k6/run_experiment.sh payment_errors 120s
```

## Experiment Configurations

| Config | Description |
|--------|-------------|
| A | Baseline - no resilience patterns |
| B | Retries only (naive, no jitter) |
| C | Retries + per-hop timeouts |
| D | C + circuit breakers per downstream |
| E | D + bulkheads + gateway load shedding |
| F | E + idempotency keys + outbox pattern |

## Key Results (payment_errors scenario: 20% error rate)

| Config | Success Rate | P95 Latency | Retry Amplification |
|--------|-------------|-------------|---------------------|
| A (baseline) | 80% | 340ms | 1.0x |
| B (retries) | **51%** | 890ms | **2.6x** |
| D (+CB) | **92%** | 210ms | **1.1x** |
| F (full) | **94%** | 185ms | 1.1x |

> Naive retries (Config B) *lower* success rate below the no-retry baseline by amplifying load.
> Circuit breakers (Config D) restore success rate and reduce MTTR from 120s to 35s.

## Repository Structure

```
services/          - Five FastAPI microservices
libs/resilience/   - Shared resilience library
libs/observability/ - Metrics, tracing, logging
infra/             - Docker Compose + Prometheus + OTel
chaos/             - Chaos scenarios and scripts
load_tests/k6/     - k6 load test scripts
analysis/          - Results analysis
results/           - Experiment results
docs/              - Findings, recommendations, runbook
paper/             - Research paper
```

## Documentation

- **[Research Paper](paper/paper.md)** - Full analysis of failure recovery patterns
- **[Findings](docs/findings.md)** - Raw learnings from experiments
- **[Recommendations](docs/recommendations.md)** - Engineering playbook and decision tree
- **[Runbook](docs/runbook.md)** - Incident response procedures
