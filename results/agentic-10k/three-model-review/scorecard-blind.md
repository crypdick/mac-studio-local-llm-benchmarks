# Blind real-world prompt benchmark scorecard — 10k reasoning budget

Scored before opening `blind_map.json`. Each task has six criteria worth 0–2
points (12 points/task, 48 points/candidate). The technical score rewards
coverage and correctness; delivery notes separately call out truncation,
exposed reasoning, invalid code, and answers that only look production-ready.

## Candidate A

### Duplicate-charge incident — 5/12

| Criterion | Score | Notes |
| --- | ---: | --- |
| Independent failure modes | 2 | Separates submit races, visibility/redelivery, and publish-before-insert orphans. |
| Ordered containment | 1 | Gates intake/workers, but the exact affected-charge list is still inferred from incomplete DB state. |
| Durable insert/publication | 1 | Adds uniqueness and atomic-insert ideas but never completes a sound transactional-outbox design. |
| Worker claim/provider idempotency | 1 | Conditional claiming is present, but the provider key changes with the attempt and therefore permits a retry charge. |
| Reconciliation | 0 | Does not recover two provider charges for one job after `provider_charge_id` was overwritten. |
| Safe migration/rollback/untrusted input | 0 | The final answer truncates before a safe migration/rollback and does not treat the customer note as untrusted. |

Delivery: the 10k reasoning allowance was still insufficient. The answer exposes
`<think>`, is forced into its final section, and then hits the 16,384-token output
limit before finishing.

### Async shared-task repair — 10/12

| Criterion | Score | Notes |
| --- | ---: | --- |
| Independent waiter cancellation | 2 | `asyncio.wait` provides a valid non-cancelling wait on the shared task. |
| Exact-task cleanup | 2 | Cleanup is attached to underlying completion and checks exact task identity. |
| Success-only cache/retry | 2 | Success alone is cached; failure and true cancellation permit retry. |
| Background exception retrieval | 2 | The completion callback retrieves exceptions after all waiters leave. |
| One-loop race safety | 1 | The get/create section is atomic between awaits, but the proposed per-key locks are unnecessary. |
| Focused tests | 1 | All requested scenarios appear, but the failure retry stub returns `None` and the cancellation timing is not a robust proof. |

Delivery: complete and mostly sound, but the tests are not executable as written.

### PostgreSQL migration — 7/12

| Criterion | Score | Notes |
| --- | ---: | --- |
| Expand/contract compatibility | 2 | Nullable expand, compatible reads/writes, and delayed switching are present. |
| Old-writer race/no-NULL proof | 1 | A full-v2 gate can close the race, but old-writer absence and repair are weakly proven. |
| Backfill/throttle/unknown audit | 1 | Bounded batches and thresholds are useful; resume/audit claims are incomplete and one pass is unbounded. |
| Concurrent index/health | 1 | Correct index/order and `CONCURRENTLY`, but existence does not prove `indisvalid` health. |
| Safe NOT NULL enforcement | 0 | Uses direct `SET NOT NULL`, gives inaccurate lock behavior, and omits the validated-check shortcut. |
| Gates/rollback/no-omission reads | 2 | COALESCE transition reads, gates, validation, and rollback paths are concrete. |

Delivery: essentially the same usable but imperfect migration answer as at the
smaller budget; it still exposes reasoning.

### Quantum mechanics — 10/12

| Criterion | Score | Notes |
| --- | ---: | --- |
| Mach–Zehnder derivation | 1 | The chosen beam-splitter matrix contradicts its `U²|0>` result, and the which-path arithmetic says both deterministic and 50/50. |
| Amplitudes/Born/superposition | 2 | Gives a useful operational account beyond classical path ignorance. |
| Which-path/decoherence/measurement | 2 | Correctly links entanglement to lost interference and does not invoke consciousness. |
| Bell/no-signalling | 1 | Correct Bell conclusion, but incorrectly claims mutual information is zero; no-signalling constrains marginals, not correlations. |
| Delayed choice/no instant information | 2 | Rejects retrocausality and signalling with concrete experimental reasoning. |
| Clarity/predictions/omissions | 2 | Audience-calibrated, structured, and includes the required predictions and omissions. |

