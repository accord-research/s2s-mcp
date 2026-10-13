"""Background jobs for slow recipe runs.

Hosts give a tool call about a minute; a cold ``seasonal_outlook`` can take
several. The tool therefore runs the recipe in a background thread and waits a
bounded time: a quick or cached run returns as before, a slow one returns a
``running`` status and keeps going. Repeating the call (the widget polls, an
agent can too) joins the same job, so identical requests from several callers
cost one run. Results land in the recipe cache, so nothing but the in-flight job
is lost if the process restarts.

Jobs are keyed per tenant by the recipe's cache key and live in this process;
a multi-process deployment needs a shared queue in place of this registry.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from mcp.server.mcpserver.exceptions import ToolError

# A finished job nobody collected is dropped after this long.
_STALE_SECONDS = 600.0


@dataclass
class Job:
    key: str
    started: float
    done: threading.Event = field(default_factory=threading.Event)
    finished: float | None = None
    result: Any = None
    error: BaseException | None = None


class Registry:
    def __init__(self) -> None:
        self._jobs: dict[tuple[str, str], Job] = {}
        self._lock = threading.Lock()

    def run(self, tenant: str, key: str, fn: Callable[[], Any], wait: float) -> tuple[str, Any]:
        """Start ``fn`` for ``key`` unless it is already running, then wait up to ``wait`` seconds.

        Returns ``("done", result)`` or ``("running", {"elapsed_seconds": n})``.
        A failed job raises its ``ToolError`` to the caller that collects it and
        is then forgotten, so the next call starts afresh.
        """
        jk = (tenant, key)
        with self._lock:
            self._purge()
            job = self._jobs.get(jk)
            if job is None:
                job = Job(key=key, started=time.monotonic())
                self._jobs[jk] = job
                threading.Thread(target=self._execute, args=(job, fn), daemon=True,
                                 name=f"s2s-job-{key[:8]}").start()
        if not job.done.wait(timeout=max(0.0, wait)):
            return "running", {"elapsed_seconds": int(time.monotonic() - job.started)}
        with self._lock:
            if self._jobs.get(jk) is job:
                del self._jobs[jk]
        if job.error is not None:
            if isinstance(job.error, ToolError):
                raise job.error
            raise ToolError(f"seasonal_outlook failed: {type(job.error).__name__}") from job.error
        return "done", job.result

    def pending(self, tenant: str) -> list[str]:
        with self._lock:
            return [k for (t, k), j in self._jobs.items() if t == tenant and not j.done.is_set()]

    @staticmethod
    def _execute(job: Job, fn: Callable[[], Any]) -> None:
        try:
            job.result = fn()
        except BaseException as exc:  # noqa: BLE001  (surfaced to the collector as a ToolError)
            job.error = exc
        finally:
            job.finished = time.monotonic()
            job.done.set()

    def _purge(self) -> None:
        now = time.monotonic()
        for jk, job in list(self._jobs.items()):
            if job.finished is not None and now - job.finished > _STALE_SECONDS:
                del self._jobs[jk]


registry = Registry()
