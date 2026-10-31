"""Background execution for ingestion, analysis and evaluation jobs.

Jobs run on a bounded thread pool inside the API process and persist their status/progress
in the database, so clients poll (or stream) progress by ID. For multi-instance deployments
replace ``submit`` with a durable queue (e.g. RQ/Celery); job functions are queue-agnostic.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from app.config import get_settings

log = logging.getLogger("tracemind.jobs")
_executor: ThreadPoolExecutor | None = None
_inline = False


def set_inline(value: bool) -> None:
    """Run jobs synchronously (tests)."""
    global _inline
    _inline = value


def submit(fn: Callable, *args) -> Future | None:
    global _executor
    if _inline:
        fn(*args)
        return None
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=get_settings().analysis_workers, thread_name_prefix="tm-job")

    def _run():
        try:
            fn(*args)
        except Exception:  # job functions record their own failure; this is a last resort
            log.exception("background job crashed")

    return _executor.submit(_run)


def shutdown() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
