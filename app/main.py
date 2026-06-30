from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import (
    DEFAULT_RATIO,
    VALID_RATIOS,
    ensure_config,
    load_settings,
    update_config,
    validate_proxy_api_scheme,
    validate_proxy_api_url,
)
from .query import query_task
from .store import (
    active_task_ids,
    create_task,
    delete_inactive_tasks,
    delete_task,
    get_meta,
    images_dir,
    list_tasks,
    set_task_images,
    validate_task_id,
)
from .temp_access import (
    AccessContext,
    QuotaExceeded,
    create_temp_tokens,
    delete_temp_token,
    ensure_temp_tokens,
    get_temp_context,
    hash_token,
    list_temp_tokens,
    refund_temp_quota,
    reserve_temp_quota,
    update_temp_token,
)
from .textfix import repair_text
from .worker import manager


create_sem = None
query_sem = None
list_sem = None
delete_sem = None
update_lock = None
RUNNING_COMMIT_SHORT = None
RUNNING_COMMIT_FULL = None

IMAGE_FORM_KEYS = {
    "image",
    "images",
    "file",
    "files",
    "reference_image",
    "reference_images",
}
IMAGE_VALUE_KEYS = {
    "image",
    "images",
    "image_url",
    "image_urls",
    "input_image",
    "input_images",
    "reference_image",
    "reference_images",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    global create_sem, query_sem, list_sem, delete_sem, update_lock
    ensure_config()
    ensure_temp_tokens()
    create_sem = asyncio.Semaphore(2)
    query_sem = asyncio.Semaphore(5)
    list_sem = asyncio.Semaphore(1)
    delete_sem = asyncio.Semaphore(1)
    update_lock = asyncio.Lock()
    await manager.start()
    try:
        yield
    finally:
        await manager.stop()


app = FastAPI(title="Fetch Task Service", lifespan=lifespan)


@app.middleware("http")
async def no_store_admin_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/admin/assets/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ADMIN_DIR = Path(__file__).resolve().parent / "admin"
API_DOCUMENTATION_PATH = PROJECT_ROOT / "API_DOCUMENTATION.md"
UPDATE_REMOTE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
UPDATE_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
DEFAULT_UPDATE_BRANCH = "main"

if ADMIN_DIR.exists():
    app.mount("/admin/assets", StaticFiles(directory=ADMIN_DIR), name="admin-assets")



@app.get("/", include_in_schema=False)
async def root_panel():
    return RedirectResponse(url="/admin", status_code=307)


async def require_token(
    x_api_token: Annotated[str | None, Header(alias="X-API-Token")] = None,
    authorization: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Query()] = None,
) -> AccessContext:
    configured = load_settings().api_token
    supplied = token or x_api_token or ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if configured and supplied == configured:
        return AccessContext(token_hash=hash_token(supplied), is_admin=True, is_temp=False)
    temp_context = get_temp_context(supplied)
    if temp_context:
        return temp_context
    raise HTTPException(status_code=403, detail="forbidden")


async def require_admin(access: Annotated[AccessContext, Depends(require_token)]) -> AccessContext:
    if not access.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")
    return access


async def require_temp(access: Annotated[AccessContext, Depends(require_token)]) -> AccessContext:
    if not access.is_temp:
        raise HTTPException(status_code=403, detail="forbidden")
    return access




@app.get("/api-documentation", include_in_schema=False, dependencies=[Depends(require_admin)])
async def api_documentation():
    if not API_DOCUMENTATION_PATH.exists():
        raise HTTPException(status_code=404, detail="api documentation not found")
    return FileResponse(
        API_DOCUMENTATION_PATH,
        media_type="text/markdown; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )

def _sanitize_git_remote(value: str) -> str:
    if "://" not in value:
        return value
    scheme, rest = value.split("://", 1)
    host = rest.split("/", 1)[0]
    if "@" not in host:
        return value
    return f"{scheme}://***@{rest.split('@', 1)[1]}"


def _git_result(args: list[str], timeout: int = 90) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="git is not installed in this container")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="git command timed out")
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    return {"ok": completed.returncode == 0, "code": completed.returncode, "stdout": stdout, "stderr": stderr}


def _git_required(args: list[str], timeout: int = 90) -> dict[str, Any]:
    result = _git_result(args, timeout=timeout)
    if not result["ok"]:
        message = result["stderr"] or result["stdout"] or "git command failed"
        raise HTTPException(status_code=500, detail=message[-500:])
    return result


def _running_git_identity() -> dict[str, str]:
    global RUNNING_COMMIT_SHORT, RUNNING_COMMIT_FULL
    if RUNNING_COMMIT_SHORT is None:
        short = _git_result(["rev-parse", "--short", "HEAD"])
        RUNNING_COMMIT_SHORT = short["stdout"] if short["ok"] else ""
    if RUNNING_COMMIT_FULL is None:
        full = _git_result(["rev-parse", "HEAD"])
        RUNNING_COMMIT_FULL = full["stdout"] if full["ok"] else ""
    return {"short": RUNNING_COMMIT_SHORT or "", "full": RUNNING_COMMIT_FULL or ""}


