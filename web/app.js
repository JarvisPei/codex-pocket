const elements = {
  drawerScrim: document.querySelector("#drawerScrim"),
  projectDrawer: document.querySelector("#projectDrawer"),
  openDrawerButton: document.querySelector("#openDrawerButton"),
  emptyOpenDrawerButton: document.querySelector("#emptyOpenDrawerButton"),
  closeDrawerButton: document.querySelector("#closeDrawerButton"),
  refreshThreadsButton: document.querySelector("#refreshThreadsButton"),
  projectsHint: document.querySelector("#projectsHint"),
  projectGroups: document.querySelector("#projectGroups"),
  refreshUsageButton: document.querySelector("#refreshUsageButton"),
  usageContent: document.querySelector("#usageContent"),
  notificationButton: document.querySelector("#notificationButton"),
  notificationStatus: document.querySelector("#notificationStatus"),
  deviceDot: document.querySelector("#deviceDot"),
  deviceStatus: document.querySelector("#deviceStatus"),
  macBattery: document.querySelector("#macBattery"),
  connectionLatency: document.querySelector("#connectionLatency"),
  selectedProjectName: document.querySelector("#selectedProjectName"),
  selectedThreadTitle: document.querySelector("#selectedThreadTitle"),
  liveBadge: document.querySelector("#liveBadge"),
  liveBadgeLabel: document.querySelector("#liveBadgeLabel"),
  taskStateDot: document.querySelector("#taskStateDot"),
  refreshConversationButton: document.querySelector("#refreshConversationButton"),
  emptyState: document.querySelector("#emptyState"),
  threadView: document.querySelector("#threadView"),
  threadMeta: document.querySelector("#threadMeta"),
  threadHistory: document.querySelector("#threadHistory"),
  managedLiveHistory: document.querySelector("#managedLiveHistory"),
  historyNotice: document.querySelector("#historyNotice"),
  composerState: document.querySelector("#composerState"),
  composerAttachments: document.querySelector("#composerAttachments"),
  attachmentButton: document.querySelector("#attachmentButton"),
  attachmentInput: document.querySelector("#attachmentInput"),
  composerInput: document.querySelector("#composerInput"),
  composerMode: document.querySelector("#composerMode"),
  modelSettingsButton: document.querySelector("#modelSettingsButton"),
  modelSettingsLabel: document.querySelector("#modelSettingsLabel"),
  composerActionButton: document.querySelector("#composerActionButton"),
  composerActionIcon: document.querySelector("#composerActionIcon"),
  conversationHeader: document.querySelector(".conversation-header"),
  composerShell: document.querySelector(".composer-shell"),
  newContentButton: document.querySelector("#newContentButton"),
  latestButtonLabel: document.querySelector("#latestButtonLabel"),
  scrollRail: document.querySelector("#scrollRail"),
  scrollThumb: document.querySelector("#scrollThumb"),
  stopDialog: document.querySelector("#stopDialog"),
  confirmTaskTitle: document.querySelector("#confirmTaskTitle"),
  cancelButton: document.querySelector("#cancelButton"),
  confirmButton: document.querySelector("#confirmButton"),
  newTaskDialog: document.querySelector("#newTaskDialog"),
  newTaskForm: document.querySelector("#newTaskForm"),
  newTaskDestination: document.querySelector("#newTaskDestination"),
  newTaskMessage: document.querySelector("#newTaskMessage"),
  newTaskAttachments: document.querySelector("#newTaskAttachments"),
  newTaskAttachmentButton: document.querySelector("#newTaskAttachmentButton"),
  newTaskAttachmentInput: document.querySelector("#newTaskAttachmentInput"),
  newTaskModelHint: document.querySelector("#newTaskModelHint"),
  newTaskError: document.querySelector("#newTaskError"),
  newTaskCancel: document.querySelector("#newTaskCancel"),
  newTaskSubmit: document.querySelector("#newTaskSubmit"),
  modelSettingsDialog: document.querySelector("#modelSettingsDialog"),
  modelSettingsForm: document.querySelector("#modelSettingsForm"),
  modelSelect: document.querySelector("#modelSelect"),
  modelDescription: document.querySelector("#modelDescription"),
  effortSelect: document.querySelector("#effortSelect"),
  effortDescription: document.querySelector("#effortDescription"),
  fastModeRow: document.querySelector("#fastModeRow"),
  fastModeInput: document.querySelector("#fastModeInput"),
  fastModeDescription: document.querySelector("#fastModeDescription"),
  modelSettingsError: document.querySelector("#modelSettingsError"),
  modelSettingsCancel: document.querySelector("#modelSettingsCancel"),
  modelSettingsSave: document.querySelector("#modelSettingsSave"),
  tokenDialog: document.querySelector("#tokenDialog"),
  tokenForm: document.querySelector("#tokenForm"),
  tokenInput: document.querySelector("#tokenInput"),
  tokenError: document.querySelector("#tokenError"),
  tokenButton: document.querySelector("#tokenButton"),
  tokenCancel: document.querySelector("#tokenCancel"),
};

const DEVICE_TOKEN_KEY = "mobileCodexDeviceToken";
const LEGACY_TOKEN_KEY = "mobileCodexBridgeToken";
const PAIRING_TICKET_KEY = "mobileCodexPairingTicket";
const SELECTED_THREAD_KEY = "mobileCodexSelectedThread";
const COLLAPSED_PROJECTS_KEY = "mobileCodexCollapsedProjects";
const NOTIFICATIONS_ENABLED_KEY = "mobileCodexNotificationsEnabled";
const INITIAL_HISTORY_TURNS = 30;
const MAX_HISTORY_TURNS = 60;
const THREAD_CACHE_TTL_MS = 60_000;
const MAX_ATTACHMENTS_PER_TURN = 4;
const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;

const fragmentParameters = new URLSearchParams(window.location.hash.slice(1));
const pairedLegacyToken = fragmentParameters.get("token") || "";
const pairedTicket = fragmentParameters.get("pairing") || "";
if (pairedLegacyToken.length >= 32) {
  sessionStorage.setItem(LEGACY_TOKEN_KEY, pairedLegacyToken);
}
if (pairedTicket.startsWith("pair1.")) {
  sessionStorage.setItem(PAIRING_TICKET_KEY, pairedTicket);
}
if (window.location.hash) {
  history.replaceState(
    null,
    "",
    `${window.location.pathname}${window.location.search}`,
  );
}

let deviceToken = localStorage.getItem(DEVICE_TOKEN_KEY) || "";
let legacyToken = sessionStorage.getItem(LEGACY_TOKEN_KEY) || "";
let pairingTicket = sessionStorage.getItem(PAIRING_TICKET_KEY) || "";
let currentTaskTitle = "";
let currentStopCandidates = 0;
let desktopStatusKnown = false;
let projects = [];
let threads = [];
let selectedThread;
let selectedThreadLastTurnId = "";
let selectedThreadLastTurnStatus = "";
let selectedThreadHasFinalAnswer = false;
let selectedThreadRuntimeStatus = "";
let desktopDispatchState;
let managedRun;
let desktopRequest;
let enrollmentPromise;
let refreshTimer;
let managedPollTimer;
let desktopHistoryPollTimer;
let drawerStatusPollTimer;
let threadCatalogRetryTimer;
let desktopHistoryRefreshInFlight = false;
let statusRefreshPromise;
let drawerStatusRefreshPromise;
let usageRefreshPromise;
let usageLastRefreshedAt = 0;
let systemMetricsRefreshPromise;
let systemMetricsLastRefreshedAt = 0;
const bridgeLatencySamples = [];
let isSendingMessage = false;
let isUploadingAttachments = false;
let isCreatingTask = false;
let isUploadingNewTaskAttachments = false;
let newTaskTarget;
let newTaskAttachments = [];
let managedRenderSignature = "";
let scrollSyncFrame = 0;
let scrollDrag;
let desktopActivityEvidence;
let hasUnseenNewContent = false;
let isScrollingToLatest = false;
let modelSettingsLoadingThreadId = "";
let notificationsEnabled = localStorage.getItem(NOTIFICATIONS_ENABLED_KEY) === "true";
let serviceWorkerRegistrationPromise;
let notificationThreadId = new URLSearchParams(window.location.search).get("thread") || "";
let threadNotificationsPrimed = false;
let desktopRequestNotificationsPrimed = false;
const completedRunsSeen = new Set();
const persistedManagedTurnIds = new Set();
const threadDrafts = new Map();
const threadAttachments = new Map();
const threadHistoryCache = new Map();
const renderedThreadSignatures = new Map();
const modelSettingsCache = new Map();
const expandedWorkedGroups = new Set();
const threadNotificationStates = new Map();
let collapsedProjects = (() => {
  try {
    const stored = JSON.parse(localStorage.getItem(COLLAPSED_PROJECTS_KEY) || "[]");
    return new Set(Array.isArray(stored) ? stored.map(String) : []);
  } catch {
    return new Set();
  }
})();

function setDeviceState(kind, label) {
  elements.deviceDot.className = `connection-dot ${kind}`;
  elements.deviceStatus.textContent = label;
}

function authorizationHeaders() {
  return { Authorization: `Bearer ${deviceToken}` };
}

function renderMacBattery(battery) {
  if (!battery?.available || !Number.isFinite(Number(battery.percent))) {
    elements.macBattery.textContent = "电量不可用";
    return;
  }
  const percent = Math.min(100, Math.max(0, Math.round(Number(battery.percent))));
  const state = {
    charging: "充电",
    discharging: "放电",
    full: "已满",
  }[battery.state] || "";
  elements.macBattery.textContent = `电量 ${percent}%${state ? ` · ${state}` : ""}`;
}

function renderConnectionLatency(milliseconds) {
  elements.connectionLatency.className = "connection-latency";
  if (!Number.isFinite(milliseconds)) {
    elements.connectionLatency.classList.add("error");
    elements.connectionLatency.textContent = "延迟不可用";
    return;
  }
  const rounded = Math.max(1, Math.round(milliseconds));
  let quality = "较弱";
  let qualityClass = "poor";
  if (rounded <= 80) {
    quality = "优秀";
    qualityClass = "excellent";
  } else if (rounded <= 180) {
    quality = "良好";
    qualityClass = "good";
  } else if (rounded <= 400) {
    quality = "一般";
    qualityClass = "fair";
  }
  elements.connectionLatency.classList.add(qualityClass);
  elements.connectionLatency.textContent = `延迟 ${rounded} ms · ${quality}`;
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 5_000) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    window.clearTimeout(timeout);
  }
}

async function measureBridgeLatency() {
  const startedAt = performance.now();
  const response = await fetchWithTimeout(
    `/health?probe=${Date.now()}`,
    { cache: "no-store" },
  );
  if (!response.ok) throw new Error("health probe failed");
  await response.json();
  return performance.now() - startedAt;
}

async function refreshSystemMetrics(force = false) {
  if (!deviceToken || systemMetricsRefreshPromise) return systemMetricsRefreshPromise;
  if (!force && Date.now() - systemMetricsLastRefreshedAt < 10_000) return;
  systemMetricsRefreshPromise = (async () => {
    const [latencyResult, metricsResult] = await Promise.allSettled([
      measureBridgeLatency(),
      fetchWithTimeout("/api/system/metrics", {
        headers: authorizationHeaders(),
        cache: "no-store",
      }),
    ]);
    if (latencyResult.status === "fulfilled") {
      bridgeLatencySamples.push(latencyResult.value);
      if (bridgeLatencySamples.length > 5) bridgeLatencySamples.shift();
      const sorted = [...bridgeLatencySamples].sort((left, right) => left - right);
      renderConnectionLatency(sorted[Math.floor(sorted.length / 2)]);
    } else {
      renderConnectionLatency(Number.NaN);
    }
    if (metricsResult.status === "fulfilled") {
      const response = metricsResult.value;
      if (response.status === 401) {
        handleUnauthorized();
        return;
      }
      if (response.ok) {
        try {
          const result = await response.json();
          renderMacBattery(result.battery);
        } catch {
          renderMacBattery(undefined);
        }
      } else {
        renderMacBattery(undefined);
      }
    } else {
      renderMacBattery(undefined);
    }
    systemMetricsLastRefreshedAt = Date.now();
  })();
  try {
    return await systemMetricsRefreshPromise;
  } finally {
    systemMetricsRefreshPromise = undefined;
  }
}

function notificationSupportAvailable() {
  return Boolean(
    window.isSecureContext
    && "Notification" in window
    && "serviceWorker" in navigator,
  );
}

function registerNotificationWorker() {
  if (!notificationSupportAvailable()) return Promise.resolve(undefined);
  if (!serviceWorkerRegistrationPromise) {
    serviceWorkerRegistrationPromise = navigator.serviceWorker.register("/sw.js");
  }
  return serviceWorkerRegistrationPromise;
}

function setNotificationButtonState(label) {
  elements.notificationStatus.textContent = label;
  elements.notificationButton.title = label;
  elements.notificationButton.setAttribute("aria-label", `系统通知：${label}`);
}

function renderNotificationState() {
  elements.notificationButton.classList.remove("enabled", "blocked");
  if (!notificationSupportAvailable()) {
    elements.notificationButton.classList.add("blocked");
    setNotificationButtonState("当前浏览器不支持");
    return;
  }
  if (Notification.permission === "denied") {
    notificationsEnabled = false;
    localStorage.removeItem(NOTIFICATIONS_ENABLED_KEY);
    elements.notificationButton.classList.add("blocked");
    setNotificationButtonState("已被浏览器阻止，请在网站设置中允许");
    return;
  }
  if (notificationsEnabled && Notification.permission !== "granted") {
    notificationsEnabled = false;
    localStorage.removeItem(NOTIFICATIONS_ENABLED_KEY);
  }
  if (notificationsEnabled && Notification.permission === "granted") {
    elements.notificationButton.classList.add("enabled");
    setNotificationButtonState("已开启 · 页面留在后台即可提醒");
    return;
  }
  setNotificationButtonState(Notification.permission === "granted"
    ? "已关闭 · 点此开启"
    : "点此开启");
}

async function showSystemNotification(title, options = {}) {
  if (
    !notificationsEnabled
    || !notificationSupportAvailable()
    || Notification.permission !== "granted"
  ) return;
  try {
    const registration = await registerNotificationWorker();
    if (!registration) return;
    await registration.showNotification(title, {
      body: options.body || "",
      tag: options.tag || "codex-pocket",
      data: {
        threadId: options.threadId || "",
        url: options.threadId
          ? `/?thread=${encodeURIComponent(options.threadId)}`
          : "/",
      },
    });
  } catch {
    // Notification delivery is best effort; task polling must keep running.
  }
}

function threadNotificationState(thread) {
  const activityStatus = String(thread?.activityStatus || "");
  return {
    title: String(thread?.title || "未命名任务"),
    activityStatus,
    updatedAt: String(thread?.updatedAt || ""),
    running: activityStatus === "inProgress",
  };
}

function observeThreadNotificationStates(nextThreads, { prime = false } = {}) {
  const nextIds = new Set();
  for (const thread of nextThreads || []) {
    if (!thread?.id) continue;
    const threadId = String(thread.id);
    nextIds.add(threadId);
    const next = threadNotificationState(thread);
    const previous = threadNotificationStates.get(threadId);
    threadNotificationStates.set(threadId, next);
    if (prime || !threadNotificationsPrimed || !previous || next.running) {
      continue;
    }
    const observedTerminalTransition = previous.running;
    const observedNewTerminalUpdate = Boolean(
      previous.updatedAt
      && next.updatedAt
      && previous.updatedAt !== next.updatedAt,
    );
    if (!observedTerminalTransition && !observedNewTerminalUpdate) continue;
    const eventIdentity = next.updatedAt || Date.now();
    if (next.activityStatus === "completed") {
      showSystemNotification("Codex 任务已完成", {
        body: next.title,
        threadId,
        tag: `codex-completed-${threadId}-${eventIdentity}`,
      });
    } else if (next.activityStatus === "interrupted") {
      showSystemNotification("Codex 任务已暂停", {
        body: next.title,
        threadId,
        tag: `codex-interrupted-${threadId}-${eventIdentity}`,
      });
    } else if (next.activityStatus === "failed") {
      showSystemNotification("Codex 任务失败", {
        body: next.title,
        threadId,
        tag: `codex-failed-${threadId}-${eventIdentity}`,
      });
    }
  }
  for (const threadId of threadNotificationStates.keys()) {
    if (!nextIds.has(threadId)) threadNotificationStates.delete(threadId);
  }
  if (prime || !threadNotificationsPrimed) threadNotificationsPrimed = true;
}

