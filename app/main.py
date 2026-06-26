from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
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


async def require_token(
    x_api_token: Annotated[str | None, Header(alias="X-API-Token")] = None,
    authorization: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Query()] = None,
) -> None:
    configured = load_settings().api_token
    supplied = token or x_api_token or ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not configured or supplied != configured:
        raise HTTPException(status_code=403, detail="forbidden")


def _json(data: dict | list, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=data, status_code=status_code)


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


@app.get("/health", dependencies=[Depends(require_token)])
async def health():
    settings = load_settings()
    return {
        "ok": True,
        "browser_workers": settings.browser_workers,
        "active": sorted(active_task_ids()),
    }


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
async def admin_panel():
    index_path = ADMIN_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="admin panel not found")
    return FileResponse(index_path)


@app.get("/config/proxy-api", dependencies=[Depends(require_token)])
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
async def update_workers_config(request: Request, browser_workers: Annotated[int | None, Query()] = None):
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


@app.patch("/config/proxy-api", dependencies=[Depends(require_token)])
@app.put("/config/proxy-api", dependencies=[Depends(require_token)])
@app.post("/config/proxy-api", dependencies=[Depends(require_token)])
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


@app.post("/tasks", dependencies=[Depends(require_token)])
async def submit_task(
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

        meta = create_task(prompt, ratio)
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
            delete_task(meta["id"])
            raise
        return {"id": meta["id"]}


@app.get("/tasks", dependencies=[Depends(require_token)])
async def all_tasks():
    assert list_sem is not None
    async with list_sem:
        return {"tasks": list_tasks()}


@app.delete("/tasks", dependencies=[Depends(require_token)])
async def clear_tasks():
    assert delete_sem is not None
    async with delete_sem:
        return {"ok": True, **delete_inactive_tasks(active_task_ids())}


@app.get("/tasks/{task_id}", dependencies=[Depends(require_token)])
async def task_result(task_id: str):
    assert query_sem is not None
    async with query_sem:
        try:
            validate_task_id(task_id)
            get_meta(task_id)
        except (ValueError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="task not found")
        return await query_task(task_id)


@app.delete("/tasks/{task_id}", dependencies=[Depends(require_token)])
async def remove_task(task_id: str):
    assert delete_sem is not None
    async with delete_sem:
        try:
            validate_task_id(task_id)
            get_meta(task_id)
        except (ValueError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="task not found")
        if task_id in active_task_ids():
            return _json({"ok": False, "message": "该任务已在生成不可取消"}, status_code=409)
        delete_task(task_id)
        return {"ok": True}