def _ensure_update_lock() -> asyncio.Lock:
    global update_lock
    if update_lock is None:
        update_lock = asyncio.Lock()
    return update_lock


def _resolve_update_branch(raw_branch: Any, fallback: str = DEFAULT_UPDATE_BRANCH) -> str:
    branch = str(raw_branch or "").strip()
    if not branch or branch == "HEAD":
        return fallback
    return branch


def _git_status_payload() -> dict[str, Any]:
    if not (PROJECT_ROOT / ".git").exists():
        return {"available": False, "root": str(PROJECT_ROOT), "message": ".git directory is missing"}
    branch = _git_result(["rev-parse", "--abbrev-ref", "HEAD"])
    disk_commit = _git_result(["rev-parse", "--short", "HEAD"])
    disk_commit_full = _git_result(["rev-parse", "HEAD"])
    remote = _git_result(["config", "--get", "remote.origin.url"])
    dirty = _git_result(["status", "--porcelain"])
    running = _running_git_identity()
    return {
        "available": True,
        "root": str(PROJECT_ROOT),
        "branch": branch["stdout"] if branch["ok"] else "",
        "commit": running["short"] or (disk_commit["stdout"] if disk_commit["ok"] else ""),
        "running_commit": running["short"],
        "running_commit_full": running["full"],
        "disk_commit": disk_commit["stdout"] if disk_commit["ok"] else "",
        "disk_commit_full": disk_commit_full["stdout"] if disk_commit_full["ok"] else "",
        "remote": _sanitize_git_remote(remote["stdout"]) if remote["ok"] else "",
        "dirty": bool(dirty["stdout"]) if dirty["ok"] else None,
    }


async def _restart_current_process(delay_seconds: float = 1.0, method: str = "exit") -> None:
    await asyncio.sleep(delay_seconds)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    if method == "execv":
        os.execv(sys.executable, [sys.executable, *sys.argv])
    os._exit(0)


def _json(data: dict | list, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=data, status_code=status_code)


def _health_payload(access: AccessContext) -> dict:
    settings = load_settings()
    data = {
        "ok": True,
        "role": "admin" if access.is_admin else "client",
        "browser_workers": settings.browser_workers,
        "active": sorted(active_task_ids()),
    }
    if access.is_temp:
        data["quota"] = {
            "limit": access.limit,
            "used": access.used,
            "remaining": access.remaining,
        }
    return data


async def _request_payload(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid json body")
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="json body must be an object")
        return {str(key): str(value) for key, value in data.items() if value is not None}

    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        return {str(key): str(value) for key, value in form.items() if value is not None}

    body = (await request.body()).decode("utf-8", errors="replace").strip()
    return {"url": body} if body else {}


async def _openai_payload(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid json body")
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="json body must be an object")
        return data
    body = await request.body()
    body_text = body.decode("utf-8", errors="replace").strip()
    if body_text.startswith("{"):
        try:
            data = json.loads(body_text)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid json body")
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="json body must be an object")
        return data
    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        return {str(key): value for key, value in form.items() if value is not None}
    return {"input": body_text} if body_text else {}


async def _openai_payload_and_uploads(request: Request) -> tuple[dict[str, Any], list[Any]]:
    content_type = request.headers.get("content-type", "").lower()
    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        payload: dict[str, Any] = {}
        uploads: list[Any] = []
        for key, value in form.multi_items():
            key = str(key)
            is_upload = hasattr(value, "filename") and hasattr(value, "file")
            if is_upload and key in IMAGE_FORM_KEYS and getattr(value, "filename", ""):
                uploads.append(value)
                continue
            if key in payload:
                existing = payload[key]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    payload[key] = [existing, value]
            else:
                payload[key] = value
        return payload, uploads
    return await _openai_payload(request), []


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if str(value.get("type") or "").lower() in {"image_url", "input_image", "input_image_url"}:
            return ""
        if isinstance(value.get("text"), str):
            return value["text"]
        if isinstance(value.get("content"), str):
            return value["content"]
        return " ".join(
            part
            for part in (
                _extract_text(item)
                for key, item in value.items()
                if str(key) not in IMAGE_VALUE_KEYS
            )
            if part
        )
    if isinstance(value, list):
        return " ".join(part for part in (_extract_text(item) for item in value) if part)
    return str(value)


def _openai_prompt(payload: dict[str, Any]) -> str:
    for key in ("input", "prompt", "text"):
        text = _extract_text(payload.get(key)).strip()
        if text:
            return text
    messages = payload.get("messages")
    if isinstance(messages, list):
        return _extract_text(messages).strip()
    return ""


def _image_url_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        nested = value.get("url") or value.get("image_url") or value.get("data")
        if isinstance(nested, (str, dict)):
            return _image_url_value(nested)
    return ""


