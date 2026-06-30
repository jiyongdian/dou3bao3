from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(
    os.environ.get(
        "DOLA_DATA_DIR",
        "/var/lib/dola-fetch-service" if os.name != "nt" else str(APP_ROOT / "data"),
    )
)
CONFIG_PATH = Path(os.environ.get("DOLA_CONFIG_PATH", str(DATA_DIR / "config.json")))
TASKS_DIR = DATA_DIR / "tasks"
RUNTIME_PATH = DATA_DIR / "runtime.json"

TARGET_URL = "https://www.dola.com/chat/create-image"
VALID_RATIOS = {"1:1", "3:4", "4:3", "9:16", "16:9", "21:9"}
DEFAULT_RATIO = "9:16"
DEFAULT_PROXY_API_URL = os.environ.get(
    "DOLA_DEFAULT_PROXY_API_URL",
    "https://example.com/get-proxy?num=1&type=txt",
)
FIXED_DEFAULT_API_TOKEN = "dfyue-video-fixed-token"
DEFAULT_API_TOKEN = (
    os.environ.get("DOLA_API_TOKEN")
    or os.environ.get("API_TOKEN")
    or FIXED_DEFAULT_API_TOKEN
).strip()
VALID_PROXY_API_SCHEMES = {"http", "https"}
VALID_PROXY_SERVER_SCHEMES = {"http", "https", "socks5", "socks5h"}
_CONFIG_LOCK = threading.Lock()


def _read_mem_gb() -> float:
    if os.name == "nt":
        return 4.0
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return 4.0
    for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) / 1024 / 1024
    return 4.0


def recommended_browser_workers() -> int:
    cpu = os.cpu_count() or 1
    mem_gb = _read_mem_gb()
    by_cpu = max(1, cpu // 2)
    by_mem = max(1, int(mem_gb // 2))
    return max(1, min(5, by_cpu, by_mem))


def default_config() -> dict[str, Any]:
    return {
        "api_token": "QwEb4Mf1z0ASpIjmRwRlouQ4lYp6iQaJOp-X6KxREtI",
        "host": "0.0.0.0",
        "port": 8088,
        "browser_workers": recommended_browser_workers(),
        "browser_executable_path": "",
        "headless": True,
        "task_timeout_seconds": 180,
        "video_duration": 15,
        "max_image_count": 9,
        "proxy_api_url": DEFAULT_PROXY_API_URL,
        "proxy_api_scheme": "http",
        "proxy_api_timeout_seconds": 20,
        "reclaim_memory_after_task": True,
        "drop_os_cache_when_idle": False,
    }


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load_config_dict() -> dict[str, Any]:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        raw: dict[str, Any] = {}
    else:
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            raw = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            raw = {}

    defaults = default_config()
    data = {key: raw.get(key, value) for key, value in defaults.items()}
    changed = data != raw
    if DEFAULT_API_TOKEN and data.get("api_token") != DEFAULT_API_TOKEN:
        data["api_token"] = DEFAULT_API_TOKEN
        changed = True
    if not data.get("api_token"):
        data["api_token"] = secrets.token_urlsafe(32)
        changed = True
    if changed or not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def ensure_config() -> dict[str, Any]:
    return _load_config_dict()


def validate_proxy_api_url(value: str) -> str:
    url = str(value or "").strip()
    if not url:
        raise ValueError("proxy_api_url is required")
    if any(char in url for char in "\r\n\0"):
        raise ValueError("proxy_api_url must be a single-line URL")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in VALID_PROXY_API_SCHEMES or not parsed.netloc:
        raise ValueError("proxy_api_url must be an http or https URL")
    return url


def validate_proxy_api_scheme(value: str | None) -> str:
    scheme = str(value or "http").strip().lower()
    if scheme not in VALID_PROXY_SERVER_SCHEMES:
        raise ValueError("proxy_api_scheme must be one of http, https, socks5, socks5h")
    return scheme


def update_config(updates: Mapping[str, Any]) -> dict[str, Any]:
    defaults = default_config()
    unknown = sorted(set(updates) - set(defaults))
    if unknown:
        raise KeyError(f"unknown config key: {', '.join(unknown)}")

    ensure_dirs()
    with _CONFIG_LOCK:
        if CONFIG_PATH.exists():
            try:
                loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                raw = loaded if isinstance(loaded, dict) else {}
            except json.JSONDecodeError:
                raw = {}
        else:
            raw = {}

        data = {key: raw.get(key, value) for key, value in defaults.items()}
        data.update(updates)
        if DEFAULT_API_TOKEN:
            data["api_token"] = DEFAULT_API_TOKEN
        if not data.get("api_token"):
            data["api_token"] = secrets.token_urlsafe(32)

        temp_path = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.tmp")
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(CONFIG_PATH)
        return data


@dataclass(frozen=True)
class Settings:
    api_token: str
    host: str
    port: int
    browser_workers: int
    browser_executable_path: str
    headless: bool
    task_timeout_seconds: int
    video_duration: int
    max_image_count: int
    proxy_api_url: str
    proxy_api_scheme: str
    proxy_api_timeout_seconds: int
    reclaim_memory_after_task: bool
    drop_os_cache_when_idle: bool


def _as_bool(value: Any, default: bool) -> bool:
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


def load_settings() -> Settings:
    data = _load_config_dict()
    proxy_api_scheme = str(data.get("proxy_api_scheme") or "http").strip().lower()
    if proxy_api_scheme not in VALID_PROXY_SERVER_SCHEMES:
        proxy_api_scheme = "http"
    return Settings(
        api_token=str(data.get("api_token") or ""),
        host=str(data.get("host") or "0.0.0.0"),
        port=int(data.get("port") or 8088),
        browser_workers=max(1, min(5, int(data.get("browser_workers") or 1))),
        browser_executable_path=str(data.get("browser_executable_path") or "").strip(),
        headless=_as_bool(data.get("headless"), True),
        task_timeout_seconds=max(30, int(data.get("task_timeout_seconds") or 180)),
        video_duration=max(1, int(data.get("video_duration") or 15)),
        max_image_count=max(0, min(9, int(data.get("max_image_count") or 9))),
        proxy_api_url=str(data.get("proxy_api_url") or DEFAULT_PROXY_API_URL).strip(),
        proxy_api_scheme=proxy_api_scheme,
        proxy_api_timeout_seconds=max(3, int(data.get("proxy_api_timeout_seconds") or 20)),
        reclaim_memory_after_task=_as_bool(data.get("reclaim_memory_after_task"), True),
        drop_os_cache_when_idle=_as_bool(data.get("drop_os_cache_when_idle"), False),
    )


def normalize_proxy_server(server: str, default_scheme: str = "http") -> str:
    value = str(server or "").strip()
    if not value:
        return ""
    if "://" in value:
        return value
    scheme = (default_scheme or "http").strip().lower()
    if scheme not in VALID_PROXY_SERVER_SCHEMES:
        scheme = "http"
    return f"{scheme}://{value}"


def browser_proxy_config_for(server: str, default_scheme: str = "http") -> dict[str, str] | None:
    proxy_server = normalize_proxy_server(server, default_scheme)
    if not proxy_server:
        return None
    return {"server": proxy_server}
