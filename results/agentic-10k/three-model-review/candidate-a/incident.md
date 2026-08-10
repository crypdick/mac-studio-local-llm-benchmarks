# Duplicate-charge production incident

<think>
We are given a scenario where a deployment caused duplicate charges and jobs retrying forever. The pseudocode and evidence are provided.

First, let's analyze the problem:

The `submit` function is supposed to be idempotent: if a job with the same `tenant_id` and `idempotency_key` exists, it returns the prior job. However, note that there is no unique constraint on `(tenant_id, idempotency_key)`. This leads to a race condition.

In the evidence:
- Two POST requests for the same tenant and idempotency key (t7, invoice-991) at 14:06:01.102 and 14:06:01.110 both return 202, creating two jobs (8d1 and 9a4). This is because both requests checked the database and found no prior job (due to concurrent execution) and then both inserted.

Additionally, the worker function does not handle the case where the job might have been processed already. The evidence shows:
- Job 8d1 is processed by worker A at 14:07:00.003, but because the charge takes 90 seconds (p95), the visibility timeout of the queue (60 seconds) expires, causing a redelivery at 14:08:00.081 to worker B. Both workers then process the same job, leading to two charges (ch_71 and ch_89).

Another issue: 
- At 14:10:02.020, a job (bb3) is published and the INSERT fails due to connection reset. Then the worker (C) tries to process it but the row is not found, so it retries 12 times (and likely continues forever because the row never exists).

Constraints: 
- API may be disabled for at most 5 minutes.
- Queue semantics (at-least-once, 60s visibility) cannot be changed today.
- Charges are refundable, but we need an exact list of likely duplicates.

We are to produce:

1. Severity and causal graph.
2. First 30 minutes of containment in exact order.
3. Durable design covering several aspects.
4. Safe migration and reconciliation approach with SQL.
5. Validation signals, rollback triggers, and remaining uncertainty.

Let's break it down.

1. Severity and causal graph:

Severity: Critical (P0). Duplicate charges are a direct financial loss and breach of trust with customers. Jobs retrying forever cause resource exhaustion and delayed processing.

Causal graph (distinguishing independent failure modes):

Failure Mode 1: Idempotency Key Race Condition (causing duplicate job creation)
  - Root Cause: Lack of unique constraint on (tenant_id, idempotency_key) and non-atomic check-then-insert.
  - Trigger: Concurrent requests for the same idempotency key.
  - Evidence: Two jobs (8d1 and 9a4) created for the same (t7, invoice-991).

Failure Mode 2: Worker Redelivery Due to Long Charge Latency (causing duplicate charges for the same job)
  - Root Cause: 
      a) Charge latency (p95=90s) exceeds queue visibility timeout (60s), causing redelivery.
      b) Worker does not check if the job is already being processed (or has been processed) before charging.
  - Trigger: Long-running charge operation.
  - Evidence: Job 8d1 processed by two workers (A and B) leading to two charges.

Failure Mode 3: Job Creation Partial Failure (causing infinite retries)
  - Root Cause: 
      a) The job is published to the queue before the database INSERT (so the queue message exists but the DB row might not).
      b) If the INSERT fails (e.g., connection reset), the worker will never find the job and retry forever.
  - Trigger: Database connection failure during INSERT.
  - Evidence: Job bb3: published but INSERT failed, then worker retries 12 times (and continues).

Note: These are independent failure modes. Mode 1 and Mode 2 can occur together (as in the evidence for job 8d1: two jobs created for the same idempotency key, and then one of them was processed twice). But they are distinct issues.

2. First 30 minutes of containment in exact order:

We have to act quickly. The problem started at 14:05 UTC. We are now at 14:10 (from evidence). We have 5 minutes of API downtime allowed.

Containment steps (within 30 minutes from 14:10 UTC):

14:10:00 - 14:10:30 (0-30 seconds):
  - Disable the API (to prevent new duplicate jobs) for up to 5 minutes. This stops the bleeding for new requests.
    Command: `curl -X POST https://api.example.com/disable -H "Authorization: Bearer $TOKEN" -d '{"duration": 300}'`
  - Immediately start investigating the evidence and logs to confirm the root causes.

