from __future__ import annotations

import asyncio
import json
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    global create_sem, query_sem, list_sem, delete_sem
    ensure_config()
    ensure_temp_tokens()
    create_sem = asyncio.Semaphore(2)
    query_sem = asyncio.Semaphore(5)
    list_sem = asyncio.Semaphore(1)
    delete_sem = asyncio.Semaphore(1)
    await manager.start()
    try:
        yield
    finally:
        await manager.stop()


app = FastAPI(title="Fetch Task Service", lifespan=lifespan)
ADMIN_DIR = Path(__file__).resolve().parent / "admin"

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
        if isinstance(value.get("text"), str):
            return value["text"]
        if isinstance(value.get("content"), str):
            return value["content"]
        return " ".join(part for part in (_extract_text(item) for item in value.values()) if part)
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


def _model_list() -> dict[str, Any]:
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": "dfyue-video",
                "object": "model",
                "created": now,
                "owned_by": "dfyue",
            }
        ],
    }


def _model_body(model: str = "dfyue-video") -> dict[str, Any]:
    return {
        "id": model,
        "object": "model",
        "created": int(time.time()),
        "owned_by": "dfyue",
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
        "model": "dfyue-video",
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
        "model": "dfyue-video",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


async def _create_openai_task(access: AccessContext, prompt: str, ratio: str) -> dict[str, Any]:
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
            return create_task(prompt, ratio, owner_token_hash=access.token_hash if access.is_temp else "")
        except Exception:
            if reserved_access:
                refund_temp_quota(reserved_access)
            raise


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


@app.get("/v1/models", dependencies=[Depends(require_token)])
async def openai_models():
    return _model_list()


@app.get("/v1/models/{model_id}", dependencies=[Depends(require_token)])
async def openai_model(model_id: str):
    if model_id != "dfyue-video":
        return _openai_error(f"model '{model_id}' is not available", status_code=404, code="model_not_found")
    return _model_body(model_id)


@app.get("/v1/engines", dependencies=[Depends(require_token)])
async def openai_engines():
    return _model_list()


@app.get("/v1/engines/{engine_id}", dependencies=[Depends(require_token)])
async def openai_engine(engine_id: str):
    if engine_id != "dfyue-video":
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
        "model": "dfyue-video",
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


@app.post("/v1/moderations", dependencies=[Depends(require_token)])
async def openai_moderations():
    return {
        "id": f"modr-{int(time.time())}",
        "model": "dfyue-video",
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
    payload = await _openai_payload(request)
    prompt = _openai_prompt(payload)
    ratio = str(payload.get("ratio") or payload.get("aspect_ratio") or DEFAULT_RATIO).strip()
    wait = _truthy(payload.get("wait"), False)
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or payload.get("max_wait_seconds") or 240)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="timeout_seconds must be an integer")

    meta = await _create_openai_task(access, prompt, ratio)
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
    payload = await _openai_payload(request)
    prompt = _openai_prompt(payload)
    if not prompt:
        prompt = "test video generation"
    ratio = str(payload.get("ratio") or payload.get("aspect_ratio") or DEFAULT_RATIO).strip()
    wait = _truthy(payload.get("wait"), False)
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or payload.get("max_wait_seconds") or 240)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="timeout_seconds must be an integer")

    meta = await _create_openai_task(access, prompt, ratio)
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


@app.post("/v1/images/generations", dependencies=[Depends(require_token)])
async def openai_image_generations(
    request: Request,
    access: Annotated[AccessContext, Depends(require_token)],
):
    payload = await _openai_payload(request)
    prompt = _openai_prompt(payload)
    ratio = str(payload.get("ratio") or payload.get("aspect_ratio") or DEFAULT_RATIO).strip()
    wait = _truthy(payload.get("wait"), False)
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or payload.get("max_wait_seconds") or 240)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="timeout_seconds must be an integer")
    meta = await _create_openai_task(access, prompt, ratio)
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
        "model": "dfyue-video",
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
            return _json({"ok": False, "message": "该任务已在生成不可取消"}, status_code=409)
        delete_task(task_id)
        return {"ok": True}
