const TOKEN_KEY = "dfyue_api_token";
const AUTH_KEY = "dfyue_auth_ok";
const RESULTS_KEY = "dfyue_task_results";
const pathName = window.location.pathname.replace(/\/+$/, "");
const portal = pathName === "/client" ? "client" : "admin";
const authPath = portal === "client" ? "/auth/client" : "/auth/admin";

const els = {
  loginView: document.getElementById("loginView"),
  loginForm: document.getElementById("loginForm"),
  loginToken: document.getElementById("loginToken"),
  loginButton: document.getElementById("loginButton"),
  loginState: document.getElementById("loginState"),
  tokenCommand: document.getElementById("tokenCommand"),
  copyTokenCommand: document.getElementById("copyTokenCommand"),
  appShell: document.getElementById("appShell"),
  logoutButton: document.getElementById("logoutButton"),
  viewTitle: document.getElementById("viewTitle"),
  sidebarStatusDot: document.getElementById("sidebarStatusDot"),
  sidebarStatusText: document.getElementById("sidebarStatusText"),
  metricService: document.getElementById("metricService"),
  metricServiceNote: document.getElementById("metricServiceNote"),
  metricWorkers: document.getElementById("metricWorkers"),
  editWorkers: document.getElementById("editWorkers"),
  metricTaskTotal: document.getElementById("metricTaskTotal"),
  metricPending: document.getElementById("metricPending"),
  metricRunning: document.getElementById("metricRunning"),
  metricFinished: document.getElementById("metricFinished"),
  taskForm: document.getElementById("taskForm"),
  promptInput: document.getElementById("promptInput"),
  ratioGroup: document.getElementById("ratioGroup"),
  ratioTrigger: document.querySelector("#ratioGroup .ratio-trigger"),
  resetSubmit: document.getElementById("resetSubmit"),
  submitTask: document.getElementById("submitTask"),
  submitState: document.getElementById("submitState"),
  imageInput: document.getElementById("imageInput"),
  clearImages: document.getElementById("clearImages"),
  imageList: document.getElementById("imageList"),
  ratioValue: document.getElementById("ratioValue"),
  refreshTasks: document.getElementById("refreshTasks"),
  queryVisibleTasks: document.getElementById("queryVisibleTasks"),
  clearTasks: document.getElementById("clearTasks"),
  taskSearch: document.getElementById("taskSearch"),
  prevPage: document.getElementById("prevPage"),
  nextPage: document.getElementById("nextPage"),
  pageState: document.getElementById("pageState"),
  taskTableBody: document.getElementById("taskTableBody"),
  proxyConfigPanel: document.getElementById("proxyConfigPanel"),
  loadProxyConfig: document.getElementById("loadProxyConfig"),
  proxyApiUrl: document.getElementById("proxyApiUrl"),
  saveProxyConfig: document.getElementById("saveProxyConfig"),
  configState: document.getElementById("configState"),
  updatePanel: document.getElementById("updatePanel"),
  checkUpdate: document.getElementById("checkUpdate"),
  applyUpdate: document.getElementById("applyUpdate"),
  updateState: document.getElementById("updateState"),
  updateCurrent: document.getElementById("updateCurrent"),
  updateRemote: document.getElementById("updateRemote"),
  updateBranch: document.getElementById("updateBranch"),
  updateStatusText: document.getElementById("updateStatusText"),
  quotaNavItem: document.getElementById("quotaNavItem"),
  quotaView: document.getElementById("quotaView"),
  clientEntryUrl: document.getElementById("clientEntryUrl"),
  copyClientEntryUrl: document.getElementById("copyClientEntryUrl"),
  refreshTempTokens: document.getElementById("refreshTempTokens"),
  openApiDocs: document.getElementById("openApiDocs"),
  openCreateTokenModal: document.getElementById("openCreateTokenModal"),
  tempTokenTableBody: document.getElementById("tempTokenTableBody"),
  workersModal: document.getElementById("workersModal"),
  workersInput: document.getElementById("workersInput"),
  workersModalState: document.getElementById("workersModalState"),
  closeWorkersModal: document.getElementById("closeWorkersModal"),
  cancelWorkersModal: document.getElementById("cancelWorkersModal"),
  saveWorkers: document.getElementById("saveWorkers"),
  createTokenModal: document.getElementById("createTokenModal"),
  closeCreateTokenModal: document.getElementById("closeCreateTokenModal"),
  cancelCreateTokenModal: document.getElementById("cancelCreateTokenModal"),
  createTokenCount: document.getElementById("createTokenCount"),
  createTokenLimit: document.getElementById("createTokenLimit"),
  createTokenState: document.getElementById("createTokenState"),
  confirmCreateTokens: document.getElementById("confirmCreateTokens"),
  textModal: document.getElementById("textModal"),
  textModalContent: document.getElementById("textModalContent"),
  closeTextModal: document.getElementById("closeTextModal"),
  confirmTextModal: document.getElementById("confirmTextModal"),
  copyTextModal: document.getElementById("copyTextModal"),
  videoModal: document.getElementById("videoModal"),
  videoLoading: document.getElementById("videoLoading"),
  videoPlayer: document.getElementById("videoPlayer"),
  closeVideoModal: document.getElementById("closeVideoModal"),
  confirmVideoModal: document.getElementById("confirmVideoModal"),
  copyVideoUrl: document.getElementById("copyVideoUrl"),
  toastStack: document.getElementById("toastStack"),
};

const state = {
  apiToken: "",
  tasks: [],
  results: loadSessionResults(),
  activeIds: [],
  page: 1,
  pageSize: 25,
  ratio: "9:16",
  images: [],
  modalText: "",
  modalVideoUrl: "",
  submitting: false,
  isTempToken: false,
  tempTokens: [],
};

const MAX_IMAGE_COUNT = 9;

function portalStorageKey(base) {
  return `${base}_${portal}`;
}

function getClientEntryUrl() {
  const configured = window.DFYUE_RUNTIME_CONFIG?.clientEntryUrl || window.DFYUE_RUNTIME_CONFIG?.client_entry_url || "";
  if (configured) return String(configured);
  const url = new URL(window.location.href);
  url.pathname = "/client";
  url.search = "";
  url.hash = "";
  return url.toString();
}

function loadSessionResults() {
  try {
    return JSON.parse(sessionStorage.getItem(RESULTS_KEY) || "{}");
  } catch (_) {
    return {};
  }
}

function saveSessionResults() {
  sessionStorage.setItem(RESULTS_KEY, JSON.stringify(state.results));
}