def _collect_image_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        item_type = str(value.get("type") or "").lower()
        if item_type in {"image_url", "input_image", "input_image_url"}:
            ref = _image_url_value(value.get("image_url") or value.get("url") or value.get("data"))
            if ref:
                refs.append(ref)
        for key, item in value.items():
            key_text = str(key)
            if key_text in IMAGE_VALUE_KEYS:
                if isinstance(item, list):
                    refs.extend(ref for ref in (_image_url_value(entry) for entry in item) if ref)
                else:
                    ref = _image_url_value(item)
                    if ref:
                        refs.append(ref)
                    refs.extend(_collect_image_refs(item))
            elif isinstance(item, (dict, list)):
                refs.extend(_collect_image_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_collect_image_refs(item))
    return list(dict.fromkeys(refs))


def _suffix_from_content_type(content_type: str, fallback: str = ".png") -> str:
    content_type = content_type.split(";", 1)[0].strip().lower()
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    return mapping.get(content_type, fallback)


async def _image_bytes_from_ref(ref: str) -> tuple[bytes, str]:
    value = str(ref or "").strip()
    if not value:
        raise ValueError("empty image reference")
    if value.startswith("data:"):
        header, _, data = value.partition(",")
        if not data:
            raise ValueError("invalid data url")
        suffix = _suffix_from_content_type(header[5:].split(";", 1)[0])
        return base64.b64decode(data), suffix
    if value.startswith("http://") or value.startswith("https://"):
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
            response = await client.get(value)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if not content_type.lower().startswith("image/"):
                raise ValueError("image url did not return an image")
            return response.content, _suffix_from_content_type(content_type)
    return base64.b64decode(value), ".png"


async def _save_openai_images(task_id: str, uploads: list[Any], refs: list[str]) -> list[Path]:
    uploads = [item for item in uploads if item and getattr(item, "filename", "")]
    refs = [item for item in refs if str(item or "").strip()]
    total = len(uploads) + len(refs)
    if total > load_settings().max_image_count:
        raise HTTPException(status_code=400, detail="too many images")
    saved_paths: list[Path] = []
    for upload in uploads:
        filename = Path(getattr(upload, "filename", "") or f"image_{len(saved_paths) + 1}.png").name
        suffix = Path(filename).suffix.lower() or ".png"
        target = images_dir(task_id) / f"{len(saved_paths) + 1:02d}{suffix}"
        with target.open("wb") as out:
            upload.file.seek(0)
            shutil.copyfileobj(upload.file, out)
        saved_paths.append(target)
    for ref in refs:
        try:
            data, suffix = await _image_bytes_from_ref(ref)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid image reference: {exc}")
        target = images_dir(task_id) / f"{len(saved_paths) + 1:02d}{suffix}"
        target.write_bytes(data)
        saved_paths.append(target)
    if saved_paths:
        set_task_images(task_id, saved_paths)
    return saved_paths


def _public_base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    host = request.headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
    if not host:
        host = request.headers.get("host", "").strip()
    if proto and host:
        return f"{proto}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _check_task_access(access: AccessContext, task_id: str) -> dict[str, Any]:
    try:
        validate_task_id(task_id)
        meta = get_meta(task_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="response not found")
    if access.is_temp and str(meta.get("owner_token_hash") or "") != access.token_hash:
        raise HTTPException(status_code=404, detail="response not found")
    return meta


def _openai_output_text(status: str, task_id: str, video_url: str, text: str) -> str:
    if status == "completed" and video_url:
        return video_url
    if status == "failed":
        return text or "video generation failed"
    return f"video task {task_id} is {status}"


def _video_model_id() -> str:
    return load_settings().video_model


def _model_body(model: str | None = None) -> dict[str, Any]:
    model_id = model or _video_model_id()
    return {
        "id": model_id,
        "object": "model",
        "created": 0,
        "owned_by": "dola",
        "root": model_id,
        "parent": None,
        "permission": [],
    }


def _model_list() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [_model_body()],
    }


def _openai_error(message: str, status_code: int = 501, code: str = "unsupported_endpoint") -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "param": None,
                "code": code,
            }
        },
    )


def _input_count(value: Any) -> int:
    if isinstance(value, list):
        return max(1, len(value))
    return 1


def _openai_response_body(
    *,
    request: Request,
    task_id: str,
    meta: dict[str, Any],
    result: dict[str, str] | None = None,
) -> dict[str, Any]:
    result = result or {"code": "0", "text": "", "url": ""}
    code = str(result.get("code") or "")
    base_url = _public_base_url(request)
    created_at = int(time.time())
    if str(meta.get("created_at") or ""):
        created_at = int(time.time())

    video_url = ""
    content_url = f"{base_url}/v1/videos/{task_id}/content"
    error = None
    if code == "2" and result.get("url"):
        status = "completed"
        video_url = content_url
    elif code == "1":
        status = "failed"
        error = {"message": str(result.get("text") or meta.get("error") or "video generation failed")}
    else:
        status = "in_progress" if str(meta.get("status") or "") == "running" else "queued"

    output_text = _openai_output_text(status, task_id, video_url, str(result.get("text") or ""))
    return {
        "id": task_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "model": _video_model_id(),
        "error": error,
        "output": [
            {
                "id": f"msg_{task_id}",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": output_text}],
            }
        ],
        "video": {
            "id": task_id,
            "status": status,
            "url": video_url,
            "content_url": video_url,
        },
    }


