# DFYue Fetch API 文档

本文档面向调用方，说明如何通过 HTTP API 提交生视频任务、查询任务状态、下载视频文件，以及如何使用 OpenAI 兼容接口接入第三方平台。

## 基础信息

- Base URL：`https://你的域名`
- 默认模型名：`seedance_v2.0`
- 默认管理员 Token：`dfyue-video-fixed-token`
- 推荐认证方式：`Authorization: Bearer <TOKEN>`
- 兼容认证方式：`X-API-Token: <TOKEN>` 或 URL 参数 `?token=<TOKEN>`

生产环境建议在 Zeabur 环境变量中设置 `DOLA_API_TOKEN` 或 `API_TOKEN` 来覆盖默认管理员 Token。

## VividAI / OpenAI compatible paths

The service accepts VividAI-style OpenAI calls:

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/v1/models` | Returns the real model configured for this service, for example `seedance_v2.0`. |
| `POST` | `/v1/images/generations` | Creates a video task through the image-generation compatible endpoint. `data[0].url` is the video content URL when ready. |
| `POST` | `/v1/images/edits` | Accepts multipart reference images with `image`, `image[]`, `input_reference`, or URL/base64 fields. |
| `POST` | `/v1/videos` | Creates an async video task and returns `id` / `status`. |
| `GET` | `/v1/videos/{id}` | Queries task status. |
| `GET` | `/v1/videos/{id}/content` | Streams the generated MP4 when status is completed. |

`size` values such as `1280x720`, `720x1280`, and `2048x2048` are automatically mapped to the closest supported video ratio. `seconds` is accepted for compatibility but current generation duration is controlled by the upstream video service.

Video parameter mapping:

| Input field | Effect |
| --- | --- |
| `ratio` / `aspect_ratio` | Directly sets the video ratio when it is one of the supported ratios. |
| `size` / `resolution` | Accepts values such as `1280x720`, maps to ratio, and is saved as task resolution. |
| `width` + `height` | Builds a resolution value such as `1280x720`. |
| `seconds` / `duration` / `duration_seconds` / `video_duration` | Sets per-task video duration, clamped to 1-60 seconds. |
| `@alias` in prompt | Binds a fixed reference image from `data/reference_images/alias.png` or `data/references/alias.png`. |
| `reference_alias` / `fixed_image` / `bind_image` | Explicitly binds a fixed reference image without putting `@alias` in the prompt. |

## Token 权限

| Token 类型 | 用途 | 可调用范围 |
| --- | --- | --- |
| 管理员 Token | 管理后台、创建临时 Token、配置代理、在线更新、调用视频 API | 全部接口 |
| 临时 Token | 给客户调用生视频 API | 只能访问自己的任务，受额度限制 |

临时 Token 调用视频生成接口时会消耗额度。任务创建失败时会自动退回额度。

## 通用错误

| HTTP 状态码 | 含义 |
| --- | --- |
| 400 | 参数错误，例如缺少 prompt、ratio 不合法、图片数量超限 |
| 403 | Token 错误或权限不足 |
| 404 | 任务不存在，或临时 Token 访问了不属于自己的任务 |
| 409 | 视频未生成完成，暂时不能下载 |
| 429 | 临时 Token 额度已用完 |
| 501 | OpenAI 兼容路径存在但当前服务不支持该能力 |

## 支持参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `model` | string | `seedance_v2.0` | 兼容 OpenAI 平台填写用；服务只对外暴露真实上游模型 `seedance_v2.0` |
| `input` / `prompt` / `text` | string | 无 | 视频提示词 |
| `ratio` / `aspect_ratio` | string | `9:16` | 支持 `1:1`、`3:4`、`4:3`、`9:16`、`16:9`、`21:9` |
| `wait` | boolean | `false` | 是否等待视频完成后再返回 |
| `timeout_seconds` / `max_wait_seconds` | integer | `240` | `wait=true` 时最长等待秒数 |
| `image` / `images` / `image_url` / `image_urls` | file/string/array | 无 | 参考图，最多 9 张 |

参考图支持三种传法：multipart 文件、图片 URL、base64 / data URL。

## OpenAI 兼容接口

### 1. 查询模型列表

```bash
curl "https://你的域名/v1/models" \
  -H "Authorization: Bearer dfyue-video-fixed-token"