async function toggleSystemNotifications() {
  if (!notificationSupportAvailable()) {
    renderNotificationState();
    return;
  }
  if (notificationsEnabled && Notification.permission === "granted") {
    notificationsEnabled = false;
    localStorage.removeItem(NOTIFICATIONS_ENABLED_KEY);
    renderNotificationState();
    return;
  }
  let permission = Notification.permission;
  if (permission === "default") permission = await Notification.requestPermission();
  if (permission !== "granted") {
    notificationsEnabled = false;
    localStorage.removeItem(NOTIFICATIONS_ENABLED_KEY);
    renderNotificationState();
    return;
  }
  try {
    await registerNotificationWorker();
  } catch {
    elements.notificationButton.classList.add("blocked");
    setNotificationButtonState("通知服务注册失败，请刷新后重试");
    return;
  }
  notificationsEnabled = true;
  localStorage.setItem(NOTIFICATIONS_ENABLED_KEY, "true");
  observeThreadNotificationStates(threads, { prime: true });
  renderNotificationState();
  await showSystemNotification("Codex Pocket 通知已开启", {
    body: "任务完成、暂停或需要确认时会提醒你。",
    tag: "codex-notifications-enabled",
  });
}

function defaultDeviceName() {
  const platform = navigator.userAgentData?.platform || navigator.platform || "";
  if (/android/i.test(navigator.userAgent)) return "Android 浏览器";
  if (platform) return `${platform} 浏览器`;
  return "移动浏览器";
}

function openDrawer() {
  elements.projectDrawer.classList.add("open");
  elements.drawerScrim.hidden = false;
  refreshDrawerThreadStates();
  refreshUsage();
  refreshSystemMetrics();
}

function closeDrawer() {
  elements.projectDrawer.classList.remove("open");
  elements.drawerScrim.hidden = true;
}

function updateProjectsHint() {
  const projectCount = projects.length;
  const recentCount = threads.filter(
    (thread) => thread.collection !== "project",
  ).length;
  elements.projectsHint.textContent = threads.length
    ? `${projectCount} 个项目 · ${recentCount} 个最近任务`
    : "没有找到持久化任务";
}

function reconcileThreadCatalog(result) {
  if (!Array.isArray(result.projects) || !Array.isArray(result.threads)) return false;
  observeThreadNotificationStates(result.threads, {
    prime: !threadNotificationsPrimed,
  });
  projects = result.projects;
  const existing = new Map(threads.map((thread) => [thread.id, thread]));
  threads = result.threads.map((update) => {
    const current = existing.get(update.id);
    if (!current) return update;
    Object.assign(current, update);
    return current;
  });
  if (selectedThread?.id) {
    selectedThread = threads.find((thread) => thread.id === selectedThread.id)
      || selectedThread;
  }
  updateProjectsHint();
  return true;
}

async function refreshDrawerThreadStates() {
  if (!deviceToken || drawerStatusRefreshPromise) return drawerStatusRefreshPromise;
  drawerStatusRefreshPromise = (async () => {
    try {
      const response = await fetch("/api/codex/threads?limit=50", {
        headers: authorizationHeaders(),
        cache: "no-store",
      });
      if (!response.ok) return;
      const result = await response.json();
      if (!reconcileThreadCatalog(result)) return;
      if (elements.projectDrawer.classList.contains("open")) renderProjectGroups();
    } catch {
      // Keep the last known drawer state; the main status indicator reports outages.
    }
  })();
  try {
    return await drawerStatusRefreshPromise;
  } finally {
    drawerStatusRefreshPromise = undefined;
  }
}

function formatRemainingPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${Math.round(number)}%`;
}

function formatUsageReset(unixSeconds) {
  const date = new Date(Number(unixSeconds) * 1000);
  if (!Number.isFinite(date.getTime())) return "重置时间未知";
  return `重置于 ${new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date)}`;
}

function usageWindowLabel(durationMinutes) {
  const minutes = Number(durationMinutes);
  if (minutes === 10_080) return "7 天";
  if (minutes === 1_440) return "24 小时";
  if (minutes >= 60 && minutes % 60 === 0) return `${minutes / 60} 小时`;
  return minutes > 0 ? `${minutes} 分钟` : "额度";
}

function renderUsage(usage) {
  elements.usageContent.replaceChildren();
  const limits = Array.isArray(usage?.limits) ? usage.limits : [];
  if (!limits.length) {
    const placeholder = document.createElement("span");
    placeholder.className = "usage-placeholder";
    placeholder.textContent = "暂时没有可展示的用量信息";
    elements.usageContent.append(placeholder);
    return;
  }
  for (const limit of limits) {
    const window = limit.primary || limit.secondary;
    if (!window) continue;
    const item = document.createElement("div");
    item.className = "usage-limit";
    const line = document.createElement("div");
    line.className = "usage-limit-line";
    const name = document.createElement("strong");
    name.textContent = limit.name || (limit.isDefault ? "Codex" : limit.id);
    const remaining = document.createElement("span");
    remaining.textContent = `${formatRemainingPercent(window.remainingPercent)} 剩余`;
    line.append(name, remaining);
    const bar = document.createElement("div");
    bar.className = "usage-bar";
    const fill = document.createElement("span");
    fill.style.width = `${Math.min(100, Math.max(0, Number(window.remainingPercent) || 0))}%`;
    bar.append(fill);
    const reset = document.createElement("div");
    reset.className = "usage-reset";
    reset.textContent = `${usageWindowLabel(window.windowDurationMins)} · ${formatUsageReset(window.resetsAt)}`;
    item.append(line, bar, reset);
    elements.usageContent.append(item);
  }
}

async function refreshUsage(force = false) {
  if (!deviceToken || usageRefreshPromise) return usageRefreshPromise;
  if (!force && Date.now() - usageLastRefreshedAt < 60_000) return;
  usageRefreshPromise = (async () => {
    elements.refreshUsageButton.disabled = true;
    elements.refreshUsageButton.classList.add("refreshing");
    try {
      const response = await fetch("/api/codex/usage", {
        headers: authorizationHeaders(),
        cache: "no-store",
      });
      if (response.status === 401) {
        handleUnauthorized();
        return;
      }
      if (!response.ok) throw new Error("usage read failed");
      const result = await response.json();
      renderUsage(result.usage);
      usageLastRefreshedAt = Date.now();
    } catch {
      elements.usageContent.replaceChildren();
      const placeholder = document.createElement("span");
      placeholder.className = "usage-placeholder";
      placeholder.textContent = "剩余用量暂时不可用";
      elements.usageContent.append(placeholder);
    } finally {
      elements.refreshUsageButton.disabled = false;
      elements.refreshUsageButton.classList.remove("refreshing");
    }
  })();
  try {
    return await usageRefreshPromise;
  } finally {
    usageRefreshPromise = undefined;
  }
}

function effortLabel(effort) {
  return {
    low: "Low",
    medium: "Medium",
    high: "High",
    xhigh: "XHigh",
    max: "Max",
    ultra: "Ultra",
  }[effort] || effort || "";
}

function shortModelName(displayName, fallback = "") {
  return String(displayName || fallback)
    .replace(/^GPT-/i, "")
    .replaceAll("-", " ");
}

function renderModelSettingsButton() {
  const entry = selectedThread ? modelSettingsCache.get(selectedThread.id) : undefined;
  const settings = entry?.settings;
  const model = entry?.models?.find((candidate) => candidate.id === settings?.model);
  if (!selectedThread) {
    elements.modelSettingsLabel.textContent = "模型…";
    return;
  }
  if (!settings || !model) {
    elements.modelSettingsLabel.textContent = (
      modelSettingsLoadingThreadId === selectedThread.id ? "读取模型…" : "模型…"
    );
    return;
  }
  const fast = settings.serviceTier === "priority" ? "⚡ " : "";
  elements.modelSettingsLabel.textContent = (
    `${fast}${shortModelName(model.displayName, model.id)} ${effortLabel(settings.effort)}`
  );
}

async function refreshModelSettings(threadId, force = false) {
  if (!threadId || !deviceToken) return undefined;
  const cached = modelSettingsCache.get(threadId);
  if (!force && cached && Date.now() - cached.fetchedAt < 60_000) {
    if (selectedThread?.id === threadId) renderModelSettingsButton();
    return cached;
  }
  modelSettingsLoadingThreadId = threadId;
  if (selectedThread?.id === threadId) {
    renderModelSettingsButton();
    updateComposerState();
  }
  try {
    const response = await fetch(
      `/api/codex/models?threadId=${encodeURIComponent(threadId)}`,
      { headers: authorizationHeaders(), cache: "no-store" },
    );
    if (response.status === 401) {
      handleUnauthorized();
      return undefined;
    }
    if (!response.ok) throw new Error("model settings read failed");
    const result = await response.json();
    const entry = {
      models: result.models || [],
      settings: result.settings || {},
      fetchedAt: Date.now(),
    };
    modelSettingsCache.set(threadId, entry);
    return entry;
  } catch {
    return undefined;
  } finally {
    if (modelSettingsLoadingThreadId === threadId) modelSettingsLoadingThreadId = "";
    if (selectedThread?.id === threadId) {
      renderModelSettingsButton();
      updateComposerState();
    }
  }
}

function showTokenDialog(message = "") {
  elements.tokenInput.value = "";
  elements.tokenError.textContent = message;
  if (!elements.tokenDialog.open) elements.tokenDialog.showModal();
  setTimeout(() => elements.tokenInput.focus(), 0);
}

function clearBootstrapCredentials() {
  legacyToken = "";
  pairingTicket = "";
  sessionStorage.removeItem(LEGACY_TOKEN_KEY);
  sessionStorage.removeItem(PAIRING_TICKET_KEY);
}

async function enrollDevice() {
  if (deviceToken) return true;
  if (enrollmentPromise) return enrollmentPromise;
  if (!legacyToken && !pairingTicket) return false;

  enrollmentPromise = (async () => {
    const headers = { "Content-Type": "application/json" };
    const body = { name: defaultDeviceName() };
    if (legacyToken) headers.Authorization = `Bearer ${legacyToken}`;
    if (pairingTicket) body.pairingTicket = pairingTicket;
    const response = await fetch("/api/devices/enroll", {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok || !result.device?.deviceToken) {
      clearBootstrapCredentials();
      throw new Error("device enrollment failed");
    }
    deviceToken = result.device.deviceToken;
    localStorage.setItem(DEVICE_TOKEN_KEY, deviceToken);
    clearBootstrapCredentials();
    return true;
  })();

  try {
    return await enrollmentPromise;
  } finally {
    enrollmentPromise = undefined;
  }
}

function handleUnauthorized() {
  deviceToken = "";
  localStorage.removeItem(DEVICE_TOKEN_KEY);
  setDeviceState("error", "设备授权已失效");
  showTokenDialog("请从 Mac 重新生成一次性配对二维码。");
}

function formatThreadTime(value) {
  if (!value) return "时间未知";
  const date = typeof value === "number"
    ? new Date(value < 1e12 ? value * 1000 : value)
    : new Date(value);
  if (Number.isNaN(date.valueOf())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function localizedThreadStatus(status) {
  const value = status?.type || status || "unknown";
  return {
    active: "运行中",
    idle: "已载入",
    notLoaded: "已保存",
    systemError: "异常",
  }[value] || value;
}

function uniqueCurrentThreadId() {
  if (!currentTaskTitle) return "";
  const matches = threads.filter((thread) => thread.title === currentTaskTitle);
  return matches.length === 1 ? matches[0].id : "";
}

const ACTIVE_TURN_STATUSES = new Set([
  "starting", "inProgress", "waitingForInput", "interrupting",
]);

function managedRunIsActive(run = managedRun) {
  return ACTIVE_TURN_STATUSES.has(run?.status);
}

function desktopDispatchIsPending() {
  return (
    desktopDispatchState?.threadId === selectedThread?.id
    && Date.now() - desktopDispatchState.startedAt < 30_000
  );
}

function desktopActivityIsRecent() {
  return (
    desktopActivityEvidence?.threadId === selectedThread?.id
    && Date.now() - desktopActivityEvidence.lastSeenAt < 15_000
  );
}

function desktopActivityIsUnresolved() {
  return desktopActivityEvidence?.threadId === selectedThread?.id;
}

function selectedThreadHasActiveTurn() {
  if (!selectedThread) return false;
  if (managedRun?.threadId === selectedThread.id && managedRunIsActive()) return true;
  if (desktopDispatchIsPending()) return true;
  if (desktopActivityIsRecent()) return true;
  if (ACTIVE_TURN_STATUSES.has(selectedThreadLastTurnStatus)) return true;
  return (
    selectedThreadRuntimeStatus === "active"
    && !selectedThreadHasFinalAnswer
    && selectedThreadLastTurnStatus !== "completed"
  );
}

function selectedThreadIsPaused() {
  if (!selectedThread) return false;
  if (selectedThreadHasActiveTurn()) return false;
  if (desktopActivityIsUnresolved()) return false;
  if (managedRun?.threadId === selectedThread.id) {
    if (managedRun.status === "interrupted") return true;
    if (["completed", "failed"].includes(managedRun.status)) return false;
  }
  return selectedThreadLastTurnStatus === "interrupted";
}

function selectedThreadIsComplete() {
  if (!selectedThread || selectedThreadHasActiveTurn()) return false;
  if (desktopActivityIsUnresolved()) return false;
  if (managedRun?.threadId === selectedThread.id) {
    if (managedRun.status === "completed") return true;
    if (["interrupted", "failed"].includes(managedRun.status)) return false;
  }
  return selectedThreadHasFinalAnswer || selectedThreadLastTurnStatus === "completed";
}

function setComposerDisabled(disabled) {
  if (elements.composerInput.disabled !== disabled) {
    elements.composerInput.disabled = disabled;
  }
}

function attachmentsForThread(threadId) {
  return threadId ? (threadAttachments.get(threadId) || []) : [];
}

function selectedAttachments() {
  return attachmentsForThread(selectedThread?.id);
}

function composerHasContent() {
  return Boolean(elements.composerInput.value.trim() || selectedAttachments().length);
}

function formatAttachmentSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function removeAttachment(threadId, attachmentId) {
  const attachments = attachmentsForThread(threadId);
  const attachment = attachments.find((item) => item.id === attachmentId);
  if (!attachment) return;
  attachment.removing = true;
  renderComposerAttachments();
  try {
    const response = await fetch(
      `/api/attachments/${encodeURIComponent(attachmentId)}`,
      { method: "DELETE", headers: authorizationHeaders() },
    );
    if (response.status === 401) {
      handleUnauthorized();
      return;
    }
    if (!response.ok && response.status !== 404) throw new Error("delete failed");
    const remaining = attachments.filter((item) => item.id !== attachmentId);
    if (remaining.length) threadAttachments.set(threadId, remaining);
    else threadAttachments.delete(threadId);
  } catch {
    attachment.removing = false;
    if (selectedThread?.id === threadId) {
      elements.composerState.textContent = "附件移除失败，请稍后重试";
    }
  }
  renderComposerAttachments();
  updateComposerState();
}

function renderComposerAttachments() {
  const attachments = selectedAttachments();
  elements.composerAttachments.replaceChildren();
  elements.composerAttachments.hidden = attachments.length === 0;
  for (const attachment of attachments) {
    const chip = document.createElement("div");
    chip.className = "attachment-chip";
    chip.title = `${attachment.name} · ${formatAttachmentSize(attachment.size)}`;
    const name = document.createElement("span");
    name.className = "attachment-chip-name";
    name.textContent = attachment.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "attachment-chip-remove";
    remove.textContent = "×";
    remove.disabled = Boolean(attachment.removing || isSendingMessage);
    remove.setAttribute("aria-label", `移除附件 ${attachment.name}`);
    remove.addEventListener("click", () => {
      removeAttachment(selectedThread?.id, attachment.id);
    });
    chip.append(name, remove);
    elements.composerAttachments.append(chip);
  }
  resizeComposer();
}

async function uploadSelectedAttachments(files) {
  if (!selectedThread || isUploadingAttachments || !files.length) return;
  const threadId = selectedThread.id;
  const existing = attachmentsForThread(threadId);
  const availableSlots = MAX_ATTACHMENTS_PER_TURN - existing.length;
  const selectedFiles = Array.from(files).slice(0, Math.max(0, availableSlots));
  if (!selectedFiles.length) {
    elements.composerState.textContent = `每条指令最多 ${MAX_ATTACHMENTS_PER_TURN} 个附件`;
    return;
  }
  isUploadingAttachments = true;
  updateComposerState();
  let feedback = "";
  for (const file of selectedFiles) {
    if (!file.size || file.size > MAX_ATTACHMENT_BYTES) {
      feedback = `${file.name} 超过 20 MB 或为空文件`;
      continue;
    }
    try {
      if (selectedThread?.id === threadId) {
        elements.composerState.textContent = `正在上传 ${file.name}…`;
      }
      const response = await fetch("/api/attachments", {
        method: "POST",
        headers: {
          ...authorizationHeaders(),
          "Content-Type": file.type || "application/octet-stream",
          "X-Codex-Filename": encodeURIComponent(file.name),
        },
        body: file,
      });
      if (response.status === 401) {
        handleUnauthorized();
        feedback = "设备授权已失效，请重新配对";
        break;
      }
      const result = await response.json();
      if (!response.ok || !result.attachment) {
        feedback = response.status === 413
          ? `${file.name} 超过 20 MB`
          : `${file.name} 上传失败`;
        continue;
      }
      threadAttachments.set(threadId, [
        ...attachmentsForThread(threadId),
        result.attachment,
      ]);
      if (selectedThread?.id === threadId) renderComposerAttachments();
    } catch {
      feedback = `${file.name} 上传失败`;
    }
  }
  isUploadingAttachments = false;
  elements.attachmentInput.value = "";
  renderComposerAttachments();
  updateComposerState();
  if (feedback && selectedThread?.id === threadId) {
    elements.composerState.textContent = feedback;
  }
}

function resizeComposer() {
  const viewportHeight = window.visualViewport?.height || window.innerHeight;
  const maxHeight = Math.min(180, Math.max(96, viewportHeight * 0.3));
  elements.composerInput.style.height = "auto";
  const nextHeight = Math.min(elements.composerInput.scrollHeight, maxHeight);
  elements.composerInput.style.height = `${Math.max(43, nextHeight)}px`;
  elements.composerInput.style.overflowY = (
    elements.composerInput.scrollHeight > maxHeight ? "auto" : "hidden"
  );
  scheduleScrollHandle();
}

function selectedModelDialogEntry() {
  return selectedThread ? modelSettingsCache.get(selectedThread.id) : undefined;
}

function syncEffortDescription() {
  const entry = selectedModelDialogEntry();
  const model = entry?.models?.find(
    (candidate) => candidate.id === elements.modelSelect.value,
  );
  const effort = model?.efforts?.find(
    (candidate) => candidate.id === elements.effortSelect.value,
  );
  elements.effortDescription.textContent = effort?.description || "";
}

function syncModelDialogOptions(preferredEffort) {
  const entry = selectedModelDialogEntry();
  const model = entry?.models?.find(
    (candidate) => candidate.id === elements.modelSelect.value,
  );
  elements.modelDescription.textContent = model?.description || "";
  elements.effortSelect.replaceChildren();
  for (const effort of model?.efforts || []) {
    const option = document.createElement("option");
    option.value = effort.id;
    option.textContent = effortLabel(effort.id);
    elements.effortSelect.append(option);
  }
  const effortIds = (model?.efforts || []).map((effort) => effort.id);
  elements.effortSelect.value = effortIds.includes(preferredEffort)
    ? preferredEffort
    : (model?.defaultEffort || effortIds[0] || "");
  syncEffortDescription();

  const priority = model?.serviceTiers?.find((tier) => tier.id === "priority");
  elements.fastModeRow.hidden = !priority;
  elements.fastModeDescription.textContent = (
    priority?.description || "使用优先处理服务档位"
  );
  if (!priority) elements.fastModeInput.checked = false;
}

async function openModelSettingsDialog() {
  const threadId = selectedThread?.id;
  if (!threadId || elements.modelSettingsButton.disabled) return;
  const entry = await refreshModelSettings(threadId, true);
  if (!entry || selectedThread?.id !== threadId) {
    elements.composerState.textContent = "暂时无法读取模型设置";
    return;
  }
  elements.modelSelect.replaceChildren();
  for (const model of entry.models) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = model.displayName;
    elements.modelSelect.append(option);
  }
  elements.modelSelect.value = entry.settings.model;
  elements.fastModeInput.checked = entry.settings.serviceTier === "priority";
  syncModelDialogOptions(entry.settings.effort);
  elements.modelSettingsError.textContent = "";
  elements.modelSettingsDialog.showModal();
}

async function saveModelSettings() {
  const threadId = selectedThread?.id;
  const entry = selectedModelDialogEntry();
  if (!threadId || !entry) return;
  elements.modelSettingsSave.disabled = true;
  elements.modelSettingsCancel.disabled = true;
  elements.modelSettingsError.textContent = "正在应用…";
  const model = entry.models.find(
    (candidate) => candidate.id === elements.modelSelect.value,
  );
  const supportsFast = model?.serviceTiers?.some((tier) => tier.id === "priority");
  const settings = {
    model: elements.modelSelect.value,
    effort: elements.effortSelect.value,
    serviceTier: supportsFast && elements.fastModeInput.checked ? "priority" : null,
  };
  try {
    const response = await fetch(
      `/api/codex/threads/${encodeURIComponent(threadId)}/settings`,
      {
        method: "POST",
        headers: {
          ...authorizationHeaders(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify(settings),
      },
    );
    if (response.status === 401) {
      handleUnauthorized();
      return;
    }
    const result = await response.json();
    if (!response.ok) {
      elements.modelSettingsError.textContent = result.error === "model_settings_locked"
        ? "任务已经开始运行，请完成或停止后再修改。"
        : "模型设置没有应用，请稍后重试。";
      return;
    }
    modelSettingsCache.set(threadId, {
      models: entry.models,
      settings: result.settings,
      fetchedAt: Date.now(),
    });
    renderModelSettingsButton();
    elements.modelSettingsDialog.close();
  } catch {
    elements.modelSettingsError.textContent = "无法连接 Mac，请稍后重试。";
  } finally {
    elements.modelSettingsSave.disabled = false;
    elements.modelSettingsCancel.disabled = false;
  }
}

function conversationNearBottom(threshold = 140) {
  const pageHeight = Math.max(
    document.documentElement.scrollHeight,
    document.body.scrollHeight,
  );
  const viewportHeight = window.visualViewport?.height || window.innerHeight;
  return pageHeight - (window.scrollY + viewportHeight) <= threshold;
}

function hideNewContentNotice() {
  hasUnseenNewContent = false;
  elements.newContentButton.hidden = true;
}

function showNewContentNotice() {
  hasUnseenNewContent = true;
  updateLatestButton();
}

function updateLatestButton() {
  const shouldShow = Boolean(
    selectedThread
    && !isScrollingToLatest
    && !conversationNearBottom()
  );
  elements.newContentButton.hidden = !shouldShow;
  elements.latestButtonLabel.textContent = hasUnseenNewContent
    ? "下方有新内容"
    : "回到最新";
  elements.newContentButton.classList.toggle("has-new-content", hasUnseenNewContent);
  elements.newContentButton.title = hasUnseenNewContent
    ? "查看下方的新内容"
    : "回到对话最下面";
}

function scrollToLatest(behavior = "smooth") {
  hideNewContentNotice();
  isScrollingToLatest = true;
  window.scrollTo({ top: document.body.scrollHeight, behavior });
  window.setTimeout(() => {
    isScrollingToLatest = false;
    updateLatestButton();
  }, behavior === "smooth" ? 500 : 0);
}

function followLatestOrNotify(wasNearBottom) {
  requestAnimationFrame(() => {
    if (
      wasNearBottom
      && document.activeElement !== elements.composerInput
    ) {
      scrollToLatest("auto");
    } else if (conversationNearBottom()) {
      hideNewContentNotice();
    } else {
      showNewContentNotice();
    }
  });
}

function threadContentSignature(thread) {
  return JSON.stringify({
    activityStatus: thread.activityStatus,
    updatedAt: thread.updatedAt,
    turns: (thread.turns || []).slice(-3).map((turn) => ({
      id: turn.id,
      status: turn.status,
      completedAt: turn.completedAt,
      items: (turn.items || []).map((item) => ({
        id: item.id,
        type: item.type,
        status: item.status,
        phase: item.phase,
        text: item.text,
        label: item.label,
        count: item.count,
        attachments: item.attachments,
      })),
    })),
  });
}

function scrollMetrics() {
  const pageHeight = Math.max(
    document.documentElement.scrollHeight,
    document.body.scrollHeight,
  );
  const viewportHeight = window.visualViewport?.height || window.innerHeight;
  return {
    pageHeight,
    viewportHeight,
    maxScroll: Math.max(0, pageHeight - viewportHeight),
  };
}

function syncScrollHandle() {
  const headerBottom = Math.max(
    70,
    Math.round(elements.conversationHeader.getBoundingClientRect().bottom + 5),
  );
  const composerHeight = Math.round(elements.composerShell.getBoundingClientRect().height);
  elements.scrollRail.style.top = `${headerBottom}px`;
  elements.scrollRail.style.bottom = `${composerHeight + 7}px`;
  elements.newContentButton.style.bottom = `${composerHeight + 12}px`;
  const { pageHeight, viewportHeight, maxScroll } = scrollMetrics();
  elements.scrollRail.hidden = false;
  const trackHeight = elements.scrollRail.clientHeight;
  const visible = maxScroll > 12 && trackHeight > 80;
  elements.scrollRail.hidden = !visible;
  if (!visible) return;
  const thumbHeight = Math.max(
    52,
    Math.min(trackHeight, trackHeight * (viewportHeight / pageHeight)),
  );
  const thumbRange = Math.max(0, trackHeight - thumbHeight);
  const currentScroll = Math.min(maxScroll, Math.max(0, window.scrollY));
  const thumbTop = maxScroll ? (currentScroll / maxScroll) * thumbRange : 0;
  elements.scrollThumb.style.height = `${thumbHeight}px`;
  elements.scrollThumb.style.transform = `translateY(${thumbTop}px)`;
}

function scheduleScrollHandle() {
  if (scrollSyncFrame) return;
  scrollSyncFrame = window.requestAnimationFrame(() => {
    scrollSyncFrame = 0;
    syncScrollHandle();
  });
}

function startScrollDrag(event) {
  if (elements.scrollRail.hidden) return;
  event.preventDefault();
  const railRect = elements.scrollRail.getBoundingClientRect();
  const thumbHeight = elements.scrollThumb.getBoundingClientRect().height;
  const thumbRange = Math.max(1, railRect.height - thumbHeight);
  const { maxScroll } = scrollMetrics();
  let startScrollY = window.scrollY;
  if (event.target !== elements.scrollThumb) {
    const desiredTop = Math.min(
      thumbRange,
      Math.max(0, event.clientY - railRect.top - thumbHeight / 2),
    );
    startScrollY = (desiredTop / thumbRange) * maxScroll;
    window.scrollTo({ top: startScrollY, behavior: "auto" });
  }
  scrollDrag = {
    pointerId: event.pointerId,
    startY: event.clientY,
    startScrollY,
    maxScroll,
    thumbRange,
  };
  elements.scrollRail.classList.add("dragging");
  elements.scrollRail.setPointerCapture(event.pointerId);
}

function moveScrollDrag(event) {
  if (!scrollDrag || scrollDrag.pointerId !== event.pointerId) return;
  event.preventDefault();
  const delta = event.clientY - scrollDrag.startY;
  const next = scrollDrag.startScrollY
    + (delta / scrollDrag.thumbRange) * scrollDrag.maxScroll;
  window.scrollTo({
    top: Math.min(scrollDrag.maxScroll, Math.max(0, next)),
    behavior: "auto",
  });
}

function endScrollDrag(event) {
  if (!scrollDrag || scrollDrag.pointerId !== event.pointerId) return;
  scrollDrag = undefined;
  elements.scrollRail.classList.remove("dragging");
  if (elements.scrollRail.hasPointerCapture(event.pointerId)) {
    elements.scrollRail.releasePointerCapture(event.pointerId);
  }
}

function updateComposerState() {
  const isManagedThread = (
    managedRun?.threadId === selectedThread?.id
    && managedRunIsActive()
  );
  const isDesktopThread = (
    selectedThread?.id === uniqueCurrentThreadId()
    && !isManagedThread
  );
  const managedForSelected = managedRun?.threadId === selectedThread?.id;
  const hasActiveTurn = selectedThreadHasActiveTurn();
  const isPaused = selectedThreadIsPaused();
  const isComplete = selectedThreadIsComplete();
  const hasComposerContent = composerHasContent();
  let badgeKind = "history";
  let badgeLabel = "历史";
  let dotKind = "";
  if (
    isSendingMessage
    || isManagedThread
    || hasActiveTurn
    || (isDesktopThread && currentStopCandidates === 1)
  ) {
    badgeKind = "running";
    badgeLabel = isSendingMessage ? "启动中" : "运行中";
    dotKind = "error";
  } else if (isPaused) {
    badgeKind = "unknown";
    badgeLabel = "已暂停";
    dotKind = "pending";
  } else if (
    isComplete
  ) {
    badgeKind = "complete";
    badgeLabel = "已完成";
    dotKind = "ready";
  } else if (isDesktopThread || (managedForSelected && managedRun?.status === "failed")) {
    badgeKind = "unknown";
    badgeLabel = managedRun?.status === "failed" ? "失败" : "状态未知";
    dotKind = "pending";
  }
  elements.liveBadge.hidden = !selectedThread;
  elements.liveBadge.className = `live-badge ${badgeKind}`;
  elements.liveBadgeLabel.textContent = badgeLabel;
  elements.taskStateDot.className = `connection-dot ${dotKind}`;
  let stateText = "选择任务后可查看历史";
  let modeText = "Managed";
  let actionIsStop = false;
  let actionIsContinue = false;
  let actionDisabled = true;
  let inputDisabled = !selectedThread || isSendingMessage || isUploadingAttachments;
  if (!selectedThread) {
    modeText = "只读历史";
  } else if (isSendingMessage) {
    stateText = "正在由 Mac Desktop 启动任务…";
    modeText = "Desktop";
  } else if (isManagedThread) {
    stateText = {
      starting: "正在恢复任务…",
      inProgress: "Managed 任务运行中 · 点红色按钮可停止",
      waitingForInput: "任务正在等待你的确认",
      interrupting: "正在停止 Managed 任务…",
    }[managedRun.status] || "Managed 任务正在运行";
    modeText = "Managed · 运行中";
    actionIsStop = true;
    actionDisabled = (
      managedRun.status === "starting"
      || managedRun.status === "interrupting"
      || !managedRun.turnId
    );
  } else if (
    hasActiveTurn
    || (isDesktopThread && currentStopCandidates === 1)
  ) {
    modeText = "Desktop · 运行中";
    if (selectedThreadLastTurnStatus === "interrupting") {
      stateText = "正在停止 Desktop 任务…";
    } else {
      stateText = isDesktopThread && currentStopCandidates === 1
        ? "Mac 任务运行中 · 点红色按钮可停止"
        : "任务正在 Mac 上运行 · 点红色按钮可切换并停止";
      actionIsStop = true;
      actionDisabled = false;
    }
  } else if (isPaused) {
    stateText = "任务已暂停 · 留空可继续，也可输入新指令";
    modeText = `${isDesktopThread ? "Desktop" : "历史"} · 已暂停`;
    actionIsContinue = !hasComposerContent;
    actionDisabled = false;
  } else if (isComplete) {
    stateText = "任务已完成 · 可以继续发送新指令";
    modeText = `${isDesktopThread ? "Desktop" : "历史"} · 已完成`;
    actionDisabled = !hasComposerContent;
  } else if (isDesktopThread) {
    modeText = "Desktop";
    stateText = currentStopCandidates < 0
      ? "Desktop 状态暂时不可用 · 暂不允许发送"
      : "正在核对任务状态 · 暂不允许发送";
  } else {
    actionDisabled = !hasComposerContent;
    if (managedForSelected && managedRun?.status === "failed") {
      stateText = `上个任务失败：${managedRun.error || "未知错误"}`;
      modeText = "Managed · 失败";
    } else {
      stateText = "历史任务 · 可从手机继续";
      modeText = "历史";
    }
  }

  elements.composerState.textContent = stateText;
  elements.composerMode.textContent = modeText;
  elements.composerActionButton.classList.toggle("stop", actionIsStop);
  elements.composerActionButton.classList.toggle("continue", actionIsContinue);
  elements.composerActionButton.disabled = actionDisabled;
  const actionLabel = actionIsStop
    ? "停止当前任务"
    : (actionIsContinue ? "继续当前任务" : "发送消息");
  elements.composerActionButton.setAttribute(
    "aria-label",
    actionLabel,
  );
  elements.composerActionButton.title = actionLabel;
  elements.composerInput.placeholder = selectedThread
    ? (isPaused ? "输入新指令，或留空直接继续…" : "向 Codex 发送消息…")
    : "选择一个任务…";
  elements.modelSettingsButton.disabled = Boolean(
    !selectedThread
    || isSendingMessage
    || hasActiveTurn
    || isManagedThread
    || (isDesktopThread && currentStopCandidates === 1)
    || modelSettingsLoadingThreadId === selectedThread.id
  );
  elements.attachmentButton.disabled = Boolean(
    !selectedThread
    || isSendingMessage
    || isUploadingAttachments
    || selectedAttachments().length >= MAX_ATTACHMENTS_PER_TURN
  );
  if (isUploadingAttachments && !actionIsStop) {
    elements.composerActionButton.disabled = true;
  }
  renderModelSettingsButton();
  setComposerDisabled(inputDisabled);
}

function createThreadButton(thread, liveThreadId, isRecent = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "drawer-thread";
  if (isRecent) button.classList.add("recent-thread");
  if (thread.id === selectedThread?.id) button.classList.add("active");
  const title = document.createElement("span");
  title.className = "drawer-thread-title";
  title.textContent = thread.title;
  button.append(title);
  const isRunning = (
    thread.activityStatus === "inProgress"
    || (
      thread.id === liveThreadId
      && (currentStopCandidates === 1 || desktopActivityIsRecent())
    )
  );
  const isComplete = !isRunning && thread.activityStatus === "completed";
  if (isRunning || isComplete) {
    const state = document.createElement("span");
    state.className = `drawer-thread-state ${isRunning ? "running" : "complete"}`;
    const dot = document.createElement("span");
    dot.className = "thread-state-dot";
    const label = document.createElement("span");
    label.textContent = isRunning ? "运行中" : "已完成";
    state.title = isRunning ? "这个任务仍在运行" : "这个任务已经完成";
    state.append(dot, label);
    button.append(state);
  }
  button.addEventListener("click", () => openThread(thread.id));
  return button;
}

function createSafeThreadButton(thread, liveThreadId, isRecent = false) {
  try {
    return createThreadButton(thread, liveThreadId, isRecent);
  } catch {
    // A partially-written Desktop catalog entry must not prevent every later
    // project and Recent task from rendering. Keep the task reachable with the
    // stable fields that are available and let the next catalog refresh repair
    // its richer state.
    const button = document.createElement("button");
    button.type = "button";
    button.className = "drawer-thread";
    if (isRecent) button.classList.add("recent-thread");
    const title = document.createElement("span");
    title.className = "drawer-thread-title";
    title.textContent = String(thread?.title || "未命名任务");
    button.append(title);
    if (typeof thread?.id === "string" && thread.id) {
      button.addEventListener("click", () => openThread(thread.id));
    } else {
      button.disabled = true;
    }
    return button;
  }
}

function saveCollapsedProjects() {
  localStorage.setItem(
    COLLAPSED_PROJECTS_KEY,
    JSON.stringify([...collapsedProjects]),
  );
}

function newTaskCreationDisabled() {
  return isCreatingTask;
}

function createCollectionNewTaskButton(target) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "collection-new-task";
  button.textContent = "+";
  button.disabled = newTaskCreationDisabled();
  const destination = target.projectId ? target.name : "Recents";
  button.setAttribute("aria-label", `在 ${destination} 中新建任务`);
  button.title = newTaskCreationDisabled()
    ? "正在创建另一个任务"
    : `在 ${destination} 中新建任务`;
  button.addEventListener("click", () => openNewTaskDialog(target));
  return button;
}

function updateNewTaskControls() {
  const busy = isCreatingTask || isUploadingNewTaskAttachments;
  elements.newTaskAttachmentButton.disabled = Boolean(
    busy || newTaskAttachments.length >= MAX_ATTACHMENTS_PER_TURN
  );
  elements.newTaskSubmit.disabled = busy;
  elements.newTaskCancel.disabled = busy;
}

function renderNewTaskAttachments() {
  elements.newTaskAttachments.replaceChildren();
  elements.newTaskAttachments.hidden = newTaskAttachments.length === 0;
  for (const attachment of newTaskAttachments) {
    const chip = document.createElement("div");
    chip.className = "attachment-chip";
    chip.title = `${attachment.name} · ${formatAttachmentSize(attachment.size)}`;
    const name = document.createElement("span");
    name.className = "attachment-chip-name";
    name.textContent = attachment.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "attachment-chip-remove";
    remove.textContent = "×";
    remove.disabled = Boolean(attachment.removing || isCreatingTask);
    remove.setAttribute("aria-label", `移除附件 ${attachment.name}`);
    remove.addEventListener("click", () => removeNewTaskAttachment(attachment.id));
    chip.append(name, remove);
    elements.newTaskAttachments.append(chip);
  }
  updateNewTaskControls();
}

async function removeNewTaskAttachment(attachmentId) {
  const attachment = newTaskAttachments.find((item) => item.id === attachmentId);
  if (!attachment || isCreatingTask) return;
  attachment.removing = true;
  renderNewTaskAttachments();
  try {
    const response = await fetch(
      `/api/attachments/${encodeURIComponent(attachmentId)}`,
      { method: "DELETE", headers: authorizationHeaders() },
    );
    if (response.status === 401) {
      handleUnauthorized();
      return;
    }
    if (!response.ok && response.status !== 404) throw new Error("delete failed");
    newTaskAttachments = newTaskAttachments.filter(
      (item) => item.id !== attachmentId,
    );
  } catch {
    attachment.removing = false;
    elements.newTaskError.textContent = "附件移除失败，请稍后重试。";
  }
  renderNewTaskAttachments();
}

function discardNewTaskAttachments() {
  const discarded = newTaskAttachments;
  newTaskAttachments = [];
  renderNewTaskAttachments();
  for (const attachment of discarded) {
    fetch(`/api/attachments/${encodeURIComponent(attachment.id)}`, {
      method: "DELETE",
      headers: authorizationHeaders(),
    }).catch(() => {});
  }
}

async function uploadNewTaskAttachments(files) {
  if (isCreatingTask || isUploadingNewTaskAttachments || !files.length) return;
  const availableSlots = MAX_ATTACHMENTS_PER_TURN - newTaskAttachments.length;
  const selectedFiles = Array.from(files).slice(0, Math.max(0, availableSlots));
  if (!selectedFiles.length) {
    elements.newTaskError.textContent = `每个新任务最多 ${MAX_ATTACHMENTS_PER_TURN} 个附件。`;
    return;
  }
  isUploadingNewTaskAttachments = true;
  elements.newTaskError.textContent = "";
  updateNewTaskControls();
  let feedback = "";
  try {
    for (const file of selectedFiles) {
      if (!file.size || file.size > MAX_ATTACHMENT_BYTES) {
        feedback = `${file.name} 超过 20 MB 或为空文件。`;
        continue;
      }
      elements.newTaskError.textContent = `正在上传 ${file.name}…`;
      try {
        const response = await fetch("/api/attachments", {
          method: "POST",
          headers: {
            ...authorizationHeaders(),
            "Content-Type": file.type || "application/octet-stream",
            "X-Codex-Filename": encodeURIComponent(file.name),
          },
          body: file,
        });
        if (response.status === 401) {
          handleUnauthorized();
          feedback = "设备授权已失效，请重新配对。";
          break;
        }
        const result = await response.json();
        if (!response.ok || !result.attachment) {
          feedback = response.status === 413
            ? `${file.name} 超过 20 MB。`
            : `${file.name} 上传失败。`;
          continue;
        }
        newTaskAttachments.push(result.attachment);
        renderNewTaskAttachments();
      } catch {
        feedback = `${file.name} 上传失败。`;
      }
    }
  } finally {
    isUploadingNewTaskAttachments = false;
    elements.newTaskAttachmentInput.value = "";
    renderNewTaskAttachments();
    elements.newTaskError.textContent = feedback;
  }
}

function openNewTaskDialog(target) {
  if (newTaskCreationDisabled()) return;
  if (newTaskAttachments.length) discardNewTaskAttachments();
  newTaskTarget = target;
  elements.newTaskDestination.textContent = target.projectId
    ? `Project「${target.name}」`
    : "Recents";
  elements.newTaskMessage.value = "";
  elements.newTaskError.textContent = "";
  const entry = selectedThread
    ? modelSettingsCache.get(selectedThread.id)
    : undefined;
  const settings = entry?.settings;
  const model = entry?.models?.find((candidate) => candidate.id === settings?.model);
  elements.newTaskModelHint.textContent = settings && model
    ? `沿用当前模型：${shortModelName(model.displayName, model.id)} ${effortLabel(settings.effort)}`
    : "使用 Desktop 默认模型设置";
  elements.newTaskDialog.showModal();
  window.setTimeout(() => elements.newTaskMessage.focus(), 0);
}

async function createNewTask() {
  if (!newTaskTarget || isCreatingTask) return;
  const message = elements.newTaskMessage.value.trim();
  if (!message && !newTaskAttachments.length) {
    elements.newTaskError.textContent = "请输入第一条指令或添加附件。";
    return;
  }
  const sourceEntry = selectedThread
    ? modelSettingsCache.get(selectedThread.id)
    : undefined;
  const sourceSettings = sourceEntry?.settings;
  const body = {
    projectId: newTaskTarget.projectId || null,
    message,
    attachmentIds: newTaskAttachments.map((attachment) => attachment.id),
  };
  if (sourceSettings?.model && sourceSettings?.effort) {
    Object.assign(body, sourceSettings);
  }
  isCreatingTask = true;
  updateNewTaskControls();
  elements.newTaskError.textContent = "正在 Mac 上创建并发送…";
  renderProjectGroups();
  const requestController = new AbortController();
  const requestTimeout = window.setTimeout(
    () => requestController.abort(),
    newTaskAttachments.length ? 45_000 : 20_000,
  );
  try {
    const response = await fetch("/api/codex/threads", {
      method: "POST",
      headers: {
        ...authorizationHeaders(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: requestController.signal,
    });
    if (response.status === 401) {
      handleUnauthorized();
      return;
    }
    const result = await response.json();
    if (!response.ok) {
      if (result.threadCreated && result.threadId) {
        if (message) threadDrafts.set(result.threadId, message);
        if (newTaskAttachments.length) {
          threadAttachments.set(result.threadId, [...newTaskAttachments]);
        }
        newTaskAttachments = [];
        renderNewTaskAttachments();
        localStorage.setItem(SELECTED_THREAD_KEY, result.threadId);
        elements.newTaskDialog.close();
        await loadThreads();
        if (
          result.thread?.id
          && !threads.some((thread) => thread.id === result.thread.id)
        ) {
          threads = [result.thread, ...threads];
          renderProjectGroups();
        }
        if (threads.some((thread) => thread.id === result.threadId)) {
          await openThread(result.threadId, {
            scroll: true,
            refreshRun: false,
          });
        }
        const dispatchMessages = {
          desktop_accessibility_unavailable: "空任务已创建，但后台 Helper 的辅助功能授权已失效；请在 Mac 上重新开关授权后重试。",
          desktop_attachment_unconfirmed: "空任务已创建，但 Desktop 没有确认附件；指令和附件已保留，可重试。",
        };
        elements.composerState.textContent = dispatchMessages[result.error]
          || "任务已创建，但 Desktop 没有确认发送；指令已保留，可重试。";
        return;
      }
      const messages = {
        desktop_turn_active: "新任务自身已经开始运行，请刷新列表确认状态。",
        invalid_project: "这个 Project 已发生变化，请刷新列表。",
        project_path_missing: "这个 Project 没有可用的本地目录。",
        projectless_directory_failed: "无法创建 Recents 的独立工作目录。",
        invalid_model_settings: "当前模型设置不可用于新任务。",
        thread_create_failed: "Desktop 没有成功创建任务，请稍后重试。",
      };
      elements.newTaskError.textContent = messages[result.error] || "新任务没有创建，请稍后重试。";
      if (result.error === "desktop_turn_active") {
        await refreshStatus();
      }
      return;
    }
    const createdThread = result.thread;
    if (!createdThread?.id) throw new Error("created thread missing");
    threads = [createdThread, ...threads.filter((thread) => thread.id !== createdThread.id)];
    if (sourceEntry?.models && result.settings) {
      modelSettingsCache.set(createdThread.id, {
        models: sourceEntry.models,
        settings: result.settings,
        fetchedAt: Date.now(),
      });
    }
    currentTaskTitle = result.desktop?.taskTitle || createdThread.title;
    currentStopCandidates = Number(result.desktop?.stopCandidates) || 1;
    desktopStatusKnown = true;
    selectedThreadLastTurnStatus = "inProgress";
    selectedThreadHasFinalAnswer = false;
    selectedThreadRuntimeStatus = "active";
    desktopDispatchState = {
      threadId: createdThread.id,
      baselineTurnId: "",
      startedAt: Date.now(),
    };
    localStorage.setItem(SELECTED_THREAD_KEY, createdThread.id);
    newTaskAttachments = [];
    renderNewTaskAttachments();
    elements.newTaskDialog.close();
    await openThread(createdThread.id, { fresh: true, closeDrawer: true });
    updateComposerState();
    window.setTimeout(refreshStatus, 600);
  } catch {
    elements.newTaskError.textContent = "无法连接 Mac，请稍后重试。";
  } finally {
    window.clearTimeout(requestTimeout);
    isCreatingTask = false;
    updateNewTaskControls();
    renderProjectGroups();
  }
}

function renderProjectGroups() {
  elements.projectGroups.replaceChildren();
  const grouped = new Map();
  const recentThreads = [];
  for (const project of projects) {
    if (!project?.id) continue;
    grouped.set(String(project.id), {
      id: String(project.id),
      name: project.name || "未命名项目",
      path: project.path || "",
      order: Number(project.order) || 0,
      threads: [],
    });
  }
  for (const thread of threads) {
    if (thread.collection !== "project" || !thread.project?.id) {
      recentThreads.push(thread);
      continue;
    }
    const key = String(thread.project.id);
    if (!grouped.has(key)) {
      grouped.set(key, {
        id: key,
        name: thread.project.name || "未命名项目",
        path: thread.project.path || "",
        order: Number(thread.project.order) || 0,
        threads: [],
      });
    }
    grouped.get(key).threads.push(thread);
  }

  const liveThreadId = uniqueCurrentThreadId();
  const projectGroups = [...grouped.values()].sort(
    (left, right) => left.order - right.order,
  );
  for (const group of projectGroups) {
    const section = document.createElement("section");
    section.className = "project-group";
    if (collapsedProjects.has(group.id)) section.classList.add("collapsed");

    const headingRow = document.createElement("div");
    headingRow.className = "project-heading-row";
    const heading = document.createElement("button");
    heading.type = "button";
    heading.className = "project-heading";
    heading.setAttribute(
      "aria-expanded",
      String(!collapsedProjects.has(group.id)),
    );
    heading.setAttribute("aria-label", `${group.name}，展开或折叠项目`);

    const chevron = document.createElement("span");
    chevron.className = "project-chevron";
    chevron.textContent = "›";
    const folder = document.createElement("span");
    folder.className = "project-folder-icon";
    folder.textContent = "▱";
    const label = document.createElement("span");
    label.className = "project-heading-label";
    label.textContent = group.name;
    heading.append(chevron, folder, label);

    heading.addEventListener("click", () => {
      const collapsed = section.classList.toggle("collapsed");
      heading.setAttribute("aria-expanded", String(!collapsed));
      if (collapsed) {
        collapsedProjects.add(group.id);
      } else {
        collapsedProjects.delete(group.id);
      }
      saveCollapsedProjects();
    });
    headingRow.append(heading);
    try {
      headingRow.append(createCollectionNewTaskButton({
        projectId: group.id,
        name: group.name,
      }));
    } catch {
      // The project remains navigable even if its transient action state is bad.
    }
    section.append(headingRow);
    elements.projectGroups.append(section);

    const body = document.createElement("div");
    body.className = "project-body";
    if (group.path) {
      const path = document.createElement("span");
      path.className = "project-path";
      path.textContent = String(group.path);
      path.title = String(group.path);
      body.append(path);
    }
    for (const thread of group.threads) {
      body.append(createSafeThreadButton(thread, liveThreadId));
    }
    section.append(body);
  }

  const section = document.createElement("section");
  section.className = "recents-section";
  const headingRow = document.createElement("div");
  headingRow.className = "recents-heading-row";
  const heading = document.createElement("div");
  heading.className = "recents-heading";
  heading.textContent = "Recents";
  headingRow.append(
    heading,
    createCollectionNewTaskButton({ projectId: null, name: "Recents" }),
  );
  section.append(headingRow);
  for (const thread of recentThreads) {
    section.append(createSafeThreadButton(thread, liveThreadId, true));
  }
  elements.projectGroups.append(section);
}

function appendTextWithBreaks(parent, value) {
  String(value).split("\n").forEach((part, index) => {
    if (index) parent.append(document.createElement("br"));
    parent.append(document.createTextNode(part));
  });
}

function safeLinkTarget(value) {
  try {
    const parsed = new URL(value);
    return ["http:", "https:", "mailto:"].includes(parsed.protocol)
      ? parsed.href
      : "";
  } catch {
    return "";
  }
}

function appendInlineMarkdown(parent, source, depth = 0) {
  const value = String(source || "");
  if (!value || depth > 4) {
    appendTextWithBreaks(parent, value);
    return;
  }
  const patterns = [
    { type: "code", regex: /`([^`\n]+)`/ },
    { type: "link", regex: /\[([^\]\n]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/ },
    { type: "strong", regex: /\*\*([^*\n]+)\*\*|__([^_\n]+)__/ },
    { type: "strike", regex: /~~([^~\n]+)~~/ },
    { type: "em", regex: /\*([^*\n]+)\*|_([^_\n]+)_/ },
    { type: "autolink", regex: /<(https?:\/\/[^>\s]+)>/ },
  ];
  let matchInfo;
  for (const pattern of patterns) {
    const match = pattern.regex.exec(value);
    if (match && (!matchInfo || match.index < matchInfo.match.index)) {
      matchInfo = { ...pattern, match };
    }
  }
  if (!matchInfo) {
    appendTextWithBreaks(parent, value);
    return;
  }
  appendTextWithBreaks(parent, value.slice(0, matchInfo.match.index));
  const { type, match } = matchInfo;
  if (type === "code") {
    const code = document.createElement("code");
    code.textContent = match[1];
    parent.append(code);
  } else if (type === "link" || type === "autolink") {
    const label = type === "link" ? match[1] : match[1];
    const href = safeLinkTarget(type === "link" ? match[2] : match[1]);
    if (href) {
      const link = document.createElement("a");
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      appendInlineMarkdown(link, label, depth + 1);
      parent.append(link);
    } else {
      const localReference = document.createElement("span");
      localReference.className = "markdown-local-reference";
      localReference.title = type === "link" ? match[2] : match[1];
      appendInlineMarkdown(localReference, label, depth + 1);
      parent.append(localReference);
    }
  } else {
    const element = document.createElement({
      strong: "strong",
      strike: "s",
      em: "em",
    }[type]);
    appendInlineMarkdown(element, match[1] || match[2], depth + 1);
    parent.append(element);
  }
  appendInlineMarkdown(
    parent,
    value.slice(match.index + match[0].length),
    depth,
  );
}

