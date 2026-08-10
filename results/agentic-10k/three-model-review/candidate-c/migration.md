# Zero-downtime PostgreSQL column migration

# Zero-Downtime PostgreSQL 16 Migration Plan: `events` Table

## Executive Summary

This plan adds a typed `account_region text NOT NULL` column and supporting index to a 2 TB `events` table receiving 15,000 writes/second, using a three-phase expand/migrate/contract pattern. The migration is designed for zero downtime, resumable backfill, and safe rollback.

---

## Phase 1: Expand (Add Column, Dual-Write)

### Step 1.1: Add Nullable Column

```sql
-- Add column as nullable first (no table rewrite, instant in PG16)
ALTER TABLE events ADD COLUMN account_region text;
```

**PostgreSQL Lock Behavior:** `ALTER TABLE ... ADD COLUMN` acquires `ACCESS EXCLUSIVE` lock but is instant for nullable columns in PG16 (no rewrite). The lock is held briefly.

### Step 1.2: Deploy New Application Version (Dual-Write)

Deploy new app version that:
- **Writes** to both `payload` and `account_region`
- **Reads** from `account_region` with fallback to `payload->>'account_region'`

```sql
-- New writer: INSERT with dual-write
INSERT INTO events (tenant_id, account_region, payload, created_at)
VALUES ($1, $2, $3, $4);

-- New reader: COALESCE for backward compatibility
SELECT * FROM events
WHERE tenant_id = $1 
  AND account_region = $2 
  AND created_at >= $3
ORDER BY created_at DESC
LIMIT 200;
```

**Critical:** The new app version must use `COALESCE(account_region, payload->>'account_region')` in queries to handle rows written by old binaries.

### Step 1.3: Backfill Historical Rows

```sql
-- Backfill script (resumable, batched)
-- Uses a tracking table to resume from last processed ID
CREATE TABLE IF NOT EXISTS backfill_progress (
    id bigserial PRIMARY KEY,
    last_processed_id bigint NOT NULL,
    batch_size int NOT NULL DEFAULT 10000,
    started_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

-- Backfill function (resumable)
CREATE OR REPLACE FUNCTION backfill_account_region()
RETURNS void AS $$
DECLARE
    v_last_id bigint;
    v_batch_size int := 10000;
    v_rows_updated int;
BEGIN
    -- Get last processed ID
    SELECT last_processed_id INTO v_last_id 
    FROM backfill_progress 
    ORDER BY id DESC LIMIT 1;
    
    IF v_last_id IS NULL THEN
        v_last_id := 0;
    END IF;
    
    -- Process batch
    UPDATE events 
    SET account_region = 'unknown'
    WHERE id > v_last_id 
      AND id <= v_last_id + v_batch_size
      AND account_region IS NULL;
    
    GET DIAGNOSTICS v_rows_updated = ROW_COUNT;
    
    -- Update progress
    INSERT INTO backfill_progress (last_processed_id, batch_size)
    VALUES (v_last_id + v_batch_size, v_batch_size);
    
    -- Log progress
    RAISE NOTICE 'Backfilled % rows, last ID: %', v_rows_updated, v_last_id + v_batch_size;
END;
$$ LANGUAGE plpgsql;
```

**Backfill Strategy:**
- **Batch size:** 10,000 rows per batch
- **Throttling:** 100ms sleep between batches
- **Concurrency:** Single-threaded to avoid write amplification
- **Resumable:** Tracks last processed ID in `backfill_progress` table
- **Safe under concurrent writes:** Uses `WHERE account_region IS NULL` to avoid overwriting new writes

**Estimated Duration:** 2 TB / 10,000 rows per batch = ~200,000 batches. At 100ms per batch = ~5.5 hours. With 15,000 writes/second, we need to be careful about write amplification.

**Throttling Implementation:**
```sql
-- Run backfill with throttling
DO $$
DECLARE
    v_batch_size int := 10000;
    v_last_id bigint := 0;
    v_rows_updated int;
    v_total_updated bigint := 0;
BEGIN
    LOOP
        -- Process batch
        UPDATE events 
        SET account_region = 'unknown'
        WHERE id > v_last_id 
          AND id <= v_last_id + v_batch_size
          AND account_region IS NULL;
        
        GET DIAGNOSTICS v_rows_updated = ROW_COUNT;
        v_total_updated := v_total_updated + v_rows_updated;
        
        -- Update progress
        INSERT INTO backfill_progress (last_processed_id, batch_size)
        VALUES (v_last_id + v_batch_size, v_batch_size);
        
        -- Throttle: sleep 100ms
        PERFORM pg_sleep(0.1);
        
        -- Update last processed ID
        v_last_id := v_last_id + v_batch_size;
        
        -- Exit if no more rows
        EXIT WHEN v_rows_updated = 0;
        
        -- Log progress every 100 batches
        IF v_total_updated % 1000000 = 0 THEN
            RAISE NOTICE 'Total updated: %, last ID: %', v_total_updated, v_last_id;
        END IF;
    END LOOP;
END;
$$;
```