def _image_generation_body(response: dict[str, Any]) -> dict[str, Any]:
    video = response.get("video") if isinstance(response.get("video"), dict) else {}
    return {
        "created": int(response.get("created_at") or time.time()),
        "data": [
            {
                "url": str(video.get("url") or video.get("content_url") or ""),
                "revised_prompt": f"video task {response.get('id')} is {response.get('status')}",
            }
        ],
    }


def _video_generation_body(response: dict[str, Any]) -> dict[str, Any]:
    video = response.get("video") if isinstance(response.get("video"), dict) else {}
    video_url = str(video.get("url") or video.get("content_url") or "")
    task_id = str(response.get("id") or "")
    status = str(response.get("status") or "queued")
    body = {
        "id": task_id,
        "object": "video.generation",
        "created": int(response.get("created_at") or time.time()),
        "model": str(response.get("model") or _video_model_id()),
        "status": status,
        "url": video_url,
        "content_url": video_url,
        "video": {
            "id": task_id,
            "status": status,
            "url": video_url,
            "content_url": video_url,
        },
        "data": [{"url": video_url}] if video_url else [],
    }
    if response.get("error"):
        body["error"] = response["error"]
    return body


def _video_list_body(request: Request, access: AccessContext) -> dict[str, Any]:
    owner = access.token_hash if access.is_temp else None
    data: list[dict[str, Any]] = []
    for item in list_tasks(owner_token_hash=owner):
        task_id = str(item.get("id") or "")
        if not task_id:
            continue
        status = str(item.get("status") or "queued")
        response = _openai_response_body(request=request, task_id=task_id, meta=item)
        data.append(_video_generation_body(response))
    return {"object": "list", "data": data}


def _chat_completion_body(response: dict[str, Any]) -> dict[str, Any]:
    text = ""
    output = response.get("output")
    if isinstance(output, list) and output:
        content = output[0].get("content") if isinstance(output[0], dict) else None
        if isinstance(content, list) and content:
            item = content[0]
            if isinstance(item, dict):
                text = str(item.get("text") or "")
    if not text:
        text = f"video task {response.get('id')} is {response.get('status')}"
    return {
        "id": f"chatcmpl-{response.get('id')}",
        "object": "chat.completion",
        "created": int(response.get("created_at") or time.time()),
        "model": _video_model_id(),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


async def _create_openai_task(
    access: AccessContext,
    prompt: str,
    ratio: str,
    uploads: list[Any] | None = None,
    image_refs: list[str] | None = None,
) -> dict[str, Any]:
    assert create_sem is not None
    async with create_sem:
        prompt = repair_text((prompt or "").strip())
        ratio = (ratio or DEFAULT_RATIO).strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="input is required")
        if ratio not in VALID_RATIOS:
            raise HTTPException(status_code=400, detail="invalid ratio")

        reserved_access: AccessContext | None = None
        try:
            reserved_access = reserve_temp_quota(access)
        except QuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc))

        try:
            meta = create_task(prompt, ratio, owner_token_hash=access.token_hash if access.is_temp else "")
        except Exception:
            if reserved_access:
                refund_temp_quota(reserved_access)
            raise
        try:
            await _save_openai_images(meta["id"], uploads or [], image_refs or [])
        except Exception:
            if reserved_access:
                refund_temp_quota(reserved_access)
            delete_task(meta["id"])
            raise
        return meta


async def _query_openai_task(access: AccessContext, task_id: str) -> tuple[dict[str, Any], dict[str, str]]:
    assert query_sem is not None
    async with query_sem:
        meta = _check_task_access(access, task_id)
        result = await query_task(task_id)
        return meta, result