```

兼容路径：`/models`、`/v1/v1/models`。如果上游平台会自动拼接 `/v1`，API 地址填域名即可；如果误填了带 `/v1` 的地址，兼容路径也能返回模型。

响应示例：

```json
{
  "object": "list",
  "data": [
    {
      "id": "seedance_v2.0",
      "object": "model",
      "created": 0,
      "owned_by": "dola",
      "root": "seedance_v2.0",
      "parent": null,
      "permission": []
    }
  ]
}
```

### 2. 创建视频任务，立即返回任务 ID

```bash
curl -X POST "https://你的域名/v1/responses" \
  -H "Authorization: Bearer dfyue-video-fixed-token" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "seedance_v2.0",
    "input": "一个人在跑步健身，电影感，竖屏",
    "ratio": "9:16",
    "wait": false
  }'
```

响应示例：

```json
{
  "id": "0123456789abcdef0123456789abcdef",
  "object": "response",
  "status": "queued",
  "model": "seedance_v2.0",
  "output": [
    {
      "type": "message",
      "role": "assistant",
      "content": [
        {
          "type": "output_text",
          "text": "video task 0123456789abcdef0123456789abcdef is queued"
        }
      ]
    }
  ],
  "video": {
    "id": "0123456789abcdef0123456789abcdef",
    "status": "queued",
    "url": "",
    "content_url": ""
  }
}
```

### 3. 创建视频任务并等待完成

适合没有“查询任务”功能的平台。平台请求超时时间必须大于 `timeout_seconds`，否则可能中途断开。

```bash
curl -X POST "https://你的域名/v1/responses" \
  -H "Authorization: Bearer dfyue-video-fixed-token" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "seedance_v2.0",
    "input": "一个人在海边散步，日落，真实摄影风格",
    "ratio": "16:9",
    "wait": true,
    "timeout_seconds": 240
  }'
```

完成后的响应里会返回视频下载地址：

```json
{
  "id": "0123456789abcdef0123456789abcdef",
  "object": "response",
  "status": "completed",
  "model": "seedance_v2.0",
  "output": [
    {
      "type": "message",
      "role": "assistant",
      "content": [
        {
          "type": "output_text",
          "text": "https://你的域名/v1/videos/0123456789abcdef0123456789abcdef/content"
        }
      ]
    }
  ],
  "video": {
    "id": "0123456789abcdef0123456789abcdef",
    "status": "completed",
    "url": "https://你的域名/v1/videos/0123456789abcdef0123456789abcdef/content",
    "content_url": "https://你的域名/v1/videos/0123456789abcdef0123456789abcdef/content"
  }
}
```

### 4. 查询任务状态

```bash
curl "https://你的域名/v1/responses/0123456789abcdef0123456789abcdef" \
  -H "Authorization: Bearer dfyue-video-fixed-token"
```

状态说明：

| status | 含义 |
| --- | --- |
| `queued` | 已提交，等待执行 |
| `in_progress` | 正在生成 |
| `completed` | 已完成，可下载 |
| `failed` | 生成失败 |

### 5. 下载视频文件

接口路径：`GET /v1/videos/{video_id}/content`

`ash
curl -L "https://你的域名/v1/videos/0123456789abcdef0123456789abcdef/content" \
  -H "Authorization: Bearer dfyue-video-fixed-token" \
  -o generated_video.mp4
