from __future__ import annotations

import asyncio
import binascii
import hashlib
import hmac
import json
import mimetypes
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import httpx
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .config import TARGET_URL, browser_proxy_config_for, load_settings, normalize_video_model
from .proxy_manager import fetch_proxy_from_api
from .store import (
    clear_transient_result,
    mark_pending,
    mark_success,
    save_result,
    task_image_paths,
)


REGION_RESTRICTED_URL = "https://www.dola.com/security/region-restricted?source=1"
IMAGEX_REGION = "us-east-1"
IMAGEX_SERVICE = "imagex"
IMAGEX_API_VERSION = "2018-08-01"
PREPARE_UPLOAD_BODY = {"tenant_id": "5", "scene_id": "4", "resource_type": 2}
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
BROWSER_EXTRA_HTTP_HEADERS = {
    "sec-ch-ua": '"Not-A.Brand";v="24", "Chromium";v="146"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}
BROWSER_INIT_SCRIPT = r"""
(() => {
  const brands = [
    { brand: "Not-A.Brand", version: "24" },
    { brand: "Chromium", version: "146" }
  ];
  const fullVersionList = [
    { brand: "Not-A.Brand", version: "24.0.0.0" },
    { brand: "Chromium", version: "146.0.0.0" }
  ];
  const userAgentData = {
    brands,
    mobile: false,
    platform: "Windows",
    getHighEntropyValues: async (hints = []) => {
      const values = { brands, mobile: false, platform: "Windows" };
      const map = {
        architecture: "x86",
        bitness: "64",
        fullVersionList,
        model: "",
        platformVersion: "10.0.0",
        uaFullVersion: "146.0.0.0",
        wow64: false
      };
      for (const hint of hints) {
        if (Object.prototype.hasOwnProperty.call(map, hint)) values[hint] = map[hint];
      }
      return values;
    },
    toJSON: () => ({ brands, mobile: false, platform: "Windows" })
  };
  const define = (target, name, value) => {
    try {
      Object.defineProperty(target, name, { get: () => value, configurable: true });
    } catch (_) {}
  };
  define(Navigator.prototype, "platform", "Win32");
  define(Navigator.prototype, "userAgentData", userAgentData);
})();
"""


PREPARE_UPLOAD_SCRIPT = r"""
async ({body}) => {
  function uuid() {
    return crypto.randomUUID ? crypto.randomUUID() :
      "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0;
        const v = c === "x" ? r : (r & 0x3 | 0x8);
        return v.toString(16);
      });
  }
  function randomDigits(len) {
    let out = "";
    for (let i = 0; i < len; i += 1) out += String(Math.floor(Math.random() * 10));
    return out.replace(/^0/, "1");
  }
  function randomHex(len) {
    const bytes = new Uint8Array(Math.ceil(len / 2));
    crypto.getRandomValues(bytes);
    return Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("").slice(0, len);
  }
  function flowTrace() {
    return `04-${randomHex(32)}-${randomHex(16)}-01`;
  }
  function cookieValue(name) {
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const m = document.cookie.match(new RegExp(`(?:^|; )${escaped}=([^;]*)`));
    return m ? decodeURIComponent(m[1]) : "";
  }
  function storageFind(regex) {
    const stores = [localStorage, sessionStorage];
    for (const store of stores) {
      for (let i = 0; i < store.length; i += 1) {
        const key = store.key(i);
        const value = store.getItem(key) || "";
        if (regex.test(key) && value && value.length < 100) return value;
      }
    }
    return "";
  }
  function buildQuery() {
    const fp = cookieValue("s_v_web_id") || storageFind(/s_v_web_id|fp|verify/i) || `verify_${randomDigits(12)}`;
    const webId = storageFind(/web_id|tea_uuid/i).replace(/\D/g, "").slice(0, 20) || `${Date.now()}${randomDigits(6)}`;
    const deviceId = storageFind(/device_id|inner_did/i).replace(/\D/g, "").slice(0, 20) || webId;
    const region = cookieValue("flow_user_country") || "JP";
    const params = new URLSearchParams({
      aid: "495671",
      device_id: deviceId,
      device_platform: "web",
      fp,
      language: "zh",
      pc_version: "3.25.1",
      pkg_type: "release_version",
      real_aid: "495671",
      region,
      samantha_web: "1",
      sys_region: region,
      tea_uuid: webId,
      "use-olympus-account": "1",
      version_code: "20800",
      web_id: webId,
      web_platform: "browser",
      web_tab_id: uuid()
    });
    const msToken = cookieValue("msToken") || storageFind(/mstoken/i);
    if (msToken) params.set("msToken", msToken);
    return params;
  }
  function trySign(url) {
    const signers = [window.byted_acrawler, window.bytedAcrawler, window.__acrawler, window.ABogus].filter(Boolean);
    for (const signer of signers) {
      try {
        if (typeof signer.sign === "function") {
          const signed = signer.sign({ url });
          if (typeof signed === "string" && signed) return signed;
          if (signed && typeof signed === "object") {
            if (typeof signed.a_bogus === "string") return signed.a_bogus;
            if (typeof signed.aBogus === "string") return signed.aBogus;
            if (typeof signed.url === "string") {
              const parsed = new URL(signed.url, location.origin);
              const v = parsed.searchParams.get("a_bogus");
              if (v) return v;
            }
          }
        }
      } catch (_) {}
    }
    return "";
  }
  const params = buildQuery();
  let requestUrl = `${location.origin}/alice/resource/prepare_upload?${params.toString()}`;
  const aBogus = trySign(requestUrl);
  if (aBogus) {
    params.set("a_bogus", aBogus);
    requestUrl = `${location.origin}/alice/resource/prepare_upload?${params.toString()}`;
  }
  const response = await fetch(requestUrl, {
    method: "POST",
    credentials: "include",
    headers: {
      "accept": "application/json, text/plain, */*",
      "accept-language": "zh-CN,zh;q=0.9",
      "agw-js-conv": "str",
      "content-type": "application/json",
      "x-flow-trace": flowTrace()
    },
    body: JSON.stringify(body)
  });
  const text = await response.text();
  let json = null;
  try { json = text ? JSON.parse(text) : null; } catch (_) {}
  return { ok: response.ok, status: response.status, text, json };
}
"""


SUBMIT_SCRIPT = r"""
async ({prompt, ratio, duration, model, resolution, attachments}) => {
  function uuid() {
    return crypto.randomUUID ? crypto.randomUUID() :
      "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0;
        const v = c === "x" ? r : (r & 0x3 | 0x8);
        return v.toString(16);
      });
  }
  function randomDigits(len) {
    let out = "";
    for (let i = 0; i < len; i += 1) out += String(Math.floor(Math.random() * 10));
    return out.replace(/^0/, "1");
  }
  function randomHex(len) {
    const bytes = new Uint8Array(Math.ceil(len / 2));
    crypto.getRandomValues(bytes);
    return Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("").slice(0, len);
  }
  function flowTrace() {
    return `04-${randomHex(32)}-${randomHex(16)}-01`;
  }
  function cookieValue(name) {
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const m = document.cookie.match(new RegExp(`(?:^|; )${escaped}=([^;]*)`));
    return m ? decodeURIComponent(m[1]) : "";
  }
  function storageFind(regex) {
    const stores = [localStorage, sessionStorage];
    for (const store of stores) {
      for (let i = 0; i < store.length; i += 1) {
        const key = store.key(i);
        const value = store.getItem(key) || "";
        if (regex.test(key) && value && value.length < 100) return value;
      }
    }
    return "";
  }
  function buildQuery() {
    const fp = cookieValue("s_v_web_id") || storageFind(/s_v_web_id|fp|verify/i) || `verify_${randomDigits(12)}`;
    const webId = storageFind(/web_id|tea_uuid/i).replace(/\D/g, "").slice(0, 20) || `${Date.now()}${randomDigits(6)}`;
    const deviceId = storageFind(/device_id|inner_did/i).replace(/\D/g, "").slice(0, 20) || webId;
    const region = cookieValue("flow_user_country") || "JP";
    const params = new URLSearchParams({
      aid: "495671",
      device_id: deviceId,
      device_platform: "web",
      fp,
      language: "zh",
      pc_version: "3.25.1",
      pkg_type: "release_version",
      real_aid: "495671",
      region,
      samantha_web: "1",
      sys_region: region,
      tea_uuid: webId,
      "use-olympus-account": "1",
      version_code: "20800",
      web_id: webId,
      web_platform: "browser",
      web_tab_id: uuid()
    });
    const msToken = cookieValue("msToken") || storageFind(/mstoken/i);
    if (msToken) params.set("msToken", msToken);
    return params;
  }
  function trySign(url) {
    const signers = [window.byted_acrawler, window.bytedAcrawler, window.__acrawler, window.ABogus].filter(Boolean);
    for (const signer of signers) {
      try {
        if (typeof signer.sign === "function") {
          const signed = signer.sign({ url });
          if (typeof signed === "string" && signed) return signed;
          if (signed && typeof signed === "object") {
            if (typeof signed.a_bogus === "string") return signed.a_bogus;
            if (typeof signed.aBogus === "string") return signed.aBogus;
            if (typeof signed.url === "string") {
              const parsed = new URL(signed.url, location.origin);
              const v = parsed.searchParams.get("a_bogus");
              if (v) return v;
            }
          }
        }
      } catch (_) {}
    }
    return "";
  }
  function extractConversationId(text) {
    if (!text) return "";
    const patterns = [
      /"conversation_id"\s*:\s*"(\d{17})"/,
      /conversation_id(?:\\?"|)\s*[:=]\s*(?:\\?")?(\d{17})/,
      /\/chat\/(\d{17})(?:\D|$)/
    ];
    for (const re of patterns) {
      const m = text.match(re);
      if (m) return m[1];
    }
    return "";
  }
  function buildPayload({localConversationId}) {
    const collectionId = uuid();
    const uniqueKey = uuid();
    const textParts = [prompt];
    if (ratio) textParts.push(ratio);
    const text = `\u751f\u6210\u89c6\u9891\uff1a${textParts.filter(Boolean).join("\uff0c")}`;
    const messages = [];
    if (attachments && attachments.length) {
      messages.push({
        local_message_id: uuid(),
        content_block: [{
          block_type: 10052,
          content: {
            attachment_block: {
              attachments: attachments.map(item => ({
                type: 1,
                identifier: item.identifier || uuid(),
                image: {
                  name: item.name || "image.png",
                  uri: item.uri,
                  image_ori: {
                    url: "",
                    width: Number(item.width || 0),
                    height: Number(item.height || 0),
                    format: "",
                    url_formats: {}
                  }
                },
                parse_state: 0,
                review_state: 1,
                upload_status: 1,
                progress: 100,
                src: ""
              }))
            },
            pc_event_block: ""
          },
          block_id: uuid(),
          parent_id: "",
          meta_info: [],
          append_fields: []
        }],
        message_status: 0
      });
    }
    messages.push({
      local_message_id: uuid(),
      content_block: [{
        block_type: 10000,
        content: {
          text_block: { text, icon_url: "", icon_url_dark: "", summary: "" },
          pc_event_block: ""
        },
        block_id: uuid(),
        parent_id: "",
        meta_info: [],
        append_fields: []
      }],
      message_status: 0
    });
    const fp = cookieValue("s_v_web_id") || storageFind(/s_v_web_id|fp|verify/i) || "";
    return {
      client_meta: {
        local_conversation_id: localConversationId,
        conversation_id: "",
        bot_id: "7339470689562525703",
        last_section_id: "",
        last_message_index: null
      },
      messages,
      option: {
        send_message_scene: "",
        create_time_ms: Date.now(),
        collect_id: collectionId,
        is_audio: false,
        answer_with_suggest: false,
        tts_switch: false,
        need_deep_think: 0,
        click_clear_context: false,
        from_suggest: false,
        is_regen: false,
        is_replace: false,
        is_from_click_option: false,
        is_from_click_softlink: false,
        disable_sse_cache: false,
        select_text_action: "",
        is_select_text: false,
        resend_for_regen: false,
        scene_type: 0,
        unique_key: uniqueKey,
        start_seq: 0,
        need_create_conversation: true,
        conversation_init_option: { need_ack_conversation: true },
        regen_query_id: [],
        edit_query_id: [],
        regen_instruction: "",
        no_replace_for_regen: false,
        message_from: 0,
        shared_app_name: "",
        shared_app_id: "",
        sse_recv_event_options: { support_chunk_delta: true },
        is_ai_playground: false,
        is_old_user: false,
        recovery_option: {
          is_recovery: false,
          req_create_time_sec: Math.floor(Date.now() / 1000),
          append_sse_event_scene: 0
        },
        message_storage_type: 0
      },
      chat_ability: {
        ability_type: 17,
        ability_param: JSON.stringify({ ratio, model: "seedance_v2.0", duration: Number(duration) })
      },
      user_context: [],
      ext: {
        answer_with_suggest: "0",
        fp,
        sub_conv_firstmet_type: "1",
        collection_id: collectionId,
        conversation_init_option: JSON.stringify({ need_ack_conversation: true }),
        commerce_credit_config_enable: "0"
      }
    };
  }
  const localConversationId = `local_${randomDigits(16)}`;
  history.pushState({}, "", `/chat/${localConversationId}`);
  const params = buildQuery();
  let requestUrl = `${location.origin}/chat/completion?${params.toString()}`;
  const aBogus = trySign(requestUrl);
  if (aBogus) {
    params.set("a_bogus", aBogus);
    requestUrl = `${location.origin}/chat/completion?${params.toString()}`;
  }
  const response = await fetch(requestUrl, {
    method: "POST",
    credentials: "include",
    headers: {
      "accept": "*/*",
      "accept-language": "zh-CN,zh;q=0.9",
      "agw-js-conv": "str, str",
      "content-type": "application/json",
      "last-event-id": "undefined",
      "x-flow-trace": flowTrace()
    },
    body: JSON.stringify(buildPayload({localConversationId}))
  });
  let text = "";
  let serviceFrequent = false;
  let timedOut = false;
  const reader = response.body && response.body.getReader ? response.body.getReader() : null;
  if (reader) {
    const decoder = new TextDecoder("utf-8");
    const deadline = Date.now() + 30000;
    for (;;) {
      const remain = Math.max(1, deadline - Date.now());
      const timer = new Promise(resolve => setTimeout(() => resolve({timeout: true}), remain));
      const item = await Promise.race([reader.read(), timer]);
      if (item.timeout) {
        timedOut = true;
        break;
      }
      const {done, value} = item;
      if (done) break;
      const chunk = decoder.decode(value, {stream: true});
      text += chunk;
      serviceFrequent = text.includes("鏈嶅姟璁块棶棰戠箒") || text.includes("褰撳墠鏈嶅姟璁块棶棰戠箒");
    }
    try { await reader.cancel(); } catch (_) {}
    try { text += decoder.decode(); } catch (_) {}
  } else {
    text = await response.text();
  }
  serviceFrequent = serviceFrequent || text.includes("鏈嶅姟璁块棶棰戠箒") || text.includes("褰撳墠鏈嶅姟璁块棶棰戠箒") || text.includes("710022002");
  const countryRestricted = text.includes("country restricted");
  const conversationId = extractConversationId(text);
  const requestParams = Object.fromEntries(params.entries());
  return {
    status: response.status,
    contentType: response.headers.get("content-type") || "",
    responseBytes: text.length,
    sse_response_text: text.slice(0, 200000),
    sse_response_preview: text.slice(0, 4000),
    conversation_id: conversationId,
    device_id: requestParams.device_id || "",
    web_id: requestParams.web_id || "",
    tea_uuid: requestParams.tea_uuid || "",
    region: requestParams.region || "",
    sys_region: requestParams.sys_region || "",
    web_tab_id: requestParams.web_tab_id || "",
    sse_timed_out: timedOut,
    service_frequent: serviceFrequent,
    country_restricted: countryRestricted,
    location_href: location.href
  };
}
"""


def _random_base36(length: int = 11) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _mime_from_path(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "image/png"


def _file_extension_for_upload(path: Path) -> str:
    return path.suffix.lower() or ".png"


def _sha256_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _hmac_sha256(key: str | bytes, value: str, *, hex_digest: bool = False) -> str | bytes:
    if isinstance(key, str):
        key = key.encode("utf-8")
    digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest() if hex_digest else digest.digest()


def _aws_encode(value: str) -> str:
    return quote(str(value), safe="-_.~")


def _canonical_query_string(raw_url: str) -> str:
    parsed = urlsplit(raw_url)
    pairs = [(_aws_encode(key), _aws_encode(value)) for key, value in parse_qsl(parsed.query, keep_blank_values=True)]
    pairs.sort()
    return "&".join(f"{key}={value}" for key, value in pairs)


def _amz_date_parts() -> tuple[str, str]:
    amz_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return amz_date, amz_date[:8]


def _normalize_upload_credentials(token: dict[str, Any]) -> dict[str, str]:
    credentials = {
        "access_key_id": token.get("access_key") or token.get("accessKeyId") or token.get("AccessKeyId") or token.get("AccessKeyID"),
        "secret_access_key": token.get("secret_key") or token.get("secretAccessKey") or token.get("SecretAccessKey"),
        "session_token": token.get("session_token") or token.get("sessionToken") or token.get("SessionToken"),
    }
    if not all(credentials.values()):
        raise RuntimeError("prepare_upload did not return complete upload credentials")
    return {key: str(value) for key, value in credentials.items()}


def _sign_imagex_request(
    *,
    method: str,
    raw_url: str,
    credentials: dict[str, str],
    body: str = "",
    include_payload_hash: bool = False,
) -> dict[str, str]:
    parsed = urlsplit(raw_url)
    amz_date, date_stamp = _amz_date_parts()
    payload_hash = _sha256_hex(body)
    canonical_headers_map = {
        "x-amz-date": amz_date,
        "x-amz-security-token": credentials["session_token"],
    }
    if include_payload_hash:
        canonical_headers_map["x-amz-content-sha256"] = payload_hash

    signed_header_names = sorted(canonical_headers_map)
    canonical_headers = "".join(
        f"{name}:{' '.join(str(canonical_headers_map[name]).strip().split())}\n"
        for name in signed_header_names
    )
    signed_headers = ";".join(signed_header_names)
    canonical_request = "\n".join(
        [
            method.upper(),
            parsed.path or "/",
            _canonical_query_string(raw_url),
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{date_stamp}/{IMAGEX_REGION}/{IMAGEX_SERVICE}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            _sha256_hex(canonical_request),
        ]
    )
    date_key = _hmac_sha256(f"AWS4{credentials['secret_access_key']}", date_stamp)
    region_key = _hmac_sha256(date_key, IMAGEX_REGION)
    service_key = _hmac_sha256(region_key, IMAGEX_SERVICE)
    signing_key = _hmac_sha256(service_key, "aws4_request")
    signature = _hmac_sha256(signing_key, string_to_sign, hex_digest=True)
    headers = {
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={credentials['access_key_id']}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
        "X-Amz-Date": amz_date,
        "x-amz-security-token": credentials["session_token"],
    }
    if include_payload_hash:
        headers["X-Amz-Content-Sha256"] = payload_hash
    return headers


def _json_compact(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


async def _fetch_json(client: httpx.AsyncClient, url: str, *, label: str, **kwargs: Any) -> tuple[dict[str, Any], httpx.Response]:
    response = await client.request(url=url, **kwargs)
    text = response.content.decode("utf-8-sig", errors="replace")
    try:
        data = json.loads(text) if text else {}
    except Exception as exc:
        raise RuntimeError(f"{label} returned non-json response: {text[:500]}") from exc
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"{label} failed with HTTP {response.status_code}: {text[:500]}")
    return data, response


class DolaFetchAutomation:
    def __init__(
        self,
        task_id: str,
        prompt: str,
        ratio: str,
        *,
        duration: int | None = None,
        model: str = "",
        resolution: str = "",
    ):
        self.task_id = task_id
        self.prompt = prompt
        self.ratio = ratio
        self.settings = load_settings()
        self.duration = duration or self.settings.video_duration
        self.model = normalize_video_model(model or self.settings.video_model)
        self.resolution = resolution
        self.uploaded_images: list[dict[str, Any]] = []

    async def run(self) -> bool:
        try:
            return await asyncio.wait_for(self._run_once(), timeout=self.settings.task_timeout_seconds)
        except asyncio.TimeoutError:
            mark_pending(self.task_id, "browser timeout")
            return False
        except Exception as exc:
            mark_pending(self.task_id, str(exc)[:500])
            return False

    async def _run_once(self) -> bool:
        clear_transient_result(self.task_id)
        async with async_playwright() as playwright:
            browser: Browser | None = None
            context: BrowserContext | None = None
            try:
                executable_path = self._browser_executable_path()
                proxy_config = await self._browser_proxy_config()
                browser = await playwright.chromium.launch(
                    headless=self.settings.headless,
                    executable_path=executable_path,
                    proxy=proxy_config,
                    args=[
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                context = await browser.new_context(
                    locale="zh-CN",
                    viewport={"width": 1365, "height": 900},
                    user_agent=BROWSER_USER_AGENT,
                    extra_http_headers=BROWSER_EXTRA_HTTP_HEADERS,
                    accept_downloads=False,
                )
                await context.add_init_script(BROWSER_INIT_SCRIPT)
                page = await context.new_page()
                await self._prepare_page(page)
                await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
                try:
                    await page.wait_for_function("document.readyState === 'complete'", timeout=20000)
                except Exception:
                    pass
                await page.wait_for_timeout(5000)
                if self._is_region_restricted(page.url):
                    mark_pending(self.task_id, "region restricted")
                    return False

                attachments = await self._upload_images_if_needed(page)
                result = await page.evaluate(
                    SUBMIT_SCRIPT,
                    {
                        "prompt": self.prompt,
                        "ratio": self.ratio,
                        "duration": self.duration,
                        "model": self.model,
                        "resolution": self.resolution,
                        "attachments": attachments,
                    },
                )
                cookies = await context.cookies()
                cookie_string = "; ".join(f"{item['name']}={item['value']}" for item in cookies if item.get("name"))
                save_result(
                    self.task_id,
                    conversation_id=str(result.get("conversation_id") or ""),
                    cookie_string=cookie_string,
                    extra={
                        "chat_status": result.get("status"),
                        "chat_content_type": result.get("contentType"),
                        "chat_response_bytes": int(result.get("responseBytes") or 0),
                        "sse_response_text": str(result.get("sse_response_text") or ""),
                        "chat_response_preview": str(result.get("sse_response_preview") or "")[:4000],
                        "sse_timed_out": bool(result.get("sse_timed_out")),
                        "device_id": result.get("device_id") or "",
                        "web_id": result.get("web_id") or "",
                        "tea_uuid": result.get("tea_uuid") or "",
                        "region": result.get("region") or "",
                        "sys_region": result.get("sys_region") or "",
                        "web_tab_id": result.get("web_tab_id") or "",
                    },
                )
                if result.get("service_frequent"):
                    mark_pending(self.task_id, "service frequent")
                    return False
                if result.get("country_restricted"):
                    mark_pending(self.task_id, "country restricted")
                    return False
                if self._is_region_restricted(str(result.get("location_href") or page.url)):
                    mark_pending(self.task_id, "region restricted")
                    return False
                mark_success(self.task_id)
                return True
            finally:
                if context:
                    await context.close()
                if browser:
                    await browser.close()

    async def _browser_proxy_config(self) -> dict[str, str] | None:
        self.settings = load_settings()
        proxy = await fetch_proxy_from_api(
            self.settings.proxy_api_url,
            timeout_seconds=self.settings.proxy_api_timeout_seconds,
            scheme=self.settings.proxy_api_scheme,
        )
        save_result(
            self.task_id,
            extra={
                "proxy_source": "api",
                "proxy_server": proxy["server"],
            },
        )
        return browser_proxy_config_for(proxy["server"], default_scheme=self.settings.proxy_api_scheme)

    async def _prepare_page(self, page: Page) -> None:
        await page.route("**/*", self._route_handler)

    async def _route_handler(self, route, request) -> None:
        url = request.url.lower()
        if ".jpeg" in url or ".jpg" in url:
            if self._is_blocked_jpeg(url):
                await route.abort()
                return
        await route.continue_()

    @staticmethod
    def _is_blocked_jpeg(url: str) -> bool:
        return ".jpeg~" in url or ".jpeg?" in url or url.endswith(".jpeg") or ".jpg~" in url or ".jpg?" in url or url.endswith(".jpg")

    async def _prepare_image_upload(self, page: Page) -> dict[str, Any]:
        result = await page.evaluate(PREPARE_UPLOAD_SCRIPT, {"body": PREPARE_UPLOAD_BODY})
        if not isinstance(result, dict):
            raise RuntimeError("prepare_upload returned invalid response")
        if not result.get("ok"):
            raise RuntimeError(f"prepare_upload failed with HTTP {result.get('status')}: {str(result.get('text') or '')[:500]}")
        data = result.get("json")
        if not isinstance(data, dict) or data.get("code") != 0:
            raise RuntimeError(f"prepare_upload returned unexpected body: {str(result.get('text') or data)[:500]}")
        upload_config = data.get("data")
        if not isinstance(upload_config, dict):
            raise RuntimeError("prepare_upload did not return upload config")
        return upload_config

    async def _upload_one_image_by_fetch(self, page: Page, image_path: Path) -> dict[str, Any]:
        buffer = image_path.read_bytes()
        file_name = image_path.name
        ext = _file_extension_for_upload(image_path)
        mime = _mime_from_path(image_path)
        upload_config = await self._prepare_image_upload(page)
        credentials = _normalize_upload_credentials(upload_config.get("upload_auth_token") or {})
        service_id = str(upload_config.get("service_id") or "")
        imagex_host = str(upload_config.get("upload_host") or "imagex-ap-southeast-1.bytevcloudapi.com")
        if not service_id:
            raise RuntimeError("prepare_upload did not return service_id")

        timeout = httpx.Timeout(90.0, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            apply_params = {
                "Action": "ApplyImageUpload",
                "Version": IMAGEX_API_VERSION,
                "ServiceId": service_id,
                "FileSize": str(len(buffer)),
                "FileExtension": ext,
                "s": _random_base36(),
            }
            apply_url = f"https://{imagex_host}/?{urlencode(apply_params)}"
            apply_headers = {
                "Accept": "*/*",
                **_sign_imagex_request(method="GET", raw_url=apply_url, credentials=credentials),
            }
            apply_data, _ = await _fetch_json(
                client,
                apply_url,
                label="ApplyImageUpload",
                method="GET",
                headers=apply_headers,
            )

            upload_address = (((apply_data or {}).get("Result") or {}).get("UploadAddress") or {})
            store_infos = upload_address.get("StoreInfos") or []
            upload_hosts = upload_address.get("UploadHosts") or []
            store_info = store_infos[0] if store_infos and isinstance(store_infos[0], dict) else {}
            upload_host = str(upload_hosts[0]) if upload_hosts else ""
            session_key = str(upload_address.get("SessionKey") or "")
            store_uri = str(store_info.get("StoreUri") or "")
            store_auth = str(store_info.get("Auth") or "")
            if not store_uri or not store_auth or not upload_host or not session_key:
                raise RuntimeError("ApplyImageUpload did not return a complete upload address")

            upload_headers = {
                "Authorization": store_auth,
                "Content-CRC32": f"{binascii.crc32(buffer) & 0xffffffff:08x}",
                "Content-Disposition": f'attachment; filename="{file_name.replace(chr(34), "")}"',
                "Content-Type": "application/octet-stream",
            }
            if isinstance(upload_address.get("UploadHeader"), dict):
                upload_headers.update({str(key): str(value) for key, value in upload_address["UploadHeader"].items()})
            upload_url = f"https://{upload_host}/upload/v1/{store_uri}"
            upload_data, upload_response = await _fetch_json(
                client,
                upload_url,
                label="direct image upload",
                method="POST",
                headers=upload_headers,
                content=buffer,
            )
            if upload_data.get("code") != 2000:
                raise RuntimeError(f"direct image upload returned unexpected body: {json.dumps(upload_data, ensure_ascii=False)[:500]}")

            commit_url = f"https://{imagex_host}/?{urlencode({'Action': 'CommitImageUpload', 'Version': IMAGEX_API_VERSION, 'ServiceId': service_id})}"
            commit_body = _json_compact({"SessionKey": session_key})
            commit_headers = {
                "Accept": "*/*",
                "Content-Type": "application/json",
                **_sign_imagex_request(
                    method="POST",
                    raw_url=commit_url,
                    credentials=credentials,
                    body=commit_body,
                    include_payload_hash=True,
                ),
            }
            commit_data, _ = await _fetch_json(
                client,
                commit_url,
                label="CommitImageUpload",
                method="POST",
                headers=commit_headers,
                content=commit_body,
            )

        result = (commit_data or {}).get("Result") or {}
        results = result.get("Results") if isinstance(result, dict) else []
        plugins = result.get("PluginResult") if isinstance(result, dict) else []
        first_result = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else {}
        plugin = plugins[0] if isinstance(plugins, list) and plugins and isinstance(plugins[0], dict) else {}
        uri = str(first_result.get("Uri") or "")
        if not uri:
            raise RuntimeError(f"CommitImageUpload did not return image uri: {json.dumps(commit_data, ensure_ascii=False)[:500]}")
        return {
            "uri": uri,
            "name": plugin.get("FileName") or Path(uri).name or file_name,
            "width": plugin.get("ImageWidth") or 0,
            "height": plugin.get("ImageHeight") or 0,
            "size": plugin.get("ImageSize") or len(buffer),
            "mime": mime,
            "uploadStatus": upload_response.status_code,
        }

    async def _upload_images_if_needed(self, page: Page) -> list[dict[str, Any]]:
        paths = task_image_paths(self.task_id)
        if not paths:
            return []
        images: list[dict[str, Any]] = []
        for path in paths:
            images.append(await self._upload_one_image_by_fetch(page, path))
        self.uploaded_images = self._unique_images(images)
        if len(self.uploaded_images) < len(paths):
            raise RuntimeError("image upload did not return uri")
        return self.uploaded_images[: len(paths)]

    @staticmethod
    def _unique_images(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for item in items:
            uri = str(item.get("uri") or "")
            if not uri or uri in seen:
                continue
            seen.add(uri)
            out.append(item)
        return out

    @staticmethod
    def _is_region_restricted(url: str) -> bool:
        return url.startswith(REGION_RESTRICTED_URL)

    def _browser_executable_path(self) -> str | None:
        if self.settings.browser_executable_path:
            return self.settings.browser_executable_path
        for candidate in (
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
        ):
            if Path(candidate).exists():
                return candidate
        return None