async function requestJson(path, token, options = {}) {
  if (!token) {
    throw new Error("请输入 API Token");
  }

  const headers = new Headers(options.headers || {});
  headers.set("X-API-Token", token);

  let body = options.body;
  if (body && !(body instanceof FormData) && typeof body !== "string") {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(body);
  }

  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body,
  });

  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (_) {
      data = { text };
    }
  }

  if (!response.ok) {
    const detail = data?.detail || data?.message || text || `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return data || {};
}

async function apiFetch(path, options = {}) {
  return requestJson(path, state.apiToken, options);
}

function toast(message, type = "info") {
  const node = document.createElement("div");
  node.className = `toast ${type === "error" ? "error" : ""}`;
  node.textContent = message;
  els.toastStack.appendChild(node);
  window.setTimeout(() => node.remove(), 1000);
}

function setBusy(button, busy, label) {
  if (!button) return;
  if (!button.dataset.idleText) {
    button.dataset.idleText = button.textContent;
  }
  button.disabled = busy;
  if (button.classList.contains("submit-arrow-button")) {
    return;
  }
  button.textContent = busy ? label : button.dataset.idleText;
}

function setSubmitControlsDisabled(disabled) {
  state.submitting = disabled;
  els.taskForm.classList.toggle("is-submitting", disabled);
  els.promptInput.disabled = disabled;
  els.imageInput.disabled = disabled;
  els.submitTask.disabled = disabled;
  if (els.resetSubmit) els.resetSubmit.disabled = disabled;
  els.ratioTrigger.disabled = disabled;
  els.ratioGroup.classList.remove("open");
  els.ratioGroup.querySelectorAll("button[data-ratio]").forEach((button) => {
    button.disabled = disabled;
  });
  els.imageList.querySelectorAll("button").forEach((button) => {
    button.disabled = disabled;
  });
}

function clearTokenFromUrl() {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("token")) return;
  url.searchParams.delete("token");
  const query = url.searchParams.toString();
  window.history.replaceState({}, document.title, `${url.pathname}${query ? `?${query}` : ""}${url.hash}`);
}

function shortId(id) {
  return id ? `${id.slice(0, 8)}...${id.slice(-6)}` : "-";
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatBytes(size) {
  if (!Number.isFinite(size)) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(2)} MB`;
}

function md5ArrayBuffer(buffer) {
  const bytes = new Uint8Array(buffer);
  const shifts = [
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
    5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
    4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
    6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
  ];
  const constants = Array.from({ length: 64 }, (_, index) =>
    Math.floor(Math.abs(Math.sin(index + 1)) * 0x100000000) >>> 0
  );
  const rotateLeft = (value, bits) => ((value << bits) | (value >>> (32 - bits))) >>> 0;

  const paddedLength = (((bytes.length + 8) >> 6) + 1) * 64;
  const padded = new Uint8Array(paddedLength);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const bitLength = bytes.length * 8;
  for (let i = 0; i < 8; i += 1) {
    padded[paddedLength - 8 + i] = Math.floor(bitLength / (2 ** (8 * i))) & 0xff;
  }

  let a0 = 0x67452301;
  let b0 = 0xefcdab89;
  let c0 = 0x98badcfe;
  let d0 = 0x10325476;

  for (let offset = 0; offset < padded.length; offset += 64) {
    const words = [];
    for (let index = 0; index < 16; index += 1) {
      const base = offset + index * 4;
      words[index] = (
        padded[base] |
        (padded[base + 1] << 8) |
        (padded[base + 2] << 16) |
        (padded[base + 3] << 24)
      ) >>> 0;
    }

    let a = a0;
    let b = b0;
    let c = c0;
    let d = d0;

    for (let index = 0; index < 64; index += 1) {
      let f;
      let g;
      if (index < 16) {
        f = (b & c) | (~b & d);
        g = index;
      } else if (index < 32) {
        f = (d & b) | (~d & c);
        g = (5 * index + 1) % 16;
      } else if (index < 48) {
        f = b ^ c ^ d;
        g = (3 * index + 5) % 16;
      } else {
        f = c ^ (b | ~d);
        g = (7 * index) % 16;
      }
      const next = d;
      d = c;
      c = b;
      b = (b + rotateLeft((a + f + constants[index] + words[g]) >>> 0, shifts[index])) >>> 0;
      a = next;
    }

    a0 = (a0 + a) >>> 0;
    b0 = (b0 + b) >>> 0;
    c0 = (c0 + c) >>> 0;
    d0 = (d0 + d) >>> 0;
  }

  const hexWord = (value) => {
    let out = "";
    for (let i = 0; i < 4; i += 1) {
      out += ((value >> (i * 8)) & 0xff).toString(16).padStart(2, "0");
    }
    return out;
  };
  return [a0, b0, c0, d0].map(hexWord).join("");
}

