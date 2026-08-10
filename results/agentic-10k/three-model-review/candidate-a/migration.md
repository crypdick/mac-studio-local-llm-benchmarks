# Zero-downtime PostgreSQL column migration

<think>
Okay, let me tackle this PostgreSQL migration problem. The user wants a zero-downtime migration for a massive 2TB events table with high write volume. 

First, I need to understand all the constraints. The table gets 15k writes per second, so any operation that locks the table is out of the question. The main goal is to replace a JSONB field (payload->>'account_region') with a proper NOT NULL text column, plus create a new index for a specific query pattern. 

Hmm... the tricky parts here are:
- Dual-write during transition (old app uses JSON, new app writes both)
- Backfilling 2TB without blocking writes
- Handling missing regions (must set to "unknown")
- Ensuring no rows get missed during transition
- Rollback safety for 72 hours after old version disappears

I recall that PostgreSQL 16 has some nice features for this. The GENERATED ALWAYS AS identity columns might help, but wait - no, we need to backfill existing data. Maybe using a DEFAULT with a function? But that won't work for existing rows. 

Ah! The key is to add the column as nullable first, then backfill, then set NOT NULL. But with 2TB data, backfill could take days. Need to do it in chunks without locking. 

For the index... creating it concurrently is essential, but even that can cause issues on big tables. Should probably create it after backfill to avoid bloating the index with NULLs. 

Wait - the query uses tenant_id, account_region, created_at. So the ideal index would be (tenant_id, account_region, created_at DESC). But need to confirm the order. Since it's ORDER BY created_at DESC, putting it last in index makes sense for the sort.

Biggest worry: how to handle the transition period where some rows have the new column and some don't. The app must read from both during migration. And we must prove no rows are missed before switching to new column.

For backfill safety: using a WHERE clause that checks for NULL in new column but exists in old JSON path. But what if new writes happen during backfill? Need to lock only the rows being updated, not the whole table. UPDATE ... WHERE with LIMIT and sleep between chunks should work.

Locking behavior is critical here. ALTER TABLE ADD COLUMN is fast (just catalog change), but adding NOT NULL constraint requires a SHARE UPDATE EXCLUSIVE lock which is short but could block. Better to add as nullable first.

Rollback plan: if we need to revert, we must drop the new column and index, but only after confirming no new app is writing to it. The 72-hour window after old app disappears gives us safety.

For the "unknown" policy: during backfill, when JSON path is missing, set to "unknown". But need to count those rows for audit. Maybe log the count per chunk.

Validation is crucial. Before dropping the old JSON field, we must verify that all rows have been backfilled and new app is writing to both. A query comparing counts between old and new should work, but need to handle concurrent writes.

Throttling the backfill is non-negotiable. With 15k writes/sec, even a small backfill job could overwhelm the system. Using pg_sleep between chunks and monitoring replica lag.

Let me structure this in phases:
1. Expand: add new column and index (but index later)
2. Migrate: dual-write, backfill in chunks
3. Contract: switch to new column, drop old

Wait - the index should be created after backfill to avoid indexing NULLs. But the query needs it for performance. Maybe create it during migrate phase after some backfill? No, better to create after backfill completes to minimize index size.

For the dual-read: app must check new column first, if NULL then fall back to JSON. But during backfill, some rows will have NULL in new column even if JSON has value. So app must always check both until backfill is 100% done.

Proving no rows missed: before contract, run a query that finds rows where new column != JSON value (accounting for "unknown" conversion). Should return zero rows.

Biggest risk: what if backfill misses some rows due to concurrent writes? The UPDATE WHERE condition must be precise: WHERE account_region IS NULL AND payload ? 'account_region'. But if a new write happens during backfill, it might set account_region, so the WHERE condition would skip it - which is correct.

For the NOT NULL constraint: can't add until backfill is 100% complete and we're sure no NULLs exist. But during backfill, new writes set it, so after backfill finishes, we need one final check for NULLs before setting NOT NULL.

Rollback steps: if we need to revert before contract phase, just stop new app from writing to new column. But after contract, it's harder. The 72-hour window is our safety net.

Let me outline the exact SQL steps with lock considerations:
- Phase 1: Add column (fast, minimal lock)
- Phase 2: Backfill in chunks (careful UPDATEs)
- Phase 3: Add index concurrently (long but non-blocking)
- Phase 4: Set NOT NULL (requires full table scan but no write lock)
- Phase 5: Switch app to read new column only
- Phase 6: Drop old JSON dependency

Wait - the NOT NULL constraint addition requires ACCESS SHARE lock on the table, which blocks only DDL, not writes. So it's safe as long as we do it during low traffic? But with 15k writes/sec, maybe do it during backfill finalization.

For observability: track backfill progress with a counter, monitor replica lag, CPU. Set alerts for lag > 25s.