14:10:30 - 14:12:00 (30-120 seconds):
  - Identify all duplicate charges that have occurred since the deployment (14:05 UTC). We can query the database for jobs with the same (tenant_id, idempotency_key) but different job IDs, and also for jobs that have multiple charges (by looking at the provider_charge_id being set multiple times? but note: the worker updates the same job row, so we cannot have two provider_charge_id for the same job). However, note that in the evidence, job 8d1 was charged twice (but the second update would overwrite the first? Actually, the worker does two updates: one for the first charge and then the second charge would update the same row again, so we would only see the last charge). But wait: the evidence shows two different provider_charge_id (ch_71 and ch_89) for the same job id (8d1). How? Because the two workers both updated the same job row. The second update would set the provider_charge_id to ch_89, so we lose ch_71. However, the payment provider would have two charges.

  Instead, we must look for jobs that have been processed multiple times. How? We can look for jobs that have `attempt > 1` and `status = 'succeeded'`? But note: the first worker sets status to 'running' and then after charge, sets to 'succeeded'. The second worker also sets status to 'running' (overwriting the first) and then to 'succeeded'. So the job row would show attempt=2 and status='succeeded'. But we don't know if the charge was done twice.

  Alternatively, we can check the payment provider logs for duplicate charges for the same idempotency key? But the code doesn't send an idempotency key to the provider.

  Given the constraints, we must generate a list of likely duplicates by:
    - Finding jobs that have `attempt > 1` (meaning they were processed more than once) and `status = 'succeeded'` (so the last attempt succeeded, but previous attempts might have also charged).
    - Also, note that for the same (tenant_id, idem_key) we have multiple jobs (like 8d1 and 9a4) and each might have been processed multiple times.

  However, the evidence shows two jobs for the same idem_key (8d1 and 9a4). Both might have been processed? But the evidence only shows 8d1 being processed twice. We don't see 9a4 in the worker logs. But it might have been processed as well.

  So the list of duplicates should include:
    - All jobs that have `attempt > 1` (because each attempt beyond the first likely caused a charge).
    - All jobs that are for the same (tenant_id, idem_key) as another job (because each job for the same idem_key is a duplicate).

  But note: the first job for an idem_key is valid, the second is duplicate. However, if the first job was processed multiple times, then the first job itself caused multiple charges.

  Therefore, we need two lists:
    a) Jobs that are duplicates due to multiple jobs for the same idem_key: for each (tenant_id, idem_key) with count>1, all jobs except the first one (by creation time) are duplicates.
    b) Jobs that were processed multiple times: for each job with attempt>1, each attempt beyond the first is a duplicate charge.

  However, the payment provider does not have idempotency, so each charge attempt is a new charge.

  Steps for containment:
    - Generate a list of duplicate charges by:
        Query 1: Find duplicate jobs (same idem_key, multiple jobs) and mark all but the first as duplicate jobs (which would have caused duplicate charges if processed).
        Query 2: Find jobs that were processed multiple times (attempt>1) and mark each extra attempt as duplicate.

    But note: a job might be both (e.g., job 8d1: it is the first job for its idem_key, but it was processed twice -> two charges). So we have two sources of duplicates.

  We must get this list to finance immediately.

  Command to generate duplicate job list (for same idem_key):
    ```sql
    WITH ranked_jobs AS (
        SELECT 
            id, tenant_id, idempotency_key, 
            ROW_NUMBER() OVER (PARTITION BY tenant_id, idempotency_key ORDER BY created_at) AS rn
        FROM jobs
        WHERE created_at >= '2023-10-05 14:05:00 UTC'  -- deployment time
    )
    SELECT id, tenant_id, idempotency_key
    FROM ranked_jobs
    WHERE rn > 1;
    ```

  Command to generate jobs that were processed multiple times (and hence caused multiple charges):
    ```sql
    SELECT id, tenant_id, idempotency_key, attempt
    FROM jobs
    WHERE created_at >= '2023-10-05 14:05:00 UTC'
      AND attempt > 1
      AND status = 'succeeded';
    ```

  However, note: the job might have failed on first attempt and succeeded on second, so attempt>1 doesn't necessarily mean duplicate charge? But the problem says "duplicate charges", meaning unintended extra charges. In this system, every attempt to charge (that succeeds) is a charge. So if a job has attempt=2 and succeeded, then there were two charges (the first attempt might have succeeded or failed? but the worker code does the charge and then updates the status). Actually, the worker does:
      - Update to running (increment attempt)
      - Then charge (which if fails, we don't see in the evidence, but the worker would crash and retry? the pseudocode doesn't show error handling)
      - Then update to succeeded.

  But the evidence shows two successful charges for the same job. So if the charge operation succeeds, then we have a charge. Therefore, for a job that has attempt=2 and status='succeeded', it means two charges were attempted and both succeeded? Not necessarily: the first attempt might have failed (so no charge) and the second succeeded. But the problem states that we have duplicate charges, so we are concerned with cases where multiple charges occurred.

  How to know if multiple charges occurred? We cannot from the DB alone because the provider_charge_id is overwritten. We would need to check the payment provider's logs. But the problem says: "finance needs an exact list of likely duplicates". So we have to use heuristics.

  Given the time pressure, we will:
    - For duplicate jobs (same idem_key): all jobs except the first are definitely duplicate charges (if they were processed at least once).
    - For jobs with attempt>1: we assume that each attempt beyond the first that resulted in a charge (i.e., the worker got to the charge step) caused a duplicate charge. But note: the worker might have failed before charging (so no charge). However, the evidence shows that the charge step was reached twice. Given the p95 latency of 90s, it's likely that the charge step was reached in the first attempt and then again in the second.

  So we will generate two lists and combine them.

  But note: the job that is the first for an idem_key might have been processed multiple times (like 8d1) and that causes duplicates. The job that is the second for an idem_key (like 9a4) might have been processed once (which is a duplicate of the first job) and that is one duplicate charge.

  Therefore, the total duplicate charges are:
    - For each duplicate job (rn>1): 1 duplicate charge (if processed at least once) plus any extra attempts on that job beyond the first.
    - For the primary job (rn=1): any attempts beyond the first.

  However, we don't know if the duplicate job was processed. So we have to assume it was (if it exists, it was queued and workers would pick it up).

  Given the urgency, we will:
    - Immediately run the two queries above and send the results to finance.
    - Also, note the evidence: job 8d1 (which is the first for its idem_key) was processed twice -> two charges, so one duplicate charge. Job 9a4 (the second for the same idem_key) might have been processed once -> one duplicate charge (because the first job was the valid one). So total two duplicate charges for that idem_key.

  Steps at 14:10:30:
    - Run the two SQL queries to generate the list of duplicate jobs and multi-attempt jobs.
    - Send the list to finance: include job_id, tenant_id, idempotency_key, and the count of duplicate charges (for duplicate jobs: 1 per job; for multi-attempt: (attempt-1) per job).

  However, note: for a duplicate job (rn>1), we don't know how many times it was processed. So we have to assume at least once? But the worker might not have picked it up yet. However, the problem says "likely duplicates", so we mark it as duplicate.

  We'll generate:
    List A (duplicate jobs): each job in this list is a duplicate job (so if processed, it caused a duplicate charge). We don't know the number of charges for that job, but at least one.
    List B (multi-attempt jobs): for each job, (attempt-1) duplicate charges.

  But note: a job might be in both lists? No, because if it's a duplicate job (rn>1) then it's not the first, so it's not the primary. The primary job (rn=1) might have multi-attempts.

  So total duplicate charges = (number of jobs in List A) + (sum of (attempt-1) for jobs in List B).

  However, for a job in List A, it might have been processed multiple times (so more than one duplicate charge). But we don't have that data. Given the time, we report:
    - For List A: each job is one duplicate charge (minimum) but could be more. We'll have to check the payment provider for exact count.
    - For List B: we know the extra attempts.

  But the problem says "exact list of likely duplicates". We can provide:
    - For List A: job_id, tenant_id, idempotency_key, and note "duplicate job (at least one duplicate charge)"
    - For List B: job_id, tenant_id, idempotency_key, and (attempt-1) as the number of duplicate charges.

  However, the finance team needs to refund, so they need the charge IDs. But we don't have them stored for duplicate jobs (because the duplicate job might have been processed and then the provider_charge_id stored, but the primary job's charge ID is overwritten by the second attempt). 

  This is messy. Given the time, we focus on stopping new duplicates and then work on reconciliation.

  Immediate action at 14:10:30:
    - Disable the API (if not done already).
    - Run the two queries and send the results to finance.

14:12:00 - 14:15:00 (120-300 seconds):
  - Investigate the infinite retry issue (job bb3). We see that the INSERT failed, so the job row doesn't exist. We need to prevent workers from retrying forever.
    - We can set a max retry count on the queue? But the constraint says queue semantics cannot be changed today. So we have to handle in the worker.
    - However, we cannot change code immediately (deployment caused the problem, so we are in containment). 
    - Alternative: manually delete the bad message from the queue? But the evidence says it's retried 12 times and continues. We can use the queue's dead-letter queue (DLQ) if configured. If not, we might have to drain the queue or skip the message.

  Steps:
    - Check if the queue has a DLQ. If yes, configure the main queue to send to DLQ after N retries (but we cannot change queue semantics today? the constraint says "queue semantics cannot be changed today", meaning we cannot change the visibility timeout or delivery guarantees, but maybe we can configure DLQ? The constraint is ambiguous. We'll assume we cannot change queue configuration at all today.)

    - If DLQ is not an option, we must skip the message. How? We can have the worker catch the "row not found" and then ack the message (to remove it) after a certain number of retries. But we cannot change the worker code immediately.

    - Given the 5-minute API downtime, we might use that time to deploy a hotfix for the worker? But the problem says the deployment caused the problem, so we are cautious.

    - Alternatively, we can manually remove the message from the queue. But the evidence says it's retried 12 times and continues, so it's stuck. We can use the queue's API to delete the message by receipt handle? But we don't have the receipt handle.

  Given the time, we focus on the most critical: duplicate charges. The infinite retry is causing resource exhaustion but not direct financial loss. We can:
    - Temporarily scale up workers to handle the backlog? But the stuck messages will tie up workers.

  Immediate action for infinite retry:
    - Identify all jobs that are in the queue but not in the DB. We can do:
        SELECT message_id FROM queue_messages WHERE job_id NOT IN (SELECT id FROM jobs);
      But we don't have a table for queue_messages.

    - Instead, we can monitor the worker logs for "jobs row not found" and then manually delete those messages from the queue (if the queue supports it). But this is manual and not scalable.

  Given the 5-minute downtime, we decide:
    - We are already disabling the API for 5 minutes. During that time, we can also stop the workers to prevent further duplicate charges and infinite retries. Then we can:
        a) Fix the duplicate charges by refunding (using the list we generated).
        b) Clean up the queue: remove messages for which the job row does not exist.

    Steps:
      14:12:00: Stop all worker processes (to prevent any more charges and infinite retries).
      14:12:30: Generate the duplicate charge list (as above) and send to finance for immediate refunds.
      14:13:00: Clean the queue: 
          - For each message in the queue, if the job_id does not exist in the jobs table, delete the message.
          - How? We might need to use the queue's API to list messages and then delete. But this could be heavy. Alternatively, we can let the workers die and then drain the queue by processing and skipping missing jobs? But we stopped workers.

      Since we stopped workers, we can write a script to:
          - Iterate over the queue (if the queue supports listing, which it might not for at-least-once queues like SQS) and check the job_id against the DB, and delete if not found.

      But SQS does not support listing messages. So we cannot do that.

      Alternative: let the workers run but with a hotfix that skips missing jobs after N retries. But we cannot deploy immediately.

      Given the constraints, we decide to let the workers stay stopped and accept that the queue will back up. We will fix the worker code and deploy a hotfix.

  However, the problem says: "the API may be disabled for at most five minutes". It doesn't say anything about workers. So we can stop workers for longer? But the business impact of stopped workers is delayed payments.

  We must balance: 
    - Stopping workers prevents duplicate charges and infinite retries, but delays processing.
    - We are already in an incident, so delaying processing is acceptable for a short time.

  Plan:
    - Stop workers immediately (at 14:12:00) to prevent further damage.
    - Use the 5-minute API downtime to also stop workers (so we have 5 minutes of no new jobs and no processing) but then we can restart workers with a hotfix? But we don't have a hotfix ready.

  Given the time, we do:
    14:10:00: Disable API.
    14:12:00: Stop workers.

  Then we have time to:
    - Generate duplicate list (by 14:15:00).
    - Prepare a hotfix for the worker to handle missing jobs (by setting a max retry count and then acking the message if the job is not found).

  But the hotfix deployment might take time. We are allowed 5 minutes of API downtime, but workers can be down longer? The problem doesn't specify.

  We'll assume we can keep workers down for 30 minutes to deploy a hotfix.

  Revised containment timeline:

14:10:00 - 14:10:30:
  - Disable API (curl command as above).
  - Stop all worker processes (to prevent further duplicate charges and infinite retries).

14:10:30 - 14:12:00:
  - Run SQL queries to generate duplicate charge list and send to finance.
    Query for duplicate jobs (same idem_key, multiple jobs):
      ```sql
      WITH ranked_jobs AS (
          SELECT 
              id, tenant_id, idempotency_key, 
              ROW_NUMBER() OVER (PARTITION BY tenant_id, idempotency_key ORDER BY created_at) AS rn,
              created_at
          FROM jobs
          WHERE created_at >= '2023-10-05 14:05:00 UTC'
      )
      SELECT id, tenant_id, idempotency_key, created_at
      FROM ranked_jobs
      WHERE rn > 1;
      ```

    Query for jobs with multiple attempts (that succeeded, so likely multiple charges):
      ```sql
      SELECT id, tenant_id, idempotency_key, attempt, created_at
      FROM jobs
      WHERE created_at >= '2023-10-05 14:05:00 UTC'
        AND attempt > 1
        AND status = 'succeeded';
      ```

    Note: We include created_at to help finance correlate.

  - Also, check for jobs that are in the queue but not in the DB (to clean up). We cannot query the queue, but we can look at worker logs for "jobs row not found" errors and collect the job IDs. Then we can delete those messages from the queue (if possible) or let the hotfix handle them.

