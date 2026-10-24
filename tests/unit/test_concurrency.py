"""Unit tests for BoundedExecutor, async_bounded_gather, and runtime isolation."""

from __future__ import annotations

import asyncio
import os
import threading

import pytest

from honba_data.concurrency import (
    ENV_HONBA_LIVE_ACTOR,
    BoundedExecutor,
    LiveActorConcurrencyViolation,
    async_bounded_gather,
    verify_lakehouse_runtime,
)


def test_bounded_executor_lifecycle():
    """Verify BoundedExecutor executes tasks and returns results correctly."""

    def _double(x: int) -> int:
        return x * 2

    with BoundedExecutor(max_workers=3, max_queue_size=5) as executor:
        futures = [executor.submit(_double, i) for i in range(10)]
        results = [f.result() for f in futures]

    assert results == [i * 2 for i in range(10)]


def test_bounded_executor_map():
    """Verify BoundedExecutor map yields ordered results with bounded workers."""
    with BoundedExecutor(max_workers=2, max_queue_size=4) as executor:
        res = list(executor.map(lambda x: x + 10, [1, 2, 3, 4]))
    assert res == [11, 12, 13, 14]


def test_bounded_executor_invalid_params():
    """Verify validation on worker and queue parameters."""
    with pytest.raises(ValueError, match="max_workers must be positive"):
        BoundedExecutor(max_workers=0)

    with pytest.raises(ValueError, match="max_queue_size must be positive"):
        BoundedExecutor(max_workers=2, max_queue_size=0)


def test_bounded_executor_shutdown_rejects_tasks():
    """Verify submitting tasks after shutdown raises an error."""
    executor = BoundedExecutor(max_workers=2)
    executor.shutdown(wait=True)
    with pytest.raises(RuntimeError, match="Cannot submit tasks to a closed"):
        executor.submit(lambda: 42)


def test_live_actor_runtime_violation_via_env():
    """Verify that execution is blocked when HONBA_LIVE_ACTOR_RUNTIME is set."""
    os.environ[ENV_HONBA_LIVE_ACTOR] = "1"
    try:
        with pytest.raises(LiveActorConcurrencyViolation, match="Uncoordinated ThreadPoolExecutor"):
            verify_lakehouse_runtime()

        with pytest.raises(LiveActorConcurrencyViolation):
            BoundedExecutor(max_workers=2)
    finally:
        os.environ.pop(ENV_HONBA_LIVE_ACTOR, None)


def test_live_actor_runtime_violation_via_thread_name():
    """Verify that execution on a thread with live actor naming is prohibited."""
    violation_caught = False

    def _actor_run():
        nonlocal violation_caught
        try:
            verify_lakehouse_runtime()
        except LiveActorConcurrencyViolation:
            violation_caught = True

    t = threading.Thread(target=_actor_run, name="honba-live-actor-execution-0")
    t.start()
    t.join()
    assert violation_caught, "Expected LiveActorConcurrencyViolation on pinned live actor thread"


@pytest.mark.asyncio
async def test_async_bounded_gather():
    """Verify async_bounded_gather executes coroutines with bounded semaphore."""
    active_coros = 0
    max_observed_concurrency = 0

    async def _worker(val: int) -> int:
        nonlocal active_coros, max_observed_concurrency
        active_coros += 1
        max_observed_concurrency = max(max_observed_concurrency, active_coros)
        await asyncio.sleep(0.01)
        active_coros -= 1
        return val * 3

    tasks = [_worker(i) for i in range(10)]
    results = await async_bounded_gather(tasks, max_concurrency=3)

    assert results == [i * 3 for i in range(10)]
    assert max_observed_concurrency <= 3