async def _wait_openai_task(
    *,
    access: AccessContext,
    request: Request,
    task_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(1, min(timeout_seconds, 600))
    last: dict[str, Any] | None = None
    while True:
        meta, result = await _query_openai_task(access, task_id)
        last = _openai_response_body(request=request, task_id=task_id, meta=meta, result=result)
        if last["status"] in {"completed", "failed"} or time.monotonic() >= deadline:
            return last
        await asyncio.sleep(3)


@app.get("/health", dependencies=[Depends(require_token)])
async def health(access: Annotated[AccessContext, Depends(require_token)]):
    return _health_payload(access)


@app.get("/auth/admin", dependencies=[Depends(require_admin)])
async def admin_auth(access: Annotated[AccessContext, Depends(require_admin)]):
    return _health_payload(access)


@app.get("/auth/client", dependencies=[Depends(require_temp)])
async def client_auth(access: Annotated[AccessContext, Depends(require_temp)]):
    return _health_payload(access)



@app.get("/admin/update/status", dependencies=[Depends(require_admin)])
async def admin_update_status(
    access: Annotated[AccessContext, Depends(require_admin)],
    check_remote: bool = False,
    remote: str = "origin",
    branch: str | None = None,
):
    status = _git_status_payload()
    if not status.get("available") or not check_remote:
        return status

    lock = _ensure_update_lock()
    remote = str(remote or "origin").strip()
    branch = _resolve_update_branch(branch or status.get("branch"))
    if not UPDATE_REMOTE_RE.fullmatch(remote):
        raise HTTPException(status_code=400, detail="invalid git remote name")
    if not UPDATE_BRANCH_RE.fullmatch(branch) or branch.startswith("-") or ".." in branch:
        raise HTTPException(status_code=400, detail="invalid git branch name")

    async with lock:
        await asyncio.to_thread(_git_required, ["fetch", "--prune", remote, branch], 180)
        remote_ref = f"{remote}/{branch}"
        remote_commit = _git_result(["rev-parse", "--short", remote_ref])
        compare_ref = status.get("running_commit_full") or "HEAD"
        counts = _git_result(["rev-list", "--left-right", "--count", f"{compare_ref}...{remote_ref}"])
        ahead = behind = 0
        if counts["ok"] and counts["stdout"]:
            parts = counts["stdout"].split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                ahead = int(parts[0])
                behind = int(parts[1])
        status.update(
            {
                "checked_remote": True,
                "remote_branch": branch,
                "remote_commit": remote_commit["stdout"] if remote_commit["ok"] else "",
                "ahead": ahead,
                "behind": behind,
                "has_update": behind > 0,
            }
        )
        return status


@app.post("/admin/update", dependencies=[Depends(require_admin)])
async def admin_update(
    request: Request,
    access: Annotated[AccessContext, Depends(require_admin)],
):
    lock = _ensure_update_lock()
    payload = await _request_payload(request)
    remote = str(payload.get("remote") or "origin").strip()
    current = _git_status_payload()
    branch = _resolve_update_branch(payload.get("branch") or current.get("branch"))
    restart = str(payload.get("restart") or "true").strip().lower() not in {"0", "false", "no", "off"}
    restart_method = str(payload.get("restart_method") or "exit").strip().lower()
    if restart_method not in {"exit", "execv"}:
        raise HTTPException(status_code=400, detail="invalid restart method")

    if not current.get("available"):
        raise HTTPException(status_code=409, detail="git metadata is missing; redeploy once with the new Dockerfile first")
    if not UPDATE_REMOTE_RE.fullmatch(remote):
        raise HTTPException(status_code=400, detail="invalid git remote name")
    if not UPDATE_BRANCH_RE.fullmatch(branch) or branch.startswith("-") or ".." in branch:
        raise HTTPException(status_code=400, detail="invalid git branch name")

    async with lock:
        before = current.get("running_commit") or _git_required(["rev-parse", "--short", "HEAD"])["stdout"]
        disk_before = _git_required(["rev-parse", "--short", "HEAD"])["stdout"]
        await asyncio.to_thread(_git_required, ["fetch", "--prune", remote, branch], 180)
        remote_ref = f"{remote}/{branch}"
        await asyncio.to_thread(_git_required, ["checkout", "-B", branch, remote_ref], 90)
        await asyncio.to_thread(_git_required, ["reset", "--hard", remote_ref], 90)
        after = _git_required(["rev-parse", "--short", "HEAD"])["stdout"]
        status = _git_status_payload()
        changed = before != after
        if restart:
            asyncio.create_task(_restart_current_process(method=restart_method))
        return {
            "ok": True,
            "before": before,
            "after": after,
            "disk_before": disk_before,
            "changed": changed,
            "branch": branch,
            "restart": restart,
            "restart_method": restart_method,
            "status": status,
        }


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
async def admin_panel():
    index_path = ADMIN_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="admin panel not found")
    return FileResponse(index_path, headers={"Cache-Control": "no-store"})


@app.get("/client", include_in_schema=False)
@app.get("/client/", include_in_schema=False)
async def client_panel():
    index_path = ADMIN_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="client panel not found")
    return FileResponse(index_path, headers={"Cache-Control": "no-store"})


@app.get("/config/proxy-api", dependencies=[Depends(require_admin)])
async def proxy_api_config():
    settings = load_settings()
    return {
        "proxy_api_url": settings.proxy_api_url,
        "proxy_api_scheme": settings.proxy_api_scheme,
        "proxy_api_timeout_seconds": settings.proxy_api_timeout_seconds,
    }


@app.get("/config/workers", dependencies=[Depends(require_token)])
async def workers_config():
    settings = load_settings()
    return {"browser_workers": settings.browser_workers}


@app.post("/config/workers", dependencies=[Depends(require_token)])
async def update_workers_config(
    access: Annotated[AccessContext, Depends(require_token)],
    request: Request,
    browser_workers: Annotated[int | None, Query()] = None,
):
    if access.is_temp:
        raise HTTPException(status_code=403, detail="forbidden")
    payload = await _request_payload(request)
    raw_workers = payload.get("browser_workers") or payload.get("workers") or browser_workers
    if raw_workers is None:
        raise HTTPException(status_code=400, detail="browser_workers is required")
    try:
        workers = int(raw_workers)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="browser_workers must be an integer")
    if workers < 1 or workers > 5:
        raise HTTPException(status_code=400, detail="browser_workers must be between 1 and 5")
    try:
        update_config({"browser_workers": workers})
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    settings = load_settings()
    return {"ok": True, "browser_workers": settings.browser_workers}