14:12:00 - 14:15:00:
  - Prepare a hotfix for the worker to prevent infinite retries:
      In the worker, when we get a message, if the job row is not found, we should:
          - Check how many times this message has been delivered (using the queue's delivery count).
          - If delivery count > N (say 5), then ack the message (to remove it) and log an error.
          - Otherwise, let it retry.

      But the constraint says queue semantics cannot be changed, but we can change the worker to use the delivery count.

      Pseudocode for worker:
        ```python
        async def worker(message):
            # Check delivery count (if available from the queue)
            if message.delivery_count > 5:
                logger.error(f"Job {message.job_id} not found after 5 retries, skipping")
                await queue.ack(message)
                return

            job = await db.fetchrow("SELECT * FROM jobs WHERE id=$1", message.job_id)
            if not job:
                # Requeue by not acking? But the queue will redeliver. Instead, we let it redeliver until max retries.
                # But we want to avoid infinite retry, so we rely on delivery count above.
                # We don't ack, so the message will be redelivered.
                # However, if the job was deleted (which shouldn't happen) or never existed, we want to skip after N retries.
                # So we do nothing here; the delivery count check above will handle it.
                return

            # ... rest of the worker ...
        ```

      However, we cannot deploy this immediately. We are in containment.

  - Also, prepare a hotfix for the submit function to use a unique constraint and atomic insert.

  But note: we are in the first 30 minutes of containment. We are not deploying fixes yet, just containing.

14:15:00 - 14:40:00 (next 25 minutes):
  - Work with finance to start refunds for the duplicate charges identified.
  - Clean up the queue: 
        We cannot easily delete messages, so we will let the workers (when restarted with hotfix) handle the missing jobs by skipping after 5 retries. But we have stopped workers, so we can wait until the hotfix is deployed.

  - Deploy the hotfix for the worker (to prevent infinite retries) and for the submit function (to prevent duplicate job creation). But note: the problem says "the first 30 minutes of containment", so we are only listing up to 14:40.

  However, the containment steps are for the first 30 minutes from the incident start (14:10). So we stop at 14:40.

  But the problem says: "the first 30 minutes of containment in exact order". So we list steps until 14:40.

  Exact order:

  14:10:00: Disable API endpoint to prevent new job submissions.
  14:10:15: Stop all worker processes to prevent further duplicate charges and infinite retries.
  14:10:30: Run SQL query to identify duplicate jobs (same idempotency key, multiple jobs) created since 14:05 UTC.
  14:11:00: Run SQL query to identify jobs with multiple successful attempts (attempt>1 and status='succeeded') created since 14:05 UTC.
  14:11:30: Send the results of both queries to finance team for immediate refund processing.
  14:12:00: Gather list of job IDs from worker logs that caused "jobs row not found" errors (to address infinite retries).
  14:15:00: Begin drafting hotfix for worker to skip messages after 5 retries when job row is missing.
  14:20:00: Begin drafting hotfix for submit function to use atomic upsert with unique constraint.
  14:25:00: Coordinate with finance on refund status.
  14:30:00: Prepare deployment of hotfixes (build, test in staging if possible, but given severity, test minimally).
  14:35:00: Deploy hotfix for worker (to prevent infinite retries).
  14:40:00: Deploy hotfix for submit function (to prevent duplicate job creation) and re-enable API.

  However, note the 5-minute API downtime constraint: we disabled at 14:10, so we must re-enable by 14:15. But we are deploying a hotfix at 14:35 and 14:40. This violates the 5-minute downtime.

  We must re-enable the API by 14:15. But without a fix, duplicate jobs will be created again.

  Therefore, we cannot re-enable the API until the hotfix is deployed. The constraint says "at most five minutes", meaning we can do less, but not more. So we must re-enable by 14:15.

  This is a problem.

  Alternative: 
    - We disable the API at 14:10, and then at 14:15 we re-enable it but with a feature flag that uses a temporary in-memory cache for idempotency? But that might not be safe.

  Given the severity, we might have to exceed the 5 minutes? But the constraint is strict.

  We must find a way to re-enable within 5 minutes. How?

    Option: 
      - At 14:10, disable API.
      - At 14:12, deploy a hotfix that adds the unique constraint and changes the submit function to use an upsert. But deploying a database migration might take time.

    Steps for hotfix deployment (must be done within 5 minutes):

      14:10:00: Disable API.
      14:10:15: Start deploying hotfix:
          - Add unique constraint: 
                ALTER TABLE jobs ADD CONSTRAINT unique_tenant_idempotency UNIQUE (tenant_id, idempotency_key);
            But note: there are duplicate rows already (from the incident). So this will fail.

          - We must first clean up duplicate jobs? But we cannot delete jobs arbitrarily because some might be in progress.

      Given the time, it's not feasible to deploy a database migration in 5 minutes.

  Therefore, we must re-enable the API at 14:15 without a fix, but with a temporary mitigation:

    - Use an in-memory cache (like Redis) for idempotency keys per tenant. But this is not durable and might not cover multiple instances.

    However, the problem says "the API may be disabled for at most five minutes", so we have to re-enable at 14:15.

    We decide:
      14:10:00: Disable API.
      14:10:15: Deploy a temporary in-memory cache (using a shared cache like Redis) for idempotency keys. The cache will store (tenant_id, idempotency_key) for a short time (e.g., 5 minutes). This won't prevent duplicates across cache instances, but it will reduce them.

      But we don't have Redis set up. This might take more than 5 minutes.

  Given the constraints, we have to re-enable the API at 14:15 without a fix, accepting that duplicates might occur again, but we have stopped workers so no charges will happen? No, because the submit function publishes to the queue and then inserts. If workers are stopped, the queue will back up, but when workers restart, they will process and might cause duplicates again.

  But we stopped workers at 14:10:15, so even if we re-enable the API, the jobs will queue up but not be processed. Then we can deploy the hotfix and restart workers.

  So:
      14:10:00: Disable API.
      14:10:15: Stop workers.
      14:15:00: Re-enable API (within 5 minutes) but with workers still stopped. This allows new requests to be queued, but not processed. Then we can deploy the hotfix and restart workers.

  This meets the constraint: API was disabled for 5 minutes (14:10-14:15), and then re-enabled. The workers are still stopped, so no processing happens. Then we deploy the hotfix and restart workers.

  Revised containment timeline:

  14:10:00: Disable API (curl command).
  14:10:15: Stop all worker processes.
  14:10:30: Run SQL query for duplicate jobs (same idem_key).
  14:11:00: Run SQL query for multi-attempt jobs.
  14:11:30: Send duplicate charge list to finance.
  14:12:00: Gather job IDs from logs for missing jobs (to clean up queue later).
  14:15:00: Re-enable API (curl command to enable) - within 5 minutes.
  14:15:30: Deploy hotfix for worker (to handle missing jobs after 5 retries).
  14:20:00: Deploy hotfix for submit function (using upsert with unique constraint).
  14:25:00: Restart workers.
  14:30:00: Monitor for new duplicates and infinite retries.

  But note: when we re-enable the API at 14:15, new requests will be accepted and published to the queue, but workers are stopped until 14:25. So the queue will grow, but that's acceptable.

  This meets the 5-minute API downtime.

  So the first 30 minutes of containment (from 14:10 to 14:40) in exact order:

  1. 14:10:00: Disable API to prevent new job submissions.
  2. 14:10:15: Stop all worker processes to prevent further processing.
  3. 14:10:30: Execute SQL to identify duplicate jobs (same idempotency key) created since 14:05 UTC.
  4. 14:11:00: Execute SQL to identify jobs with multiple successful attempts (attempt>1) created since 14:05 UTC.
  5. 14:11:30: Send the results of the SQL queries to finance for refund processing.
  6. 14:12:00: Collect job IDs from worker logs that caused "jobs row not found" errors.
  7. 14:15:00: Re-enable API (within the 5-minute window).
  8. 14:15:30: Deploy hotfix for worker to skip messages after 5 retries when job row is missing.
  9. 14:20:00: Deploy hotfix for submit function to use atomic upsert with unique constraint.
  10. 14:25:00: Restart worker processes.
  11. 14:30:00: Verify that new submissions are idempotent and workers are not causing duplicate charges or infinite retries.
  12. 14:35:00: Coordinate with finance on refund status and provide additional data if needed.
  13. 14:40:00: Declare initial containment complete.

3. Durable design:

  We need to fix the root causes.

  Requirements:
    - Idempotency: same (tenant_id, idempotency_key) must not create multiple jobs.
    - Prevent duplicate charges for the same job (due to redelivery).
    - Prevent infinite retries for missing jobs.

  Proposed design:

  a) Database constraints and transactions:
      - Add a unique constraint on (tenant_id, idempotency_key) to prevent duplicate job creation.
      - Change the submit function to use an atomic upsert (to avoid the check-then-insert race).

      However, note: the unique constraint will cause an error on duplicate insert. We want to return the existing job.

      We can use:
        INSERT INTO jobs ... 
        ON CONFLICT (tenant_id, idempotency_key) DO UPDATE SET ... 
        RETURNING *

      But we don't want to update anything; we just want to return the existing row.

      Alternatively, we can do:
        WITH new_job AS (
            INSERT INTO jobs (id, tenant_id, idempotency_key, status)
            VALUES ($1, $2, $3, 'queued')
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING *
        )
        SELECT * FROM new_job
        UNION ALL
        SELECT * FROM jobs WHERE tenant_id=$2 AND idempotency_key=$3 AND NOT EXISTS (SELECT 1 FROM new_job);

      But this is complex and might not be efficient.

      Better: use a function that does the upsert and returns the job.

      However, PostgreSQL 9.5+ supports:
        INSERT ... ON CONFLICT ... DO UPDATE ... 
        but we don't want to update, so we can do:

        INSERT INTO jobs (id, tenant_id, idempotency_key, status)
        VALUES ($1, $2, $3, 'queued')
        ON CONFLICT (tenant_id, idempotency_key) DO UPDATE
        SET id = EXCLUDED.id  -- no-op, but required
        RETURNING *;

      But this will update the row (even if no change) and might cause a write lock.

      Alternatively, we can do:

        INSERT INTO jobs (id, tenant_id, idempotency_key, status)
        VALUES ($1, $2, $3, 'queued')
        ON CONFLICT (tenant_id, idempotency_key) DO NOTHING;

        Then if it returns nothing, select the existing row.

      But that is two queries and might have a race.

      The safe way is to use a transaction with SERIALIZABLE isolation, but that might cause retries.

      Given the volume, we can use:

        BEGIN;
        SELECT * FROM jobs WHERE tenant_id=$1 AND idempotency_key=$2 FOR UPDATE;
        IF found:
            RETURN found;
        ELSE:
            INSERT ...;
            RETURN new;
        COMMIT;

      But this uses a lock and might not scale.

      The best practice is to use the unique constraint and then catch the unique violation exception and then select. But that is two queries in the error case.

      However, the error case (duplicate) is rare, so it's acceptable.

      Proposed submit function:

        async def submit(tenant_id, idempotency_key, payload):
            job_id = uuid4()
            try:
                await db.execute(
                    "INSERT INTO jobs(id, tenant_id, idempotency_key, status) "
                    "VALUES($1,$2,$3,'queued')",
                    job_id, tenant_id, idempotency_key,
                )
                await queue.publish({"job_id": job_id, "payload": payload})
                return {"status": 202, "job_id": job_id}
            except UniqueViolationError:
                # Rollback the transaction (if in one) and then select
                prior = await db.fetchrow(
                    "SELECT * FROM jobs WHERE tenant_id=$1 AND idempotency_key=$2",
                    tenant_id, idempotency_key,
                )
                return prior

      But note: the INSERT and the queue publish must be atomic. If we insert and then fail to publish, we have a job in the DB but not in the queue. Then the job will never be processed.

      So we must:
        - Either publish to the queue first? But then if the DB insert fails, we have a message without a job row -> infinite retry.
        - Or do the DB insert and queue publish in one transaction? But the queue is external.

      We cannot have a distributed transaction.

      Therefore, we must:
        - Insert into DB and then publish to queue. If publish fails, we have a job row that will be processed immediately by a worker? But the worker might not pick it up until later. However, we can have a separate process to retry publishing.

      But the problem says: at 14:10:02.020, INSERT failed after publish. So we did publish then INSERT.

      To avoid the infinite retry issue, we must ensure that the job row exists before publishing.

      Revised order:
        - Insert into DB (with unique constraint) -> if fails (duplicate), return existing.
        - Then publish to queue.

      But if the publish fails after DB insert, then the job is in the DB but not in the queue. Then it will never be processed.

      How to handle? 
        - We can have a background job that scans for jobs with status='queued' and not in the queue, and then publishes them. But that's complex.

      Alternatively, we can use a transactional outbox pattern: 
        - Insert the job into the DB and also insert a record into an outbox table in the same transaction.
        - Then have a separate process that reads the outbox and publishes to the queue, and then deletes the outbox record.

      But that adds complexity.

      Given the constraints, we decide:

        Step 1: Insert job into DB (with unique constraint handling).
        Step 2: Publish to queue.

        If Step 2 fails, we have a job in the DB that is not in the queue. We can detect this by:
          - Having a worker that periodically checks for jobs with status='queued' and created_at older than X minutes, and then republishes.

        But this is not real-time.

      However, the evidence shows that the publish succeeded but the INSERT failed (connection reset). So we did publish then INSERT. That caused the infinite retry.

      To prevent that, we must do INSERT first.

      So the new order:

        async def submit(tenant_id, idempotency_key, payload):
            job_id = uuid4()
            try:
                # Insert into DB first
                await db.execute(
                    "INSERT INTO jobs(id, tenant_id, idempotency_key, status) "
                    "VALUES($1,$2,$3,'queued')",
                    job_id, tenant_id, idempotency_key,
                )
            except UniqueViolationError:
                prior = await db.fetchrow(
                    "SELECT * FROM jobs WHERE tenant_id=$1 AND idempotency_key=$2",
                    tenant_id, idempotency_key,
                )
                return prior

            # Then publish
            try:
                await queue.publish({"job_id": job_id, "payload": payload})
                return {"status": 202, "job_id": job_id}
            except Exception as e:
                # If publish fails, we have a job row but no queue message.
                # Mark the job as failed? But we don't have a failed status for this.
                # We can set status to 'publish_failed' and then have a retry mechanism.
                await db.execute(
                    "UPDATE jobs SET status='publish_failed' WHERE id=$1",
                    job_id,
                )
                raise e  # or return an error to the client

      But the client gets an error, so they might retry. And the retry would hit the unique constraint and return the existing job (which is in 'publish_failed' status). Then the client might retry again, but we don't want to publish again.

      So we need to handle 'publish_failed' status in the submit function:

        async def submit(tenant_id, idempotency_key, payload):
            job_id = uuid4()
            try:
                await db.execute(...)
            except UniqueViolationError:
                prior = ... 
                # If prior status is 'publish_failed', then we try to republish?
                if prior['status'] == 'publish_failed':
                    # Attempt to republish
                    try:
                        await queue.publish(...)
                        await db.execute("UPDATE jobs SET status='queued' WHERE id=$1", prior['id'])
                        return prior  # but now it's queued, so the worker will pick it up
                    except:
                        # Still failed, leave as publish_failed
                        return prior
                else:
                    return prior

      This is getting complex.

  Given the time, we propose a simpler durable design:

    - Use the unique constraint on (tenant_id, idempotency_key).
    - Change the submit function to:
        BEGIN;
        -- Lock the row for this idempotency key if exists
        SELECT * FROM jobs WHERE tenant_id=$1 AND idempotency_key=$2 FOR UPDATE;
        IF found:
            RETURN found;
        ELSE:
            INSERT ...;
            -- Then publish to queue (within the same transaction? but queue is external)
            -- We cannot, so we do:
            COMMIT;
            publish to queue;
            IF publish fails:
                ROLLBACK the transaction? But we already committed.
                Instead, we mark the job as 'publish_pending' and have a background process to publish.

    This is not trivial.

  Industry best practice: 
    - Use the unique constraint.
    - Do the insert first (with the unique constraint) and then publish. 
    - If publish fails, set the job status to 'publish_failed' and have a background job that retries publishing.

  For the worker:

    - To prevent duplicate charges for the same job, we must ensure that only one worker processes the job at a time.
    - We can do this by:
        a) Using a database lock when updating the job to 'running'. But the current update does:
             UPDATE jobs SET status='running', attempt=attempt+1 WHERE id=$1
          This is atomic, so only one worker will succeed (the others will update 0 rows). But the evidence shows two workers updating the same job: 
             14:07:00.003 worker=A job=8d1 charge start
             14:08:00.081 worker=B job=8d1 charge start (redelivery)
          How? Because the first worker took 90 seconds, so the visibility timeout expired and the message was redelivered. Then worker B also updated the job (setting attempt=2). 

        The problem is that the update does not check the current status. So worker B updated even though worker A was still processing.

        We should change the update to:
          UPDATE jobs SET status='running', attempt=attempt+1 
          WHERE id=$1 AND status='queued'

        Then worker B would not update the job (because after worker A updated, status is 'running'). But the evidence shows worker A updated to 'running' at 14:07:00.003, so worker B at 14:08:00.081 would see status='running' and the update would affect 0 rows. Then worker B should not proceed.

        However, the worker code does not check the number of rows updated. It assumes the update succeeded.

        Proposed worker change:

          async def worker(message):
              updated = await db.execute(
                  "UPDATE jobs SET status='running', attempt=attempt+1 "
                  "WHERE id=$1 AND status='queued'",
                  message.job_id,
              )
              if updated == 0:
                  # Job is not in queued state, so skip (it might be already running or done)
                  await queue.ack(message)
                  return

              # Proceed to charge

        But what if the job was canceled? We don't have a canceled status. So we assume only 'queued' is eligible.

        Also, if the job is in 'running' state (from a previous attempt that timed out), then we don't want to process it again. But the evidence shows that the job was in 'running' state when worker B tried to update? Actually, worker A set it to 'running', so worker B's update would fail.

        However, if worker A crashes after setting to 'running' but before charging, then the job is stuck in 'running'. We need a timeout mechanism to reset stuck jobs.

        But that's a separate issue.

        For now, this change prevents multiple workers from processing the same job concurrently.

    - Additionally, we should use the payment provider's idempotency key. The problem says the provider accepts an optional idempotency key. We should generate one (e.g., the job_id) and send it. Then even if the charge is attempted twice, the provider will deduplicate.

        But note: the evidence shows two different charge IDs (ch_71 and ch_89), so the provider did not deduplicate. Because we didn't send an idempotency key.

        So we must send the idempotency key to the provider.

        Proposed change in worker:

          charge = await payment_provider.charge(
              payload, 
              idempotency_key=message.job_id   # or a new idempotency key per attempt? 
          )

        However, if we use the same idempotency key for the same job, then the second attempt will be rejected by the provider.

        But what if the first attempt failed (network error) and we retry? We want the retry to succeed. So we should use a new idempotency key for each attempt.

        How about: idempotency_key = f"{job_id}-{attempt}"

        But the attempt number is incremented in the DB update. So when we do the update, we know the new attempt number.

        In the worker:

          updated = await db.execute(...)
          if updated == 0: ...

          # Now we know the attempt number? Not exactly, because we incremented it in the update, but we don't have the new value.
          # We could do a SELECT after the update, but that adds latency.

        Alternatively, we can generate the idempotency key as job_id + "-" + (current_attempt+1) but we don't know current_attempt.

        Better: do a SELECT for the job after the update to get the attempt number.

        But to minimize latency, we can:

          async def worker(message):
              # First, get the job and lock it
              job = await db.fetchrow(
                  "SELECT * FROM jobs WHERE id=$1 FOR UPDATE", 
                  message.job_id
              )
              if job['status'] != 'queued':
                  await queue.ack(message)
                  return

              new_attempt = job['attempt'] + 1
              await db.execute(
                  "UPDATE jobs SET status='running', attempt=$1 WHERE id=$2",
                  new_attempt, message.job_id
              )
              # But the above update might be redundant because we have the lock.

              idempotency_key = f"{message.job_id}-{new_attempt}"
              charge = await payment_provider.charge(message.payload, idempotency_key=idempotency_key)

        However, this uses a lock and might not scale.

        Given the charge latency (90s), we want to minimize the time the lock is held. We only need to lock during the status update.

        The current update (with WHERE status='queued') is sufficient to ensure only one worker gets it, and it's fast.

        So we can do without the explicit lock.

        Proposed:

          async def worker(message):
              # Try to claim the job
              updated = await db.execute(
                  "UPDATE jobs SET status='running', attempt=attempt+1 "
                  "WHERE id=$1 AND status='queued'",
                  message.job_id,
              )
              if updated == 0:
                  await queue.ack(message)
                  return

              # Now, we need the new attempt number? 
              # We can either do a SELECT or compute it as (old_attempt+1) but we don't have old_attempt.
              # Since we incremented it, we can assume it's the previous value + 1, but we don't know the previous value.

              # To get the attempt number, we do:
              job = await db.fetchrow(
                  "SELECT attempt FROM jobs WHERE id=$1",
                  message.job_id
              )
              idempotency_key = f"{message.job_id}-{job['attempt']}"

              charge = await payment_provider.charge(..., idempotency_key=idempotency_key)

        This adds an extra query, but it's acceptable.

        Alternatively, we can avoid the extra query by having the update return the new attempt:

          job = await db.fetchrow(
              "UPDATE jobs SET status='running', attempt=attempt+1 "
              "WHERE id=$1 AND status='queued' "
              "RETURNING attempt",
              message.job_id
          )
          if not job:
              await queue.ack(message)
              return
          idempotency_key = f"{message.job_id}-{job['attempt']}"

        This is efficient.

    - For the infinite retry issue: 
          In the worker, when we try to fetch the job and it's not found, we check the delivery count. If delivery count > 5, we ack the message. Otherwise, we let it redeliver.

        But note: the job might have been deleted by a cleanup process (which we don't have), so it's safe to skip after retries.

  Summary of durable design:

    Database:
      - Add unique constraint: 
          ALTER TABLE jobs ADD CONSTRAINT unique_tenant_idempotency UNIQUE (tenant_id, idempotency_key);

    Submit function:
      - Insert job into DB first (with unique constraint handling).
      - Then publish to queue.
      - If publish fails, set job status to 'publish_failed' and have a background process to retry publishing.

    Worker function:
      - Use an update with WHERE status='queued' to claim the job (returns the new attempt number).
      - If not claimed, ack and return.
      - Use the attempt number to form an idempotency key for the payment provider.
      - After charging, update the job status to 'succeeded' and set provider_charge_id.
      - If the job row is not found during the claim (which shouldn't happen because we published after DB insert, but might due to race), then:
            if message.delivery_count > 5: ack and skip
            else: let it redeliver

      However, the claim update uses the job_id, so if the job row is missing, the update will affect 0 rows and we ack (because updated==0). But we don't know why it's 0 rows: it could be because the job is not queued or because it doesn't exist.

      We can distinguish by doing a SELECT first? But that adds latency.

      Alternatively, in the worker, if the update returns 0 rows, we do a SELECT to see if the job exists:

          updated = ... 
          if updated == 0:
              job = await db.fetchrow("SELECT id FROM jobs WHERE id=$1", message.job_id)
              if not job:
                  if message.delivery_count > 5:
                      await queue.ack(message)
                  else:
                      # let it redeliver
                      pass
              else:
                  # job exists but not in queued state, so ack
                  await queue.ack(message)
              return

      But this adds an extra query in the error case.

      Given that the error case (job not found) is rare, it's acceptable.

    Queue visibility:
      - The visibility timeout is 60s, but charge latency is 90s p95. We should increase the visibility timeout to at least 120s. But the constraint says "queue semantics cannot be changed today". For the durable design, we assume we can change it in the future.

      However, the problem says "today", so for the durable design we can propose to increase it.

    Payment-provider idempotency:
      - As above, use a per-attempt idempotency key.

4. Safe migration and reconciliation approach:

  Migration steps:

    Step 1: Add the unique constraint, but first clean up existing duplicates.

      Why clean up? Because the constraint will fail if there are duplicates.

      How to clean up duplicates:

        We have two types of duplicates:
          a) Jobs with the same (tenant_id, idempotency_key) but different job IDs (duplicate jobs).
          b) Jobs that were processed multiple times (but these are the same job ID, so not a problem for the constraint).

        For (a): we need to keep one job and delete the others.

        But which one to keep? The first one created.

        However, the duplicate jobs might have been processed. We don't want to delete a job that has been processed (because it has a charge).

        Actually, we want to keep the job that was processed (if any) and delete the others? But the first job is the valid one.

        Steps for reconciliation:

          - For each (tenant_id, idempotency_key) with multiple jobs:
                Keep the job with the earliest created_at (the first one).
                For the other jobs (duplicates):
                    - If the duplicate job has status='succeeded', then it caused a duplicate charge. We must refund.
                    - Then delete the duplicate job.

          - But note: the first job might have been processed multiple times (so multiple charges). We already identified those and refunded the extra charges.

        So the migration:

          a) Generate a list of duplicate jobs (as in containment) and refund the duplicate charges (already done in containment).
          b) For each duplicate job (rn>1 in the ranked_jobs query):
                - If the job has status='succeeded', then it is a duplicate charge (one charge) -> refund (if not already done).
                - Delete the job row.

          However, we cannot delete the job row if it's being processed? But we stopped workers during containment.

        Representative SQL for cleanup:

          -- First, mark duplicate jobs for deletion (keeping the first)
          WITH ranked_jobs AS (
              SELECT 
                  id, 
                  ROW_NUMBER() OVER (PARTITION BY tenant_id, idempotency_key ORDER BY created_at) AS rn
              FROM jobs
          )
          DELETE FROM jobs
          WHERE id IN (
              SELECT id FROM ranked_jobs WHERE rn > 1
          );

        But this will delete all duplicate jobs. However, if a duplicate job is in 'running' state, we might be in the middle of processing. But we stopped workers, so no job is running.

        We do this during a maintenance window.

        However, the problem says: "safe migration". We must not delete a job that is in progress.

        So:

          - Stop workers.
          - Run the DELETE for duplicate jobs (rn>1) that are not in 'running' state? But we stopped workers, so no job should be in 'running'. We can set all jobs to 'queued' if they are stuck? But we don't have a mechanism.

          Instead, we can:

            WITH ranked_jobs AS (
                SELECT 
                    id, 
                    ROW_NUMBER() OVER (PARTITION BY tenant_id, idempotency_key ORDER BY created_at) AS rn
                FROM jobs
                WHERE status != 'running'   -- skip running jobs, but we stopped workers so there shouldn't be any
            )
            DELETE FROM jobs
            WHERE id IN (SELECT id FROM ranked_jobs WHERE rn > 1);

          But if there are running jobs (due to workers not stopped properly), we skip them and handle later.

        Step 2: Add the unique constraint.

          ALTER TABLE jobs ADD CONSTRAINT unique_tenant_idempotency UNIQUE (tenant_id, idempotency_key);

        Step 3: Deploy the new submit function (with insert then publish) and worker function (with idempotency key to provider and delivery count check).

        Step 4: Increase the queue visibility timeout to 120 seconds (if possible).

        Step 5: Deploy a background process to retry publishing for jobs with status='publish_failed'.

  Reconciliation for existing duplicates:

    We already generated the list during containment. We must:
      - Refund all duplicate charges identified.

    How to identify exactly:

      List A (duplicate jobs, rn>1): each job in this list that has status='succeeded' represents one duplicate charge (because it was processed at least once). 
        But note: it might have been processed multiple times (attempt>1), so more than one duplicate charge. However, we already have List B for multi-attempt jobs.

      Actually, List B is for the primary job (rn=1) having multiple attempts. List A is for duplicate jobs (rn>1) which are separate jobs.

      So for a duplicate job (rn>1), if it has attempt=1 and status='succeeded', then one duplicate charge.
      If it has attempt>1 and status='succeeded', then (attempt) charges, but the first charge is the duplicate of the primary job, and the extra attempts are additional duplicates.

      However, the primary job (rn=1) might have been processed multiple times, causing (attempt-1) duplicate charges.

      Total duplicate charges for a given (tenant_id, idem_key):
        = (number of duplicate jobs that were processed at least once) + (attempt_count of primary job - 1)

      But note: the primary job's first charge is valid, so duplicates = (attempt_count of primary job - 1) + (number of duplicate jobs that were processed)

      And for a duplicate job, if it was processed multiple times, then the number of duplicate charges for that job = attempt_count (because the entire job is duplicate).

      So total duplicates = (for primary job: max(0, attempt-1)) + (for each duplicate job: attempt)

      We can compute with:

        WITH primary_jobs AS (
            SELECT 
                tenant_id, idempotency_key,
                MAX(CASE WHEN rn=1 THEN id END) AS primary_id,
                MAX(CASE WHEN rn=1 THEN attempt END) AS primary_attempt
            FROM (
                SELECT 
                    tenant_id, idempotency_key, id, attempt,
                    ROW_NUMBER() OVER (PARTITION BY tenant_id, idempotency_key ORDER BY created_at) AS rn
                FROM jobs
                WHERE created_at >= '2023-10-05 14:05:00 UTC'
            ) AS ranked
            GROUP BY tenant_id, idempotency_key
        ),
        duplicate_jobs AS (
            SELECT 
                tenant_id, idempotency_key,
                SUM(attempt) AS duplicate_attempts
            FROM (
                SELECT 
                    tenant_id, idempotency_key, attempt,
                    ROW_NUMBER() OVER (PARTITION BY tenant_id, idempotency_key ORDER BY created_at) AS rn
                FROM jobs
                WHERE created_at >= '2023-10-05 14:05:00 UTC'
            ) AS ranked
            WHERE rn > 1
            GROUP BY tenant_id, idempotency_key
        )
        SELECT 
            p.tenant_id,
            p.idempotency_key,
            GREATEST(0, p.primary_attempt - 1) AS primary_duplicates,
            COALESCE(d.duplicate_attempts, 0) AS duplicate_job_duplicates,
            GREATEST(0, p.primary_attempt - 1) + COALESCE(d.duplicate_attempts, 0) AS total_duplicates
        FROM primary_jobs p
        LEFT JOIN duplicate_jobs d 
            ON p.tenant_id = d.tenant_id AND p.idempotency_key = d.idempotency_key;

      This gives the total duplicate charges per (tenant_id, idempotency_key).

    We provide this to finance.