### Step 1.4: Create Index Concurrently

```sql
-- Create index concurrently (no blocking writes)
CREATE INDEX CONCURRENTLY idx_events_tenant_region_created
ON events (tenant_id, account_region, created_at DESC);
```

**PostgreSQL Lock Behavior:** `CREATE INDEX CONCURRENTLY` acquires `SHARE UPDATE EXCLUSIVE` lock, which blocks `ALTER TABLE` but allows reads and writes. The index build runs in the background.

**Index Design Rationale:**
- `(tenant_id, account_region, created_at DESC)` supports the query pattern
- `account_region` is a low-cardinality column, so the index is efficient
- `created_at DESC` supports the `ORDER BY` clause

**Monitoring Index Build:**
```sql
-- Monitor index build progress
SELECT 
    indexrelname,
    pg_size_pretty(pg_relation_size(indexrelid)) as index_size,
    pg_size_pretty(pg_relation_size(indrelid)) as table_size
FROM pg_stat_user_indexes 
WHERE indexrelname = 'idx_events_tenant_region_created';
```

---

## Phase 2: Migrate (Enforce NOT NULL, Validate)

### Step 2.1: Add CHECK Constraint (NOT VALID)

```sql
-- Add CHECK constraint as NOT VALID (instant, no table scan)
ALTER TABLE events 
ADD CONSTRAINT events_account_region_not_null 
CHECK (account_region IS NOT NULL) NOT VALID;
```

**PostgreSQL Lock Behavior:** `ADD CONSTRAINT ... NOT VALID` acquires `ACCESS EXCLUSIVE` lock but is instant (no table scan). The constraint is added but not validated.

### Step 2.2: Validate CHECK Constraint

```sql
-- Validate constraint (scans table but doesn't block writes)
ALTER TABLE events 
VALIDATE CONSTRAINT events_account_region_not_null;
```

**PostgreSQL Lock Behavior:** `VALIDATE CONSTRAINT` acquires `SHARE UPDATE EXCLUSIVE` lock, which blocks `ALTER TABLE` but allows reads and writes. The validation scans the table in the background.

**Monitoring Validation:**
```sql
-- Monitor validation progress
SELECT 
    conname,
    convalidated,
    pg_size_pretty(pg_relation_size(conrelid)) as table_size
FROM pg_constraint 
WHERE conname = 'events_account_region_not_null';
```

### Step 2.3: Set NOT NULL

```sql
-- Set NOT NULL (uses CHECK constraint to avoid table scan)
ALTER TABLE events 
ALTER COLUMN account_region SET NOT NULL;
```

**PostgreSQL Lock Behavior:** `SET NOT NULL` acquires `ACCESS EXCLUSIVE` lock but is instant if a valid CHECK constraint exists (PG12+). The constraint is used to avoid scanning the table.

### Step 2.4: Verify No Rows Were Missed

```sql
-- Verify no NULL values remain
SELECT COUNT(*) 
FROM events 
WHERE account_region IS NULL;

-- Verify audit count of 'unknown' rows
SELECT COUNT(*) 
FROM events 
WHERE account_region = 'unknown';

-- Verify index is being used
EXPLAIN (ANALYZE, BUFFERS) 
SELECT * FROM events
WHERE tenant_id = $1 AND account_region = $2 AND created_at >= $3
ORDER BY created_at DESC
LIMIT 200;
```

---

## Phase 3: Contract (Remove Old Code Paths)

### Step 3.1: Remove Dual-Write Logic

Deploy new app version that:
- **Writes** only to `account_region` (no dual-write)
- **Reads** from `account_region` (no fallback)

### Step 3.2: Drop Old Index (If Any)

```sql
-- Drop old index if it exists
DROP INDEX CONCURRENTLY IF EXISTS idx_events_tenant_region_created_old;
```

### Step 3.3: Final Validation

```sql
-- Final validation: no NULL values
SELECT COUNT(*) 
FROM events 
WHERE account_region IS NULL;

-- Final validation: audit count of 'unknown' rows
SELECT COUNT(*) 
FROM events 
WHERE account_region = 'unknown';

-- Final validation: index usage
EXPLAIN (ANALYZE, BUFFERS) 
SELECT * FROM events
WHERE tenant_id = $1 AND account_region = $2 AND created_at >= $3
ORDER BY created_at DESC
LIMIT 200;
```

---

## Rollback Plan

### Rollback Triggers
- **Replica lag > 30 seconds**
- **Database CPU > 70%**
- **Backfill not completing within expected time**
- **Application errors during dual-write**

### Rollback Steps

```sql
-- 1. Remove NOT NULL constraint
ALTER TABLE events 
ALTER COLUMN account_region DROP NOT NULL;

-- 2. Drop CHECK constraint
ALTER TABLE events 
DROP CONSTRAINT events_account_region_not_null;

-- 3. Drop index
DROP INDEX CONCURRENTLY IF EXISTS idx_events_tenant_region_created;

-- 4. Remove column (if needed)
ALTER TABLE events 
DROP COLUMN account_region;
```

