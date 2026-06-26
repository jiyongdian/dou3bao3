from __future__ import annotations

import json
import re
from typing import Any, Iterable

import httpx


PROXY_LINE_RE = re.compile(r"(?:(?:https?|socks5h?)://)?([A-Za-z0-9.-]+:\d{2,5})")


def _proxy_candidates(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _proxy_candidates(item)
        return
    if isinstance(value, dict):
        host = value.get("ip") or value.get("host") or value.get("server")
        port = value.get("port")
        if host and port:
            yield f"{host}:{port}"
        for item in value.values():
            yield from _proxy_candidates(item)


def parse_proxy_api_response(text: str) -> str:
    cleaned = str(text or "").replace("\ufeff", "").strip()
    if not cleaned:
        raise RuntimeError("proxy api returned empty response")

    candidates: list[str] = []
    try:
        candidates.extend(_proxy_candidates(json.loads(cleaned)))
    except Exception:
        pass
    candidates.extend(cleaned.splitlines())

    for item in candidates:
        match = PROXY_LINE_RE.search(str(item).strip())
        if match:
            return match.group(1)

    preview = cleaned[:300].replace("\n", "\\n")
    raise RuntimeError(f"proxy api returned no usable ip:port: {preview}")


async def fetch_proxy_from_api(api_url: str, *, timeout_seconds: int = 20, scheme: str = "http") -> dict[str, str]:
    if not api_url:
        raise RuntimeError("proxy api url is empty")

    timeout = httpx.Timeout(float(timeout_seconds), connect=min(10.0, float(timeout_seconds)))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        response = await client.get(api_url, headers={"User-Agent": "dola-fetch-service/1.0"})

    text = response.content.decode("utf-8-sig", errors="replace")
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"proxy api failed with HTTP {response.status_code}: {text[:300]}")

    host_port = parse_proxy_api_response(text)
    normalized_scheme = scheme if scheme in {"http", "https", "socks5", "socks5h"} else "http"
    return {
        "server": f"{normalized_scheme}://{host_port}",
        "host_port": host_port,
        "raw": text.strip()[:1000],
    }
