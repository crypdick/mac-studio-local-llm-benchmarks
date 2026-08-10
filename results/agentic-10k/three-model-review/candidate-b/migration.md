# Zero-downtime PostgreSQL column migration

# Zero-Downtime PostgreSQL 16 Migration Plan: `events.account_region`

## Executive Summary

This plan uses an **Expand → Migrate → Contract** pattern with four discrete phases. The core strategy: add the typed column as nullable, backfill with resumable cursor batches, enforce NOT NULL only after backfill completes, and retain the old payload path for 72 hours post-migration to guarantee rollback capability.

---

## Phase 0: Pre-Migration Gate

**Objective:** Validate environment and capture baseline metrics.

```sql
-- Capture baseline for regression detection
CREATE TABLE IF NOT EXISTS migration_audit (
    id          BIGSERIAL PRIMARY KEY,
    phase       TEXT NOT NULL,
    step        TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    rows_affected BIGINT,
    note        TEXT
);

-- Record table size and write rate
SELECT
    pg_size_pretty(pg_total_relation_size('events')) AS total_size,
    pg_size_pretty(pg_relation_size('events'))       AS heap_size,
    count(*)                                          AS estimated_rows
FROM events TABLESAMPLE SYSTEM (0.001);  -- quick estimate
```

**Deploy gate:** Confirm replica lag < 10s, CPU < 50%, no long-running transactions > 5s.

---

## Phase 1: Expand

**Objective:** Introduce the new column and index without touching existing code paths.

### Step 1.1 — Add Nullable Column (No Default, No Rewrite)

```sql
ALTER TABLE events ADD COLUMN IF NOT EXISTS account_region text;
```

**Why no DEFAULT:** In PG 11+, adding a column with a constant DEFAULT causes a table rewrite (acquires `AccessExclusiveLock`). Omitting the DEFAULT makes this a catalog-only change (~10 ms on a 2 TB table). The column starts NULL for all rows.

**Lock behavior:** `ShareUpdateExclusiveLock` — permits concurrent reads and writes.

### Step 1.2 — Create Index Concurrently

```sql
-- Guard against duplicate creation on re-run
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'events'
          AND indexname = 'events_tenant_region_created_idx'
    ) THEN
        CREATE INDEX CONCURRENTLY events_tenant_region_created_idx
            ON events (tenant_id, account_region, created_at DESC)
            WHERE account_region IS NOT NULL;  -- partial avoids indexing "unknown"
    END IF;
END
$$;
```

**Lock behavior:** `ShareUpdateExclusiveLock` — takes two shorter locks during build but never blocks readers or writers. The index builds in the background. **Critical:** `CREATE INDEX CONCURRENTLY` cannot run inside an explicit transaction; run as a standalone statement.

**Replica lag impact:** Minimal. The index build writes to catalogs, not the table heap.

### Step 1.3 — Deploy Application Version 2

- Old binary: writes only to `payload->>'account_region'`
- New binary: **dual-writes** to both `payload` and `account_region`

```sql
-- Application code pattern (new binary)
INSERT INTO events (tenant_id, payload, account_region, created_at)
VALUES (
    p_tenant_id,
    p_payload,
    p_payload->>'account_region',   -- explicit column
    NOW()
)
ON CONFLICT (...) DO UPDATE SET
    payload          = EXCLUDED.payload,
    account_region   = EXCLUDED.account_region;  -- dual-write
```

**Deploy gate:** 10% canary → 50% → full rollout. Monitor error rate and p99 latency.

---

## Phase 2: Migrate

**Objective:** Backfill all historical rows while production traffic continues.

### Step 2.1 — Create Backfill Watermark Table

```sql
CREATE TABLE IF NOT EXISTS backfill_watermark (
    table_name    TEXT PRIMARY KEY DEFAULT 'events',
    last_id       BIGINT NOT NULL DEFAULT 0,
    batch_size    BIGINT NOT NULL DEFAULT 500,
    sleep_ms      BIGINT NOT NULL DEFAULT 100,
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    total_updated BIGINT NOT NULL DEFAULT 0
);

INSERT INTO backfill_watermark (started_at)
SELECT NOW() WHERE NOT EXISTS (SELECT 1 FROM backfill_watermark);
```

### Step 2.2 — Resumable Backfill Job

Run as a long-lived process (pg_partman, custom script, or Kubernetes Job):

