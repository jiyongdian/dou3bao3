from __future__ import annotations

import asyncio
from contextlib import suppress

from .automation import DolaFetchAutomation
from .config import load_settings, normalize_video_model
from .memory import reclaim_memory_after_task
from .store import (
    claim_next_pending,
    get_meta,
    has_pending_tasks,
    mark_pending,
    reset_running_tasks,
    set_active_tasks,
)


class WorkerManager:
    def __init__(self) -> None:
        self._supervisor: asyncio.Task | None = None
        self._workers: dict[str, asyncio.Task] = {}
        self._claimed: set[str] = set()
        self._stopping = False
        self._worker_seq = 0

    async def start(self) -> None:
        reset_running_tasks()
        self._stopping = False
        self._supervisor = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        self._stopping = True
        tasks = list(self._workers.values())
        for task in tasks:
            task.cancel()
        if self._supervisor:
            self._supervisor.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._supervisor:
            with suppress(asyncio.CancelledError):
                await self._supervisor
        self._claimed.clear()
        set_active_tasks([])

    async def _supervise(self) -> None:
        while not self._stopping:
            desired = load_settings().browser_workers
            current_ids = list(self._workers.keys())
            for worker_id in current_ids[desired:]:
                task = self._workers.pop(worker_id, None)
                if task:
                    task.cancel()
            while len(self._workers) < desired:
                self._worker_seq += 1
                worker_id = f"worker-{self._worker_seq}"
                self._workers[worker_id] = asyncio.create_task(self._worker_loop(worker_id))
            for worker_id, task in list(self._workers.items()):
                if task.done():
                    self._workers.pop(worker_id, None)
            set_active_tasks(self._claimed)
            await asyncio.sleep(5)

    async def _worker_loop(self, worker_id: str) -> None:
        while not self._stopping:
            task_id = claim_next_pending(worker_id, self._claimed)
            if not task_id:
                await asyncio.sleep(2)
                continue
            self._claimed.add(task_id)
            set_active_tasks(self._claimed)
            try:
                meta = get_meta(task_id)
                runner = DolaFetchAutomation(
                    task_id,
                    str(meta.get("prompt") or ""),
                    str(meta.get("ratio") or "9:16"),
                    duration=int(meta.get("video_duration") or 0) or None,
                    model=normalize_video_model(meta.get("video_model")),
                    resolution=str(meta.get("resolution") or ""),
                )
                success = await runner.run()
                if not success:
                    await asyncio.sleep(2)
            except Exception as exc:
                mark_pending(task_id, str(exc)[:500])
                await asyncio.sleep(2)
            finally:
                self._claimed.discard(task_id)
                set_active_tasks(self._claimed)
                settings = load_settings()
                if settings.reclaim_memory_after_task:
                    queue_idle = not self._claimed and not has_pending_tasks(self._claimed)
                    await reclaim_memory_after_task(
                        idle=queue_idle,
                        drop_os_cache=settings.drop_os_cache_when_idle,
                    )


manager = WorkerManager()