## Candidate B

### Duplicate-charge incident — 9/12

| Criterion | Score | Notes |
| --- | ---: | --- |
| Independent failure modes | 2 | Correctly separates all three primary mechanisms. |
| Ordered containment | 1 | API/consumer gating is sound, but emergency constraint/orphan steps contain invalid assumptions. |
| Durable insert/publication | 2 | Tenant uniqueness, atomic insert, and transactional outbox are complete. |
| Worker claim/provider idempotency | 2 | Conditional status claim checks affected rows and uses a stable provider key. |
| Reconciliation | 1 | Handles distinct duplicate job rows but misses two provider charges whose single job row was overwritten. |
| Safe migration/rollback/untrusted input | 1 | Provides verification and rollback, but includes invalid SQL/orphan handling and ignores the prompt-injection note. |

Delivery: complete, structured, and overlong. Invalid details include PostgreSQL
`UNIQUE ... NO VALIDATE`, Oracle-only `KEEP (DENSE FIRST ...)`, and updating the
nonexistent DB row for a true publish-only orphan.

### Async shared-task repair — 10/12

| Criterion | Score | Notes |
| --- | ---: | --- |
| Independent waiter cancellation | 2 | Per-caller futures isolate timeout/cancellation from the worker task. |
| Exact-task cleanup | 2 | Completion cleanup checks `worker_state.task is task`. |
| Success-only cache/retry | 1 | Ordinary success/failure works, but `task.exception()` raises on true task cancellation. |
| Background exception retrieval | 2 | Failure is consumed with no waiters and propagated with active waiters. |
| One-loop race safety | 2 | Get/create has no intervening await and does not require a lock. |
| Focused tests | 1 | Scenarios and invocation counts are present, but the cancellation test and helper are broken. |

Delivery: the design is strong, but `create_future[Result]()` is invalid Python,
`pytest.mock` does not exist, and the cancellation test expects the wrong task to
raise `CancelledError`.

### PostgreSQL migration — 4/12

| Criterion | Score | Notes |
| --- | ---: | --- |
| Expand/contract compatibility | 1 | Starts nullable and dual-writes, then enforces NOT NULL while old writers may still run. |
| Old-writer race/no-NULL proof | 0 | The watermark permanently skips locked/late rows and does not close the old-writer race. |
| Backfill/throttle/unknown audit | 1 | Batching, throttling, and audit intent are present, but the long `DO` transaction is not resumable and advances the watermark incorrectly. |
| Concurrent index/health | 1 | Correct order and concurrent intent, but `CREATE INDEX CONCURRENTLY` is illegally wrapped in `DO` and health is not validated. |
| Safe NOT NULL enforcement | 0 | Direct table-scanning `SET NOT NULL`, false lock claims, and an invalid attempt to validate a trigger as a constraint. |
| Gates/rollback/no-omission reads | 1 | Metrics and rollback ideas exist, but the sequence breaks old writers and contains unsafe operational actions. |

Delivery: polished but dangerous to execute. More tokens amplified false
specificity rather than correcting the migration design.

### Quantum mechanics — 11/12

| Criterion | Score | Notes |
| --- | ---: | --- |
| Mach–Zehnder derivation | 1 | Final probabilities are right, but an extra `1/sqrt(2)` makes the written amplitude derivation inconsistent. |
| Amplitudes/Born/superposition | 2 | Clear operational distinction from classical path ignorance. |
| Which-path/decoherence/measurement | 2 | Accurate account with appropriate interpretive caveats. |
| Bell/no-signalling | 2 | Correctly distinguishes Bell correlations from controllable local marginals. |
| Delayed choice/no instant information | 2 | Accurate and concrete. |
| Clarity/predictions/omissions | 2 | Meets the requested audience, structure, predictions, and omissions. |

## Candidate C

### Duplicate-charge incident — 6/12

