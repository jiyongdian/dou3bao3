# dola_fetch_service

这是一个基于 FastAPI + Playwright 的任务服务，带 Web 管理面板、任务队列、代理提取 API 配置、并发控制和视频结果查询能力。

## 一键安装

在 Linux 服务器上使用 `root` 执行。脚本会自动检测依赖，缺少依赖会自动安装；安装源失败时会自动尝试切换常用镜像源；安装成功后会启动服务，并输出面板地址和 API Token。

支持系统：

- Debian / Ubuntu
- CentOS / RHEL / Rocky / AlmaLinux

一键安装：

```bash
bash <(curl -fsSL "https://raw.githubusercontent.com/DaFangYue/dola_fetch_service/main/scripts/install.sh")
```

安装完成后窗口提示：

```text
安装成功
面板地址：http://服务器IP:8088/admin
API Token：xxxxxxxx
```

查看 API Token：

```bash
/opt/dola-fetch-service/scripts/show-token.sh
```

重置 API Token：

```bash
/opt/dola-fetch-service/scripts/reset-token.sh
```

查看服务状态：

```bash
systemctl status dola-fetch-service
```

重启服务：

```bash
systemctl restart dola-fetch-service
```

## 功能

- Web 管理面板：`/admin`
- API Token 登录和接口鉴权
- 支持文本任务和带图任务
- 支持任务列表、查询、删除、清空
- 支持修改并发数量
- 支持修改代理提取 API
- 每个任务使用独立的浏览会话数据
- 任务完成后自动关闭会话并回收内存

## 环境要求

- Python 3.11+
- Linux 服务器，推荐 Debian / Ubuntu
- Playwright 支持的 Chromium 运行依赖

## 本地运行

Linux / macOS：

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python run.py
```

Windows PowerShell：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe run.py
```

本地打开：

```text
http://127.0.0.1:8088/admin
```

## 配置文件

服务器运行配置默认保存在：

```text
/var/lib/dola-fetch-service/config.json
```

可以参考 `config.example.json`：

```json
{
  "api_token": "首次启动自动生成",
  "browser_workers": 2,
  "proxy_api_url": "https://example.com/get-proxy?num=1&type=txt",
  "proxy_api_scheme": "http",
  "proxy_api_timeout_seconds": 20,
  "reclaim_memory_after_task": true,
  "drop_os_cache_when_idle": false
}
```

`proxy_api_url` 应返回单个 `ip:port`。服务会在每个任务启动前直接请求代理提取 API，然后只把这个代理传给 Chromium。

也可以用环境变量指定默认代理提取 API：

```bash
export DOLA_DEFAULT_PROXY_API_URL="https://example.com/get-proxy?num=1&type=txt"
```

## 常用接口

所有接口都需要在请求 Header 中携带：

```text
X-API-Token: <API Token>
```

### GET /health

检查服务状态，返回服务是否可用、并发配置和正在执行的任务 ID。

请求示例：

```bash
curl -H "X-API-Token: $API_TOKEN" http://SERVER_IP:8088/health
```

响应示例：

```json
{"ok":true,"browser_workers":4,"active":[]}
```

### GET /config/proxy-api

读取当前代理提取 API 配置。

请求示例：

```bash
curl -H "X-API-Token: $API_TOKEN" http://SERVER_IP:8088/config/proxy-api
```

响应示例：

```json
{"proxy_api_url":"https://example.com/get","proxy_api_scheme":"http","proxy_api_timeout_seconds":20}
```

### GET /config/workers

读取当前并发配置。

请求示例：

```bash
curl -H "X-API-Token: $API_TOKEN" http://SERVER_IP:8088/config/workers
```

响应示例：

```json
{"browser_workers":4}
```

### POST /config/workers

修改并发数量，范围 1 - 5。

请求示例：

```bash
curl -X POST -H "X-API-Token: $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"browser_workers":4}' \
  http://SERVER_IP:8088/config/workers
```

响应示例：

```json
{"ok":true,"browser_workers":4}
```

### POST /config/proxy-api

修改代理提取 API。

请求示例：

```bash
curl -X POST -H "X-API-Token: $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"proxy_api_url":"https://example.com/get","proxy_api_scheme":"http"}' \
  http://SERVER_IP:8088/config/proxy-api
```

响应示例：

```json
{"ok":true,"proxy_api_url":"https://example.com/get","proxy_api_scheme":"http","proxy_api_timeout_seconds":20}
```

### POST /tasks

提交任务，使用 `multipart/form-data`，字段为 `prompt`、`ratio`、`images`，图片最多按后端配置接收。

请求示例：

```bash
curl -X POST -H "X-API-Token: $API_TOKEN" \
  -F "prompt=一个人在奔跑健身" \
  -F "ratio=9:16" \
  http://SERVER_IP:8088/tasks
```

响应示例：

```json
{"id":"0123456789abcdef0123456789abcdef"}
```

### GET /tasks

获取当前任务列表。

请求示例：

```bash
curl -H "X-API-Token: $API_TOKEN" http://SERVER_IP:8088/tasks
```

响应示例：

```json
{"tasks":[{"id":"0123456789abcdef0123456789abcdef","prompt_preview":"一个人在奔跑健身","status":"success","image_count":0}]}
```

### DELETE /tasks

批量清空未在生成中的任务，生成中的任务会自动保留。

请求示例：

```bash
curl -X DELETE -H "X-API-Token: $API_TOKEN" http://SERVER_IP:8088/tasks
```

响应示例：

```json
{"ok":true,"deleted":10,"skipped":[]}
```

### GET /tasks/{task_id}

查询单个任务状态和结果。

请求示例：

```bash
curl -H "X-API-Token: $API_TOKEN" http://SERVER_IP:8088/tasks/0123456789abcdef0123456789abcdef
```

响应示例：

```json
{"code":"2","text":"","url":"https://example.com/video.mp4"}
```

### DELETE /tasks/{task_id}

删除未在生成中的任务；生成中任务会返回不可取消。

请求示例：

```bash
curl -X DELETE -H "X-API-Token: $API_TOKEN" http://SERVER_IP:8088/tasks/0123456789abcdef0123456789abcdef
```

响应示例：

```json
{"ok":true}
```

## 服务器各项命令

服务名：`dola-fetch-service`

查看服务状态：

```bash
systemctl --no-pager --full status dola-fetch-service
```

重启服务：

```bash
systemctl restart dola-fetch-service
```

停止服务：

```bash
systemctl stop dola-fetch-service
```

启动服务：

```bash
systemctl start dola-fetch-service
```

查看实时日志：

```bash
journalctl -u dola-fetch-service -f --no-pager
```

查看最近日志：

```bash
journalctl -u dola-fetch-service -n 200 --no-pager
```

查看配置文件：

```bash
cat /var/lib/dola-fetch-service/config.json
```

查看 Token：

```bash
/opt/dola-fetch-service/scripts/show-token.sh
```

重置 Token：

```bash
/opt/dola-fetch-service/scripts/reset-token.sh
```

清空任务数据：

```bash
find /var/lib/dola-fetch-service/tasks -mindepth 1 -maxdepth 1 -exec rm -rf {} +
```