```

下载接口会返回 `302` 跳转到真实视频地址，调用方需要允许跟随重定向。

## 参考图生视频

### multipart 上传图片

```bash
curl -X POST "https://你的域名/v1/responses" \
  -H "Authorization: Bearer dfyue-video-fixed-token" \
  -F "model=seedance_v2.0" \
  -F "input=让参考图中的人物在城市街头行走" \
  -F "ratio=9:16" \
  -F "wait=false" \
  -F "images=@./reference-1.png" \
  -F "images=@./reference-2.jpg"
```

### JSON 传图片 URL

```bash
curl -X POST "https://你的域名/v1/responses" \
  -H "Authorization: Bearer dfyue-video-fixed-token" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "seedance_v2.0",
    "input": "让参考图中的人物转身看向镜头",
    "ratio": "9:16",
    "image_urls": [
      "https://example.com/reference-1.png",
      "https://example.com/reference-2.jpg"
    ]
  }'
```

### OpenAI 内容数组写法

```bash
curl -X POST "https://你的域名/v1/responses" \
  -H "Authorization: Bearer dfyue-video-fixed-token" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "seedance_v2.0",
    "input": [
      {"type": "input_text", "text": "让参考图中的人物微笑挥手"},
      {"type": "input_image", "image_url": "https://example.com/person.png"}
    ],
    "ratio": "9:16"
  }'
```

## Chat Completions 兼容接口

如果第三方平台只支持 `/v1/chat/completions`，可以这样调用：

```bash
curl -X POST "https://你的域名/v1/chat/completions" \
  -H "Authorization: Bearer dfyue-video-fixed-token" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "seedance_v2.0",
    "messages": [
      {"role": "user", "content": "一个人在跑步健身，竖屏"}
    ],
    "ratio": "9:16",
    "wait": false
  }'
```

返回的 `choices[0].message.content` 会包含任务状态或视频地址。

## Images Generations 兼容接口

部分平台只支持图片生成接口，也可以接入：

```bash
curl -X POST "https://你的域名/v1/images/generations" \
  -H "Authorization: Bearer dfyue-video-fixed-token" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "seedance_v2.0",
    "prompt": "一只机器人在霓虹街道跳舞",
    "ratio": "16:9",
    "wait": true
  }'
```

返回的 `data[0].url` 是视频下载接口地址，不是图片地址。

## 原生任务接口

### 提交任务

```bash
curl -X POST "https://你的域名/tasks" \
  -H "Authorization: Bearer dfyue-video-fixed-token" \
  -F "prompt=一个人在跑步健身" \
  -F "ratio=9:16" \
  -F "images=@./reference.png"
```

响应：

```json
{
  "id": "0123456789abcdef0123456789abcdef"
}
```

### 查询任务

```bash
curl "https://你的域名/tasks/0123456789abcdef0123456789abcdef" \
  -H "Authorization: Bearer dfyue-video-fixed-token"
```

后端任务结果字段：

| 字段 | 含义 |
| --- | --- |
| `code` | `0` 排队或生成中，`1` 失败，`2` 成功 |
| `text` | 状态文本或错误文本 |
| `url` | 成功后的视频原始地址 |

### 查看任务列表

```bash
curl "https://你的域名/tasks" \
  -H "Authorization: Bearer dfyue-video-fixed-token"
```

## 临时 Token 管理

临时 Token 只能由管理员 Token 创建。

### 创建临时 Token

```bash
curl -X POST "https://你的域名/temp-tokens" \
  -H "Authorization: Bearer dfyue-video-fixed-token" \
  -H "Content-Type: application/json" \
  -d '{"count": 3, "limit": 100}'
```

响应示例：

```json
{
  "tokens": [
    {
      "id": "token_hash",
      "token": "tmp_xxx",
      "limit": 100,
      "used": 0,
      "remaining": 100,
      "created_at": "2026-06-30T00:00:00+00:00"
    }
  ]
}
```

### 查看临时 Token

```bash
curl "https://你的域名/temp-tokens" \
  -H "Authorization: Bearer dfyue-video-fixed-token"
```

### 修改临时 Token 额度

```bash
curl -X PATCH "https://你的域名/temp-tokens/token_hash" \
  -H "Authorization: Bearer dfyue-video-fixed-token" \
  -H "Content-Type: application/json" \
  -d '{"limit": 200}'
