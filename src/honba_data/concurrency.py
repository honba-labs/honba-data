"""Concurrency model and runtime isolation primitives for the Honba Data Lakehouse.

Architectural Scope & Invariants:
--------------------------------
1. Batch/Offline Lakehouse System:
   `honba-data` operates as an offline, batch-oriented financial lakehouse and fundamentals
   engine. It is completely decoupled from `honba-ya`'s pinned-thread actor runtime and
   low-latency order execution engine.

2. Bounded Concurrency & Backpressure:
   All ingestion, download, and backfill pipelines must enforce bounded concurrency with
   strict backpressure (via bounded worker queues or `asyncio.Semaphore`). Ingestion tasks
   must never submit unbounded workloads into unbounded in-memory queues.

3. Live Runtime Isolation:
   Uncoordinated `ThreadPoolExecutor` execution in live trading paths is strictly prohibited.
   In live actor environments (e.g. `honba-ya`), thread pools introduce uncoordinated context
   switching, CPU core thrashing, and event-loop stalling.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import threading
from collections.abc import Callable, Coroutine, Iterable, Iterator
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

ENV_HONBA_LIVE_ACTOR = "HONBA_LIVE_ACTOR_RUNTIME"
LIVE_ACTOR_THREAD_PREFIX = "honba-live-actor"


class LiveActorConcurrencyViolation(RuntimeError):
    """Raised when an uncoordinated thread pool is attempted inside a live actor execution path."""


def verify_lakehouse_runtime() -> None:
    """Verify that execution is permitted and not violating live actor isolation rules.

    Raises:
        LiveActorConcurrencyViolation: If running within a live actor context.
    """
    if os.environ.get(ENV_HONBA_LIVE_ACTOR, "").strip().lower() in ("1", "true", "yes"):
        raise LiveActorConcurrencyViolation(
            "Uncoordinated ThreadPoolExecutor execution is strictly prohibited within live trading paths. "
            "Lakehouse batch pipelines must remain decoupled from honba-ya pinned-thread actors."
        )

    current_thread_name = threading.current_thread().name
    if current_thread_name.startswith(LIVE_ACTOR_THREAD_PREFIX):
        raise LiveActorConcurrencyViolation(
            f"Execution on live actor thread '{current_thread_name}' detected. "
            "ThreadPoolExecutor cannot be spawned in pinned-thread trading actor paths."
        )


class BoundedExecutor:
    """Bounded worker pool that enforces strict backpressure on task submission.

    Unlike standard `concurrent.futures.ThreadPoolExecutor` which has an unbounded task queue,
    `BoundedExecutor` limits the number of pending tasks using a bounded semaphore/queue.
    Calls to `submit()` block once the queue depth limit is reached until a slot opens up.
    """

    def __init__(
        self,
        max_workers: int = 8,
        max_queue_size: int | None = None,
        thread_name_prefix: str = "honba-data-worker",
    ) -> None:
        verify_lakehouse_runtime()
        if max_workers <= 0:
            raise ValueError(f"max_workers must be positive, got {max_workers}")

        self.max_workers = max_workers
        queue_size = max_queue_size if max_queue_size is not None else (max_workers * 2)
        if queue_size <= 0:
            raise ValueError(f"max_queue_size must be positive, got {queue_size}")

        self._semaphore = threading.BoundedSemaphore(queue_size + max_workers)
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._shutdown = False

    def submit(
        self, fn: Callable[..., R], *args: Any, **kwargs: Any
    ) -> concurrent.futures.Future[R]:
        """Submit a callable with backpressure, blocking if the bounded queue is full."""
        verify_lakehouse_runtime()
        if self._shutdown:
            raise RuntimeError("Cannot submit tasks to a closed BoundedExecutor.")

        self._semaphore.acquire()

        try:
            future = self._executor.submit(self._worker_wrapper, fn, args, kwargs)
        except Exception:
            self._semaphore.release()
            raise

        return future

    def _worker_wrapper(
        self,
        fn: Callable[..., R],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> R:
        try:
            return fn(*args, **kwargs)
        finally:
            self._semaphore.release()

    def map(
        self,
        fn: Callable[[T], R],
        items: Iterable[T],
        timeout: float | None = None,
    ) -> Iterator[R]:
        """Execute `fn` across `items` in parallel with bounded queue backpressure."""
        futures: list[concurrent.futures.Future[R]] = []
        for item in items:
            futures.append(self.submit(fn, item))

        for f in futures:
            yield f.result(timeout=timeout)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        """Shutdown the underlying worker pool."""
        self._shutdown = True
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)

    def __enter__(self) -> BoundedExecutor:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.shutdown(wait=True)


async def async_bounded_gather(
    tasks: Iterable[Coroutine[Any, Any, T]],
    max_concurrency: int = 8,
) -> list[T]:
    """Execute asynchronous coroutines with bounded concurrency via asyncio.Semaphore.

    Applies strict backpressure preventing unbounded concurrent coroutine creation.
    """
    verify_lakehouse_runtime()
    if max_concurrency <= 0:
        raise ValueError(f"max_concurrency must be positive, got {max_concurrency}")

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _sem_wrapper(coro: Coroutine[Any, Any, T]) -> T:
        async with semaphore:
            return await coro

    wrapped = [_sem_wrapper(t) for t in tasks]
    return await asyncio.gather(*wrapped)
