# Async shared-task timeout and cancellation repair

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

    async def _run_once(self, key: str) -> Result:
        await asyncio.sleep(0.2)
        return Result(value=f"done:{key}")

    def _on_task_done(self, key: str, task: asyncio.Task[Result]) -> None:
        # Cache successful results before removing the inflight entry so a
        # caller arriving in the done-callback window still sees the cache.
        if not task.cancelled():
            exc = task.exception()
            if exc is None:
                self._cache[key] = task.result()

        # Remove only the exact task that finished.  A newer task for the same
        # key must never be removed by an old task's callback.
        if self._inflight.get(key) is task:
            del self._inflight[key]

    async def run(self, key: str, timeout_s: float) -> Result:
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        task = self._inflight.get(key)

        # A finished task may still be present until its done callback runs.
        # Do not reuse failed/cancelled tasks; retry them immediately.
        if task is not None and task.done():
            if task.cancelled():
                self._inflight.pop(key, None)
                task = None
            else:
                exc = task.exception()
                if exc is None:
                    result = task.result()
                    self._cache[key] = result
                    return result
                self._inflight.pop(key, None)
                task = None

        if task is None:
            task = asyncio.create_task(self._run_once(key))
            self._inflight[key] = task
            task.add_done_callback(
                lambda t, k=key: self._on_task_done(k, t)
            )

        # shield() is essential: wait_for() may cancel the future it is waiting
        # on when this caller times out or is cancelled.  shield() prevents that
        # cancellation from reaching the shared underlying task.
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
            self._cache[key] = result
            return result
        except asyncio.TimeoutError:
            raise
        except asyncio.CancelledError:
            raise
```

## Invariants

- **One shared task per key**: `_inflight` is checked and populated synchronously before any `await`, so concurrent callers cannot create duplicate `_run_once` tasks.
- **Independent caller timeout/cancellation**: callers await `asyncio.shield(task)`. If `wait_for` cancels its shield on timeout or caller cancellation, the cancellation stops at the shield and does not cancel the shared task.
- **Cleanup tied to task completion**: the done callback removes the inflight entry only when that exact task finishes. A stale callback cannot delete a newer retry task because the callback checks identity.
- **Only success is cached**: the done callback and `run()` cache only when `task.exception()` is `None`.
- **Failed/cancelled tasks are retryable**: `run()` detects a finished failed/cancelled task still in `_inflight`, removes it, and starts a fresh task.
- **No “Task exception was never retrieved”**: the done callback calls `task.exception()` for every non-cancelled finished task, even if all waiters have already timed out.

## Tests

```python
import asyncio

import pytest


class CountingRunner(ToolRunner):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def _run_once(self, key: str) -> Result:
        self.calls += 1
        await asyncio.sleep(0.2)
        return Result(value=f"done:{key}")


class FailingRunner(ToolRunner):
    def __init__(self, fail_first: bool = True) -> None:
        super().__init__()
        self.calls = 0
        self.fail_first = fail_first

    async def _run_once(self, key: str) -> Result:
        self.calls += 1
        await asyncio.sleep(0.05)
        if self.fail_first and self.calls == 1:
            raise RuntimeError("boom")
        return Result(value=f"done:{key}")


@pytest.mark.asyncio
async def test_mixed_timeouts_share_one_task() -> None:
    runner = CountingRunner()

    t1 = asyncio.create_task(runner.run("k", timeout_s=0.05))
    t2 = asyncio.create_task(runner.run("k", timeout_s=0.5))

    with pytest.raises(asyncio.TimeoutError):
        await t1

    result = await t2
    assert result.value == "done:k"
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_third_caller_joins_after_first_timeout() -> None:
    runner = CountingRunner()

    t1 = asyncio.create_task(runner.run("k", timeout_s=0.05))
    with pytest.raises(asyncio.TimeoutError):
        await t1

    # The original task is still running; a new caller must join it, not restart.
    result = await runner.run("k", timeout_s=0.5)
    assert result.value == "done:k"
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_failure_then_retry() -> None:
    runner = FailingRunner(fail_first=True)

    with pytest.raises(RuntimeError, match="boom"):
        await runner.run("k", timeout_s=0.5)

    result = await runner.run("k", timeout_s=0.5)
    assert result.value == "done:k"
    assert runner.calls == 2


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_cancel_shared_task() -> None:
    runner = CountingRunner()

    t1 = asyncio.create_task(runner.run("k", timeout_s=0.5))
    await asyncio.sleep(0.01)
    t1.cancel()

    with pytest.raises(asyncio.CancelledError):
        await t1

    result = await runner.run("k", timeout_s=0.5)
    assert result.value == "done:k"
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_no_duplicate_run_once_invocation() -> None:
    runner = CountingRunner()

    results = await asyncio.gather(
        runner.run("k", timeout_s=0.5),
        runner.run("k", timeout_s=0.5),
        runner.run("k", timeout_s=0.5),
    )

    assert [r.value for r in results] == ["done:k"] * 3
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_all_waiters_timeout_and_failure_is_retrieved() -> None:
    runner = FailingRunner(fail_first=True)

    t1 = asyncio.create_task(runner.run("k", timeout_s=0.01))
    t2 = asyncio.create_task(runner.run("k", timeout_s=0.01))

    with pytest.raises(asyncio.TimeoutError):
        await t1
    with pytest.raises(asyncio.TimeoutError):
        await t2

    # Let the background task fail and run its done callback.
    await asyncio.sleep(0.1)

    # No "Task exception was never retrieved" should be reported by pytest.
    # A retry now starts a fresh task.
    result = await runner.run("k", timeout_s=0.5)
    assert result.value == "done:k"
    assert runner.calls == 2
```

## Rollback

If this patch must be reverted, restore the original `ToolRunner` class. No schema, configuration, or external interface changes are involved; the public API remains `run(key, timeout_s)`.

---

`finish_reason=stop; wall_s=1846.4; completion_tokens=11845`
