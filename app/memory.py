from __future__ import annotations

import asyncio
import ctypes
import gc
import os
import time
from pathlib import Path


_DROP_CACHE_LOCK = asyncio.Lock()
_LAST_DROP_CACHE = 0.0


def _malloc_trim() -> None:
    if os.name != "posix":
        return
    try:
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except Exception:
        return


def _drop_os_caches(path: Path) -> None:
    if hasattr(os, "sync"):
        os.sync()
    path.write_text("3\n", encoding="ascii")


async def reclaim_memory_after_task(*, idle: bool, drop_os_cache: bool) -> None:
    gc.collect()
    _malloc_trim()
    if not idle or not drop_os_cache or os.name != "posix":
        return
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return

    path = Path("/proc/sys/vm/drop_caches")
    if not path.exists():
        return

    async with _DROP_CACHE_LOCK:
        global _LAST_DROP_CACHE
        now = time.monotonic()
        if now - _LAST_DROP_CACHE < 15:
            return
        _LAST_DROP_CACHE = now
        try:
            await asyncio.to_thread(_drop_os_caches, path)
        except Exception:
            return