The final validation query must be:
SELECT COUNT(*) FROM events 
WHERE (account_region IS NULL AND payload ? 'account_region')
   OR (account_region = 'unknown' AND (payload->>'account_region') IS NOT NULL)
   OR (account_region <> 'unknown' AND account_region <> (payload->>'account_region'));

This should return 0 before contract phase.

Throttling parameters: 5000 rows per chunk, 100ms sleep. Adjust based on CPU and lag metrics.

Rollback during backfill: just stop the backfill job. The new column has partial data but app is still reading from JSON, so no data loss.

After contract phase, rollback would require restoring from backup since we dropped the JSON dependency. Hence the 72-hour window - we keep old app running until sure new app is stable.

I think this covers all requirements. Now to structure it as ordered steps with exact SQL and gates.
</think>

### Zero-Downtime PostgreSQL 16 Migration Plan for `events` Table  
**Total Data**: 2 TB | **Write Load**: 15,000 writes/sec | **Target Index**: `(tenant_id, account_region, created_at DESC)`  

---

#### **Phase 0: Pre-Migration Validation**  
*Execute before any DDL. Confirm operational readiness.*  
```sql
-- Verify no existing column/index conflicts
SELECT 1 FROM pg_attribute 
WHERE attrelid = 'events'::regclass 
  AND attname IN ('account_region', 'idx_events_tenant_region_created');
-- Must return 0 rows

-- Check historical "unknown" count (for audit baseline)
SELECT COUNT(*) 
FROM events 
WHERE (payload->>'account_region') IS NULL;
-- Record result (e.g., 1,205,432 rows)
```

**Deploy Gate**: Proceed only if no conflicts exist and baseline audit count is recorded.  

---

#### **Phase 1: Expand (Add New Column & Dual-Write Support)**  
*Minimal locks; safe for concurrent writes. Old app continues using `payload`.*  

1. **Add nullable column** (fast, `ACCESS EXCLUSIVE` lock for milliseconds):  
   ```sql
   ALTER TABLE events ADD COLUMN account_region text;
   -- Lock: ACCESS EXCLUSIVE (brief, non-blocking for writes)
   ```

2. **Deploy new application version** (v2) with:  
   - **Dual-write logic**:  
     ```python
     # New app writes to BOTH fields
     account_region = payload.get('account_region', 'unknown')
     db.execute("""
         INSERT INTO events (..., payload, account_region) 
         VALUES (..., %s, %s)
     """, [payload, account_region])
     ```
   - **Dual-read logic** (for 48h+ transition):  
     ```sql
     SELECT 
         COALESCE(account_region, payload->>'account_region', 'unknown') AS account_region,
         ...
     FROM events
     WHERE tenant_id = $1 
       AND COALESCE(account_region, payload->>'account_region', 'unknown') = $2
       AND created_at >= $3
     ORDER BY created_at DESC
     LIMIT 200;
     ```

**Deploy Gate**:  
- v2 deployed to 100% of instances.  
- Monitoring confirms:  
  - Write latency < 10ms (p99)  
  - Replica lag < 15s  
  - CPU < 60%  

---

#### **Phase 2: Migrate (Backfill & Index Creation)**  
*Resumable backfill; throttled to avoid replica lag/CPU spikes.*  

1. **Backfill historical data** (resumable, chunked):  
   ```sql
   -- Run repeatedly until 0 rows updated
   WITH chunk AS (
     SELECT id 
     FROM events 
     WHERE account_region IS NULL 
       AND (payload->>'account_region') IS NOT NULL 
     ORDER BY id 
     LIMIT 5000 FOR UPDATE SKIP LOCKED
   )
   UPDATE events e
   SET account_region = COALESCE(payload->>'account_region', 'unknown')
   FROM chunk c 
   WHERE e.id = c.id;
   -- Throttle: 100ms sleep between chunks
   -- Monitor: replica_lag < 25s, CPU < 65%
   ```

   - **Critical safety**: `FOR UPDATE SKIP LOCKED` avoids write contention.  
   - **"unknown" policy**: Rows missing `account_region` in `payload` get `'unknown'` (audited via Phase 0 baseline).  
   - **Resume logic**: Script tracks last processed `id`; restarts from failure point.  

2. **Backfill missing "unknown" rows** (after main backfill):  
   ```sql
   UPDATE events 
   SET account_region = 'unknown' 
   WHERE account_region IS NULL 
     AND (payload->>'account_region') IS NULL;
   -- Single run (low volume)
   ```

3. **Add index concurrently** (after backfill completes):  
   ```sql
   CREATE INDEX CONCURRENTLY idx_events_tenant_region_created 
   ON events (tenant_id, account_region, created_at DESC);
   -- Lock: SHARE UPDATE EXCLUSIVE (non-blocking for writes)
   -- Monitor: Index build progress via pg_stat_progress_create_index
   ```

