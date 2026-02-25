# Chaos Scenario: Inventory DB Lock Contention

## What
Simulate PostgreSQL row-level lock contention in the Inventory service by introducing
artificial delay inside transactions that hold locks.

## Why
High-concurrency workloads cause lock queuing in PostgreSQL.  When many orders try to
reserve the same products simultaneously, lock wait time grows.  This tests whether
per-hop timeouts and bulkhead isolation prevent inventory slowness from starving the
order-processing worker pool.

## How to Enable
```bash
./chaos/scripts/inventory_lock.sh enable
curl -X POST "http://localhost:8003/chaos/config?lock_contention_ms=2000"
```

## How to Disable
```bash
./chaos/scripts/inventory_lock.sh disable
```

## Expected Behavior

### Without bulkhead:
- All async workers eventually block on inventory
- Payment calls also starve (shared pool)
- Full service saturation

### With bulkhead (Config E):
- Inventory bulkhead limits concurrent calls to 20
- Excess requests rejected immediately (503) — not queued indefinitely
- Payment calls unaffected
- P95 latency for inventory-heavy orders increases but orders service remains responsive
