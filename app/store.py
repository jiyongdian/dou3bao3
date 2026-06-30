from __future__ import annotations

import json
import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import TASKS_DIR, ensure_dirs


TASK_ID_RE = re.compile(r"^[0-9a-f]{32}$")
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
TRANSIENT_RESULT_FIELDS = {
    "chat_status",
    "chat_content_type",
    "chat_response_bytes",
    "chat_response_preview",
    "sse_response_text",
    "sse_timed_out",
    "conversation_id",
    "cookie_string",
    "cookies",
    "main_url",
    "decoded_main_url",
    "last_query_error",
    "proxy_source",
    "proxy_server",
    "proxy_raw",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_task_id(task_id: str) -> str:
    task_id = (task_id or "").strip().lower()
    if not TASK_ID_RE.fullmatch(task_id):
        raise ValueError("invalid task id")
    return task_id


def task_dir(task_id: str) -> Path:
    return TASKS_DIR / validate_task_id(task_id)


def images_dir(task_id: str) -> Path:
    return task_dir(task_id) / "images"


def meta_path(task_id: str) -> Path:
    return task_dir(task_id) / "meta.json"


def result_path(task_id: str) -> Path:
    return task_dir(task_id) / "result.json"


def runtime_path() -> Path:
    from .config import RUNTIME_PATH

    return RUNTIME_PATH


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return {} if default is None else dict(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else dict(default)


def ensure_storage() -> None:
    ensure_dirs()
    runtime = runtime_path()
    if not runtime.exists():
        write_json(runtime, default_runtime())


def create_task(
    prompt: str,
    ratio: str,
    owner_token_hash: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_storage()
    for _ in range(20):
        task_id = secrets.token_hex(16)
        root = task_dir(task_id)
        if not root.exists():
            root.mkdir(parents=True)
            images_dir(task_id).mkdir()
            meta = {
                "id": task_id,
                "prompt": prompt,
                "ratio": ratio,
                "status": STATUS_PENDING,
                "image_count": 0,
                "owner_token_hash": owner_token_hash,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "error": "",
            }
            if extra:
                meta.update(extra)
            write_json(meta_path(task_id), meta)
            return meta
    raise RuntimeError("could not allocate task id")


def set_task_images(task_id: str, paths: Iterable[Path]) -> None:
    meta = get_meta(task_id)
    meta["image_count"] = len(list(paths))
    meta["updated_at"] = utc_now()
    write_json(meta_path(task_id), meta)


def get_meta(task_id: str) -> dict[str, Any]:
    path = meta_path(task_id)
    if not path.exists():
        raise FileNotFoundError(task_id)
    return read_json(path)


def update_meta(task_id: str, **updates: Any) -> dict[str, Any]:
    meta = get_meta(task_id)
    meta.update(updates)
    meta["updated_at"] = utc_now()
    write_json(meta_path(task_id), meta)
    return meta


def mark_running(task_id: str, worker_id: str) -> None:
    update_meta(task_id, status=STATUS_RUNNING, worker_id=worker_id, started_at=utc_now(), error="")


def mark_pending(task_id: str, reason: str = "") -> None:
    update_meta(task_id, status=STATUS_PENDING, worker_id="", error=reason)


def mark_success(task_id: str) -> None:
    update_meta(task_id, status=STATUS_SUCCESS, worker_id="", finished_at=utc_now(), error="")


def save_result(
    task_id: str,
    *,
    conversation_id: str = "",
    cookie_string: str = "",
    cookies: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
    remove: Iterable[str] | None = None,
) -> None:
    data = read_json(result_path(task_id), {})
    if conversation_id:
        data["conversation_id"] = conversation_id
    if cookie_string:
        data["cookie_string"] = cookie_string
    if cookies is not None:
        data["cookies"] = cookies
    if extra:
        data.update(extra)
    if remove:
        for key in remove:
            data.pop(str(key), None)
    data["updated_at"] = utc_now()
    write_json(result_path(task_id), data)


def clear_transient_result(task_id: str) -> None:
    path = result_path(task_id)
    data = read_json(path, {})
    if not data:
        return
    changed = False
    for key in TRANSIENT_RESULT_FIELDS:
        if key in data:
            data.pop(key, None)
            changed = True
    if changed:
        data["updated_at"] = utc_now()
        write_json(path, data)


def load_result(task_id: str) -> dict[str, Any]:
    return read_json(result_path(task_id), {})


def task_image_paths(task_id: str) -> list[Path]:
    root = images_dir(task_id)
    if not root.exists():
        return []
    return sorted([p for p in root.iterdir() if p.is_file()])


def list_tasks(owner_token_hash: str | None = None) -> list[dict[str, Any]]:
    ensure_storage()
    items: list[dict[str, Any]] = []
    for path in sorted(TASKS_DIR.iterdir(), key=lambda p: p.stat().st_ctime if p.exists() else 0):
        if not path.is_dir() or not TASK_ID_RE.fullmatch(path.name):
            continue
        meta = read_json(path / "meta.json", {})
        if owner_token_hash is not None and str(meta.get("owner_token_hash") or "") != owner_token_hash:
            continue
        prompt = str(meta.get("prompt") or "")
        items.append(
            {
                "id": path.name,
                "prompt": prompt,
                "prompt_preview": prompt[:15],
                "created_at": str(meta.get("created_at") or ""),
                "updated_at": str(meta.get("updated_at") or ""),
                "status": str(meta.get("status") or ""),
                "image_count": int(meta.get("image_count") or 0),
                "error": str(meta.get("error") or ""),
                "owner_token_hash": str(meta.get("owner_token_hash") or ""),
            }
        )
    return items


def delete_task(task_id: str) -> None:
    root = task_dir(task_id)
    if root.exists():
        shutil.rmtree(root)


def delete_inactive_tasks(active_ids: set[str] | None = None, owner_token_hash: str | None = None) -> dict[str, Any]:
    ensure_storage()
    active = active_ids or set()
    deleted = 0
    skipped: list[str] = []
    for item in list_tasks(owner_token_hash=owner_token_hash):
        task_id = item["id"]
        if task_id in active:
            skipped.append(task_id)
            continue
        delete_task(task_id)
        deleted += 1
    return {"deleted": deleted, "skipped": skipped}


def reset_running_tasks() -> None:
    ensure_storage()
    for item in list_tasks():
        task_id = item["id"]
        try:
            meta = get_meta(task_id)
        except FileNotFoundError:
            continue
        if meta.get("status") == STATUS_RUNNING:
            mark_pending(task_id, "service restarted")


def claim_next_pending(worker_id: str, claimed_ids: set[str]) -> str | None:
    ensure_storage()
    for item in list_tasks():
        task_id = item["id"]
        if task_id in claimed_ids:
            continue
        try:
            meta = get_meta(task_id)
        except FileNotFoundError:
            continue
        if meta.get("status") == STATUS_PENDING:
            mark_running(task_id, worker_id)
            return task_id
    return None


def has_pending_tasks(claimed_ids: set[str] | None = None) -> bool:
    ensure_storage()
    claimed = claimed_ids or set()
    for item in list_tasks():
        task_id = item["id"]
        if task_id in claimed:
            continue
        try:
            meta = get_meta(task_id)
        except FileNotFoundError:
            continue
        if meta.get("status") == STATUS_PENDING:
            return True
    return False


def default_runtime() -> dict[str, Any]:
    return {
        "active_task_ids": [],
    }


def load_runtime() -> dict[str, Any]:
    ensure_storage()
    data = read_json(runtime_path(), default_runtime())
    active = data.get("active_task_ids")
    if not isinstance(active, list):
        active = []
    return {"active_task_ids": [str(item) for item in active]}


def save_runtime(data: dict[str, Any]) -> None:
    active = data.get("active_task_ids")
    if not isinstance(active, list):
        active = []
    write_json(runtime_path(), {"active_task_ids": sorted({str(item) for item in active})})


def set_active_tasks(task_ids: Iterable[str]) -> None:
    data = load_runtime()
    data["active_task_ids"] = sorted(set(task_ids))
    save_runtime(data)


def active_task_ids() -> set[str]:
    return set(load_runtime().get("active_task_ids") or [])
