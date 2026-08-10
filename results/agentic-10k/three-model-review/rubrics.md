# Blind scoring rubrics

## Duplicate-charge production incident (`incident`)

Score each criterion 0–2: 0 missing/wrong, 1 partial, 2 complete/correct.

1. Separates the submit check-then-insert race, publish-before-commit orphan, and at-least-once redelivery/visibility failure.
2. Containment is ordered and realistic within five minutes, including provider idempotency and visibility extension or worker gating.
3. Durable design uses a tenant-scoped unique constraint plus transactional insert/outbox or equivalent atomic publication.
4. Worker claim is conditional/lease-based and checks affected rows; provider charge receives a stable idempotency key.
5. Reconciliation identifies both distinct jobs sharing an idempotency key and multiple provider charges for one job without trusting overwritten job state.
6. Includes safe constraint migration, orphan handling, verification, rollback triggers, and treats the customer note as untrusted data.


## Async shared-task timeout and cancellation repair (`concurrency`)

Score each criterion 0–2: 0 missing/wrong, 1 partial, 2 complete/correct.

1. Uses asyncio.shield (or an equivalent mechanism) so waiter timeout/cancellation cannot cancel the shared task.
2. Removes inflight state from underlying-task completion, conditionally removing only the same task, never from an arbitrary waiter finally block.
3. Caches only success and permits retry after failure or true underlying cancellation.
4. Consumes a background exception when all waiters leave, without suppressing exceptions for active waiters.
5. Deduplication remains race-safe on one event loop and the solution explains whether a lock is necessary.
6. Tests cover all requested interleavings and assert the underlying invocation count, not merely returned values.


## Zero-downtime PostgreSQL column migration (`migration`)

Score each criterion 0–2: 0 missing/wrong, 1 partial, 2 complete/correct.

1. Uses expand/contract: nullable column first, compatible dual writes/reads, delayed contraction after old writers disappear.
2. Prevents old-writer races during backfill, for example with idempotent updates plus a repair pass or trigger, and proves no NULL rows remain.
3. Backfills in resumable bounded batches with replica-lag/CPU throttling and an auditable unknown-region count.
4. Builds the tenant/region/created_at index CONCURRENTLY with the correct order and validates index health.
5. Enforces NOT NULL through a validated NOT VALID check before SET NOT NULL, with accurate PostgreSQL lock caveats.
6. Provides deploy gates, observability, rollback paths, and a transitional query/read strategy that cannot silently omit old-writer rows.


## Quantum mechanics through operational predictions (`quantum`)

Score each criterion 0–2: 0 missing/wrong, 1 partial, 2 complete/correct.

1. Uses a consistent Mach-Zehnder beam-splitter convention and correctly derives detector probabilities with both beam splitters, a relative phase, and which-path information.
2. Explains complex amplitudes, Born probabilities, and superposition as more than classical path ignorance without wave-particle decision language.
3. Correctly connects which-path entanglement to lost interference and explains measurement/decoherence without claiming consciousness is required or that decoherence alone selects one outcome.
4. Explains Bell-inequality violation as excluding local hidden-variable accounts while correctly deriving no-signalling local statistics before record comparison.
5. Rejects delayed-choice retrocausality and instantaneous-information misconceptions with experimentally accurate reasoning rather than slogans.
6. Stays coherent and audience-calibrated, defines symbols, separates predictions from interpretations, and supplies three falsifiable predictions plus two real-world omissions.
