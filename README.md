# dola_fetch_service

这是一个基于 FastAPI + Playwright 的任务服务，带 Web 管理面板、任务队列、代理提取 API 配置、并发控制和视频结果查询能力。

## 一键安装

在 Linux 服务器上使用 `root` 执行。脚本会自动检测依赖，缺少依赖会自动安装；安装源失败时会自动尝试切换常用镜像源；安装成功后会启动服务，并输出面板地址和 API Token。

支持系统：

- Debian / Ubuntu
- CentOS / RHEL / Rocky / AlmaLinux

从 GitHub 拉取项目到服务器：

```bash
apt-get update && apt-get install -y git
git clone https://github.com/DaFangYue/dola_fetch_service.git /opt/dola-fetch-service
cd /opt/dola-fetch-service
bash scripts/install.sh
```

项目已上传到服务器时：

```bash
cd /opt/dola-fetch-service
bash scripts/install.sh
```

直接用 GitHub 压缩包安装：

```bash
REPO_ZIP_URL="https://github.com/DaFangYue/dola_fetch_service/archive/refs/heads/main.zip" \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/DaFangYue/dola_fetch_service/main/scripts/install.sh)"
```

安装完成后：

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

Windows 本地开发默认保存在：

```text
data/config.json
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

所有接口都需要 Header：

```text
X-API-Token: <API Token>
```

健康检查：

```bash
curl -H "X-API-Token: $API_TOKEN" http://SERVER_IP:8088/health
```

提交文本任务：

```bash
curl -X POST -H "X-API-Token: $API_TOKEN" \
  -F "prompt=一个人在奔跑健身" \
  -F "ratio=9:16" \
  http://SERVER_IP:8088/tasks
```

查询任务列表：

```bash
curl -H "X-API-Token: $API_TOKEN" http://SERVER_IP:8088/tasks
```

修改代理提取 API：

```bash
curl -X POST -H "X-API-Token: $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"proxy_api_url":"https://example.com/get-proxy?num=1&type=txt","proxy_api_scheme":"http"}' \
  http://SERVER_IP:8088/config/proxy-api
```

修改并发数量：

```bash
curl -X POST -H "X-API-Token: $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"browser_workers":4}' \
  http://SERVER_IP:8088/config/workers
```

## 手动部署

本地安装部署依赖：

```bash
pip install -r requirements-deploy.txt
```

设置服务器信息：

```bash
export DEPLOY_HOST="<server-ip>"
export DEPLOY_USER="root"
export DEPLOY_PASSWORD="<server-password>"
python scripts/deploy_server.py
```

部署脚本会跳过 `.venv`、`data`、`__pycache__` 和大二进制文件。

## 发布 GitHub 前注意

不要提交：

- `data/`
- `.venv/`
- 生成的 API Token
- 真实代理商 API 地址和 key

仓库已配置 `.gitignore` 来排除这些文件。
