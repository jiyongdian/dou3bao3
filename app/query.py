from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx

from .store import STATUS_SUCCESS, get_meta, load_result, save_result
from .textfix import repair_text


RECENT_CONV_URL = (
    "https://www.dola.com/im/chain/recent_conv?"
    "version_code=20800&language=zh&device_platform=web&aid=495671&real_aid=495671"
    "&pkg_type=release_version&device_id=111&pc_version=3.23.7&web_id=111"
    "&tea_uuid=111&region=JP&sys_region=JP&samantha_web=1&web_platform=browser"
    "&use-olympus-account=1&web_tab_id=111"
)

SINGLE_CHAIN_URL = (
    "https://www.dola.com/im/chain/single?"
    "version_code=20800&language=zh&device_platform=web&aid=495671&real_aid=495671"
    "&pkg_type=release_version&device_id=111&pc_version=3.23.7&web_id=111"
    "&tea_uuid=111&region=JP&sys_region=JP&samantha_web=1&web_platform=browser"
    "&use-olympus-account=1&web_tab_id=111"
)

QUERY_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
QUERY_CLIENT_HINTS = {
    "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


def _headers(cookie: str) -> dict[str, str]:
    return {
        "agw-js-conv": "str",
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json; encoding=utf-8",
        "user-agent": QUERY_UA,
        "cookie": cookie,
        **QUERY_CLIENT_HINTS,
    }


def _recent_payload() -> dict[str, Any]:
    return {
        "cmd": 3200,
        "uplink_body": {
            "pull_recent_conv_chain_uplink_body": {
                "limit": 10,
                "message_count_per_conv": 10,
                "api_version": 1,
                "conv_version": 0,
                "direction": 3,
                "option": {
                    "not_need_message": True,
                    "need_complete_conversation": True,
                    "need_coco_conversation": True,
                    "need_coco_bot": True,
                },
            }
        },
        "sequence_id": "111",
        "channel": 2,
        "version": "1",
    }


def _single_payload(conversation_id: str) -> dict[str, Any]:
    return {
        "cmd": 3100,
        "uplink_body": {
            "pull_singe_chain_uplink_body": {
                "conversation_id": conversation_id,
                "anchor_index": 111,
                "conversation_type": 3,
                "direction": 1,
                "limit": 20,
                "ext": {},
                "filter": {"index_list": []},
                "evaluate_ab_params": "",
                "evaluate_common_params": "",
            }
        },
        "sequence_id": "111",
        "channel": 2,
        "version": "1",
    }


def _try_parse_json_string(value: str) -> Any:
    text = value.strip()
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _walk(value: Any, depth: int = 0):
    if depth > 40:
        return
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item, depth + 1)
    elif isinstance(value, str):
        parsed = _try_parse_json_string(value)
        if parsed is not None:
            yield from _walk(parsed, depth + 1)


def extract_conversation_id(data: Any) -> str:
    for item in _walk(data):
        if isinstance(item, dict):
            cid = item.get("conversation_id")
            if isinstance(cid, str) and cid.isdigit() and len(cid) == 17:
                return cid
            if isinstance(cid, int) and len(str(cid)) == 17:
                return str(cid)
    return ""


def extract_conversation_id_from_sse(text: str) -> str:
    if not text:
        return ""
    patterns = (
        r'\\?"conversation_id\\?"\s*:\s*\\?"?(\d{17})',
        r"conversation_id(?:\\\\?\"|)\s*[:=]\s*(?:\\\\?\")?(\d{17})",
        r"/chat/(\d{17})(?:\D|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def extract_main_url(data: Any) -> str:
    for item in _walk(data):
        if isinstance(item, dict) and "video_model" in item:
            video_model = item.get("video_model")
            parsed = _try_parse_json_string(video_model) if isinstance(video_model, str) else video_model
            for nested in _walk(parsed):
                if isinstance(nested, dict):
                    main_url = nested.get("main_url")
                    if isinstance(main_url, str) and main_url:
                        return main_url
    for item in _walk(data):
        if isinstance(item, dict):
            main_url = item.get("main_url")
            if isinstance(main_url, str) and main_url:
                return main_url
    return ""


def _single_chain_messages(data: Any) -> list[dict[str, Any]]:
    body = data.get("downlink_body", {}) if isinstance(data, dict) else {}
    chain = body.get("pull_singe_chain_downlink_body", {}) if isinstance(body, dict) else {}
    messages = chain.get("messages", []) if isinstance(chain, dict) else []
    return [item for item in messages if isinstance(item, dict)]


def _collect_strings(value: Any, depth: int = 0) -> list[str]:
    if depth > 40:
        return []
    if isinstance(value, str):
        parsed = _try_parse_json_string(value)
        if parsed is not None:
            return [value, *_collect_strings(parsed, depth + 1)]
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_collect_strings(item, depth + 1))
        return out
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_collect_strings(item, depth + 1))
        return out
    return []