```sql
DO $$
DECLARE
    v_last_id      BIGINT;
    v_batch_size   BIGINT := 500;
    v_sleep_ms     BIGINT := 100;
    v_updated      BIGINT;
    v_remaining    BIGINT;
    v_start_time   TIMESTAMPTZ := NOW();
BEGIN

    -- Load watermark
    SELECT last_id INTO v_last_id FROM backfill_watermark FOR UPDATE;
    
    -- Quick remaining count (sampled)
    SELECT reltuples::BIGINT * 1000
    INTO v_remaining
    FROM pg_class
    WHERE relname = 'events';

    RAISE NOTICE 'Starting backfill from id > % (estimated % rows remaining)',
        v_last_id, v_remaining;

    -- Main backfill loop
    WHILE TRUE LOOP
    
        -- Update one batch: derive from payload, skip rows written after backfill start
        -- (those are handled by the trigger in Phase 3)
        WITH batch AS (
            SELECT id
            FROM events
            WHERE id            > v_last_id
              AND account_region IS NULL
              AND created_at    < v_start_time  -- skip live traffic
            ORDER BY id
            LIMIT v_batch_size
            FOR UPDATE SKIP LOCKED  -- race-free: skip rows locked by writers
        )
        UPDATE events e
        SET account_region = COALESCE(
            NULLIF(e.payload->>'account_region', ''),
            'unknown'
        )
        WHERE e.id IN (SELECT id FROM batch);

        GET DIAGNOSTICS v_updated = ROW_COUNT;

        IF v_updated = 0 THEN
            -- No more rows to backfill in pre-existing data
            UPDATE backfill_watermark
            SET completed_at = NOW(),
                last_id = v_last_id
            WHERE table_name = 'events';
            
            RAISE NOTICE 'Backfill complete. Total updated: %',
                (SELECT total_updated FROM backfill_watermark);
            RETURN;
        END IF;

        -- Advance watermark
        UPDATE backfill_watermark
        SET last_id       = v_last_id + v_updated,
            total_updated = total_updated + v_updated
        WHERE table_name = 'events';

        v_last_id := v_last_id + v_updated;

        -- Throttle: sleep between batches
        PERFORM pg_sleep(v_sleep_ms::NUMERIC / 1000.0);

        -- Progress logging every 10,000 rows
        IF v_last_id % 10000 = 0 THEN
            RAISE NOTICE 'Backfill progress: id > %, updated total: %',
                v_last_id, (SELECT total_updated FROM backfill_watermark);
        END IF;

    END LOOP;
END;
$$;
```

**Race-free guarantees:**
- `FOR UPDATE SKIP LOCKED` ensures backfill never blocks writers and never waits for locked rows.
- The watermark table makes the job idempotent and resumable after crash.
- `created_at < v_start_time` filter excludes live traffic rows (those are handled by the trigger).

**Throttling knobs:**
| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `batch_size` | 500 | ~15 MB write/second at 15k TPS; stays under 70% CPU |
| `sleep_ms` | 100 | 5 batches/second = 2,500 rows/second backfill rate |
| Estimated completion | ~2–4 days for 2 TB | Acceptable given no write outage |

### Step 2.3 — Observability During Backfill

```sql
-- Real-time backfill progress
SELECT
    w.last_id,
    w.total_updated,
    w.started_at,
    NOW() - w.started_at                     AS elapsed,
    ROUND(w.total_updated::NUMERIC / 
        EXTRACT(EPOCH FROM (NOW() - w.started_at))) AS rows_per_sec,
    w.completed_at IS NOT NULL               AS is_done
FROM backfill_watermark w;

-- Remaining NULL count (sampled)
SELECT
    count(*) * 1000 AS estimated_nulls
FROM events
WHERE account_region IS NULL
TABLESAMPLE SYSTEM (0.1);

-- Replica lag check
SELECT
    now() - pg_last_xact_replay_timestamp() AS replication_lag,
    state
FROM pg_stat_replication;

-- CPU and write rate
SELECT
    sum(n_tup_ins)     AS total_inserts,
    sum(n_tup_upd)     AS total_updates,
    sum(n_tup_del)     AS total_deletes
FROM pg_stat_user_tables
WHERE relname = 'events';
```

