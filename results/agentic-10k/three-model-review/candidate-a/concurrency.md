# Async shared-task timeout and cancellation repair

<think>
We are given a class `ToolRunner` that has issues with cancellation and duplicate work.
 The problem: 
   - A short-timeout caller cancels work for every caller (because when the task is cancelled by one waiter, it cancels the shared task).
   - New callers sometimes start duplicate work while the original call continues (because when the first caller times out, it removes the inflight task, and then a new caller sees no inflight task and starts a new one, even though the original task is still running).

 Required semantics:
   - Concurrent callers for one key share exactly one underlying `_run_once` task.
   - Each caller has an independent timeout or cancellation; one caller must not cancel the shared task.
   - Only successful results are cached.
   - Failed or genuinely cancelled underlying tasks are not cached and can be retried.
   - The inflight entry is removed only when that exact underlying task finishes.
   - If every waiter times out and the background task later fails, its exception is retrieved rather than reported as "Task exception was never retrieved".

 The current implementation has two main problems:
   1. When a caller times out (or cancels), it cancels the shared task (via `asyncio.wait_for` which cancels the task on timeout). But we don't want one caller to cancel the shared task because other callers might still be waiting.
   2. The `finally` block removes the inflight task as soon as one caller times out, which then allows a new caller to start a duplicate task even though the original task is still running.

 How to fix:
   - We must not let a single caller's timeout cancel the shared task. Instead, we want the shared task to run to completion regardless of individual callers timing out.
   - We need to keep the inflight task in the `_inflight` dictionary until the task actually completes (whether successfully or with an exception). However, we also want to avoid leaking the task if all callers time out and the task fails (to avoid the "Task exception was never retrieved" warning).

 Approach:
   We can change the design so that:
     - The `_inflight` dictionary holds a task that is never cancelled by the callers. Instead, we use a separate mechanism to wait for the task with a timeout without cancelling the task.
     - We can use `asyncio.shield` to protect the task from cancellation by the waiters? But note: the requirement says "one caller must not cancel the shared task". However, if a caller cancels its own wait, we don't want that to cancel the shared task.

   However, note: the problem is that `asyncio.wait_for` cancels the task when the timeout occurs. We don't want that.

   Alternative: Instead of having the callers wait directly on the shared task, we can have the shared task run independently and then have the callers wait on a future that is set by the shared task. But note: we want to avoid duplicate work, so we still need to share the same underlying task.

   Proposed design:
     - We will have the `_inflight` dictionary hold a task that is the actual `_run_once` task (which we do not want to cancel from the outside).
     - However, when a caller waits, we do not want to cancel the shared task if the caller times out. So we cannot use `asyncio.wait_for` on the shared task.

   Instead, we can do:
     - For each key, we have a single task (the `_run_once` task) that we start if not already present.
     - But we also want to allow callers to time out without affecting the task.

   How about: 
        We create a task for the `_run_once` and store it in `_inflight`.
        Then, for each caller, we create a shielded wait? But note: we don't want to create multiple tasks per key.

   Actually, we can have the callers wait on the same task but without cancelling it. We can use:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
        except asyncio.TimeoutError:
            ... 

   However, note: `asyncio.shield` prevents cancellation of the task, but if the task is already running and we shield it, then when we cancel the wait (due to timeout) the task continues. But the problem is that `asyncio.wait_for` when it times out, it cancels the shielded task? Actually, no: `asyncio.shield` returns a task that is shielded, meaning that if you cancel the shielded task, it cancels the inner task only after the inner task completes? Actually, the documentation says: 
        "The shielded task is not cancellable. If the inner task is cancelled, the shielded task is still pending until the inner task is cancelled."

   But note: we are not cancelling the inner task from the outside. We want the inner task to run to completion regardless.

   However, the issue is that when we do `await asyncio.wait_for(task, timeout)`, if the timeout occurs, `wait_for` cancels the task. We don't want that. So we can shield the task from cancellation by the `wait_for`? 

   Actually, we can do:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)

   But note: `asyncio.shield` returns a task that is shielded. However, if the task is cancelled from the outside (which we don't want to happen) then the shielded task would still be cancelled? But in our case, we don't want the task to be cancelled at all by the waiters.

   However, the problem is that the `wait_for` will try to cancel the shielded task when the timeout occurs. But the shielded task is designed to prevent cancellation until the inner task is done. So when `wait_for` tries to cancel the shielded task, it will not actually cancel the inner task, but the shielded task will be cancelled (and then the inner task continues). But then the shielded task will raise `asyncio.CancelledError`? 

   Actually, the behavior of `asyncio.shield` is:
        - If the inner task is cancelled, the shielded task will be cancelled too, but only after the inner task is done? 
        - But we don't want the inner task to be cancelled at all.

   However, in our scenario, we are not cancelling the inner task from the outside. The only cancellation comes from `wait_for` when it times out. And `wait_for` will try to cancel the task we passed to it (which is the shielded task). The shielded task will then try to cancel the inner task? But the shielded task is designed to prevent cancellation of the inner task until it completes. So the inner task will continue running.

   But note: the shielded task, when cancelled, will wait for the inner task to complete and then raise `CancelledError`. However, we don't want the caller to see a `CancelledError` for the task that is still running? Actually, the caller that times out should get a `TimeoutError`, not a `CancelledError`. 

   How does `wait_for` work?
        It creates a task for the inner awaitable and then waits for either the task or a timeout.
        If the timeout occurs, it cancels the task and then raises `TimeoutError`.

   Now, if we pass a shielded task to `wait_for`:
        - The shielded task is a task that wraps the inner task and prevents cancellation of the inner task.
        - When `wait_for` tries to cancel the shielded task, the shielded task will not cancel the inner task, but it will mark itself as cancelled (and then when the inner task completes, the shielded task will be set to the result of the inner task, but it was already cancelled?).

   Actually, the behavior is a bit complex. There is a known issue: 
        https://bugs.python.org/issue42518

   Alternatively, we can avoid using `wait_for` on the task and instead use a timeout without cancellation.

   We can do:
        done, pending = await asyncio.wait([task], timeout=timeout_s)
        if not done:
            # timed out
            raise asyncio.TimeoutError
        else:
            result = task.result()

   But note: this does not cancel the task. However, the problem is that if we do this, then the task continues to run even if the caller times out, which is what we want.

   However, there is a catch: if the task fails, then `task.result()` will raise the exception. But we want to cache only successful results.

   Steps for the fixed `run` method:

     1. Check cache -> return if present.
     2. Check if there's an inflight task for the key. If not, create one and store it in `_inflight`.
     3. Now, we want to wait for the task with a timeout, but without cancelling the task.

        We can do:
            try:
                await asyncio.wait_for(task, timeout=timeout_s)
            except asyncio.TimeoutError:
                # But note: we don't want to cancel the task, so we don't use wait_for in a way that cancels.
                # Instead, we use a different method.

        Alternatively, we can use:
            try:
                result = await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
            except asyncio.TimeoutError:
                ... 

        However, as discussed, `asyncio.shield` might not be the cleanest.

   Another idea: use a future that is set by the task when it completes, and then wait on that future with a timeout. But note: we are already using a task, which is a future.

   Actually, the problem with `asyncio.wait_for` is that it cancels the task on timeout. We don't want that. So we can avoid `wait_for` and do:

        try:
            result = await asyncio.wait_for(task, timeout=timeout_s)
        except asyncio.TimeoutError:
            ... 

        This cancels the task. We don't want that.

   Instead, we can do:

        done, pending = await asyncio.wait([task], timeout=timeout_s)
        if task in done:
            result = task.result()
        else:
            raise asyncio.TimeoutError

   This does not cancel the task. However, note that `asyncio.wait` does not cancel the task. So the task continues to run.

   But what if the task fails? Then `task.result()` will raise the exception. We want to handle that.

   Steps:

        if task in done:
            try:
                result = task.result()
            except Exception as e:
                # We don't cache failures, so we remove the inflight task? But note: the task has completed (with failure), so we should remove it from inflight? 
                # However, the requirement says: "The inflight entry is removed only when that exact underlying task finishes." -> so we remove it when the task finishes (whether success or failure).
                # But note: we are in the middle of a caller that is waiting. We want to let the caller see the exception, and then we remove the inflight task? 
                # However, the inflight task is stored in `_inflight` until the task finishes. But the task has finished (with exception). So we should remove it from `_inflight`? 
                # But wait: the requirement says "The inflight entry is removed only when that exact underlying task finishes." -> meaning we remove it when the task finishes. However, we are in the context of a caller that is seeing the exception. We cannot remove it until all callers that are waiting for this task have been notified? Actually, no: the task has finished, so we can remove it immediately after the task finishes? But note: multiple callers might be waiting on the same task. We want to remove the inflight entry only after the task finishes, but we don't want to remove it until we are sure that no new callers will try to use it? Actually, once the task finishes, we don't need the inflight entry because:
                    - If the task succeeded, we cache the result and then future callers will use the cache.
                    - If the task failed, we don't cache, so future callers will try to start a new task.

                However, we must remove the inflight entry when the task finishes so that a new task can be started for the same key (if the previous one failed).

                But note: the task has finished, so we can remove it from `_inflight` immediately after it finishes? However, we are in the middle of a caller that is processing the result. We cannot remove it until we are done with the task? Actually, we can remove it as soon as the task finishes, but we have to do it in a way that is safe for multiple callers.

                How about: we remove the inflight entry when the task finishes, regardless of success or failure. But we cannot do that in the middle of a caller because:
                    - If the task fails, we want to let the caller see the exception, and then we remove the inflight entry? But what if two callers are waiting? The first caller sees the exception and removes the inflight entry, then the second caller tries to get the result and the inflight entry is gone? But the task is still in the `_inflight` dictionary until we remove it. However, the task has finished, so it's safe to remove it.

                Actually, we can remove the inflight entry as soon as the task finishes. We can do that by adding a callback to the task.

                Proposed:
                    When we create the task, we add a done callback that removes the task from `_inflight` and also, if the task succeeded, caches the result.

                But note: the requirement says "Only successful results are cached". So we cache only on success.

                However, the current code caches only on success (in the `run` method). But if we move the caching to a done callback, then we have to be careful: the done callback runs in the event loop, and we want to cache the result only if it's successful.

                Also, note: the requirement says "Failed or genuinely cancelled underlying tasks are not cached". So we don't cache on failure.

                But what about cancellation of the underlying task? The requirement says "genuinely cancelled" meaning if the task was cancelled (by whom? we don't want callers to cancel it, but what if the task itself gets cancelled for some other reason? Actually, we are not cancelling the task from the outside, but the task might be cancelled if the event loop is shutting down? We don't have to worry about that in normal operation). However, the requirement says: if the task is cancelled (for a reason other than the caller's timeout) then we don't cache.

                How do we handle cancellation of the underlying task? 
                    - We don't want callers to cancel the task, but if the task is cancelled (for example, because the event loop is closing) then we treat it as a failure.

                So plan:

                    We will create the task and store it in `_inflight`.
                    We will add a done callback to the task that:
                        - If the task succeeded, cache the result.
                        - Remove the task from `_inflight`.

                    Then, in the `run` method, we wait for the task without cancelling it (using `asyncio.wait` with timeout) and then:
                        - If the task is done and succeeded, we return the result (and note: the caching is already done by the callback? but we also want to return the result to the caller).
                        - If the task is done and failed, we let the exception propagate (and the caller will see it, and then the task is already removed from `_inflight` by the callback).

                    However, note: the done callback runs before the caller gets the result? Actually, the done callback runs when the task is done, which is before the caller's `await` returns. But the caller's `await` will get the result (or exception) and then the done callback runs? Actually, the done callback runs when the task is done, which is when the task's result is set. The caller's `await` will then get the result. The done callback runs in the same event loop iteration as when the task is marked done.

                    But we have to be cautious: if we remove the task from `_inflight` in the done callback, then a new caller might come in and see that the task is not in `_inflight` and start a new task, even while the old task is still being processed by the done callback? Actually, the done callback runs before the new caller gets a chance to run (because it's the same event loop iteration). So it's safe.

                Steps for the done callback:

                    def task_done(task):
                        try:
                            result = task.result()
                            # If we get here, the task succeeded -> cache it.
                            self._cache[key] = result
                        except asyncio.CancelledError:
                            # We don't cache cancelled tasks, and we don't want to log an error for cancellation?
                            # But note: the requirement says "genuinely cancelled" -> we don't cache.
                            pass
                        except Exception:
                            # Failure: don't cache.
                            pass
                        finally:
                            # Remove from inflight, but only if it's still the current task for this key?
                            # Why? Because it's possible that after the task finished, a new task was started for the same key (if the task failed and a new caller came in). But note: we remove the task from `_inflight` only when the task finishes, and we are going to remove the exact task we stored.
                            # However, if a new task was started for the same key (because the first task failed and a new caller started a new task) then the `_inflight` for that key would have been replaced by the new task. So we must check that the task we are removing is the one that is still in `_inflight`.
                            if self._inflight.get(key) is task:
                                del self._inflight[key]

                But note: the done callback runs after the task is done, and at that moment, the `_inflight` dictionary might have been updated by a new caller? Actually, no: because the task is done, and the `_inflight` entry for that key is only removed by the done callback. And the done callback runs before any new task can be started for the same key? 

                How? 
                    - When the task finishes, the done callback runs immediately (in the same event loop tick).
                    - Then, the next event loop tick might have a new caller. But in the done callback, we remove the task from `_inflight` only if it is still the current task (i.e., if no new task has been started for the same key in the meantime). However, how could a new task be started in the same tick? It can't because the event loop is processing the done callback.

                But note: the done callback runs in the context of the event loop when the task is done. At that moment, the `_inflight` dictionary still holds the task (because we haven't removed it yet). And no other code runs in between. So we can safely remove it without checking? 

                However, consider: what if two tasks for the same key are started? That should not happen because we check `_inflight` before starting a new task. So when the first task is running, the `_inflight` has the task. When the task finishes, we remove it. Then a new caller comes and starts a new task. So the done callback for the first task should remove the first task, and it is safe to do so without checking because no new task has been started for the same key until after the done callback runs.

                But wait: what if the done callback for the first task runs, and then before it removes the task from `_inflight`, a new caller comes in? That cannot happen because the done callback runs to completion in one event loop tick.

                However, to be safe, we can do:

                    if self._inflight.get(key) is task:
                        del self._inflight[key]

                This is a good practice because it avoids removing a new task that might have been started for the same key (though in reality, it shouldn't happen, but it's cheap to check).

                Now, in the `run` method:

                    We check cache -> if present, return.
                    We check inflight: if there's a task, we use that; else, we create a new task and store it in `_inflight`, and add the done callback.

                    Then, we wait for the task with a timeout without cancelling it:

                        done, pending = await asyncio.wait([task], timeout=timeout_s)
                        if task in done:
                            return task.result()   # which may raise an exception if the task failed
                        else:
                            raise asyncio.TimeoutError

                However, note: if the task fails, `task.result()` will raise the exception. We want the caller to see that exception. And then the done callback has already run (because the task is done) and removed the task from `_inflight` and not cached the result.

                But what about the requirement: "If every waiter times out and the background task later fails, its exception is retrieved rather than reported as 'Task exception was never retrieved'."?

                    - In our design, the done callback runs when the task finishes, and in the done callback we call `task.result()`. This retrieves the exception (if any) and thus prevents the "never retrieved" warning.

                However, note: in the done callback, we do:

                    try:
                        result = task.result()
                        ... # cache
                    except ... 

                This call to `task.result()` will retrieve the exception and prevent the warning.

                So the done callback serves two purposes:
                    - Cache the result on success.
                    - Remove the task from `_inflight`.
                    - Retrieve the exception (if any) to avoid the warning.

                Now, what about the timeout in the caller?

                    We do:
                        done, pending = await asyncio.wait([task], timeout=timeout_s)

                    This does not cancel the task. So if the caller times out, the task continues to run in the background.

                Also, note: if multiple callers are waiting for the same task, they all do the same `asyncio.wait` and will all time out independently.

                Steps for the fixed class:

                    class ToolRunner:
                        def __init__(self) -> None:
                            self._inflight: dict[str, asyncio.Task[Result]] = {}
                            self._cache: dict[str, Result] = {}

                        async def _run_once(self, key: str) -> Result:
                            await asyncio.sleep(0.2)
                            return Result(value=f"done:{key}")

                        def _task_done(self, key: str, task: asyncio.Task[Result]) -> None:
                            try:
                                result = task.result()
                                self._cache[key] = result
                            except asyncio.CancelledError:
                                # We don't cache cancelled tasks, and we don't want to log an error for cancellation?
                                # But note: the requirement says "genuinely cancelled" -> we don't cache.
                                # Also, we don't want to log an error for cancellation because it might be intentional (like during shutdown).
                                pass
                            except Exception:
                                # Failure: don't cache.
                                # We don't need to do anything, but we retrieve the exception so it doesn't get logged as unhandled.
                                pass
                            finally:
                                # Remove from inflight only if this task is still the one for the key.
                                if self._inflight.get(key) is task:
                                    del self._inflight[key]

                        async def run(self, key: str, timeout_s: float) -> Result:
                            # Check cache first
                            if key in self._cache:
                                return self._cache[key]

                            # Check inflight
                            task = self._inflight.get(key)
                            if task is None:
                                task = asyncio.create_task(self._run_once(key))
                                self._inflight[key] = task
                                task.add_done_callback(lambda t: self._task_done(key, t))

                            # Wait for the task with timeout, without cancelling the task.
                            done, pending = await asyncio.wait([task], timeout=timeout_s)
                            if task in done:
                                return task.result()
                            else:
                                raise asyncio.TimeoutError

                However, note: what if the task is done but we get an exception? Then `task.result()` will raise the exception. That's what we want.

                But what about the requirement: "Only successful results are cached."? 
                    - The caching is done in the done callback only when `task.result()` does not raise (i.e., on success). So that's correct.

                Also, note: the requirement "The inflight entry is removed only when that exact underlying task finishes." is satisfied because we remove it in the done callback.

                And "If every waiter times out and the background task later fails, its exception is retrieved" -> because the done callback calls `task.result()`, which retrieves the exception.

                Now, what about duplicate work?
                    - We only start one task per key because we check `_inflight` before starting a new task.

                But note: what if the task fails? Then the inflight entry is removed (by the done callback) and the cache is not updated. So the next caller will see no cache and no inflight task, and will start a new task. That's correct.

                However, there is a race condition: 
                    - Suppose the first task fails. The done callback runs and removes the task from `_inflight` and does not cache.
                    - Then, two callers come in at the same time. Both see that there is no inflight task and no cache. Both will try to start a new task.

                How to avoid duplicate work in that case?
                    - We need to make sure that only one task is started per key, even after a failure.

                The current code in `run` does:

                    task = self._inflight.get(key)
                    if task is None:
                        ... start new task

                But if two callers come in at the same time after the first task failed, they both see `task is None` and both start a new task.

                How to fix the race?
                    - We need to use a lock per key? But that might be heavy.

                Alternatively, we can use an async lock for the entire operation per key? But note: we don't want to block the entire event loop.

                Another idea: when we start a new task, we store it in `_inflight` and then add the done callback. But if two callers try to start a task at the same time, the second one should see the task that the first one started.

                However, the problem is that the `_inflight` dictionary is updated after the task is created, but before the done callback is added. So if two callers come in at the same time:

                    Caller1: sees no inflight task -> creates task1 -> stores task1 in `_inflight` -> adds done callback.
                    Caller2: sees task1 in `_inflight` -> uses task1.

                But what if the first task fails and is removed from `_inflight` by the done callback, and then two callers come in at the same time?

                    Time0: task1 fails -> done callback runs and removes task1 from `_inflight`.
                    Time1: CallerA and CallerB both check `_inflight` and see no task -> both create a new task.

                To avoid that, we need to make the operation of checking and starting atomic. We can use a lock per key.

                However, the requirement says: "Keep `run(key, timeout_s)` as the public API and assume one event loop with many concurrent callers."

                We can use a dictionary of locks, but we have to be careful to not leak locks.

                Alternatively, we can use an async lock that we create on the fly and then remove when no longer needed? But that is complex.

                Another idea: use a sentinel value in `_inflight` to indicate that a task is being started? But that doesn't help because the two callers would both try to start.

                Standard solution for this is to use a lock per key. We can have:

                    self._locks = defaultdict(asyncio.Lock)

                But note: we don't want to leak locks. We can remove the lock when the task completes? But then if a new task is started, we need a lock again.

                Actually, we can keep the lock for the key until we are sure that no new task will be started? But that's not possible.

                Alternatively, we can use a single lock for the entire `_inflight` dictionary? But that would serialize all callers for all keys, which is bad for performance.

                Better: per-key lock. We can store the lock in a separate dictionary and clean it up when the task completes? But note: the lock is only needed during the creation of the task. Once the task is in `_inflight`, we don't need the lock for that key until the task is removed (and then a new task might be started).

                Steps:

                    We maintain a dictionary `_locks` of asyncio.Lock for keys.

                    In `run`:

                        if key in self._cache: ... 

                        async with self._get_lock(key):
                            task = self._inflight.get(key)
                            if task is None:
                                task = ... 
                                self._inflight[key] = task
                                task.add_done_callback(...)

                But how to manage the locks? We don't want to leak locks.

                We can create a lock when needed and then remove it after the task is started? But note: the lock is only needed for the critical section of checking and starting. Once the task is in `_inflight`, other callers will see it and won't try to start a new task. So we can release the lock immediately after setting the task.

                However, the lock must be held until the task is stored in `_inflight` to prevent two callers from both starting a task.

                We can do:

                    lock = self._locks.setdefault(key, asyncio.Lock())
                    async with lock:
                        task = self._inflight.get(key)
                        if task is None:
                            task = ... 
                            self._inflight[key] = task
                            task.add_done_callback(...)

                But then we have a lock per key that we never remove. That's acceptable if the number of keys is bounded. But if keys are unbounded, we might leak memory.

                Alternatively, we can remove the lock after the task completes? But note: after the task completes, the inflight entry is removed, and then a new task might be started for the same key. So we need the lock to be present for the next task.

                Actually, we can keep the lock for the key forever. The memory usage is one lock per key that has ever been used. If the keys are unbounded, this is a problem.

                Another idea: use a single global lock? But that would serialize all callers for all keys, which is bad.

                Alternatively, we can avoid the lock by using an atomic operation? But in asyncio, we don't have atomic operations for dictionaries.

                Known pattern: use a dictionary of tasks that are "starting" tasks. But that's what we are doing with `_inflight` already? The problem is that when the task is removed from `_inflight` (because it failed), there is a window where two callers might both try to start a new task.

                We can change the `_inflight` dictionary to hold a task that is either:
                    - The actual `_run_once` task, OR
                    - A task that is responsible for starting the `_run_once` task (a "starter" task).

                But that complicates.

                Standard solution in asyncio for this is to use a lock per key. And if the keys are unbounded, we might need to clean up the locks. However, the problem statement does not specify the key space. In production, if keys are unbounded, we might need to use a weak reference or a cache for locks. But the problem says "production-quality", so we should handle unbounded keys.

                However, note: the requirement says "deduplicates expensive tool calls", so the keys are probably bounded (like tool names). But to be safe, we should avoid leaking locks.

                We can use a weak value dictionary for locks? But locks are not weakrefable.

                Alternatively, we can use a single lock for the entire operation? But that would be a bottleneck.

                Another idea: use an async lock that we create and then discard after the critical section? But we cannot discard it because we need it for the next time the same key is used.

                Actually, we can use a pattern with a dictionary of locks that we clean up when the task completes and there are no other waiters? But it's complex.

                Given the complexity, and since the problem states that the production issue is about cancellation and duplicate work (and the duplicate work only happens after a failure), and the requirement says "new callers sometimes start duplicate work while the original call continues", which in the original code happened because the inflight task was removed too early (on timeout, not on completion), but in our new design the inflight task is removed only on completion, so the duplicate work after a failure is a separate issue.

                However, the problem statement says: "new callers sometimes start duplicate work while the original call continues". In the original code, that happened because when the first caller timed out, it removed the inflight task, and then a new caller started a new task even though the original task was still running. In our new design, we don't remove the inflight task until the task completes, so that particular issue is fixed. But the issue of duplicate work after a failure (when two callers come in at the same time after a failure) is a new issue that wasn't mentioned in the problem statement? 

                Actually, the problem statement says: "new callers sometimes start duplicate work while the original call continues". In the original code, the "original call" was still running when the new caller started. In our new design, the inflight task remains until the task completes, so a new caller will see the inflight task and not start a duplicate. The only time duplicate work can happen is when the task fails and is removed from `_inflight`, and then two callers come in at the same time and both start a new task.

                But the problem statement does not explicitly say that duplicate work happens after a failure. It says "while the original call continues", meaning the original call is still running. In our new design, the inflight task remains until the task completes, so if the task is still running (whether it will succeed or fail), the inflight task is present and new callers will not start a duplicate.

                Therefore, the only duplicate work that can happen in our new design is when the task fails and is removed, and then two callers come in at the same time. But note: the problem statement says "production shows that ... new callers sometimes start duplicate work while the original call continues". In the case of failure, the original call has already finished (with failure) so it's not "continuing". So the problem statement is about the case where the task is still running.

                Therefore, our new design fixes the immediate problem. The race condition after failure might be acceptable if it's rare? But the requirement says "deduplicates expensive tool calls", so we must avoid duplicate work always.

                Given the requirement, we must avoid duplicate work even after a failure.

                How about: we don't remove the inflight task until the task completes, and then we leave a marker that the task has completed? But then how do we start a new task? We would need to remove the marker.

                Alternatively, we can change the `_inflight` dictionary to hold a task that is always present until we decide to start a new task. But that doesn't help.

                Another idea: when the task completes (with failure), we leave a special "failure marker" in the `_inflight` dictionary? But then we would have to distinguish between a running task and a failure marker.

                Actually, we can do:

                    Instead of storing the task directly, we store an object that has the task and also a flag for whether we are in the process of starting a new task.

                But that complicates.

                The simplest solution is to use a per-key lock. And to avoid leaking locks, we can use a weak key dictionary for locks? But locks are not weakrefable.

                Alternatively, we can use a single global lock? But that would be a performance bottleneck.

                Given the trade-offs, and since the problem says "production-quality", we should use a per-key lock and accept that we might leak locks if keys are unbounded. But in practice, if the keys are tool names, they are bounded.

                However, the problem does not specify the key space. To be safe, we can use a cache for locks with a maximum size? But that's complex.

                Another idea: use an async lock that is stored in the `_inflight` dictionary? But the `_inflight` dictionary is for tasks.

                Known pattern: use a dictionary of "future" objects that represent the starting of the task. But that's what we are doing with the task itself? 

                Actually, we can change the `_inflight` dictionary to hold a task that is the "starter" task. The starter task is responsible for starting the actual `_run_once` task and then waiting for it. But then we have two layers.

                Standard solution in asyncio for this is the "asyncio cached" pattern, which uses a lock per key. And many libraries do that (like aiohttp).

                Given the time, and since the problem statement does not specify unbounded keys, we'll use a per-key lock and note that if keys are unbounded, we might need to add a cleanup mechanism (but that's out of scope for this fix).

                Steps:

                    We'll add a `_locks` dictionary that maps keys to asyncio.Lock.

                    In `run`:

                        if key in self._cache:
                            return self._cache[key]

                        # Get or create a lock for this key
                        lock = self._locks.setdefault(key, asyncio.Lock())
                        async with lock:
                            task = self._inflight.get(key)
                            if task is None:
                                task = asyncio.create_task(self._run_once(key))
                                self._inflight[key] = task
                                task.add_done_callback(lambda t: self._task_done(key, t))

                        # Now wait for the task (outside the lock, because waiting might take time)
                        done, pending = await asyncio.wait([task], timeout=timeout_s)
                        if task in done:
                            return task.result()
                        else:
                            raise asyncio.TimeoutError

                But note: what if the task is done by the time we get out of the lock? Then we are waiting for a done task, which is fine.

                However, we must be cautious: the lock is only held during the critical section of checking and starting the task. Once the task is in `_inflight`, we release the lock and then wait. This is safe.

                Also, note: the done callback runs while the lock is not held, which is fine.

                But what about the lock dictionary? We never remove locks. If keys are unbounded, this leaks memory.

                To mitigate, we can remove the lock after the task completes and there are no other waiters? But it's complex.

                Alternatively, we can use a weak value dictionary for locks? But locks are not weakrefable.

                Another idea: use a single lock for the entire `_inflight` dictionary? But that would serialize all callers for all keys, which is bad for performance.

                Given the problem statement, and since the original issue was about cancellation and duplicate work during the same task (not after failure), and the duplicate work after failure might be rare, we might decide that the race after failure is acceptable? But the requirement says "deduplicates expensive tool calls", so we must avoid duplicate work.

                However, the problem statement says: "new callers sometimes start duplicate work while the original call continues". This implies that the original call is still running. In our new design, the inflight task remains until the task completes, so if the task is still running, the inflight task is present and new callers will not start a duplicate. The only duplicate work that can happen is when the task has failed and been removed, and then two callers come in at the same time. But in that case, the original call is not continuing (it has failed). So the problem statement does not cover that scenario.

                Therefore, for the purpose of this fix, we might not need the lock. But the requirement says "deduplicates expensive tool calls", which implies no duplicate work ever.

                Given the ambiguity, and since the problem statement says "production shows" the issue of duplicate work while the original call continues, and our new design fixes that, we might not need the lock. However, the problem also says "new callers sometimes start duplicate work", and in production it might be happening after failure as well.

                But note: the original code had the inflight task removed on timeout, which caused duplicate work while the original call was still running. Our new design does not have that issue. The issue after failure is a separate race that was not mentioned in the problem statement, but it is a logical consequence of the design.

                Since the problem says "Required semantics: Concurrent callers for one key share exactly one underlying `_run_once` task", we must avoid duplicate work always.

                Therefore, we must fix the race after failure.

                How about: we don't remove the inflight task until we are ready to start a new task? But that doesn't make sense.

                Another idea: when the task fails, we leave a special "failure" task in the `_inflight` dictionary? But then how do we start a new task? We would have to replace it.

                We can change the `_inflight` dictionary to hold a task that is always the current task for the key, and when the task fails, we remove it and then the next caller will start a new task. But to avoid two callers starting at the same time, we need synchronization.

                Given the time, and since this is a common problem, I think using a per-key lock is the way to go. And in many production systems, the key space is bounded.

                We'll add a `_locks` dictionary and use it.

                Also, note: we must not forget to clean up the lock? But if we never clean up, and keys are unbounded, it's a problem. However, the problem does not specify, so we'll assume bounded keys.

                Alternatively, we can use a weak key dictionary for locks? But locks are not weakrefable.

                We can store the lock in a way that it gets garbage collected when there are no references? But the lock is stored in `_locks`, so it won't be garbage collected.

                Another idea: use a single lock for the entire operation? But that would be a bottleneck.

                Given the trade-offs, and since the problem is about expensive tool calls (so the number of distinct tools is small), we'll use a per-key lock and not worry about cleanup.

                Now, let's write the code.

                Also, note: the requirement says "genuinely cancelled underlying tasks are not cached". In our done callback, we catch `asyncio.CancelledError` and do not cache. But note: the task might be cancelled by the event loop during shutdown. We don't want to cache that.

                But what if the task is cancelled for some other reason? We treat it as a failure.

                Now, let's write the fixed class.

                Important: the done callback must be added before we release the lock, so that if the task completes very quickly, the done callback runs and removes the task from `_inflight` before the next caller comes in.

                Steps in the critical section (with lock held):

                    task = self._inflight.get(key)
                    if task is None:
                        task = create_task(...)
                        self._inflight[key] = task
                        task.add_done_callback(...)

                This ensures that even if the task completes immediately, the done callback will run while the lock is still held? Not necessarily: the done callback runs in the event loop, so it might run after we release the lock. But that's okay because the lock is only for the critical section of starting the task. Once the task is in `_inflight`, it's safe.

                However, if the task completes very quickly, the done callback might run and remove the task from `_inflight` before we release the lock. Then, when we release the lock, the next caller might see no inflight task and start a new task. But that's what we want: if the task completed successfully, we cache the result and the next caller will use the cache. If it failed, we don't cache and the next caller will start a new task.

                But note: if the task completes successfully, the done callback caches the result and removes the task from `_inflight`. Then, when we release the lock, the next caller will see the cache and not start a new task.

                If the task fails, the done callback does not cache and removes the task from `_inflight`. Then, the next caller will see no cache and no inflight task, and will start a new task (with the lock held, so only one task is started).

                So it's safe.

                Now, let's write the code.

                We'll add:
                    self._locks: dict[str, asyncio.Lock] = {}

                And in `run`:

                    if key in self._cache:
                        return self._cache[key]

                    # Get or create a lock for this key
                    if key not in self._locks:
                        self._locks[key] = asyncio.Lock()
                    lock = self._locks[key]

                    async with lock:
                        task = self._inflight.get(key)
                        if task is None:
                            task = asyncio.create_task(self._run_once(key))
                            self._inflight[key] = task
                            task.add_done_callback(lambda t: self._task_done(key, t))

                    # Now wait for the task
                    done, pending = await asyncio.wait([task], timeout=timeout_s)
                    if task in done:
                        return task.result()
                    else:
                        raise asyncio.TimeoutError

                But note: what if the task is done by the time we get out of the lock? Then `asyncio.wait` will return immediately.

                Also, note: the done callback might have run and removed the task from `_inflight` by the time we get out of the lock, but that's okay because we still have the task object.

                However, there is a potential issue: if the task completes and the done callback runs and removes the task from `_inflight`, and then a new task is started for the same key (because the first task failed), then the `_inflight` dictionary now has a new task. But we are still waiting on the old task. That's fine because the old task is done.

                Now, what about the lock dictionary? We never remove locks. We can try to remove the lock when the task completes? But then if a new task is started for the same key, we would need to recreate the lock.

                We can do in the done callback:

                    # After removing the task from _inflight, if there are no other references to the lock, we could remove it from _locks?
                    # But we don't know if there are other callers waiting.

                Actually, we cannot remove the lock because there might be callers waiting to acquire the lock (but they are blocked on the lock, so we cannot remove it while it's held).

                Given the complexity, and since the number of distinct keys is likely small, we'll leave the locks in place.

                Now, let's write the complete class.

                Also, note: the requirement says "Failed or genuinely cancelled underlying tasks are not cached", which we handle in the done callback.

                And "Only successful results are cached", which we also handle.

                And "The inflight entry is removed only when that exact underlying task finishes", which we do in the done callback.

                And "If every waiter times out and the background task later fails, its exception is retrieved", which we do by calling `task.result()` in the done callback.

                Now, the public API is still `run(key, timeout_s)`.

                Let's write the code.

                One more thing: the done callback must be added before we release the lock, so that if the task completes very quickly, the done callback runs and we don't leave the task in `_inflight` forever.

                We do add it before releasing the lock.

                Now, the fixed class:
</think>

### Production-Quality Patch

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class Result:
    value: str


class ToolRunner:
    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Task[Result]] = {}
        self._cache: dict[str, Result] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _run_once(self, key: str) -> Result:
        await asyncio.sleep(0.2)
        return Result(value=f"done:{key}")

    def _task_done(self, key: str, task: asyncio.Task[Result]) -> None:
        try:
            result = task.result()
            self._cache[key] = result
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            if self._inflight.get(key) is task:
                del self._inflight[key]

    async def run(self, key: str, timeout_s: float) -> Result:
        if key in self._cache:
            return self._cache[key]
        
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        lock = self._locks[key]
        
        async with lock:
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._run_once(key))
                self._inflight[key] = task
                task.add_done_callback(lambda t: self._task_done(key, t))
        
        done, _ = await asyncio.wait([task], timeout=timeout_s)
        if task in done:
            return task.result()
        raise asyncio.TimeoutError
```

### Cancellation/Cleanup Invariants
1. **Shared Task Execution**: Only one `_run_once` task runs per key, enforced by per-key async locks during task initialization.
2. **Independent Caller Timeouts**: Timeouts cancel only the caller's wait (via `asyncio.wait`), never the shared task. The task always runs to completion.
3. **Cache Semantics**: Results are cached only on successful completion (handled in `_task_done`).
4. **Inflight Management**: 
   - Inflight entries persist until the underlying task *completes* (success/failure/cancellation).
   - Removal is atomic via `_task_done` with a task identity check to avoid race conditions.
5. **Exception Handling**: 
   - Task exceptions are retrieved in `_task_done`, preventing "Task exception was never retrieved" warnings.
   - Failed/cancelled tasks are never cached, allowing safe retries.
6. **Lock Management**: Per-key locks prevent duplicate task creation after failures. Locks persist (bounded by distinct keys), which is safe for typical tool-name keyspaces.

### Focused Pytest Tests
```python
import pytest
from tool_runner import ToolRunner, Result


@pytest.mark.asyncio
async def test_mixed_timeouts():
    runner = ToolRunner()
    key = "test"
    
    # Start two concurrent calls with different timeouts
    task1 = asyncio.create_task(runner.run(key, 0.1))
    task2 = asyncio.create_task(runner.run(key, 0.3))
    
    # First call should time out
    with pytest.raises(asyncio.TimeoutError):
        await task1
    
    # Second call should succeed (task continues after first timeout)
    result = await task2
    assert result == Result(value="done:test")
    assert key in runner._cache


@pytest.mark.asyncio
async def test_third_caller_after_timeout():
    runner = ToolRunner()
    key = "test"
    
    # First call times out
    task1 = asyncio.create_task(runner.run(key, 0.1))
    with pytest.raises(asyncio.TimeoutError):
        await task1
    
    # Second call starts while original task still runs
    task2 = asyncio.create_task(runner.run(key, 0.3))
    result = await task2
    assert result == Result(value="done:test")
    
    # Third call uses cache (no new task)
    task3 = asyncio.create_task(runner.run(key, 0.1))
    assert await task3 == Result(value="done:test")
    assert len(runner._inflight) == 0  # Inflight cleared after completion


@pytest.mark.asyncio
async def test_failure_then_retry():
    runner = ToolRunner()
    key = "fail"
    
    # Force failure on first attempt
    async def failing_run(key):
        raise ValueError("Simulated failure")
    
    runner._run_once = failing_run
    
    # First call fails (no cache)
    with pytest.raises(ValueError):
        await runner.run(key, 1.0)
    assert key not in runner._cache
    assert key not in runner._inflight
    
    # Second call succeeds (new task started)
    runner._run_once = lambda k: asyncio.sleep(0.1) or Result(value="retried")
    result = await runner.run(key, 1.0)
    assert result == Result(value="retried")
    assert key in runner._cache


@pytest.mark.asyncio
async def test_caller_cancellation():
    runner = ToolRunner()
    key = "cancel"
    
    # Start task and cancel one caller
    task1 = asyncio.create_task(runner.run(key, 1.0))
    task2 = asyncio.create_task(runner.run(key, 1.0))
    task1.cancel()
    
    # Verify task continues despite cancellation
    with pytest.raises(asyncio.CancelledError):
        await task1
    result = await task2
    assert result == Result(value="done:cancel")
    assert key in runner._cache


@pytest.mark.asyncio
async def test_no_duplicate_invocations():
    runner = ToolRunner()
    key = "unique"
    original_run = runner._run_once
    call_count = 0
    
    async def counting_run(key):
        nonlocal call_count
        call_count += 1
        return await original_run(key)
    
    runner._run_once = counting_run
    
    # Three concurrent callers
    results = await asyncio.gather(
        runner.run(key, 0.3),
        runner.run(key, 0.3),
        runner.run(key, 0.3),
        return_exceptions=True
    )
    
    # Only one underlying invocation
    assert call_count == 1
    assert all(r == Result(value="done:unique") for r in results)
    assert len(runner._inflight) == 0
```

---

`finish_reason=stop; wall_s=713.3; completion_tokens=11725`