async function fileMd5(file) {
  if (file.__dfyueMd5) return file.__dfyueMd5;
  const hash = md5ArrayBuffer(await file.arrayBuffer());
  Object.defineProperty(file, "__dfyueMd5", { value: hash, enumerable: false });
  return hash;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setServiceState(ok, note) {
  els.sidebarStatusDot.classList.toggle("ok", ok);
  els.sidebarStatusDot.classList.toggle("bad", !ok);
  els.metricService.classList.toggle("ok", ok);
  els.metricService.classList.toggle("bad", !ok);
  els.sidebarStatusText.textContent = ok ? "已连接" : "连接失败";
  els.metricService.textContent = ok ? "正常" : "异常";
  els.metricServiceNote.textContent = note || (ok ? "API 可访问" : "检查 Token 或服务");
}

function showLogin(message = "等待输入") {
  document.body.dataset.portal = portal;
  els.appShell.classList.add("hidden");
  els.loginView.classList.remove("hidden");
  els.loginState.textContent = message;
  sessionStorage.removeItem(portalStorageKey(AUTH_KEY));
}

function showApp() {
  document.body.dataset.portal = portal;
  els.loginView.classList.add("hidden");
  els.appShell.classList.remove("hidden");
  switchView("dashboard");
}

function applyPortalText() {
  document.body.dataset.portal = portal;
  document.title = portal === "client" ? "DFYue Fetch 客户入口" : "DFYue Fetch 管理面板";
  if (els.clientEntryUrl) els.clientEntryUrl.value = getClientEntryUrl();
  document.querySelectorAll(".login-heading .eyebrow").forEach((node) => {
    node.textContent = portal === "client" ? "客户入口" : "管理面板";
  });
  const tokenLabel = document.querySelector("label.field span");
  if (tokenLabel) tokenLabel.textContent = portal === "client" ? "客户 Token" : "管理员 Token";
  if (els.loginToken) {
    els.loginToken.placeholder = portal === "client" ? "请输入客户 Token" : "请输入管理员 Token";
  }
  const hint = document.querySelector(".login-token-hint > span");
  if (hint) {
    hint.textContent = portal === "client"
      ? "客户 Token 由管理员分配，请联系管理员获取"
      : "API Token唯一获取方式：登入服务器使用命令行获取";
  }
  document.querySelector(".login-command-row")?.classList.toggle("hidden", portal === "client");
}

function applyAccessScope(data = {}) {
  state.isTempToken = portal === "client";
  const isClient = portal === "client";
  if (els.editWorkers) els.editWorkers.classList.toggle("hidden", isClient);
  if (els.proxyConfigPanel) els.proxyConfigPanel.classList.toggle("hidden", isClient);
  if (els.updatePanel) els.updatePanel.classList.toggle("hidden", isClient);
  if (els.quotaNavItem) {
    els.quotaNavItem.classList.toggle("hidden", isClient);
    els.quotaNavItem.setAttribute("aria-hidden", isClient ? "true" : "false");
    els.quotaNavItem.tabIndex = isClient ? -1 : 0;
  }
  if (els.quotaView) {
    els.quotaView.classList.toggle("hidden", isClient);
    els.quotaView.setAttribute("aria-hidden", isClient ? "true" : "false");
  }
  const docsNav = document.querySelector('.nav-item[data-view="docs"]');
  const docsView = document.getElementById("docsView");
  if (docsNav) {
    docsNav.classList.toggle("hidden", isClient);
    docsNav.setAttribute("aria-hidden", isClient ? "true" : "false");
    docsNav.tabIndex = isClient ? -1 : 0;
  }
  if (docsView) {
    docsView.classList.toggle("hidden", isClient);
    docsView.setAttribute("aria-hidden", isClient ? "true" : "false");
  }
  if (isClient && ["quotaView", "docsView"].includes(document.querySelector(".view.active")?.id || "")) {
    switchView("dashboard");
  }
}

async function login(event) {
  event.preventDefault();
  const token = els.loginToken.value.trim();
  setBusy(els.loginButton, true, "校验中");
  els.loginState.textContent = "校验中";
  try {
    const health = await requestJson(authPath, token);
    state.apiToken = token;
    applyAccessScope(health);
    localStorage.setItem(portalStorageKey(TOKEN_KEY), token);
    sessionStorage.setItem(portalStorageKey(AUTH_KEY), "1");
    clearTokenFromUrl();
    showApp();
    await refreshDashboard();
  } catch (error) {
    els.loginState.textContent = "校验失败";
    toast(`登录失败：${error.message}`, "error");
  } finally {
    setBusy(els.loginButton, false);
  }
}

function logout() {
  state.apiToken = "";
  sessionStorage.removeItem(portalStorageKey(AUTH_KEY));
  showLogin("已退出");
}

function switchView(name) {
  if (portal === "client" && ["quota", "docs"].includes(name)) {
    name = "dashboard";
  }
  const targetView = document.getElementById(`${name}View`);
  if (!targetView || targetView.classList.contains("hidden")) {
    name = "dashboard";
  }
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((view) => {
    const active = view.id === `${name}View`;
    view.classList.toggle("active", active);
    if (active && els.viewTitle) els.viewTitle.textContent = view.dataset.title || "";
  });
  if (name === "tasks" && !state.tasks.length) refreshTasks();
  if (name === "quota" && !state.tempTokens.length) refreshTempTokens();
}

function updateDashboardMetrics() {
  const activeIds = new Set(state.activeIds);
  let pending = 0;
  let running = 0;
  let finished = 0;

  state.tasks.forEach((task) => {
    const status = String(task.status || "").toLowerCase();
    if (activeIds.has(task.id) || status === "running") {
      running += 1;
      return;
    }
    if (status === "success") {
      finished += 1;
      return;
    }
    if (status === "pending") {
      pending += 1;
      return;
    }

    const queryStatus = getTaskStatus(task);
    if (queryStatus.className === "success" || queryStatus.className === "failed") {
      finished += 1;
    } else if (queryStatus.className === "running") {
      running += 1;
    }
  });

  els.metricTaskTotal.textContent = String(state.tasks.length);
  els.metricPending.textContent = String(pending);
  els.metricRunning.textContent = String(running);
  els.metricFinished.textContent = String(finished);
}

async function refreshHealth() {
  const data = await apiFetch("/health");
  state.activeIds = Array.isArray(data.active) ? data.active : [];
  els.metricWorkers.textContent = String(data.browser_workers ?? "-");
  applyAccessScope(data);
  setServiceState(true);
  updateDashboardMetrics();
  return data;
}

async function loadProxyConfig() {
  if (portal === "client" || !els.proxyApiUrl) return null;
  const data = await apiFetch("/config/proxy-api");
  els.proxyApiUrl.value = data.proxy_api_url || "";
  if (els.configState) els.configState.textContent = "已读取";
  return data;
}

async function saveProxyConfig() {
  if (portal === "client") return;
  const url = els.proxyApiUrl?.value.trim() || "";
  if (!url) {
    toast("提取地址不能为空", "error");
    return;
  }
  setBusy(els.saveProxyConfig, true, "保存中");
  try {
    await apiFetch("/config/proxy-api", {
      method: "POST",
      body: {
        proxy_api_url: url,
        proxy_api_scheme: "http",
      },
    });
    if (els.configState) els.configState.textContent = "已保存";
    toast("代理配置已更新");
  } catch (error) {
    toast(`保存失败：${error.message}`, "error");
  } finally {
    setBusy(els.saveProxyConfig, false);
  }
}


function renderUpdateStatus(data) {
  if (!data || portal === "client") return;
  const available = data.available !== false;
  if (els.updateCurrent) els.updateCurrent.textContent = data.commit || "-";
  if (els.updateRemote) els.updateRemote.textContent = data.remote_commit || "-";
  if (els.updateBranch) els.updateBranch.textContent = data.remote_branch || data.branch || "-";
  let label = available ? "可检查" : "需要重新部署一次";
  if (data.checked_remote) {
    label = data.has_update ? `有更新，落后 ${data.behind} 个提交` : "已是最新";
  } else if (data.dirty) {
    label = "本地有改动";
  }
  if (els.updateStatusText) els.updateStatusText.textContent = label;
  if (els.updateState) els.updateState.textContent = data.checked_remote ? "已检查" : "已读取";
}

async function checkUpdateStatus(options = {}) {
  if (portal === "client" || !els.updatePanel) return null;
  const query = options.checkRemote ? "?check_remote=true" : "";
  const data = await apiFetch(`/admin/update/status${query}`);
  renderUpdateStatus(data);
  return data;
}

async function applyHotUpdate() {
  if (portal === "client") return;
  const ok = window.confirm("确认拉取 GitHub main 分支并重启服务吗？");
  if (!ok) return;
  setBusy(els.applyUpdate, true, "更新中");
  if (els.updateState) els.updateState.textContent = "更新中";
  try {
    const data = await apiFetch("/admin/update", {
      method: "POST",
      body: { branch: "main", restart: true, restart_method: "exit" },
    });
    renderUpdateStatus(data.status || data);
    const before = data.before || "-";
    const after = data.after || "-";
    const changed = Boolean(data.changed);
    toast(changed ? `\u5df2\u62c9\u53d6\u65b0\u7248\u672c\uff1a${before} -> ${after}\uff0c\u670d\u52a1\u5373\u5c06\u91cd\u542f` : `\u672a\u53d1\u73b0\u65b0\u7248\u672c\uff1a${before} -> ${after}\uff0c\u5df2\u89e6\u53d1\u91cd\u542f`);
    if (els.updateState) els.updateState.textContent = data.restart ? "\u91cd\u542f\u4e2d" : "\u5df2\u66f4\u65b0";
  } catch (error) {
    toast(`更新失败：${error.message}`, "error");
    if (els.updateState) els.updateState.textContent = "更新失败";
  } finally {
    setBusy(els.applyUpdate, false);
  }
}

async function openApiDocumentation() {
  const tab = window.open("about:blank", "_blank");
  if (!tab) {
    toast("浏览器拦截了新窗口，请允许弹窗后重试", "error");
    return;
  }
  tab.document.write("<!doctype html><title>API接口文档</title><body style=\"font-family: system-ui, sans-serif; padding: 24px;\">正在加载 API 接口文档...</body>");
  try {
    const response = await fetch("/api-documentation", {
      headers: { "X-API-Token": state.apiToken },
      cache: "no-store",
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `HTTP ${response.status}`);
    }
    const markdown = await response.text();
    const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>API接口文档</title>
  <style>
    body { margin: 0; background: #f6f8fa; color: #172026; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    main { max-width: 980px; margin: 0 auto; padding: 28px 20px 48px; }
    h1 { margin: 0 0 16px; font-size: 28px; }
    pre { margin: 0; padding: 20px; overflow-x: auto; white-space: pre-wrap; word-break: break-word; background: #ffffff; border: 1px solid #d7e0e5; border-radius: 8px; line-height: 1.65; font-family: "Cascadia Mono", "SFMono-Regular", Consolas, monospace; font-size: 13px; }
  </style>
</head>
<body>
  <main>
    <h1>API接口文档</h1>
    <pre>${escapeHtml(markdown)}</pre>
  </main>
</body>
</html>`;
    tab.document.open();
    tab.document.write(html);
    tab.document.close();
  } catch (error) {
    tab.document.open();
    tab.document.write(`<!doctype html><title>文档打开失败</title><body style="font-family: system-ui, sans-serif; padding: 24px;"><h1>文档打开失败</h1><pre>${escapeHtml(error.message)}</pre></body>`);
    tab.document.close();
    toast(`文档打开失败：${error.message}`, "error");
  }
}
function openWorkersModal() {
  const current = Number.parseInt(els.metricWorkers.textContent, 10);
  els.workersInput.value = Number.isFinite(current) ? String(current) : "1";
  els.workersModalState.textContent = "";
  els.workersModal.classList.remove("hidden");
  els.workersModal.setAttribute("aria-hidden", "false");
  els.workersInput.focus();
  els.workersInput.select();
}

function closeWorkersModal() {
  els.workersModal.classList.add("hidden");
  els.workersModal.setAttribute("aria-hidden", "true");
}

function openTextModal(text) {
  const value = String(text || "");
  if (!value || value === "-") return;
  state.modalText = value;
  els.textModalContent.textContent = value;
  els.textModal.classList.remove("hidden");
  els.textModal.setAttribute("aria-hidden", "false");
}

function closeTextModal() {
  state.modalText = "";
  els.textModal.classList.add("hidden");
  els.textModal.setAttribute("aria-hidden", "true");
}

function openVideoModal(url) {
  const value = String(url || "").trim();
  if (!value) return;
  state.modalVideoUrl = value;
  els.videoLoading.textContent = "正在加载视频链接...";
  els.videoLoading.classList.remove("hidden");
  els.videoPlayer.classList.add("hidden");
  els.videoPlayer.removeAttribute("src");
  els.videoPlayer.load();
  els.videoModal.classList.remove("hidden");
  els.videoModal.setAttribute("aria-hidden", "false");
  window.setTimeout(() => {
    if (state.modalVideoUrl !== value || els.videoModal.classList.contains("hidden")) return;
    els.videoPlayer.src = value;
    els.videoPlayer.classList.remove("hidden");
    els.videoLoading.classList.add("hidden");
    els.videoPlayer.load();
  }, 120);
}

function closeVideoModal() {
  state.modalVideoUrl = "";
  els.videoPlayer.pause();
  els.videoPlayer.removeAttribute("src");
  els.videoPlayer.load();
  els.videoModal.classList.add("hidden");
  els.videoModal.setAttribute("aria-hidden", "true");
}

async function saveWorkersConfig() {
  const workers = Number.parseInt(els.workersInput.value, 10);
  if (!Number.isInteger(workers) || workers < 1 || workers > 5) {
    els.workersModalState.textContent = "请输入 1 - 5";
    toast("并发数量范围是 1 - 5", "error");
    return;
  }
  setBusy(els.saveWorkers, true, "保存中");
  try {
    const data = await apiFetch("/config/workers", {
      method: "POST",
      body: { browser_workers: workers },
    });
    els.metricWorkers.textContent = String(data.browser_workers ?? workers);
    els.workersModalState.textContent = "已保存";
    toast("并发配置已更新");
    closeWorkersModal();
    await refreshHealth();
  } catch (error) {
    els.workersModalState.textContent = "保存失败";
    toast(`保存失败：${error.message}`, "error");
  } finally {
    setBusy(els.saveWorkers, false);
  }
}

async function refreshDashboard() {
  try {
    await refreshHealth();
    const jobs = [refreshTasks({ quiet: true })];
    if (portal === "admin") {
      jobs.push(loadProxyConfig());
      jobs.push(checkUpdateStatus());
    }
    const results = await Promise.allSettled(jobs);
    const rejected = results.find((item) => item.status === "rejected");
    if (rejected) throw rejected.reason;
  } catch (error) {
    setServiceState(false, error.message);
    if (String(error.message).includes("forbidden")) {
      showLogin("Token 已失效");
    }
    toast(`刷新失败：${error.message}`, "error");
  } finally {
    updateDashboardMetrics();
  }
}

async function refreshTasks(options = {}) {
  if (!options.quiet) setBusy(els.refreshTasks, true, "刷新中");
  try {
    const data = await apiFetch("/tasks");
    const tasks = Array.isArray(data.tasks) ? data.tasks : [];
    state.tasks = tasks.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
    state.page = 1;
    renderTaskTable();
    updateDashboardMetrics();
    if (!options.quiet) toast(`已载入 ${state.tasks.length} 条任务`);
  } catch (error) {
    if (!options.quiet) toast(`任务列表读取失败：${error.message}`, "error");
    throw error;
  } finally {
    if (!options.quiet) setBusy(els.refreshTasks, false);
  }
}

function getTaskStatus(task) {
  const result = state.results[task.id];
  if (!result) {
    const status = String(task.status || "").toLowerCase();
    if (status === "pending") return { label: "待执行", className: "unknown", text: task.error || "", url: "" };
    if (status === "running") return { label: "生成中", className: "running", text: task.error || "", url: "" };
    if (status === "success") return { label: "任务结束", className: "success", text: task.error || "", url: "" };
    return { label: "未查询", className: "unknown", text: "", url: "" };
  }
  if (result.error) return { label: "查询失败", className: "failed", text: result.error, url: "" };

  const code = String(result.code ?? "");
  const text = String(result.text ?? "");
  const url = String(result.url ?? "");
  if (code === "2" || url) return { label: "成功", className: "success", text, url };
  if (text.includes("无法生成") || text.includes("违规") || text.includes("失败")) {
    return { label: "失败", className: "failed", text, url };
  }
  if (code === "1" || code === "0") return { label: "生成中", className: "running", text, url };
  return { label: code ? `code ${code}` : "未知", className: "unknown", text, url };
}

function filteredTasks() {
  const keyword = els.taskSearch.value.trim().toLowerCase();
  if (!keyword) return state.tasks;
  return state.tasks.filter((task) => {
    const status = getTaskStatus(task);
    return [
      task.id,
      task.prompt,
      task.prompt_preview,
      task.created_at,
      task.updated_at,
      task.status,
      task.error,
      status.label,
      status.text,
      status.url,
    ].some((value) => String(value || "").toLowerCase().includes(keyword));
  });
}

function pageTasks() {
  const tasks = filteredTasks();
  const totalPages = Math.max(1, Math.ceil(tasks.length / state.pageSize));
  state.page = Math.min(Math.max(1, state.page), totalPages);
  const start = (state.page - 1) * state.pageSize;
  return {
    tasks: tasks.slice(start, start + state.pageSize),
    total: tasks.length,
    totalPages,
  };
}

function renderTaskTable() {
  const page = pageTasks();
  els.pageState.textContent = `${state.page} / ${page.totalPages}`;
  els.prevPage.disabled = state.page <= 1;
  els.nextPage.disabled = state.page >= page.totalPages;

  if (!page.tasks.length) {
    els.taskTableBody.innerHTML = `<tr><td colspan="5"><div class="empty-state">暂无任务</div></td></tr>`;
    return;
  }

  els.taskTableBody.innerHTML = page.tasks.map((task) => {
    const status = getTaskStatus(task);
    const resultText = status.text || task.error || "-";
    const canOpenResult = resultText && resultText !== "-";
    const fullPrompt = String(task.prompt || task.prompt_preview || "").trim();
    const promptPreview = String(task.prompt_preview || "").trim() || "-";
    const video = status.url
      ? `<div class="video-actions">
           <button class="video-link-button" type="button" data-action="open-video" data-id="${escapeHtml(task.id)}">打开视频</button>
           <button class="video-link-button" type="button" data-action="copy-url" data-id="${escapeHtml(task.id)}">复制链接</button>
           <button class="video-link-button" type="button" data-action="download-video" data-id="${escapeHtml(task.id)}">一键下载</button>
         </div>`
      : "-";
    return `
      <tr>
        <td>
          <div class="task-id">
            <div class="task-prompt-row">
              <span class="task-prompt" title="${escapeHtml(fullPrompt || task.prompt_preview || "")}">${escapeHtml(promptPreview)}</span>
              <button class="task-copy-prompt" type="button" data-action="copy-prompt" data-id="${escapeHtml(task.id)}">复制</button>
            </div>
            <code title="${escapeHtml(task.id)}">${escapeHtml(shortId(task.id))}</code>
            <span class="task-time">${escapeHtml(formatTime(task.created_at))}</span>
          </div>
        </td>
        <td><span class="chip ${status.className}">${escapeHtml(status.label)}</span></td>
        <td>
          <button class="result-text-button ${canOpenResult ? "has-detail" : ""}" type="button" data-action="show-result" data-id="${escapeHtml(task.id)}" title="${canOpenResult ? "查看完整文本" : ""}">
            <span class="result-text-preview">${escapeHtml(resultText)}</span>
          </button>
        </td>
        <td><div class="url-cell">${video}</div></td>
        <td>
          <div class="row-actions">
            <button class="icon-button" type="button" data-action="query" data-id="${escapeHtml(task.id)}">查询</button>
            <button class="icon-button" type="button" data-action="copy-id" data-id="${escapeHtml(task.id)}">复制ID</button>
            <button class="danger-button" type="button" data-action="delete" data-id="${escapeHtml(task.id)}">删除</button>
          </div>
        </td>
      </tr>
    `;
  }).join("");
}

function renderTempTokenTable() {
  if (!els.tempTokenTableBody) return;
  if (!state.tempTokens.length) {
    els.tempTokenTableBody.innerHTML = `<tr><td colspan="6"><div class="empty-state">暂无临时 Token</div></td></tr>`;
    return;
  }

  els.tempTokenTableBody.innerHTML = state.tempTokens.map((item) => {
    const token = String(item.token || "");
    const tokenId = String(item.id || "");
    const limit = Number(item.limit || 0);
    const used = Number(item.used || 0);
    const remaining = Number(item.remaining ?? Math.max(0, limit - used));
    return `
      <tr>
        <td>
          <div class="temp-token-cell">
            <code title="${escapeHtml(token)}">${escapeHtml(token)}</code>
            <button class="token-copy-button" type="button" data-action="copy-temp-token" data-id="${escapeHtml(tokenId)}">复制</button>
          </div>
        </td>
        <td>
          <input class="quota-limit-input" type="number" min="1" max="100000" step="1" value="${escapeHtml(limit)}" data-token-limit="${escapeHtml(tokenId)}" />
        </td>
        <td>${escapeHtml(used)}</td>
        <td>${escapeHtml(remaining)}</td>
        <td>${escapeHtml(formatTime(item.created_at))}</td>
        <td>
          <div class="row-actions quota-row-actions">
            <button class="icon-button" type="button" data-action="save-temp-token" data-id="${escapeHtml(tokenId)}">保存</button>
            <button class="danger-button" type="button" data-action="delete-temp-token" data-id="${escapeHtml(tokenId)}">删除</button>
          </div>
        </td>
      </tr>
    `;
  }).join("");
}

async function refreshTempTokens(options = {}) {
  if (portal === "client") return;
  if (!options.quiet) setBusy(els.refreshTempTokens, true, "刷新中");
  try {
    const data = await apiFetch("/temp-tokens");
    state.tempTokens = Array.isArray(data.tokens) ? data.tokens : [];
    renderTempTokenTable();
    if (!options.quiet) toast(`已加载 ${state.tempTokens.length} 个临时 Token`);
  } catch (error) {
    if (!options.quiet) toast(`临时 Token 读取失败：${error.message}`, "error");
    throw error;
  } finally {
    if (!options.quiet) setBusy(els.refreshTempTokens, false);
  }
}

function openCreateTokenModal() {
  if (portal === "client") return;
  els.createTokenCount.value = "1";
  els.createTokenLimit.value = "100";
  els.createTokenState.textContent = "";
  els.createTokenModal.classList.remove("hidden");
  els.createTokenModal.setAttribute("aria-hidden", "false");
  els.createTokenCount.focus();
  els.createTokenCount.select();
}

function closeCreateTokenModal() {
  els.createTokenModal.classList.add("hidden");
  els.createTokenModal.setAttribute("aria-hidden", "true");
}

async function createTempTokens() {
  const count = Number.parseInt(els.createTokenCount.value, 10);
  const limit = Number.parseInt(els.createTokenLimit.value, 10);
  if (!Number.isInteger(count) || count < 1 || count > 200) {
    toast("生成条数范围是 1 - 200", "error");
    return;
  }
  if (!Number.isInteger(limit) || limit < 1 || limit > 100000) {
    toast("额度范围是 1 - 100000", "error");
    return;
  }

  setBusy(els.confirmCreateTokens, true, "生成中");
  try {
    const data = await apiFetch("/temp-tokens", {
      method: "POST",
      body: { count, limit },
    });
    const created = Array.isArray(data.tokens) ? data.tokens.length : 0;
    els.createTokenState.textContent = "已生成";
    toast(`已生成 ${created} 个临时 Token`);
    closeCreateTokenModal();
    await refreshTempTokens({ quiet: true });
  } catch (error) {
    els.createTokenState.textContent = "生成失败";
    toast(`生成失败：${error.message}`, "error");
  } finally {
    setBusy(els.confirmCreateTokens, false);
  }
}

async function saveTempTokenLimit(tokenId) {
  const input = Array.from(document.querySelectorAll("[data-token-limit]")).find((node) => node.dataset.tokenLimit === tokenId);
  const limit = Number.parseInt(input?.value || "", 10);
  if (!Number.isInteger(limit) || limit < 1 || limit > 100000) {
    toast("额度范围是 1 - 100000", "error");
    return;
  }
  await apiFetch(`/temp-tokens/${encodeURIComponent(tokenId)}`, {
    method: "PATCH",
    body: { limit },
  });
  toast("额度已保存");
  await refreshTempTokens({ quiet: true });
}

async function deleteTempTokenById(tokenId) {
  const item = state.tempTokens.find((token) => token.id === tokenId);
  const ok = window.confirm(`确认删除这个临时 Token？\n${item?.token || tokenId}`);
  if (!ok) return;
  await apiFetch(`/temp-tokens/${encodeURIComponent(tokenId)}`, { method: "DELETE" });
  toast("临时 Token 已删除");
  await refreshTempTokens({ quiet: true });
}

async function queryTask(id, options = {}) {
  try {
    const data = await apiFetch(`/tasks/${encodeURIComponent(id)}`);
    state.results[id] = data;
    saveSessionResults();
    if (!options.quiet) toast(`${shortId(id)} 查询完成`);
    renderTaskTable();
    updateDashboardMetrics();
    return data;
  } catch (error) {
    state.results[id] = { error: error.message };
    saveSessionResults();
    renderTaskTable();
    updateDashboardMetrics();
    if (!options.quiet) toast(`${shortId(id)} 查询失败：${error.message}`, "error");
    throw error;
  }
}

async function queryVisibleTasks() {
  const ids = pageTasks().tasks.map((task) => task.id);
  if (!ids.length) return;
  setBusy(els.queryVisibleTasks, true, "查询中");
  let success = 0;
  let failed = 0;
  await runPool(ids, 5, async (id) => {
    try {
      await queryTask(id, { quiet: true });
      success += 1;
    } catch (_) {
      failed += 1;
    }
  });
  setBusy(els.queryVisibleTasks, false);
  toast(`本页查询完成：成功 ${success}，失败 ${failed}`, failed ? "error" : "info");
}

async function runPool(items, limit, worker) {
  let index = 0;
  const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (index < items.length) {
      const current = items[index];
      index += 1;
      await worker(current);
    }
  });
  await Promise.all(runners);
}

async function deleteTask(id) {
  try {
    await apiFetch(`/tasks/${encodeURIComponent(id)}`, { method: "DELETE" });
    state.tasks = state.tasks.filter((task) => task.id !== id);
    delete state.results[id];
    saveSessionResults();
    renderTaskTable();
    updateDashboardMetrics();
    toast(`${shortId(id)} 已删除`);
  } catch (error) {
    toast(`删除失败：${error.message}`, "error");
  }
}

async function clearTasks() {
  if (!state.tasks.length) return;
  const ok = window.confirm(`确认删除当前列表中的 ${state.tasks.length} 条任务？生成中的任务会被后端拒绝删除。`);
  if (!ok) return;

  setBusy(els.clearTasks, true, "删除中");
  try {
    const data = await apiFetch("/tasks", { method: "DELETE" });
    state.results = {};
    saveSessionResults();
    await refreshTasks({ quiet: true });
    const skipped = Array.isArray(data.skipped) ? data.skipped.length : 0;
    toast(`清空完成：删除 ${data.deleted || 0}${skipped ? `，保留生成中 ${skipped}` : ""}`);
  } catch (error) {
    toast(`清空失败：${error.message}`, "error");
  } finally {
    setBusy(els.clearTasks, false);
  }
}

function setSegmentValue(group, key, value) {
  group.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", button.dataset[key] === value);
  });
  if (els.ratioValue) els.ratioValue.textContent = value;
}

function renderImages() {
  if (!state.images.length) {
    els.imageList.innerHTML = "";
    return;
  }
  els.imageList.innerHTML = "";
  state.images.forEach((file, index) => {
    const row = document.createElement("div");
    row.className = "image-item";
    const img = document.createElement("img");
    img.className = "thumb";
    img.alt = file.name;
    img.src = URL.createObjectURL(file);
    img.onload = () => URL.revokeObjectURL(img.src);

    const meta = document.createElement("div");
    meta.className = "image-meta";
    meta.innerHTML = `<strong>${escapeHtml(file.name)}</strong><span>${escapeHtml(formatBytes(file.size))}</span>`;

    const button = document.createElement("button");
    button.className = "icon-button";
    button.type = "button";
    button.textContent = "移除";
    button.disabled = state.submitting;
    button.addEventListener("click", () => {
      if (state.submitting) return;
      state.images.splice(index, 1);
      renderImages();
    });

    row.append(img, meta, button);
    els.imageList.appendChild(row);
  });
}

async function setImages(files) {
  if (state.submitting) return;
  const incoming = Array.from(files || []).filter((file) => file.type.startsWith("image/"));
  const keys = new Set(await Promise.all(state.images.map(fileMd5)));
  const unique = [];
  let duplicateCount = 0;

  for (const file of incoming) {
    const key = await fileMd5(file);
    if (keys.has(key)) {
      duplicateCount += 1;
      continue;
    }
    keys.add(key);
    unique.push(file);
  }

  const remaining = Math.max(0, MAX_IMAGE_COUNT - state.images.length);
  const accepted = unique.slice(0, remaining);
  const overflow = unique.length - accepted.length;
  state.images = state.images.concat(accepted);
  els.imageInput.value = "";

  if (duplicateCount) {
    toast(`已跳过 ${duplicateCount} 张重复图片`);
  }
  if (overflow > 0) {
    toast(`最多支持 ${MAX_IMAGE_COUNT} 张图片，已忽略 ${overflow} 张`, "error");
  }
  renderImages();
}

function resetSubmitForm(options = {}) {
  if (state.submitting && !options.force) return;
  els.promptInput.value = "";
  state.ratio = "9:16";
  setSegmentValue(els.ratioGroup, "ratio", state.ratio);
  state.images = [];
  els.imageInput.value = "";
  renderImages();
  els.submitState.textContent = "待提交";
}

async function submitTask(event) {
  event.preventDefault();
  if (state.submitting) return;
  const prompt = els.promptInput.value.trim();
  if (!prompt) {
    toast("提示词不能为空", "error");
    return;
  }

  const form = new FormData();
  form.append("prompt", prompt);
  form.append("ratio", state.ratio);
  state.images.forEach((file) => form.append("images", file, file.name));

  setSubmitControlsDisabled(true);
  els.submitState.textContent = "提交中";
  try {
    const data = await apiFetch("/tasks", { method: "POST", body: form });
    els.submitState.textContent = `已提交：${shortId(data.id)}`;
    toast(`任务已提交：${data.id}`);
    await refreshTasks({ quiet: true });
    resetSubmitForm({ force: true });
  } catch (error) {
    els.submitState.textContent = "提交失败";
    toast(`提交失败：${error.message}`, "error");
  } finally {
    setSubmitControlsDisabled(false);
  }
}

async function copyText(value, label = "内容") {
  const text = String(value || "");
  if (!text) {
    toast("没有可复制内容", "error");
    return;
  }

  try {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      textarea.style.top = "0";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      const copied = document.execCommand("copy");
      document.body.removeChild(textarea);
      if (!copied) throw new Error("copy command failed");
    }
    toast(`${label}已复制`);
  } catch (error) {
    console.warn("copy failed", error);
    toast("复制失败，请手动选择文本复制", "error");
  }
}

function downloadVideo(url, id) {
  const value = String(url || "").trim();
  if (!value) return;
  const link = document.createElement("a");
  link.href = value;
  link.download = `${id || "video"}.mp4`;
  link.target = "_blank";
  link.rel = "noreferrer";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function bindEvents() {
  window.dfyueRefreshTempTokens = () => refreshTempTokens();
  window.dfyueOpenCreateTokenModal = () => openCreateTokenModal();
  window.dfyueCreateTempTokens = () => createTempTokens();

  els.loginForm.addEventListener("submit", login);
  els.copyTokenCommand?.addEventListener("click", async () => {
    await copyText(els.tokenCommand?.value?.trim() || "", "命令行");
  });
  els.copyClientEntryUrl?.addEventListener("click", async () => {
    await copyText(els.clientEntryUrl?.value?.trim() || getClientEntryUrl(), "客户入口");
  });
  els.clientEntryUrl?.addEventListener("dblclick", () => {
    els.clientEntryUrl.select();
  });
  els.tokenCommand?.addEventListener("dblclick", () => {
    els.tokenCommand.select();
  });
  if (els.logoutButton) els.logoutButton.addEventListener("click", logout);

  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });

  els.refreshTasks.addEventListener("click", () => refreshTasks());
  els.queryVisibleTasks.addEventListener("click", queryVisibleTasks);
  els.clearTasks.addEventListener("click", clearTasks);
  els.loadProxyConfig?.addEventListener("click", async () => {
    setBusy(els.loadProxyConfig, true, "读取中");
    try {
      await loadProxyConfig();
      toast("代理配置已读取");
    } catch (error) {
      toast(`读取失败：${error.message}`, "error");
    } finally {
      setBusy(els.loadProxyConfig, false);
    }
  });
  els.saveProxyConfig?.addEventListener("click", saveProxyConfig);
  els.checkUpdate?.addEventListener("click", async () => {
    setBusy(els.checkUpdate, true, "检查中");
    try {
      const data = await checkUpdateStatus({ checkRemote: true });
      const current = data?.commit || "-";
      const remote = data?.remote_commit || "-";
      toast(data?.has_update ? `\u53d1\u73b0\u65b0\u7248\u672c\uff1a${current} -> ${remote}` : `\u68c0\u67e5\u5b8c\u6210\uff1a\u5f53\u524d ${current}\uff0c\u8fdc\u7aef ${remote}\uff0c\u4e0d\u4f1a\u81ea\u52a8\u66f4\u65b0`);
    } catch (error) {
      toast(`检查失败：${error.message}`, "error");
      if (els.updateState) els.updateState.textContent = "检查失败";
    } finally {
      setBusy(els.checkUpdate, false);
    }
  });
  els.applyUpdate?.addEventListener("click", applyHotUpdate);
  els.openApiDocs?.addEventListener("click", openApiDocumentation);
  els.refreshTempTokens?.addEventListener("click", () => refreshTempTokens());
  els.openCreateTokenModal?.addEventListener("click", openCreateTokenModal);
  els.editWorkers.addEventListener("click", openWorkersModal);
  els.closeWorkersModal.addEventListener("click", closeWorkersModal);
  els.cancelWorkersModal.addEventListener("click", closeWorkersModal);
  els.workersModal.addEventListener("click", (event) => {
    if (event.target === els.workersModal) closeWorkersModal();
  });
  els.closeTextModal.addEventListener("click", closeTextModal);
  els.confirmTextModal.addEventListener("click", closeTextModal);
  els.copyTextModal.addEventListener("click", async () => {
    await copyText(state.modalText, "完整文本");
  });
  els.textModal.addEventListener("click", (event) => {
    if (event.target === els.textModal) closeTextModal();
  });
  els.closeVideoModal.addEventListener("click", closeVideoModal);
  els.confirmVideoModal.addEventListener("click", closeVideoModal);
  els.copyVideoUrl.addEventListener("click", async () => {
    await copyText(state.modalVideoUrl, "视频 URL");
  });
  els.videoModal.addEventListener("click", (event) => {
    if (event.target === els.videoModal) closeVideoModal();
  });
  els.workersInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") saveWorkersConfig();
    if (event.key === "Escape") closeWorkersModal();
  });
  els.saveWorkers.addEventListener("click", saveWorkersConfig);
  els.closeCreateTokenModal?.addEventListener("click", closeCreateTokenModal);
  els.cancelCreateTokenModal?.addEventListener("click", closeCreateTokenModal);
  els.createTokenModal?.addEventListener("click", (event) => {
    if (event.target === els.createTokenModal) closeCreateTokenModal();
  });
  els.confirmCreateTokens?.addEventListener("click", createTempTokens);
  els.createTokenCount?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") createTempTokens();
    if (event.key === "Escape") closeCreateTokenModal();
  });
  els.createTokenLimit?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") createTempTokens();
    if (event.key === "Escape") closeCreateTokenModal();
  });

  els.tempTokenTableBody?.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const id = button.dataset.id;
    const action = button.dataset.action;
    try {
      if (action === "copy-temp-token") {
        const item = state.tempTokens.find((token) => token.id === id);
        if (item?.token) await copyText(item.token, "临时 Token");
      }
      if (action === "save-temp-token") {
        setBusy(button, true, "保存中");
        await saveTempTokenLimit(id);
      }
      if (action === "delete-temp-token") {
        await deleteTempTokenById(id);
      }
    } catch (error) {
      toast(`操作失败：${error.message}`, "error");
    } finally {
      if (action === "save-temp-token") setBusy(button, false);
    }
  });

  document.addEventListener("click", async (event) => {
    const target = event.target.closest("#refreshTempTokens, #openCreateTokenModal, #confirmCreateTokens, #tempTokenTableBody button[data-action]");
    if (!target) return;
    event.preventDefault();
    event.stopPropagation();
    if (target.id === "refreshTempTokens") {
      await refreshTempTokens();
      return;
    }
    if (target.id === "openCreateTokenModal") {
      openCreateTokenModal();
      return;
    }
    if (target.id === "confirmCreateTokens") {
      await createTempTokens();
      return;
    }

    const id = target.dataset.id;
    const action = target.dataset.action;
    try {
      if (action === "copy-temp-token") {
        const item = state.tempTokens.find((token) => token.id === id);
        if (item?.token) await copyText(item.token, "临时 Token");
      }
      if (action === "save-temp-token") {
        setBusy(target, true, "保存中");
        await saveTempTokenLimit(id);
      }
      if (action === "delete-temp-token") {
        await deleteTempTokenById(id);
      }
    } catch (error) {
      toast(`操作失败：${error.message}`, "error");
    } finally {
      if (action === "save-temp-token") setBusy(target, false);
    }
  }, true);

  els.ratioGroup.addEventListener("click", (event) => {
    const trigger = event.target.closest(".ratio-trigger");
    if (trigger) {
      els.ratioGroup.classList.toggle("open");
      return;
    }

    const button = event.target.closest("button[data-ratio]");
    if (!button) return;
    state.ratio = button.dataset.ratio;
    setSegmentValue(els.ratioGroup, "ratio", state.ratio);
    els.ratioGroup.classList.remove("open");
  });

  document.addEventListener("click", (event) => {
    if (!els.ratioGroup.contains(event.target)) {
      els.ratioGroup.classList.remove("open");
    }
  });

  els.taskForm.addEventListener("submit", submitTask);
  els.resetSubmit?.addEventListener("click", resetSubmitForm);
  els.clearImages?.addEventListener("click", () => {
    if (state.submitting) return;
    state.images = [];
    els.imageInput.value = "";
    renderImages();
  });
  els.imageInput.addEventListener("change", () => {
    if (state.submitting) return;
    setImages(els.imageInput.files).catch((error) => {
      toast(`图片读取失败：${error.message}`, "error");
      els.imageInput.value = "";
    });
  });

  els.taskSearch.addEventListener("input", () => {
    state.page = 1;
    renderTaskTable();
  });
  els.prevPage.addEventListener("click", () => {
    state.page -= 1;
    renderTaskTable();
  });
  els.nextPage.addEventListener("click", () => {
    state.page += 1;
    renderTaskTable();
  });

  els.taskTableBody.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const id = button.dataset.id;
    const action = button.dataset.action;
    if (action === "show-result") {
      const task = state.tasks.find((item) => item.id === id);
      const status = task ? getTaskStatus(task) : null;
      const text = status?.text || task?.error || "";
      openTextModal(text);
      return;
    }
    if (action === "query") {
      setBusy(button, true, "查询中");
      try {
        await queryTask(id);
      } catch (_) {
      } finally {
        setBusy(button, false);
      }
    }
    if (action === "copy-id") {
      await copyText(id, "任务 ID");
    }
    if (action === "copy-prompt") {
      const task = state.tasks.find((item) => item.id === id);
      const prompt = String(task?.prompt || task?.prompt_preview || "").trim();
      if (prompt) await copyText(prompt, "提示词");
    }
    if (action === "copy-url") {
      const url = state.results[id]?.url;
      if (url) await copyText(url, "视频 URL");
    }
    if (action === "open-video") {
      const url = state.results[id]?.url;
      if (url) openVideoModal(url);
    }
    if (action === "download-video") {
      const url = state.results[id]?.url;
      if (url) downloadVideo(url, id);
    }
    if (action === "delete") {
      await deleteTask(id);
    }
  });
}

async function init() {
  const params = new URLSearchParams(window.location.search);
  const tokenFromUrl = params.get("token") || "";
  const legacyToken = portal === "admin" ? localStorage.getItem(TOKEN_KEY) || localStorage.getItem("dola_fetch_api_token") || "" : "";
  const savedToken = tokenFromUrl || localStorage.getItem(portalStorageKey(TOKEN_KEY)) || legacyToken;
  els.loginToken.value = savedToken;

  applyPortalText();
  bindEvents();
  renderImages();
  renderTaskTable();
  updateDashboardMetrics();

  if (sessionStorage.getItem(portalStorageKey(AUTH_KEY)) === "1" && savedToken) {
    try {
      const health = await requestJson(authPath, savedToken);
      state.apiToken = savedToken;
      applyAccessScope(health);
      clearTokenFromUrl();
      showApp();
      await refreshDashboard();
    } catch (error) {
      state.apiToken = "";
      sessionStorage.removeItem(portalStorageKey(AUTH_KEY));
      showLogin("Token 已失效");
      toast(`自动登录失败：${error.message}`, "error");
    }
    return;
  }

  showLogin(savedToken ? "等待进入" : "等待输入");
}

init();
