"""
agent/concurrency.py
────────────────────
Handles safe concurrent execution when multiple developers push at the same time.

Two mechanisms:

  1. FileLock  — per-script file lock so two webhook calls never write the
                 same script simultaneously. Uses a .lock sidecar file.

  2. WebhookQueue — serialises orchestrate() calls so each commit is fully
                    processed (detect → generate → patch) before the next
                    one starts. Prevents interleaved GPT writes and k6
                    validation races.

Usage
─────
  # In script_generator / script_patcher:
  from agent.concurrency import file_lock
  with file_lock(script_path):
      _write(script_path, content)

  # In github_webhook_agent:
  from agent.concurrency import WebhookQueue
  queue = WebhookQueue()          # one instance at module level
  result = queue.submit(orchestrate, pr_number=pr, commit_sha=sha)
"""

import os
import time
import threading
import logging
from contextlib import contextmanager
from concurrent.futures import Future
from queue import Queue, Empty
from typing import Callable, Any

log = logging.getLogger("agent.concurrency")

# ── 1. Per-file lock ──────────────────────────────────────────────────────────

_file_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def _get_file_lock(path: str) -> threading.Lock:
    """Return (creating if needed) a per-path threading.Lock."""
    abs_path = os.path.abspath(path)
    with _registry_lock:
        if abs_path not in _file_locks:
            _file_locks[abs_path] = threading.Lock()
        return _file_locks[abs_path]


@contextmanager
def file_lock(path: str, timeout: float = 60.0):
    """
    Context manager that acquires an in-process lock for `path`.
    Raises TimeoutError if the lock isn't acquired within `timeout` seconds.

    Usage:
        with file_lock("scripts/dev/api_orders_perf_test.js"):
            write_file(...)
    """
    lock = _get_file_lock(path)
    acquired = lock.acquire(timeout=timeout)
    if not acquired:
        raise TimeoutError(
            f"[concurrency] Could not acquire lock for {path} within {timeout}s — "
            "another commit is still processing this script."
        )
    log.debug(f"[concurrency] Lock acquired: {path}")
    try:
        yield
    finally:
        lock.release()
        log.debug(f"[concurrency] Lock released: {path}")


# ── 2. Webhook serialisation queue ───────────────────────────────────────────

class WebhookQueue:
    """
    Serialises orchestrate() calls so concurrent webhook events are processed
    one at a time — no interleaved file writes or k6 validation races.

    Each call to submit() is non-blocking: it enqueues the job and returns a
    Future. The caller can call future.result() to wait, or ignore it and let
    the worker thread process it in the background.

    A single background worker thread drains the queue in FIFO order.
    """

    def __init__(self):
        self._queue: Queue = Queue()
        self._worker = threading.Thread(target=self._run, daemon=True, name="webhook-worker")
        self._worker.start()
        log.info("[concurrency] WebhookQueue started — single worker thread")

    def submit(self, fn: Callable, **kwargs) -> Future:
        """
        Enqueue fn(**kwargs) for serial execution.
        Returns a Future whose .result() gives the return value (or raises).
        """
        future: Future = Future()
        self._queue.put((fn, kwargs, future))
        log.info(
            f"[concurrency] Queued job: {fn.__name__}({kwargs}) "
            f"— queue depth now {self._queue.qsize()}"
        )
        return future

    def _run(self):
        """Worker loop — processes one job at a time."""
        while True:
            try:
                fn, kwargs, future = self._queue.get(timeout=1)
            except Empty:
                continue

            commit_id = kwargs.get("commit_sha", kwargs.get("pr_number", "unknown"))
            log.info(f"[concurrency] Processing: {fn.__name__} commit={commit_id}")
            t0 = time.time()

            try:
                result = fn(**kwargs)
                future.set_result(result)
                elapsed = time.time() - t0
                log.info(
                    f"[concurrency] Done: {fn.__name__} commit={commit_id} "
                    f"in {elapsed:.1f}s — {self._queue.qsize()} job(s) remaining"
                )
            except Exception as exc:
                future.set_exception(exc)
                log.error(
                    f"[concurrency] Failed: {fn.__name__} commit={commit_id} — {exc}"
                )
            finally:
                self._queue.task_done()

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()