```

### 删除临时 Token

```bash
curl -X DELETE "https://你的域名/temp-tokens/token_hash" \
  -H "Authorization: Bearer dfyue-video-fixed-token"
```

## 管理员接口

以下接口只允许管理员 Token 调用。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/config/proxy-api` | 查看代理提取 API 配置 |
| `POST` / `PUT` / `PATCH` | `/config/proxy-api` | 修改代理提取 API 配置 |
| `GET` | `/admin/update/status` | 查看当前 Git 版本状态 |
| `GET` | `/admin/update/status?check_remote=true` | 检查 GitHub 是否有更新 |
| `POST` | `/admin/update` | 拉取 GitHub 最新代码并重启 |

在线更新示例：

```bash
curl -X POST "https://你的域名/admin/update" \
  -H "Authorization: Bearer dfyue-video-fixed-token" \
  -H "Content-Type: application/json" \
  -d '{"branch":"main","restart":true}'
```

## Python 示例

```python
import time
import requests

BASE_URL = "https://你的域名"
TOKEN = "dfyue-video-fixed-token"

headers = {"Authorization": f"Bearer {TOKEN}"}

create = requests.post(
    f"{BASE_URL}/v1/responses",
    headers=headers,
    json={
        "model": "seedance_v2.0",
        "input": "一个人在跑步健身，竖屏",
        "ratio": "9:16",
        "wait": False,
    },
    timeout=60,
)
create.raise_for_status()
task_id = create.json()["id"]

while True:
    status = requests.get(f"{BASE_URL}/v1/responses/{task_id}", headers=headers, timeout=30)
    status.raise_for_status()
    data = status.json()
    if data["status"] == "completed":
        video_url = data["video"]["url"]
        break
    if data["status"] == "failed":
        raise RuntimeError(data.get("error") or "video generation failed")
    time.sleep(5)

video = requests.get(video_url, headers=headers, allow_redirects=True, timeout=120)
video.raise_for_status()
open("generated_video.mp4", "wb").write(video.content)
```

## Node.js 示例

```js
const BASE_URL = "https://你的域名";
const TOKEN = "dfyue-video-fixed-token";

async function main() {
  const create = await fetch(`${BASE_URL}/v1/responses`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "seedance_v2.0",
      input: "一个人在跑步健身，竖屏",
      ratio: "9:16",
      wait: false,
    }),
  });
  if (!create.ok) throw new Error(await create.text());
  const task = await create.json();

  for (;;) {
    const res = await fetch(`${BASE_URL}/v1/responses/${task.id}`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    if (data.status === "completed") {
      console.log(data.video.url);
      return;
    }
    if (data.status === "failed") throw new Error(JSON.stringify(data.error));
    await new Promise((resolve) => setTimeout(resolve, 5000));
  }
}

main().catch(console.error);
```

## 第三方平台填写建议

| 配置项 | 填写内容 |
| --- | --- |
| API 地址 / Base URL | `https://你的域名`，不要额外加 `/v1` |
| API Key | 管理员 Token 或临时 Token |
| 模型名称 | `seedance_v2.0` |
| 接口类型 | OpenAI 兼容 |
| 推荐路径 | `/v1/responses` |
| 无查询能力的平台 | 请求体加入 `"wait": true` 和合适的 `timeout_seconds` |

## 注意事项

- 当前服务支持参考图生视频，最多 9 张参考图。
- 当前接口文档没有声明支持参考视频、参考音频输入；调用方不要传视频或音频文件。
- 视频生成是长耗时任务。推荐 `wait=false` 创建任务，再轮询查询结果。
- 如果平台没有查询功能，可以用 `wait=true`，但平台自身必须允许长连接等待。
- 下载视频时请允许 HTTP 302 跳转。
- 临时 Token 只能看到自己创建的任务，管理员 Token 可以管理全部任务。