**Rollback Window:** 72 hours after old version is absent.

---

## Observability

### Metrics to Monitor

```sql
-- 1. Backfill progress
SELECT 
    last_processed_id,
    batch_size,
    started_at,
    updated_at
FROM backfill_progress 
ORDER BY id DESC 
LIMIT 1;

-- 2. NULL count (should decrease over time)
SELECT COUNT(*) 
FROM events 
WHERE account_region IS NULL;

-- 3. Index build progress
SELECT 
    indexrelname,
    pg_size_pretty(pg_relation_size(indexrelid)) as index_size
FROM pg_stat_user_indexes 
WHERE indexrelname = 'idx_events_tenant_region_created';

-- 4. Write throughput
SELECT 
    tup_inserted,
    tup_updated,
    tup_deleted
FROM pg_stat_user_tables 
WHERE relname = 'events';

-- 5. Replica lag
SELECT 
    pg_last_wal_receive_lsn(),
    pg_last_wal_replay_lsn(),
    EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp())) as lag_seconds;
```

### Alerting

```sql
-- Alert if NULL count is not decreasing
SELECT COUNT(*) 
FROM events 
WHERE account_region IS NULL;

-- Alert if backfill is stuck
SELECT 
    last_processed_id,
    updated_at,
    now() - updated_at as time_since_update
FROM backfill_progress 
ORDER BY id DESC 
LIMIT 1;
```

---

## Proving No Rows Were Missed

### Validation Queries

```sql
-- 1. Verify no NULL values
SELECT COUNT(*) 
FROM events 
WHERE account_region IS NULL;

-- 2. Verify audit count of 'unknown' rows
SELECT COUNT(*) 
FROM events 
WHERE account_region = 'unknown';

-- 3. Verify index is being used
EXPLAIN (ANALYZE, BUFFERS) 
SELECT * FROM events
WHERE tenant_id = $1 AND account_region = $2 AND created_at >= $3
ORDER BY created_at DESC
LIMIT 200;

-- 4. Verify no rows written by old binary during transition
SELECT COUNT(*) 
FROM events 
WHERE account_region IS NULL 
  AND created_at >= $transition_start_time;
```

### Audit Trail

```sql
-- Create audit table for tracking
CREATE TABLE IF NOT EXISTS migration_audit (
    id bigserial PRIMARY KEY,
    phase text NOT NULL,
    action text NOT NULL,
    rows_affected bigint,
    started_at timestamptz DEFAULT now(),
    completed_at timestamptz,
    status text DEFAULT 'in_progress'
);

-- Log migration events
INSERT INTO migration_audit (phase, action, rows_affected, status)
VALUES ('expand', 'add_column', 0, 'completed');

INSERT INTO migration_audit (phase, migrate', 'backfill', rows_affected, 'completed');
```

---

## Summary

| Phase | Action | Lock Type | Duration | Rollback |
|-------|--------|-----------|----------|----------|
| Expand | Add column | ACCESS EXCLUSIVE (instant) | Instant | Drop column |
| Expand | Dual-write | None | 48 hours | Remove dual-write |
| Expand | Backfill | SHARE UPDATE EXCLUSIVE | ~5.5 hours | Stop backfill |
| Expand | Create index | SHARE UPDATE EXCLUSIVE | ~2 hours | Drop index |
| Migrate | Add CHECK | ACCESS EXCLUSIVE (instant) | Instant | Drop constraint |
| Migrate | Validate | SHARE UPDATE EXCLUSIVE | ~1 hour | Drop constraint |
| Migrate | Set NOT NULL | ACCESS EXCLUSIVE (instant) | Instant | Drop NOT NULL |
| Contract | Remove dual-write | None | 48 hours | Re-add dual-write |
| Contract | Drop old index | SHARE UPDATE EXCLUSIVE | Instant | Re-create index |

**Total Duration:** ~7 days (including 48-hour dual-write window and 72-hour rollback window)

**Key PostgreSQL Lock Behaviors:**
- `ALTER TABLE ... ADD COLUMN` (nullable): Instant, ACCESS EXCLUSIVE
- `CREATE INDEX CONCURRENTLY`: SHARE UPDATE EXCLUSIVE, allows reads/writes
- `ADD CONSTRAINT ... NOT VALID`: Instant, ACCESS EXCLUSIVE
- `VALIDATE CONSTRAINT`: SHARE UPDATE EXCLUSIVE, allows reads/writes
- `SET NOT NULL`: Instant if CHECK constraint exists, ACCESS EXCLUSIVE

This plan ensures zero downtime, resumable backfill, safe rollback, and no rows missed during the transition.

---

`finish_reason=stop; wall_s=374.8; completion_tokens=13145`
