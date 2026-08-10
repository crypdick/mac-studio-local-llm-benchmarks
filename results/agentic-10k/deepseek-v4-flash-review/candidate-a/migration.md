# Zero-downtime PostgreSQL column migration

We’ll use an **expand / migrate / contract** plan with a nullable-but-defaulted column, a resumable batch backfill, concurrent index builds, and a dual-read fallback until all old writers are gone. The key is to avoid `ALTER COLUMN SET NOT NULL` on the live 2 TB table; instead we enforce “not null” with a validated `CHECK` constraint and keep the column `NOT NULL DEFAULT 'unknown'` from the moment it is added.

## 1. Lock-behavior notes that drive the plan

- `ADD COLUMN ... NOT NULL DEFAULT 'unknown'` is metadata-only in PostgreSQL 11+ and does **not** rewrite the 2 TB table. It still takes a brief `ACCESS EXCLUSIVE` lock, but normally for milliseconds.
- `CREATE INDEX CONCURRENTLY` does not block DML, but it can take a long time and consumes I/O/CPU. It also cannot run inside a transaction and can leave an invalid index if it fails.
- `ADD CONSTRAINT ... NOT VALID` is metadata-only and takes a brief `ACCESS EXCLUSIVE` lock.
- `VALIDATE CONSTRAINT` takes `SHARE UPDATE EXCLUSIVE`, which allows concurrent `INSERT`/`UPDATE`/`DELETE` but blocks other DDL of the same type. It scans the table and can be throttled by running it with low concurrency.
- `ALTER COLUMN SET NOT NULL` would take `ACCESS EXCLUSIVE` and scan the table; we avoid it entirely.

---

## 2. Ordered plan

### Phase 0: Preflight and safety

- Ensure a primary key exists on `events`; the SQL below assumes `id bigint`. If the real key is composite, adjust the batch predicate.
- Enable monitoring:
  - replica lag: `pg_stat_replication`
  - CPU: OS metrics or cloud monitoring
  - backfill progress: custom progress table
  - index build progress: `pg_stat_progress_create_index`
- Set a feature flag for the new read path so it can be toggled independently.

---

### Phase 1: Expand

#### 1.1 Add the typed column with a safe default

```sql
ALTER TABLE events
  ADD COLUMN account_region text NOT NULL DEFAULT 'unknown';
```

This is fast and does not rewrite the table. Existing rows logically get `'unknown'`.

#### 1.2 Create an audit table for missing historical values

```sql
CREATE TABLE events_account_region_audit (
  captured_at   timestamptz NOT NULL DEFAULT now(),
  missing_count bigint      NOT NULL,
  total_rows    bigint      NOT NULL
);
```

#### 1.3 Deploy new application versions

- New writers set `account_region` explicitly from `payload->>'account_region'`.
- New readers use the **dual-read fallback** below until contraction.
- Old writers continue writing only `payload`; the column default gives them `'unknown'`.

Dual-read query during transition:

```sql
SELECT *
FROM events
WHERE tenant_id = $1
  AND created_at >= $3
  AND (
    account_region = $2
    OR (
      account_region = 'unknown'
      AND payload->>'account_region' = $2
    )
  )
ORDER BY created_at DESC
LIMIT 200;
```

This ensures rows written by old binaries are not silently omitted even before backfill catches them.

---

### Phase 2: Migrate

#### 2.1 Create a backfill progress table

```sql
CREATE TABLE events_account_region_backfill (
  last_id       bigint PRIMARY KEY,
  updated_rows  bigint NOT NULL DEFAULT 0,
  missing_rows  bigint NOT NULL DEFAULT 0,
  finished_at   timestamptz
);
```

#### 2.2 Backfill actual regions in resumable batches

Run this as a background job, one batch at a time, with a commit after each batch. Use `FOR UPDATE SKIP LOCKED` to avoid fighting concurrent writes.

```sql
WITH batch AS (
  SELECT id
  FROM events
  WHERE account_region = 'unknown'
    AND payload ? 'account_region'
    AND id > :last_seen_id
  ORDER BY id
  LIMIT 5000
  FOR UPDATE SKIP LOCKED
), updated AS (
  UPDATE events e
  SET account_region = e.payload->>'account_region'
  FROM batch b
  WHERE e.id = b.id
  RETURNING e.id
)
SELECT count(*) INTO :batch_size FROM updated;
```

After each batch:

```sql
UPDATE events_account_region_backfill
SET last_id = :max_id_from_batch,
    updated_rows = updated_rows + :batch_size
WHERE last_id = :previous_last_id;
```

Throttle between batches:

```sql
SELECT pg_sleep(0.2);  -- tune to keep CPU < 70%
```

If the job is stopped, restart from `last_id` in the progress table. Rows already updated no longer match `account_region = 'unknown'`, so the job is naturally resumable.

#### 2.3 Count and audit rows that lack `account_region` in `payload`

After the backfill above finishes, every row with `payload ? 'account_region'` has been corrected. The remaining `'unknown'` rows are the historical rows that genuinely lack the key.

```sql
INSERT INTO events_account_region_audit (missing_count, total_rows)
SELECT count(*) FILTER (WHERE NOT (payload ? 'account_region')),
       count(*)
FROM events;
```