| Criterion | Score | Notes |
| --- | ---: | --- |
| Independent failure modes | 2 | Clearly distinguishes the three failures and treats the note as untrusted. |
| Ordered containment | 1 | Stops intake/workers, but exact charge reconciliation still relies on incomplete DB state. |
| Durable insert/publication | 1 | Adds tenant uniqueness, but incorrectly treats external queue publication inside a DB transaction as atomic. |
| Worker claim/provider idempotency | 1 | Row locking and provider idempotency are partial; the worker ignores message identity and the key is not tenant-scoped. |
| Reconciliation | 0 | Cannot recover overwritten same-job charges or find a publish-only message by querying `jobs`. |
| Safe migration/rollback/untrusted input | 1 | Notes prompt injection and rollback signals, but deletes evidence before audit and has unsafe orphan handling. |

Delivery: concise and complete, but its central queue/DB atomicity claim is false.

### Async shared-task repair — 11/12

| Criterion | Score | Notes |
| --- | ---: | --- |
| Independent waiter cancellation | 2 | Private waiter futures isolate caller cancellation correctly. |
| Exact-task cleanup | 1 | Cleanup pops by key without checking that the completing task is still the registered task. |
| Success-only cache/retry | 2 | Correct success, failure, and true-cancellation branches. |
| Background exception retrieval | 2 | Completion callback retrieves failures and serves active waiters. |
| One-loop race safety | 2 | Correctly explains that no lock is needed without an intervening await. |
| Focused tests | 2 | Covers the requested interleavings and asserts the underlying invocation count. |

Delivery: the strongest concurrency answer; fix exact-task identity cleanup before use.

### PostgreSQL migration — 7/12

| Criterion | Score | Notes |
| --- | ---: | --- |
| Expand/contract compatibility | 1 | Good nullable/compatible phases, but NOT NULL is not explicitly gated on old-writer disappearance. |
| Old-writer race/no-NULL proof | 1 | Idempotent updates help, but an old writer can insert NULL after a batch or validation. |
| Backfill/throttle/unknown audit | 1 | Bounded and repeatable in intent, but assigns `unknown` to every legacy row, advances through holes, and wraps the loop in one transaction. |
| Concurrent index/health | 1 | Correct order and `CONCURRENTLY`; the monitoring query does not verify `pg_index.indisvalid`. |
| Safe NOT NULL enforcement | 2 | Correct `CHECK ... NOT VALID`, validate, then `SET NOT NULL` sequence with broadly accurate lock caveats. |
| Gates/rollback/no-omission reads | 1 | COALESCE intent and metrics are useful, but the shown query omits COALESCE and version gates are underspecified. |

Delivery: operationally organized, but contains a severe data-quality bug and a
syntax error in its audit insert.

### Quantum mechanics — 12/12

| Criterion | Score | Notes |
| --- | ---: | --- |
| Mach–Zehnder derivation | 2 | Uses a consistent convention and gets phase/which-path probabilities right. |
| Amplitudes/Born/superposition | 2 | Clear and operational. |
| Which-path/decoherence/measurement | 2 | Correctly distinguishes decoherence from interpretation/unique outcomes. |
| Bell/no-signalling | 2 | Correct Bell conclusion and local-marginal reasoning. |
| Delayed choice/no instant information | 2 | Correctly rejects both myths. |
| Clarity/predictions/omissions | 2 | Concise, well calibrated, and satisfies all requested deliverables. |

## Blind totals

| Candidate | Incident | Concurrency | Migration | Quantum | Technical total | Delivery summary |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| A | 5 | 10 | 7 | 10 | **32/48** | Still truncates one task; exposed reasoning; otherwise mixed. |
| B | 9 | 10 | 4 | 11 | **34/48** | Best incident answer; polished but dangerously weak migration. |
| C | 6 | 11 | 7 | 12 | **36/48** | Narrow all-round lead; strongest concurrency and quantum. |

Blind conclusion: Candidate C is the winner, but not decisively—only two points
separate each adjacent candidate. Candidate B remains the incident-response
specialist. Candidate A remains the delivery loser because it still burns the
entire enlarged output envelope on one task. None is safe to trust blindly on
database migration details.