def _extract_wait_text(data: Any) -> str:
    values: list[str] = []
    pattern = re.compile(r"预计等待\s*[^。！？\n\r，,]*?(?:分钟|秒|小时)")
    for raw_text in _collect_strings(data):
        text = repair_text(raw_text)
        for match in pattern.findall(text):
            if match and match not in values:
                values.append(match)
    return "，".join(values)


def extract_tts_content(data: Any) -> str:
    messages = _single_chain_messages(data)
    text = ""
    if messages:
        tts = messages[0].get("tts_content")
        if isinstance(tts, str):
            text = repair_text(tts.strip())
    wait_text = _extract_wait_text(data)
    if wait_text:
        return f"{text}{wait_text}" if text else wait_text
    return text


def decode_main_url(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            padded = cleaned + "=" * (-len(cleaned) % 4)
            data = decoder(padded.encode("ascii"))
            text = data.decode("utf-8", errors="strict")
            if text.startswith("http://") or text.startswith("https://"):
                return text
        except Exception:
            continue
    return ""


async def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    timeout = httpx.Timeout(30.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return json.loads(response.content.decode("utf-8-sig", errors="replace"))


async def fetch_recent_conversation_id(cookie: str) -> str:
    data = await _post_json(RECENT_CONV_URL, _headers(cookie), _recent_payload())
    return extract_conversation_id(data)


async def fetch_single_chain(cookie: str, conversation_id: str) -> tuple[str, str]:
    data = await _post_json(SINGLE_CHAIN_URL, _headers(cookie), _single_payload(conversation_id))
    return extract_main_url(data), extract_tts_content(data)


async def query_task(task_id: str) -> dict[str, str]:
    meta = get_meta(task_id)
    if meta.get("status") != STATUS_SUCCESS:
        return {"code": "0", "text": "", "url": ""}

    result = load_result(task_id)
    cached_url = str(result.get("decoded_main_url") or "")
    if cached_url:
        return {"code": "2", "text": "", "url": cached_url}

    cookie = str(result.get("cookie_string") or "")
    if not cookie:
        return {"code": "1", "text": "没有文本", "url": ""}

    sse_text = str(
        result.get("sse_response_text")
        or result.get("chat_response_text")
        or result.get("chat_response_preview")
        or ""
    )
    conversation_id = extract_conversation_id_from_sse(sse_text)
    if conversation_id:
        save_result(task_id, conversation_id=conversation_id)
    if not conversation_id:
        conversation_id = str(result.get("conversation_id") or "")
    if not conversation_id:
        try:
            conversation_id = await fetch_recent_conversation_id(cookie)
        except Exception as exc:
            save_result(task_id, extra={"last_query_error": str(exc)})
            return {"code": "1", "text": "没有文本", "url": ""}
        if conversation_id:
            save_result(task_id, conversation_id=conversation_id)

    if not conversation_id:
        return {"code": "1", "text": "没有文本", "url": ""}

    try:
        main_url_encoded, tts_content = await fetch_single_chain(cookie, conversation_id)
    except Exception as exc:
        save_result(task_id, extra={"last_query_error": str(exc)})
        return {"code": "1", "text": "没有文本", "url": ""}

    if main_url_encoded:
        decoded = decode_main_url(main_url_encoded)
        if decoded:
            save_result(
                task_id,
                extra={"decoded_main_url": decoded},
                remove={"main_url", "cookie_string", "cookies", "conversation_id", "last_query_error"},
            )
            return {"code": "2", "text": "", "url": decoded}

    return {"code": "1", "text": tts_content or "没有文本", "url": ""}