function splitTableRow(line) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isTableDelimiter(line) {
  const cells = splitTableRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function isMarkdownBlockStart(lines, index) {
  const line = lines[index] || "";
  return (
    /^```/.test(line)
    || /^#{1,4}\s+/.test(line)
    || /^>\s?/.test(line)
    || /^\s*[-+*]\s+/.test(line)
    || /^\s*\d+\.\s+/.test(line)
    || /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)
    || (line.includes("|") && isTableDelimiter(lines[index + 1] || ""))
  );
}

function renderMarkdown(container, source) {
  const lines = String(source || "（空消息）").replace(/\r\n?/g, "\n").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const fence = /^"?```([\w.+-]*)"?\s*$/.exec(line);
    if (fence) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^"?```"?\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      const block = document.createElement("div");
      block.className = "markdown-code-block";
      const bar = document.createElement("div");
      bar.className = "markdown-code-bar";
      const language = document.createElement("span");
      language.textContent = fence[1] || "code";
      const copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "复制";
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(codeLines.join("\n"));
          copy.textContent = "已复制";
          window.setTimeout(() => { copy.textContent = "复制"; }, 1200);
        } catch {
          copy.textContent = "复制失败";
        }
      });
      bar.append(language, copy);
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = codeLines.join("\n");
      pre.append(code);
      block.append(bar, pre);
      container.append(block);
      continue;
    }
    const heading = /^(#{1,4})\s+(.+)$/.exec(line);
    if (heading) {
      const element = document.createElement(`h${heading[1].length + 1}`);
      appendInlineMarkdown(element, heading[2]);
      container.append(element);
      index += 1;
      continue;
    }
    if (line.includes("|") && isTableDelimiter(lines[index + 1] || "")) {
      const rows = [splitTableRow(line)];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      const wrapper = document.createElement("div");
      wrapper.className = "markdown-table-wrap";
      const table = document.createElement("table");
      rows.forEach((cells, rowIndex) => {
        const row = document.createElement("tr");
        cells.forEach((cell) => {
          const element = document.createElement(rowIndex ? "td" : "th");
          appendInlineMarkdown(element, cell);
          row.append(element);
        });
        (rowIndex ? table.tBodies[0] : table.createTHead()).append(row);
        if (!rowIndex) table.createTBody();
      });
      wrapper.append(table);
      container.append(wrapper);
      continue;
    }
    if (/^>\s?/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\s?/, ""));
        index += 1;
      }
      const quote = document.createElement("blockquote");
      renderMarkdown(quote, quoteLines.join("\n"));
      container.append(quote);
      continue;
    }
    const listMatch = /^(\s*)([-+*]|\d+\.)\s+(.+)$/.exec(line);
    if (listMatch) {
      const ordered = /\d+\./.test(listMatch[2]);
      const list = document.createElement(ordered ? "ol" : "ul");
      while (index < lines.length) {
        const itemMatch = /^(\s*)([-+*]|\d+\.)\s+(.+)$/.exec(lines[index]);
        if (!itemMatch || /\d+\./.test(itemMatch[2]) !== ordered) break;
        const item = document.createElement("li");
        const task = /^\[([ xX])\]\s+(.+)$/.exec(itemMatch[3]);
        if (task) {
          item.className = "markdown-task";
          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.checked = task[1].toLowerCase() === "x";
          checkbox.disabled = true;
          item.append(checkbox);
          appendInlineMarkdown(item, task[2]);
        } else {
          appendInlineMarkdown(item, itemMatch[3]);
        }
        list.append(item);
        index += 1;
      }
      container.append(list);
      continue;
    }
    if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      container.append(document.createElement("hr"));
      index += 1;
      continue;
    }
    const paragraphLines = [line];
    index += 1;
    while (
      index < lines.length
      && lines[index].trim()
      && !isMarkdownBlockStart(lines, index)
    ) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    const paragraph = document.createElement("p");
    appendInlineMarkdown(paragraph, paragraphLines.join("\n"));
    container.append(paragraph);
  }
}

