#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dola-fetch-service}"
DATA_DIR="${DATA_DIR:-/var/lib/dola-fetch-service}"
SERVICE_NAME="${SERVICE_NAME:-dola-fetch-service}"
GITHUB_OWNER="${GITHUB_OWNER:-DaFangYue}"
GITHUB_REPO="${GITHUB_REPO:-dola_fetch_service}"
GITHUB_REF="${GITHUB_REF:-main}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
REPO_ZIP_URL="${REPO_ZIP_URL:-}"
DEFAULT_PROXY_API_URL="${DOLA_DEFAULT_PROXY_API_URL:-https://example.com/get-proxy?num=1&type=txt}"
PYTHON_BIN=""
PKG_MANAGER=""
ORIGINAL_APT_SOURCES=""

APT_MIRRORS=(
  ""
  "https://mirrors.aliyun.com"
  "https://mirrors.tencent.com"
  "https://mirrors.huaweicloud.com"
  "https://mirrors.ustc.edu.cn"
)

PIP_INDEXES=(
  "https://pypi.org/simple"
  "https://pypi.tuna.tsinghua.edu.cn/simple"
  "https://mirrors.aliyun.com/pypi/simple"
  "https://pypi.mirrors.ustc.edu.cn/simple"
)

log() {
  echo
  echo "==> $*"
}

die() {
  echo "安装失败：$*" >&2
  exit 1
}

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    die "请使用 root 运行，例如：sudo bash scripts/install.sh"
  fi
}

detect_package_manager() {
  if command -v apt-get >/dev/null 2>&1; then
    PKG_MANAGER="apt"
  elif command -v dnf >/dev/null 2>&1; then
    PKG_MANAGER="dnf"
  elif command -v yum >/dev/null 2>&1; then
    PKG_MANAGER="yum"
  else
    die "未识别到 apt-get / dnf / yum，暂不支持该系统自动安装"
  fi
}

backup_apt_sources() {
  if [ "$PKG_MANAGER" != "apt" ]; then
    return
  fi
  ORIGINAL_APT_SOURCES="$(mktemp -d)"
  if [ -f /etc/apt/sources.list ]; then
    cp /etc/apt/sources.list "$ORIGINAL_APT_SOURCES/sources.list"
  fi
  if [ -d /etc/apt/sources.list.d ]; then
    mkdir -p "$ORIGINAL_APT_SOURCES/sources.list.d"
    cp -a /etc/apt/sources.list.d/. "$ORIGINAL_APT_SOURCES/sources.list.d/" 2>/dev/null || true
  fi
}

restore_apt_sources() {
  if [ "$PKG_MANAGER" != "apt" ] || [ -z "$ORIGINAL_APT_SOURCES" ] || [ ! -d "$ORIGINAL_APT_SOURCES" ]; then
    return
  fi
  if [ -f "$ORIGINAL_APT_SOURCES/sources.list" ]; then
    cp "$ORIGINAL_APT_SOURCES/sources.list" /etc/apt/sources.list
  fi
  if [ -d "$ORIGINAL_APT_SOURCES/sources.list.d" ]; then
    rm -rf /etc/apt/sources.list.d
    mkdir -p /etc/apt/sources.list.d
    cp -a "$ORIGINAL_APT_SOURCES/sources.list.d/." /etc/apt/sources.list.d/ 2>/dev/null || true
  fi
}