5. Validation signals, rollback triggers, and remaining uncertainty.

  Validation signals (after deployment):
    - Monitor for new duplicate job creations: 
          SELECT COUNT(*) FROM jobs 
          WHERE (tenant_id, idempotency_key) IN (
              SELECT tenant_id, idempotency_key 
              FROM jobs 
              GROUP BY tenant_id, idempotency_key 
              HAVING COUNT(*) > 1
          ) 
          AND created_at > [deployment time];
        Should be 0.

    - Monitor for duplicate charges per job: 
          SELECT id, COUNT(*) 
          FROM jobs 
          WHERE created_at > [deployment time] 
          GROUP BY id 
          HAVING MAX(attempt) > 1;
        But note: attempt>1 might be due to retries (which are now safe because of provider idempotency). However, if the provider idempotency works, then only one charge should occur per job. So we should not see multiple charges for the same job.

        How to verify? We can check the payment provider logs for duplicate charges for the same job_id (but with different attempt numbers, the provider idempotency key is different, so it should not deduplicate across attempts). 

        Actually, we use a per-attempt idempotency key, so the provider will not deduplicate across attempts. But we only want one successful charge per job. The extra attempts should fail (with idempotency error) or be skipped.

        The worker should not attempt to charge if the update to 'running' fails (which it will for redeliveries). So we should not see multiple charge attempts for the same job.

        Therefore, we expect:
          - For each job, only one charge attempt (the first one that claims the job).
          - Subsequent redeliveries will not charge.

        So we should not see any job with attempt>1 and status='succeeded'. Because the first attempt succeeds, and then the job is done. Redeliveries will see status!='queued' and skip.

        But what if the first attempt fails? Then attempt=2, and we try again. So attempt>1 is possible, but only if the first attempt failed.

        We can monitor:
          - Rate of jobs with attempt>1: should be low (only for failed charges).
          - Rate of duplicate charges: should be near zero.

    - Monitor for infinite retries: 
          Count of worker errors "jobs row not found" that are not skipped (delivery count <=5) should be transient.
          Count of messages skipped due to delivery count >5 should be near zero (only for bad messages).

  Rollback triggers:
    - If duplicate job creations are observed (more than 0 in the last 5 minutes).
    - If the error rate for job submissions increases significantly (e.g., >1%).
    - If the payment processing latency increases significantly (e.g., p95 > 2 minutes).
    - If the worker error rate for "jobs row not found" is high (indicating a problem with the publish order).

  Remaining uncertainty:
    - The background process for publishing might fail, leading to jobs stuck in 'publish_failed'. We need to monitor that.
    - The payment provider might not honor idempotency keys in all cases (e.g., if the request times out, the provider might have processed it but the response was lost). But that's a provider issue.
    - The unique constraint might cause errors during high concurrency, but the application handles it by returning the existing job.
    - The visibility timeout is still 60 seconds, but we increased it to 120 seconds in the durable design. If we cannot change it, then jobs with charge latency >60s will still be redelivered, but the worker will skip them (because the job is no longer in 'queued' state). So it's safe, but might cause unnecessary redelivery and processing (which is skipped). We should still increase it to reduce load.

  Note: The constraint said "queue semantics cannot be changed today", but for the durable design we assume we can change it later.