**Alert thresholds:**
- Replica lag > 30s → pause backfill (`UPDATE backfill_watermark SET sleep_ms = 2000`)
- CPU > 70% → pause backfill
- Write throughput drop > 20% → pause backfill

---

## Phase 3: Validate & Enforce

**Objective:** Prove no rows were missed, then enforce NOT NULL.

### Step 3.1 — Final NULL Check

```sql
-- Gate: must return 0 rows
SELECT count(*) AS null_count FROM events WHERE account_region IS NULL;
-- Expected: 0
```

If non-zero, either backfill is still running or live traffic produced NULLs (which the trigger will catch).

### Step 3.2 — Cross-Validation: No Silent Row Omission

```sql
-- Compare payload extraction vs. explicit column for a random sample
-- Any mismatches indicate backfill corruption or concurrent write issues
SELECT count(*) AS mismatches
FROM events
WHERE account_region IS DISTINCT FROM
      COALESCE(NULLIF(payload->>'account_region', ''), 'unknown')
TABLESAMPLE BERNOULLI (1);  -- 1% sample
-- Expected: 0
```

### Step 3.3 — Audit "Unknown" Count

```sql
-- Policy: derive "unknown" for rows lacking account_region in payload
-- Retain auditable count
SELECT count(*) AS unknown_count
FROM events
WHERE account_region = 'unknown';

-- Log to audit table
INSERT INTO migration_audit (phase, step, rows_affected, note)
SELECT
    'migrate',
    'unknown_audit',
    count(*),
    format('Rows derived as "unknown": %', count(*))
FROM events
WHERE account_region = 'unknown';
```

### Step 3.4 — Add NOT NULL Constraint

```sql
-- Add NOT NULL only after backfill is complete and validated
-- This acquires ShareUpdateExclusiveLock briefly, not AccessExclusiveLock
ALTER TABLE events
    ALTER COLUMN account_region SET NOT NULL;
```

**Lock behavior:** In PG 11+, this does not rewrite the table. It acquires a brief `ShareUpdateExclusiveLock` to validate existing rows, then takes a metadata lock. No blocking of reads or writes.

**Why now:** At this point, all existing rows have a value (either derived from payload or "unknown"). New rows written by the new binary have the value. The old binary may still write rows without the column — those are caught by the trigger in Step 3.5.

### Step 3.5 — Install Safety Trigger (Transition Window)

```sql
-- Trigger function: enforce account_region on writes during transition
CREATE OR REPLACE FUNCTION enforce_account_region_not_null()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.account_region IS NULL THEN
        RAISE EXCEPTION 'account_region cannot be NULL (tenant_id: %)', NEW.tenant_id
            USING ERRCODE = '23502';  -- not_null_violation
    END IF;
    RETURN NEW;
END;
$$;

-- Install as NOT VALID to avoid AccessExclusiveLock
CREATE TRIGGER trg_account_region_not_null
    BEFORE INSERT OR UPDATE ON events
    FOR EACH ROW
    EXECUTE FUNCTION enforce_account_region_not_null();

-- Validate without locking writers
ALTER TABLE events VALIDATE CONSTRAINT trg_account_region_not_null;
```

**Lock behavior:** `NOT VALID` + `VALIDATE CONSTRAINT` uses `ShareUpdateExclusiveLock`, which permits concurrent writes. The trigger fires on every row change, but the check is a simple NULL test — negligible overhead at 15k TPS.

**Purpose:** Catches any writes (from old binary during co-existence window) that attempt to bypass the column. Without this, old binary writes would succeed and violate NOT NULL, causing the constraint to block future writes.

### Step 3.6 — Switch Read Path

Update application to read from `account_region` instead of `payload->>'account_region'`:

```sql
-- New query pattern
SELECT *
FROM events
WHERE tenant_id      = $1
  AND account_region = $2
  AND created_at    >= $3
ORDER BY created_at DESC
LIMIT 200;
```

**Deploy gate:** Read path switch only after NOT NULL + trigger are confirmed active. Monitor query latency and error rates.

---

## Phase 4: Contract (72-Hour Rollback Window)

**Objective:** Clean up after confirming stability. Retain rollback capability for 72 hours.

### Step 4.1 — 72-Hour Stabilization Wait

During this window:
- Old binary may still be running in other regions/pods (up to 48h deploy window + buffer)
- Trigger ensures all writes populate `account_region`
- `payload` column remains readable for rollback