This is a full scan; run it with a low `statement_timeout` and during a low-load window if needed. It does not block writes.

#### 2.4 Create the final index concurrently

```sql
CREATE INDEX CONCURRENTLY events_tenant_region_created_idx
ON events (tenant_id, account_region, created_at DESC);
```

To reduce CPU/I/O impact:

```sql
SET maintenance_work_mem = '2GB';        -- tune
SET max_parallel_maintenance_workers = 2; -- tune
```

If the index build fails, it leaves an invalid index. Drop it and retry:

```sql
DROP INDEX CONCURRENTLY IF EXISTS events_tenant_region_created_idx;
```

#### 2.5 Add and validate the NOT NULL check constraint

We do **not** use `ALTER COLUMN SET NOT NULL`. Instead:

```sql
ALTER TABLE events
  ADD CONSTRAINT events_account_region_not_null
  CHECK (account_region IS NOT NULL)
  NOT VALID;
```

This is metadata-only and fast.

Then validate it without blocking DML:

```sql
ALTER TABLE events
  VALIDATE CONSTRAINT events_account_region_not_null;
```

`VALIDATE CONSTRAINT` scans the table but allows concurrent writes. It may take a long time; monitor it and let it run.

---

### Phase 3: Contract

#### 3.1 Prove no rows are missed before removing the fallback

Run these checks and require all to return `0`:

```sql
-- No row with a payload region still has the placeholder
SELECT count(*)
FROM events
WHERE account_region = 'unknown'
  AND payload ? 'account_region';

-- No row has a mismatch between payload and typed column
SELECT count(*)
FROM events
WHERE payload ? 'account_region'
  AND account_region IS DISTINCT FROM payload->>'account_region';

-- No nulls (should be impossible due to NOT NULL default, but verify)
SELECT count(*)
FROM events
WHERE account_region IS NULL;
```

Also confirm the old binary has been fully absent for **72 hours** before proceeding.

#### 3.2 Remove the dual-read fallback

Deploy application code that uses only:

```sql
SELECT *
FROM events
WHERE tenant_id = $1
  AND account_region = $2
  AND created_at >= $3
ORDER BY created_at DESC
LIMIT 200;
```

#### 3.3 Drop the temporary fallback index if one was created

If you created a temporary partial expression index for the fallback path, drop it:

```sql
DROP INDEX CONCURRENTLY IF EXISTS events_unknown_region_payload_idx;
```

#### 3.4 Drop the default (optional)

After all writers are new and always supply `account_region`, you can drop the default:

```sql
ALTER TABLE events ALTER COLUMN account_region DROP DEFAULT;
```

This is a catalog-only change and fast. If you prefer to keep the default as a safety net, it is safe to leave it.

#### 3.5 Stop the backfill job and archive progress/audit tables

Keep the audit table for compliance. The backfill progress table can be dropped after verification.

---

## 3. Rollback plan

Until the old binary has been absent for 72 hours, rollback is possible:

1. Stop the backfill job.
2. Deploy the old application read/write path.
3. Drop the new index:
   ```sql
   DROP INDEX CONCURRENTLY IF EXISTS events_tenant_region_created_idx;
   ```
4. Drop the check constraint:
   ```sql
   ALTER TABLE events DROP CONSTRAINT IF EXISTS events_account_region_not_null;
   ```
5. Drop the column:
   ```sql
   ALTER TABLE events DROP COLUMN IF EXISTS account_region;
   ```
6. Drop audit/progress tables if desired.

The `DROP COLUMN` is metadata-only and does not rewrite the table. It will also drop any dependent index/constraint.

---

## 4. Key operational gates

| Gate | Condition |
|---|---|
| Expand complete | Column added; new app deployed with dual-read fallback; old app still healthy |
| Backfill complete | `events_account_region_backfill.finished_at` set; no rows with `account_region = 'unknown' AND payload ? 'account_region'` |
| Index ready | `pg_class.relkind = 'i'` and `pg_index.indisvalid = true` for the new index |
| Constraint validated | `pg_constraint.convalidated = true` for `events_account_region_not_null` |
| Contract ready | Old binary absent for 72 hours; all three “no missed rows” checks return `0` |
| Contract complete | Fallback removed; default dropped; backfill job stopped |

---

## 5. Material uncertainties

- The exact primary key of `events` is assumed to be `id`. If it is composite, the backfill batch predicate must use that key.
- The `payload` JSONB shape and the meaning of `account_region` inside it must be stable. If old binaries can write malformed payloads, the backfill and fallback need additional validation.
- `CREATE INDEX CONCURRENTLY` and `VALIDATE CONSTRAINT` can still be I/O intensive. You must monitor CPU and replica lag and be prepared to pause or throttle other maintenance.
- The brief `ACCESS EXCLUSIVE` locks for `ADD COLUMN`, `ADD CONSTRAINT NOT VALID`, and `DROP DEFAULT` are unavoidable in PostgreSQL. They are metadata-only and should be sub-second, but they will queue concurrent DML for that instant. If even that is unacceptable, this plan would need a different mechanism such as a new table with logical replication.

---

`finish_reason=stop; wall_s=1957.7; completion_tokens=12413`