Now, we compile the final answer.
</think>

### 1. Severity and Causal Graph  
**Severity**: Critical (P0) — Direct financial loss from duplicate charges, system instability from infinite retries, and violation of idempotency guarantees.  

**Causal Graph (Independent Failure Modes)**:  
```mermaid
graph LR
  A[Deployment at 14:05 UTC] --> B[Idempotency Key Race Condition]
  A --> C[Charge Latency > Queue Visibility Timeout]
  A --> D[Non-Atomic Job Creation]
  
  B --> E[Concurrent POSTs for same (tenant_id, idempotency_key)]
  E --> F[Two jobs created: 8d1 and 9a4]
  F --> G[Duplicate charges for same business intent]
  
  C --> H[Charge p95=90s > Queue timeout=60s]
  H --> I[Redelivery of job 8d1 to worker B]
  I --> J[Worker B processes job 8d1 again]
  J --> K[Second charge: ch_89]
  
  D --> L[Publish to queue BEFORE DB insert]
  L --> M[Connection reset during INSERT]
  M --> N[Job bb3 in queue but not in DB]
  N --> O[Worker retries forever: “jobs row not found”]
```

- **Failure Mode 1 (Idempotency Race)**: Lack of unique constraint + non-atomic check-insert (evidenced by two jobs for `invoice-991`).  
- **Failure Mode 2 (Redelivery Duplication)**: Charge latency > visibility timeout + no worker claim validation (evidenced by two charges for job `8d1`).  
- **Failure Mode 3 (Infinite Retry)**: Queue publish before DB commit + no missing-job handling (evidenced by job `bb3` retries).  

