# Async shared-task timeout and cancellation repair

Looking at the bugs:

1. `asyncio.wait_for(task, ...)` cancels the shared task on timeout → all callers lose their work
2. `finally` removes the inflight entry even when the task is still running → new callers spawn duplicates

The fix: decouple per-caller timeouts from the shared task using a `Future`-based fan-out pattern.

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Result:
    value: str


class ToolRunner:
    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Task[Result]] = {}
        self._cache: dict[str, Result] = {}
        # Per-key list of futures that callers await independently
        self._waiters: dict[str, list[asyncio.Future[Result]]] = {}

    async def _run_once(self, key: str) -> Result:
        await asyncio.sleep(0.2)
        return Result(value=f"done:{key}")

    def _on_task_done(self, key: str, task: asyncio.Task[Result]) -> None:
        """Callback fired when the shared task completes (success, failure, or cancel)."""
        # Remove inflight entry so future callers can start a new task
        self._inflight.pop(key, None)

        # Snapshot and clear the waiter list for this key
        waiters = self._waiters.pop(key, [])

        if task.cancelled():
            for w in waiters:
                w.cancel()
        elif task.exception() is not None:
            exc = task.exception()
            for w in waiters:
                w.set_exception(exc)
        else:
            result = task.result()
            self._cache[key] = result
            for w in waiters:
                w.set_result(result)

    async def run(self, key: str, timeout_s: float) -> Result:
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._run_once(key))
            self._inflight[key] = task
            task.add_done_callback(lambda t, k=key: self._on_task_done(k, t))

        # Each caller gets its own Future; the shared task is never cancelled
        # by a caller's timeout.
        future: asyncio.Future[Result] = asyncio.get_event_loop().create_future()
        self._waiters.setdefault(key, []).append(future)

        try:
            result = await asyncio.wait_for(future, timeout=timeout_s)
            self._cache[key] = result
            return result
        except asyncio.TimeoutError:
            raise
        finally:
            # Remove this caller's future from the waiter list
            waiters = self._waiters.get(key, [])
            if future in waiters:
                waiters.remove(future)