function eventStatusLabel(status) {
  return {
    completed: "已完成",
    failed: "失败",
    inProgress: "运行中",
    pending: "等待中",
    declined: "已拒绝",
  }[status] || status || "";
}

function collapsibleEventPresentation(item) {
  if (item.type === "contextCompaction") {
    return { title: "Context compacted", detail: "" };
  }
  if (item.type === "desktopActivity") {
    const titles = {
      readFiles: "Read files",
      ranCommands: "Ran commands",
      working: "Desktop task is running",
    };
    return {
      title: titles[item.activityKind] || "Desktop activity",
      detail: item.count > 1 ? `${item.count} 次` : "",
    };
  }
  if (item.type === "commandExecution") {
    return {
      title: "命令",
      detail: eventStatusLabel(item.status),
      body: [item.command, item.output].filter(Boolean).join("\n\n"),
      command: true,
    };
  }
  if (item.type === "fileChange") {
    const changes = item.changes || [];
    return {
      title: "文件修改",
      detail: [changes.length && `${changes.length} 项`, eventStatusLabel(item.status)]
        .filter(Boolean).join(" · "),
      body: changes
        .map((change) => `${change.kind || "change"} · ${change.path}`)
        .join("\n"),
    };
  }
  if (item.type === "plan") {
    return { title: "计划", detail: eventStatusLabel(item.status), markdown: item.text };
  }
  const titles = {
    mcpToolCall: "MCP 工具",
    dynamicToolCall: "工具调用",
    collabToolCall: "协作工具",
    webSearch: "网页搜索",
  };
  if (titles[item.type]) {
    const isBrowserActivity = item.activityKind === "browser";
    return {
      title: isBrowserActivity ? "Used the browser" : titles[item.type],
      detail: eventStatusLabel(item.status),
      body: item.label || eventStatusLabel(item.status) || "没有更多详情",
      preview: item.label || "",
    };
  }
  return undefined;
}

function appendHistoryEventEntry(item, view, target) {
  const entry = document.createElement("article");
  entry.className = `history-event-entry ${item.type || ""}`;
  const header = document.createElement("div");
  header.className = "history-event-entry-header";
  const entryTitle = document.createElement("span");
  entryTitle.className = "history-event-entry-title";
  entryTitle.textContent = view.title;
  const entryPreview = document.createElement("span");
  entryPreview.className = "history-event-entry-preview";
  entryPreview.textContent = view.preview || view.detail || "";
  const entryStatus = document.createElement("span");
  entryStatus.className = "history-event-entry-status";
  entryStatus.textContent = view.preview ? view.detail : "";
  header.append(entryTitle, entryPreview, entryStatus);
  entry.append(header);

  if (view.markdown || view.command || item.type === "fileChange") {
    const body = document.createElement("div");
    body.className = "history-event-entry-body history-text";
    if (view.markdown) {
      body.classList.add("markdown");
      renderMarkdown(body, view.markdown);
    } else {
      body.classList.add("plain-text");
      if (view.command) body.classList.add("command-output");
      body.textContent = view.body || "没有更多详情";
    }
    entry.append(body);
  }
  target.append(entry);
}

function activitySummaryCount(item) {
  if (item.type === "desktopActivity") return Math.max(1, Number(item.count) || 1);
  if (item.type === "fileChange") return Math.max(1, (item.changes || []).length);
  return 1;
}

function appendWorkingActivitySummary(events, groupKey, target) {
  if (!events.length) return;
  const counts = new Map();
  let total = 0;
  for (const { item, view } of events) {
    const count = activitySummaryCount(item);
    counts.set(view.title, (counts.get(view.title) || 0) + count);
    total += count;
  }
  const overview = [...counts]
    .map(([title, count]) => `${title} ${count}`)
    .join(" · ");
  const stateKey = groupKey;
  const details = document.createElement("details");
  details.className = "history-event working-activity-group";
  details.open = expandedWorkedGroups.has(stateKey);
  details.addEventListener("toggle", () => {
    if (details.open) expandedWorkedGroups.add(stateKey);
    else expandedWorkedGroups.delete(stateKey);
  });

  const summary = document.createElement("summary");
  summary.className = "history-event-summary working-activity-summary";
  const chevron = document.createElement("span");
  chevron.className = "history-event-chevron";
  chevron.textContent = "›";
  const title = document.createElement("span");
  title.className = "history-event-title";
  title.textContent = "Activity";
  const preview = document.createElement("span");
  preview.className = "history-event-preview";
  preview.textContent = overview;
  const status = document.createElement("span");
  status.className = "history-event-status";
  status.textContent = `${total} 项`;
  summary.append(chevron, title, preview, status);

  const body = document.createElement("div");
  body.className = "history-event-group-body working-activity-body";
  for (const { item, view } of events) {
    appendHistoryEventEntry(item, view, body);
  }
  details.append(summary, body);
  target.append(details);
}

function appendHistoryEventGroup(items, target = elements.threadHistory) {
  const events = items
    .map((item) => ({ item, view: collapsibleEventPresentation(item) }))
    .filter(({ view }) => Boolean(view));
  if (!events.length) return;
  const counts = new Map();
  for (const { view } of events) {
    counts.set(view.title, (counts.get(view.title) || 0) + 1);
  }
  const overview = [...counts.entries()]
    .map(([title, count]) => `${title} ${count}`)
    .join(" · ");

  const details = document.createElement("details");
  details.className = "history-item history-event history-event-group";
  const summary = document.createElement("summary");
  summary.className = "history-event-summary";
  const chevron = document.createElement("span");
  chevron.className = "history-event-chevron";
  chevron.textContent = "›";
  const title = document.createElement("span");
  title.className = "history-event-title";
  title.textContent = "工作记录";
  const preview = document.createElement("span");
  preview.className = "history-event-preview";
  preview.textContent = overview;
  const status = document.createElement("span");
  status.className = "history-event-status";
  status.textContent = `${events.length} 项`;
  summary.append(chevron, title, preview, status);

  const groupBody = document.createElement("div");
  groupBody.className = "history-event-group-body";
  for (const { item, view } of events) {
    appendHistoryEventEntry(item, view, groupBody);
  }
  details.append(summary, groupBody);
  target.append(details);
}

function timestampMilliseconds(value) {
  if (!value) return Number.NaN;
  if (typeof value === "number") return value < 1e12 ? value * 1000 : value;
  return new Date(value).valueOf();
}