@app.patch("/config/proxy-api", dependencies=[Depends(require_admin)])
@app.put("/config/proxy-api", dependencies=[Depends(require_admin)])
@app.post("/config/proxy-api", dependencies=[Depends(require_admin)])
async def update_proxy_api_config(
    request: Request,
    url: Annotated[str | None, Query()] = None,
    proxy_api_url: Annotated[str | None, Query()] = None,
    scheme: Annotated[str | None, Query()] = None,
    proxy_api_scheme: Annotated[str | None, Query()] = None,
):
    payload = await _request_payload(request)
    next_url = payload.get("proxy_api_url") or payload.get("url") or proxy_api_url or url
    next_scheme = payload.get("proxy_api_scheme") or payload.get("scheme") or proxy_api_scheme or scheme
    if not next_url:
        raise HTTPException(status_code=400, detail="proxy_api_url is required")

    try:
        updates = {"proxy_api_url": validate_proxy_api_url(next_url)}
        if next_scheme:
            updates["proxy_api_scheme"] = validate_proxy_api_scheme(next_scheme)
        update_config(updates)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    settings = load_settings()
    return {
        "ok": True,
        "proxy_api_url": settings.proxy_api_url,
        "proxy_api_scheme": settings.proxy_api_scheme,
        "proxy_api_timeout_seconds": settings.proxy_api_timeout_seconds,
    }


@app.get("/temp-tokens", dependencies=[Depends(require_admin)])
async def temp_tokens_list():
    return {"tokens": list_temp_tokens()}


@app.post("/temp-tokens", dependencies=[Depends(require_admin)])
async def temp_tokens_create(request: Request):
    payload = await _request_payload(request)
    try:
        count = int(payload.get("count") or payload.get("num") or 1)
        limit = int(payload.get("limit") or 100)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="count and limit must be integers")
    return {"tokens": create_temp_tokens(count, limit)}


@app.patch("/temp-tokens/{token_id}", dependencies=[Depends(require_admin)])
@app.put("/temp-tokens/{token_id}", dependencies=[Depends(require_admin)])
async def temp_tokens_update(token_id: str, request: Request):
    payload = await _request_payload(request)
    if "limit" not in payload:
        raise HTTPException(status_code=400, detail="limit is required")
    try:
        token = update_temp_token(token_id, limit=int(payload["limit"]))
    except KeyError:
        raise HTTPException(status_code=404, detail="token not found")
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="limit must be an integer")
    return {"ok": True, "token": token}


@app.delete("/temp-tokens/{token_id}", dependencies=[Depends(require_admin)])
async def temp_tokens_delete(token_id: str):
    if not delete_temp_token(token_id):
        raise HTTPException(status_code=404, detail="token not found")
    return {"ok": True}


@app.get("/models", dependencies=[Depends(require_token)])
@app.get("/v1/models", dependencies=[Depends(require_token)])
@app.get("/v1/v1/models", dependencies=[Depends(require_token)])
async def openai_models():
    return _model_list()


@app.get("/models/{model_id}", dependencies=[Depends(require_token)])
@app.get("/v1/models/{model_id}", dependencies=[Depends(require_token)])
@app.get("/v1/v1/models/{model_id}", dependencies=[Depends(require_token)])
async def openai_model(model_id: str):
    if model_id != _video_model_id():
        return _openai_error(f"model '{model_id}' is not available", status_code=404, code="model_not_found")
    return _model_body(model_id)


@app.get("/engines", dependencies=[Depends(require_token)])
@app.get("/v1/engines", dependencies=[Depends(require_token)])
@app.get("/v1/v1/engines", dependencies=[Depends(require_token)])
async def openai_engines():
    return _model_list()


@app.get("/engines/{engine_id}", dependencies=[Depends(require_token)])
@app.get("/v1/engines/{engine_id}", dependencies=[Depends(require_token)])
@app.get("/v1/v1/engines/{engine_id}", dependencies=[Depends(require_token)])
async def openai_engine(engine_id: str):
    if engine_id != _video_model_id():
        return _openai_error(f"engine '{engine_id}' is not available", status_code=404, code="model_not_found")
    return _model_body(engine_id)


@app.post("/v1/embeddings", dependencies=[Depends(require_token)])
async def openai_embeddings(request: Request):
    payload = await _openai_payload(request)
    count = _input_count(payload.get("input"))
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": index, "embedding": [0.0] * 8}
            for index in range(count)
        ],
        "model": _video_model_id(),
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


@app.post("/v1/moderations", dependencies=[Depends(require_token)])
async def openai_moderations():
    return {
        "id": f"modr-{int(time.time())}",
        "model": _video_model_id(),
        "results": [
            {
                "flagged": False,
                "categories": {},
                "category_scores": {},
            }
        ],
    }