```

**Cancellation/cleanup invariants:**

| Invariant | How it's enforced |
|---|---|
| One shared task per key | `self._inflight` check-and-create is atomic within a single coroutine; `add_done_callback` fires only once per task |
| Caller timeout never cancels shared task | `asyncio.wait_for` wraps the caller's own `Future`, not the shared `Task` |
| Inflight removed only when task finishes | `_on_task_done` callback fires on task completion (success/failure/cancel) and pops from `_inflight` |
| No duplicate `_run_once` | New callers see the existing task in `_inflight` and attach a new waiter future |
| Exception retrieval | `_on_task_done` calls `task.exception()` which retrieves the exception; if all waiters timed out, the exception is still consumed by the callback |
| Only successful results cached | `_on_task_done` only sets `self._cache[key]` in the success branch |

Now the tests:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, patch


# ── Tests ──────────────────────────────────────────────────────────────────

@pytest.fixture
def runner():
    return ToolRunner()


@pytest.mark.asyncio
async def test_no_duplicate_run_once(runner):
    """Two concurrent callers share exactly one _run_once invocation."""
    call_count = 0
    original = runner._run_once

    async def counting_run_once(key):
        nonlocal call_count
        call_count += 1
        return await original(key)

    runner._run_once = counting_run_once

    results = await asyncio.gather(
        runner.run("k", timeout_s=5.0),
        runner.run("k", timeout_s=5.0),
    )
    assert call_count == 1
    assert results[0] == results[1] == Result(value="done:k")


@pytest.mark.asyncio
async def test_mixed_timeouts(runner):
    """Short-timeout caller gets TimeoutError; long-timeout caller gets result."""
    # Make the task take 0.3s
    async def slow_run_once(key):
        await asyncio.sleep(0.3)
        return Result(value=f"done:{key}")

    runner._run_once = slow_run_once

    short = asyncio.create_task(runner.run("k", timeout_s=0.1))
    long = asyncio.create_task(runner.run("k", timeout_s=2.0))

    with pytest.raises(asyncio.TimeoutError):
        await short

    result = await long
    assert result == Result(value="done:k")


@pytest.mark.asyncio
async def test_third_caller_after_first_timeout(runner):
    """After a short-timeout caller times out, a new caller still gets the result."""
    async def slow_run_once(key):
        await asyncio.sleep(0.3)
        return Result(value=f"done:{key}")

    runner._run_once = slow_run_once

    # First caller times out
    short = asyncio.create_task(runner.run("k", timeout_s=0.1))
    with pytest.raises(asyncio.TimeoutError):
        await short

    # Third caller joins while the task is still running
    result = await runner.run("k", timeout_s=2.0)
    assert result == Result(value="done:k")


@pytest.mark.asyncio
async def test_failure_then_retry(runner):
    """Failed task is not cached; a new caller starts a fresh task."""
    call_count = 0

    async def failing_then_ok(key):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        return Result(value=f"done:{key}")

    runner._run_once = failing_then_ok

    # First call fails
    with pytest.raises(RuntimeError, match="boom"):
        await runner.run("k", timeout_s=5.0)

    # Second call retries (new task)
    result = await runner.run("k", timeout_s=5.0)
    assert result == Result(value="done:k")
    assert call_count == 2


@pytest.mark.asyncio
async def test_caller_cancellation(runner):
    """Cancelling a caller's coroutine does not cancel the shared task."""
    async def slow_run_once(key):
        await asyncio.sleep(0.3)
        return Result(value=f"done:{key}")

    runner._run_once = slow_run_once

    # Start a caller and cancel it
    caller = asyncio.create_task(runner.run("k", timeout_s=5.0))
    await asyncio.sleep(0.05)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    # The shared task should still be running; another caller can get the result
    result = await runner.run("k", timeout_s=2.0)
    assert result == Result(value="done:k")


@pytest.mark.asyncio
async def test_all_waiters_timeout_exception_retrieved(runner):
    """When all waiters time out, the task's exception is still retrieved (no warning)."""
    async def failing_run_once(key):
        await asyncio.sleep(0.1)
        raise RuntimeError("fail")

    runner._run_once = failing_run_once

    # Both callers time out
    with pytest.raises(asyncio.TimeoutError):
        await runner.run("k", timeout_s=0.05)
    with pytest.raises(asyncio.TimeoutError):
        await runner.run("k", timeout_s=0.05)

    # Give the task time to complete
    await asyncio.sleep(0.2)

    # The task should have been cleaned up (no "Task exception was never retrieved")
    assert "k" not in runner._inflight


@pytest.mark.asyncio
async def test_cached_result_returned_immediately(runner):
    """After a successful call, subsequent calls return the cached result."""
    result = await runner.run("k", timeout_s=5.0)
    assert result == Result(value="done:k")

    # Second call should return cached result without starting a new task
    result2 = await runner.run("k", timeout_s=5.0)
    assert result2 == result
    assert "k" not in runner._inflight
```

**Key design decisions:**

- **`Future`-based fan-out**: Each caller gets its own `asyncio.Future` that is resolved by the shared task's `done_callback`. This decouples per-caller timeouts from the shared task.
- **`add_done_callback`**: Ensures the shared task's result/exception is always consumed, even if all waiters timed out. This prevents "Task exception was never retrieved" warnings.
- **No `asyncio.wait_for` on the shared task**: The shared task is never cancelled by a caller's timeout. Only the caller's own `Future` is wrapped with `wait_for`.
- **Inflight cleanup in callback**: The inflight entry is removed only when the shared task actually completes, not when a caller times out.

---

`finish_reason=stop; wall_s=343.5; completion_tokens=12100`