**Deploy Gates**:  
- Backfill complete: `SELECT COUNT(*) FROM events WHERE account_region IS NULL;` returns `0`.  
- Index built: `SELECT 1 FROM pg_indexes WHERE indexname = 'idx_events_tenant_region_created';` returns `1`.  
- Replica lag sustained < 25s during backfill/index build.  

---

#### **Phase 3: Contract (Enforce New Schema & Validate)**  
*Finalize migration; prepare for rollback safety.*  

1. **Enforce `NOT NULL`** (after 100% backfill):  
   ```sql
   ALTER TABLE events 
   ALTER COLUMN account_region SET NOT NULL;
   -- Lock: SHARE UPDATE EXCLUSIVE (brief scan, non-blocking for writes)
   ```

2. **Deploy v3 application** (reads ONLY `account_region`):  
   ```sql
   SELECT * FROM events
   WHERE tenant_id = $1 AND account_region = $2 AND created_at >= $3
   ORDER BY created_at DESC
   LIMIT 200;
   ```

3. **Critical validation** (prove no rows missed):  
   ```sql
   SELECT COUNT(*) 
   FROM events 
   WHERE 
     (account_region <> 'unknown' AND account_region <> (payload->>'account_region')) OR
     (account_region = 'unknown' AND (payload->>'account_region') IS NOT NULL);
   -- MUST return 0
   ```
   - **Why this works**: Catches rows where:  
     (a) New column ≠ JSON value (excluding `'unknown'`), OR  
     (b) New column is `'unknown'` but JSON has a value (violates policy).  

4. **Rollback safeguard**:  
   - Retain v2 binaries for **72 hours after v3 deploys**.  
   - If rollback needed:  
     ```sql
     ALTER TABLE events ALTER COLUMN account_region DROP NOT NULL;
     DROP INDEX CONCURRENTLY idx_events_tenant_region_created;
     -- Revert to v2 app (dual-read logic)
     ```

**Deploy Gate**:  
- Validation query returns `0`.  
- v3 running for ≥ 24h with stable metrics.  
- Old v1/v2 binaries confirmed absent for ≥ 72h (per policy).  

---

#### **Phase 4: Cleanup (Post-Rollback Window)**  
*After 72h with no old binaries*:  
```sql
-- Optional: Remove JSON dependency (if policy allows)
-- ALTER TABLE events DROP COLUMN payload; -- NOT REQUIRED per problem statement
```

---

### **Key PostgreSQL Lock Behavior & Mitigations**  
| **Operation**                          | **Lock Type**               | **Risk**                                  | **Mitigation**                              |  
|----------------------------------------|-----------------------------|-------------------------------------------|---------------------------------------------|  
| `ALTER TABLE ADD COLUMN`               | `ACCESS EXCLUSIVE`          | Milliseconds; blocks all                | Safe at low traffic                         |  
| Backfill `UPDATE`                      | `ROW EXCLUSIVE`             | Contention on hot rows                  | `SKIP LOCKED` + chunking                    |  
| `CREATE INDEX CONCURRENTLY`            | `SHARE UPDATE EXCLUSIVE`    | Blocks only DDL, not writes             | Monitor build progress                      |  
| `ALTER ... SET NOT NULL`               | `SHARE UPDATE EXCLUSIVE`    | Brief full scan                         | Run after backfill (no NULLs)               |  

---

### **Operational Safeguards**  
- **Throttling**: Backfill chunks limited to 5,000 rows + 100ms sleep. Auto-pauses if:  
  - Replica lag > 25s (`pg_stat_replication`), OR  
  - CPU > 65% (`pg_stat_bgwriter`).  
- **Observability**:  
  - Track backfill progress: `events_backfill_progress{status="completed"}` (Prometheus).  
  - Alert on validation failure: `migration_validation_errors > 0`.  
- **Rollback**:  
  - Possible until 72h after old binaries retire (v2 reverts to dual-read).  
  - **Never** drop `payload` during migration (retained for rollback).  
- **No Silent Omissions**:  
  - Dual-read logic ensures old writes (via `payload`) are always visible until v3 deploys.  
  - Validation query guarantees 100% coverage before contract.  

---

### **Why This Works**  
1. **Zero downtime**: All operations avoid `ACCESS EXCLUSIVE` locks during writes.  
2. **No missed rows**: Dual-read + validation query covers all transition states.  
3. **Rollback-safe**: Old app logic remains functional until 72h after migration.  
4. **Performance**: Index built *after* backfill avoids bloat; throttling prevents resource exhaustion.  
5. **Audit compliance**: Historical "unknown" count tracked from Phase 0.  

**Final Validation Before Contract**:  
```sql
-- MUST return 0 (proves no rows missed during transition)
SELECT COUNT(*) FROM events 
WHERE (account_region <> 'unknown' AND account_region <> (payload->>'account_region'))
   OR (account_region = 'unknown' AND (payload->>'account_region') IS NOT NULL);
```

---

`finish_reason=stop; wall_s=162.1; completion_tokens=3472`