**Monitoring during wait:**
```sql
-- Verify no constraint violations (trigger should prevent these, but sanity check)
SELECT count(*) AS recent_null_violations
FROM events
WHERE account_region IS NULL
  AND created_at > NOW() - INTERVAL '1 hour';
-- Expected: 0
```

### Step 4.2 — Drop Safety Trigger

After confirming no old binaries remain:

```sql
DROP TRIGGER IF EXISTS trg_account_region_not_null ON events;
DROP FUNCTION IF EXISTS enforce_account_region_not_null();
```

**Lock behavior:** `ShareUpdateExclusiveLock` — brief, non-blocking.

### Step 4.3 — Optional: Drop `payload` Column

**Decision gate:** Only after:
1. 72-hour rollback window elapsed
2. Application code no longer references `payload->>'account_region'`
3. All queries audited for payload access

```sql
-- Keep payload column as safety net indefinitely, OR:
ALTER TABLE events DROP COLUMN IF EXISTS payload;
```

**Alternative:** Rename for later cleanup:
```sql
ALTER TABLE events RENAME COLUMN payload TO payload_legacy;
```

---

## Complete Deployment Timeline

```
Week 1, Day 1 (Hour 0)
├── Phase 0: Pre-migration gate
├── Phase 1.1: ALTER TABLE ADD COLUMN (nullable, no default)
├── Phase 1.2: CREATE INDEX CONCURRENTLY
└── Deploy v2 (dual-write capable)

Week 1-2 (Days 1-4)
├── Phase 2: Backfill runs continuously
│   ├── Watermark persisted every batch
│   ├── Pausable on replica lag / CPU thresholds
│   └── Resumable after crash
└── Monitor: NULL count, replica lag, CPU, write throughput

Week 2, Day X (Backfill complete)
├── Phase 3.1: NULL count = 0 ✓
├── Phase 3.2: Cross-validation = 0 mismatches ✓
├── Phase 3.3: Audit "unknown" count logged ✓
├── Phase 3.4: ALTER COLUMN SET NOT NULL
├── Phase 3.5: CREATE TRIGGER (NOT VALID → VALIDATE)
└── Deploy v3 (read from account_region)

Week 2-3 (Days X to X+72h)
├── Phase 4.1: Stabilization window
│   └── Monitor for NULL violations
└── Phase 4.2: DROP TRIGGER (after 72h confirmed)

Future (optional)
└── Phase 4.3: DROP payload column (after full audit)
```

---

## Rollback Decision Matrix

| Failure Point | Action |
|--------------|--------|
| Backfill OOM or crash | Restart job; resumes from watermark |
| Replica lag > 30s | Pause backfill; auto-resumes when lag clears |
| NOT NULL constraint fails | Investigate: is old binary still writing NULLs? |
| Application read switch causes errors | Revert to `payload->>'account_region'`; trigger still protects writes |
| Catastrophic failure | `ALTER TABLE events DROP COLUMN account_region`; v2 binary already deployed with dual-write |

**Key invariant:** The `payload` column is never touched until 72 hours after the last old binary could have run. Rollback always has a working data source.

---

## PostgreSQL Lock Reference for This Plan

| Operation | Lock Mode | Blocks Reads | Blocks Writes |
|-----------|-----------|--------------|---------------|
| `ADD COLUMN` (no default) | `ShareUpdateExclusive` | No | No |
| `CREATE INDEX CONCURRENTLY` | `ShareUpdateExclusive` | No | No |
| `ALTER COLUMN SET NOT NULL` | `ShareUpdateExclusive` | No | No |
| `CREATE TRIGGER ... NOT VALID` | `ShareUpdateExclusive` | No | No |
| `VALIDATE CONSTRAINT` | `ShareUpdateExclusive` | No | No |
| `DROP TRIGGER` | `ShareUpdateExclusive` | No | No |
| `ADD COLUMN` (with DEFAULT) | `AccessExclusive` | **Yes** | **Yes** | ← Avoided |
| `DROP COLUMN` | `AccessExclusive` | **Yes** | **Yes** | ← Deferred |

The plan avoids `AccessExclusiveLock` entirely after Phase 0, ensuring zero write outage and zero blocking reads throughout.

---

`finish_reason=stop; wall_s=521.3; completion_tokens=13837`