@app.post("/v1/responses", dependencies=[Depends(require_token)])
async def openai_create_response(
    request: Request,
    access: Annotated[AccessContext, Depends(require_token)],
):
    payload, uploads = await _openai_payload_and_uploads(request)
    prompt = _openai_prompt(payload)
    ratio = str(payload.get("ratio") or payload.get("aspect_ratio") or DEFAULT_RATIO).strip()
    wait = _truthy(payload.get("wait"), False)
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or payload.get("max_wait_seconds") or 240)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="timeout_seconds must be an integer")

    meta = await _create_openai_task(access, prompt, ratio, uploads, _collect_image_refs(payload))
    if wait:
        return await _wait_openai_task(
            access=access,
            request=request,
            task_id=meta["id"],
            timeout_seconds=timeout_seconds,
        )
    return _openai_response_body(request=request, task_id=meta["id"], meta=meta)


@app.post("/v1/chat/completions", dependencies=[Depends(require_token)])
async def openai_chat_completions(
    request: Request,
    access: Annotated[AccessContext, Depends(require_token)],
):
    payload, uploads = await _openai_payload_and_uploads(request)
    prompt = _openai_prompt(payload)
    if not prompt:
        prompt = "test video generation"
    ratio = str(payload.get("ratio") or payload.get("aspect_ratio") or DEFAULT_RATIO).strip()
    wait = _truthy(payload.get("wait"), False)
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or payload.get("max_wait_seconds") or 240)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="timeout_seconds must be an integer")

    meta = await _create_openai_task(access, prompt, ratio, uploads, _collect_image_refs(payload))
    if wait:
        response = await _wait_openai_task(
            access=access,
            request=request,
            task_id=meta["id"],
            timeout_seconds=timeout_seconds,
        )
    else:
        response = _openai_response_body(request=request, task_id=meta["id"], meta=meta)
    return _chat_completion_body(response)


@app.post("/v1/video/generations", dependencies=[Depends(require_token)])
@app.post("/v1/videos/generations", dependencies=[Depends(require_token)])
async def openai_video_generations(
    request: Request,
    access: Annotated[AccessContext, Depends(require_token)],
):
    payload, uploads = await _openai_payload_and_uploads(request)
    prompt = _openai_prompt(payload)
    ratio = str(payload.get("ratio") or payload.get("aspect_ratio") or payload.get("size") or DEFAULT_RATIO).strip()
    wait = _truthy(payload.get("wait"), False)
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or payload.get("max_wait_seconds") or 240)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="timeout_seconds must be an integer")
    meta = await _create_openai_task(access, prompt, ratio, uploads, _collect_image_refs(payload))
    if wait:
        response = await _wait_openai_task(
            access=access,
            request=request,
            task_id=meta["id"],
            timeout_seconds=timeout_seconds,
        )
    else:
        response = _openai_response_body(request=request, task_id=meta["id"], meta=meta)
    return _video_generation_body(response)


@app.post("/v1/images/generations", dependencies=[Depends(require_token)])
async def openai_image_generations(
    request: Request,
    access: Annotated[AccessContext, Depends(require_token)],
):
    payload, uploads = await _openai_payload_and_uploads(request)
    prompt = _openai_prompt(payload)
    ratio = str(payload.get("ratio") or payload.get("aspect_ratio") or DEFAULT_RATIO).strip()
    wait = _truthy(payload.get("wait"), False)
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or payload.get("max_wait_seconds") or 240)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="timeout_seconds must be an integer")
    meta = await _create_openai_task(access, prompt, ratio, uploads, _collect_image_refs(payload))
    if wait:
        response = await _wait_openai_task(
            access=access,
            request=request,
            task_id=meta["id"],
            timeout_seconds=timeout_seconds,
        )
    else:
        response = _openai_response_body(request=request, task_id=meta["id"], meta=meta)
    return _image_generation_body(response)


@app.post("/v1/completions", dependencies=[Depends(require_token)])
async def openai_completions(
    request: Request,
    access: Annotated[AccessContext, Depends(require_token)],
):
    chat = await openai_chat_completions(request, access)
    text = chat["choices"][0]["message"]["content"]
    return {
        "id": chat["id"],
        "object": "text_completion",
        "created": chat["created"],
        "model": _video_model_id(),
        "choices": [{"index": 0, "text": text, "finish_reason": "stop"}],
        "usage": chat["usage"],
    }


@app.get("/v1/responses/{response_id}", dependencies=[Depends(require_token)])
async def openai_get_response(
    request: Request,
    access: Annotated[AccessContext, Depends(require_token)],
    response_id: str,
):
    meta, result = await _query_openai_task(access, response_id)
    return _openai_response_body(request=request, task_id=response_id, meta=meta, result=result)


@app.api_route("/v1/videos", methods=["GET", "POST"], dependencies=[Depends(require_token)])
async def openai_videos(
    request: Request,
    access: Annotated[AccessContext, Depends(require_token)],
    id: str | None = None,
    video_id: str | None = None,
    response_id: str | None = None,
):
    payload: dict[str, Any] = {}
    if request.method != "GET":
        payload = await _openai_payload(request)
    task_id = str(
        id
        or video_id
        or response_id
        or payload.get("id")
        or payload.get("video_id")
        or payload.get("response_id")
        or ""
    ).strip()
    if not task_id:
        return _video_list_body(request, access)
    meta, result = await _query_openai_task(access, task_id)
    response = _openai_response_body(request=request, task_id=task_id, meta=meta, result=result)
    return _video_generation_body(response)