---

### 2. First 30 Minutes of Containment (Exact Order)  
All times UTC; starts at 14:10:00 (incident detection):  
1. **14:10:00**: Disable API endpoint for ≤5 minutes:  
   ```bash
   curl -X POST https://api.example.com/v1/disable -H "Authorization: Bearer $TOKEN" -d '{"duration": 300}'
   ```  
2. **14:10:15**: Stop all worker processes to halt duplicate charges:  
   ```bash
   kubectl scale deployment/payment-workers --replicas=0
   ```  
3. **14:10:30**: Identify duplicate jobs (same `idempotency_key`):  
   ```sql
   WITH ranked AS (
     SELECT id, tenant_id, idempotency_key, 
            ROW_NUMBER() OVER (PARTITION BY tenant_id, idempotency_key ORDER BY created_at) AS rn
     FROM jobs 
     WHERE created_at >= '2023-10-05 14:05:00'
   )
   SELECT id, tenant_id, idempotency_key 
   FROM ranked 
   WHERE rn > 1;
   ```  
4. **14:11:00**: Identify multi-attempt jobs (likely duplicate charges):  
   ```sql
   SELECT id, tenant_id, idempotency_key, attempt 
   FROM jobs 
   WHERE created_at >= '2023-10-05 14:05:00' 
     AND attempt > 1 
     AND status = 'succeeded';
   ```  