rewrite_apt_sources() {
  local mirror="$1"
  if [ -z "$mirror" ]; then
    restore_apt_sources
    return
  fi

  local codename
  codename="$(. /etc/os-release && echo "${VERSION_CODENAME:-}")"
  if [ -z "$codename" ] && command -v lsb_release >/dev/null 2>&1; then
    codename="$(lsb_release -sc)"
  fi
  [ -n "$codename" ] || return 1

  local distro="debian"
  if [ -f /etc/os-release ]; then
    if grep -qi "ubuntu" /etc/os-release; then
      distro="ubuntu"
    fi
  fi

  mkdir -p /etc/apt/sources.list.d
  rm -f /etc/apt/sources.list.d/*.list 2>/dev/null || true

  if [ "$distro" = "ubuntu" ]; then
    cat > /etc/apt/sources.list <<EOF
deb ${mirror}/ubuntu/ ${codename} main restricted universe multiverse
deb ${mirror}/ubuntu/ ${codename}-updates main restricted universe multiverse
deb ${mirror}/ubuntu/ ${codename}-backports main restricted universe multiverse
deb ${mirror}/ubuntu/ ${codename}-security main restricted universe multiverse
EOF
  else
    cat > /etc/apt/sources.list <<EOF
deb ${mirror}/debian/ ${codename} main contrib non-free non-free-firmware
deb ${mirror}/debian/ ${codename}-updates main contrib non-free non-free-firmware
deb ${mirror}/debian-security/ ${codename}-security main contrib non-free non-free-firmware
EOF
  fi
}

install_system_dependencies_apt() {
  local packages="python3 python3-venv python3-pip curl ca-certificates unzip"
  local mirror
  backup_apt_sources
  for mirror in "${APT_MIRRORS[@]}"; do
    if [ -n "$mirror" ]; then
      log "切换 apt 镜像：$mirror"
      rewrite_apt_sources "$mirror" || true
    else
      log "使用当前 apt 镜像"
      restore_apt_sources
    fi

    if apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y $packages; then
      return 0
    fi
  done
  return 1
}

install_system_dependencies_rhel() {
  local installer="$1"
  $installer install -y python3 python3-pip curl ca-certificates unzip || return 1
  python3 -m venv --help >/dev/null 2>&1 || $installer install -y python3-virtualenv || true
}

install_system_dependencies() {
  log "检测并安装系统依赖"
  if [ "$PKG_MANAGER" = "apt" ]; then
    install_system_dependencies_apt || die "系统依赖安装失败，已尝试多个 apt 镜像"
  elif [ "$PKG_MANAGER" = "dnf" ]; then
    install_system_dependencies_rhel dnf || die "系统依赖安装失败"
  else
    install_system_dependencies_rhel yum || die "系统依赖安装失败"
  fi

  command -v python3 >/dev/null 2>&1 || die "python3 安装失败"
  command -v curl >/dev/null 2>&1 || die "curl 安装失败"
  command -v unzip >/dev/null 2>&1 || die "unzip 安装失败"
  PYTHON_BIN="$(command -v python3)"
}

default_repo_zip_url() {
  echo "https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/zipball/${GITHUB_REF}"
}

curl_to_file() {
  local url="$1"
  local output="$2"
  local args=(-fL --retry 5 --retry-delay 2)
  if [ -n "$GITHUB_TOKEN" ]; then
    args+=(
      -H "Authorization: Bearer ${GITHUB_TOKEN}"
      -H "Accept: application/vnd.github+json"
      -H "X-GitHub-Api-Version: 2022-11-28"
    )
  fi
  curl "${args[@]}" "$url" -o "$output"
}

local_source_dir() {
  local script_dir source_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)" || return 1
  source_dir="$(cd "$script_dir/.." >/dev/null 2>&1 && pwd)" || return 1
  if [ -f "$source_dir/run.py" ] && [ -d "$source_dir/app" ] && [ -f "$source_dir/requirements.txt" ]; then
    echo "$source_dir"
    return 0
  fi
  return 1
}

copy_source() {
  log "准备项目文件"
  mkdir -p "$APP_DIR" "$DATA_DIR"

  local source_dir
  source_dir=""
  if [ -z "$REPO_ZIP_URL" ]; then
    source_dir="$(local_source_dir || true)"
  fi

  if [ -n "$REPO_ZIP_URL" ] || [ -z "$source_dir" ]; then
    local zip_url
    zip_url="${REPO_ZIP_URL:-$(default_repo_zip_url)}"
    log "下载项目压缩包：${GITHUB_OWNER}/${GITHUB_REPO}@${GITHUB_REF}"
    if [ -n "$GITHUB_TOKEN" ]; then
      log "使用 GITHUB_TOKEN 访问私有仓库"
    fi
    local tmp_dir source_dir
    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' EXIT
    curl_to_file "$zip_url" "$tmp_dir/source.zip" || die "项目压缩包下载失败"
    unzip -q "$tmp_dir/source.zip" -d "$tmp_dir/source"
    source_dir="$(find "$tmp_dir/source" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
    [ -n "$source_dir" ] || die "压缩包内容不正确，未找到项目目录"
    find "$APP_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    cp -a "$source_dir"/. "$APP_DIR"/
  else
    log "使用本地项目目录：$source_dir"
    if [ "$source_dir" != "$APP_DIR" ]; then
      find "$APP_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
      cp -a "$source_dir"/. "$APP_DIR"/
    fi
  fi

  rm -rf "$APP_DIR/.git" "$APP_DIR/.venv" "$APP_DIR/data"
  find "$APP_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} +
}

pip_install_with_mirrors() {
  local args="$1"
  local index
  for index in "${PIP_INDEXES[@]}"; do
    log "pip 安装：$args，镜像：$index"
    if "$APP_DIR/.venv/bin/pip" install -i "$index" --trusted-host "$(echo "$index" | awk -F/ '{print $3}')" $args; then
      return 0
    fi
  done
  return 1
}

setup_python_environment() {
  log "创建 Python 虚拟环境"
  cd "$APP_DIR"
  "$PYTHON_BIN" -m venv .venv || die "创建虚拟环境失败"
  pip_install_with_mirrors "--upgrade pip" || die "pip 升级失败"
  pip_install_with_mirrors "-r requirements.txt" || die "Python 依赖安装失败，已尝试多个 pip 镜像"
}

install_chromium() {
  log "安装 Chromium 和运行依赖"
  local playwright="$APP_DIR/.venv/bin/python -m playwright install --with-deps chromium"
  if bash -lc "$playwright"; then
    return 0
  fi

  log "Playwright 安装失败，先补装系统依赖后重试"
  if [ "$PKG_MANAGER" = "apt" ]; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
      libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
      libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 \
      libcairo2 libatspi2.0-0 libx11-6 libxcb1 libxext6 xvfb fonts-noto-color-emoji \
      fonts-unifont fonts-liberation fonts-wqy-zenhei || true
  fi
  bash -lc "$playwright" || die "Chromium 安装失败"
}

init_config() {
  log "初始化配置"
  export DOLA_DATA_DIR="$DATA_DIR"
  export DOLA_CONFIG_PATH="$DATA_DIR/config.json"
  export DOLA_DEFAULT_PROXY_API_URL="$DEFAULT_PROXY_API_URL"
  cd "$APP_DIR"
  "$APP_DIR/.venv/bin/python" - <<'PY'
from app.config import load_settings

settings = load_settings()
print(settings.api_token)
PY
}

install_service() {
  log "安装并启动 systemd 服务"
  cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=dola_fetch_service
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
Environment=DOLA_DATA_DIR=${DATA_DIR}
Environment=DOLA_CONFIG_PATH=${DATA_DIR}/config.json
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/run.py
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

  chmod +x "$APP_DIR"/scripts/*.sh || true
  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME"
  systemctl restart "$SERVICE_NAME"
  sleep 3
  systemctl is-active --quiet "$SERVICE_NAME" || {
    systemctl --no-pager --full status "$SERVICE_NAME" || true
    die "服务启动失败"
  }
}

get_server_ip() {
  local ip
  ip="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
  if [ -z "$ip" ]; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fi
  echo "${ip:-服务器IP}"
}

write_runtime_config() {
  local server_ip port admin_url client_url
  server_ip="$1"
  port="$2"
  admin_url="http://${server_ip}:${port}/admin"
  client_url="http://${server_ip}:${port}/client"
  cat > "$APP_DIR/app/admin/runtime-config.js" <<EOF
window.DFYUE_RUNTIME_CONFIG = {
  adminEntryUrl: "${admin_url}",
  clientEntryUrl: "${client_url}"
};
EOF
}

print_result() {
  local token server_ip port admin_url client_url
  token="$("$APP_DIR/scripts/show-token.sh" 2>/dev/null || true)"
  server_ip="$(get_server_ip)"
  port="$("$APP_DIR/.venv/bin/python" - <<'PY'
from app.config import load_settings
print(load_settings().port)
PY
)"
  admin_url="http://${server_ip}:${port}/admin"
  client_url="http://${server_ip}:${port}/client"
  write_runtime_config "$server_ip" "$port"

  echo
  echo "安装成功"
  echo "管理员面板：${admin_url}"
  echo "客户入口：${client_url}"
  echo "API Token：${token}"
  echo
  echo "常用命令："
  echo "查看服务：systemctl status ${SERVICE_NAME}"
  echo "重启服务：systemctl restart ${SERVICE_NAME}"
  echo "查看 Token：${APP_DIR}/scripts/show-token.sh"
}

main() {
  require_root
  detect_package_manager
  install_system_dependencies
  copy_source
  setup_python_environment
  install_chromium
  init_config >/dev/null
  install_service
  print_result
}

main "$@"