function workedDurationLabel(turn) {
  const startedAt = timestampMilliseconds(turn.startedAt);
  const completedAt = timestampMilliseconds(turn.completedAt);
  const isWorking = ACTIVE_TURN_STATUSES.has(String(turn.status || ""));
  if (!Number.isFinite(startedAt)) return isWorking ? "Working" : "Worked";
  const endAt = Number.isFinite(completedAt) ? completedAt : Date.now();
  const seconds = Math.max(1, Math.round((endAt - startedAt) / 1000));
  const prefix = isWorking ? "Working for" : "Worked for";
  if (seconds < 60) return `${prefix} ${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) {
    return `${prefix} ${minutes}m${remainder ? ` ${remainder}s` : ""}`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `${prefix} ${hours}h${remainingMinutes ? ` ${remainingMinutes}m` : ""}`;
}

function appendWorkedGroup(items, turn, groupKey, target = elements.threadHistory) {
  if (!items.length) return;
  const eventCounts = new Map();
  let commentaryCount = 0;
  for (const item of items) {
    const view = collapsibleEventPresentation(item);
    if (view) {
      const count = activitySummaryCount(item);
      eventCounts.set(view.title, (eventCounts.get(view.title) || 0) + count);
    }
    if (item.type === "agentMessage") commentaryCount += 1;
  }
  const overview = [
    commentaryCount && `过程回复 ${commentaryCount}`,
    ...[...eventCounts].map(([title, count]) => `${title} ${count}`),
  ].filter(Boolean).join(" · ");

  const details = document.createElement("details");
  details.className = "history-item history-event history-event-group history-worked-group";
  const isWorking = ACTIVE_TURN_STATUSES.has(String(turn.status || ""));
  if (isWorking) details.classList.add("history-working-group");
  details.open = isWorking || expandedWorkedGroups.has(groupKey);
  details.addEventListener("toggle", () => {
    if (isWorking) return;
    if (details.open) expandedWorkedGroups.add(groupKey);
    else expandedWorkedGroups.delete(groupKey);
  });
  const summary = document.createElement("summary");
  summary.className = "history-event-summary history-worked-summary";
  const chevron = document.createElement("span");
  chevron.className = "history-event-chevron";
  chevron.textContent = "›";
  const title = document.createElement("span");
  title.className = "history-event-title history-worked-title";
  title.textContent = workedDurationLabel(turn);
  const preview = document.createElement("span");
  preview.className = "history-event-preview";
  preview.textContent = overview;
  const status = document.createElement("span");
  status.className = "history-event-status";
  status.textContent = `${items.length} 项`;
  summary.append(chevron, title, preview, status);

  const body = document.createElement("div");
  body.className = "history-event-group-body history-worked-body";
  let pendingEvents = [];
  let pendingStartIndex = -1;
  const flushActivitySummary = () => {
    if (!pendingEvents.length) return;
    appendWorkingActivitySummary(
      pendingEvents,
      `${groupKey}:activity:${pendingStartIndex}`,
      body,
    );
    pendingEvents = [];
    pendingStartIndex = -1;
  };
  for (let itemIndex = 0; itemIndex < items.length; itemIndex += 1) {
    const item = items[itemIndex];
    const view = collapsibleEventPresentation(item);
    if (view) {
      if (!pendingEvents.length) pendingStartIndex = itemIndex;
      pendingEvents.push({ item, view });
      continue;
    }
    flushActivitySummary();
    if (item.type === "agentMessage") {
      const message = document.createElement("article");
      message.className = "history-worked-message";
      const text = document.createElement("div");
      text.className = "history-text markdown";
      renderMarkdown(text, item.text);
      message.append(text);
      body.append(message);
      continue;
    }
    appendHistoryItem(item, body);
  }
  flushActivitySummary();
  details.append(summary, body);
  target.append(details);
}

function appendTurnHistory(turn, groupKey, target = elements.threadHistory) {
  const items = turn.items || [];
  let finalAnswerIndex = -1;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (items[index].type === "agentMessage" && items[index].phase === "final_answer") {
      finalAnswerIndex = index;
      break;
    }
  }
  const workedItems = items.filter((item, index) => (
    item.type !== "userMessage" && index !== finalAnswerIndex
  ));
  let lastUserIndex = -1;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (items[index].type === "userMessage") {
      lastUserIndex = index;
      break;
    }
  }
  let workedAppended = false;
  const appendWorked = () => {
    if (workedAppended) return;
    appendWorkedGroup(workedItems, turn, groupKey, target);
    workedAppended = true;
  };
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    if (item.type === "userMessage") {
      appendHistoryItem(item, target);
    } else if (index === finalAnswerIndex) {
      appendWorked();
      appendHistoryItem(item, target);
    } else if (lastUserIndex < 0 || index > lastUserIndex) {
      // Codex can record a context-compaction item before the user message that
      // started the turn. Keep the whole Working/Worked section after the user
      // block, matching Desktop's visual turn order.
      appendWorked();
    }
  }
  appendWorked();
}

function appendHistoryItem(item, target = elements.threadHistory) {

  const card = document.createElement("article");
  card.className = `history-item ${item.type || ""}`;
  const label = document.createElement("p");
  label.className = "history-label";
  const text = document.createElement("div");
  text.className = "history-text";

  if (item.type === "userMessage") {
    label.textContent = "你";
    text.classList.add("plain-text");
    text.textContent = item.text || (
      Array.isArray(item.attachments) && item.attachments.length
        ? "（仅附件）"
        : "（空消息）"
    );
  } else if (item.type === "agentMessage") {
    label.textContent = item.phase === "final_answer"
      ? "Codex · 最终回复"
      : item.phase === "streaming" ? "Codex · 正在回复" : "Codex";
    text.classList.add("markdown");
    renderMarkdown(text, item.text);
  } else {
    label.textContent = item.type || "事件";
    text.classList.add("plain-text");
    text.textContent = item.label || item.status || "";
  }
  if (
    item.timestamp
    && (
      item.type === "userMessage"
      || (item.type === "agentMessage" && item.phase === "final_answer")
    )
  ) {
    const milliseconds = timestampMilliseconds(item.timestamp);
    if (Number.isFinite(milliseconds)) {
      const date = new Date(milliseconds);
      const now = new Date();
      const sameDay = (
        date.getFullYear() === now.getFullYear()
        && date.getMonth() === now.getMonth()
        && date.getDate() === now.getDate()
      );
      const timestamp = document.createElement("time");
      timestamp.className = "history-timestamp";
      timestamp.dateTime = date.toISOString();
      timestamp.textContent = new Intl.DateTimeFormat("zh-CN", {
        ...(sameDay
          ? {}
          : {
            ...(date.getFullYear() === now.getFullYear()
              ? {}
              : { year: "numeric" }),
            month: "numeric",
            day: "numeric",
          }),
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).format(date);
      label.append(timestamp);
    }
  }
  card.append(label);
  if (item.type === "userMessage" && Array.isArray(item.attachments)) {
    const attachments = document.createElement("div");
    attachments.className = "history-message-attachments";
    for (const attachment of item.attachments) {
      if (
        attachment?.type !== "image"
      ) continue;
      const row = document.createElement("div");
      row.className = "history-message-attachment";
      const name = document.createElement("strong");
      name.textContent = attachment.name || "图片";
      const path = document.createElement("code");
      path.textContent = attachment.path || "本机附件";
      row.append(name, path);
      attachments.append(row);
    }
    if (attachments.childElementCount) card.append(attachments);
  }
  card.append(text);
  target.append(card);
}

function cachedThreadDetail(summary, turnLimit) {
  const cached = threadHistoryCache.get(summary.id);
  if (
    !cached
    || String(cached.updatedAt || "") !== String(summary.updatedAt || "")
    || cached.turnLimit < turnLimit
  ) return undefined;
  return cached;
}

function rememberThreadDetail(summary, thread, turnLimit) {
  threadHistoryCache.set(summary.id, {
    updatedAt: summary.updatedAt,
    turnLimit,
    fetchedAt: Date.now(),
    thread,
  });
  while (threadHistoryCache.size > 8) {
    threadHistoryCache.delete(threadHistoryCache.keys().next().value);
  }
}

function renderHistoryNotice(thread, turnLimit) {
  elements.historyNotice.replaceChildren();
  if (!elements.threadHistory.childElementCount) {
    elements.historyNotice.textContent = "这个任务没有可显示的消息记录。";
    return;
  }
  const totalTurns = Number(thread.totalTurns) || (thread.turns || []).length;
  if (totalTurns <= turnLimit) return;
  const text = document.createElement("span");
  text.textContent = `为保证移动端速度，先显示最近 ${turnLimit} 个 turn。`;
  elements.historyNotice.append(text);
  if (turnLimit >= MAX_HISTORY_TURNS) return;
  const loadOlder = document.createElement("button");
  loadOlder.type = "button";
  loadOlder.className = "history-load-more";
  loadOlder.textContent = "加载更早历史";
  loadOlder.addEventListener("click", () => openThread(thread.id, {
    turnLimit: MAX_HISTORY_TURNS,
    scroll: false,
    preservePosition: true,
  }));
  elements.historyNotice.append(loadOlder);
}

function renderThreadDetail(thread, turnLimit, options = {}) {
  const previousHeight = document.documentElement.scrollHeight;
  const previousScroll = window.scrollY;
  const wasNearBottom = conversationNearBottom();
  const nextContentSignature = threadContentSignature(thread);
  const previousContentSignature = renderedThreadSignatures.get(thread.id);
  const contentChanged = Boolean(
    previousContentSignature
    && previousContentSignature !== nextContentSignature
  );
  renderedThreadSignatures.set(thread.id, nextContentSignature);
  const summary = threads.find((candidate) => candidate.id === thread.id);
  const previousActivityStatus = summary?.activityStatus || "";
  if (summary) summary.activityStatus = String(thread.activityStatus || "");
  elements.threadMeta.textContent = [
    localizedThreadStatus(thread.status),
    formatThreadTime(thread.updatedAt),
    thread.cwd,
  ].filter(Boolean).join(" · ");
  for (const turn of thread.turns || []) {
    if (turn.id) persistedManagedTurnIds.add(turn.id);
  }
  const lastTurn = (thread.turns || []).at(-1);
  const lastTurnId = String(lastTurn?.id || "");
  const staleBaseline = (
    desktopDispatchState?.threadId === thread.id
    && lastTurnId === desktopDispatchState.baselineTurnId
    && desktopDispatchIsPending()
  );
  selectedThreadLastTurnId = lastTurnId;
  selectedThreadRuntimeStatus = String(thread.status?.type || thread.status || "");
  if (staleBaseline) {
    selectedThreadLastTurnStatus = "inProgress";
    selectedThreadHasFinalAnswer = false;
  } else {
    selectedThreadLastTurnStatus = String(lastTurn?.status || "");
    selectedThreadHasFinalAnswer = Boolean((lastTurn?.items || []).some((item) => (
      item.type === "agentMessage" && item.phase === "final_answer"
    )));
    if (
      desktopDispatchState?.threadId === thread.id
      && lastTurnId !== desktopDispatchState.baselineTurnId
    ) {
      desktopDispatchState = undefined;
    }
  }
  if (
    desktopActivityEvidence?.threadId === thread.id
    && currentStopCandidates !== 1
  ) {
    const terminalStatuses = new Set(["completed", "interrupted", "failed"]);
    const terminalUpdateArrived = (
      lastTurnId
      && terminalStatuses.has(selectedThreadLastTurnStatus)
      && (
        lastTurnId !== desktopActivityEvidence.baselineTurnId
        || ACTIVE_TURN_STATUSES.has(desktopActivityEvidence.baselineTurnStatus)
      )
    );
    if (terminalUpdateArrived) desktopActivityEvidence = undefined;
  }
  const history = document.createDocumentFragment();
  const visibleTurns = (thread.turns || []).slice(-turnLimit);
  const firstVisibleIndex = Math.max(0, (thread.turns || []).length - visibleTurns.length);
  for (let index = 0; index < visibleTurns.length; index += 1) {
    const turn = visibleTurns[index];
    const turnIdentity = turn.id || turn.startedAt || `turn-${firstVisibleIndex + index}`;
    appendTurnHistory(turn, `${thread.id}:${turnIdentity}`, history);
  }
  elements.threadHistory.replaceChildren(history);
  renderHistoryNotice(thread, turnLimit);
  if (summary && previousActivityStatus !== summary.activityStatus) {
    renderProjectGroups();
  }
  if (options.preservePosition) {
    requestAnimationFrame(() => {
      const addedHeight = document.documentElement.scrollHeight - previousHeight;
      window.scrollTo({ top: previousScroll + Math.max(0, addedHeight) });
    });
  } else if (options.scroll !== false) {
    requestAnimationFrame(() => scrollToLatest("auto"));
  } else if (contentChanged) {
    followLatestOrNotify(wasNearBottom);
  }
  updateComposerState();
}

async function respondToManagedRequest(requestId, payload) {
  if (!selectedThread || !managedRun?.pendingRequest) return;
  const response = await fetch(
    `/api/codex/threads/${encodeURIComponent(selectedThread.id)}/requests/${encodeURIComponent(requestId)}`,
    {
      method: "POST",
      headers: {
        ...authorizationHeaders(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    },
  );
  if (response.status === 401) {
    handleUnauthorized();
    return;
  }
  if (!response.ok) throw new Error("pending response failed");
  const result = await response.json();
  managedRun = result.run;
  renderManagedRun();
  updateComposerState();
}

function renderPendingRequest(request, target) {
  const card = document.createElement("article");
  card.className = "history-item pending-request";
  const label = document.createElement("p");
  label.className = "history-label";
  label.textContent = request.kind === "userInput" ? "需要你的回答" : "需要确认";
  const text = document.createElement("p");
  text.className = "history-text";
  if (request.kind === "commandApproval") {
    text.textContent = [
      request.reason,
      request.command,
      request.cwd && `目录：${request.cwd}`,
    ].filter(Boolean).join("\n");
  } else if (request.kind === "fileApproval") {
    text.textContent = [
      request.reason || "Codex 请求修改工作区之外的文件。",
      request.grantRoot && `范围：${request.grantRoot}`,
    ].filter(Boolean).join("\n");
  } else if (request.kind === "mcpElicitation") {
    text.textContent = request.message || "外部工具请求补充信息。";
  } else {
    text.textContent = "Codex 需要你的输入后才能继续。";
  }
  card.append(label, text);

  if (request.kind === "userInput") {
    const form = document.createElement("form");
    const controls = new Map();
    for (const question of request.questions || []) {
      const wrapper = document.createElement("div");
      wrapper.className = "pending-question";
      const questionLabel = document.createElement("label");
      questionLabel.textContent = question.question || question.header;
      let control;
      if (question.options?.length) {
        control = document.createElement("select");
        for (const option of question.options) {
          const choice = document.createElement("option");
          choice.value = option.label;
          choice.textContent = option.description
            ? `${option.label} — ${option.description}`
            : option.label;
          control.append(choice);
        }
      } else {
        control = document.createElement("input");
        control.type = question.isSecret ? "password" : "text";
        control.required = true;
      }
      questionLabel.htmlFor = `managed-question-${question.id}`;
      control.id = `managed-question-${question.id}`;
      controls.set(question.id, control);
      wrapper.append(questionLabel, control);
      form.append(wrapper);
    }
    const actions = document.createElement("div");
    actions.className = "pending-actions";
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.className = "pending-action accept";
    submit.textContent = "提交回答";
    actions.append(submit);
    form.append(actions);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      const answers = {};
      for (const [id, control] of controls) answers[id] = control.value;
      try {
        await respondToManagedRequest(request.id, { answers });
      } catch {
        elements.composerState.textContent = "回答发送失败，任务仍在等待";
        submit.disabled = false;
      }
    });
    card.append(form);
  } else {
    const actions = document.createElement("div");
    actions.className = "pending-actions";
    if (request.kind !== "mcpElicitation") {
      const accept = document.createElement("button");
      accept.type = "button";
      accept.className = "pending-action accept";
      accept.textContent = "仅本次允许";
      accept.addEventListener("click", async () => {
        actions.querySelectorAll("button").forEach((button) => {
          button.disabled = true;
        });
        try {
          await respondToManagedRequest(request.id, { decision: "accept" });
        } catch {
          elements.composerState.textContent = "审批发送失败，任务仍在等待";
          actions.querySelectorAll("button").forEach((button) => {
            button.disabled = false;
          });
        }
      });
      actions.append(accept);
    }
    const decline = document.createElement("button");
    decline.type = "button";
    decline.className = "pending-action";
    decline.textContent = "拒绝";
    decline.addEventListener("click", () => (
      respondToManagedRequest(request.id, { decision: "decline" })
    ));
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "pending-action cancel";
    cancel.textContent = "取消任务";
    cancel.addEventListener("click", () => (
      respondToManagedRequest(request.id, { decision: "cancel" })
    ));
    actions.append(decline, cancel);
    card.append(actions);
  }
  target.append(card);
}

function desktopRequestMatchesSelected() {
  return Boolean(
    desktopRequest
    && selectedThread
    && selectedThread.id === uniqueCurrentThreadId(),
  );
}

async function respondToDesktopRequest(action, response = {}) {
  if (!desktopRequestMatchesSelected()) return;
  const pending = desktopRequest;
  const result = await fetch("/api/desktop/request", {
    method: "POST",
    headers: {
      ...authorizationHeaders(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      expectedTaskTitle: currentTaskTitle,
      fingerprint: pending.fingerprint,
      action,
      ...response,
    }),
  });
  if (result.status === 401) {
    handleUnauthorized();
    return;
  }
  const payload = await result.json();
  if (!result.ok) {
    if ([
      "foreground_task_changed",
      "desktop_request_unavailable",
      "desktop_request_changed",
    ].includes(payload.error)) {
      desktopRequest = undefined;
      managedRenderSignature = "";
      renderManagedRun();
      elements.composerState.textContent = "Desktop 请求已经变化，请刷新后再操作";
      window.setTimeout(refreshStatus, 150);
      return;
    }
    throw new Error(payload.error || "desktop request response failed");
  }
  desktopRequest = undefined;
  managedRenderSignature = "";
  renderManagedRun();
  elements.composerState.textContent = "已发送到 Desktop，等待任务继续";
  window.setTimeout(refreshStatus, 200);
  window.setTimeout(() => {
    if (selectedThread) openThread(selectedThread.id, {
      fresh: true,
      scroll: false,
      refreshRun: false,
      rerenderProjects: false,
      closeDrawer: false,
    });
  }, 700);
}

function renderDesktopRequest(request, target) {
  const card = document.createElement("article");
  card.className = "history-item pending-request desktop-pending-request";
  const label = document.createElement("p");
  label.className = "history-label";
  label.textContent = request.kind === "userInput"
    ? "DESKTOP · 需要你的回答"
    : "DESKTOP · 需要确认";
  const text = document.createElement("p");
  text.className = "history-text";
  text.textContent = request.prompt || "Codex 正在等待你的操作。";
  card.append(label, text);

  const actions = document.createElement("div");
  actions.className = "pending-actions";
  const setBusy = (busy) => {
    card.querySelectorAll("button, input, select").forEach((control) => {
      control.disabled = busy;
    });
  };
  const fail = () => {
    elements.composerState.textContent = "操作没有送达 Desktop，请刷新后重试";
    setBusy(false);
  };

  if (request.kind === "approval") {
    const approve = (request.actions || []).find((action) => action.id === "approve_once");
    const deny = (request.actions || []).find((action) => action.id === "deny");
    if (approve) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pending-action accept";
      button.textContent = "仅本次允许";
      button.addEventListener("click", async () => {
        setBusy(true);
        try {
          await respondToDesktopRequest("approve_once");
        } catch {
          fail();
        }
      });
      actions.append(button);
    }
    if (deny) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pending-action cancel";
      button.textContent = "拒绝";
      button.addEventListener("click", async () => {
        setBusy(true);
        try {
          await respondToDesktopRequest("deny");
        } catch {
          fail();
        }
      });
      actions.append(button);
    }
    card.append(actions);
    target.append(card);
    return;
  }

  const form = document.createElement("form");
  const question = document.createElement("div");
  question.className = "pending-question";
  const options = Array.isArray(request.options) ? request.options : [];
  let optionSelect;
  let freeformInput;
  if (options.length) {
    const optionLabel = document.createElement("label");
    optionLabel.textContent = "选择回答";
    optionSelect = document.createElement("select");
    optionSelect.id = "desktop-request-option";
    optionLabel.htmlFor = optionSelect.id;
    for (const value of options) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      optionSelect.append(option);
    }
    if (request.allowsFreeform) {
      const option = document.createElement("option");
      option.value = "__freeform__";
      option.textContent = "其他…";
      optionSelect.append(option);
    }
    question.append(optionLabel, optionSelect);
  }
  if (request.allowsFreeform) {
    const answerLabel = document.createElement("label");
    answerLabel.textContent = options.length ? "其他回答" : "回答";
    freeformInput = document.createElement("input");
    freeformInput.type = "text";
    freeformInput.id = "desktop-request-answer";
    freeformInput.placeholder = "输入后提交";
    answerLabel.htmlFor = freeformInput.id;
    if (options.length) freeformInput.hidden = true;
    question.append(answerLabel, freeformInput);
    if (options.length) answerLabel.hidden = true;
    optionSelect?.addEventListener("change", () => {
      const show = optionSelect.value === "__freeform__";
      answerLabel.hidden = !show;
      freeformInput.hidden = !show;
      if (show) freeformInput.focus();
    });
  }
  form.append(question);
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "pending-action accept";
  submit.textContent = "提交回答";
  actions.append(submit);
  if ((request.actions || []).some((action) => action.id === "skip")) {
    const skip = document.createElement("button");
    skip.type = "button";
    skip.className = "pending-action";
    skip.textContent = "跳过";
    skip.addEventListener("click", async () => {
      setBusy(true);
      try {
        await respondToDesktopRequest("skip");
      } catch {
        fail();
      }
    });
    actions.append(skip);
  }
  form.append(actions);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const useFreeform = !optionSelect || optionSelect.value === "__freeform__";
    const answer = freeformInput?.value.trim() || "";
    if (useFreeform && !answer) {
      freeformInput?.focus();
      return;
    }
    setBusy(true);
    try {
      await respondToDesktopRequest("answer", useFreeform
        ? { answer }
        : { optionLabel: optionSelect.value });
    } catch {
      fail();
    }
  });
  card.append(form);
  target.append(card);
}

function renderManagedRun() {
  const signature = JSON.stringify({
    threadId: managedRun?.threadId,
    status: managedRun?.status,
    userText: managedRun?.userText,
    agentText: managedRun?.agentText,
    items: managedRun?.items,
    pendingRequest: managedRun?.pendingRequest,
    desktopRequest: desktopRequestMatchesSelected() ? desktopRequest : undefined,
  });
  if (signature === managedRenderSignature) return;
  const wasNearBottom = conversationNearBottom();
  managedRenderSignature = signature;
  elements.managedLiveHistory.replaceChildren();
  const showManaged = Boolean(
    managedRun?.threadId === selectedThread?.id
    && !(
      !managedRunIsActive()
      && managedRun?.turnId
      && persistedManagedTurnIds.has(managedRun.turnId)
    ),
  );
  if (showManaged && managedRun.userText) {
    appendHistoryItem(
      { type: "userMessage", text: managedRun.userText },
      elements.managedLiveHistory,
    );
  }
  if (showManaged) appendHistoryEventGroup(
    (managedRun.items || []).filter(
      (item) => !["userMessage", "agentMessage"].includes(item.type),
    ),
    elements.managedLiveHistory,
  );
  if (showManaged && managedRun.agentText) {
    appendHistoryItem(
      { type: "agentMessage", text: managedRun.agentText, phase: "streaming" },
      elements.managedLiveHistory,
    );
  }
  if (showManaged && managedRun.pendingRequest) {
    renderPendingRequest(managedRun.pendingRequest, elements.managedLiveHistory);
  }
  if (desktopRequestMatchesSelected()) {
    renderDesktopRequest(desktopRequest, elements.managedLiveHistory);
  }
  followLatestOrNotify(wasNearBottom);
}

async function refreshManagedRun(threadId = selectedThread?.id) {
  if (!threadId || threadId !== selectedThread?.id || !deviceToken) return;
  try {
    const response = await fetch(
      `/api/codex/threads/${encodeURIComponent(threadId)}/run`,
      { headers: authorizationHeaders(), cache: "no-store" },
    );
    if (response.status === 401) {
      handleUnauthorized();
      return;
    }
    if (!response.ok) throw new Error("managed run failed");
    const result = await response.json();
    if (threadId !== selectedThread?.id) return;
    const previous = managedRun?.threadId === threadId ? managedRun : undefined;
    managedRun = result.run;
    renderManagedRun();
    updateComposerState();
    if (
      previous
      && managedRun
      && managedRun.turnId
      && managedRunIsActive(previous)
      && !managedRunIsActive(managedRun)
      && !completedRunsSeen.has(managedRun.turnId)
    ) {
      completedRunsSeen.add(managedRun.turnId);
      await new Promise((resolve) => window.setTimeout(resolve, 300));
      await openThread(threadId, { refreshRun: false, fresh: true });
      managedRun = result.run;
      renderManagedRun();
      updateComposerState();
      if (!persistedManagedTurnIds.has(managedRun.turnId)) {
        window.setTimeout(() => {
          if (selectedThread?.id === threadId && !managedRunIsActive()) {
            openThread(threadId, { refreshRun: false, fresh: true, scroll: false });
          }
        }, 1_200);
      }
    }
  } catch {
    if (managedRunIsActive()) {
      elements.composerState.textContent = "Managed 状态暂时不可用，正在重试…";
    }
  }
}

async function startManagedTurn({ continueOnly = false } = {}) {
  if (
    !selectedThread
    || isSendingMessage
    || isUploadingAttachments
    || managedRunIsActive()
  ) return;
  const message = elements.composerInput.value.trim();
  const attachments = selectedAttachments();
  if (continueOnly) {
    if (!selectedThreadIsPaused() || message || attachments.length) return;
  } else if (!message && !attachments.length) {
    return;
  }
  const threadId = selectedThread.id;
  const baselineTurnId = selectedThreadLastTurnId;
  let feedback = "";
  isSendingMessage = true;
  updateComposerState();
  try {
    const response = await fetch(
      `/api/codex/threads/${encodeURIComponent(threadId)}/${continueOnly ? "continue" : "turn"}`,
      {
        method: "POST",
        headers: {
          ...authorizationHeaders(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify(continueOnly ? {} : {
          message,
          attachmentIds: attachments.map((attachment) => attachment.id),
        }),
      },
    );
    if (response.status === 401) {
      handleUnauthorized();
      return;
    }
    const result = await response.json();
    if (!response.ok) {
      if (result.error === "desktop_turn_active") {
        feedback = "Desktop 任务已经在运行；可等待完成或按 Stop";
        await refreshStatus();
        return;
      }
      if (result.error === "desktop_draft_present") {
        feedback = "Mac 输入框已有草稿，为避免覆盖没有发送";
        return;
      }
      if (
        result.error === "desktop_composer_unavailable"
        || result.error === "desktop_send_unavailable"
      ) {
        feedback = "暂时找不到 Mac 的输入框或发送按钮";
        return;
      }
      if (
        result.error === "desktop_composer_write_failed"
        || result.error === "desktop_send_failed"
        || result.error === "desktop_send_unconfirmed"
        || result.error === "desktop_dispatch_failed"
      ) {
        feedback = "Mac 没有确认发送，任务未启动";
        return;
      }
      if (
        result.error === "desktop_attachment_invalid"
        || result.error === "desktop_attachment_paste_failed"
        || result.error === "desktop_attachment_unconfirmed"
        || result.error === "desktop_attachment_conflict"
      ) {
        feedback = result.error === "desktop_attachment_conflict"
          ? "Mac 输入框里的附件状态与手机不一致，没有发送"
          : "Mac 没有确认附件已加入，任务未启动";
        return;
      }
      if (
        result.error === "attachment_not_found"
        || result.error === "invalid_attachments"
      ) {
        feedback = "附件已过期或不可用，请移除后重新选择";
        return;
      }
      if (result.error === "foreground_task_changed") {
        feedback = "Desktop 任务刚刚切换，请确认后重新发送";
        await refreshStatus();
        return;
      }
      if (
        result.error === "desktop_stop_state_ambiguous"
        || result.error === "desktop_state_unavailable"
      ) {
        feedback = "无法确认 Desktop 已空闲，没有启动任务";
        await refreshStatus();
        return;
      }
      if (result.error === "managed_turn_active") {
        feedback = "这个任务已有 Managed turn 正在运行";
        await refreshManagedRun(threadId);
        return;
      }
      if (result.error === "thread_not_interrupted") {
        feedback = "任务状态已经变化，刷新后再继续";
        await openThread(threadId, { fresh: true, scroll: false });
        return;
      }
      if (result.error === "codex_thread_unavailable") {
        feedback = "暂时无法读取这个任务，请刷新后重试";
        return;
      }
      if (result.error === "managed_turn_start_failed") {
        feedback = result.detail
          ? `Codex 无法启动：${result.detail}`
          : "Codex 无法恢复这个任务，请稍后重试";
        return;
      }
      if (result.error === "desktop_or_thread_state_unavailable") {
        feedback = "无法核对 Desktop 状态，请稍后重试";
        return;
      }
      throw new Error("managed turn start failed");
    }
    if (!continueOnly) {
      elements.composerInput.value = "";
      resizeComposer();
      threadDrafts.delete(threadId);
      threadAttachments.delete(threadId);
      renderComposerAttachments();
    }
    threadHistoryCache.delete(threadId);
    if (result.mode === "desktop" && result.desktop) {
      managedRun = undefined;
      managedRenderSignature = "";
      elements.managedLiveHistory.replaceChildren();
      currentTaskTitle = result.desktop.taskTitle || selectedThread.title;
      currentStopCandidates = Number(result.desktop.stopCandidates) || 0;
      selectedThreadLastTurnStatus = "inProgress";
      selectedThreadHasFinalAnswer = false;
      selectedThreadRuntimeStatus = "active";
      desktopDispatchState = {
        threadId,
        baselineTurnId,
        startedAt: Date.now(),
      };
      setDeviceState("ready", "Mac 在线 · Desktop 已接管");
      window.setTimeout(refreshStatus, 500);
      window.setTimeout(() => openThread(threadId, {
        fresh: true,
        scroll: true,
        refreshRun: false,
        rerenderProjects: false,
        closeDrawer: false,
      }), 900);
    } else {
      managedRun = result.run;
      managedRenderSignature = "";
      renderManagedRun();
    }
  } catch {
    feedback = continueOnly
      ? "继续失败，没有启动任务"
      : "发送失败，没有启动新任务";
  } finally {
    isSendingMessage = false;
    updateComposerState();
    if (feedback) elements.composerState.textContent = feedback;
  }
}

function sendManagedMessage() {
  return startManagedTurn();
}

function continueInterruptedTurn() {
  return startManagedTurn({ continueOnly: true });
}

async function interruptManagedTurn() {
  if (!selectedThread || !managedRunIsActive() || !managedRun?.turnId) return;
  elements.composerActionButton.disabled = true;
  elements.composerState.textContent = "正在停止 Managed 任务…";
  try {
    const response = await fetch(
      `/api/codex/threads/${encodeURIComponent(selectedThread.id)}/interrupt`,
      {
        method: "POST",
        headers: {
          ...authorizationHeaders(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ confirm: true }),
      },
    );
    if (response.status === 401) {
      handleUnauthorized();
      return;
    }
    if (!response.ok) throw new Error("managed interrupt failed");
    const result = await response.json();
    managedRun = result.run;
    renderManagedRun();
    updateComposerState();
  } catch {
    elements.composerState.textContent = "停止请求失败，任务可能仍在运行";
    await refreshManagedRun();
  }
}

async function openThread(threadId, options = {}) {
  const summary = threads.find((thread) => thread.id === threadId);
  if (!summary) return;
  const isThreadSwitch = selectedThread?.id !== threadId;
  if (selectedThread?.id) {
    threadDrafts.set(selectedThread.id, elements.composerInput.value);
  }
  selectedThread = summary;
  if (isThreadSwitch) hideNewContentNotice();
  selectedThreadLastTurnId = "";
  selectedThreadLastTurnStatus = "";
  selectedThreadHasFinalAnswer = false;
  selectedThreadRuntimeStatus = String(summary.status?.type || summary.status || "");
  elements.composerInput.value = threadDrafts.get(threadId) || "";
  renderComposerAttachments();
  resizeComposer();
  if (managedRun?.threadId !== threadId) {
    managedRun = undefined;
    managedRenderSignature = "";
  }
  localStorage.setItem(SELECTED_THREAD_KEY, threadId);
  elements.selectedProjectName.textContent = summary.project?.name || "Recents";
  elements.selectedThreadTitle.textContent = summary.title;
  elements.refreshConversationButton.disabled = false;
  elements.emptyState.hidden = true;
  elements.threadView.hidden = false;
  const turnLimit = Math.min(
    MAX_HISTORY_TURNS,
    Math.max(1, Number(options.turnLimit) || INITIAL_HISTORY_TURNS),
  );
  const cached = cachedThreadDetail(summary, turnLimit);
  if (cached) {
    renderThreadDetail(cached.thread, turnLimit, options);
  } else {
    elements.threadMeta.textContent = "正在读取历史…";
    elements.threadHistory.replaceChildren();
  }
  if (managedRun?.threadId !== threadId) {
    elements.managedLiveHistory.replaceChildren();
  }
  managedRenderSignature = "";
  if (!cached) elements.historyNotice.textContent = "";
  if (options.rerenderProjects !== false) renderProjectGroups();
  updateComposerState();
  void refreshModelSettings(threadId);
  if (options.closeDrawer !== false) closeDrawer();

  const cacheIsFresh = cached && Date.now() - cached.fetchedAt < THREAD_CACHE_TTL_MS;
  try {
    if (cacheIsFresh && !options.fresh) {
      if (options.refreshRun !== false) await refreshManagedRun(threadId);
      return;
    }
    const query = new URLSearchParams({
      turns: String(turnLimit),
      revision: String(summary.updatedAt || ""),
    });
    if (options.fresh) query.set("fresh", "1");
    const response = await fetch(
      `/api/codex/threads/${encodeURIComponent(threadId)}?${query}`,
      { headers: authorizationHeaders(), cache: "no-store" },
    );
    if (response.status === 401) {
      handleUnauthorized();
      return;
    }
    if (!response.ok) throw new Error("thread read failed");
    const { thread } = await response.json();
    rememberThreadDetail(summary, thread, turnLimit);
    if (threadId !== selectedThread?.id) return;
    renderThreadDetail(thread, turnLimit, options);
    if (options.refreshRun !== false) await refreshManagedRun(threadId);
  } catch {
    elements.threadMeta.textContent = cached
      ? "已显示缓存历史 · 暂时无法刷新"
      : "无法读取任务，请稍后重试。";
  }
}

async function refreshCurrentConversation() {
  if (!selectedThread || elements.refreshConversationButton.disabled) return;
  const threadId = selectedThread.id;
  elements.refreshConversationButton.disabled = true;
  elements.refreshConversationButton.classList.add("refreshing");
  elements.threadMeta.textContent = "正在刷新最新回复…";
  try {
    await Promise.all([
      refreshStatus(),
      openThread(threadId, {
        fresh: true,
        scroll: false,
        rerenderProjects: false,
        closeDrawer: false,
      }),
    ]);
  } finally {
    elements.refreshConversationButton.classList.remove("refreshing");
    elements.refreshConversationButton.disabled = selectedThread?.id !== threadId;
  }
}

async function loadThreads() {
  elements.refreshThreadsButton.disabled = true;
  elements.projectsHint.textContent = "正在读取项目…";
  try {
    if (!deviceToken && !(await enrollDevice())) {
      setDeviceState("error", "需要配对");
      showTokenDialog();
      return;
    }
    let result;
    for (let attempt = 0; attempt < 3 && !result; attempt += 1) {
      try {
        const response = await fetch("/api/codex/threads?limit=50", {
          headers: authorizationHeaders(),
          cache: "no-store",
        });
        if (response.status === 401) {
          handleUnauthorized();
          return;
        }
        if (!response.ok) throw new Error("thread list failed");
        const candidate = await response.json();
        if (!Array.isArray(candidate.projects) || !Array.isArray(candidate.threads)) {
          throw new Error("invalid thread list");
        }
        result = candidate;
      } catch {
        if (attempt < 2) {
          await new Promise((resolve) => window.setTimeout(resolve, 300 * (attempt + 1)));
        }
      }
    }
    if (!result) {
      elements.projectsHint.textContent = "项目暂时不可用，正在自动重试…";
      window.clearTimeout(threadCatalogRetryTimer);
      threadCatalogRetryTimer = window.setTimeout(() => {
        threadCatalogRetryTimer = undefined;
        loadThreads();
      }, 1_500);
      return;
    }
    window.clearTimeout(threadCatalogRetryTimer);
    threadCatalogRetryTimer = undefined;
    reconcileThreadCatalog(result);
    renderProjectGroups();

    const remembered = localStorage.getItem(SELECTED_THREAD_KEY);
    const initialId = (
      threads.some((thread) => thread.id === notificationThreadId) && notificationThreadId
    ) || (
      threads.some((thread) => thread.id === remembered) && remembered
    ) || uniqueCurrentThreadId() || threads[0]?.id;
    if (initialId) {
      try {
        await openThread(initialId);
        if (notificationThreadId) {
          notificationThreadId = "";
          history.replaceState(null, "", window.location.pathname);
        }
      } catch {
        // The catalog is already valid; conversation loading reports its own error.
      }
    } else {
      openDrawer();
    }
  } finally {
    elements.refreshThreadsButton.disabled = false;
  }
}

async function refreshStatusOnce() {
  try {
    if (!deviceToken && !(await enrollDevice())) {
      setDeviceState("error", "需要配对");
      showTokenDialog();
      return;
    }
    const response = await fetch("/api/desktop/interrupt/status", {
      headers: authorizationHeaders(),
      cache: "no-store",
    });
    if (response.status === 401) {
      handleUnauthorized();
      return;
    }
    if (!response.ok) throw new Error("status failed");
    const status = await response.json();
    const previousTitle = currentTaskTitle;
    const previousStopCandidates = currentStopCandidates;
    const previousRequestFingerprint = desktopRequest?.fingerprint || "";
    currentTaskTitle = status.taskTitle;
    currentStopCandidates = Number(status.stopCandidates) || 0;
    desktopStatusKnown = true;
    desktopRequest = status.request || undefined;
    const nextRequestFingerprint = desktopRequest?.fingerprint || "";
    if (
      desktopRequestNotificationsPrimed
      && nextRequestFingerprint
      && nextRequestFingerprint !== previousRequestFingerprint
    ) {
      showSystemNotification("Codex 需要你的确认", {
        body: currentTaskTitle || "打开 Codex Pocket 查看请求",
        threadId: uniqueCurrentThreadId(),
        tag: `codex-request-${nextRequestFingerprint}`,
      });
    }
    desktopRequestNotificationsPrimed = true;
    const foregroundThreadId = uniqueCurrentThreadId();
    if (
      currentStopCandidates === 1
      && foregroundThreadId
    ) {
      if (desktopActivityEvidence?.threadId !== foregroundThreadId) {
        desktopActivityEvidence = {
          threadId: foregroundThreadId,
          baselineTurnId: selectedThread?.id === foregroundThreadId
            ? selectedThreadLastTurnId
            : "",
          baselineTurnStatus: selectedThread?.id === foregroundThreadId
            ? selectedThreadLastTurnStatus
            : "",
          lastSeenAt: Date.now(),
        };
      } else {
        desktopActivityEvidence.lastSeenAt = Date.now();
      }
      if (selectedThread?.id === foregroundThreadId) {
        selectedThreadHasFinalAnswer = false;
      }
    } else if (
      desktopActivityEvidence
      && currentTaskTitle
      && threads.some((thread) => (
        thread.id === desktopActivityEvidence.threadId
        && thread.title !== currentTaskTitle
      ))
    ) {
      desktopActivityEvidence = undefined;
    }
    setDeviceState("ready", "Mac 在线 · 设备已信任");
    if (
      threads.length
      && (
        previousTitle !== currentTaskTitle
        || previousStopCandidates !== currentStopCandidates
      )
    ) renderProjectGroups();
    if (previousRequestFingerprint !== nextRequestFingerprint) {
      managedRenderSignature = "";
      renderManagedRun();
    }
    updateComposerState();
    if (
      previousStopCandidates === 1
      && currentStopCandidates === 0
      && selectedThread?.id === uniqueCurrentThreadId()
    ) {
      window.setTimeout(() => openThread(selectedThread.id, {
        fresh: true,
        scroll: false,
        refreshRun: false,
        rerenderProjects: false,
        closeDrawer: false,
      }), 500);
    }
  } catch {
    currentStopCandidates = -1;
    desktopStatusKnown = false;
    desktopRequest = undefined;
    managedRenderSignature = "";
    renderManagedRun();
    setDeviceState("error", "Mac 状态暂不可用");
    renderProjectGroups();
    updateComposerState();
  }
}

async function refreshStatus() {
  if (statusRefreshPromise) return statusRefreshPromise;
  statusRefreshPromise = refreshStatusOnce();
  try {
    return await statusRefreshPromise;
  } finally {
    statusRefreshPromise = undefined;
  }
}

async function refreshDesktopConversationIfRunning() {
  const selectedIsForeground = selectedThread?.id === uniqueCurrentThreadId();
  const shouldRefresh = (
    (selectedIsForeground && currentStopCandidates === 1)
    || selectedThreadHasActiveTurn()
  );
  if (
    desktopHistoryRefreshInFlight
    || document.visibilityState !== "visible"
    || !selectedThread
    || !shouldRefresh
  ) return;
  desktopHistoryRefreshInFlight = true;
  try {
    await openThread(selectedThread.id, {
      fresh: true,
      scroll: false,
      refreshRun: false,
      rerenderProjects: false,
      closeDrawer: false,
    });
  } finally {
    desktopHistoryRefreshInFlight = false;
  }
}

async function interruptCurrentTask() {
  if (!selectedThread?.id || !selectedThread.title) return;
  const threadId = selectedThread.id;
  const expectedTaskTitle = selectedThread.title;
  elements.confirmButton.disabled = true;
  elements.confirmButton.textContent = "正在核对…";
  try {
    const response = await fetch("/api/desktop/interrupt", {
      method: "POST",
      headers: {
        ...authorizationHeaders(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        confirm: true,
        threadId,
        expectedTaskTitle,
      }),
    });
    const result = await response.json();
    if (response.ok && result.interrupted) {
      elements.stopDialog.close();
      currentTaskTitle = expectedTaskTitle;
      currentStopCandidates = 0;
      desktopDispatchState = undefined;
      selectedThreadLastTurnStatus = "interrupting";
      selectedThreadHasFinalAnswer = false;
      selectedThreadRuntimeStatus = "active";
      elements.composerState.textContent = "已发送停止，正在等待 Desktop 更新…";
      updateComposerState();
      setTimeout(refreshStatus, 900);
      window.setTimeout(() => {
        if (selectedThread?.id === threadId) {
          openThread(threadId, {
            fresh: true,
            scroll: false,
            refreshRun: false,
            rerenderProjects: false,
            closeDrawer: false,
          });
        }
      }, 900);
      return;
    }
    if (result.error === "foreground_task_changed") {
      elements.stopDialog.close();
      elements.composerState.textContent = "任务切换后标识发生变化，已拒绝停止";
      await refreshStatus();
      return;
    }
    if (result.error === "thread_navigation_failed") {
      elements.stopDialog.close();
      elements.composerState.textContent = "Mac 没有成功切换到该任务，未执行停止";
      await refreshStatus();
      return;
    }
    if (result.error === "active_stop_button_not_unique") {
      elements.stopDialog.close();
      elements.composerState.textContent = Number(result.stopCandidates) === 0
        ? "任务可能刚刚结束，未找到停止按钮"
        : "检测到多个停止按钮，为安全起见未执行";
      await refreshStatus();
      await openThread(threadId, { fresh: true, scroll: false });
      return;
    }
    throw new Error("interrupt refused");
  } catch {
    elements.composerState.textContent = "停止请求失败，没有执行操作";
  } finally {
    elements.confirmButton.disabled = false;
    elements.confirmButton.textContent = "确认停止";
  }
}

elements.openDrawerButton.addEventListener("click", openDrawer);
elements.emptyOpenDrawerButton.addEventListener("click", openDrawer);
elements.closeDrawerButton.addEventListener("click", closeDrawer);
elements.drawerScrim.addEventListener("click", closeDrawer);
elements.newContentButton.addEventListener("click", () => scrollToLatest("smooth"));
elements.refreshThreadsButton.addEventListener("click", loadThreads);
elements.refreshUsageButton.addEventListener("click", () => refreshUsage(true));
elements.notificationButton.addEventListener("click", toggleSystemNotifications);
elements.refreshConversationButton.addEventListener("click", refreshCurrentConversation);
elements.modelSettingsButton.addEventListener("click", openModelSettingsDialog);
elements.attachmentButton.addEventListener("click", () => {
  if (!elements.attachmentButton.disabled) elements.attachmentInput.click();
});
elements.attachmentInput.addEventListener("change", () => {
  uploadSelectedAttachments(elements.attachmentInput.files || []);
});
elements.newTaskCancel.addEventListener("click", () => {
  elements.newTaskDialog.close();
});
elements.newTaskAttachmentButton.addEventListener("click", () => {
  if (!elements.newTaskAttachmentButton.disabled) {
    elements.newTaskAttachmentInput.click();
  }
});
elements.newTaskAttachmentInput.addEventListener("change", () => {
  uploadNewTaskAttachments(elements.newTaskAttachmentInput.files || []);
});
elements.newTaskDialog.addEventListener("cancel", (event) => {
  if (isCreatingTask || isUploadingNewTaskAttachments) event.preventDefault();
});
elements.newTaskDialog.addEventListener("close", () => {
  if (!isCreatingTask && newTaskAttachments.length) discardNewTaskAttachments();
});
elements.newTaskForm.addEventListener("submit", (event) => {
  event.preventDefault();
  createNewTask();
});
elements.modelSelect.addEventListener("change", () => {
  syncModelDialogOptions();
});
elements.effortSelect.addEventListener("change", syncEffortDescription);
elements.modelSettingsCancel.addEventListener("click", () => {
  elements.modelSettingsDialog.close();
});
elements.modelSettingsForm.addEventListener("submit", (event) => {
  event.preventDefault();
  saveModelSettings();
});
elements.composerActionButton.addEventListener("click", () => {
  if (elements.composerActionButton.classList.contains("stop")) {
    if (managedRunIsActive() && managedRun?.threadId === selectedThread?.id) {
      elements.composerState.textContent = "正在向 Mac 发送停止请求…";
      interruptManagedTurn();
      return;
    }
    elements.confirmTaskTitle.textContent = selectedThread?.title || "当前任务";
    elements.composerState.textContent = "请在弹窗中确认停止 Desktop 任务";
    elements.stopDialog.showModal();
    return;
  }
  if (elements.composerActionButton.classList.contains("continue")) {
    continueInterruptedTurn();
    return;
  }
  sendManagedMessage();
});
elements.composerInput.addEventListener("input", () => {
  if (selectedThread?.id) {
    threadDrafts.set(selectedThread.id, elements.composerInput.value);
  }
  resizeComposer();
  updateComposerState();
});
elements.composerInput.addEventListener("focus", () => {
  document.body.classList.add("composer-focused");
});
elements.composerInput.addEventListener("blur", () => {
  document.body.classList.remove("composer-focused");
});
elements.composerInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    sendManagedMessage();
  }
});
elements.cancelButton.addEventListener("click", () => elements.stopDialog.close());
elements.confirmButton.addEventListener("click", interruptCurrentTask);
elements.tokenButton.addEventListener("click", () => {
  showTokenDialog(
    deviceToken
      ? "当前浏览器已是受信任设备；仅在授权被撤销后才需要重新配对。"
      : "",
  );
});
elements.tokenCancel.addEventListener("click", () => elements.tokenDialog.close());
elements.tokenForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const supplied = elements.tokenInput.value.trim();
  if (supplied.startsWith("pair1.")) {
    pairingTicket = supplied;
    sessionStorage.setItem(PAIRING_TICKET_KEY, supplied);
  } else if (supplied.length >= 32) {
    legacyToken = supplied;
    sessionStorage.setItem(LEGACY_TOKEN_KEY, supplied);
  } else {
    elements.tokenError.textContent = "配对信息无效。";
    return;
  }
  deviceToken = "";
  localStorage.removeItem(DEVICE_TOKEN_KEY);
  elements.tokenDialog.close();
  setDeviceState("pending", "正在注册受信任设备…");
  Promise.all([refreshStatus(), loadThreads()]);
});

renderNotificationState();
registerNotificationWorker().catch(() => {
  if (notificationsEnabled) {
    elements.notificationButton.classList.add("blocked");
    setNotificationButtonState("通知服务注册失败，请刷新后重试");
  }
});
Promise.all([refreshStatus(), loadThreads()]);
refreshTimer = window.setInterval(refreshStatus, 3000);
managedPollTimer = window.setInterval(() => {
  if (managedRunIsActive()) refreshManagedRun();
}, 900);
desktopHistoryPollTimer = window.setInterval(
  refreshDesktopConversationIfRunning,
  5_000,
);
drawerStatusPollTimer = window.setInterval(() => {
  if (elements.projectDrawer.classList.contains("open") || notificationsEnabled) {
    refreshDrawerThreadStates();
  }
  if (elements.projectDrawer.classList.contains("open")) {
    refreshUsage();
    refreshSystemMetrics();
  }
}, 10_000);
elements.scrollRail.addEventListener("pointerdown", startScrollDrag);
elements.scrollRail.addEventListener("pointermove", moveScrollDrag);
elements.scrollRail.addEventListener("pointerup", endScrollDrag);
elements.scrollRail.addEventListener("pointercancel", endScrollDrag);
window.addEventListener("scroll", () => {
  scheduleScrollHandle();
  if (conversationNearBottom()) hideNewContentNotice();
  else updateLatestButton();
}, { passive: true });
window.addEventListener("resize", () => {
  scheduleScrollHandle();
  updateLatestButton();
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  refreshStatus();
  if (selectedThread && !desktopHistoryRefreshInFlight) {
    desktopHistoryRefreshInFlight = true;
    openThread(selectedThread.id, {
      fresh: true,
      scroll: false,
      refreshRun: false,
      rerenderProjects: false,
      closeDrawer: false,
    }).finally(() => {
      desktopHistoryRefreshInFlight = false;
    });
  }
  if (elements.projectDrawer.classList.contains("open")) {
    refreshDrawerThreadStates();
  }
});
navigator.serviceWorker?.addEventListener("message", (event) => {
  if (event.data?.type !== "open-thread" || typeof event.data.threadId !== "string") {
    return;
  }
  const threadId = event.data.threadId;
  if (threads.some((thread) => thread.id === threadId)) {
    openThread(threadId, { fresh: true });
  } else {
    notificationThreadId = threadId;
    loadThreads();
  }
});
window.visualViewport?.addEventListener("resize", () => {
  resizeComposer();
  scheduleScrollHandle();
  updateLatestButton();
});
document.addEventListener("toggle", () => {
  scheduleScrollHandle();
  updateLatestButton();
}, true);
if (window.ResizeObserver) {
  const layoutObserver = new ResizeObserver(() => {
    scheduleScrollHandle();
    updateLatestButton();
  });
  layoutObserver.observe(document.body);
  layoutObserver.observe(elements.composerShell);
}
scheduleScrollHandle();
window.addEventListener("pagehide", () => {
  window.clearInterval(refreshTimer);
  window.clearInterval(managedPollTimer);
  window.clearInterval(desktopHistoryPollTimer);
  window.clearInterval(drawerStatusPollTimer);
});