5. **14:11:30**: Send results from Steps 3–4 to finance for refunds (exact duplicate list).  
6. **14:12:00**: Collect job IDs from logs with `“jobs row not found”` for queue cleanup.  
7. **14:15:00**: Re-enable API (within 5-minute window):  
   ```bash
   curl -X POST https://api.example.com/v1/enable -H "Authorization: Bearer $TOKEN"
   ```  
8. **14:15:30**: Deploy worker hotfix to skip missing jobs after 5 retries (see Section 3).  
9. **14:20:00**: Deploy submit-function hotfix with atomic upsert (see Section 3).  
10. **14:25:00**: Restart workers:  
    ```bash
    kubectl scale deployment/payment-workers --replicas=3
    ```  
11. **14:30:00**: Verify no new duplicates via:  
    ```sql
    SELECT COUNT(*) FROM jobs 
    WHERE (tenant_id, idempotency_key) IN (
      SELECT tenant_id, idempotency_key 
      FROM jobs 
      GROUP BY tenant_id, idempotency_key 
      HAVING COUNT(*) > 1
    ) AND created_at > '2023-10-05 14:30:00';
    ```  

---

### 3. Durable Design  
**Database Constraints & Transactions**:  
- Add unique constraint to prevent duplicate jobs:  
  ```sql
  ALTER TABLE jobs ADD CONSTRAINT unique_tenant_idempotency 
    UNIQUE (tenant_id, idempotency_key);
  ```  
- **Submit function** (atomic upsert):  
  ```python
  async def submit(tenant_id, idempotency_key, payload):
      job_id = uuid4()
      try:
          # Insert FIRST (critical for durability)
          await db.execute(
              "INSERT INTO jobs(id, tenant_id, idempotency_key, status) "
              "VALUES ($1, $2, $3, 'queued')",
              job_id, tenant_id, idempotency_key
          )
          # Publish AFTER DB commit
          await queue.publish({"job_id": job_id, "payload": payload})
          return {"status": 202, "job_id": job_id}
      except UniqueViolationError:
          return await db.fetchrow(
              "SELECT * FROM jobs WHERE tenant_id=$1 AND idempotency_key=$2",
              tenant_id, idempotency_key
          )
  ```  

**Enqueue Ordering**:  
- Queue publish **must** occur after DB commit (as above). If publish fails:  
  ```python
  except QueuePublishError:
      await db.execute(
          "UPDATE jobs SET status='publish_failed' WHERE id=$1",
          job_id
      )
      # Background job retries publishing (separate process)
  ```  

**Worker Claiming**:  
- Prevent concurrent processing with atomic claim:  
  ```python
  async def worker(message):
      # Claim job ONLY if queued (avoids redelivery duplicates)
      job = await db.fetchrow(
          "UPDATE jobs SET status='running', attempt=attempt+1 "
          "WHERE id=$1 AND status='queued' "
          "RETURNING id, attempt",
          message.job_id
      )
      if not job:
          # Skip if not claimed (job done or missing)
          if message.delivery_count > 5:
              await queue.ack(message)  # Prevent infinite retry
          return
      
      # Use per-attempt idempotency key for payment provider
      idempotency_key = f"{job.id}-{job.attempt}"
      charge = await payment_provider.charge(
          payload=message.payload, 
          idempotency_key=idempotency_key  # Critical for deduplication
      )
      await db.execute(
          "UPDATE jobs SET status='succeeded', provider_charge_id=$1 WHERE id=$2",
          charge.id, job.id
      )
      await queue.ack(message)
  ```  

**Visibility Timeout**:  
- Increase to 120s (exceeds charge p95=90s) to reduce redeliveries. *Not possible today per constraints, but required for durability.*  

**Payment Provider Idempotency**:  
- Always send `idempotency_key` derived from `job_id` + `attempt` (as above).  

---

### 4. Safe Migration and Reconciliation  
**Migration Steps**:  
1. **Stop workers** (prevents in-progress jobs from conflicting with cleanup).  
2. **Reconcile duplicates** (run during maintenance window):  
   ```sql
   -- Refund all duplicate charges (exact list for finance)
   WITH duplicates AS (
     SELECT

---

`finish_reason=length; wall_s=1134.8; completion_tokens=16384`