@app.get("/v1/videos/{video_id}", dependencies=[Depends(require_token)])
@app.get("/videos/{video_id}", dependencies=[Depends(require_token)])
async def openai_video(
    request: Request,
    access: Annotated[AccessContext, Depends(require_token)],
    video_id: str,
):
    meta, result = await _query_openai_task(access, video_id)
    response = _openai_response_body(request=request, task_id=video_id, meta=meta, result=result)
    return _video_generation_body(response)


@app.get("/v1/videos/{video_id}/content", dependencies=[Depends(require_token)])
@app.get("/videos/{video_id}/content", dependencies=[Depends(require_token)])
async def openai_video_content(access: Annotated[AccessContext, Depends(require_token)], video_id: str):
    _check_task_access(access, video_id)
    result = await query_task(video_id)
    url = str(result.get("url") or "")
    if str(result.get("code") or "") != "2" or not url:
        raise HTTPException(status_code=409, detail="video is not ready")
    return RedirectResponse(url=url, status_code=302)


@app.api_route(
    "/v1/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    dependencies=[Depends(require_token)],
)
async def openai_unsupported(path: str):
    return _openai_error(
        f"/v1/{path} is recognized but not supported by this video-only service",
        status_code=501,
    )


@app.post("/tasks", dependencies=[Depends(require_token)])
async def submit_task(
    access: Annotated[AccessContext, Depends(require_token)],
    prompt: Annotated[str, Form()],
    ratio: Annotated[str, Form()] = DEFAULT_RATIO,
    images: Annotated[list[UploadFile] | None, File(alias="images")] = None,
):
    assert create_sem is not None
    async with create_sem:
        prompt = repair_text((prompt or "").strip())
        ratio = (ratio or DEFAULT_RATIO).strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt is required")
        if ratio not in VALID_RATIOS:
            raise HTTPException(status_code=400, detail="invalid ratio")
        uploads = [item for item in (images or []) if item and item.filename]
        if len(uploads) > load_settings().max_image_count:
            raise HTTPException(status_code=400, detail="too many images")

        reserved_access: AccessContext | None = None
        try:
            reserved_access = reserve_temp_quota(access)
        except QuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc))

        try:
            meta = create_task(prompt, ratio, owner_token_hash=access.token_hash if access.is_temp else "")
        except Exception:
            if reserved_access:
                refund_temp_quota(reserved_access)
            raise
        saved_paths: list[Path] = []
        try:
            for index, upload in enumerate(uploads, start=1):
                filename = Path(upload.filename or f"image_{index}.png").name
                suffix = Path(filename).suffix.lower() or ".png"
                target = images_dir(meta["id"]) / f"{index:02d}{suffix}"
                with target.open("wb") as out:
                    shutil.copyfileobj(upload.file, out)
                saved_paths.append(target)
            set_task_images(meta["id"], saved_paths)
        except Exception:
            if reserved_access:
                refund_temp_quota(reserved_access)
            delete_task(meta["id"])
            raise
        response = {"id": meta["id"]}
        if reserved_access and reserved_access.is_temp:
            response["quota"] = {
                "limit": reserved_access.limit,
                "used": reserved_access.used,
                "remaining": reserved_access.remaining,
            }
        return response


@app.get("/tasks", dependencies=[Depends(require_token)])
async def all_tasks(access: Annotated[AccessContext, Depends(require_token)]):
    assert list_sem is not None
    async with list_sem:
        owner = access.token_hash if access.is_temp else None
        return {"tasks": list_tasks(owner_token_hash=owner)}


@app.delete("/tasks", dependencies=[Depends(require_token)])
async def clear_tasks(access: Annotated[AccessContext, Depends(require_token)]):
    assert delete_sem is not None
    async with delete_sem:
        owner = access.token_hash if access.is_temp else None
        return {"ok": True, **delete_inactive_tasks(active_task_ids(), owner_token_hash=owner)}


@app.get("/tasks/{task_id}", dependencies=[Depends(require_token)])
async def task_result(access: Annotated[AccessContext, Depends(require_token)], task_id: str):
    assert query_sem is not None
    async with query_sem:
        try:
            validate_task_id(task_id)
            meta = get_meta(task_id)
        except (ValueError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="task not found")
        if access.is_temp and str(meta.get("owner_token_hash") or "") != access.token_hash:
            raise HTTPException(status_code=404, detail="task not found")
        return await query_task(task_id)


@app.delete("/tasks/{task_id}", dependencies=[Depends(require_token)])
async def remove_task(access: Annotated[AccessContext, Depends(require_token)], task_id: str):
    assert delete_sem is not None
    async with delete_sem:
        try:
            validate_task_id(task_id)
            meta = get_meta(task_id)
        except (ValueError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="task not found")
        if access.is_temp and str(meta.get("owner_token_hash") or "") != access.token_hash:
            raise HTTPException(status_code=404, detail="task not found")
        if task_id in active_task_ids():
            return _json({"ok": False, "message": "task is running and cannot be deleted"}, status_code=409)
        delete_task(task_id)
        return {"ok": True}
